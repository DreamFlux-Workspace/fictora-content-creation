"""Text check: find words the video model drew into a filmed take (burned-in subtitles, mostly).

The provider rewrites take prompts, so a "no captions" instruction does not
always reach the model, and a take sometimes comes back with its spoken lines
drawn as subtitles. House captions go on in ``finish``; drawn ones are a fault
the human has to see before saying Use it.

This check is free and local. It samples frames from the raw take (two a
second, four a second inside the spoken-line windows the take facts give),
reads them with the ``tesseract`` command when it is installed, and keeps only
text that looks like real words:

- a word counts at :data:`MIN_WORD_CONF` confidence or more, with at least two
  letters, mostly letters, and a height between :data:`MIN_TEXT_HEIGHT` and
  :data:`MAX_TEXT_HEIGHT` of the frame (tiny marks, hair strands and blobs are
  dropped);
- a row counts with at least two such words and :data:`MIN_ROW_LETTERS`
  letters (a badge logo or a stray glyph is not a row).

Rows are grouped across samples by height in the frame. A group is a
**subtitle** when it sits centred in the caption band (the lower part of the
frame) and either shows on more than one sample or matches a spoken line; a
group in view on nearly every sample that matches no line is the set (a shop
sign), not a subtitle. Text anywhere else is reported as a note unless it
matches a spoken line (then it is a fault too). A subtitle group is reported
with its times, the box to blur, and a saved crop sheet.

No ``tesseract`` on the machine means the check says so ("text check skipped
(no OCR installed)"): never a silent pass.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from creation.ops.folder import next_versioned_path
from creation.post.media import MediaToolError, ffmpeg_bin, probe_video

#: Samples a second across the take, and inside a spoken-line window.
SAMPLE_FPS = 2.0
LINE_SAMPLE_FPS = 4.0
#: A word is read when tesseract is at least this sure of it (0-100); a row needs this mean.
MIN_WORD_CONF = 70.0
MIN_ROW_CONF = 80.0
#: Letter height as a share of the frame height: under it is a mark, over it a blob.
MIN_TEXT_HEIGHT = 0.012
MAX_TEXT_HEIGHT = 0.08
#: A row needs this many letters across at least two words.
MIN_ROW_LETTERS = 6
#: The caption band: row centre this far down the frame or more, and this close to the middle.
CAPTION_BAND_TOP = 0.6
CAPTION_CENTRE_SLACK = 0.15
#: Rows within this share of the frame height of each other are one group.
GROUP_SLACK = 0.04
#: A group seen on at least this share of the samples, matching no line, is the set (a sign).
SET_DRESSING_SHARE = 0.9
#: Share of a row's words found in one spoken line for the row to be that line.
LINE_MATCH_SHARE = 0.5
#: Tesseract language packs for the show's spoken language (Latin script is always read).
OCR_LANGUAGES = {"ja": "jpn", "ko": "kor", "zh": "chi_sim"}

SKIPPED_NO_OCR = (
    "text check skipped (no OCR installed): drawn subtitles were NOT looked for. "
    "Install tesseract (macOS: brew install tesseract) or watch the take for drawn text"
)

OcrRunner = Callable[[Sequence[Path], str], str]
"""``(frame PNGs, languages) -> tesseract TSV`` (one page per frame, in order)."""


@dataclass(frozen=True)
class Word:
    """One word tesseract read on one sample."""

    sample: int
    left: int
    top: int
    width: int
    height: int
    conf: float
    text: str

    @property
    def letters(self) -> int:
        """Letters in the word."""

        return sum(1 for ch in self.text if ch.isalpha())


@dataclass(frozen=True)
class Row:
    """Words side by side on one sample."""

    sample: int
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        """The row's words, left to right."""

        return " ".join(w.text for w in sorted(self.words, key=lambda w: w.left))

    @property
    def box(self) -> tuple[int, int, int, int]:
        """``(left, top, right, bottom)`` in take pixels."""

        return (
            min(w.left for w in self.words),
            min(w.top for w in self.words),
            max(w.left + w.width for w in self.words),
            max(w.top + w.height for w in self.words),
        )


@dataclass
class Finding:
    """One group of text rows at one height in the frame, across samples."""

    kind: str
    rows: list[Row] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    line_id: str | None = None

    @property
    def text(self) -> str:
        """The most often read wording."""

        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.text] = counts.get(row.text, 0) + 1
        return max(counts, key=lambda t: (counts[t], len(t)))

    @property
    def box(self) -> tuple[int, int, int, int]:
        """Union box ``(left, top, right, bottom)``."""

        boxes = [row.box for row in self.rows]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def blur_box(
        self, size: tuple[int, int], *, margin: int = 12
    ) -> tuple[int, int, int, int]:
        """``x, y, w, h`` for ``blur --box``, with a margin, inside the frame."""

        left, top, right, bottom = self.box
        x, y = max(0, left - margin), max(0, top - margin)
        return (
            x,
            y,
            min(size[0], right + margin) - x,
            min(size[1], bottom + margin) - y,
        )

    def window(self, step: float) -> tuple[float, float]:
        """First and last time seen, widened by half a sample each side."""

        return max(0.0, min(self.times) - step / 2), max(self.times) + step / 2


@dataclass
class TextCheck:
    """What the text check found on one take."""

    take: Path
    status: str
    note: str = ""
    findings: list[Finding] = field(default_factory=list)
    samples: int = 0
    size: tuple[int, int] = (0, 0)
    sheet: Path | None = None

    @property
    def subtitles(self) -> list[Finding]:
        """Groups that are drawn subtitles (or a spoken line drawn anywhere)."""

        return [f for f in self.findings if f.kind == "subtitle"]

    @property
    def other(self) -> list[Finding]:
        """Other readable text (signs, set dressing): a note, not a fault."""

        return [f for f in self.findings if f.kind != "subtitle"]

    def warning_lines(self, *, desk: Path | str = "<desk>", episode: int = 1,
                      take_id: str = "t1", final: Path | None = None) -> list[str]:  # fmt: skip
        """Loud lines for ``finish`` and ``review``: what was drawn, when, the crop, and the two fixes."""

        if self.status == "skipped":
            return [f"!! {self.note}"]
        if not self.subtitles:
            return []
        lines = [
            f"!! DRAWN TEXT in the raw take `{self.take.name}`: the video model burned words into the picture"
        ]
        step = 1.0 / SAMPLE_FPS
        for finding in self.subtitles:
            start, end = finding.window(step)
            said = f" (spoken line {finding.line_id})" if finding.line_id else ""
            times = ", ".join(f"{t:.2f}s" for t in finding.times[:8])
            more = (
                f" and {len(finding.times) - 8} more" if len(finding.times) > 8 else ""
            )
            lines.append(f"   '{finding.text}'{said} at {times}{more}")
        if self.sheet is not None:
            lines.append(f"   crop: {self.sheet}")
        target = final or Path("<finished file>")
        boxes = " ".join(
            "--box " + ",".join(str(v) for v in f.blur_box(self.size))
            for f in self.subtitles
        )
        start = min(f.window(step)[0] for f in self.subtitles)
        end = max(f.window(step)[1] for f in self.subtitles)
        lines.append(
            f"   Fix: re-film the take (`fictora-produce film --desk {desk} --episode {episode} --take {take_id} "
            f'--cause "the model drew the line as subtitles"`), or patch it with the blur edit '
            f"(`fictora-produce blur --desk {desk} --take-file {target} {boxes} --from {start:.2f} --to {end:.2f}`) "
            "and watch the window at full size"
        )
        return lines

    def summary(self) -> str:
        """One line for a review section."""

        if self.status == "skipped":
            return self.note
        found = len(self.subtitles)
        extra = (
            f"; {len(self.other)} other text group(s) (signs?)" if self.other else ""
        )
        if found:
            return f"{found} drawn subtitle group(s) on {self.samples} sampled frame(s){extra}"
        return f"no drawn subtitles on {self.samples} sampled frame(s){extra}"

    def as_json(self) -> dict[str, Any]:
        """JSON-able form."""

        step = 1.0 / SAMPLE_FPS
        return {
            "take": str(self.take),
            "status": self.status,
            "note": self.note,
            "samples": self.samples,
            "sheet": str(self.sheet) if self.sheet else None,
            "findings": [
                {"kind": f.kind, "text": f.text, "line_id": f.line_id,
                 "times": [round(t, 2) for t in f.times], "window": [round(v, 2) for v in f.window(step)],
                 "box": list(f.blur_box(self.size))}
                for f in self.findings
            ],
        }  # fmt: skip


def tesseract_bin() -> str | None:
    """The ``tesseract`` command, or ``None`` when it is not installed."""

    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
        if Path(candidate).exists():
            return candidate
    return None


def ocr_languages(binary: str, spoken: str | None) -> tuple[str, str | None]:
    """``eng`` plus the show's script when its pack is installed; a note when it is missing."""

    pack = OCR_LANGUAGES.get((spoken or "en").lower())
    if pack is None:
        return "eng", None
    listed = subprocess.run(
        [binary, "--list-langs"], capture_output=True, text=True, timeout=30
    )
    if pack in listed.stdout.split():
        return f"eng+{pack}", None
    return (
        "eng",
        f"Latin script only: no tesseract `{pack}` pack, so drawn {spoken} text is not read",
    )


def tesseract_runner(binary: str) -> OcrRunner:
    """Read every frame in one ``tesseract`` call (sparse text, TSV out)."""

    def run(frames: Sequence[Path], languages: str) -> str:
        with tempfile.TemporaryDirectory(prefix="fictora-ocr-") as tmp:
            listing = Path(tmp) / "frames.txt"
            listing.write_text(
                "\n".join(str(p) for p in frames) + "\n", encoding="utf-8"
            )
            done = subprocess.run(
                [binary, str(listing), "stdout", "-l", languages, "--psm", "11", "tsv"],
                capture_output=True, text=True, timeout=600,
            )  # fmt: skip
        if done.returncode != 0:
            raise MediaToolError(f"tesseract failed: {done.stderr[-300:]}")
        return done.stdout

    return run


def parse_tsv(tsv: str) -> list[Word]:
    """Words from tesseract TSV (``level`` 5 rows), ``sample`` = page number - 1."""

    words: list[Word] = []
    for raw in tsv.splitlines()[1:]:
        cols = raw.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
            page, left, top, width, height = (int(c) for c in (cols[1], *cols[6:10]))
        except ValueError:
            continue
        if text and conf >= 0:
            words.append(Word(page - 1, left, top, width, height, conf, text))
    return words


def _word_ok(word: Word, frame_height: int) -> bool:
    letters = word.letters
    share = word.height / max(1, frame_height)
    return (
        word.conf >= MIN_WORD_CONF
        and letters >= 2
        and letters >= 0.5 * len(word.text)
        and MIN_TEXT_HEIGHT <= share <= MAX_TEXT_HEIGHT
    )


def rows_of(words: Iterable[Word], size: tuple[int, int]) -> list[Row]:
    """Group a sample's readable words into rows (vertically overlapping, side by side)."""

    by_sample: dict[int, list[Word]] = {}
    for word in words:
        if _word_ok(word, size[1]):
            by_sample.setdefault(word.sample, []).append(word)
    rows: list[Row] = []
    for sample, found in sorted(by_sample.items()):
        found.sort(key=lambda w: w.top + w.height / 2)
        current: list[Word] = []
        for word in found:
            centre = word.top + word.height / 2
            if current:
                last = sum(w.top + w.height / 2 for w in current) / len(current)
                tall = max(w.height for w in current)
                if abs(centre - last) > 0.6 * max(tall, word.height):
                    rows.append(Row(sample, tuple(current)))
                    current = []
            current.append(word)
        if current:
            rows.append(Row(sample, tuple(current)))
    return [
        row
        for row in rows
        if len(row.words) >= 2
        and sum(w.letters for w in row.words) >= MIN_ROW_LETTERS
        and sum(w.conf for w in row.words) / len(row.words) >= MIN_ROW_CONF
    ]


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[^\W\d_]+", text.casefold()) if len(t) >= 2]


def matching_line(text: str, lines: Sequence[Mapping[str, str]]) -> str | None:
    """The ``line_id`` of the spoken line a row reads as, or ``None``."""

    words = _tokens(text)
    if len(words) < 2:
        return None
    best: tuple[float, str | None] = (0.0, None)
    for line in lines:
        said = set()
        for key in ("subtitle", "text", "performed", "spoken_text"):
            said |= set(_tokens(str(line.get(key) or "")))
        if not said:
            continue
        share = sum(1 for w in words if w in said) / len(words)
        if share > best[0]:
            best = (share, str(line.get("line_id") or "") or None)
    return best[1] if best[0] >= LINE_MATCH_SHARE else None


def _in_band(box: tuple[int, int, int, int], size: tuple[int, int]) -> bool:
    left, top, right, bottom = box
    centre_y = (top + bottom) / 2 / max(1, size[1])
    centre_x = (left + right) / 2 / max(1, size[0])
    return centre_y >= CAPTION_BAND_TOP and abs(centre_x - 0.5) <= CAPTION_CENTRE_SLACK


def classify(
    rows: Sequence[Row],
    times: Sequence[float],
    size: tuple[int, int],
    *,
    lines: Sequence[Mapping[str, str]] = (),
) -> list[Finding]:
    """Group rows by height in the frame across samples, and name each group ``subtitle`` or ``text``.

    A group is placed by the box around all its rows, so a sample that read only
    part of a centred subtitle stays with the rest of it.
    """

    groups: list[tuple[float, list[Row]]] = []
    for row in sorted(rows, key=lambda r: r.sample):
        centre = (row.box[1] + row.box[3]) / 2 / max(1, size[1])
        for i, (g_centre, g_rows) in enumerate(groups):
            if abs(g_centre - centre) <= GROUP_SLACK:
                g_rows.append(row)
                groups[i] = (
                    (g_centre * (len(g_rows) - 1) + centre) / len(g_rows),
                    g_rows,
                )
                break
        else:
            groups.append((centre, [row]))
    findings: list[Finding] = []
    total = max(1, len(times))
    for _centre, g_rows in groups:
        finding = Finding("text", list(g_rows))
        seen = sorted({r.sample for r in g_rows})
        finding.times = [round(times[s], 3) for s in seen if s < len(times)]
        finding.line_id = next(
            (m for r in g_rows if (m := matching_line(r.text, lines))), None
        )
        everywhere = len(seen) / total >= SET_DRESSING_SHARE
        if finding.line_id is not None or (
            _in_band(finding.box, size) and len(seen) >= 2 and not everywhere
        ):
            finding.kind = "subtitle"
        findings.append(finding)
    return findings


def sample_times(
    duration: float, windows: Sequence[tuple[float, float]] = ()
) -> list[float]:
    """Times to read: every half second, and every quarter second inside a spoken-line window."""

    times: list[float] = []
    step = 1.0 / LINE_SAMPLE_FPS
    ratio = round(LINE_SAMPLE_FPS / SAMPLE_FPS)
    index = 0
    while index * step < duration:
        t = index * step
        if index % ratio == 0 or any(a <= t < b for a, b in windows):
            times.append(round(t, 3))
        index += 1
    return times


def line_windows(facts: Mapping[str, Any] | None) -> list[tuple[float, float]]:
    """The spoken-line windows of a take, from its facts (``lines[].start_seconds/end_seconds``)."""

    windows: list[tuple[float, float]] = []
    for line in (facts or {}).get("lines") or []:
        if not isinstance(line, Mapping):
            continue
        start, end = line.get("start_seconds"), line.get("end_seconds")
        if (
            isinstance(start, (int, float))
            and isinstance(end, (int, float))
            and end > start
        ):
            windows.append((float(start), float(end)))
    return windows


def _grab(take: Path, times: Sequence[float], folder: Path) -> list[Path]:
    """Write the frames at ``times`` as grey PNGs (decoded at the line rate, one frame in memory)."""

    wanted = {round(t * LINE_SAMPLE_FPS) for t in times}
    pattern = folder / "all-%05d.png"
    done = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(take), "-vf",
         f"fps={LINE_SAMPLE_FPS:g}:round=down,format=gray", "-start_number", "0", str(pattern)],
        capture_output=True, text=True, timeout=600,
    )  # fmt: skip
    if done.returncode != 0:
        raise MediaToolError(f"frame grab failed on {take.name}: {done.stderr[-300:]}")
    kept: list[Path] = []
    for path in sorted(folder.glob("all-*.png")):
        index = int(path.stem.split("-")[1])
        if index in wanted:
            kept.append(path)
        else:
            path.unlink()
    return kept


def crop_sheet(
    findings: Sequence[Finding], frames: Sequence[Path], out: Path, *, limit: int = 6
) -> Path:
    """Save one crop per finding (its first sample, padded box outlined), stacked."""

    crops: list[Image.Image] = []
    for finding in list(findings)[:limit]:
        row = finding.rows[0]
        with Image.open(frames[row.sample]) as opened:
            frame = opened.convert("RGB")
        left, top, right, bottom = finding.box
        pad = 40
        box = (max(0, left - pad), max(0, top - pad), min(frame.width, right + pad),
               min(frame.height, bottom + pad))  # fmt: skip
        crop = frame.crop(box)
        ImageDraw.Draw(crop).rectangle(
            (left - box[0], top - box[1], right - box[0], bottom - box[1]),
            outline=(255, 0, 0),
            width=3,
        )
        crops.append(crop)
    width = max(c.width for c in crops)
    sheet = Image.new("RGB", (width, sum(c.height for c in crops)), (0, 0, 0))
    y = 0
    for crop in crops:
        sheet.paste(crop, (0, y))
        y += crop.height
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def check_take_text(
    take: Path,
    *,
    lines: Sequence[Mapping[str, str]] = (),
    facts: Mapping[str, Any] | None = None,
    spoken_language: str | None = None,
    sheet_dir: Path | None = None,
    sheet_stem: str = "text-check",
    ocr: OcrRunner | None = None,
) -> TextCheck:
    """Look for drawn subtitles in one raw take.

    Parameters
    ----------
    take
        The raw take (before house captions: a finished file carries ours).
    lines
        The take's spoken lines (``episode_dialogue`` rows), to tell a drawn line from a sign.
    facts
        The take's facts, for the spoken-line windows (sampled more often).
    spoken_language
        ``ja`` / ``ko`` / ``zh`` add that script when its tesseract pack is installed.
    sheet_dir, sheet_stem
        Where the crop sheet of drawn subtitles is saved (``sheet_stem-vN.png``); ``None`` saves none.
    ocr
        Injected for tests; default the ``tesseract`` command (skipped, loudly, when not installed).

    Returns
    -------
    TextCheck
        ``found`` (drawn subtitles), ``clean`` or ``skipped`` with the reason.

    Raises
    ------
    MediaToolError
        When ffmpeg or tesseract fails on the file.
    """

    note = ""
    languages = "eng"
    if ocr is None:
        binary = tesseract_bin()
        if binary is None:
            return TextCheck(take, "skipped", SKIPPED_NO_OCR)
        languages, missing = ocr_languages(binary, spoken_language)
        note = missing or ""
        ocr = tesseract_runner(binary)
    info = probe_video(take)
    size = (info.width, info.height)
    times = sample_times(info.duration_seconds, line_windows(facts))
    with tempfile.TemporaryDirectory(prefix="fictora-text-") as tmp:
        frames = _grab(take, times, Path(tmp))
        times = times[: len(frames)]
        words = parse_tsv(ocr(frames, languages))
        findings = classify(rows_of(words, size), times, size, lines=lines)
        check = TextCheck(
            take,
            "found" if any(f.kind == "subtitle" for f in findings) else "clean",
            note,
            findings,
            len(frames),
            size,
        )
        if check.subtitles and sheet_dir is not None:
            sheet_dir.mkdir(parents=True, exist_ok=True)
            check.sheet = crop_sheet(
                check.subtitles,
                frames,
                next_versioned_path(sheet_dir, sheet_stem, ".png"),
            )
    return check


def desk_text_check(
    desk: Path, episode: int, take_id: str, take: Path, *, ocr: OcrRunner | None = None
) -> TextCheck:
    """:func:`check_take_text` with the take's lines, facts and language read from the desk."""

    import json

    from creation.post.desk import saved_spine, show_language
    from creation.post.review import take_lines
    from creation.post.sfx import saved_take_facts

    found_lines = take_lines(desk, episode, take_id)
    lines, language = found_lines if found_lines else ([], None)
    if not lines:
        spine = saved_spine(desk, episode)
        language = show_language(spine[0]) if spine else None
    facts_path = saved_take_facts(desk, episode, take_id)
    facts = json.loads(facts_path.read_text(encoding="utf-8")) if facts_path else None
    return check_take_text(
        take,
        lines=lines,
        facts=facts,
        spoken_language=language,
        sheet_dir=desk / f"ep{episode:02d}" / "takes",
        sheet_stem=f"text-check-ep{episode:02d}-{take_id}",
        ocr=ocr,
    )


__all__ = [
    "SKIPPED_NO_OCR",
    "Finding",
    "TextCheck",
    "check_take_text",
    "classify",
    "desk_text_check",
    "line_windows",
    "matching_line",
    "parse_tsv",
    "rows_of",
    "sample_times",
    "tesseract_bin",
]
