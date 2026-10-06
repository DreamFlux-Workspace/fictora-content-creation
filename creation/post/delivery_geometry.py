"""Where the 4:3 picture, the title and the captions sit in a letterbox show's 9:16 file.

The layout matches the "Not Home" reference (user decision 2026-10-06);
fictora-drama letterbox delivery uses the same numbers. Change them only
together with the server's.

Everything is measured on the 1080x1920 deliverable (the house canvas); a
canvas of another size scales them by height (and the x positions by width).
"""

from __future__ import annotations

from dataclasses import dataclass

# --- the letterbox layout: matches "Not Home" (user decision 2026-10-06) --------------------------
#: The deliverable: 1080x1920, pure black.
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
CANVAS_COLOUR = "black"
#: A letterbox take is filmed 4:3: scaled to 1080x810 and centred, y 555-1365.
MASTER_RATIO = (4, 3)
#: The Sokii mark in the top black band (x 60, y 173: under the top 8% UI strip), never on the picture.
MARK_X = 60
MARK_Y = 173
#: The mark image's height (``assets/sokii-line-72.png`` is 72x63); the title never rises above it plus this gap.
MARK_HEIGHT = 63
MARK_TITLE_GAP = 20
#: Title block: Arial Bold 48, stepping down 2 px at a time to a 40 px floor (then fewer words); each part
#: (setup line white, hook line yellow) on at most two lines; centred; its bottom edge at y 505 (50 px
#: above the picture); lines may run 972 px wide (90% of the canvas).
TITLE_SIZE = 48
TITLE_MIN_SIZE = 40
TITLE_SIZE_STEP = 2
TITLE_MAX_LINES = 2
TITLE_BOTTOM = 505
TITLE_WIDTH = 972
#: Leading of a title that runs to more than one line, as a multiple of its size.
TITLE_LINE_HEIGHT = 1.04
#: Captions: Arial Bold 56, top at y 1435 (70 px under the picture), x 60-950 (clear of the right-hand
#: rail). One line preferred: a chunk too wide steps down to 46 px; one that still needs two lines moves
#: up so its bottom stays above y 1536 (the platform chrome), never above y 1385.
CAPTION_SIZE = 56
CAPTION_MIN_SIZE = 46
CAPTION_TOP = 1435
CAPTION_HIGHEST_TOP = 1385
CAPTION_FLOOR = 1536
CAPTION_LEFT = 60
CAPTION_RIGHT = 950
#: Title colours (ASS &HBBGGRR): the setup line white, the hook line house yellow (#FFE500).
TITLE_SETUP_COLOUR = "&HFFFFFF&"
TITLE_HOOK_COLOUR = "&H00E5FF&"
#: Caption colours (ASS &HAABBGGRR): house yellow by default, white on request.
CAPTION_COLOURS = {"yellow": "&H0000E5FF", "white": "&H00FFFFFF"}
DEFAULT_CAPTION_COLOUR = "yellow"


@dataclass(frozen=True)
class Box:
    """A rectangle in canvas pixels, top-left origin."""

    x: int
    y: int
    width: int
    height: int

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def right(self) -> int:
        return self.x + self.width


@dataclass(frozen=True)
class LetterboxLayout:
    """Every place on the 9:16 letterbox canvas (pixels).

    ``title`` is the box the title block grows up from: its bottom edge is the
    block's bottom, its top is under the mark. ``caption`` runs from the
    caption top (y 1435) down to the chrome floor (y 1536), x 60-950;
    ``caption_highest_top`` is as high as a two-line caption may move.
    """

    canvas: Box
    picture: Box
    title: Box
    caption: Box
    caption_highest_top: int
    mark: tuple[int, int]
    title_sizes: tuple[int, ...]
    caption_size: int
    caption_min_size: int


def layout(width: int = CANVAS_WIDTH, height: int = CANVAS_HEIGHT) -> LetterboxLayout:
    """The letterbox layout on a ``width`` x ``height`` canvas (1080x1920 for the deliverable).

    Returns
    -------
    LetterboxLayout
        Picture 1080x810 at y 555-1365; title bottom y 505; captions from y 1435, x 60-950, above y 1536;
        mark at (60, 173), on 1080x1920.
    """

    def y(value: float) -> int:
        return round(value * height / CANVAS_HEIGHT)

    def x(value: float) -> int:
        return round(value * width / CANVAS_WIDTH)

    ratio_w, ratio_h = MASTER_RATIO
    scale = min(width / ratio_w, height / ratio_h)
    picture_w, picture_h = round(ratio_w * scale), round(ratio_h * scale)
    picture = Box(
        (width - picture_w) // 2, (height - picture_h) // 2, picture_w, picture_h
    )
    mark = (x(MARK_X), y(MARK_Y))
    title_top = mark[1] + y(MARK_HEIGHT + MARK_TITLE_GAP)
    title_w = x(TITLE_WIDTH)
    title = Box(
        (width - title_w) // 2, title_top, title_w, max(0, y(TITLE_BOTTOM) - title_top)
    )
    caption = Box(
        x(CAPTION_LEFT), y(CAPTION_TOP), x(CAPTION_RIGHT) - x(CAPTION_LEFT),
        y(CAPTION_FLOOR) - y(CAPTION_TOP),
    )  # fmt: skip
    sizes = tuple(
        y(size) for size in range(TITLE_SIZE, TITLE_MIN_SIZE - 1, -TITLE_SIZE_STEP)
    )
    return LetterboxLayout(
        Box(0, 0, width, height), picture, title, caption, y(CAPTION_HIGHEST_TOP), mark,
        sizes, y(CAPTION_SIZE), y(CAPTION_MIN_SIZE),
    )  # fmt: skip


def title_sizes(height: int = CANVAS_HEIGHT) -> tuple[int, ...]:
    """The title's sizes on a ``height``-high canvas, largest first (48, 46, 44, 42, 40 on 1920)."""

    return layout(round(height * CANVAS_WIDTH / CANVAS_HEIGHT), height).title_sizes


def caption_colour_code(colour: str | None) -> str:
    """The ASS colour of a letterbox caption: ``yellow`` (default) or ``white``.

    Raises
    ------
    ValueError
        For any other colour.
    """

    name = (colour or DEFAULT_CAPTION_COLOUR).strip().casefold()
    if name not in CAPTION_COLOURS:
        raise ValueError(
            f"caption colour {colour!r}: use {' or '.join(CAPTION_COLOURS)}"
        )
    return CAPTION_COLOURS[name]


__all__ = [
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "CAPTION_COLOURS",
    "DEFAULT_CAPTION_COLOUR",
    "Box",
    "LetterboxLayout",
    "caption_colour_code",
    "layout",
    "title_sizes",
]
