"""Background shouts (``DramaBeat.crowd_lines``): how the kit prints them, finds them and keeps them apart.

fictora-drama #682 (founder, 8 Oct 2026): unnamed background people may shout
one short line (a soldier's "Charge!", kids' "Again!"). A shout is at most six
words, at most two per beat, and is spoken in a generic crowd voice. It is
never a character: no cast card, no voice to approve, never counted as a
speaker. The server keeps shouts off ``dialogue_lines`` and so does the kit:
every cast, voice and speaker path keeps reading ``dialogue_lines`` only, and
the views that should show a shout (the script gate, ``line`` listings, the
line check) ask this module.

Pure functions over ``GET /v1/spines/{id}`` JSON; nothing here calls the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

#: Most words a shout may carry (the server's ``MAX_CROWD_LINE_WORDS``).
MAX_SHOUT_WORDS = 6
#: The cast id a shout carries on a track or soundtrack line (``crowd:<line id>``); never a cast member's.
CROWD_CAST_ID_PREFIX = "crowd:"
#: How a shout is marked wherever the kit prints it.
BACKGROUND_TAG = "[background]"

_IRREGULAR_PLURALS = {
    "child": "children",
    "man": "men",
    "woman": "women",
    "person": "people",
    "kid": "kids",
    "gentleman": "gentlemen",
}


@dataclass(frozen=True)
class BackgroundShout:
    """One background shout on a beat.

    Parameters
    ----------
    line_id
        ``crowd_episode_01_03_1``.
    label
        Who shouts it, as written (``soldier``).
    count
        How many people shout it (1-12).
    text
        The English shout.
    spoken
        The performed shout on a show not spoken in English (empty otherwise).
    subtitle
        Its English subtitle (empty otherwise).
    before_line
        Plays before the beat's character line (else after it).
    beat_ordinal
        The beat's number in the episode.
    """

    line_id: str
    label: str
    count: int
    text: str
    spoken: str = ""
    subtitle: str = ""
    before_line: bool = False
    beat_ordinal: int = 0

    @property
    def who(self) -> str:
        """``Soldier``, or ``Soldiers ×3`` when several shout it."""

        return shout_label(self.label, self.count)

    @property
    def performed(self) -> str:
        """What is heard: the performed shout, else the English."""

        return self.spoken or self.text

    def row(self) -> str:
        """``[background] Soldier: "Charge!"``; on a localized show ``"돌격!"  (Charge!)``."""

        gloss = self.subtitle or (self.text if self.spoken else "")
        tail = f"  ({gloss})" if gloss and gloss != self.performed else ""
        return f'{BACKGROUND_TAG} {self.who}: "{self.performed}"{tail}'


def _plural(label: str) -> str:
    words = label.split()
    last = words[-1]
    lower = last.casefold()
    if lower in _IRREGULAR_PLURALS:
        plural = _IRREGULAR_PLURALS[lower]
    elif lower.endswith(("s", "x", "z", "ch", "sh")):
        plural = f"{last}es"
    elif lower.endswith("y") and len(lower) > 1 and lower[-2] not in "aeiou":
        plural = f"{last[:-1]}ies"
    else:
        plural = f"{last}s"
    return " ".join([*words[:-1], plural])


def shout_label(label: str, count: int = 1) -> str:
    """Name who shouts, as the server's captions do, with the head count when several shout.

    Parameters
    ----------
    label
        The crowd speaker's label (``soldier``).
    count
        How many shout it.

    Returns
    -------
    str
        ``Soldier``; ``Soldiers ×3``.
    """

    text = " ".join(str(label or "").split()) or "someone"
    if count > 1:
        text = _plural(text)
    text = f"{text[:1].upper()}{text[1:]}"
    return f"{text} ×{count}" if count > 1 else text


def beat_shouts(beat: Mapping[str, Any]) -> list[BackgroundShout]:
    """A beat's background shouts, in the order the spine holds them (none on an older spine).

    Parameters
    ----------
    beat
        One beat.

    Returns
    -------
    list[BackgroundShout]
        The shouts; empty when the beat has none.
    """

    out: list[BackgroundShout] = []
    for raw in beat.get("crowd_lines") or []:
        if not isinstance(raw, Mapping) or not raw.get("line_id"):
            continue
        speaker = raw.get("speaker") if isinstance(raw.get("speaker"), Mapping) else {}
        try:
            count = int(speaker.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        out.append(
            BackgroundShout(
                line_id=str(raw["line_id"]),
                label=str(speaker.get("label") or ""),
                count=max(1, count),
                text=str(raw.get("text") or "").strip(),
                spoken=str(raw.get("spoken_text") or "").strip(),
                subtitle=str(raw.get("subtitle_text") or "").strip(),
                before_line=raw.get("order") == "before_line",
                beat_ordinal=int(beat.get("ordinal") or 0),
            )
        )
    return out


def beat_rows(beat: Mapping[str, Any], character_rows: Sequence[str]) -> list[str]:
    """A beat's printed lines in the order they play: shouts marked ``before_line``, the character line, the rest.

    Parameters
    ----------
    beat
        One beat.
    character_rows
        The beat's character line rows, already formatted by the caller.

    Returns
    -------
    list[str]
        The rows, shouts as :meth:`BackgroundShout.row` (no indent).
    """

    shouts = beat_shouts(beat)
    before = [shout.row() for shout in shouts if shout.before_line]
    after = [shout.row() for shout in shouts if not shout.before_line]
    return [*before, *character_rows, *after]


def _episode_beats(spine: Mapping[str, Any], episode: int) -> list[Mapping[str, Any]]:
    from creation.spine_view import episode_id_for

    episode_id = episode_id_for(spine, episode)
    beats = [
        beat
        for beat in spine.get("beats") or []
        if isinstance(beat, Mapping) and beat.get("episode_id") == episode_id
    ]
    beats.sort(key=lambda beat: int(beat.get("ordinal") or 0))
    return beats


def episode_shouts(spine: Mapping[str, Any], *, episode: int) -> list[BackgroundShout]:
    """An episode's background shouts in script order; shout N of the episode is item N-1.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[BackgroundShout]
        Every shout of the episode.
    """

    return [
        shout for beat in _episode_beats(spine, episode) for shout in beat_shouts(beat)
    ]


def shout_ids(spine: Mapping[str, Any]) -> set[str]:
    """Every background shout id on the spine (all episodes).

    Parameters
    ----------
    spine
        Spine JSON.

    Returns
    -------
    set[str]
        The ids.
    """

    return {
        shout.line_id
        for beat in spine.get("beats") or []
        if isinstance(beat, Mapping)
        for shout in beat_shouts(beat)
    }


def is_crowd_cast_id(cast_id: Any) -> bool:
    """Whether a track or soundtrack line's ``cast_id`` is a background shout's (``crowd:<line id>``).

    Parameters
    ----------
    cast_id
        A line's cast id.

    Returns
    -------
    bool
        ``True`` for a shout: never look it up in the cast.
    """

    return str(cast_id or "").startswith(CROWD_CAST_ID_PREFIX)


def shout_listing(spine: Mapping[str, Any], *, episode: int) -> list[str]:
    """Number an episode's shouts for ``line --shout N``: number, id, beat, who, words.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[str]
        One printable row per shout; empty when the episode has none.
    """

    return [
        f"  s{number}. {shout.line_id}  beat {shout.beat_ordinal}  {shout.row()}"
        for number, shout in enumerate(episode_shouts(spine, episode=episode), start=1)
    ]


def take_shouts(
    spine: Mapping[str, Any], *, episode: int, take_index: int, take_count: int
) -> list[BackgroundShout]:
    """The background shouts on one take's beats, in the order they play.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    take_index
        1-based take index.
    take_count
        Takes on the episode.

    Returns
    -------
    list[BackgroundShout]
        The take's shouts.
    """

    from creation.spine_view import beats_by_take

    grouped = beats_by_take(spine, episode=episode, take_count=take_count)
    if not 1 <= take_index <= len(grouped):
        return []
    return [shout for beat in grouped[take_index - 1] for shout in beat_shouts(beat)]


__all__ = [
    "BACKGROUND_TAG",
    "BackgroundShout",
    "CROWD_CAST_ID_PREFIX",
    "MAX_SHOUT_WORDS",
    "beat_rows",
    "beat_shouts",
    "episode_shouts",
    "is_crowd_cast_id",
    "shout_ids",
    "shout_label",
    "shout_listing",
    "take_shouts",
]
