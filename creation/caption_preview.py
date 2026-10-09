"""The show's caption style, asked once at the look approval with a free preview still (6 Oct 2026).

A new show is captioned ``bold`` (:mod:`creation.caption_bold`); ``subtle``
(the house yellow word flicker) stays an option. The first time a new show's
look is approved (``approve --gate look``; on a desk that drew no look frame,
the first plates yes), the kit draws one still on this laptop, free: the
approved look frame (else the first plate, else the first board) with the
show's first dialogue line in Bold on the left and in Subtle on the right,
and says "Captions default to Bold; reply 'subtle' to switch." It saves
``caption_style: bold`` in the desk's ``production.config.json`` there and
then, so silence is Bold; nothing waits on the answer.

A continuing show (a desk with finished episodes) is not asked: it keeps
``subtle``, the captions its episodes went out with, saved the same way,
unless the operator switches (``caption-style --desk D --set bold``). A desk
whose ``caption_style`` is already set (by ``start --caption-style``, an
older desk's ``house``, or an earlier switch) is never asked again. This
mirrors the per-show voice mode (:mod:`creation.voice_mode`): one choice per
show, an earlier show keeps what it had.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TextIO

from creation.captions import (
    CAPTION_STYLES,
    CONTINUING_SHOW_CAPTION_STYLE,
    FONTS_DIR,
    NEW_SHOW_CAPTION_STYLE,
    ass_for_this_laptop,
    Span,
    build_ass,
    build_line_cues,
    canonical_caption_style,
    caption_style_word,
    desk_has_finished_episodes,
    episode_caption_lines,
    find_ffmpeg,
    show_caption_style,
)
from creation.ops.folder import next_versioned_path

#: Where the preview still is written on the desk (a new ``-vN`` file each time).
PREVIEW_DIR = ("shared", "look")
PREVIEW_STEM = "caption-preview"
#: The line drawn when the script has no dialogue yet.
FALLBACK_LINE = "This is where it starts."
SWITCH_PROMPT = "Captions default to Bold; reply 'subtle' to switch."
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def preview_image(desk: Path) -> Path | None:
    """The picture the preview is drawn on: the approved look frame, else the first plate, else a board.

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    Path | None
        An image file on the desk, or ``None`` when it has none yet.
    """

    from creation.look_gate import newest_look_frame
    from creation.ops.state import load_series

    desk = desk.expanduser().resolve()
    try:
        look = load_series(desk).look
        approved = desk / look.path if look.status == "approved" and look.path else None
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        approved = None
    if approved is not None and approved.is_file():
        return approved
    frame = newest_look_frame(desk)
    if frame is not None:
        return frame
    for folder in (
        desk / "shared" / "plates",
        desk / "ep01" / "plates",
        desk / "ep01" / "boards",
    ):
        if folder.is_dir():
            found = sorted(
                p for p in folder.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES
            )
            if found:
                return found[0]
    return None


def sample_line(desk: Path) -> str:
    """The show's first dialogue line (episode 1, from the desk's saved spine), else :data:`FALLBACK_LINE`."""

    from creation.post.desk import saved_spine

    desk = desk.expanduser().resolve()
    found = saved_spine(desk, 1)
    if found is not None:
        lines = [line for line in episode_caption_lines(found[0], 1) if line.english]
        if lines:
            return lines[0].text
    return FALLBACK_LINE


def _first_chunk_cue(text: str, style: str):  # noqa: ANN202 - a Cue
    """The cue a viewer sees once the line's first chunk is fully said, in ``style``."""

    chunking = "bold" if style == "bold" else "three"
    cues = build_line_cues([text], [Span(0.0, 4.0)], chunking=chunking)[0]
    if style == "bold":
        first = cues[0].chunk
        cue = [c for c in cues if c.chunk == first][-1]
    else:
        # The house flicker builds up to three words, then resets: its third word's cue.
        cue = cues[min(2, len(cues) - 1)]
    from dataclasses import replace

    return replace(cue, start=0.0, end=60.0)


def _labelled(ass: str, label: str, width: int, height: int) -> str:
    """``ass`` with a small style label at the top of the frame."""

    size = max(8, round(40 * height / 1920))
    style = (
        f"Style: Label,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,"
        f"100,100,0,0,3,0,0,8,10,10,{round(height * 0.04)},1\n"
    )
    ass = ass.replace("\n[Events]\n", f"{style}\n[Events]\n", 1)
    return ass + f"Dialogue: 1,0:00:00.00,0:01:00.00,Label,,0,0,0,,{label}\n"


def render_caption_preview(
    image: Path, line: str, out: Path, *, ffmpeg: str | None = None
) -> Path:
    """Draw ``line`` on ``image`` in Bold (left) and Subtle (right), side by side, into ``out`` (PNG).

    The captions are the ones ``finish`` burns (:func:`creation.captions.build_ass`
    through libass), each at its full first chunk; free, nothing is sent.

    Parameters
    ----------
    image
        The look frame (or plate, or board).
    line
        The sample line.
    out
        The PNG to write (must not exist: the caller picks a new name).
    ffmpeg
        ffmpeg with libass (default :func:`creation.captions.find_ffmpeg`).

    Returns
    -------
    Path
        ``out``.

    Raises
    ------
    RuntimeError
        When ffmpeg cannot draw it.
    FileExistsError
        When ``out`` exists.
    """

    from PIL import Image

    if out.exists():
        raise FileExistsError(f"{out} exists: the preview never overwrites a file")
    ffmpeg_bin = ffmpeg or find_ffmpeg()[0]
    with Image.open(image) as picture:
        width, height = picture.size
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = out.with_name(out.stem + ".tmp")
    scratch.mkdir(exist_ok=True)
    try:
        files = []
        for style, label in (("bold", "Bold (default)"), ("house", "Subtle")):
            ass = build_ass(
                [_first_chunk_cue(line, style)], width=width, height=height, style=style
            )
            path = scratch / f"{style}.ass"
            path.write_text(
                ass_for_this_laptop(_labelled(ass, label, width, height)),
                encoding="utf-8",
            )
            files.append(path)

        def esc(p: Path) -> str:
            return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

        graph = (
            "[0:v]split[a][b];"
            f"[a]ass='{esc(files[0])}':fontsdir='{esc(FONTS_DIR)}'[l];"
            f"[b]ass='{esc(files[1])}':fontsdir='{esc(FONTS_DIR)}'[r];"
            "[l][r]hstack=inputs=2"
        )
        result = subprocess.run(
            [ffmpeg_bin, "-v", "error", "-y", "-i", str(image), "-filter_complex", graph,
             "-frames:v", "1", str(out)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
        )  # fmt: skip
        if result.returncode != 0:
            raise RuntimeError(
                f"caption preview failed: {result.stderr.strip()[-400:]}"
            )
    finally:
        for path in scratch.glob("*"):
            path.unlink()
        scratch.rmdir()
    return out


def caption_style_at_look(desk: Path, *, out: TextIO | None = None) -> Path | None:
    """Ask the show's caption style once, at its look approval (see the module notes). Never blocks.

    Parameters
    ----------
    desk
        Series desk.
    out
        Text stream.

    Returns
    -------
    Path | None
        The preview still when one was drawn (a new show asked for the first
        time), else ``None``.
    """

    from creation.production_config import (
        load_production_config,
        save_production_config,
    )

    from creation.rules_epoch import is_legacy

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    if is_legacy(desk):
        # A desk created before 6 Oct 2026 is never asked: it keeps its captions (house) and its
        # production.config.json untouched (creation.rules_epoch; frozen, do not change).
        return None
    config = load_production_config(desk)
    if config.caption_style:
        return None
    _, source = show_caption_style(desk)
    if source == "continuing" or desk_has_finished_episodes(desk):
        config.caption_style = CONTINUING_SHOW_CAPTION_STYLE
        save_production_config(desk, config)
        print(
            "Captions: this show keeps Subtle (the yellow word flicker its finished episodes went out "
            f"with). `caption-style --desk {desk} --set bold` switches it.",
            file=out,
        )
        return None
    config.caption_style = NEW_SHOW_CAPTION_STYLE
    save_production_config(desk, config)
    preview: Path | None = None
    image = preview_image(desk)
    if image is None:
        print(
            "Captions: no look frame, plate or board on the desk to preview on.",
            file=out,
        )
    else:
        target = next_versioned_path(desk.joinpath(*PREVIEW_DIR), PREVIEW_STEM, ".png")
        try:
            preview = render_caption_preview(image, sample_line(desk), target)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
            print(
                f"Captions: preview not drawn ({type(exc).__name__}: {exc})", file=out
            )
    if preview is not None:
        print(
            f"Captions preview (free): {preview} (left Bold, right Subtle, on "
            f"`{image.name}`).",
            file=out,
        )
    print(
        f"{SWITCH_PROMPT} (saved bold; on 'subtle': `caption-style --desk {desk} --set subtle`)",
        file=out,
    )
    return preview


def run_caption_style(
    desk: Path, *, set_to: str | None = None, out: TextIO | None = None
) -> str:
    """``caption-style --desk D [--set bold|subtle|plain|none]``: read or change the show's captions (free).

    Parameters
    ----------
    desk
        Series desk.
    set_to
        A style to save for the show; ``None`` reads it.
    out
        Text stream.

    Returns
    -------
    str
        The show's style after the call, as the kit names it.
    """

    from creation.production_config import (
        load_production_config,
        save_production_config,
    )

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    if set_to is not None:
        if set_to not in CAPTION_STYLES:
            raise ValueError(
                f"--set {set_to!r}: choose one of {', '.join(CAPTION_STYLES)}"
            )
        config = load_production_config(desk)
        word = caption_style_word(set_to)
        config.caption_style = word
        save_production_config(desk, config)
        print(
            f"Captions set to {word} for this show (finish, caption and reel read it; episodes "
            "already finished keep what they were burned with until finished again).",
            file=out,
        )
        return word
    style, source = show_caption_style(desk)
    why = {
        "show": "set for this show",
        "continuing": "not chosen; this show has finished episodes, so it keeps subtle",
        "new": "not chosen yet; a new show is captioned bold (asked at the look approval)",
        "legacy": "not chosen; a desk created before 6 Oct 2026 keeps subtle, as it always had",
    }[source]
    word = (
        caption_style_word(canonical_caption_style(style))
        if style in CAPTION_STYLES
        else style
    )
    print(f"Captions: {word} ({why}).", file=out)
    return word


__all__ = [
    "FALLBACK_LINE",
    "SWITCH_PROMPT",
    "caption_style_at_look",
    "preview_image",
    "render_caption_preview",
    "run_caption_style",
    "sample_line",
]
