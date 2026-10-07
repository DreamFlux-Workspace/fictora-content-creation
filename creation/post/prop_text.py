"""Prop text: write a prop's real words on it in ``finish``, never the model's (pairs with fictora-drama prop_text).

The video model invents letters in every script. Lost and Found canary (7 Oct
2026, ``take-ep01-t1-raw-v1``, shot 3, 7-11 s): "a note in her late mother's
handwriting" was filmed with made-up English ("Thaen sl4t") over nonsense
Chinese characters, and any viewer who reads the language sees it is fake.
The server now pictures every prop that carries writing (a note, a letter, a
ticket, a phone screen) so its words can't be read, and records it on the
take facts::

    prop_text: [{"shot_index": int, "prop": str, "text": str}]

``text`` is the words the story intends ("I kept it dry for you. — Mom"),
empty when the story gives none. When the words matter, ``finish --prop-text
"TEXT"[@SHOT] --prop-box x,y,w,h`` writes them in a handwriting face, in the
script they are written in (Latin: Caveat; Hangul: Nanum Pen Script; kana and
kanji: Yomogi; all SIL OFL, shipped in ``assets/fonts``), on a paper plate over
the prop's box, for that shot's window only. ``--prop-text @3`` takes the
story's words for shot 3 from the take facts.

It never invents words: with no text given and none in the take facts nothing
is written (blur the prop instead: ``blur --box``). The box is the operator's
(the same ``x,y,w,h`` convention as ``blur --box``), or the take facts' own
``box`` when a server sends one.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creation.captions import FONT_NAME, FONTS_DIR, _ass_escape, _ass_time, text_width
from creation.post.edit import BlurBox
from creation.post.story_signs import shot_windows

#: The handwriting faces, by script: (family libass matches, file in FONTS_DIR).
LATIN_HAND = ("Caveat", "Caveat-Variable.ttf")
HANGUL_HAND = ("Nanum Pen", "NanumPenScript-Regular.ttf")
JAPANESE_HAND = ("Yomogi", "Yomogi-Regular.ttf")
#: The words fill at most this share of the box's width and height.
FILL_WIDTH = 0.88
FILL_HEIGHT = 0.8
#: Line height as a share of the font size.
LINE_HEIGHT = 1.15
MAX_LINES = 4
#: Dark ink on an off-white paper plate (ASS ``&HAABBGGRR`` / ``&HBBGGRR&``).
INK_COLOUR = "&H00352A22"
PAPER_FILL = "&HE4ECEF&"
#: The plate's soft edge, in pixels: a sticker with hard edges reads as a patch.
PLATE_BLUR = 3

_HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏가-힯]")
_KANA = re.compile("[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HAN = re.compile("[㐀-䶿一-鿿豈-﫿]")
_CJK_CHAR = re.compile("[ᄀ-ᇿ　-ヿ㄰-㆏㐀-䶿一-鿿가-힯豈-﫿＀-￯]")
_SHOT_SUFFIX = re.compile(r"^(?P<text>.*?)(?:@(?P<shot>\d+))?$", re.DOTALL)


@dataclass(frozen=True)
class FactsProp:
    """A prop with writing on it that one shot pictures, from the take facts."""

    shot_index: int
    prop: str
    text: str
    box: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class PropTextRequest:
    """One ``--prop-text TEXT[@SHOT]``: the words (empty: the story's) and the shot."""

    text: str
    shot: int | None


@dataclass(frozen=True)
class PropOverlay:
    """One prop's real words, drawn on its box for one shot's window."""

    text: str
    shot_index: int
    prop: str
    box: tuple[int, int, int, int]
    start: float
    end: float
    family: str
    font_note: str = ""

    def describe(self) -> str:
        """``shot 3 "I kept it dry for you." on the note at 7.00-11.00s, box x,y,w,h, Caveat``."""

        on = f" on {self.prop}" if self.prop else ""
        return (
            f'shot {self.shot_index} "{self.text}"{on} at {self.start:.2f}-{self.end:.2f}s, '
            f"box {','.join(str(v) for v in self.box)}, {self.family}"
            + (f" ({self.font_note})" if self.font_note else "")
        )


def parse_prop_text(raw: str) -> PropTextRequest:
    """Read ``--prop-text TEXT[@SHOT]``.

    Parameters
    ----------
    raw
        ``"I kept it dry for you.@3"``, ``"엄마가@2"``, ``"@3"`` (the story's
        words for shot 3) or ``"TEXT"`` (the shot that pictures a prop).

    Returns
    -------
    PropTextRequest
        The words, single-spaced (empty: take them from the take facts), and
        the shot (``None``: the take facts decide).

    Raises
    ------
    ValueError
        When both the words and the shot are missing.
    """

    match = _SHOT_SUFFIX.match(raw.strip())
    assert match is not None  # the pattern matches any string
    text = " ".join(match.group("text").split())
    shot = int(match.group("shot")) if match.group("shot") else None
    if not text and shot is None:
        raise ValueError(
            '--prop-text: give the words ("I kept it dry for you.@3") or a shot whose take facts '
            'carry the story\'s words ("@3"); the kit never makes words up'
        )
    if shot is not None and shot < 1:
        raise ValueError(f"--prop-text {raw!r}: the shot is counted from 1")
    return PropTextRequest(text, shot)


def _body(facts: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not facts:
        return {}
    body = facts.get("take_facts", facts)
    return body if isinstance(body, Mapping) else {}


def _box(raw: Any) -> tuple[int, int, int, int] | None:
    if isinstance(raw, Sequence) and not isinstance(raw, str) and len(raw) == 4:
        values = [v for v in raw if isinstance(v, int) and not isinstance(v, bool)]
        if len(values) == 4 and values[2] > 0 and values[3] > 0 and min(values) >= 0:
            return (values[0], values[1], values[2], values[3])
    return None


def facts_props(facts: Mapping[str, Any] | None) -> list[FactsProp]:
    """The take facts' ``prop_text`` (empty when absent: an older server, or a legacy story)."""

    props: list[FactsProp] = []
    for item in _body(facts).get("prop_text") or []:
        if not isinstance(item, Mapping):
            continue
        index, prop = item.get("shot_index"), str(item.get("prop") or "").strip()
        if (
            isinstance(index, int)
            and not isinstance(index, bool)
            and index >= 1
            and prop
        ):
            props.append(
                FactsProp(
                    index,
                    prop,
                    " ".join(str(item.get("text") or "").split()),
                    _box(item.get("box")),
                )
            )
    return props


def handwriting_face(text: str, *, fonts_dir: Path = FONTS_DIR) -> tuple[str, str]:
    """The handwriting face the words are written in, by their script, and a note when it is not one.

    Parameters
    ----------
    text
        The words.
    fonts_dir
        Where the kit's fonts are (tests pass another).

    Returns
    -------
    tuple[str, str]
        The family name libass matches, and a note: empty for a handwriting
        face; why the house font is used when the face's file is missing; that
        Chinese-only characters the Japanese face lacks fall back to the
        system CJK face. Letters a face lacks always fall back to a system
        face that has them (never an empty box).
    """

    note = ""
    if _HANGUL.search(text):
        family, filename = HANGUL_HAND
    elif _KANA.search(text) or _HAN.search(text):
        family, filename = JAPANESE_HAND
        if not _KANA.search(text):
            note = "no handwriting face for Chinese is shipped: characters Yomogi lacks use the system CJK face"
    else:
        family, filename = LATIN_HAND
    if not (fonts_dir / filename).is_file():
        return (
            FONT_NAME,
            f"handwriting face {filename} not found in {fonts_dir}: house font {FONT_NAME} used",
        )
    return family, note


def _is_cjk(text: str) -> bool:
    return bool(_CJK_CHAR.search(text))


def _width(text: str, size: int) -> float:
    return text_width(text, size)


def _split(text: str, lines: int) -> list[str]:
    """``text`` in ``lines`` lines of about equal width (by words; by characters for CJK)."""

    if lines <= 1:
        return [text]
    units = (
        list(text.replace(" ", ""))
        if _is_cjk(text) and " " not in text.strip()
        else text.split(" ")
    )
    joiner = "" if _is_cjk(text) and " " not in text.strip() else " "
    if len(units) < lines:
        return [text]
    total = _width(text, 100)
    out: list[str] = []
    current: list[str] = []
    for unit in units:
        current.append(unit)
        if len(out) < lines - 1 and _width(joiner.join(current), 100) >= total / lines:
            out.append(joiner.join(current))
            current = []
    if current:
        out.append(joiner.join(current))
    return [line for line in out if line]


def fit_words(text: str, width: int, height: int) -> tuple[list[str], int]:
    """Lay the words out in the box: the line count that gives the largest size, and that size.

    Parameters
    ----------
    text
        The words.
    width, height
        The box, in pixels.

    Returns
    -------
    tuple[list[str], int]
        The lines and the font size (at least 8 px).
    """

    best: tuple[list[str], int] = ([text], 8)
    for count in range(1, MAX_LINES + 1):
        lines = _split(text, count)
        widest = max(_width(line, 100) for line in lines) / 100 or 1.0
        size = int(
            min(
                height * FILL_HEIGHT / (len(lines) * LINE_HEIGHT),
                width * FILL_WIDTH / widest,
            )
        )
        if size > best[1]:
            best = (lines, size)
        if len(lines) < count:
            break
    return best


def plan_prop_overlays(
    facts: Mapping[str, Any] | None,
    requests: Sequence[PropTextRequest],
    boxes: Sequence[BlurBox],
    *,
    fonts_dir: Path = FONTS_DIR,
) -> tuple[list[PropOverlay], list[str]]:
    """Match each ``--prop-text`` to its shot, its words and its box.

    Parameters
    ----------
    facts
        The take facts (``prop_text`` and ``shots``).
    requests
        The ``--prop-text`` values, in order.
    boxes
        The ``--prop-box`` values, one per request in the same order; a
        request without one uses the take facts' ``box`` for its prop.
    fonts_dir
        Where the kit's fonts are.

    Returns
    -------
    tuple[list[PropOverlay], list[str]]
        The overlays to draw, and one note per request that cannot be drawn
        (no words, no shot, no box, no shot window): nothing is drawn for it.
    """

    props = facts_props(facts)
    windows = shot_windows(facts)
    overlays: list[PropOverlay] = []
    notes: list[str] = []
    for position, request in enumerate(requests):
        label = f"--prop-text {request.text or ''}{'@' + str(request.shot) if request.shot else ''}".strip()
        shot = request.shot
        if shot is None:
            wanted = _key(request.text)
            matching = sorted(
                {p.shot_index for p in props if wanted and _key(p.text) == wanted}
            )
            shots = matching or sorted({p.shot_index for p in props})
            if len(shots) != 1:
                notes.append(
                    f"{label}: which shot? "
                    + (
                        f"the take facts picture writing props in shots {', '.join(map(str, shots))}"
                        if shots
                        else "the take facts carry no prop_text"
                    )
                    + ": add @SHOT. Nothing drawn"
                )
                continue
            shot = shots[0]
        in_shot = [p for p in props if p.shot_index == shot]
        text = request.text
        if not text:
            worded = list(dict.fromkeys(p.text for p in in_shot if p.text))
            if len(worded) != 1:
                notes.append(
                    f"{label}: "
                    + (
                        f"shot {shot} has {len(worded)} props with words ({', '.join(repr(w) for w in worded)}): "
                        "give the words"
                        if worded
                        else f"the story gives no words for shot {shot}'s prop: nothing is written "
                        "(the kit never makes words up). Blur it instead: blur --box x,y,w,h"
                    )
                )
                continue
            text = worded[0]
        prop = next(
            (p for p in in_shot if p.text == text),
            in_shot[0] if len(in_shot) == 1 else None,
        )
        box: tuple[int, int, int, int] | None
        if position < len(boxes):
            given = boxes[position]
            box = (given.x, given.y, given.w, given.h)
        else:
            box = prop.box if prop is not None else None
        if box is None:
            notes.append(
                f"{label}: no box for shot {shot}'s prop: give --prop-box x,y,w,h in pixels of the take "
                "(as for blur --box), one per --prop-text. Nothing drawn"
            )
            continue
        if shot not in windows:
            notes.append(
                f"{label}: the take facts have no window for shot {shot}. Nothing drawn"
            )
            continue
        family, font_note = handwriting_face(text, fonts_dir=fonts_dir)
        start, end = windows[shot]
        overlays.append(
            PropOverlay(
                text,
                shot,
                prop.prop if prop is not None else "",
                box,
                start,
                end,
                family,
                font_note,
            )
        )
    return overlays, notes


def _key(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum()
    )


def prop_text_ass(overlays: Sequence[PropOverlay], *, width: int, height: int) -> str:
    """The ASS that writes each prop's words in its handwriting face on a soft paper plate over its box.

    Parameters
    ----------
    overlays
        :func:`plan_prop_overlays`.
    width, height
        The take's size.

    Returns
    -------
    str
        A complete ASS script; each overlay is drawn only inside its shot's window.
    """

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
        f"Style: Prop,{LATIN_HAND[0]},32,{INK_COLOUR},{INK_COLOUR},{INK_COLOUR},{INK_COLOUR},"
        "0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events: list[str] = []
    for item in overlays:
        x, y, w, h = item.box
        lines, size = fit_words(item.text, w, h)
        plate = (
            f"{{\\an7\\pos({x},{y})\\bord0\\shad0\\blur{PLATE_BLUR}\\1c{PAPER_FILL}\\p1}}"
            f"m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}"
        )
        words = (
            f"{{\\an5\\pos({x + w // 2},{y + h // 2})\\fn{item.family}\\fs{size}\\bord0.5\\shad0\\blur0.6}}"
            + "\\N".join(_ass_escape(line) for line in lines)
        )
        for layer, text in ((0, plate), (1, words)):
            events.append(
                f"Dialogue: {layer},{_ass_time(item.start)},{_ass_time(item.end)},Prop,,0,0,0,,{text}\n"
            )
    return header + "".join(events)


def suggestion_lines(
    facts: Mapping[str, Any] | None, *, desk: Path | str, episode: int, take_id: str
) -> list[str]:
    """What ``finish`` prints when the take facts carry writing props and no ``--prop-text`` was given."""

    props = facts_props(facts)
    if not props:
        return []
    lines: list[str] = []
    for prop in props:
        words = (
            f'the story\'s words: "{prop.text}"'
            if prop.text
            else "the story gives no words"
        )
        lines.append(
            f"prop with writing: shot {prop.shot_index}: {prop.prop} ({words})"
        )
    worded = sorted({p.shot_index for p in props if p.text})
    lines.append(
        "   the take was asked to show it unreadable; if the video drew letters on it, "
        + (
            f"write the real words (`fictora-produce finish --desk {desk} --episode {episode} --take {take_id} "
            f'--prop-text "@{worded[0]}" --prop-box x,y,w,h`) or '
            if worded
            else ""
        )
        + "blur it (`blur --box x,y,w,h`)"
    )
    return lines


__all__ = [
    "HANGUL_HAND",
    "JAPANESE_HAND",
    "LATIN_HAND",
    "FactsProp",
    "PropOverlay",
    "PropTextRequest",
    "facts_props",
    "fit_words",
    "handwriting_face",
    "parse_prop_text",
    "plan_prop_overlays",
    "prop_text_ass",
    "suggestion_lines",
]
