"""Where the 4:3 picture, the title and the captions sit in a letterbox show's 9:16 file.

The layout matches the "Not Home" reference (user decision 2026-10-06);
fictora-drama letterbox delivery uses the same numbers. Change them only
together with the server's.

The targets are the reference's glyph ink, measured at full resolution on
``Not Home Episode 1.mp4`` (luma > 150, outside the picture): mark ink x 39-96,
y 179-227; title lines Arial Bold 56 (ink rows 333-384 and 396-435, line pitch
63, last baseline y 435); caption Arial Bold 56, ink top y 1417. "56 px" is the
glyphs' em: libass sets an ASS ``Fontsize`` as winAscent + winDescent, so ASS
size 62 draws them (62 x 2048 / 2288 = 55.5). libass places a line's box, not
its ink, so the box positions below are derived from Arial Bold's own metrics
(``ARIAL_BOLD_*``) to land the ink on those rows. fictora-drama's letterbox
render (#614) measures the same ink boxes within 4 px;
``tests/test_letterbox_ink.py`` renders and measures the kit's.

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
#: The Sokii mark image (``assets/sokii-line-72.png``, 72x63, its ink 6 px in) placed at (32, 173), the
#: portrait mark's own spot (3% / 9%): its ink lands at x 38-97, y 179-228, the reference's x 39-96, y 179-227.
MARK_X = 32
MARK_Y = 173
#: The mark image's height; the title's ink never rises above it plus this gap.
MARK_HEIGHT = 63
MARK_TITLE_GAP = 20
#: Title block: ASS size 62 (Not Home's 56 px Arial Bold), stepping down 2 at a time to a 52 floor (then
#: fewer words); each part (setup line white, hook line yellow) on at most two lines; centred; the last
#: line's baseline at y 435 (the block grows upward); lines 63 px apart at 62 (the pitch scales with the
#: size); lines may run 972 px wide (90% of the canvas).
TITLE_SIZE = 62
TITLE_MIN_SIZE = 52
TITLE_SIZE_STEP = 2
TITLE_MAX_LINES = 2
TITLE_LAST_BASELINE = 435
TITLE_PITCH = 63 / 62
TITLE_WIDTH = 972
#: Captions: ASS size 62 (Not Home's 56 px Arial Bold), ink top at y 1417 (52 px under the picture), centred
#: within x 60-950 (clear of the right-hand rail; a short caption centres on x 505). One line preferred: a
#: chunk too wide steps down to 52; one that still needs two lines moves up so its box ends above y 1536
#: (the platform chrome), never above y 1385.
CAPTION_SIZE = 62
CAPTION_MIN_SIZE = 52
CAPTION_INK_TOP = 1417
CAPTION_HIGHEST_TOP = 1385
CAPTION_FLOOR = 1536
CAPTION_LEFT = 60
CAPTION_RIGHT = 950
#: Arial Bold's metrics (font units): libass sets an ASS Fontsize as winAscent + winDescent; a capital's ink
#: top sits (winAscent - capHeight) under the line box's top, the baseline winDescent above its bottom.
ARIAL_BOLD_WIN_ASCENT = 1854
ARIAL_BOLD_WIN_DESCENT = 434
ARIAL_BOLD_CAP_HEIGHT = 1467
#: Title colours (ASS &HBBGGRR): the setup line white, the hook line house yellow (#FFE500).
TITLE_SETUP_COLOUR = "&HFFFFFF&"
TITLE_HOOK_COLOUR = "&H00E5FF&"
#: Caption colours (ASS &HAABBGGRR): house yellow by default, white on request.
CAPTION_COLOURS = {"yellow": "&H0000E5FF", "white": "&H00FFFFFF"}
DEFAULT_CAPTION_COLOUR = "yellow"


# --- caption width: Arial Bold advances, copied from fictora-drama -------------------------------
# Source: fictora-drama src/fictora/drama_generation/delivery_geometry.py (``ARIAL_BOLD_ADVANCES``,
# ``arial_bold_width``), branch feat/letterbox-band-delivery at 4c3a5565 (PR #614). The kit lays a
# letterbox caption out (its one-line fit and its centring) on these, so the kit's and the server's
# files put a caption in the same place whatever font file this laptop has.
#: Arial Bold's advance widths in font units (2048 per em), for the printable
#: ASCII range and the curly quotes, dash and ellipsis. Liberation Sans Bold,
#: which fontconfig serves for "Arial" on the render container, is metric-
#: compatible (same advances), so a letterbox caption's width is known exactly
#: on both, without reading a font file. Any other character counts as a digit.
ARIAL_BOLD_ADVANCES: dict[str, int] = {
    " ": 569,
    "!": 682,
    '"': 971,
    "#": 1139,
    "$": 1139,
    "%": 1821,
    "&": 1479,
    "'": 487,
    "(": 682,
    ")": 682,
    "*": 797,
    "+": 1196,
    ",": 569,
    "-": 682,
    ".": 569,
    "/": 569,
    "0": 1139,
    "1": 1139,
    "2": 1139,
    "3": 1139,
    "4": 1139,
    "5": 1139,
    "6": 1139,
    "7": 1139,
    "8": 1139,
    "9": 1139,
    ":": 682,
    ";": 682,
    "<": 1196,
    "=": 1196,
    ">": 1196,
    "?": 1251,
    "@": 1997,
    "A": 1479,
    "B": 1479,
    "C": 1479,
    "D": 1479,
    "E": 1366,
    "F": 1251,
    "G": 1593,
    "H": 1479,
    "I": 569,
    "J": 1139,
    "K": 1479,
    "L": 1251,
    "M": 1706,
    "N": 1479,
    "O": 1593,
    "P": 1366,
    "Q": 1593,
    "R": 1479,
    "S": 1366,
    "T": 1251,
    "U": 1479,
    "V": 1366,
    "W": 1933,
    "X": 1366,
    "Y": 1366,
    "Z": 1251,
    "[": 682,
    "\\": 569,
    "]": 682,
    "^": 1196,
    "_": 1139,
    "`": 682,
    "a": 1139,
    "b": 1251,
    "c": 1139,
    "d": 1251,
    "e": 1139,
    "f": 682,
    "g": 1251,
    "h": 1251,
    "i": 569,
    "j": 569,
    "k": 1139,
    "l": 569,
    "m": 1821,
    "n": 1251,
    "o": 1251,
    "p": 1251,
    "q": 1251,
    "r": 797,
    "s": 1139,
    "t": 682,
    "u": 1251,
    "v": 1139,
    "w": 1593,
    "x": 1139,
    "y": 1139,
    "z": 1024,
    "{": 797,
    "|": 573,
    "}": 797,
    "~": 1196,
    "‘": 569,
    "’": 569,
    "“": 1024,
    "”": 1024,
    "—": 2048,
    "…": 2048,
}
#: Arial's ascent plus descent in font units: libass sets a ``Fontsize`` over
#: this height, so one em is ``size * 2048 / 2288`` pixels.
_ARIAL_HEIGHT_UNITS = 1854 + 434


def arial_bold_width(text: str, size: int) -> float:
    """Return the advance width of a line of Arial Bold at a libass ``Fontsize``.

    Parameters
    ----------
    text
        One line of text.
    size
        The libass ``Fontsize``.

    Returns
    -------
    float
        Its width in pixels (kerning ignored).
    """

    units = sum(
        ARIAL_BOLD_ADVANCES.get(char, ARIAL_BOLD_ADVANCES["0"]) for char in text
    )
    return units * size / _ARIAL_HEIGHT_UNITS


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

    ``title`` is the room the title block has: its bottom edge is the last
    line's baseline (y 435), its top is under the mark. ``caption`` runs from
    the caption line box's top (its ink top at y 1417) down to the chrome
    floor (y 1536), x 60-950; ``caption_highest_top`` is as high as a two-line
    caption's box may move.
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

    def title_pitch(self, size: int) -> float:
        """Baseline to baseline of two title lines at ``size`` (63 at 62)."""

        return size * TITLE_PITCH

    def line_descent(self, size: int) -> float:
        """How far an Arial Bold line box at ASS ``size`` runs below its baseline."""

        return (
            size
            * ARIAL_BOLD_WIN_DESCENT
            / (ARIAL_BOLD_WIN_ASCENT + ARIAL_BOLD_WIN_DESCENT)
        )


def ink_offset(size: int) -> float:
    """How far a capital's ink top sits under its Arial Bold line box's top at ASS ``size`` (10.5 at 62)."""

    return (
        size
        * (ARIAL_BOLD_WIN_ASCENT - ARIAL_BOLD_CAP_HEIGHT)
        / (ARIAL_BOLD_WIN_ASCENT + ARIAL_BOLD_WIN_DESCENT)
    )


def layout(width: int = CANVAS_WIDTH, height: int = CANVAS_HEIGHT) -> LetterboxLayout:
    """The letterbox layout on a ``width`` x ``height`` canvas (1080x1920 for the deliverable).

    Returns
    -------
    LetterboxLayout
        Picture 1080x810 at y 555-1365; last title baseline y 435; caption box from y 1407 (ink top 1417),
        x 60-950, above y 1536; mark placed at (32, 173), on 1080x1920.
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
        (width - title_w) // 2, title_top, title_w,
        max(0, y(TITLE_LAST_BASELINE) - title_top),
    )  # fmt: skip
    caption_size = y(CAPTION_SIZE)
    caption_top = round(y(CAPTION_INK_TOP) - ink_offset(caption_size))
    caption = Box(
        x(CAPTION_LEFT), caption_top, x(CAPTION_RIGHT) - x(CAPTION_LEFT),
        y(CAPTION_FLOOR) - caption_top,
    )  # fmt: skip
    sizes = tuple(
        y(size) for size in range(TITLE_SIZE, TITLE_MIN_SIZE - 1, -TITLE_SIZE_STEP)
    )
    return LetterboxLayout(
        Box(0, 0, width, height), picture, title, caption, y(CAPTION_HIGHEST_TOP), mark,
        sizes, caption_size, y(CAPTION_MIN_SIZE),
    )  # fmt: skip


def title_sizes(height: int = CANVAS_HEIGHT) -> tuple[int, ...]:
    """The title's ASS sizes on a ``height``-high canvas, largest first (62, 60, 58, 56, 54, 52 on 1920)."""

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
    "ARIAL_BOLD_ADVANCES",
    "arial_bold_width",
    "caption_colour_code",
    "ink_offset",
    "layout",
    "title_sizes",
]
