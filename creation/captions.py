"""Local house captions: time spine lines on a raw take and burn them with ffmpeg.

The raw take from the Drama API has no burn-in (``api_captions: false``). This
module finishes it on the operator's laptop:

1. Read episode dialogue from the desk spine snapshot (``ep01/api/*spine*.json``):
   for one take of a two- or four-take episode, only the lines of the beats that
   take plays (split as the board splits them, :func:`take_caption_lines`).
2. Find speech spans with ffmpeg ``silencedetect`` (no transcription model needed:
   the words are already known from the script gate).
3. Anchor each line on its speech span and spread words by length.
4. Write a house flicker ASS (up to three words build up, then reset) and burn it.

Captions are always the English line. On a show spoken in Japanese or Korean
(spine ``spoken_language`` not English) the English words cannot be timed
against the speech, so step 3-4 show each whole line over its speech span
instead of flickering word by word.

On such a show, when a Whisper transcript of the take is given (``words_json``:
the words the server heard, ``/v1/transcripts``), each whole line is timed on
the words matched to it (:func:`creation.post.whisper.line_windows`) instead of
on speech spans: a stammer or a mid-line pause no longer moves the caption. A
line the transcript does not match falls back to its speech span. Each whole
line then stays up long enough to read (:func:`readable_seconds`), extended
only forward, never into the next line. ``--line-start`` / ``--line-end``
override either end by hand.

A voice that is heard, not seen (a line marked ``off_screen``, or any line of a
cast member the server flags ``voice_only``) is captioned in Georgia italic:
same size, colour, edge and place as the house caption, only the face changes
(the retired internal kit's convention for a remembered or off-screen voice).

Captions are English only: the caption font has no Japanese, Chinese or Korean
glyphs. A line whose caption text (``subtitle_text``, else ``text``) is not
English is left uncaptioned and reported as ``NOT ENGLISH`` so the operator can
give it an English subtitle. It still takes its place in the timing, so the
lines after it stay on their own speech.

The look is the house style the content team delivers (runbook rule 1):
Arial Bold 64 on a 1080x1920 canvas, yellow ``#FFE500``, black outline 5,
soft shadow, no box, one face for every line. It is scaled to the take's real
frame by height (64 on 1920 is 45 on the 1344-high H3 take), so an operator
never re-burns at a size meant for another canvas. Placement follows the
TikTok / Reels / Shorts safe zones: the text's bottom edge sits at 62% of the
frame height and the caption block stays inside 55-70%. A caption too wide for
one line is wrapped into at most two balanced lines (:func:`wrap_caption`);
only a caption that still does not fit on two lines is set smaller.

A line with Japanese, Chinese or Korean characters is set in a CJK face
(:func:`cjk_font_name`): Arial has no such glyphs. Captions stay English (the
``NOT ENGLISH`` rule above), so this only matters for a hand-made cue.

Three caption styles (``--caption-style`` on ``finish`` and ``caption``, or
``caption_style`` in the desk's ``production.config.json``): ``house`` (the
above), ``plain`` (white whole-line captions, same face, size and safe band)
and ``none`` (no captions burned).

A letterbox show filmed 4:3 (``layout="letterbox"`` on :func:`caption_take`,
6 Oct 2026) is captioned on its 9:16 canvas in the band under the picture
(:mod:`creation.post.delivery_geometry`): Arial Bold (ASS 62), ink from y 1417, centred
on the frame unless that crosses the right-hand rail, one line preferred
(:func:`letterbox_caption_place`), yellow by default or white. Its words build up in phrases of up to five
(:func:`phrase_cues`), never leaving one word alone, and a line's italics
follow ``italic_overrides`` (a speaker the take draws is upright). A portrait
show is captioned exactly as before.
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from creation.caption_dashes import drawn_text as no_dash_text
from creation.rules_epoch import under_desk_rules
from creation.ops.folder import next_versioned_path

#: The house style's canvas height: the content team's 1080x1920 deliverable.
HOUSE_CANVAS_HEIGHT = 1920
#: Font size, outline and shadow on that canvas (runbook rule 1: Arial Bold 64, outline 5).
HOUSE_FONT_SIZE = 64
HOUSE_OUTLINE = 5
HOUSE_SHADOW = 2
#: Left + right ASS margins on the 1080-wide canvas (scaled by width).
HOUSE_CANVAS_WIDTH = 1080
HOUSE_SIDE_MARGIN = 60
#: Bottom edge of the caption text, as a fraction of frame height (social safe zones).
CAPTION_BOTTOM_FRACTION = 0.62
#: The caption block must stay inside this band of the frame height.
CAPTION_BAND = (0.55, 0.70)
#: A caption wraps onto at most this many lines (balanced), never more.
MAX_CAPTION_LINES = 2
FONT_NAME = "Arial"
#: Face for a voice heard, not seen (off-screen line or voice-only cast), set in italic.
ITALIC_FONT_NAME = "Georgia"
#: libass sets an ASS ``Fontsize`` as the face's OS/2 winAscent + winDescent, not its em.
#: Em per Fontsize unit: Arial Bold 2048 / (1854 + 434); Georgia Italic 2048 / (1878 + 449).
HOUSE_EM_PER_SIZE = 2048 / (1854 + 434)
ITALIC_EM_PER_SIZE = 2048 / (1878 + 449)
#: Caption styles: ``bold`` (one short white line at a time, one yellow word; the default for a new
#: show, :mod:`creation.caption_bold`), ``subtle`` (yellow word flicker; ``house`` is its older name and
#: still works), ``plain`` (white whole lines), ``none``.
CAPTION_STYLES = ("bold", "subtle", "house", "plain", "none")
#: The style a function draws when it is not told: today's house flicker (``subtle``).
DEFAULT_CAPTION_STYLE = "house"
#: A new show's captions (user decision, 6 Oct 2026); a show with finished episodes keeps ``subtle``.
NEW_SHOW_CAPTION_STYLE = "bold"
#: The style a show that already has finished episodes keeps unless the operator switches.
CONTINUING_SHOW_CAPTION_STYLE = "subtle"
#: Frozen for desks created before 2026-10-06; do not change: a legacy desk's unset style, and the
#: style list its notes name (:mod:`creation.rules_epoch`).
LEGACY_CAPTION_STYLE = "house"
LEGACY_CAPTION_STYLES = ("house", "plain", "none")
#: Other names a style answers to, and the name the builders use.
_STYLE_ALIASES = {"subtle": "house"}


def canonical_caption_style(style: str) -> str:
    """The builders' name for a caption style: ``subtle`` is ``house``; the rest are their own."""

    return _STYLE_ALIASES.get(style, style)


def caption_style_word(style: str) -> str:
    """The style as the kit names it to a person: ``house`` is said ``subtle``."""

    return "subtle" if style in ("house", "subtle") else style


def desk_has_finished_episodes(desk: Path) -> bool:
    """True when the show has a finished episode: a ``finish`` record on a take, or a post yes.

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    bool
        Whether any ``epNN/takes/take-epNN-tK-finish-vN.json`` exists or any
        episode's post gate in ``series.json`` is approved.
    """

    desk = desk.expanduser().resolve()
    if any(desk.glob("ep*/takes/take-ep*-t*-finish-v*.json")):
        return True
    from creation.ops.state import load_series

    try:
        series = load_series(desk)
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return False
    return any(ep.post.status == "approved" for ep in series.episodes)


def _legacy_desk(desk: Path) -> bool:
    from creation.rules_epoch import is_legacy

    return is_legacy(desk)


def _per_word_desk(desk: Path, episode: int) -> bool:
    """Whether this episode's captions show each word when it is said (new desks; Group B episodes)."""

    from creation.rules_epoch import continuing_fix

    return continuing_fix(desk, "per_word_captions", episode=episode)


def show_caption_style(desk: Path) -> tuple[str, str]:
    """The desk's caption style and where it came from, before any per-run flag.

    ``caption_style`` in ``production.config.json`` when set; otherwise a show
    with finished episodes keeps ``subtle`` (the captions it was finished
    with) and a new show gets ``bold``.

    Returns
    -------
    tuple[str, str]
        The style as configured (``bold``, ``subtle``, ``house`` ...; may be a
        server recipe), and ``show`` / ``continuing`` / ``new``, or ``legacy``
        (a desk created before 6 Oct 2026 with no style set: ``house``, as it
        always was; :mod:`creation.rules_epoch`).
    """

    from creation.production_config import load_production_config

    configured = str(load_production_config(desk).caption_style or "").strip()
    if configured:
        return configured, "show"
    if _legacy_desk(desk):
        # Frozen for desks created before 2026-10-06; do not change: their unset style was house.
        return LEGACY_CAPTION_STYLE, "legacy"
    if desk_has_finished_episodes(desk):
        return CONTINUING_SHOW_CAPTION_STYLE, "continuing"
    return NEW_SHOW_CAPTION_STYLE, "new"


def resolve_caption_style(desk: Path, given: str | None = None) -> tuple[str, str]:
    """The caption style to burn: ``given`` (the command's flag), else the show's (:func:`show_caption_style`).

    Parameters
    ----------
    desk
        Series desk (its ``production.config.json`` ``caption_style``).
    given
        ``--caption-style`` on ``finish`` / ``caption`` / ``reel``; ``None`` reads the desk.

    Returns
    -------
    tuple[str, str]
        The builders' style (``bold``, ``house`` for ``subtle``, ``plain`` or
        ``none``), and ``""`` or a note when the desk's value is not a local
        style (a server recipe set for ``--api-captions``, such as
        ``viral_karaoke``): those desks are captioned as a show with no choice is.

    Raises
    ------
    ValueError
        When ``given`` is not one of :data:`CAPTION_STYLES`.
    """

    if given is not None:
        if given not in CAPTION_STYLES:
            raise ValueError(
                f"--caption-style {given!r}: choose one of {', '.join(CAPTION_STYLES)}"
            )
        return canonical_caption_style(given), ""
    configured, source = show_caption_style(desk)
    if configured in CAPTION_STYLES:
        return canonical_caption_style(configured), ""
    if source == "show" and _legacy_desk(desk):
        # Frozen for desks created before 2026-10-06; do not change (the note as it was then).
        return LEGACY_CAPTION_STYLE, (
            f"the desk's caption_style {configured!r} is not a local caption style "
            f"({', '.join(LEGACY_CAPTION_STYLES)}); captioned {LEGACY_CAPTION_STYLE}"
        )
    fallback = (
        CONTINUING_SHOW_CAPTION_STYLE
        if desk_has_finished_episodes(desk)
        else NEW_SHOW_CAPTION_STYLE
    )
    return canonical_caption_style(fallback), (
        f"the desk's caption_style {configured!r} is not a local caption style "
        f"({', '.join(CAPTION_STYLES)}); captioned {fallback}"
    )


def house_font_size(height: int) -> int:
    """ASS ``Fontsize`` of the house caption on a frame ``height`` px high (64 on 1920, 45 on 1344)."""

    return max(8, round(HOUSE_FONT_SIZE * height / HOUSE_CANVAS_HEIGHT))


def side_margin(width: int) -> int:
    """Left and right ASS margin on a frame ``width`` px wide (60 on 1080)."""

    return max(1, round(HOUSE_SIDE_MARGIN * width / HOUSE_CANVAS_WIDTH))


def italic_size(size: int) -> int:
    """ASS ``Fontsize`` that draws Georgia italic at the same em as Arial Bold at ``size``.

    Same em is how the retired internal kit set its italic caption (one font
    size for both faces); cap heights then match within a few percent.
    """

    return max(1, round(size * HOUSE_EM_PER_SIZE / ITALIC_EM_PER_SIZE))


#: ASS colours are &HAABBGGRR: yellow #FFE500 (white for ``plain``), black edge, 50% black shadow.
PRIMARY_COLOUR = "&H0000E5FF"
PLAIN_COLOUR = "&H00FFFFFF"
OUTLINE_COLOUR = "&H00000000"
SHADOW_COLOUR = "&H80000000"
MAX_WORDS_ON_SCREEN = 3
#: Letterbox captions build up within a phrase of up to this many words (6 Oct 2026).
MAX_PHRASE_WORDS = 5
#: A word ending in one of these ends a phrase (a sentence or a clause, a breath).
_PHRASE_END = (".", "!", "?", "…", ",", ";", ":", "—", "–")
#: Words a phrase should not end on (an article, a possessive, an auxiliary, a preposition), and words a
#: phrase should start with (a conjunction or a preposition): fictora-drama ``caption_compile`` break rules.
_NEVER_END_ON = frozenset(
    """
    a an the my your his her its our their this that these those
    is are was were am be been being do does did have has had
    will would can could shall should may might must
    of to in on at for with from by as and or but not no
    """.split()
)
_BREAK_BEFORE = frozenset(
    """
    and but or nor so yet because although though while whereas since unless until
    if when whenever where wherever after before as than that which who whom whose
    in on at to from with without within into onto over under above below through
    across against between among during about for of by like near past toward towards
    """.split()
)
#: A pause this long between two timed words is a breath: the phrase ends there.
PHRASE_BREATH_SECONDS = 0.35
#: Caption chunking: ``three`` (portrait house flicker), ``phrase`` (letterbox) or ``bold``
#: (one short chunk at a time, :mod:`creation.caption_bold`).
CHUNKINGS = ("three", "phrase", "bold")

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

#: Word timing (a transcript of the take): hold after the line's last heard word.
WORD_HOLD_SECONDS = 0.25
#: One word longer than this (and than :data:`SECONDS_PER_WORD_CHAR` per character) is a
#: Whisper stretch into the silence before or after it (a stammer drawn out to the next word).
MAX_WORD_SECONDS = 1.2
SECONDS_PER_WORD_CHAR = 0.25
#: A leading word this short (characters), cut off from the rest by a pause, is a stammer.
STAMMER_MAX_CHARS = 2
#: A speech onset this close to a stretched word's end is the next word's, not this one's.
ONSET_END_MARGIN_SECONDS = 0.05
#: A whole English line stays up at least max(this, words x READ_SECONDS_PER_WORD) to be read.
#: A word stamped in silence moves to the speech inside it only when that is at least this much later.
ONSET_SNAP_SECONDS = 0.1
MIN_LINE_SECONDS = 1.2
READ_SECONDS_PER_WORD = 0.3

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
    #: Bold only (:mod:`creation.caption_bold`): the whole chunk this cue belongs to ("" otherwise) ...
    chunk: str = ""
    #: ... the index of its yellow word in the chunk (-1: none) ...
    emphasis: int = -1
    #: ... and how many of the chunk's words are said by this cue's start (0: not Bold).
    shown: int = 0


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
    #: What is heard (``spoken_text``, else ``text``): matched against a transcript of the take.
    performed: str = ""
    #: Other spellings of what is heard (``text``, ``spoken_text``), for the transcript match.
    spellings: tuple[str, ...] = ()
    #: The writer-marked emphasis word (the spine's optional ``emphasis``), for Bold's yellow word.
    emphasis: str | int | None = None

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

    This is the whole episode (a joined episode file). One take of a two- or
    four-take episode plays only its own beats: see :func:`take_caption_lines`.

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
    beats = [
        beat
        for beat in body.get("beats") or []
        if isinstance(beat, dict) and beat.get("episode_id") == episode_id
    ]
    return _beat_caption_lines(body, beats)


def take_caption_lines(
    spine: dict[str, Any],
    episode_ordinal: int,
    *,
    take_index: int,
    take_count: int | None,
) -> tuple[list[CaptionLine], str]:
    """Return the spoken lines of the beats one take plays, as the caption sees them.

    The beats are split into takes the way the board and ``check-lines`` split
    them (:func:`creation.spine_view.beats_by_take`, from the spine's
    ``beats_per_storyboard_set``). A one-take episode gets every line.

    When the split cannot be read (the spine has no ``beats_per_storyboard_set``
    on a multi-take episode, no take count is known, or the pattern and the take
    count disagree) every line of the episode is returned, as before, with a
    warning naming why.

    Parameters
    ----------
    spine
        Spine JSON as saved on the desk (bare spine or ``{"spine": …}``).
    episode_ordinal
        1-based episode number.
    take_index
        1-based take (``t2`` is 2).
    take_count
        Takes on the desk for this episode (its series slot); ``None`` when the
        desk does not say, then the spine's storyboard pattern gives the count.

    Returns
    -------
    tuple[list[CaptionLine], str]
        The take's lines, and ``""`` or a ``TAKE LINES: …`` warning when every
        line of the episode was returned because the split is not known.
    """

    from creation.spine_view import beats_by_take

    body = spine.get("spine", spine)
    pattern = tuple(
        int(n)
        for n in body.get("beats_per_storyboard_set") or ()
        if isinstance(n, int) or str(n).isdigit()
    )
    count = take_count if take_count is not None else (len(pattern) or None)
    if count == 1 and take_index == 1:
        return episode_caption_lines(spine, episode_ordinal), ""
    reason = ""
    if count is None:
        reason = (
            "the desk names no takes for the episode and the spine has no "
            "beats_per_storyboard_set"
        )
    elif not pattern:
        reason = "the spine has no beats_per_storyboard_set"
    elif len(pattern) != count:
        reason = (
            f"the spine's beats_per_storyboard_set {list(pattern)} does not match "
            f"the {count} take(s) on the desk"
        )
    elif not 1 <= take_index <= count:
        reason = f"t{take_index} is not one of the {count} take(s) on the desk"
    if reason:
        return episode_caption_lines(spine, episode_ordinal), (
            f"TAKE LINES: ep{episode_ordinal:02d} t{take_index} is captioned with every "
            f"line of the episode, not only its own, because {reason}. Check the "
            "captions against what the take says."
        )
    grouped = beats_by_take(body, episode=episode_ordinal, take_count=count)
    return _beat_caption_lines(body, grouped[take_index - 1]), ""


def desk_take_count(desk: Path, episode_ordinal: int) -> int | None:
    """Takes in the desk's series slot for one episode.

    Parameters
    ----------
    desk
        Series desk root.
    episode_ordinal
        1-based episode number.

    Returns
    -------
    int | None
        The slot's take count; ``None`` when ``series.json`` is missing, not a
        v1 desk, or has no such episode (the caller then falls back on the
        spine's storyboard pattern, see :func:`take_caption_lines`).
    """

    from creation.ops.state import episode_by_ordinal, load_series

    try:
        slot = episode_by_ordinal(load_series(desk), episode_ordinal)
    except (FileNotFoundError, ValueError):
        return None
    return len(slot.takes) or None


_TAKE_IN_NAME = re.compile(r"^take-ep\d+-t(\d+)(?:-|$)")


def take_index_from_name(path: Path) -> int | None:
    """The take a desk file is, from its name: ``take-ep01-t2-raw-v1.mp4`` is 2.

    Parameters
    ----------
    path
        A take file.

    Returns
    -------
    int | None
        The take number; ``None`` for a file not named for one take (a joined
        episode file), which is then captioned with every line of the episode.
    """

    match = _TAKE_IN_NAME.match(path.stem)
    return int(match.group(1)) if match and int(match.group(1)) >= 1 else None


def _voice_only_cast(body: dict[str, Any]) -> set[str]:
    """Cast ids the server marks ``voice_only`` (a character it only ever hears)."""

    return {
        str(card.get("cast_id"))
        for card in body.get("cast") or []
        if isinstance(card, dict)
        and card.get("cast_id")
        and card.get("voice_only") is True
    }


def _beat_caption_lines(
    body: dict[str, Any], beats: Sequence[dict[str, Any]]
) -> list[CaptionLine]:
    """The caption lines of ``beats`` in the order given (see :func:`episode_caption_lines`)."""

    voice_only = _voice_only_cast(body)
    lines: list[CaptionLine] = []
    for beat in beats:
        for line in beat.get("dialogue_lines") or []:
            # Captions are English subtitles: ``subtitle_text`` when the line has one, else ``text``.
            # Their timing comes from where speech is heard on the take (``silencedetect``), or on a
            # show not spoken in English from the words a transcript matched to ``performed``.
            text = str(line.get("subtitle_text") or line.get("text") or "").strip()
            if not text:
                continue
            heard_not_seen = (
                line.get("off_screen") is True
                or str(line.get("cast_id") or "") in voice_only
            )
            spoken = str(line.get("spoken_text") or "").strip()
            written = str(line.get("text") or "").strip()
            from creation.caption_bold import spine_emphasis

            lines.append(
                CaptionLine(
                    str(line.get("line_id") or f"line {len(lines) + 1}"),
                    text,
                    heard_not_seen,
                    spoken or written or text,
                    tuple(dict.fromkeys(t for t in (written, spoken) if t)),
                    spine_emphasis(line),
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
    time. Spans left over after the last line (ambience, a door, music, or
    speech that is not in the script) are not captioned:
    :func:`anchor_lines_and_unused` returns them, and :func:`time_lines`
    warns about them (``EXTRA SPEECH``).

    Raises
    ------
    ValueError
        When there are fewer speech spans than lines.
    """

    return anchor_lines_and_unused(lines, spans)[0]


def anchor_lines_and_unused(
    lines: Sequence[str], spans: Sequence[Span]
) -> tuple[list[Span], list[Span]]:
    """:func:`anchor_lines`, and the speech spans no line took.

    Lines take spans strictly in order, so a stray stretch of speech before a
    line (a mumble, a line the take invented) is taken by that line and every
    later line moves one stretch on; the real last line's speech is then left
    over. Leftover spans are the only sign of that on a take with no
    transcript.

    Returns
    -------
    tuple[list[Span], list[Span]]
        One anchor per line, and the unused spans in order.

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
    return anchored, list(spans[index:])


#: Unused speech stretches named in an ``EXTRA SPEECH`` warning (the rest are counted).
EXTRA_SPEECH_LISTED = 5


def extra_speech_warning(
    *, line_count: int, span_count: int, unused: Sequence[Span]
) -> str:
    """The ``EXTRA SPEECH`` warning: more speech stretches on the take than its lines took.

    Parameters
    ----------
    line_count
        Lines timed on the take.
    span_count
        Speech stretches found on it (silencedetect).
    unused
        The stretches no line took (:func:`anchor_lines_and_unused`).

    Returns
    -------
    str
        The warning, or ``""`` when every stretch was taken.
    """

    if not unused:
        return ""
    listed = ", ".join(
        f"{span.start:.2f}-{span.end:.2f}s" for span in unused[:EXTRA_SPEECH_LISTED]
    )
    more = len(unused) - EXTRA_SPEECH_LISTED
    if more > 0:
        listed += f" and {more} more"
    return (
        f"EXTRA SPEECH: {span_count} speech stretch(es) for {line_count} line(s); not captioned: "
        f"{listed}. Lines are placed on speech stretches in order, so if any stretch before one of "
        "these is speech that is not in the script (a mumble, a line the take made up), every later "
        "caption is on the wrong words. Watch the captions. Fix: a transcript of the take "
        "(`review --transcribe`; captions then follow the words), `finish --mute A-B` for stray "
        "speech, or --line-start / --line-end per line"
    )


def without_windows(
    spans: Sequence[Span], windows: Sequence[tuple[float, float]]
) -> list[Span]:
    """Speech spans with the given windows cut out (lines laid by hand are not script speech).

    A piece left beside a window shorter than :data:`SILENCE_MIN_SECONDS` is
    dropped: silencedetect cannot see a silence that short, so such a piece is
    as likely the quiet just before or after the laid line as speech.
    """

    kept: list[Span] = []
    for span in spans:
        pieces = [span]
        for a, b in windows:
            cut: list[Span] = []
            for piece in pieces:
                if b <= piece.start or a >= piece.end:
                    cut.append(piece)
                    continue
                if a - piece.start >= SILENCE_MIN_SECONDS:
                    cut.append(Span(piece.start, a))
                if piece.end - b >= SILENCE_MIN_SECONDS:
                    cut.append(Span(b, piece.end))
            pieces = cut
        kept += pieces
    return kept


class HeardWord(Protocol):
    """One word of a transcript (:class:`creation.post.whisper.Word`)."""

    @property
    def start(self) -> float: ...
    @property
    def end(self) -> float: ...
    @property
    def text(self) -> str: ...
    @property
    def reading(self) -> str | None: ...


def _word_chars(word: HeardWord) -> int:
    return max(1, len(re.sub(r"[\W_]", "", word.reading or word.text)))


def _word_limit(word: HeardWord) -> float:
    """Longest a word can plausibly last: 1.2 s, or 0.25 s per character for a long word."""

    return max(MAX_WORD_SECONDS, SECONDS_PER_WORD_CHAR * _word_chars(word))


def speech_onset_in(word: HeardWord, spans: Sequence[Span]) -> float | None:
    """Where speech starts inside a word Whisper stretched back over silence, or None.

    The last speech span that starts inside the word (after its start, not
    in its last :data:`ONSET_END_MARGIN_SECONDS`) after a real pause (over
    :data:`MAX_PAUSE_IN_LINE_SECONDS` of silence since the span before it; a
    shorter gap is a breath inside speech). Hanakaze ep 4's 「ちょっと!」 was
    heard 9.37-12.45 s and said from 11.34 s; ep 6's 「悪」 8.81-12.57 s from
    12.42 s.

    Parameters
    ----------
    word
        A stretched transcript word.
    spans
        Speech spans of the take (:func:`speech_spans`), in order.

    Returns
    -------
    float or None
        The onset in seconds.
    """

    onset: float | None = None
    previous_end = 0.0
    for span in sorted(spans, key=lambda s: s.start):
        if (
            word.start < span.start <= word.end - ONSET_END_MARGIN_SECONDS
            and span.start - previous_end > MAX_PAUSE_IN_LINE_SECONDS
        ):
            onset = span.start
        previous_end = max(previous_end, span.end)
    return onset


def word_span(
    words: Sequence[HeardWord],
    speech: Callable[[], Sequence[Span]] | None = None,
) -> Span | None:
    """Where one line is spoken, from the transcript words matched to it.

    The span runs from the start of the line's core to the end of its last
    word. At the head:

    - a word Whisper stretched past what one word can last
      (:func:`_word_limit`) starts where its speech starts
      (:func:`speech_onset_in`, on the take's ``speech`` spans), and is
      skipped only when no onset is found inside it (``もう`` over 1.86 s,
      a stammer drawn out to the next word);
    - one short leading word (up to two characters, a stammer such as
      ``も、``) cut off from the rest by a pause over 0.6 s is skipped.

    A last word that is stretched is cut to what it can last from its start;
    a single stretched word starts at its onset, else keeps what it can last
    up to its end.

    Parameters
    ----------
    words
        The line's words in order (``start``, ``end``, ``text``, ``reading``).
    speech
        Speech spans of the take, asked for only when a word is stretched.

    Returns
    -------
    Span or None
        None when there are no words.
    """

    core = [w for w in words if w.end >= w.start]
    if not core:
        return None

    def onset(word: HeardWord) -> float | None:
        if speech is None or word.end - word.start <= _word_limit(word):
            return None
        return speech_onset_in(word, speech())

    stammer_dropped = False
    start_at: float | None = None
    while len(core) > 1:
        head, after = core[0], core[1]
        if head.end - head.start > _word_limit(head):
            start_at = onset(head)
            if start_at is not None:
                break
            core.pop(0)
        elif (
            not stammer_dropped
            and after.start - head.end > MAX_PAUSE_IN_LINE_SECONDS
            and _word_chars(head) <= STAMMER_MAX_CHARS
        ):
            core.pop(0)
            stammer_dropped = True
        else:
            break
    first, last = core[0], core[-1]
    if len(core) == 1 and first.end - first.start > _word_limit(first):
        start_at = onset(first)
        if start_at is None:
            start_at = first.end - _word_limit(first)
        return Span(round(start_at, 3), round(first.end, 3))
    begin = first.start if start_at is None else start_at
    end = min(last.end, last.start + _word_limit(last))
    return Span(round(begin, 3), round(max(end, begin), 3))


def word_anchors(
    lines: Sequence[CaptionLine],
    words: Sequence[HeardWord],
    speech: Callable[[], Sequence[Span]] | None = None,
) -> list[Span | None]:
    """Each line's span from a transcript of the take, or None where the line was not matched.

    Lines are matched in order with :func:`creation.post.whisper.line_windows`
    on what is heard (``performed`` and every spelling), so a Japanese line is
    matched on its reading; :func:`word_span` then trims the matched words
    (a stretched first word starts at its onset in ``speech``).
    """

    return [
        word_span(matched, speech) if matched else None
        for matched in line_words(lines, words)
    ]


def line_words(
    lines: Sequence[CaptionLine], words: Sequence[HeardWord]
) -> list[list[HeardWord]]:
    """The transcript words each line was heard as (empty where the line was not matched).

    Lines are matched in order with :func:`creation.post.whisper.line_windows`
    on what is heard (``performed`` and every spelling).
    """

    from creation.post.whisper import line_windows

    heard = tuple(words)
    windows = line_windows(
        heard,  # type: ignore[arg-type]
        tuple(line.performed or line.text for line in lines),
        alternates=tuple(line.spellings for line in lines),
    )
    return [
        [heard[i] for i in window.words]
        if window.start is not None and window.words
        else []
        for window in windows
    ]


def _speech_share(a: float, b: float, spans: Sequence[Span]) -> float:
    """Share of ``a``-``b`` that is speech."""

    if b <= a:
        return 1.0
    inside = sum(max(0.0, min(b, s.end) - max(a, s.start)) for s in spans)
    return inside / (b - a)


def word_onset(word: HeardWord, speech: Sequence[Span] | None = None) -> float:
    """When a transcript word starts being said (never before its sound starts).

    Whisper's word times run early: a word after a pause is often stamped
    from the end of the word before it (SCP-173 Blink ep 1: ``open`` heard
    8.19 s, said from 8.8 s; ``Please,`` heard 6.29-8.19 s, said from 7.8 s).
    With the take's speech spans:

    - a word that starts mostly in silence starts where the first speech span
      inside it starts (the part of the word before it is under half speech,
      and at least :data:`ONSET_SNAP_SECONDS` long);
    - a word longer than one word can last (:func:`_word_limit`) starts at
      its onset after a real pause (:func:`speech_onset_in`), else where it
      is stamped when that is speech (stretched over the silence after it).

    Without speech spans a stretched word starts where it can last up to its
    end; any other word starts where it is stamped.

    Parameters
    ----------
    word
        One transcript word.
    speech
        The take's speech spans (:func:`speech_spans`), or None.

    Returns
    -------
    float
        The onset in seconds.
    """

    stretched = word.end - word.start > _word_limit(word)
    if speech:
        ordered = sorted(speech, key=lambda s: s.start)
        if stretched:
            onset = speech_onset_in(word, ordered)
            if onset is not None:
                return onset
        for span in ordered:
            if (
                word.start + ONSET_SNAP_SECONDS
                <= span.start
                <= word.end - ONSET_END_MARGIN_SECONDS
                and _speech_share(word.start, span.start, ordered) < 0.5
            ):
                return span.start
        if not stretched or _speech_share(word.start, word.start + 0.2, ordered) >= 0.5:
            return word.start
    if stretched:
        return max(word.start, word.end - _word_limit(word))
    return word.start


def _token_key(text: str) -> str:
    """Letters and digits only, case-folded: ``NOT—`` and ``not!`` are the same word."""

    return "".join(
        ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum()
    )


def heard_word_cues(
    text: str,
    words: Sequence[HeardWord],
    *,
    speech: Sequence[Span] | None = None,
    not_before: float | None = None,
) -> tuple[list[Cue], list[str]]:
    """One cue per word of a line, each starting when that word is said (the transcript's own times).

    The line's words are aligned with the words heard for it. Where they agree
    (letters and digits, any case or punctuation) the line's spelling is shown
    (``NOT—`` for a heard ``not!``); where one heard word is a near spelling of
    one written word (a name, ``Ren`` for ``Wren``) the line's spelling stays.
    Anywhere else what was heard wins: a written word that was not said is not
    shown, and a word said that the line does not have is shown as heard. Each
    such change is reported.

    Every word starts at :func:`word_onset` (never before its sound), at least
    ``not_before`` and 50 ms after the word before it.

    Parameters
    ----------
    text
        The line as written.
    words
        The transcript words the line was heard as (:func:`line_words`).
    speech
        The take's speech spans, or None.
    not_before
        The line's start (:func:`word_span`): no word starts before it.

    Returns
    -------
    tuple[list[Cue], list[str]]
        The word cues (``end`` is where the word stops) and the wording changes.
    """

    import difflib

    written = text.split()
    heard = [w for w in words if w.text.strip()]
    if not heard:
        return [], []
    a = [_token_key(t) for t in written]
    b = [_token_key(w.text) for w in heard]
    shown: list[tuple[str, HeardWord]] = []
    changes: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, a, b, autojunk=False
    ).get_opcodes():
        if op == "equal":
            shown += [(written[i1 + k], heard[j1 + k]) for k in range(i2 - i1)]
            continue
        if op == "replace" and "".join(a[i1:i2]) == "".join(b[j1:j2]):
            # One word heard as two (``D-9341.`` heard ``D`` ``-9341.``), or two as one: the line's
            # spelling, each written word timed on the heard word its first letter falls in.
            ends, total = [], 0
            for key in b[j1:j2]:
                total += len(key)
                ends.append(total)
            offset = 0
            for i in range(i1, i2):
                at = next(
                    k
                    for k, end in enumerate(ends)
                    if offset < end or k == len(ends) - 1
                )
                shown.append((written[i], heard[j1 + at]))
                offset += len(a[i])
            continue
        if (
            op == "replace"
            and i2 - i1 == j2 - j1
            and all(_near_spelling(written[i1 + k], b[j1 + k]) for k in range(i2 - i1))
        ):
            shown += [(written[i1 + k], heard[j1 + k]) for k in range(i2 - i1)]
            continue
        said_words: list[tuple[str, HeardWord]] = []
        for w in heard[j1:j2]:
            said_text = w.text.strip()
            if said_words and said_text[:1] in "-'’":
                # Whisper splits ``D-9341.`` into ``D`` + ``-9341.``: one word on screen.
                said_words[-1] = (said_words[-1][0] + said_text, said_words[-1][1])
            else:
                said_words.append((said_text, w))
        said = " ".join(t for t, _ in said_words)
        wrote = " ".join(written[i1:i2])
        if said and wrote:
            changes.append(f"{wrote!r} was said {said!r}")
        elif said:
            changes.append(f"{said!r} was said (not in the line)")
        else:
            changes.append(f"{wrote!r} was not said")
        shown += said_words
    speech = sorted(speech, key=lambda s: s.start) if speech else None
    cues: list[Cue] = []
    floor = not_before if not_before is not None else float("-inf")
    for shown_text, word in shown:
        start = max(word_onset(word, speech), floor)
        if cues:
            start = max(start, cues[-1].start + 0.05)
        end = min(word.end, start + _word_limit(word))
        cues.append(Cue(round(start, 3), round(max(end, start + 0.05), 3), shown_text))
    return cues, changes


def _near_spelling(written: str, heard_key: str) -> bool:
    """A heard word that is the written one spelt another way: a name by sound, any word of 4+ letters one letter off."""

    from creation.post.whisper import _one_edit_apart, sounds_alike

    key = _token_key(written)
    if not key or not heard_key:
        return False
    if written[:1].isupper() and len(key) >= 3 and sounds_alike(heard_key, key):
        return True
    return min(len(key), len(heard_key)) >= 4 and _one_edit_apart(key, heard_key)


@dataclass(frozen=True)
class LineTiming:
    """Where each line is placed and how.

    ``methods[i]`` says what timed line ``i``: ``words`` (the transcript),
    ``speech`` (speech spans), ``manual`` (``--line-start`` and ``--line-end``),
    or a mix such as ``manual start, words end``; ``estimated`` is the old
    end from a hand start and the line's length.
    """

    anchors: tuple[Span, ...]
    methods: tuple[str, ...]
    holds: tuple[float, ...]
    fixed_ends: tuple[bool, ...]
    #: ``EXTRA SPEECH: …`` when a line was timed on speech stretches and some stretch was left over.
    warnings: tuple[str, ...] = ()
    #: Per line: one cue per word at the time the transcript heard it (:func:`heard_word_cues`),
    #: or None where the words are spread over the line's span (no transcript words, or hand times).
    word_cues: tuple[tuple[Cue, ...] | None, ...] = ()
    #: ``line N: 'x' was said 'y'`` per place the shown words follow what was said, not the line.
    wording: tuple[str, ...] = ()


#: A transcript's line that starts more than this after its voice measured on the take is not trusted.
WORDS_LATE_SECONDS = 0.3


def time_lines(
    lines: Sequence[CaptionLine],
    *,
    duration: float,
    line_starts: Sequence[float] | None = None,
    line_ends: Sequence[float] | None = None,
    words: Sequence[HeardWord] | None = None,
    spans: Callable[[], Sequence[Span]] | None = None,
    known: Sequence[Span | None] | None = None,
    per_word: bool = False,
    known_after_words: bool = False,
    floors: Sequence[float | None] | None = None,
) -> LineTiming:
    """Place every line: known windows first, then transcript words, then speech spans, hand times on top.

    Parameters
    ----------
    lines
        The episode's caption lines in order.
    duration
        Take length in seconds.
    line_starts, line_ends
        Hand times, one per line in order (``--line-start`` / ``--line-end``).
    words
        Transcript words of the take, on any show (see :func:`caption_take`).
    spans
        Speech spans of the take, asked for only when a line needs them (no
        hand start and no transcript match, or a first word Whisper stretched
        back over silence: :func:`speech_onset_in`). Asked at most once.
    known
        Per line (same order), the window the line plays in when it is known
        exactly (a locked-voice take's dialogue track, from the take facts'
        ``soundtrack.lines``), else ``None``. A known window is used as heard:
        method ``lines``, nothing transcribed or detected for it.
    per_word
        Time each word of a line timed on ``words`` when it was said
        (:func:`heard_word_cues`, on the speech spans), for word flicker:
        ``word_cues`` and ``wording`` are filled. Off for whole lines.
    known_after_words
        Time a line on its transcript words when the transcript heard it, and
        on its ``known`` window only when it did not, or heard it more than
        :data:`WORDS_LATE_SECONDS` after its ``floors`` voice start (a
        locked-voice take with a transcript, new desks: L-20261005-6).
        Default: ``known`` first.
    floors
        Per line (same order), where its voice was measured to start
        (:func:`creation.post.caption_timing.voice_starts`), or ``None``: a
        line timed automatically never starts before it. Hand starts win.

    Returns
    -------
    LineTiming

    Raises
    ------
    ValueError
        When a hand list has the wrong length, a line would end before it
        starts, or there are fewer speech spans than lines.
    """

    texts = [line.text for line in lines]
    n = len(texts)
    for flag, given in (("--line-start", line_starts), ("--line-end", line_ends)):
        if given and len(given) != n:
            raise ValueError(f"{flag} given {len(given)} time(s) for {n} line(s)")
    if spans is not None:
        spans = functools.cache(spans)  # silencedetect runs once, whoever asks first
    by_known: list[Span | None] = [
        known[i] if known and i < len(known) else None for i in range(n)
    ]
    heard: list[list[HeardWord]] = line_words(lines, words) if words else [[]] * n
    by_words: list[Span | None] = [
        word_span(matched, spans) if matched else None for matched in heard
    ]
    if known_after_words:
        # A line the transcript heard is timed on its words, not on its window; words heard well
        # after the voice measured on the take are the transcript's mistake, and the window stays.
        floor_of = [floors[i] if floors and i < len(floors) else None for i in range(n)]
        by_words = [
            None
            if w is not None and f is not None and w.start > f + WORDS_LATE_SECONDS
            else w
            for w, f in zip(by_words, floor_of)
        ]
        by_known = [k if w is None else None for k, w in zip(by_known, by_words)]
        by_words = [w if w is not None else k for k, w in zip(by_known, by_words)]
    else:
        by_words = [k if k is not None else w for k, w in zip(by_known, by_words)]
    by_speech: list[Span | None] = [None] * n
    warnings: list[str] = []
    if not line_starts and any(span is None for span in by_words):
        if spans is None:
            raise ValueError("no speech spans to time the lines; pass --line-start")
        found = spans()
        anchored, unused = anchor_lines_and_unused(texts, found)
        by_speech = list(anchored)
        warning = extra_speech_warning(
            line_count=n, span_count=len(found), unused=unused
        )
        if warning:
            warnings.append(warning)
    anchors: list[Span] = []
    methods: list[str] = []
    holds: list[float] = []
    fixed: list[bool] = []
    word_cues: list[tuple[Cue, ...] | None] = []
    wording: list[str] = []
    for i, text in enumerate(texts):
        auto, auto_how = (
            (by_words[i], "lines" if by_known[i] is not None else "words")
            if by_words[i] is not None
            else (by_speech[i], "speech")
        )
        if line_starts:
            start, start_how = float(line_starts[i]), "manual"
        else:
            assert auto is not None
            start, start_how = auto.start, auto_how
            floor = floors[i] if floors and i < len(floors) else None
            if floor is not None and start < floor < auto.end:
                # Never before the voice (L-20261005-6): a caption goes up when its line is heard.
                start = floor
        if line_ends:
            end, end_how = float(line_ends[i]), "manual"
        elif auto is not None and auto.end > start:
            end, end_how = auto.end, auto_how
        else:
            # A hand start with nothing heard to end on: as long as the line needs, up to the next start.
            following = (
                float(line_starts[i + 1]) if line_starts and i + 1 < n else duration
            )
            end = min(following, start + max(0.6, len(text.split()) * SECONDS_PER_WORD))
            end_how = "estimated"
        if end <= start:
            raise ValueError(
                f"line {i + 1} ends at {end:.2f}s, not after its start {start:.2f}s; check --line-start/--line-end"
            )
        cues: tuple[Cue, ...] | None = None
        if per_word and start_how == "words" and end_how == "words" and heard[i]:
            # Each word when it is said, not spread over the line (6 Oct 2026: spreading put
            # SCP-173 Blink ep 1's "door!" 0.8 s after it was said).
            found, changes = heard_word_cues(
                text, heard[i], speech=spans() if spans is not None else None,
                not_before=start,
            )  # fmt: skip
            if found:
                cues = tuple(found)
                wording += [f"line {i + 1}: {change}" for change in changes]
        word_cues.append(cues)
        anchors.append(Span(start, end))
        methods.append(
            start_how if start_how == end_how else f"{start_how} start, {end_how} end"
        )
        holds.append(
            WORD_HOLD_SECONDS if end_how == "words" else LAST_WORD_HOLD_SECONDS
        )
        fixed.append(end_how == "manual")
    return LineTiming(
        tuple(anchors), tuple(methods), tuple(holds), tuple(fixed), tuple(warnings),
        tuple(word_cues), tuple(wording),
    )  # fmt: skip


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


def flicker_cues(
    words: Sequence[Cue],
    *,
    hold_until: float | None = None,
    hold: float = LAST_WORD_HOLD_SECONDS,
) -> list[Cue]:
    """Build up to three words at a time, then reset (house flicker).

    Each event lasts until the next word starts; the last word holds ``hold``
    seconds, never past ``hold_until`` (the next line's start).
    """

    cues: list[Cue] = []
    for i, word in enumerate(words):
        chunk_start = i - (i % MAX_WORDS_ON_SCREEN)
        text = " ".join(w.text for w in words[chunk_start : i + 1])
        if i + 1 < len(words):
            end = words[i + 1].start
        else:
            end = word.end + hold
            if hold_until is not None:
                end = min(end, hold_until)
        cues.append(Cue(word.start, max(end, word.start + 0.05), text))
    return cues


def _bare(word: str) -> str:
    return "".join(ch for ch in word if ch.isalpha() or ch == "'").casefold()


def _split_run(run: list[int], words: Sequence[str]) -> list[list[int]]:
    """Cut one run of words (no punctuation inside) into balanced groups of at most ``MAX_PHRASE_WORDS``.

    Each cut moves by one word when that lands it before a conjunction or a
    preposition, or keeps it off an article or an auxiliary (the server's
    caption break rules), as long as no group then runs past the cap.
    """

    count = -(-len(run) // MAX_PHRASE_WORDS)
    if count <= 1:
        return [run]
    base, extra = divmod(len(run), count)
    sizes = [base + (1 if i < extra else 0) for i in range(count)]
    cuts = [sum(sizes[: i + 1]) for i in range(count - 1)]

    def score(cut: int) -> int:
        before, after = _bare(words[run[cut - 1]]), _bare(words[run[cut]])
        return (100 if before in _NEVER_END_ON else 0) - (
            20 if after in _BREAK_BEFORE else 0
        )

    for index, cut in enumerate(cuts):
        low = cuts[index - 1] if index else 0
        high = cuts[index + 1] if index + 1 < len(cuts) else len(run)
        best = cut
        for moved in (cut - 1, cut + 1):
            if (
                (low + 1 < moved < high - 1 or (low < moved < high and len(run) > 2))
                and moved - low <= MAX_PHRASE_WORDS
                and high - moved <= MAX_PHRASE_WORDS
            ):
                if score(moved) < score(best):
                    best = moved
        cuts[index] = best
    edges = [0, *cuts, len(run)]
    return [run[a:b] for a, b in zip(edges, edges[1:]) if b > a]


def phrase_groups(words: Sequence[Cue]) -> list[list[int]]:
    """Group one line's timed words into phrases: the letterbox caption chunks (6 Oct 2026).

    A phrase ends on punctuation (a sentence or a clause) or a breath (a pause
    of :data:`PHRASE_BREATH_SECONDS` between timed words); a run longer than
    :data:`MAX_PHRASE_WORDS` is cut into balanced groups at the best joint.
    One word is never left alone: an orphan joins the phrase before it (the
    one after it when it opens the line).

    Parameters
    ----------
    words
        One line's words, timed (:func:`time_words`).

    Returns
    -------
    list[list[int]]
        Word indices per phrase, in order.
    """

    texts = [w.text for w in words]
    runs: list[list[int]] = []
    current: list[int] = []
    for i, word in enumerate(words):
        current.append(i)
        breath = (
            i + 1 < len(words)
            and words[i + 1].start - word.end >= PHRASE_BREATH_SECONDS
        )
        if word.text.rstrip("\"')]”’").endswith(_PHRASE_END) or breath:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    groups = [group for run in runs for group in _split_run(run, texts)]
    merged: list[list[int]] = []
    for group in groups:
        if len(group) == 1 and merged:
            merged[-1] = merged[-1] + group
        else:
            merged.append(group)
    if len(merged) > 1 and len(merged[0]) == 1:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)
    return merged


def phrase_cues(
    words: Sequence[Cue],
    *,
    hold_until: float | None = None,
    hold: float = LAST_WORD_HOLD_SECONDS,
) -> list[Cue]:
    """Build each phrase up word by word (:func:`phrase_groups`), then start the next one fresh.

    Like :func:`flicker_cues`, each event lasts until the next word starts and
    the line's last word holds ``hold`` seconds, never past ``hold_until``.
    """

    cues: list[Cue] = []
    for group in phrase_groups(words):
        for position, i in enumerate(group):
            word = words[i]
            text = " ".join(words[j].text for j in group[: position + 1])
            if i + 1 < len(words):
                end = words[i + 1].start
            else:
                end = word.end + hold
                if hold_until is not None:
                    end = min(end, hold_until)
            cues.append(Cue(word.start, max(end, word.start + 0.05), text))
    return cues


def readable_seconds(text: str) -> float:
    """Least time a whole English line stays on screen: max(1.2 s, 0.3 s per word)."""

    return max(MIN_LINE_SECONDS, len(text.split()) * READ_SECONDS_PER_WORD)


def whole_line_cue(
    text: str,
    span: Span,
    *,
    hold_until: float | None = None,
    hold: float = LAST_WORD_HOLD_SECONDS,
    min_seconds: float = 0.0,
) -> Cue:
    """One cue showing the whole line over its speech span.

    The line holds ``hold`` seconds after the span and stays up at least
    ``min_seconds`` (extended forward only), never past ``hold_until`` (the
    next line's start).
    """

    end = max(span.end + hold, span.start + min_seconds)
    if hold_until is not None:
        end = min(end, hold_until)
    return Cue(round(span.start, 3), round(max(end, span.start + 0.05), 3), text)


def build_line_cues(
    lines: Sequence[str],
    anchors: Sequence[Span],
    *,
    whole_lines: bool = False,
    italic: Sequence[bool] = (),
    skip: Sequence[bool] = (),
    holds: Sequence[float] = (),
    fixed_ends: Sequence[bool] = (),
    chunking: str = "three",
    word_cues: Sequence[Sequence[Cue] | None] = (),
    emphasis: Sequence[str | int | None] = (),
) -> list[list[Cue]]:
    """Cues for every line on its anchor span, grouped per line (empty for a skipped line).

    ``italic[i]`` sets line ``i``'s cues in Georgia italic; ``skip[i]`` leaves
    line ``i`` uncaptioned (not English) while its span still bounds the hold
    of the line before it. Missing entries mean False. ``holds[i]`` is how
    long line ``i`` holds after its span (default 0.15 s). ``fixed_ends[i]``
    (a ``--line-end`` given by hand) ends line ``i`` exactly at its span end:
    no hold and no readable minimum.

    A whole line stays up at least :func:`readable_seconds`, extended forward
    only, never into the next line.

    ``word_cues[i]`` (:attr:`LineTiming.word_cues`) times line ``i``'s words
    as they were heard, and is the text shown; without it the line's words are
    spread over its span by length (:func:`time_words`: an estimate).

    Every cue's text goes through :func:`creation.caption_dashes.caption_text`
    (no em or en dash on the picture; the script and the voice keep theirs).

    With ``chunking="bold"`` (the Bold style) each line shows one short chunk
    at a time, each word from when it is said, one word yellow
    (:func:`creation.caption_bold.bold_cues`; ``emphasis[i]`` is line ``i``'s
    writer-marked word, else a heuristic picks); whole lines are drawn in
    Bold's look (:func:`creation.caption_bold.bold_whole_line`).

    English shows flicker word by word (:func:`flicker_cues`; with
    ``chunking="phrase"``, a letterbox show, :func:`phrase_cues`). With
    ``whole_lines`` (a show spoken in Japanese or Korean, captioned with the
    English line) each line is one cue over its speech span: English words
    cannot be timed against Japanese or Korean speech, so spreading them would
    flash words that are not being said. This is the whole-line rule the
    server follows for a translated subtitle.
    """

    groups: list[list[Cue]] = []
    for i, (text, span) in enumerate(zip(lines, anchors)):
        if i < len(skip) and skip[i]:
            groups.append([])
            continue
        next_start = anchors[i + 1].start if i + 1 < len(anchors) else None
        fixed = i < len(fixed_ends) and fixed_ends[i]
        hold = 0.0 if fixed else holds[i] if i < len(holds) else LAST_WORD_HOLD_SECONDS
        marked = emphasis[i] if i < len(emphasis) else None
        if whole_lines:
            line_cues = [
                whole_line_cue(
                    text,
                    span,
                    hold_until=next_start,
                    hold=hold,
                    min_seconds=0.0 if fixed else readable_seconds(text),
                )
            ]
            if chunking == "bold":
                from creation.caption_bold import bold_whole_line, marked_index

                at = marked_index(text.split(), marked)
                line_cues = [bold_whole_line(line_cues[0], () if at is None else {at})]
        elif chunking == "bold":
            from creation.caption_bold import bold_cues, marked_index

            heard = word_cues[i] if i < len(word_cues) else None
            timed = list(heard) if heard else time_words(text, span)
            at = marked_index([w.text for w in timed], marked)
            line_cues = bold_cues(
                timed,
                hold_until=next_start,
                hold=hold,
                marked=() if at is None else {at},
            )
        else:
            build = phrase_cues if chunking == "phrase" else flicker_cues
            heard = word_cues[i] if i < len(word_cues) else None
            timed = list(heard) if heard else time_words(text, span)
            line_cues = build(timed, hold_until=next_start, hold=hold)
        slanted = i < len(italic) and italic[i]
        # The cue's text is drawn without an em or en dash (after timing, so word timings
        # are unchanged): "Please—" shows "Please…" (creation.caption_dashes).
        groups.append(
            [
                replace(c, text=no_dash_text(c.text), italic=slanted or c.italic)
                for c in line_cues
            ]
        )
    return groups


def build_cues(
    lines: Sequence[str],
    anchors: Sequence[Span],
    *,
    whole_lines: bool = False,
    italic: Sequence[bool] = (),
    skip: Sequence[bool] = (),
    holds: Sequence[float] = (),
    fixed_ends: Sequence[bool] = (),
) -> list[Cue]:
    """Every cue of :func:`build_line_cues`, in order."""

    groups = build_line_cues(
        lines, anchors, whole_lines=whole_lines, italic=italic, skip=skip,
        holds=holds, fixed_ends=fixed_ends,
    )  # fmt: skip
    return [cue for group in groups for cue in group]


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


#: Kana, CJK ideographs and Hangul (and their punctuation): Arial has none of these glyphs.
_CJK_CHARS = re.compile("[　-ヿ㐀-䶿一-鿿豈-﫿＀-￯ᄀ-ᇿ㄰-㆏가-힯]")
_HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏가-힯]")


def has_cjk(text: str) -> bool:
    """True when ``text`` has a Japanese, Chinese or Korean character (Arial cannot set it)."""

    return bool(_CJK_CHARS.search(text))


def cjk_font_name(text: str, *, platform: str | None = None) -> str:
    """The face a line with CJK characters is set in, per line, instead of Arial.

    macOS ships Hiragino Sans (Japanese and Chinese) and Apple SD Gothic Neo
    (Korean); elsewhere the Noto Sans CJK families are the usual install.

    Parameters
    ----------
    text
        The caption text (Hangul picks the Korean face).
    platform
        ``sys.platform`` by default; tests pass one.

    Returns
    -------
    str
        A font family name for the ASS ``\\fn`` override.
    """

    korean = bool(_HANGUL.search(text))
    if (platform or sys.platform) == "darwin":
        return "Apple SD Gothic Neo" if korean else "Hiragino Sans"
    return "Noto Sans CJK KR" if korean else "Noto Sans CJK JP"


#: Em size the measuring face is loaded at; widths scale linearly from it.
_MEASURE_EM = 100


@functools.cache
def _measuring_font() -> Any:
    """Arial Bold when this laptop has it, else the bundled Poppins Bold (wider: wraps a little early)."""

    from PIL import ImageFont

    found = find_house_font().path
    path = found if found is not None else FONTS_DIR / "Poppins-Bold.ttf"
    return ImageFont.truetype(str(path), _MEASURE_EM)


def text_width(text: str, size: int) -> float:
    """Drawn width of ``text`` in Arial Bold at ASS ``Fontsize`` ``size``, in pixels.

    libass draws a ``Fontsize`` at :data:`HOUSE_EM_PER_SIZE` ems, so the width
    is measured at that em. A CJK character counts one em (a CJK face's full
    width); the rest is measured in Arial Bold (or the bundled Poppins Bold,
    which is wider, when Arial is missing).
    """

    em = size * HOUSE_EM_PER_SIZE
    cjk = len(_CJK_CHARS.findall(text))
    rest = _CJK_CHARS.sub("", text)
    measured = float(_measuring_font().getlength(rest)) if rest else 0.0
    return measured * em / _MEASURE_EM + cjk * em


def wrap_caption(
    text: str, size: int, width: int, *, room: float | None = None
) -> tuple[list[str], int]:
    """Lay one caption out on at most two balanced lines inside the side margins.

    A caption that fits on one line stays one line. One that does not is
    broken at the word (or, for text without spaces, the character) that
    makes the two lines closest in width; the top line is the shorter when
    two breaks tie. Only when the wider of the two lines still does not fit
    is the size lowered, never below 8.

    Parameters
    ----------
    text
        The caption text (one cue).
    size
        ASS ``Fontsize`` (:func:`house_font_size`).
    width
        Frame width in pixels.
    room
        Width the text may take; default the frame inside the side margins.

    Returns
    -------
    tuple[list[str], int]
        The lines (one or two) and the size to set them at.
    """

    if room is None:
        room = width - 2 * side_margin(width)
    if text_width(text, size) <= room:
        return [text], size
    spaced = " " in text.strip()
    tokens = text.split() if spaced else list(text.strip())
    if len(tokens) < 2:
        return [text], max(8, int(size * room / text_width(text, size)))
    joiner = " " if spaced else ""
    best: tuple[float, float, list[str]] | None = None
    for cut in range(1, len(tokens)):
        top, bottom = joiner.join(tokens[:cut]), joiner.join(tokens[cut:])
        top_w, bottom_w = text_width(top, size), text_width(bottom, size)
        key = (max(top_w, bottom_w), top_w)
        if best is None or key < best[:2]:
            best = (*key, [top, bottom])
    assert best is not None
    widest, _, lines = best
    if widest <= room:
        return lines, size
    return lines, max(8, int(size * room / widest))


def _caption_text(
    cue: Cue, size: int, width: int, *, platform: str | None, room: float | None = None
) -> str:
    """One cue's ASS text: wrapped (``\\N``), with ``\\fs`` only when it had to shrink, CJK face per line."""

    lines, fit = wrap_caption(cue.text, size, width, room=room)
    tags = ""
    if fit != size:
        tags += f"\\fs{italic_size(fit) if cue.italic else fit}"
    if has_cjk(cue.text):
        tags += f"\\fn{cjk_font_name(cue.text, platform=platform)}"
    prefix = f"{{{tags}}}" if tags else ""
    return prefix + "\\N".join(_ass_escape(line) for line in lines)


@dataclass(frozen=True)
class LetterboxBand:
    """Where a letterbox show's captions go on its 9:16 canvas (:mod:`creation.post.delivery_geometry`).

    ``top`` is the caption's top edge; a chunk that needs two lines moves up
    so its bottom stays above ``floor``, never above ``highest_top``. ``size``
    steps down to ``min_size`` first so a chunk fits one line.
    """

    left: int
    right: int
    top: int
    highest_top: int
    floor: int
    size: int
    min_size: int
    #: Size step when a chunk is too wide for one line.
    step: int = 2


def letterbox_band(width: int, height: int) -> LetterboxBand:
    """The caption band under the picture on a ``width`` x ``height`` letterbox canvas.

    The one place ``finish`` (:func:`caption_take`) and ``reel`` read it from
    (:mod:`creation.post.delivery_geometry`).
    """

    from creation.post.delivery_geometry import layout as letterbox_layout

    place = letterbox_layout(width, height)
    return LetterboxBand(
        place.caption.x, place.caption.right, place.caption.y,
        place.caption_highest_top, place.caption.bottom,
        place.caption_size, place.caption_min_size,
    )  # fmt: skip


def letterbox_centre(widest: float, band: LetterboxBand, width: int) -> int:
    """Where a letterbox caption ``widest`` px wide is centred (user decision 2026-10-06).

    On the frame's centre (x 540, like "Not Home") whenever its right edge then
    stays at or left of ``band.right`` (x 950, clear of the right-hand
    engagement buttons); else shifted left just enough that its right edge
    sits on ``band.right``, its left edge never left of ``band.left``.
    """

    centre = width / 2
    if centre + widest / 2 > band.right:
        centre = max(band.left + widest / 2, band.right - widest / 2)
    return round(centre)


def letterbox_caption_place(
    text: str, band: LetterboxBand, width: int
) -> tuple[list[str], int, int, int]:
    """Lay one letterbox caption out: ``(lines, size, top, centre_x)``.

    One line at ``band.size`` when it fits between the margins; else the
    largest size down to ``band.min_size`` (in ``band.step`` px) that fits one
    line; else two balanced lines (:func:`wrap_caption`, shrunk only when even
    two lines are too wide), moved up so the block ends above ``band.floor``,
    never higher than ``band.highest_top``. Centred by :func:`letterbox_centre`.
    Widths are Arial Bold's own advances (the server's table,
    :func:`creation.post.delivery_geometry.arial_bold_width`), so the place is
    the server's whatever font file this machine has.
    """

    from creation.post.delivery_geometry import arial_bold_width

    room = band.right - band.left
    size = band.size
    while size >= band.min_size:
        widest = arial_bold_width(text, size)
        if widest <= room:
            return [text], size, band.top, letterbox_centre(widest, band, width)
        size -= band.step
    lines, fit = wrap_caption(text, band.size, width, room=room)
    height = len(lines) * fit
    top = max(band.highest_top, min(band.top, band.floor - height))
    widest = max(arial_bold_width(line, fit) for line in lines)
    return lines, fit, top, letterbox_centre(widest, band, width)


def _letterbox_caption_text(
    cue: Cue, band: LetterboxBand, width: int, *, platform: str | None
) -> str:
    lines, fit, top, centre = letterbox_caption_place(cue.text, band, width)
    tags = f"\\an8\\pos({centre},{top})"
    if fit != band.size:
        tags += f"\\fs{italic_size(fit) if cue.italic else fit}"
    if has_cjk(cue.text):
        tags += f"\\fn{cjk_font_name(cue.text, platform=platform)}"
    return f"{{{tags}}}" + "\\N".join(_ass_escape(line) for line in lines)


def build_ass(
    cues: Sequence[Cue],
    *,
    width: int,
    height: int,
    style: str = DEFAULT_CAPTION_STYLE,
    platform: str | None = None,
    band: LetterboxBand | None = None,
    colour: str | None = None,
) -> str:
    """Render the caption ASS for a frame of ``width`` x ``height``.

    The house style (runbook rule 1) is Arial Bold 64 on a 1920-high canvas,
    yellow, outline 5; every number is scaled to this frame by height
    (:func:`house_font_size`). Two styles: ``House`` (Arial Bold; ``Plain``
    and white for ``style="plain"``) and ``Italic`` (Georgia italic, not
    bold; same drawn size, colour, edge, shadow and place) for a cue with
    ``italic``. The italic ``Fontsize`` is :func:`italic_size`, so both
    faces draw at the same em.

    No cue draws an em or en dash: a cut-off ("Please—") shows "Please…",
    a dash between words a comma (:func:`creation.caption_dashes.caption_text`).

    A cue too wide for one line is wrapped into two balanced lines
    (:func:`wrap_caption`; ``WrapStyle: 2`` so libass adds no breaks of its
    own). The block's bottom edge sits at 62%, inside the 55-70% band. A
    cue with CJK characters is set in a CJK face (:func:`cjk_font_name`).

    Parameters
    ----------
    cues
        The cues to draw.
    width, height
        The take's frame size.
    style
        ``house`` (``subtle``), ``plain`` or ``bold`` (``none`` draws nothing
        and is never rendered). ``bold`` is drawn by
        :func:`creation.caption_bold.build_bold_ass` (its own size, place and
        colours; ``band`` and ``colour`` do not apply).
    platform
        ``sys.platform`` by default (picks the CJK face); tests pass one.
    band
        A letterbox show's caption band under the picture
        (:func:`letterbox_caption_place`: its own size, one line preferred,
        each cue placed with ``\\pos``); ``None`` is the house band.
    colour
        The text colour (``&HAABBGGRR``); ``None`` is the style's own.

    Returns
    -------
    str
        The ASS file text.

    Raises
    ------
    ValueError
        For a style other than ``house`` or ``plain``.
    """

    style = canonical_caption_style(style)
    if style == "bold" and band is None:
        from creation.caption_bold import build_bold_ass

        return build_bold_ass(cues, width=width, height=height, platform=platform)
    if style == "bold":
        style = "house"  # a letterbox band keeps its own layout
    if style not in ("house", "plain"):
        raise ValueError(
            f"caption style {style!r} draws no captions; use bold, subtle (house) or plain"
        )
    # No em or en dash on the picture (6 Oct 2026): every cue, after timing (creation.caption_dashes).
    cues = [replace(c, text=no_dash_text(c.text)) for c in cues]
    scale = height / HOUSE_CANVAS_HEIGHT
    size = house_font_size(height)
    margin_v = caption_margin_v(height)
    margin_x = side_margin(width)
    margin_l, margin_r = margin_x, margin_x
    if band is not None:
        size = band.size
    outline = max(1, round(HOUSE_OUTLINE * scale))
    shadow = max(1, round(HOUSE_SHADOW * scale))
    colour = colour or (PLAIN_COLOUR if style == "plain" else PRIMARY_COLOUR)
    main = "Plain" if style == "plain" else "House"
    tail = f"{OUTLINE_COLOUR},{SHADOW_COLOUR}"
    place = f"100,100,0,0,1,{outline},{shadow},2,{margin_l},{margin_r},{margin_v},1"
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
        f"Style: {main},{FONT_NAME},{size},{colour},{colour},{tail},-1,0,0,0,{place}\n"
        f"Style: Italic,{ITALIC_FONT_NAME},{italic_size(size)},{colour},{colour},{tail},0,-1,0,0,{place}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = "".join(
        f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},{'Italic' if c.italic else main},,0,0,0,,"
        + (
            _letterbox_caption_text(c, band, width, platform=platform)
            if band is not None
            else _caption_text(c, size, width, platform=platform)
        )
        + "\n"
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
            [ffmpeg, "-hide_banner", "-filters"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
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


def windows_font_dirs() -> tuple[Path, ...]:
    """Where Windows keeps fonts: the system folder (``%WINDIR%\\Fonts``), then per-user installs."""

    dirs = [Path(os.environ.get("WINDIR") or "C:/Windows") / "Fonts"]
    if os.environ.get("LOCALAPPDATA"):
        dirs.append(
            Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts"
        )
    return tuple(dirs)


def system_font_dirs(platform: str) -> tuple[Path, ...]:
    """Font folders searched by file name after the bundled dir (Linux asks fontconfig instead)."""

    if platform == "darwin":
        return MAC_FONT_DIRS
    if platform.startswith("win"):
        return windows_font_dirs()
    return ()


#: Georgia Italic's file name: macOS, then the Microsoft core fonts package on Linux.
ITALIC_FONT_FILES: tuple[str, ...] = ("Georgia Italic.ttf", "georgiai.ttf")
#: Georgia regular's file name (libass slants it when there is no italic face).
REGULAR_FONT_FILES: tuple[str, ...] = ("Georgia.ttf", "georgia.ttf")
GEORGIA_INSTALL_HINT = (
    "install Georgia: macOS ships it in /System/Library/Fonts/Supplemental "
    "(Font Book > File > Restore Standard Fonts brings it back); Linux: "
    "sudo apt install ttf-mscorefonts-installer, or copy Georgia.ttf and "
    "'Georgia Italic.ttf' into ~/.local/share/fonts and run fc-cache -f; Windows: "
    "it ships as C:\\Windows\\Fonts\\georgiai.ttf (Settings > Personalization > Fonts "
    "to check; right-click a copied georgiai.ttf > Install brings it back)"
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
            stdin=subprocess.DEVNULL,
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
    (the standard font folders, :data:`MAC_FONT_DIRS`), DirectWrite on Windows
    (:func:`windows_font_dirs`) and fontconfig on Linux
    (``fc-match Georgia:italic``).

    Parameters
    ----------
    platform
        ``sys.platform`` by default; tests pass one.
    font_dirs
        Folders searched by file name (default: the bundled fonts dir, plus
        :func:`system_font_dirs`).
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
        font_dirs = (FONTS_DIR, *system_font_dirs(platform))
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


#: Arial Bold's file name: macOS, then the Microsoft core fonts package on Linux (and Windows).
HOUSE_FONT_FILES: tuple[str, ...] = ("Arial Bold.ttf", "Arial_Bold.ttf", "arialbd.ttf")
ARIAL_INSTALL_HINT = (
    "install Arial: macOS ships it in /System/Library/Fonts/Supplemental "
    "(Font Book > File > Restore Standard Fonts brings it back); Linux: "
    "sudo apt install ttf-mscorefonts-installer, or copy 'Arial Bold.ttf' "
    "into ~/.local/share/fonts and run fc-cache -f; Windows: it ships as "
    "C:\\Windows\\Fonts\\arialbd.ttf (Settings > Personalization > Fonts to check; "
    "right-click a copied arialbd.ttf > Install brings it back)"
)


@dataclass(frozen=True)
class HouseFont:
    """Where the house caption face (Arial Bold) resolves on this machine.

    Parameters
    ----------
    path
        The Arial Bold file libass will draw with, or None.
    fallback
        The face libass would use instead, as fontconfig names it, or None
        when that cannot be told.
    """

    path: Path | None
    fallback: str | None = None

    @property
    def ok(self) -> bool:
        """Arial Bold itself is installed."""

        return self.path is not None

    def warning(self) -> str | None:
        """The operator warning when captions will not be set in Arial Bold, else None."""

        if self.path is not None:
            return None
        instead = self.fallback or "whatever face libass falls back to"
        return (
            f"Arial Bold not found; captions will fall back to {instead} (house style is "
            f"Arial Bold). To fix, {ARIAL_INSTALL_HINT}"
        )


def find_house_font(
    *,
    platform: str | None = None,
    font_dirs: Sequence[Path] | None = None,
    match: FcMatch | None = None,
) -> HouseFont:
    """Resolve Arial Bold the way libass does when captions are burned.

    Same search as :func:`find_italic_font`: the bundled fonts dir, then the
    macOS font folders (:data:`MAC_FONT_DIRS`) or the Windows ones
    (:func:`windows_font_dirs`, ``arialbd.ttf``), then ``fc-match Arial:bold``
    on Linux.

    Parameters
    ----------
    platform
        ``sys.platform`` by default; tests pass one.
    font_dirs
        Folders searched by file name.
    match
        fontconfig lookup (default :func:`fc_match`); tests pass a fake.

    Returns
    -------
    HouseFont
        What was found; ``warning()`` says what to do when it is not Arial Bold.
    """

    platform = platform or sys.platform
    match = match or fc_match
    if font_dirs is None:
        font_dirs = (FONTS_DIR, *system_font_dirs(platform))
    found = _first_file(font_dirs, HOUSE_FONT_FILES)
    if found is not None:
        return HouseFont(found)
    answer = match(f"{FONT_NAME}:bold")
    if answer is None:
        return HouseFont(None)
    family, style, file = answer
    if FONT_NAME.lower() in family.lower().split(",")[0] and platform != "darwin":
        if "bold" in style.lower():
            return HouseFont(Path(file))
    return HouseFont(None, f"{family} ({file})")


def house_font_warning(cues: Sequence[Cue]) -> str:
    """Warn (on stderr, and returned) when the house cues will not be set in Arial Bold.

    Parameters
    ----------
    cues
        The cues about to be burned; nothing is checked when every one is italic.

    Returns
    -------
    str
        ``FONT: …`` warning, or ``""``.
    """

    if all(cue.italic for cue in cues):
        return ""
    problem = find_house_font().warning()
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
        stdin=subprocess.DEVNULL,
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
        stdin=subprocess.DEVNULL,
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
        stdin=subprocess.DEVNULL,
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
    #: Per line: what timed it (``words``, ``speech``, ``manual``, or a mix; see :class:`LineTiming`).
    methods: tuple[str, ...] = ()
    #: Per line: when it is on screen (first cue start to last cue end); None for a line not drawn.
    shown: tuple[Span | None, ...] = ()
    #: The transcript the lines were timed on, when one was used.
    words_json: Path | None = None
    #: ``TAKE LINES: …`` when a take was captioned with every line of the episode
    #: because its beats could not be told apart (also printed on stderr), else "".
    take_lines_warning: str = ""
    #: ``EXTRA SPEECH: …`` when lines were timed on speech stretches and some were left over
    #: (also printed on stderr), else "".
    timing_warning: str = ""

    def timing_lines(self) -> list[str]:
        """One ``on screen a-b s 'line' (method)`` entry per line, for the report and run notes."""

        rows: list[str] = []
        for i, line in enumerate(self.lines):
            shown = self.shown[i] if i < len(self.shown) else None
            span = shown or self.anchors[i]
            how = self.methods[i] if i < len(self.methods) else "speech"
            rows.append(f"{span.start:.2f}-{span.end:.2f}s {line!r} ({how})")
        return rows


#: ``(desk, episode) -> the spine as the server holds it now`` (saved on the desk), or None.
SpineFetcher = Callable[[Path, int], "dict[str, Any] | None"]


def api_spine_fetcher(desk: Path, episode: int) -> dict[str, Any] | None:
    """Read the spine from the server and save it on the desk (``api/spine.json``, ``epNN/api/spine.json``).

    A free read (``GET /v1/spines/{id}``). ``None`` when the desk has no spine id yet.
    """

    from creation.post.desk import open_api, refresh_spine, spine_id

    try:
        spine_id(desk)
    except ValueError:
        return None
    run = open_api(desk, episode)
    try:
        return refresh_spine(run, desk, episode)
    finally:
        run.client.close()


def current_spine(
    desk: Path, episode: int, *, fetch: SpineFetcher | None = None
) -> tuple[dict[str, Any] | None, str]:
    """The spine to caption from: the server's, else the desk's copy with a loud note.

    Captions come from the lines the story has now (L-20261001-8: a line
    deleted before filming was burned from a stale desk copy). The desk copy
    is used only when the server cannot be read, and the note says so.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    fetch
        Server read (default :func:`api_spine_fetcher`); tests pass a fake.

    Returns
    -------
    tuple[dict | None, str]
        The spine (``None``: let :func:`caption_take` read the desk), and
        ``""`` or a ``!! captions from the desk's copy …`` note.
    """

    import httpx

    from creation.post.desk import saved_spine

    desk = desk.expanduser().resolve()
    try:
        fresh = (fetch or api_spine_fetcher)(desk, episode)
    except (
        OSError, RuntimeError, ValueError, KeyError, SystemExit, httpx.HTTPError,
    ) as exc:  # fmt: skip
        detail = str(exc.code) if isinstance(exc, SystemExit) else str(exc)
        found = saved_spine(desk, episode)
        where = f"`{found[1].name}`" if found else "on the desk"
        return (found[0] if found else None), (
            f"!! captions from the desk's copy {where}: the current spine could not be read "
            f"({type(exc).__name__}: {detail[:160]}); a line changed or deleted since that copy "
            "was saved is captioned as it was. Run `spine --refresh` and finish again"
        )
    return (dict(fresh) if fresh else None), ""


def _plain_words(text: str) -> str:
    """Lower-case words only: ``Not tonight!`` and ``not tonight.`` are the same line."""

    return " ".join(re.findall(r"[\w']+", text.casefold()))


@under_desk_rules
def caption_take(
    desk: Path,
    *,
    episode_ordinal: int = 1,
    take: Path | None = None,
    line_starts: Sequence[float] | None = None,
    line_ends: Sequence[float] | None = None,
    words_json: Path | None = None,
    timing_source: Path | None = None,
    stem: str | None = None,
    fixed_lines: Sequence[tuple[CaptionLine, Span]] = (),
    take_index: int | None = None,
    line_spans: Mapping[str, Span] | None = None,
    style: str = DEFAULT_CAPTION_STYLE,
    spine: Mapping[str, Any] | None = None,
    laid_lines: Sequence[tuple[CaptionLine, Span]] = (),
    layout: str = "portrait",
    caption_colour: str | None = None,
    italic_overrides: Mapping[str, bool] | None = None,
    line_spans_after_words: bool = False,
    voice_floors: Mapping[str, float] | None = None,
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
    line_ends
        Manual end time per line (the caption goes off exactly there).
    words_json
        A saved transcript of this take (``/v1/transcripts`` words), on any
        show: each line is timed on its matched words, a line not matched
        falls back to its speech span. An English show still flickers word by
        word, inside the span its words were heard in (L-20260930-6: speech
        spans alone put every line after a stray stretch of speech on the
        wrong words).
    timing_source
        File whose speech is detected (default ``take``). ``finish`` passes the
        take before the music bed, since silence cannot be found under music.
    stem
        Output name stem (``take-ep01-t1-cap`` writes ``take-ep01-t1-cap-vN.mp4``
        and ``.ass``); default ``<take>-house`` / ``<take>-captioned``.
    fixed_lines
        Lines placed where they were laid, not found on the take: ``finish``'s
        inner-voice lines (a character's thoughts), each with the span its dry
        line plays. They follow the script lines in the result, are drawn like
        them (flicker, or whole lines on a show not spoken in English; heard, not
        seen, so Georgia italic when ``italic``) and are left uncaptioned with
        ``NOT ENGLISH`` when not English. Their method is ``laid``.
    take_index
        The take ``take`` is (``t2`` is 2): only the lines of the beats that take
        plays are captioned (:func:`take_caption_lines`, split as the board
        splits them). ``None`` with ``take`` given captions every line of the
        episode (a joined episode file); with no ``take`` the newest ``t1`` raw
        file is captioned as take 1.
    line_spans
        ``line_id`` to the window the line plays in, when known exactly (a
        locked-voice take's ``soundtrack.lines``): those lines are timed on it
        (method ``lines``); any other line is timed as usual.
    line_spans_after_words
        With ``words_json``: a line the transcript heard is timed on its
        words, and on its ``line_spans`` window only when it was not heard
        (``finish`` on a locked-voice take of a new desk, L-20261005-6).
    voice_floors
        ``line_id`` to where its voice was measured to start on the take: no
        automatically timed caption of that line goes up before it.
    style
        ``house`` (yellow; word flicker on a show spoken in English) or
        ``plain`` (white whole lines on any show). ``none`` is the caller's to
        skip: it is refused here.
    spine
        The spine to caption from (``finish`` passes the current one, read from
        the server). ``None`` reads the newest snapshot on the desk that has
        the episode's lines.
    laid_lines
        Lines laid by hand (``finish --voice FILE@S``), each with the span it
        plays. One whose words are one of the take's script lines is that
        line (a replacement read): it is captioned once, as the script line,
        timed on the take. Any other (narration, a line not in the script) is
        captioned where it is laid, like ``fixed_lines``, in Georgia italic
        when ``italic``, and its window is left out of the take's speech
        stretches so the script lines stay on their own speech.
    layout
        ``portrait`` (the house band, three-word flicker: unchanged) or
        ``letterbox``: ``take`` is a letterbox show's 9:16 canvas, captioned in
        the band under the picture (:mod:`creation.post.delivery_geometry`),
        phrase chunking (:func:`phrase_cues`).
    caption_colour
        Letterbox only: ``yellow`` (default) or ``white``.
    italic_overrides
        ``line_id`` to italic or not, decided by the caller from what the take
        draws (a stale ``off_screen`` flag on a speaker the take shows). Lines
        not named keep the spine's flag.

    Returns
    -------
    CaptionResult
        Versioned ASS + captioned MP4 under ``takes/``. A line that is not
        English is timed but not captioned; ``not_english`` says which.
    """

    style = canonical_caption_style(style)
    if style not in ("bold", "house", "plain"):
        raise ValueError(
            f"caption style {style!r} burns no captions; use bold, subtle (house) or plain "
            f"(choices: {', '.join(CAPTION_STYLES)})"
        )
    if layout not in ("portrait", "letterbox"):
        raise ValueError(f"caption layout {layout!r}: use portrait or letterbox")
    letterbox = layout == "letterbox"
    if letterbox and style == "bold":
        # A letterbox show keeps its own caption layout under the picture (6 Oct 2026).
        style = "house"
    chunking = "phrase" if letterbox else "bold" if style == "bold" else "three"
    ep_dir = desk.expanduser().resolve() / f"ep{episode_ordinal:02d}"
    takes = ep_dir / "takes"
    if take is None:
        take = latest_file(takes, f"take-ep{episode_ordinal:02d}-t1-raw-v*.mp4")
        take_index = 1 if take_index is None else take_index
    if take is None or not take.is_file():
        raise FileNotFoundError(
            f"no raw take in {takes}; run `fictora-produce step --confirm-spend` first"
        )
    api = ep_dir / "api"
    # Newest snapshot that carries beats (approve responses are receipts without them).
    caption_lines: list[CaptionLine] = []
    whole_lines = False
    take_lines_warning = ""
    take_count = (
        desk_take_count(desk.expanduser().resolve(), episode_ordinal)
        if take_index is not None
        else None
    )
    candidates: list[dict[str, Any]] = (
        [dict(spine)]
        if spine is not None
        else [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(
                api.glob("*spine*.json"), key=lambda p: p.name, reverse=True
            )
        ]
    )
    for body in candidates:
        if not isinstance(body, dict) or not episode_caption_lines(
            body, episode_ordinal
        ):
            continue
        # Newest snapshot with the episode's lines; a take gets only its own beats' lines.
        # ``plain`` is whole lines on any show; ``house`` flickers word by word on an English one.
        whole_lines = style == "plain" or captions_whole_lines(body)
        if take_index is None:
            caption_lines = episode_caption_lines(body, episode_ordinal)
        else:
            caption_lines, take_lines_warning = take_caption_lines(
                body, episode_ordinal, take_index=take_index, take_count=take_count
            )
            if take_lines_warning:
                print(f"WARNING {take_lines_warning}", file=sys.stderr)
        break
    lines = [line.text for line in caption_lines]
    # A hand-laid line that reads one of the take's script lines is that line; any other is laid like a thought.
    script_words = {
        _plain_words(text)
        for line in caption_lines
        for text in (line.text, line.performed, *line.spellings)
        if text
    }
    extra_laid = [
        (line, span)
        for line, span in laid_lines
        if _plain_words(line.text) not in script_words
    ]
    quiet = [(span.start, span.end) for _, span in extra_laid]
    fixed = sorted([*fixed_lines, *extra_laid], key=lambda item: item[1].start)
    if not lines and not fixed:
        scope = (
            f"t{take_index} of episode {episode_ordinal}"
            if take_index is not None
            else f"episode {episode_ordinal}"
        )
        raise ValueError(
            f"{scope} has no dialogue lines in any spine snapshot in {api}"
        )

    ffmpeg, ffprobe = find_ffmpeg()
    width, height, duration = probe_video(ffprobe, take)
    source = timing_source or take
    # Word timing is for whole English lines over other-language speech; English flicker keeps speech spans.
    from creation.post.whisper import load_words

    words = load_words(words_json) if words_json is not None else None
    timing = (
        time_lines(
            caption_lines,
            duration=duration,
            line_starts=line_starts,
            line_ends=line_ends,
            words=words,
            spans=lambda: without_windows(
                speech_spans(detect_silences(ffmpeg, source, duration), duration),
                quiet,
            ),
            known=[(line_spans or {}).get(line.line_id) for line in caption_lines]
            if line_spans
            else None,
            # Each word when it is said (6 Oct 2026); a legacy desk spreads them as before,
            # except from its Group B episode on (``per_word_captions``, founder 7 Oct 2026).
            per_word=not whole_lines and _per_word_desk(desk, episode_ordinal),
            known_after_words=line_spans_after_words,
            floors=[(voice_floors or {}).get(line.line_id) for line in caption_lines]
            if voice_floors
            else None,
        )
        if caption_lines
        else LineTiming((), (), (), ())
    )
    anchors = list(timing.anchors)
    timing_warning = "; ".join(timing.warnings)
    if timing_warning:
        print(f"WARNING {timing_warning}", file=sys.stderr)

    if italic_overrides:
        caption_lines = [
            replace(line, italic=italic_overrides[line.line_id])
            if line.line_id in italic_overrides
            else line
            for line in caption_lines
        ]
    italic = tuple(line.italic for line in caption_lines)
    skip = [not line.english for line in caption_lines]
    # Every line keeps its speech span (timing); only English lines are drawn.
    per_line = build_line_cues(
        lines,
        anchors,
        whole_lines=whole_lines,
        italic=italic,
        skip=skip,
        holds=timing.holds,
        fixed_ends=timing.fixed_ends,
        chunking=chunking,
        word_cues=timing.word_cues,
        emphasis=[line.emphasis for line in caption_lines],
    )
    if timing.wording:
        wording = "WORDING: captioned as said, not as written: " + "; ".join(
            timing.wording
        )
        print(f"WARNING {wording}", file=sys.stderr)
        timing_warning = f"{timing_warning}; {wording}" if timing_warning else wording
    methods = tuple(timing.methods)
    if fixed:
        # Laid lines (inner voice) sit where their dry line plays; they bound each other's hold, not the script's.
        per_line += build_line_cues(
            [line.text for line, _ in fixed],
            [span for _, span in fixed],
            whole_lines=whole_lines,
            italic=[line.italic for line, _ in fixed],
            skip=[not line.english for line, _ in fixed],
            chunking=chunking,
            emphasis=[line.emphasis for line, _ in fixed],
        )
        caption_lines = [*caption_lines, *(line for line, _ in fixed)]
        lines = [line.text for line in caption_lines]
        anchors += [span for _, span in fixed]
        italic = tuple(line.italic for line in caption_lines)
        methods += tuple("laid" for _ in fixed)
    cues = sorted(
        (cue for group in per_line for cue in group), key=lambda cue: cue.start
    )
    not_english = tuple(
        not_english_warning(line) for line in caption_lines if not line.english
    )
    base = take.stem.replace("-raw", "").rsplit("-v", 1)[0]
    ass = next_versioned_path(takes, stem or f"{base}-house", ".ass")
    if letterbox:
        from creation.post.delivery_geometry import caption_colour_code

        text = build_ass(
            cues, width=width, height=height, style=style,
            band=letterbox_band(width, height),
            colour=caption_colour_code(caption_colour)
            if caption_colour or style != "plain"
            else None,
        )  # fmt: skip
    else:
        text = build_ass(cues, width=width, height=height, style=style)
    ass.write_text(text, encoding="utf-8")
    video = next_versioned_path(takes, stem or f"{base}-captioned", ".mp4")
    font_warning = "; ".join(
        w for w in (house_font_warning(cues), italic_font_warning(cues)) if w
    )
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
        methods,
        tuple(
            Span(group[0].start, group[-1].end) if group else None for group in per_line
        ),
        words_json if words is not None else None,
        take_lines_warning,
        timing_warning,
    )
