"""Bold captions: one short line at a time, white with one yellow word (user decision, 6 Oct 2026).

``bold`` is the portrait caption style for a new show; ``subtle`` (the house
yellow word flicker, :mod:`creation.captions`) stays an option and is what a
show with finished episodes keeps (:func:`creation.captions.resolve_caption_style`).

The look, on the 1080x1920 canvas and scaled to the take's frame by height
like the house style:

- Arial Bold 92 (the house 64 x 1.44), white ``#FFFFFF``, black outline 7 and
  a shadow of 3, no box.
- One chunk of a line on screen at a time: two or three words, split above
  :data:`BOLD_MAX_CHARS` characters (letters, digits, apostrophes and spaces),
  never across a sentence or clause mark or a breath (:func:`bold_groups`).
  A chunk always fits one line: a chunk too wide is set smaller, never wrapped.
- The chunk's bottom edge sits at 76% of the frame height.
- One word of the chunk in house yellow ``#FFE500`` (:func:`pick_emphasis`):
  the line's writer-marked emphasis (the spine's optional ``emphasis_word``
  on a dialogue line) in the chunk that holds it, else the chunk's main word (the
  last content word, a shouted ALL-CAPS word first; function words never). A
  one-word chunk is yellow only when the word carries content.
- Each word appears when it is said: the chunk is laid out whole from its
  first word (so it never moves as it fills) and each later word is drawn
  from its own start (:data:`BOLD_LEAD_SECONDS` early at most, here 0), the
  words not yet said fully transparent.

A voice heard, not seen (an off-screen line, inner voice) keeps Georgia italic
at Bold's size, place, colours and chunking. A show spoken in Japanese or
Korean (English words cannot be timed against its speech) shows each whole
English line in Bold's face, size and place, wrapped to at most two lines,
with one yellow word. A letterbox show keeps its own caption layout.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import replace
from typing import Any

from creation.caption_dashes import caption_text as no_dash_text
from creation.captions import (
    _BREAK_BEFORE,
    _NEVER_END_ON,
    _PHRASE_END,
    FONT_NAME,
    HOUSE_CANVAS_HEIGHT,
    ITALIC_FONT_NAME,
    LAST_WORD_HOLD_SECONDS,
    OUTLINE_COLOUR,
    PHRASE_BREATH_SECONDS,
    PLAIN_COLOUR,
    PRIMARY_COLOUR,
    Cue,
    _ass_escape,
    _ass_time,
    cjk_font_name,
    has_cjk,
    italic_size,
    side_margin,
    text_width,
    wrap_caption,
)

#: Bold's font size on the 1920-high canvas: the house 64 x 1.44.
BOLD_FONT_SIZE = 92
#: Outline and shadow on that canvas (heavier than the house 5 and 2).
BOLD_OUTLINE = 7
BOLD_SHADOW = 3
#: Bottom edge of the chunk, as a fraction of frame height.
BOLD_BOTTOM_FRACTION = 0.76
#: A chunk holds at most this many words ...
BOLD_MAX_WORDS = 3
#: ... and, when it holds more than one, at most this many characters (letters, digits, apostrophes, spaces).
BOLD_MAX_CHARS = 13
#: A word may appear at most this early (seconds); the builder uses :data:`BOLD_LEAD_SECONDS`.
BOLD_MAX_EARLY_SECONDS = 0.08
BOLD_LEAD_SECONDS = 0.0
#: The text colour (white) and the emphasis word's (house yellow), ``&HAABBGGRR``.
BOLD_COLOUR = PLAIN_COLOUR
EMPHASIS_COLOUR = PRIMARY_COLOUR
#: Bold's shadow is a little darker than the house shadow (heavier edge on a busy frame).
BOLD_SHADOW_COLOUR = "&H60000000"
#: Words not yet said: drawn fully transparent so the chunk keeps its layout.
HIDDEN_TAG = r"{\alpha&HFF&}"

#: Words that never carry the chunk's emphasis: articles, pronouns, auxiliaries, prepositions,
#: conjunctions, fillers. Negations (no, not, never) are kept: they carry the line.
EMPHASIS_SKIP = frozenset(
    """
    a an the this that these those my your his her its our their mine yours
    i me you he him she it we us they them i'm you're he's she's it's we're they're
    i've you've we've they've i'll you'll he'll she'll we'll they'll i'd you'd he'd she'd we'd they'd
    is are was were am be been being do does did done have has had having
    will would can could shall should may might must let's
    of to in on at for with from by as into onto over under about up out off
    and or but nor so if then than because while though
    what which who whom whose where when how why there here that's there's what's
    just oh um uh ah eh hm hmm well yeah yes ok okay hey
    """.split()
)

_DASH_ONLY = re.compile(r"^[\-‒–—―⸺⸻]+$")


def bold_font_size(height: int) -> int:
    """ASS ``Fontsize`` of a Bold caption on a frame ``height`` px high (92 on 1920, 64 on 1344)."""

    return max(8, round(BOLD_FONT_SIZE * height / HOUSE_CANVAS_HEIGHT))


def bold_margin_v(height: int) -> int:
    """Bottom margin that puts the chunk's bottom edge at 76% of the frame height."""

    return round(height * (1.0 - BOLD_BOTTOM_FRACTION))


def word_key(word: str) -> str:
    """Letters, digits and apostrophes only, case-folded: ``Door!`` and ``door`` are one word."""

    text = unicodedata.normalize("NFKC", word).replace("’", "'").casefold()
    return "".join(ch for ch in text if ch.isalnum() or ch == "'").strip("'")


def is_content_word(word: str) -> bool:
    """True when ``word`` can carry a chunk's emphasis (not a function word, not punctuation only)."""

    key = word_key(word)
    return bool(key) and key not in EMPHASIS_SKIP


def _shouted(word: str) -> bool:
    letters = [ch for ch in word if ch.isalpha()]
    return len(letters) > 1 and all(ch.isupper() for ch in letters)


def pick_emphasis(words: Sequence[str]) -> int:
    """The chunk's emphasis word by the heuristic, or -1 for none (all white).

    A shouted (ALL-CAPS, two letters or more) content word first, else the last
    content word: in English the stress of a short phrase falls on its last
    word that carries meaning (``open the DOOR``, ``do not BREAK``). A chunk
    with no content word has none.

    Parameters
    ----------
    words
        The chunk's words as shown.

    Returns
    -------
    int
        Index into ``words``, or -1.
    """

    content = [i for i, w in enumerate(words) if is_content_word(w)]
    if not content:
        return -1
    shouted = [i for i in content if _shouted(words[i])]
    return (shouted or content)[-1]


def marked_index(words: Sequence[str], emphasis: str | int | None) -> int | None:
    """Where a line's writer-marked emphasis falls among its shown ``words``.

    Parameters
    ----------
    words
        The line's words as shown (the line as said, when timed on a transcript).
    emphasis
        The spine's ``emphasis`` on the dialogue line: a word (or a short
        phrase: its first content word counts), or a 0-based word index.

    Returns
    -------
    int | None
        The first matching word's index; ``None`` when unset or not found.
    """

    if emphasis is None or emphasis == "":
        return None
    if isinstance(emphasis, bool):
        return None
    if isinstance(emphasis, int):
        return emphasis if 0 <= emphasis < len(words) else None
    tokens = [word_key(t) for t in str(emphasis).split() if word_key(t)]
    if not tokens:
        return None
    wanted = next((t for t in tokens if t not in EMPHASIS_SKIP), tokens[0])
    keys = [word_key(w) for w in words]
    return keys.index(wanted) if wanted in keys else None


def spine_emphasis(line: dict[str, Any]) -> str | int | None:
    """The writer-marked emphasis on one spine dialogue line, if the spine carries it.

    fictora-drama adds an optional ``emphasis_word`` on dialogue lines (the
    word of ``text`` the writer marked); ``emphasis`` and ``caption_emphasis``
    are read too. Unset returns ``None`` and the heuristic picks.
    """

    for key in ("emphasis_word", "emphasis", "caption_emphasis"):
        value = line.get(key)
        if isinstance(value, dict):
            value = (
                value.get("word")
                if value.get("word") is not None
                else value.get("index")
            )
        if (
            isinstance(value, (str, int))
            and not isinstance(value, bool)
            and value != ""
        ):
            return value.strip() if isinstance(value, str) else value
    return None


def _chars(words: Sequence[str]) -> int:
    return len(" ".join(word_key(w) for w in words))


def _ends_phrase(word: str) -> bool:
    return word.rstrip("\"')]”’»").endswith(_PHRASE_END)


def _split_run(texts: Sequence[str]) -> list[int]:
    """Best chunk sizes for one run of words (no clause mark or breath inside).

    Fewest chunks first; then no word left alone, no chunk ending on an
    article, auxiliary or preposition, a cut before a conjunction or a
    preposition preferred; then the chunks closest in length.
    """

    n = len(texts)
    keys = [word_key(t) for t in texts]
    # best[i] = (cost, sizes) for texts[:i]
    best: list[tuple[float, list[int]] | None] = [None] * (n + 1)
    best[0] = (0.0, [])
    for end in range(1, n + 1):
        for size in range(1, BOLD_MAX_WORDS + 1):
            start = end - size
            if start < 0 or best[start] is None:
                continue
            if size > 1 and _chars(texts[start:end]) > BOLD_MAX_CHARS:
                continue
            cost = 10.0
            if size == 1 and n > 1:
                cost += 6.0
            if end < n and keys[end - 1] in _NEVER_END_ON:
                cost += 8.0
            if end < n and keys[end] in _BREAK_BEFORE:
                cost -= 2.0
            prev_cost, sizes = best[start]  # type: ignore[misc]
            total = prev_cost + cost + 0.05 * abs(_chars(texts[start:end]) - 8)
            if best[end] is None or total < best[end][0]:  # type: ignore[index]
                best[end] = (total, [*sizes, size])
    assert best[n] is not None
    return best[n][1]


def bold_groups(words: Sequence[Cue]) -> list[list[int]]:
    """Group one line's timed words into Bold chunks (word indices per chunk, in order).

    A chunk ends at a sentence or clause mark (a dash too) or a breath (a
    pause of :data:`creation.captions.PHRASE_BREATH_SECONDS`); a longer run is
    cut by :func:`_split_run` into chunks of at most :data:`BOLD_MAX_WORDS`
    words and :data:`BOLD_MAX_CHARS` characters.
    """

    runs: list[list[int]] = []
    current: list[int] = []
    for i, word in enumerate(words):
        current.append(i)
        breath = (
            i + 1 < len(words)
            and words[i + 1].start - word.end >= PHRASE_BREATH_SECONDS
        )
        if _ends_phrase(word.text) or breath:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    groups: list[list[int]] = []
    for run in runs:
        at = 0
        for size in _split_run([words[i].text for i in run]):
            groups.append(run[at : at + size])
            at += size
    return groups


def _shown_words(words: Sequence[str]) -> list[str]:
    """The chunk's words as drawn: no em or en dash (:mod:`creation.caption_dashes`), one entry per word."""

    whole = no_dash_text(" ".join(words)).split()
    if len(whole) == len(words):
        return whole
    return [no_dash_text(w) or w for w in words]


def bold_cues(
    words: Sequence[Cue],
    *,
    hold_until: float | None = None,
    hold: float = LAST_WORD_HOLD_SECONDS,
    marked: Collection[int] = (),
) -> list[Cue]:
    """One line's Bold cues: one cue per word, each showing its chunk up to that word.

    Like :func:`creation.captions.flicker_cues`, each cue lasts until the next
    word starts (so a chunk stays until the next chunk replaces it) and the
    line's last word holds ``hold`` seconds, never past ``hold_until``. Every
    cue carries its whole chunk (``chunk``), how many of its words are said
    (``shown``) and the yellow word (``emphasis``).

    Parameters
    ----------
    words
        One line's words, timed (when each is said).
    hold_until, hold
        As :func:`creation.captions.flicker_cues`.
    marked
        Word indices the writer marked (the line's emphasis); in the chunk
        that holds one, the first is the yellow word, else :func:`pick_emphasis`.

    Returns
    -------
    list[Cue]
        The cues in order.
    """

    words = [w for w in words if not _DASH_ONLY.match(w.text.strip())] or list(words)
    cues: list[Cue] = []
    for group in bold_groups(words):
        shown = _shown_words([words[i].text for i in group])
        chunk = " ".join(shown)
        hit = next((p for p, i in enumerate(group) if i in marked), None)
        emphasis = hit if hit is not None else pick_emphasis(shown)
        for position, i in enumerate(group):
            word = words[i]
            start = max(0.0, word.start - BOLD_LEAD_SECONDS)
            if i + 1 < len(words):
                end = words[i + 1].start - BOLD_LEAD_SECONDS
            else:
                end = word.end + hold
                if hold_until is not None:
                    end = min(end, hold_until)
            cues.append(
                Cue(
                    round(start, 3),
                    round(max(end, start + 0.05), 3),
                    " ".join(shown[: position + 1]),
                    italic=word.italic,
                    chunk=chunk,
                    emphasis=emphasis,
                    shown=position + 1,
                )
            )
    return cues


def bold_whole_line(cue: Cue, marked: Collection[int] = ()) -> Cue:
    """A whole-line cue (a show not spoken in English) in Bold: every word shown, one yellow."""

    shown = _shown_words(cue.text.split())
    hit = next((i for i in sorted(marked) if 0 <= i < len(shown)), None)
    return replace(
        cue,
        text=" ".join(shown),
        chunk=" ".join(shown),
        emphasis=hit if hit is not None else pick_emphasis(shown),
        shown=len(shown),
    )


def bold_from_flicker(cues: Sequence[Cue]) -> list[Cue]:
    """Turn word-flicker cues (or cues read back from any caption file) into Bold cues.

    A reel moves the accepted cut's caption cues through its edits: those cues
    build up word by word (house flicker, or Bold read back with its unsaid
    words dropped). Each word is recovered at the start of the cue that first
    shows it; words that play on without a gap and in the same face are one
    line, chunked again with :func:`bold_cues`. A cue that shows several words
    at once with nothing before it (a whole line) stays whole. A word that was
    yellow in a Bold file read back (``emphasis``) stays the yellow word.

    Parameters
    ----------
    cues
        Caption cues on one timeline.

    Returns
    -------
    list[Cue]
        Bold cues, in order.
    """

    ordered = sorted((c for c in cues if c.text.strip()), key=lambda c: c.start)
    out: list[Cue] = []
    line: list[Cue] = []
    marks: set[int] = set()

    def flush() -> None:
        if line:
            out.extend(bold_cues(line, hold=0.0, marked=set(marks)))
        line.clear()
        marks.clear()

    prev: Cue | None = None
    for cue in ordered:
        if cue.chunk and cue.shown:
            # Already Bold (built here): keep it.
            flush()
            out.append(cue)
            prev = None
            continue
        tokens = cue.text.split()
        follows = (
            prev is not None
            and cue.italic == prev.italic
            and abs(cue.start - prev.end) <= 0.02
        )
        built_on = follows and prev is not None and cue.text.startswith(prev.text + " ")
        new = (
            tokens[len(prev.text.split()) :]
            if built_on and prev is not None
            else tokens
        )
        if not built_on and len(tokens) > 1:
            # Several words at once with nothing before them: a whole line.
            flush()
            out.append(
                bold_whole_line(
                    cue, {cue.emphasis} if 0 <= cue.emphasis < len(tokens) else ()
                )
            )
            prev = None
            continue
        if not follows:
            flush()
        if line:
            # The word before ends where this one starts.
            line[-1] = replace(line[-1], end=cue.start)
        offset = len(tokens) - len(new)
        for k, word in enumerate(new):
            if cue.emphasis == offset + k:
                marks.add(len(line))
            line.append(Cue(cue.start, cue.end, word, italic=cue.italic))
        prev = cue
    flush()
    return out


def _event_text(cue: Cue, size: int, width: int, *, platform: str | None) -> str:
    """One Bold cue's ASS text: the whole chunk laid out, unsaid words transparent, one word yellow."""

    words = (cue.chunk or cue.text).split()
    shown = cue.shown or len(words)
    room = width - 2 * side_margin(width)
    chunk = " ".join(words)
    tags = ""
    if len(words) <= BOLD_MAX_WORDS:
        rows = [words]
        drawn = text_width(chunk, size)
        fit = size if drawn <= room else max(8, int(size * room / drawn))
    else:
        lines, fit = wrap_caption(chunk, size, width)
        rows, at = [], 0
        for row in lines:
            count = len(row.split())
            rows.append(words[at : at + count])
            at += count
    if fit != size:
        tags += f"\\fs{italic_size(fit) if cue.italic else fit}"
    if has_cjk(chunk):
        tags += f"\\fn{cjk_font_name(chunk, platform=platform)}"
    yellow, white = f"{{\\c{EMPHASIS_COLOUR}&}}", f"{{\\c{BOLD_COLOUR}&}}"
    out: list[str] = []
    index = 0
    hidden = False
    for row in rows:
        parts: list[str] = []
        for word in row:
            text = _ass_escape(word)
            if index >= shown and not hidden:
                text = HIDDEN_TAG + text
                hidden = True
            elif index == cue.emphasis and index < shown:
                text = f"{yellow}{text}{white}"
            parts.append(text)
            index += 1
        # A tag holds across ``\N``: once hidden, every later word stays hidden.
        out.append(" ".join(parts))
    prefix = f"{{{tags}}}" if tags else ""
    return prefix + "\\N".join(out)


def build_bold_ass(
    cues: Sequence[Cue], *, width: int, height: int, platform: str | None = None
) -> str:
    """The Bold caption ASS for a frame of ``width`` x ``height`` (see the module notes).

    Cues that are not Bold yet (house flicker cues, cues read back from a
    file) are turned into Bold first (:func:`bold_from_flicker`). Every cue's
    words go through :func:`creation.caption_dashes.caption_text`.
    """

    if any(not (c.chunk and c.shown) for c in cues):
        cues = bold_from_flicker(cues)
    scale = height / HOUSE_CANVAS_HEIGHT
    size = bold_font_size(height)
    margin_x = side_margin(width)
    outline = max(1, round(BOLD_OUTLINE * scale))
    shadow = max(1, round(BOLD_SHADOW * scale))
    tail = f"{OUTLINE_COLOUR},{BOLD_SHADOW_COLOUR}"
    place = f"100,100,0,0,1,{outline},{shadow},2,{margin_x},{margin_x},{bold_margin_v(height)},1"
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
        f"Style: Bold,{FONT_NAME},{size},{BOLD_COLOUR},{BOLD_COLOUR},{tail},-1,0,0,0,{place}\n"
        f"Style: Italic,{ITALIC_FONT_NAME},{italic_size(size)},{BOLD_COLOUR},{BOLD_COLOUR},{tail},0,-1,0,0,{place}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = "".join(
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},{'Italic' if c.italic else 'Bold'},,0,0,0,,"
        + _event_text(c, size, width, platform=platform)
        + "\n"
        for c in cues
    )
    return header + events


__all__ = [
    "BOLD_BOTTOM_FRACTION",
    "BOLD_FONT_SIZE",
    "BOLD_MAX_CHARS",
    "BOLD_MAX_WORDS",
    "EMPHASIS_SKIP",
    "bold_cues",
    "bold_font_size",
    "bold_from_flicker",
    "bold_groups",
    "bold_margin_v",
    "bold_whole_line",
    "build_bold_ass",
    "is_content_word",
    "marked_index",
    "pick_emphasis",
    "spine_emphasis",
    "word_key",
]
