"""Story signs: overlay a sign's exact words where the video garbled them (follow-up to fictora-drama #583).

The video model paints pseudo-letters on signs. The server keeps the words of
the signs a story turns on (Hanakaze ep 1: the shop sign flipped to 営業中)
and records them on the take facts::

    story_signs: [{"shot_index": int, "text": str, "where": str}]

``shot_index`` is the shot's number in the take, the same as
``shots[].shot_index``. When ``finish``'s text check reports possible
lettering on a sign (:mod:`creation.post.take_text`) inside a shot that has
exactly one story sign, the kit can draw that sign's exact words in the house
font, in a plate over the flagged box, for the shot's time range, instead of
blurring it: ``finish --sign-overlay``. Without the flag ``finish`` prints the
suggestion. Facts with no ``story_signs`` (an older server) change nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creation.captions import (
    FONT_NAME,
    _ass_escape,
    _ass_time,
    cjk_font_name,
    has_cjk,
    text_width,
)
from creation.post.take_text import SAMPLE_FPS, Finding

#: The plate is the flagged box; the words fill at most this share of its height and width.
FILL_HEIGHT = 0.8
FILL_WIDTH = 0.9
#: Plate and words: off-white words on a near-black plate (ASS ``&HAABBGGRR``).
TEXT_COLOUR = "&H00F2F2F2"
PLATE_COLOUR = "&H00181818"
#: The plate's fill as an override tag colour (``&HBBGGRR&``).
PLATE_FILL = "&H181818&"


@dataclass(frozen=True)
class StorySign:
    """A sign the story needs, with its exact words, in one shot of the take."""

    shot_index: int
    text: str
    where: str


@dataclass(frozen=True)
class SignOverlay:
    """One sign's exact words drawn over a flagged box for a time range."""

    sign: StorySign
    box: tuple[int, int, int, int]
    start: float
    end: float

    def describe(self) -> str:
        """``shot 2 "営業中" at 1.50-4.00s, box x,y,w,h``."""

        return (
            f'shot {self.sign.shot_index} "{self.sign.text}" ({self.sign.where}) at '
            f"{self.start:.2f}-{self.end:.2f}s, box {','.join(str(v) for v in self.box)}"
        )


def _body(facts: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not facts:
        return {}
    body = facts.get("take_facts", facts)
    return body if isinstance(body, Mapping) else {}


def story_signs(facts: Mapping[str, Any] | None) -> list[StorySign]:
    """The take facts' ``story_signs`` (empty when absent or unreadable)."""

    signs: list[StorySign] = []
    for item in _body(facts).get("story_signs") or []:
        if not isinstance(item, Mapping):
            continue
        index, text = item.get("shot_index"), str(item.get("text") or "").strip()
        if isinstance(index, int) and not isinstance(index, bool) and text:
            signs.append(StorySign(index, text, str(item.get("where") or "").strip()))
    return signs


def shot_windows(facts: Mapping[str, Any] | None) -> dict[int, tuple[float, float]]:
    """``shot_index`` to ``(start, end)`` seconds from the take facts' ``shots``."""

    windows: dict[int, tuple[float, float]] = {}
    for shot in _body(facts).get("shots") or []:
        if not isinstance(shot, Mapping):
            continue
        index, start, end = (
            shot.get("shot_index"),
            shot.get("start_seconds"),
            shot.get("end_seconds"),
        )
        if (
            isinstance(index, int)
            and isinstance(start, (int, float))
            and isinstance(end, (int, float))
            and end > start
        ):
            windows[index] = (float(start), float(end))
    return windows


def plan_overlays(
    facts: Mapping[str, Any] | None,
    lettering: Sequence[Finding],
    size: tuple[int, int],
) -> tuple[list[SignOverlay], list[str]]:
    """Match each possible-lettering finding to the story sign of the shot it is seen in.

    Parameters
    ----------
    facts
        The take facts (``story_signs`` and ``shots``).
    lettering
        The text check's ``sign_lettering`` findings.
    size
        ``(width, height)`` of the take.

    Returns
    -------
    tuple[list[SignOverlay], list[str]]
        The overlays to draw (one per finding in a shot with exactly one story
        sign, over the shot's whole window), and a note per finding that could
        not be matched to one sign. Both empty when the facts carry no story signs.
    """

    signs = story_signs(facts)
    if not signs or not lettering:
        return [], []
    windows = shot_windows(facts)
    overlays: list[SignOverlay] = []
    notes: list[str] = []
    step = 1.0 / SAMPLE_FPS
    for finding in lettering:
        seen_from, seen_to = finding.window(step)
        shots = [
            index
            for index, (start, end) in sorted(windows.items())
            if start < seen_to and seen_from < end
        ]
        in_shot = [sign for sign in signs if sign.shot_index in shots]
        if len(in_shot) != 1:
            if len(in_shot) > 1:
                notes.append(
                    f"lettering at {seen_from:.2f}-{seen_to:.2f}s is in shot(s) with "
                    f"{len(in_shot)} story signs ({', '.join(repr(s.text) for s in in_shot)}): "
                    "not overlaid, check which sign it is by eye"
                )
            continue
        sign = in_shot[0]
        start, end = windows[sign.shot_index]
        overlays.append(SignOverlay(sign, finding.blur_box(size), start, end))
    return overlays, notes


def overlay_ass(overlays: Sequence[SignOverlay], *, width: int, height: int) -> str:
    """The ASS that draws each sign's words in the house font on a plate filling its box."""

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
        f"Style: Sign,{FONT_NAME},32,{TEXT_COLOUR},{TEXT_COLOUR},{PLATE_COLOUR},{PLATE_COLOUR},"
        "-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for item in overlays:
        x, y, w, h = item.box
        size = max(8, round(h * FILL_HEIGHT))
        drawn = text_width(item.sign.text, size)
        if drawn > w * FILL_WIDTH:
            size = max(8, int(size * w * FILL_WIDTH / drawn))
        font = f"\\fn{cjk_font_name(item.sign.text)}" if has_cjk(item.sign.text) else ""
        # The plate is the flagged box itself (a rectangle drawn under the words), so
        # the garbled glyphs are covered edge to edge whatever the words' width.
        plate = (
            f"{{\\an7\\pos({x},{y})\\bord0\\shad0\\1c{PLATE_FILL}\\p1}}"
            f"m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}"
        )
        words = (
            f"{{\\an5\\pos({x + w // 2},{y + h // 2})\\fs{size}\\bord0\\shad0{font}}}"
            f"{_ass_escape(item.sign.text)}"
        )
        for layer, text in ((0, plate), (1, words)):
            events.append(
                f"Dialogue: {layer},{_ass_time(item.start)},{_ass_time(item.end)},Sign,,0,0,0,,{text}\n"
            )
    return header + "".join(events)


def suggestion_lines(
    overlays: Sequence[SignOverlay], *, desk: Path | str, episode: int, take_id: str
) -> list[str]:
    """What ``finish`` prints without ``--sign-overlay``: the overlay it would draw, instead of a blur."""

    if not overlays:
        return []
    return [
        f"!! possible garbled lettering on a story sign: {item.describe()}"
        for item in overlays
    ] + [
        "   The take facts carry the sign's exact words: draw them over it instead of blurring "
        f"(`fictora-produce finish --desk {desk} --episode {episode} --take {take_id} --sign-overlay`)"
    ]


__all__ = [
    "SignOverlay",
    "StorySign",
    "overlay_ass",
    "plan_overlays",
    "shot_windows",
    "story_signs",
    "suggestion_lines",
]
