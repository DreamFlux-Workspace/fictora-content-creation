"""Local house captions: time spine lines on a raw take and burn them with ffmpeg.

The raw take from the Drama API has no burn-in (``api_captions: false``). This
module finishes it on the operator's laptop:

1. Read episode dialogue from the desk spine snapshot (``ep01/api/*spine*.json``).
2. Find speech spans with ffmpeg ``silencedetect`` (no transcription model needed:
   the words are already known from the script gate).
3. Anchor each line on its speech span and spread words by length.
4. Write a house flicker ASS (up to three words build up, then reset) and burn it.

The look matches the content team's reference captions (yellow ``#FFE500``,
Poppins Bold, black edge, soft shadow, no box, text bottom at 70% of frame
height), scaled from the 768x1344 H3 frame to the take's real size.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from creation.ops.folder import next_versioned_path

#: Frame the house style was tuned on (H3 vertical take).
REFERENCE_HEIGHT = 1344
#: Font size and bottom margin on the reference frame.
REFERENCE_FONT_SIZE = 50
REFERENCE_MARGIN_V = 403
FONT_NAME = "Poppins"
#: ASS colours are &HAABBGGRR: yellow #FFE500, black edge, 50% black shadow.
PRIMARY_COLOUR = "&H0000E5FF"
OUTLINE_COLOUR = "&H00000000"
SHADOW_COLOUR = "&H80000000"
MAX_WORDS_ON_SCREEN = 3

#: silencedetect settings (runbook: noise -30 dB, 0.3 s minimum silence).
SILENCE_NOISE_DB = -30
SILENCE_MIN_SECONDS = 0.3
#: Spans shorter than this are clicks or cloth, not speech.
MIN_SPEECH_SECONDS = 0.12
#: A pause shorter than this stays inside one line.
MAX_PAUSE_IN_LINE_SECONDS = 0.6
#: Rough speaking pace used to decide how many spans one line may take.
SECONDS_PER_WORD = 0.32
LAST_WORD_HOLD_SECONDS = 0.15

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


@dataclass(frozen=True)
class Span:
    """One stretch of time in seconds."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Cue:
    """One caption event: text shown from ``start`` to ``end``."""

    start: float
    end: float
    text: str


def episode_lines(spine: dict[str, Any], episode_ordinal: int) -> list[str]:
    """Return spoken lines for one episode, in beat order.

    Parameters
    ----------
    spine
        Spine JSON as saved on the desk (bare spine or ``{"spine": …}``).
    episode_ordinal
        1-based episode number.

    Returns
    -------
    list[str]
        Non-empty dialogue texts.
    """

    body = spine.get("spine", spine)
    episode_id = f"episode_{episode_ordinal:02d}"
    lines: list[str] = []
    for beat in body.get("beats") or []:
        if beat.get("episode_id") != episode_id:
            continue
        for line in beat.get("dialogue_lines") or []:
            text = str(line.get("text") or "").strip()
            if text:
                lines.append(text)
    return lines


def parse_silencedetect(stderr: str, duration: float) -> list[Span]:
    """Turn ffmpeg ``silencedetect`` output into silence spans.

    A silence still open at end of file is closed at ``duration``.
    """

    silences: list[Span] = []
    open_start: float | None = None
    for match in re.finditer(r"silence_(start|end): (-?[0-9.]+)", stderr):
        kind, value = match.group(1), max(0.0, float(match.group(2)))
        if kind == "start":
            open_start = value
        elif open_start is not None:
            silences.append(Span(open_start, value))
            open_start = None
    if open_start is not None:
        silences.append(Span(open_start, duration))
    return silences


def speech_spans(silences: Sequence[Span], duration: float) -> list[Span]:
    """Return the gaps between silences that are long enough to be speech."""

    spans: list[Span] = []
    cursor = 0.0
    for silence in sorted(silences, key=lambda s: s.start):
        if silence.start - cursor >= MIN_SPEECH_SECONDS:
            spans.append(Span(cursor, silence.start))
        cursor = max(cursor, silence.end)
    if duration - cursor >= MIN_SPEECH_SECONDS:
        spans.append(Span(cursor, duration))
    return spans


def anchor_lines(lines: Sequence[str], spans: Sequence[Span]) -> list[Span]:
    """Give each line, in order, the speech span(s) it is spoken in.

    Each line starts on the next unused span (silence-end onset) and absorbs
    following spans only while the pause is short and the line still needs
    time. Spans left over after the last line (ambience, a door, music) are
    ignored.

    Raises
    ------
    ValueError
        When there are fewer speech spans than lines.
    """

    if len(spans) < len(lines):
        raise ValueError(
            f"found {len(spans)} speech span(s) for {len(lines)} line(s); "
            "pass --line-start to place lines by hand"
        )
    anchored: list[Span] = []
    index = 0
    for line_no, text in enumerate(lines):
        remaining_lines = len(lines) - line_no - 1
        start, end = spans[index].start, spans[index].end
        index += 1
        budget = max(0.6, len(text.split()) * SECONDS_PER_WORD) * 2.5
        while (
            index < len(spans) - remaining_lines
            and spans[index].start - end < MAX_PAUSE_IN_LINE_SECONDS
            and spans[index].end - start <= budget
        ):
            end = spans[index].end
            index += 1
        anchored.append(Span(start, end))
    return anchored


def time_words(text: str, span: Span) -> list[Cue]:
    """Spread the words of one line across its span, weighted by length."""

    words = text.split()
    if not words:
        return []
    weights = [len(word) + 1 for word in words]
    total = float(sum(weights))
    cues: list[Cue] = []
    cursor = span.start
    for word, weight in zip(words, weights):
        step = span.duration * weight / total
        cues.append(Cue(round(cursor, 3), round(cursor + step, 3), word))
        cursor += step
    return cues


def flicker_cues(words: Sequence[Cue], *, hold_until: float | None = None) -> list[Cue]:
    """Build up to three words at a time, then reset (house flicker).

    Each event lasts until the next word starts; the last word holds briefly,
    never past ``hold_until`` (the next line's start).
    """

    cues: list[Cue] = []
    for i, word in enumerate(words):
        chunk_start = i - (i % MAX_WORDS_ON_SCREEN)
        text = " ".join(w.text for w in words[chunk_start : i + 1])
        if i + 1 < len(words):
            end = words[i + 1].start
        else:
            end = word.end + LAST_WORD_HOLD_SECONDS
            if hold_until is not None:
                end = min(end, hold_until)
        cues.append(Cue(word.start, max(end, word.start + 0.05), text))
    return cues


def build_cues(lines: Sequence[str], anchors: Sequence[Span]) -> list[Cue]:
    """Flicker cues for every line on its anchor span."""

    cues: list[Cue] = []
    for i, (text, span) in enumerate(zip(lines, anchors)):
        next_start = anchors[i + 1].start if i + 1 < len(anchors) else None
        cues.extend(flicker_cues(time_words(text, span), hold_until=next_start))
    return cues


def _ass_time(seconds: float) -> str:
    centis = int(round(max(0.0, seconds) * 100))
    hours, rem = divmod(centis, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(cues: Sequence[Cue], *, width: int, height: int) -> str:
    """Render house-style ASS for a frame of ``width`` x ``height``."""

    scale = height / REFERENCE_HEIGHT
    size = round(REFERENCE_FONT_SIZE * scale)
    margin_v = round(REFERENCE_MARGIN_V * scale)
    outline = max(1, round(3 * scale))
    shadow = max(1, round(1 * scale))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: House,{FONT_NAME},{size},{PRIMARY_COLOUR},{PRIMARY_COLOUR},{OUTLINE_COLOUR},"
        f"{SHADOW_COLOUR},-1,0,0,0,100,100,1.5,0,1,{outline},{shadow},2,10,10,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = "".join(
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},House,,0,0,0,,{_ass_escape(c.text)}\n"
        for c in cues
    )
    return header + events


def find_ffmpeg() -> tuple[str, str]:
    """Return ``(ffmpeg, ffprobe)`` paths where ffmpeg can burn ASS (libass).

    Raises
    ------
    RuntimeError
        With an install hint when either tool is missing or lacks libass.
    """

    candidates = [shutil.which("ffmpeg"), "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"]
    for ffmpeg in candidates:
        if not ffmpeg or not Path(ffmpeg).exists():
            continue
        probe = str(Path(ffmpeg).with_name("ffprobe"))
        if not Path(probe).exists():
            probe = shutil.which("ffprobe") or ""
        if not probe:
            continue
        filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        if re.search(r"^\s*\S+\s+(ass|subtitles)\s", filters, re.MULTILINE):
            return ffmpeg, probe
    raise RuntimeError(
        "ffmpeg with libass (the ass/subtitles filter) and ffprobe are required for local captions. "
        "macOS: brew install ffmpeg (or ffmpeg-full if your build lacks libass)."
    )


def probe_video(ffprobe: str, path: Path) -> tuple[int, int, float]:
    """Return ``(width, height, duration_seconds)``."""

    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(data["format"]["duration"])


def detect_silences(ffmpeg: str, path: Path, duration: float) -> list[Span]:
    """Run ``silencedetect`` on the take's audio."""

    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
         f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECONDS}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"silencedetect failed on {path.name}: {result.stderr.strip()[-400:]}")
    return parse_silencedetect(result.stderr, duration)


def burn_ass(ffmpeg: str, take: Path, ass: Path, out: Path, *, fonts_dir: Path = FONTS_DIR) -> None:
    """Burn ``ass`` onto ``take`` (video re-encoded, audio copied)."""

    def esc(p: Path) -> str:
        return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    result = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-i", str(take),
         "-vf", f"ass='{esc(ass)}':fontsdir='{esc(fonts_dir)}'",
         "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
         "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"caption burn failed: {result.stderr.strip()[-400:]}")


def latest_file(directory: Path, pattern: str) -> Path | None:
    """Newest file matching ``pattern`` by name order (``-vN`` / numbered snapshots)."""

    def key(p: Path) -> tuple[int, str]:
        m = re.search(r"-v(\d+)$", p.stem)
        return (int(m.group(1)) if m else 0, p.name)

    matches = sorted((p for p in directory.glob(pattern) if p.is_file()), key=key)
    return matches[-1] if matches else None


@dataclass(frozen=True)
class CaptionResult:
    """Files written by :func:`caption_take`."""

    ass: Path
    video: Path
    cues: tuple[Cue, ...]
    anchors: tuple[Span, ...]
    lines: tuple[str, ...]


def caption_take(
    desk: Path,
    *,
    episode_ordinal: int = 1,
    take: Path | None = None,
    line_starts: Sequence[float] | None = None,
) -> CaptionResult:
    """Caption the newest raw take on a desk episode.

    Parameters
    ----------
    desk
        Series desk root.
    episode_ordinal
        Episode number (``ep01`` = 1).
    take
        Raw MP4 to caption; defaults to the newest ``takes/take-epNN-t1-raw-v*.mp4``.
    line_starts
        Manual start time per line, overriding speech detection.

    Returns
    -------
    CaptionResult
        Versioned ASS + captioned MP4 under ``takes/``.
    """

    ep_dir = desk.expanduser().resolve() / f"ep{episode_ordinal:02d}"
    takes = ep_dir / "takes"
    take = take or latest_file(takes, f"take-ep{episode_ordinal:02d}-t1-raw-v*.mp4")
    if take is None or not take.is_file():
        raise FileNotFoundError(f"no raw take in {takes}; run `fictora-produce step --confirm-spend` first")
    api = ep_dir / "api"
    # Newest snapshot that carries beats (approve responses are receipts without them).
    lines: list[str] = []
    for spine_path in sorted(api.glob("*spine*.json"), key=lambda p: p.name, reverse=True):
        spine = json.loads(spine_path.read_text(encoding="utf-8"))
        if isinstance(spine, dict):
            lines = episode_lines(spine, episode_ordinal)
        if lines:
            break
    if not lines:
        raise ValueError(f"episode {episode_ordinal} has no dialogue lines in any spine snapshot in {api}")

    ffmpeg, ffprobe = find_ffmpeg()
    width, height, duration = probe_video(ffprobe, take)
    if line_starts:
        if len(line_starts) != len(lines):
            raise ValueError(f"--line-start given {len(line_starts)} time(s) for {len(lines)} line(s)")
        ends = list(line_starts[1:]) + [duration]
        anchors = [
            Span(s, min(e, s + max(0.6, len(t.split()) * SECONDS_PER_WORD)))
            for s, e, t in zip(line_starts, ends, lines)
        ]
    else:
        spans = speech_spans(detect_silences(ffmpeg, take, duration), duration)
        anchors = anchor_lines(lines, spans)

    cues = build_cues(lines, anchors)
    stem = take.stem.replace("-raw", "").rsplit("-v", 1)[0]
    ass = next_versioned_path(takes, f"{stem}-house", ".ass")
    ass.write_text(build_ass(cues, width=width, height=height), encoding="utf-8")
    video = next_versioned_path(takes, f"{stem}-captioned", ".mp4")
    burn_ass(ffmpeg, take, ass, video)
    return CaptionResult(ass, video, tuple(cues), tuple(anchors), tuple(lines))
