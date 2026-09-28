"""Local house captions: time spine lines on a raw take and burn them with ffmpeg.

The raw take from the Drama API has no burn-in (``api_captions: false``). This
module finishes it on the operator's laptop:

1. Read episode dialogue from the desk spine snapshot (``ep01/api/*spine*.json``).
2. Find speech spans with ffmpeg ``silencedetect`` (no transcription model needed:
   the words are already known from the script gate).
3. Anchor each line on its speech span and spread words by length.
4. Write a house flicker ASS (up to three words build up, then reset) and burn it.

Captions are always the English line. On a show spoken in Japanese or Korean
(spine ``spoken_language`` not English) the English words cannot be timed
against the speech, so step 3-4 show each whole line over its speech span
instead of flickering word by word.

A voice that is heard, not seen (a line marked ``off_screen``, or any line of a
cast member the server flags ``voice_only``) is captioned in Georgia italic:
same size, colour, edge and place as the house caption, only the face changes
(the retired internal kit's convention for a remembered or off-screen voice).

Captions are English only: the caption font has no Japanese, Chinese or Korean
glyphs. A line whose caption text (``subtitle_text``, else ``text``) is not
English is left uncaptioned and reported as ``NOT ENGLISH`` so the operator can
give it an English subtitle. It still takes its place in the timing, so the
lines after it stay on their own speech.

The look matches the content team's reference captions (yellow ``#FFE500``,
Poppins Bold, black edge, soft shadow, no box), scaled from the 768x1344 H3
frame to the take's real size. Placement follows the TikTok / Reels / Shorts
safe zones: the text's bottom edge sits at 62% of the frame height and the
caption block stays inside 55-70%. Captions never wrap; a caption too wide for
one line is set smaller so it still fits on one line inside that band.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creation.ops.folder import next_versioned_path

#: Frame the house style was tuned on (H3 vertical take).
REFERENCE_HEIGHT = 1344
#: Font size on the reference frame.
REFERENCE_FONT_SIZE = 50
#: Bottom edge of the caption text, as a fraction of frame height (social safe zones).
CAPTION_BOTTOM_FRACTION = 0.62
#: The caption block must stay inside this band of the frame height.
CAPTION_BAND = (0.55, 0.70)
#: Left + right ASS margins, pixels.
SIDE_MARGIN = 10
FONT_NAME = "Poppins"
#: Face for a voice heard, not seen (off-screen line or voice-only cast), set in italic.
ITALIC_FONT_NAME = "Georgia"
#: libass sets an ASS ``Fontsize`` as the face's OS/2 winAscent + winDescent, not its em,
#: so one Fontsize draws Georgia about 1.55x larger than Poppins. Em per Fontsize unit:
#: Poppins Bold 1000 / (1135 + 627); Georgia Italic 2048 / (1878 + 449).
HOUSE_EM_PER_SIZE = 1000 / (1135 + 627)
ITALIC_EM_PER_SIZE = 2048 / (1878 + 449)


def italic_size(size: int) -> int:
    """ASS ``Fontsize`` that draws Georgia italic at the same em as Poppins at ``size``.

    Same em is how the retired internal kit set its italic caption (one font
    size for both faces); cap heights then match within 2%.
    """

    return max(1, round(size * HOUSE_EM_PER_SIZE / ITALIC_EM_PER_SIZE))


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

#: CJK punctuation (、。「」) and fullwidth forms (！？): the caption font has no glyph for these either.
_CJK_MARKS = re.compile("[\u3000-\u303f\uff00-\uffef]")


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
    #: Set in Georgia italic (a voice heard, not seen).
    italic: bool = False


@dataclass(frozen=True)
class CaptionLine:
    """One spoken line as the caption sees it.

    Parameters
    ----------
    line_id
        The spine's ``line_id`` (``line N`` when the line has none).
    text
        The caption text: ``subtitle_text`` when the line has one, else ``text``.
    italic
        True for a voice heard, not seen: the line is ``off_screen`` or its
        speaker's cast card is ``voice_only``.
    """

    line_id: str
    text: str
    italic: bool = False

    @property
    def english(self) -> bool:
        """Whether the caption font can set this text (see :func:`is_english`)."""

        return is_english(self.text)


def is_english(text: str) -> bool:
    """Return False when ``text`` is not English and cannot be captioned.

    Not English is any letter outside the Latin script (kana, CJK ideographs
    and Hangul, which the server's caption check covers, and also Cyrillic,
    Greek, Devanagari, Arabic, Thai ...) or any CJK or fullwidth punctuation
    (、。「」！？), which the caption font has no glyph for either. Accented Latin
    letters (café, naïve) are English here.

    Parameters
    ----------
    text
        Caption text.

    Returns
    -------
    bool
        True when every letter is Latin and there is no CJK mark.
    """

    if _CJK_MARKS.search(text):
        return False
    return all(
        not ch.isalpha() or unicodedata.name(ch, "").startswith("LATIN") for ch in text
    )


def not_english_warning(line: CaptionLine) -> str:
    """The operator warning for a line left uncaptioned because it is not English."""

    return (
        f'NOT ENGLISH: {line.line_id} "{line.text}" — add an English subtitle with '
        "`edit`/`line --subtitle`"
    )


def episode_caption_lines(
    spine: dict[str, Any], episode_ordinal: int
) -> list[CaptionLine]:
    """Return every spoken line of one episode as the caption sees it, in beat order.

    Parameters
    ----------
    spine
        Spine JSON as saved on the desk (bare spine or ``{"spine": …}``).
    episode_ordinal
        1-based episode number.

    Returns
    -------
    list[CaptionLine]
        Lines with non-empty caption text, their id, and whether they are set in italic.
    """

    from creation.spine_view import episode_id_for

    body = spine.get("spine", spine)
    # Found by ordinal through episode_summaries: episode 2 can be ``ep_02``, not ``episode_02``.
    episode_id = episode_id_for(body, episode_ordinal)
    # The server marks a character it only ever hears with ``voice_only`` on the cast card.
    voice_only = {
        str(card.get("cast_id"))
        for card in body.get("cast") or []
        if isinstance(card, dict)
        and card.get("cast_id")
        and card.get("voice_only") is True
    }
    lines: list[CaptionLine] = []
    for beat in body.get("beats") or []:
        if beat.get("episode_id") != episode_id:
            continue
        for line in beat.get("dialogue_lines") or []:
            # Captions are English subtitles: ``subtitle_text`` when the line has one, else ``text``.
            # Their timing never reads words: it comes from where speech is heard on the take
            # (``silencedetect``), so a Japanese or Korean performance is timed the same way.
            text = str(line.get("subtitle_text") or line.get("text") or "").strip()
            if not text:
                continue
            heard_not_seen = (
                line.get("off_screen") is True
                or str(line.get("cast_id") or "") in voice_only
            )
            lines.append(
                CaptionLine(
                    str(line.get("line_id") or f"line {len(lines) + 1}"),
                    text,
                    heard_not_seen,
                )
            )
    return lines


def episode_lines(spine: dict[str, Any], episode_ordinal: int) -> list[str]:
    """Return the caption for each spoken line of one episode, in beat order.

    Parameters
    ----------
    spine
        Spine JSON as saved on the desk (bare spine or ``{"spine": …}``).
    episode_ordinal
        1-based episode number.

    Returns
    -------
    list[str]
        Non-empty dialogue texts (see :func:`episode_caption_lines`).
    """

    return [line.text for line in episode_caption_lines(spine, episode_ordinal)]


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


def whole_line_cue(text: str, span: Span, *, hold_until: float | None = None) -> Cue:
    """One cue showing the whole line over its speech span.

    The last-word hold still applies, never past ``hold_until`` (the next line's start).
    """

    end = span.end + LAST_WORD_HOLD_SECONDS
    if hold_until is not None:
        end = min(end, hold_until)
    return Cue(round(span.start, 3), round(max(end, span.start + 0.05), 3), text)


def build_cues(
    lines: Sequence[str],
    anchors: Sequence[Span],
    *,
    whole_lines: bool = False,
    italic: Sequence[bool] = (),
    skip: Sequence[bool] = (),
) -> list[Cue]:
    """Cues for every line on its anchor span.

    ``italic[i]`` sets line ``i``'s cues in Georgia italic; ``skip[i]`` leaves
    line ``i`` uncaptioned (not English) while its span still bounds the hold
    of the line before it. Missing entries mean False.

    English shows flicker word by word (:func:`flicker_cues`). With
    ``whole_lines`` (a show spoken in Japanese or Korean, captioned with the
    English line) each line is one cue over its speech span: English words
    cannot be timed against Japanese or Korean speech, so spreading them would
    flash words that are not being said. This is the whole-line rule the
    server follows for a translated subtitle.
    """

    cues: list[Cue] = []
    for i, (text, span) in enumerate(zip(lines, anchors)):
        if i < len(skip) and skip[i]:
            continue
        next_start = anchors[i + 1].start if i + 1 < len(anchors) else None
        if whole_lines:
            line_cues = [whole_line_cue(text, span, hold_until=next_start)]
        else:
            line_cues = flicker_cues(time_words(text, span), hold_until=next_start)
        slanted = i < len(italic) and italic[i]
        cues.extend(
            Cue(c.start, c.end, c.text, italic=True) if slanted else c
            for c in line_cues
        )
    return cues


def captions_whole_lines(spine: dict[str, Any]) -> bool:
    """True when the show is spoken in another language than English (captions stay the English line).

    ``spoken_language`` is read from the spine (bare or ``{"spine": …}``); unset means English.
    """

    body = spine.get("spine", spine)
    code = (
        str(body.get("spoken_language") or "en")
        .strip()
        .replace("_", "-")
        .split("-", 1)[0]
        .lower()
    )
    return code not in ("", "en")


def _ass_time(seconds: float) -> str:
    centis = int(round(max(0.0, seconds) * 100))
    hours, rem = divmod(centis, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
    )


def caption_margin_v(height: int) -> int:
    """Bottom margin that puts the caption's bottom edge at 62% of the frame height."""

    return round(height * (1.0 - CAPTION_BOTTOM_FRACTION))


def text_width(text: str, size: int, *, spacing: float = 1.5) -> float:
    """Rendered width of ``text`` in Poppins Bold at ``size`` px (bundled font), with letter spacing."""

    from PIL import ImageFont

    font = ImageFont.truetype(str(FONTS_DIR / "Poppins-Bold.ttf"), size)
    return float(font.getlength(text)) + spacing * max(0, len(text) - 1)


def fitted_size(text: str, size: int, width: int) -> int:
    """The font size at which ``text`` fits on one line inside the side margins (never larger than ``size``)."""

    room = width - 2 * SIDE_MARGIN
    wide = text_width(text, size)
    if wide <= room:
        return size
    return max(8, int(size * room / wide))


def build_ass(cues: Sequence[Cue], *, width: int, height: int) -> str:
    """Render house-style ASS for a frame of ``width`` x ``height``.

    Two styles: ``House`` (Poppins Bold) and ``Italic`` (Georgia italic, not
    bold; same drawn size, colour, edge, shadow and place) for a cue with
    ``italic``. The italic ``Fontsize`` is :func:`italic_size`, so both faces
    draw at the same em.

    Captions never wrap (``WrapStyle: 2``): a cue too wide for one line gets a
    smaller ``\\fs`` so the block stays one line, bottom edge at 62%, inside
    the 55-70% band.
    """

    scale = height / REFERENCE_HEIGHT
    size = round(REFERENCE_FONT_SIZE * scale)
    margin_v = caption_margin_v(height)
    outline = max(1, round(3 * scale))
    shadow = max(1, round(1 * scale))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: House,{FONT_NAME},{size},{PRIMARY_COLOUR},{PRIMARY_COLOUR},{OUTLINE_COLOUR},"
        f"{SHADOW_COLOUR},-1,0,0,0,100,100,1.5,0,1,{outline},{shadow},2,{SIDE_MARGIN},{SIDE_MARGIN},{margin_v},1\n"
        f"Style: Italic,{ITALIC_FONT_NAME},{italic_size(size)},{PRIMARY_COLOUR},{PRIMARY_COLOUR},{OUTLINE_COLOUR},"
        f"{SHADOW_COLOUR},0,-1,0,0,100,100,1.5,0,1,{outline},{shadow},2,{SIDE_MARGIN},{SIDE_MARGIN},{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def text(cue: Cue) -> str:
        # Fit is measured in Poppins Bold; Georgia italic at the same em is narrower, so it fits too.
        fit = fitted_size(cue.text, size, width)
        if fit == size:
            prefix = ""
        else:
            prefix = f"{{\\fs{italic_size(fit) if cue.italic else fit}}}"
        return prefix + _ass_escape(cue.text)

    events = "".join(
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},{'Italic' if c.italic else 'House'},,0,0,0,,{text(c)}\n"
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
        filters = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True
        ).stdout
        if re.search(r"^\s*\S+\s+(ass|subtitles)\s", filters, re.MULTILINE):
            return ffmpeg, probe
    raise RuntimeError(
        "ffmpeg with libass (the ass/subtitles filter) and ffprobe are required for local captions. "
        "macOS: brew install ffmpeg (or ffmpeg-full if your build lacks libass)."
    )


#: Where CoreText (libass's font provider on macOS) finds Georgia, after the bundled fonts dir.
MAC_FONT_DIRS: tuple[Path, ...] = (
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path("~/Library/Fonts"),
)
#: Georgia Italic's file name: macOS, then the Microsoft core fonts package on Linux.
ITALIC_FONT_FILES: tuple[str, ...] = ("Georgia Italic.ttf", "georgiai.ttf")
#: Georgia regular's file name (libass slants it when there is no italic face).
REGULAR_FONT_FILES: tuple[str, ...] = ("Georgia.ttf", "georgia.ttf")
GEORGIA_INSTALL_HINT = (
    "install Georgia: macOS ships it in /System/Library/Fonts/Supplemental "
    "(Font Book > File > Restore Standard Fonts brings it back); Linux: "
    "sudo apt install ttf-mscorefonts-installer, or copy Georgia.ttf and "
    "'Georgia Italic.ttf' into ~/.local/share/fonts and run fc-cache -f"
)

#: ``(pattern) -> (family, style, file)`` from fontconfig, or None when fc-match is missing.
FcMatch = Callable[[str], tuple[str, str, str] | None]


def fc_match(pattern: str) -> tuple[str, str, str] | None:
    """Ask fontconfig which face it would draw ``pattern`` with.

    Parameters
    ----------
    pattern
        A fontconfig pattern, e.g. ``Georgia:italic``.

    Returns
    -------
    tuple of str or None
        ``(family, style, file)``, or None when ``fc-match`` is not installed
        or does not answer.
    """

    exe = shutil.which("fc-match")
    if not exe:
        return None
    try:
        result = subprocess.run(
            [exe, "-f", "%{family}\t%{style}\t%{file}", pattern],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = result.stdout.strip().split("\t")
    if result.returncode != 0 or len(parts) != 3:
        return None
    family, style, file = parts
    return family, style, file


@dataclass(frozen=True)
class ItalicFont:
    """Where the italic caption face (Georgia Italic) resolves on this machine.

    Parameters
    ----------
    italic
        The Georgia Italic file libass will draw with, or None.
    regular
        The Georgia regular file, or None (libass slants it when ``italic`` is None).
    fallback
        The face libass would use instead when Georgia is missing, as fontconfig
        names it (``file``), or None when that cannot be told.
    """

    italic: Path | None
    regular: Path | None = None
    fallback: str | None = None

    @property
    def ok(self) -> bool:
        """Georgia Italic itself is installed."""

        return self.italic is not None

    def warning(self) -> str | None:
        """The operator warning when captions will not be set in Georgia Italic, else None."""

        if self.italic is not None:
            return None
        if self.regular is not None:
            return (
                f"Georgia Italic not found (only {self.regular}); heard-not-seen captions "
                f"will be Georgia slanted by libass, not the real italic. To fix, {GEORGIA_INSTALL_HINT}"
            )
        instead = self.fallback or "whatever face libass falls back to"
        return (
            f"Georgia not found; heard-not-seen captions will fall back to {instead}. "
            f"To fix, {GEORGIA_INSTALL_HINT}"
        )


def _first_file(dirs: Sequence[Path], names: Sequence[str]) -> Path | None:
    for directory in dirs:
        for name in names:
            path = directory.expanduser() / name
            if path.is_file():
                return path
    return None


def find_italic_font(
    *,
    platform: str | None = None,
    font_dirs: Sequence[Path] | None = None,
    match: FcMatch | None = None,
) -> ItalicFont:
    """Resolve Georgia Italic the way libass does when captions are burned.

    ``burn_ass`` passes the bundled fonts dir to libass, so that is searched
    first. After it, libass asks the system font provider: CoreText on macOS
    (the standard font folders, :data:`MAC_FONT_DIRS`) and fontconfig on Linux
    (``fc-match Georgia:italic``).

    Parameters
    ----------
    platform
        ``sys.platform`` by default; tests pass one.
    font_dirs
        Folders searched by file name (default: the bundled fonts dir, plus
        :data:`MAC_FONT_DIRS` on macOS).
    match
        fontconfig lookup (default :func:`fc_match`); tests pass a fake.

    Returns
    -------
    ItalicFont
        What was found; ``warning()`` says what to do when it is not Georgia Italic.
    """

    platform = platform or sys.platform
    match = match or fc_match
    if font_dirs is None:
        font_dirs = (FONTS_DIR, *(MAC_FONT_DIRS if platform == "darwin" else ()))
    italic = _first_file(font_dirs, ITALIC_FONT_FILES)
    regular = _first_file(font_dirs, REGULAR_FONT_FILES)
    if italic is not None:
        return ItalicFont(italic, regular)
    found = match(f"{ITALIC_FONT_NAME}:italic")
    fallback: str | None = None
    if found is not None:
        family, style, file = found
        is_georgia = ITALIC_FONT_NAME.lower() in family.lower()
        # On macOS CoreText, not fontconfig, draws the caption: fontconfig only names the fallback.
        if is_georgia and platform != "darwin":
            if "italic" in style.lower():
                return ItalicFont(Path(file), regular)
            regular = regular or Path(file)
        elif not is_georgia:
            fallback = f"{family} ({file})"
    return ItalicFont(None, regular, fallback)


def italic_font_warning(cues: Sequence[Cue]) -> str:
    """Warn (on stderr, and returned) when an italic cue will not be set in Georgia Italic.

    Parameters
    ----------
    cues
        The cues about to be burned; nothing is checked when none is italic.

    Returns
    -------
    str
        ``FONT: …`` warning, or ``""`` when every italic cue gets Georgia Italic.
    """

    if not any(cue.italic for cue in cues):
        return ""
    problem = find_italic_font().warning()
    if not problem:
        return ""
    warning = f"FONT: {problem}"
    print(f"WARNING {warning}", file=sys.stderr)
    return warning


def probe_video(ffprobe: str, path: Path) -> tuple[int, int, float]:
    """Return ``(width, height, duration_seconds)``."""

    out = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return (
        int(stream["width"]),
        int(stream["height"]),
        float(data["format"]["duration"]),
    )


def detect_silences(ffmpeg: str, path: Path, duration: float) -> list[Span]:
    """Run ``silencedetect`` on the take's audio."""

    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vn",
            "-af",
            f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECONDS}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"silencedetect failed on {path.name}: {result.stderr.strip()[-400:]}"
        )
    return parse_silencedetect(result.stderr, duration)


def burn_ass(
    ffmpeg: str, take: Path, ass: Path, out: Path, *, fonts_dir: Path = FONTS_DIR
) -> None:
    """Burn ``ass`` onto ``take`` (video re-encoded, audio copied)."""

    def esc(p: Path) -> str:
        return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(take),
            "-vf",
            f"ass='{esc(ass)}':fontsdir='{esc(fonts_dir)}'",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            str(out),
        ],
        capture_output=True,
        text=True,
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
    #: True when each line was shown whole (show spoken in Japanese or Korean), False for word flicker.
    whole_lines: bool = False
    #: Per line (same order as ``lines``): set in Georgia italic (heard, not seen).
    italic: tuple[bool, ...] = ()
    #: One ``NOT ENGLISH: …`` warning per line left uncaptioned because it is not English.
    not_english: tuple[str, ...] = ()
    #: ``FONT: …`` when an italic line falls back from Georgia Italic (also printed on stderr), else "".
    font_warning: str = ""


def caption_take(
    desk: Path,
    *,
    episode_ordinal: int = 1,
    take: Path | None = None,
    line_starts: Sequence[float] | None = None,
    timing_source: Path | None = None,
    stem: str | None = None,
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
    timing_source
        File whose speech is detected (default ``take``). ``finish`` passes the
        take before the music bed, since silence cannot be found under music.
    stem
        Output name stem (``take-ep01-t1-cap`` writes ``take-ep01-t1-cap-vN.mp4``
        and ``.ass``); default ``<take>-house`` / ``<take>-captioned``.

    Returns
    -------
    CaptionResult
        Versioned ASS + captioned MP4 under ``takes/``. A line that is not
        English is timed but not captioned; ``not_english`` says which.
    """

    ep_dir = desk.expanduser().resolve() / f"ep{episode_ordinal:02d}"
    takes = ep_dir / "takes"
    take = take or latest_file(takes, f"take-ep{episode_ordinal:02d}-t1-raw-v*.mp4")
    if take is None or not take.is_file():
        raise FileNotFoundError(
            f"no raw take in {takes}; run `fictora-produce step --confirm-spend` first"
        )
    api = ep_dir / "api"
    # Newest snapshot that carries beats (approve responses are receipts without them).
    caption_lines: list[CaptionLine] = []
    whole_lines = False
    for spine_path in sorted(
        api.glob("*spine*.json"), key=lambda p: p.name, reverse=True
    ):
        spine = json.loads(spine_path.read_text(encoding="utf-8"))
        if isinstance(spine, dict):
            caption_lines = episode_caption_lines(spine, episode_ordinal)
        if caption_lines:
            whole_lines = captions_whole_lines(spine)
            break
    lines = [line.text for line in caption_lines]
    if not lines:
        raise ValueError(
            f"episode {episode_ordinal} has no dialogue lines in any spine snapshot in {api}"
        )

    ffmpeg, ffprobe = find_ffmpeg()
    width, height, duration = probe_video(ffprobe, take)
    if line_starts:
        if len(line_starts) != len(lines):
            raise ValueError(
                f"--line-start given {len(line_starts)} time(s) for {len(lines)} line(s)"
            )
        ends = list(line_starts[1:]) + [duration]
        anchors = [
            Span(s, min(e, s + max(0.6, len(t.split()) * SECONDS_PER_WORD)))
            for s, e, t in zip(line_starts, ends, lines)
        ]
    else:
        source = timing_source or take
        spans = speech_spans(detect_silences(ffmpeg, source, duration), duration)
        anchors = anchor_lines(lines, spans)

    italic = tuple(line.italic for line in caption_lines)
    # Every line keeps its speech span (timing); only English lines are drawn.
    cues = build_cues(
        lines,
        anchors,
        whole_lines=whole_lines,
        italic=italic,
        skip=[not line.english for line in caption_lines],
    )
    not_english = tuple(
        not_english_warning(line) for line in caption_lines if not line.english
    )
    base = take.stem.replace("-raw", "").rsplit("-v", 1)[0]
    ass = next_versioned_path(takes, stem or f"{base}-house", ".ass")
    ass.write_text(build_ass(cues, width=width, height=height), encoding="utf-8")
    video = next_versioned_path(takes, stem or f"{base}-captioned", ".mp4")
    font_warning = italic_font_warning(cues)
    burn_ass(ffmpeg, take, ass, video)
    return CaptionResult(
        ass,
        video,
        tuple(cues),
        tuple(anchors),
        tuple(lines),
        whole_lines,
        italic,
        not_english,
        font_warning,
    )
