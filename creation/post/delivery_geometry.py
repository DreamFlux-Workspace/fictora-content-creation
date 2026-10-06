"""Where the 4:3 picture, the title and the captions sit in a letterbox show's 9:16 file.

The numbers are the server's, so the kit's file matches what the app shows.
Source of truth: fictora-drama ``src/fictora/drama_generation/delivery_geometry.py``
(and ``delivery_overlays.py``, ``caption_house_style.py``), origin/main 46f2cc4c
(6 Oct 2026). The kit never imports fictora; the constants are copied here by
hand. Change them only together with the server's.

Founder decisions of 6 Oct 2026 sit on top of the server's layout (they are the
kit's, measured against the "Not Home" reference episode):

* the canvas is pure black ``#000000``;
* the Sokii mark sits in the top black band, under the top 8% UI strip, never
  on the picture;
* the title block is two parts: the setup line in white and the hook line in
  house yellow, each on at most two lines;
* captions sit in the band under the picture, yellow by default (white on
  request), kept clear of the right-hand rail (the right 12% of the lower two
  thirds, the kit's safe zone; the server's dialogue captions use 60 px side
  margins both sides).

Everything is computed for the 1080x1920 deliverable (the house canvas). The
server writes its file at 720x1280 and positions ASS at 1080x1920; the fractions
below make both the same layout.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The deliverable: the content team's 1080x1920 house canvas, pure black.
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
CANVAS_COLOUR = "black"
#: A letterbox take is filmed 4:3.
MASTER_RATIO = (4, 3)

# --- copied from the server's delivery_geometry.py (46f2cc4c) -------------------------------------
#: Instagram's (and TikTok's) chrome covers the bottom fifth of a reel.
CHROME_FLOOR_FRACTION = 0.80
#: TikTok's like / comment / share rail (the server caps its caption-line box here).
RIGHT_RAIL_FRACTION = 0.78
SIDE_MARGIN_FRACTION = 0.06
#: The title's size ladder, as fractions of canvas height (99, 87, 78, 69, 62, 54 px on 1920).
TITLE_SIZE_FRACTIONS: tuple[float, ...] = (
    66 / 1280,
    58 / 1280,
    52 / 1280,
    46 / 1280,
    41 / 1280,
    36 / 1280,
)
TITLE_MAX_LINES = 2
TITLE_SAFE_WIDTH_FRACTION = 0.90
#: Gaps from the picture's edge, as fractions of canvas height (60 px and 51 px on 1920).
TITLE_GAP_ABOVE_PICTURE_FRACTION = 40 / 1280
CAPTION_GAP_BELOW_PICTURE_FRACTION = 34 / 1280
#: Room one caption line needs, as a fraction of canvas height.
CAPTION_MIN_HEIGHT_FRACTION = 80 / 1280

# --- the kit's own (founder decisions, 6 Oct 2026) -----------------------------------------------
#: The top UI strip (tabs, search) the mark sits under, and the right-hand rail the captions keep clear of
#: (``creation.post.hook_overlay`` / ``safe_zones``: the same 8% and 12%).
TOP_STRIP_FRACTION = 0.08
CAPTION_RIGHT_FRACTION = 0.88
#: The Sokii mark in the top band: left edge on the text margin, top just under the UI strip
#: (``creation.post.watermark``'s 9% on a 1920-high frame).
MARK_X = 60
MARK_Y_FRACTION = 0.09
#: The mark image's height (``assets/sokii-line-72.png`` is 72x63) and the gap kept between it and the title.
MARK_HEIGHT = 63
MARK_TITLE_GAP = 20
#: Leading of a title that runs to more than one line, as a multiple of its size (the server's).
TITLE_LINE_HEIGHT = 1.04
#: Title colours (ASS &HBBGGRR): the setup line white, the hook line house yellow (#FFE500).
TITLE_SETUP_COLOUR = "&HFFFFFF&"
TITLE_HOOK_COLOUR = "&H00E5FF&"
#: Caption colours on a letterbox show (ASS &HAABBGGRR): house yellow by default, white on request.
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

    ``title`` is the box the title block grows up from (its bottom edge sits
    the server's gap above the picture; its top is under the mark). ``caption``
    is the band under the picture: captions are bottom-anchored on its bottom
    edge (the platform chrome), like the server's, and centred between the left
    margin and the right-hand rail.
    """

    canvas: Box
    picture: Box
    title: Box
    caption: Box
    mark: tuple[int, int]


def layout(width: int = CANVAS_WIDTH, height: int = CANVAS_HEIGHT) -> LetterboxLayout:
    """The letterbox layout on a ``width`` x ``height`` canvas (the server's ``geometry_for("4:3", "9:16")``).

    Parameters
    ----------
    width, height
        The canvas (1080x1920 for the deliverable).

    Returns
    -------
    LetterboxLayout
        Picture 1080x810 at y 555-1365 on 1080x1920; title bottom 495; caption band 1416-1536,
        x 60-950; mark at (60, 173).
    """

    ratio_w, ratio_h = MASTER_RATIO
    scale = min(width / ratio_w, height / ratio_h)
    picture_w, picture_h = round(ratio_w * scale), round(ratio_h * scale)
    picture = Box(
        (width - picture_w) // 2, (height - picture_h) // 2, picture_w, picture_h
    )
    margin = round(width * MARK_X / CANVAS_WIDTH)
    mark = (margin, round(height * MARK_Y_FRACTION))
    title_bottom = picture.y - round(height * TITLE_GAP_ABOVE_PICTURE_FRACTION)
    title_top = mark[1] + round((MARK_HEIGHT + MARK_TITLE_GAP) * height / CANVAS_HEIGHT)
    title_width = round(width * TITLE_SAFE_WIDTH_FRACTION)
    title = Box(
        (width - title_width) // 2,
        title_top,
        title_width,
        max(0, title_bottom - title_top),
    )
    caption_top = picture.bottom + round(height * CAPTION_GAP_BELOW_PICTURE_FRACTION)
    floor = round(height * CHROME_FLOOR_FRACTION)
    right = round(width * CAPTION_RIGHT_FRACTION)
    caption = Box(margin, caption_top, right - margin, max(0, floor - caption_top))
    return LetterboxLayout(Box(0, 0, width, height), picture, title, caption, mark)


def title_sizes(height: int = CANVAS_HEIGHT) -> tuple[int, ...]:
    """The title's ASS sizes on a ``height``-high canvas, largest first (99 ... 54 on 1920)."""

    return tuple(round(height * fraction) for fraction in TITLE_SIZE_FRACTIONS)


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
