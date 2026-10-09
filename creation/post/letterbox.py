"""A letterbox show's 9:16 deliverable: the 4:3 picture on black, the title block, the mark, band captions.

Founder decisions of 6 Oct 2026 (compared with the "Not Home" reference
episode and the hand-built "Three Payments Late" file). Only a show whose
``delivery_format`` is ``letterbox`` AND whose take is really 4:3 goes through
this module; a portrait show, and a letterbox show filmed portrait (the
title-bar path in :mod:`creation.post.hook_overlay`), are untouched
(``tests/test_letterbox_portrait_unchanged.py``).

What ``finish`` and ``join`` make for such a show (positions in
:mod:`creation.post.delivery_geometry`: the "Not Home" reference, user decision
2026-10-06; fictora-drama's letterbox delivery uses the same numbers):

* **Canvas.** 1080x1920, pure black; the 4:3 picture scaled to 1080x810 and
  centred (y 555-1365). ``finish`` puts the picture on the canvas right after
  the mix (:func:`pad_to_canvas`); the 4:3 takes before it stay on the desk.
* **Captions** in the band under the picture, burned on the canvas by the
  captions step (``caption_take(layout="letterbox")``): Arial Bold (ASS 62),
  ink top y 1417, centred on the frame unless its right edge would cross x 950
  (then ending on it), one line preferred (down to ASS 52; a two-line chunk moves up to end above y 1536,
  never above y 1385); yellow by default, white with ``--caption-colour``.
  They build up in phrases (:func:`creation.captions.phrase_cues`), end when
  the voice does on a locked-voice take (:func:`voice_end_spans`), and an
  on-screen speaker's line is upright (:func:`italic_overrides`).
* **Mark and title** go on last, with the mark (:func:`mark_and_title`): the
  Sokii mark in the top band (ink x 38-97, y 179-228), never on the picture; the title
  block just above the picture: the setup line in white (the episode's
  own ``title_line`` from ``hook-line --setup-line``, else the series title) and the hook line in house yellow
  (``--hook-line``, else the desk's pick from ``hook-line``, else the spine's
  ``hook_line_selected``), each on at most two lines, for the whole video.
  The master ``join`` reads is the captioned canvas without mark or title:
  ``join`` puts them on the joined file once.
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from creation.caption_dashes import drawn_text as no_dash_text
from creation.captions import (
    FONTS_DIR,
    FONT_NAME,
    Span,
    burnable_ass,
    _ass_escape,
    _ass_time,
    text_width,
)
from creation.post.delivery_geometry import (
    CANVAS_COLOUR,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CAPTION_COLOURS,
    DEFAULT_CAPTION_COLOUR,
    TITLE_HOOK_COLOUR,
    TITLE_SETUP_COLOUR,
    LetterboxLayout,
    layout,
)
from creation.post.hook_overlay import delivery_format, is_four_three
from creation.post.media import probe_video, run_ffmpeg
from creation.post.watermark import MARK, MARK_ALPHA

#: Where an operator's hook-line pick is kept when the server cannot store it (an older server).
HOOK_LINE_FILE = Path("shared") / "hook-line.json"
#: Where an operator's setup line is kept when the server cannot store it (before fictora-drama #628).
SETUP_LINE_FILE = Path("shared") / "setup-line.json"
#: The longest setup line the server takes (fictora-drama ``TITLE_LINE_MAX_CHARS``).
SETUP_LINE_MAX_CHARS = 60
#: A line's caption ends at the voice only when the voice stops at least this much before its window does.
VOICE_END_MIN_TRIM = 0.1
#: Level windows for the voice-end measure.
VOICE_WINDOW_SECONDS = 0.05
#: Inside a line's window, voice is louder than (loudest - this) and than the gap's level + :data:`VOICE_OVER_GAP_DB`.
VOICE_BELOW_PEAK_DB = 20.0
VOICE_OVER_GAP_DB = 3.0
#: The gap next to a line must be this long to read its level from.
VOICE_GAP_SECONDS = 0.3
#: Words that say a speaker is not drawn, near their name in a board note.
_NOT_DRAWN = re.compile(
    r"\b(not in (?:the )?frame|out of (?:the )?frame|off[- ]screen|offscreen|unseen|not visible|never seen|"
    r"not shown|heard only|voice only)\b",
    re.IGNORECASE,
)


def is_letterbox_take(spine: Mapping[str, Any] | None, size: tuple[int, int]) -> bool:
    """True for a letterbox show's real 4:3 take (the only case this module builds for)."""

    return delivery_format(spine) == "letterbox" and is_four_three(*size)


# --- caption colour ---------------------------------------------------------------------------------


def resolve_caption_colour(
    desk: Path, spine: Mapping[str, Any] | None, given: str | None = None
) -> tuple[str, str]:
    """The letterbox caption colour: ``--caption-colour``, else the desk's config, else the spine's, else yellow.

    Parameters
    ----------
    desk
        Series desk (``production.config.json`` ``letterbox_caption_colour``).
    spine
        The show's spine (``letterbox_caption_colour``: the creator's pick in the app).
    given
        The command's flag.

    Returns
    -------
    tuple[str, str]
        ``yellow`` or ``white``, and where it came from.

    Raises
    ------
    ValueError
        When a colour other than yellow or white is asked for.
    """

    from creation.production_config import load_production_config

    def checked(value: Any, where: str) -> str:
        name = str(value).strip().casefold()
        if name not in CAPTION_COLOURS:
            raise ValueError(
                f"{where}: caption colour {value!r}; use {' or '.join(CAPTION_COLOURS)}"
            )
        return name

    if given:
        return checked(given, "--caption-colour"), "--caption-colour"
    desk_value = load_production_config(desk).letterbox_caption_colour
    if desk_value:
        return checked(
            desk_value, "production.config.json letterbox_caption_colour"
        ), "the desk's production.config.json"
    body = (spine or {}).get("spine", spine) if isinstance(spine, Mapping) else {}
    server = body.get("letterbox_caption_colour") if isinstance(body, Mapping) else None
    if isinstance(server, str) and server.strip().casefold() in CAPTION_COLOURS:
        return server.strip().casefold(), "the show's setting (spine)"
    return DEFAULT_CAPTION_COLOUR, "the default"


# --- the hook line the operator picked on the desk ----------------------------------------------------


def _hook_file(desk: Path) -> Path:
    return desk.expanduser().resolve() / HOOK_LINE_FILE


def desk_hook_line(desk: Path, episode: int) -> dict[str, Any] | None:
    """The hook-line pick saved on the desk for one episode (``shared/hook-line.json``), or ``None``."""

    path = _hook_file(desk)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    entry = (
        (raw.get("episodes") or {}).get(str(episode)) if isinstance(raw, dict) else None
    )
    if not isinstance(entry, dict):
        return None
    return dict(entry) if entry.get("text") or entry.get("kind") == "off" else None


def record_desk_hook_line(
    desk: Path, episode: int, choice: Mapping[str, Any], *, why: str
) -> Path:
    """Save the operator's hook-line pick on the desk (the server could not store it)."""

    path = _hook_file(desk)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    )
    episodes = dict(raw.get("episodes") or {})
    episodes[str(episode)] = {
        **dict(choice),
        "why": why,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.write_text(
        json.dumps({"episodes": episodes}, indent=2) + "\n", encoding="utf-8"
    )
    return path


def keep_finish_hook_line(
    desk: Path, episode: int, *, hook_line: str | None, no_hook_line: bool
) -> Path | None:
    """Save ``finish --hook-line TEXT`` / ``--no-hook-line`` as the episode's desk pick, so ``join`` lays it too.

    Without this, ``join`` (and the next ``finish``) fell back to the spine's
    ``hook_line_selected`` and the joined episode opened on a different line
    than the finished take (L-20261006-5). The pick goes to the same file
    ``hook-line`` writes on an older server (``shared/hook-line.json``); a later
    ``hook-line --pick`` the server stores drops it again.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal the finish was for.
    hook_line
        ``--hook-line`` as given (blank or ``None``: nothing to keep).
    no_hook_line
        ``--no-hook-line`` was given.

    Returns
    -------
    Path | None
        The file written, or ``None`` when neither flag was given.
    """

    text = " ".join(str(hook_line or "").split())
    if no_hook_line:
        choice: dict[str, Any] = {"kind": "off", "text": ""}
        why = "finish --no-hook-line"
    elif text:
        choice = {"kind": "custom", "text": text}
        why = "finish --hook-line"
    else:
        return None
    return record_desk_hook_line(desk, episode, choice, why=why)


def clear_desk_hook_line(desk: Path, episode: int) -> bool:
    """Drop the desk's pick for one episode (the server now holds the creator's pick). True when one was dropped."""

    path = _hook_file(desk)
    if not path.is_file():
        return False
    raw = json.loads(path.read_text(encoding="utf-8"))
    episodes = dict(raw.get("episodes") or {})
    if episodes.pop(str(episode), None) is None:
        return False
    path.write_text(
        json.dumps({"episodes": episodes}, indent=2) + "\n", encoding="utf-8"
    )
    return True


# --- the episode's own setup line the operator set on the desk ---------------------------------------


def _episodes(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return dict(raw.get("episodes") or {}) if isinstance(raw, dict) else {}


def desk_setup_line(desk: Path, episode: int) -> dict[str, Any] | None:
    """The setup line kept on the desk for one episode (``shared/setup-line.json``), or ``None``.

    Returns
    -------
    dict[str, Any] | None
        ``{"kind": "custom", "text": ...}`` or ``{"kind": "default"}`` (the series title), or ``None``.
    """

    entry = _episodes(desk.expanduser().resolve() / SETUP_LINE_FILE).get(str(episode))
    if not isinstance(entry, dict) or entry.get("kind") not in ("custom", "default"):
        return None
    if entry["kind"] == "custom":
        text = " ".join(str(entry.get("text") or "").split())
        return {"kind": "custom", "text": text} if text else None
    return {"kind": "default"}


def record_desk_setup_line(
    desk: Path, episode: int, choice: Mapping[str, Any], *, why: str
) -> Path:
    """Keep the operator's setup line on the desk (the server could not store it)."""

    path = desk.expanduser().resolve() / SETUP_LINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    episodes = _episodes(path)
    episodes[str(episode)] = {
        **dict(choice),
        "why": why,
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.write_text(
        json.dumps({"episodes": episodes}, indent=2) + "\n", encoding="utf-8"
    )
    return path


def clear_desk_setup_line(desk: Path, episode: int) -> bool:
    """Drop the desk's setup line for one episode (the server holds it now). True when one was dropped."""

    path = desk.expanduser().resolve() / SETUP_LINE_FILE
    episodes = _episodes(path)
    if episodes.pop(str(episode), None) is None:
        return False
    path.write_text(
        json.dumps({"episodes": episodes}, indent=2) + "\n", encoding="utf-8"
    )
    return True


# --- the title block --------------------------------------------------------------------------------


@dataclass(frozen=True)
class TitleBlock:
    """The two parts of the title above the picture, and where the hook line came from."""

    setup: str
    hook: str
    hook_source: str = "spine"
    setup_source: str = "the series title"

    def describe(self) -> str:
        own = (
            "" if self.setup_source == "the series title" else f" ({self.setup_source})"
        )
        parts = [f"setup {self.setup!r}{own}" if self.setup else "", ""]
        if self.hook:
            parts[1] = f"hook {self.hook!r} ({self.hook_source})"
        return "title block: " + "; ".join(p for p in parts if p)


def title_block(
    spine: Mapping[str, Any] | None,
    episode: int,
    *,
    desk: Path | None = None,
    override: str | None = None,
    off: bool = False,
) -> tuple[TitleBlock | None, str]:
    """The title block for one episode's letterbox file, or ``None`` and why.

    Line 1 (white) is the episode's own setup line: the desk's (``hook-line
    --setup-line`` on a server without the field), else the spine's
    ``episode_summaries[].title_line`` (fictora-drama #628), else the series
    ``title`` (the server's fallback when no run title is given).
    Line 2 (yellow) is ``override`` (``--hook-line``), else the desk's pick
    (``hook-line`` on an older server), else the spine's ``hook_line_selected``
    (nothing when it is ``off``).
    """

    from creation.post.hook_overlay import selected_hook_line

    if off:
        return None, "no title block (--no-hook-line)"
    body = (spine or {}).get("spine", spine) if isinstance(spine, Mapping) else {}
    body = body if isinstance(body, Mapping) else {}
    # The server's setup line is the episode's title_line, else the run's title, else the series title
    # (fictora-drama #614, #628). The kit has no run title.
    from creation.spine_view import episode_summary

    setup = " ".join(str(body.get("title") or "").split())
    setup_source = "the series title"
    kept = desk_setup_line(desk, episode) if desk is not None else None
    own = " ".join(str(episode_summary(body, episode).get("title_line") or "").split())
    if kept is not None:
        if kept["kind"] == "custom":
            setup, setup_source = (
                kept["text"],
                "the desk's setup line (hook-line --setup-line)",
            )
    elif own:
        setup, setup_source = own, "the episode's setup line"
    hook, source = "", "spine"
    if override and override.strip():
        hook, source = " ".join(override.split()), "--hook-line"
    else:
        picked = desk_hook_line(desk, episode) if desk is not None else None
        if picked:
            hook, source = (
                " ".join(str(picked.get("text") or "").split()),
                "the desk's pick (hook-line)",
            )
        else:
            hook = selected_hook_line(body, episode) or ""
    if not setup and not hook:
        return (
            None,
            "no title block: the show has no title and the episode no hook line",
        )
    return TitleBlock(setup, hook, source, setup_source), ""


def _two_lines(text: str, size: int, room: float) -> list[str] | None:
    """``text`` on one line, or two balanced lines, inside ``room`` px at ``size``; ``None`` when it needs more."""

    if not text:
        return []
    if text_width(text, size) <= room:
        return [text]
    words = text.split()
    best: tuple[float, list[str]] | None = None
    for cut in range(1, len(words)):
        top, bottom = " ".join(words[:cut]), " ".join(words[cut:])
        widest = max(text_width(top, size), text_width(bottom, size))
        if best is None or widest < best[0]:
            best = (widest, [top, bottom])
    if best is None or best[0] > room:
        return None
    return best[1]


@dataclass(frozen=True)
class FittedTitle:
    """The title block laid out: one size for both parts, each on at most two lines."""

    size: int
    setup: tuple[str, ...]
    hook: tuple[str, ...]
    note: str = ""


def _block_height(place: LetterboxLayout, size: int, lines: int) -> float:
    """From the first line's cap top to the last baseline: (lines - 1) pitches and one cap height."""

    from creation.post.delivery_geometry import ink_offset

    cap = size - place.line_descent(size) - ink_offset(size)
    return (lines - 1) * place.title_pitch(size) + cap


def fit_title(block: TitleBlock, place: LetterboxLayout) -> FittedTitle:
    """The largest title size (ASS 62, down 2 at a time to 52) at which both parts fit two lines each.

    Each part may take two lines inside the title box's width (972 px); the
    block may not rise above the box (under the mark). When even 52 does not
    fit, 52 is used, each part shrunk to its two lines (the words are
    never cut), and the note says to use fewer words.
    """

    box = place.title
    sizes = place.title_sizes
    for size in sizes:
        setup = _two_lines(block.setup, size, box.width)
        hook = _two_lines(block.hook, size, box.width)
        if setup is None or hook is None:
            continue
        if _block_height(place, size, len(setup) + len(hook)) <= box.height:
            return FittedTitle(size, tuple(setup), tuple(hook))
    from creation.post.hook_overlay import _fit

    size = sizes[-1]
    setup_lines, setup_size = (
        _fit(block.setup, size, box.width, 8) if block.setup else ([], size)
    )
    hook_lines, hook_size = (
        _fit(block.hook, size, box.width, 8) if block.hook else ([], size)
    )
    size = min(setup_size, hook_size)
    while (
        size > 8
        and _block_height(place, size, len(setup_lines) + len(hook_lines)) > box.height
    ):
        size -= 2
    return FittedTitle(
        size,
        tuple(setup_lines),
        tuple(hook_lines),
        f"!! the title block does not fit the band at the smallest size ({sizes[-1]} px): set at {size} px; "
        "a shorter hook line reads better (`hook-line --text`)",
    )


def title_ass(
    block: TitleBlock, *, width: int = CANVAS_WIDTH, height: int = CANVAS_HEIGHT,
    duration: float | None = None,
) -> tuple[str, FittedTitle]:  # fmt: skip
    """The ASS that draws the title block on the canvas for the whole video, and how it was fitted.

    Arial Bold, upright, no outline (it sits on black), centred. One event per
    line, each on its own baseline: the last line's baseline at y 435, the
    lines above it one pitch (63 px at 62) apart, so the block grows upward.
    The setup line white, the hook line house yellow.
    """

    place = layout(width, height)
    # No em or en dash on the picture (6 Oct 2026; creation.caption_dashes).
    block = replace(
        block, setup=no_dash_text(block.setup), hook=no_dash_text(block.hook)
    )
    fitted = fit_title(block, place)
    end = _ass_time(duration if duration else 24 * 3600 - 1)
    lines = [(TITLE_SETUP_COLOUR, line) for line in fitted.setup] + [
        (TITLE_HOOK_COLOUR, line) for line in fitted.hook
    ]
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
        f"Style: Title,{FONT_NAME},{fitted.size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,0,0,2,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    pitch = place.title_pitch(fitted.size)
    descent = place.line_descent(fitted.size)
    events = []
    for index, (colour, line) in enumerate(lines):
        baseline = place.title.bottom - (len(lines) - 1 - index) * pitch
        events.append(
            f"Dialogue: 0,{_ass_time(0)},{end},Title,,0,0,0,,"
            f"{{\\an2\\pos({place.canvas.width // 2},{round(baseline + descent)})\\c{colour}}}"
            f"{_ass_escape(line)}\n"
        )
    return header + "".join(events), fitted


# --- the picture on the canvas, and the mark and title --------------------------------------------


def _esc(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def pad_to_canvas(take: Path, out: Path) -> Path:
    """Put a 4:3 take on the 1080x1920 black canvas: scaled to 1080x810, centred (sound copied).

    Raises
    ------
    FileExistsError
        When ``out`` exists (local post never overwrites).
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    place = layout()
    pic = place.picture
    info = probe_video(take)
    args = ["-i", str(take), "-vf",
            f"scale={pic.width}:{pic.height}:flags=lanczos,setsar=1,"
            f"pad={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{pic.x}:{pic.y}:color={CANVAS_COLOUR}",
            "-map", "0:v:0"]  # fmt: skip
    if info.has_audio:
        args += ["-map", "0:a:0", "-c:a", "copy"]
    run_ffmpeg(
        [*args, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
    )
    return out


def mark_and_title(
    video: Path,
    out: Path,
    *,
    title: TitleBlock | None,
    ass_path: Path | None = None,
    mark: Path = MARK,
    cover: Path | None = None,
) -> tuple[Path, FittedTitle | None]:
    """Put the Sokii mark in the top band and the title block above the picture, in one pass (sound copied).

    ``cover`` becomes the first frame in the same pass (:mod:`creation.post.cover_frame`).

    Parameters
    ----------
    video
        The captioned canvas (``finish``'s master, or ``join``'s joined master).
    out
        The marked file.
    title
        The title block, or ``None`` for the mark alone.
    ass_path
        Where the title's ASS is written (beside ``out`` when ``None``).
    mark
        The mark image.

    Returns
    -------
    tuple[Path, FittedTitle | None]
        ``out``, and how the title was fitted.

    Raises
    ------
    FileNotFoundError
        When the mark image is missing.
    FileExistsError
        When ``out`` exists.
    """

    if not mark.is_file():
        raise FileNotFoundError(f"watermark not found: {mark}")
    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    info = probe_video(video)
    place = layout(info.width, info.height)
    x, y = place.mark
    fitted: FittedTitle | None = None
    first = "[0:v]null[t]"
    scratch: Path | None = None
    if title is not None:
        text, fitted = title_ass(
            title, width=info.width, height=info.height, duration=info.duration_seconds
        )
        ass = ass_path or out.with_name(f"{out.stem}-title.ass")
        ass.write_text(text, encoding="utf-8")
        # One named face (the bundled twin when Arial is missing); the copy is removed below.
        burned, scratch = burnable_ass(ass)
        first = f"[0:v]ass='{_esc(burned)}':fontsdir='{_esc(FONTS_DIR)}'[t]"
    graph = f"{first};[1:v]format=rgba,colorchannelmixer=aa={MARK_ALPHA}[m];[t][m]overlay={x}:{y}[v]"
    inputs = ["-i", str(video), "-i", str(mark)]
    picture = "[v]"
    if cover is not None:
        from creation.post.cover_frame import cover_frame_graph

        inputs += ["-i", str(cover)]
        graph += ";" + cover_frame_graph(
            cover_input=2,
            picture="[v]",
            width=info.width,
            height=info.height,
            out="[vc]",
        )
        picture = "[vc]"
    args = [*inputs, "-filter_complex", graph, "-map", picture]
    if info.has_audio:
        args += ["-map", "0:a", "-c:a", "copy"]
    try:
        run_ffmpeg(
            [*args, "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
        )
    finally:
        if scratch is not None:
            scratch.unlink(missing_ok=True)
    return out, fitted


# --- captions end with the voice ------------------------------------------------------------------

Meter = Callable[..., Sequence[float]]


@dataclass(frozen=True)
class VoiceEnd:
    """One line's window and where its voice was found to end."""

    line_id: str
    window: Span
    end: float
    how: str

    @property
    def trimmed(self) -> bool:
        return self.window.end - self.end >= VOICE_END_MIN_TRIM

    def text(self) -> str:
        if not self.trimmed:
            return f"{self.line_id} ends with its window {self.window.end:.2f}s ({self.how})"
        return f"{self.line_id} ends {self.end:.2f}s, its window {self.window.end:.2f}s ({self.how})"


def _gap_level(
    levels: Sequence[float], start: float, end: float, window: float
) -> float | None:
    if end - start < VOICE_GAP_SECONDS:
        return None
    first, last = int(round(start / window)), int(round(end / window))
    inside = [lv for lv in levels[first:last] if lv > -119]
    return statistics.median(inside) if inside else None


def voice_end_spans(
    take: Path,
    lines: Sequence[Any],
    *,
    words: Sequence[Any] | None = None,
    measure: Meter | None = None,
    duration: float | None = None,
) -> list[VoiceEnd]:
    """Where each locked-voice line's voice really ends, so its caption goes off with it.

    The take facts' line windows are where the server placed each line on the
    dialogue track; the voice can stop well before the window does (the
    "Three Payments Late" captions stayed about a second past it). Two
    sources, best first:

    * ``words``: a transcript of this take (``review --transcribe``, saved on
      the desk): the line ends at the last word heard inside its window.
    * the take's own sound (the dialogue track the video was filmed to): the
      last 0.05 s inside the window louder than both the window's loudest
      minus 20 dB and the level of the gap next to the line plus 3 dB. A line
      with no gap of at least 0.3 s on either side to read that level from
      keeps its window.

    A line only ever ends earlier than its window, never later, and never in
    the first half of it.

    Parameters
    ----------
    take
        The take as filmed (its sound is the dialogue track).
    lines
        The soundtrack's lines (``line_id``, ``start``, ``end``), earliest first.
    words
        Transcript words (``start``, ``end``) of this take, or ``None``.
    measure
        Level meter (``creation.post.media.measure_rms_windows``); tests pass one.
    duration
        The take's length (default: probed).

    Returns
    -------
    list[VoiceEnd]
        One per line, in order.
    """

    from creation.post.media import measure_rms_windows

    found: list[VoiceEnd] = []
    levels: Sequence[float] | None = None
    if not words:
        levels = (measure or measure_rms_windows)(
            take, window_seconds=VOICE_WINDOW_SECONDS
        )
    total = duration if duration is not None else (
        len(levels) * VOICE_WINDOW_SECONDS if levels is not None else probe_video(take).duration_seconds
    )  # fmt: skip
    w = VOICE_WINDOW_SECONDS
    for index, line in enumerate(lines):
        window = Span(float(line.start), float(line.end))
        floor_at = window.start + window.duration / 2
        if words:
            heard = [
                x for x in words if window.start - 0.3 <= float(x.start) < window.end
            ]
            if not heard:
                found.append(
                    VoiceEnd(line.line_id, window, window.end, "no word heard in it")
                )
                continue
            end = min(window.end, max(float(x.end) for x in heard))
            found.append(
                VoiceEnd(line.line_id, window, max(end, floor_at), "transcript words")
            )
            continue
        assert levels is not None
        after_end = float(lines[index + 1].start) if index + 1 < len(lines) else total
        before_start = float(lines[index - 1].end) if index else 0.0
        gap = _gap_level(levels, window.end, min(after_end, window.end + 0.5), w)
        if gap is None:
            gap = _gap_level(
                levels, max(before_start, window.start - 0.5), window.start, w
            )
        if gap is None:
            found.append(
                VoiceEnd(
                    line.line_id, window, window.end, "no gap next to it to measure"
                )
            )
            continue
        first, last = int(round(window.start / w)), int(round(window.end / w))
        inside = list(levels[first:last])
        if not inside:
            found.append(VoiceEnd(line.line_id, window, window.end, "not measured"))
            continue
        threshold = max(max(inside) - VOICE_BELOW_PEAK_DB, gap + VOICE_OVER_GAP_DB)
        loud = [i for i, level in enumerate(inside) if level > threshold]
        if not loud:
            found.append(
                VoiceEnd(line.line_id, window, window.end, "no voice measured")
            )
            continue
        end = min(window.end, round((first + loud[-1] + 1) * w, 3))
        found.append(
            VoiceEnd(
                line.line_id,
                window,
                max(end, floor_at),
                "measured on the dialogue track",
            )
        )
    return found


# --- italics: is the speaker drawn on that shot? -------------------------------------------------


def _first_name(card: Mapping[str, Any]) -> str:
    name = str(card.get("name") or "").strip()
    return name.split()[0] if name else ""


def _named_in_note(note: str, name: str) -> bool:
    if not note or not name:
        return False
    for sentence in re.split(r"(?<=[.!?;])\s+", note):
        if re.search(
            rf"\b{re.escape(name)}\b", sentence, re.IGNORECASE
        ) and not _NOT_DRAWN.search(sentence):
            return True
    return False


def drawn_on_shot(
    spine: Mapping[str, Any],
    facts: Mapping[str, Any] | None,
    *,
    line_id: str,
    cast_id: str,
    at: float | None = None,
) -> tuple[bool | None, str]:
    """Whether the take draws a line's speaker on the shot the line plays over, and the evidence.

    Evidence, first that answers: the take facts' shot (the one the line's
    window or ``lines[].shot_index`` falls in) names the speaker among its
    people; the board frames of that shot list the speaker in ``cast_refs``;
    the redrawn board's note (``board_correction``) puts the speaker in frame
    by name (and does not say they are off screen). ``None`` when nothing on
    the desk says.
    """

    body = spine.get("spine", spine)
    take = (facts or {}).get("take_facts", facts) if isinstance(facts, Mapping) else {}
    take = take if isinstance(take, Mapping) else {}
    shots = [s for s in take.get("shots") or [] if isinstance(s, Mapping)]
    shot_index: int | None = None
    for item in take.get("lines") or []:
        if (
            isinstance(item, Mapping)
            and item.get("line_id") == line_id
            and item.get("shot_index")
        ):
            shot_index = int(item["shot_index"])
    if shot_index is None and at is not None:
        for shot in shots:
            if (
                float(shot.get("start_seconds") or 0)
                <= at
                < float(shot.get("end_seconds") or 0)
            ):
                shot_index = int(shot.get("shot_index") or 0) or None
    shot = next((s for s in shots if s.get("shot_index") == shot_index), None)
    people = shot.get("people") if isinstance(shot, Mapping) else None
    if isinstance(people, Mapping) and cast_id in (people.get("named") or []):
        return True, f"the take facts draw them in shot {shot_index}"
    beat = next(
        (b for b in body.get("beats") or [] if isinstance(b, Mapping)
         and any(isinstance(x, Mapping) and x.get("line_id") == line_id for x in b.get("dialogue_lines") or [])),
        None,
    )  # fmt: skip
    frames = [f for f in body.get("frames") or [] if isinstance(f, Mapping)]
    own = next(
        (f for f in frames if beat and f.get("frame_id") == beat.get("frame_id")), None
    )
    if own is None:
        return None, "no board frame for the line"
    group, row = own.get("storyboard_group_id"), own.get("take_shot")
    same = [
        f
        for f in frames
        if f.get("storyboard_group_id") == group and f.get("take_shot") == row
    ] or [own]
    if any(cast_id in (f.get("cast_refs") or []) for f in same):
        return True, "the board frames of that shot draw them"
    card = next(
        (
            c
            for c in body.get("cast") or []
            if isinstance(c, Mapping) and c.get("cast_id") == cast_id
        ),
        {},
    )
    name = _first_name(card)
    if any(_named_in_note(str(f.get("board_correction") or ""), name) for f in same):
        return True, f"the redrawn board's note puts {name} in frame"
    return None, "nothing on the desk says they are drawn"


def italic_overrides(
    spine: Mapping[str, Any] | None,
    facts: Mapping[str, Any] | None,
    lines: Sequence[Any],
) -> tuple[dict[str, bool], list[str]]:
    """Upright lines whose ``off_screen`` flag is stale: the take draws the speaker on that shot.

    Only a line the spine flags ``off_screen`` is looked at; a voice-only cast
    member (heard, never seen) stays italic, and a line with no flag is never
    made italic here.

    Parameters
    ----------
    spine
        The spine the captions come from.
    facts
        The take facts.
    lines
        The take's soundtrack lines (``line_id``, ``cast_id``, ``start``, ``end``),
        or any objects with those fields.

    Returns
    -------
    tuple[dict[str, bool], list[str]]
        ``line_id`` -> ``False`` for each line set upright, and one note per line looked at.
    """

    if not isinstance(spine, Mapping):
        return {}, []
    body = spine.get("spine", spine)
    voice_only = {
        str(c.get("cast_id")) for c in body.get("cast") or []
        if isinstance(c, Mapping) and c.get("voice_only") is True
    }  # fmt: skip
    flagged: dict[str, str] = {}
    for beat in body.get("beats") or []:
        for item in (beat or {}).get("dialogue_lines") or []:
            if isinstance(item, Mapping) and item.get("off_screen") is True:
                flagged[str(item.get("line_id"))] = str(item.get("cast_id") or "")
    overrides: dict[str, bool] = {}
    notes: list[str] = []
    for line in lines:
        line_id = str(getattr(line, "line_id", "") or "")
        cast_id = flagged.get(line_id)
        if cast_id is None:
            continue
        if cast_id in voice_only:
            notes.append(f"{line_id}: italic (a voice-only character)")
            continue
        start, end = getattr(line, "start", None), getattr(line, "end", None)
        at = (
            (float(start) + float(end)) / 2
            if start is not None and end is not None
            else None
        )
        drawn, why = drawn_on_shot(body, facts, line_id=line_id, cast_id=cast_id, at=at)
        if drawn:
            overrides[line_id] = False
            notes.append(f"{line_id}: upright although flagged off screen ({why})")
        else:
            notes.append(f"{line_id}: italic, flagged off screen ({why})")
    return overrides, notes


__all__ = [
    "HOOK_LINE_FILE",
    "SETUP_LINE_FILE",
    "clear_desk_setup_line",
    "desk_setup_line",
    "record_desk_setup_line",
    "FittedTitle",
    "TitleBlock",
    "VoiceEnd",
    "clear_desk_hook_line",
    "desk_hook_line",
    "drawn_on_shot",
    "fit_title",
    "is_letterbox_take",
    "keep_finish_hook_line",
    "italic_overrides",
    "mark_and_title",
    "pad_to_canvas",
    "record_desk_hook_line",
    "resolve_caption_colour",
    "title_ass",
    "title_block",
    "voice_end_spans",
]
