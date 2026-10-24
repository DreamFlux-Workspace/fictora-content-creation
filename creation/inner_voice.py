"""A character's own thoughts: the episode's inner-voice cues (``PUT /v1/spines/{id}/episodes/{n}/inner-voice``).

Contract, as the deployed ``/openapi.json`` lists it (read 2026-09-30):

- request ``DramaInnerVoiceReplaceRequest`` ``{spine_version, episode_ordinal, cues}``
  (no other field), each cue ``DramaInnerVoiceCue``
  ``{cue_id, start_ms >= 0, end_ms >= 1, speaker_cast_id, line}`` (no other field);
- answer ``DramaSpineResponse``; the episode's cues come back on
  ``episode_summaries[].inner_voice``;
- the PUT replaces the episode's whole list, so the kit reads the list, changes
  one cue and sends it all back.

A cue is spoken by someone already in the cast (the character we watch), costs
no cast place, and is never compiled into the take prompt: the server says post
lays it dry on the timeline. The kit's ``finish`` does that per take.

Cue times count from the start of the episode as filmed; the contract names no
take. Which take a cue plays on, and where, is the server's answer (24 Oct
2026, :mod:`creation.post.thought_grey`): the kit sends each take's real length
and the server places the cue the way its own join does. The kit keeps no
placement rule of its own.

Spoken words and caption, apart (a Japanese thought under an English caption).
The server's cue holds both (drama #1017, 23 Oct 2026): ``line`` is the
CAPTION (``--text``) and ``spoken_text`` the words the voice says
(``--spoken-text``). The kit no longer keeps them on the desk. A desk made
before keeps its ``epNN/inner-voice-spoken.json`` (:func:`load_spoken`), read
only for a cue the server holds no ``spoken_text`` for, keyed by cue id and
the caption they go with, so a cue whose caption changed never picks up stale
words (:func:`spoken_for`).

How a thought is acted (its ``acting_direction``, "soft, breaking") is the
server's too: it carries the direction from the script line the thought came
from, keeps it on every later PUT, and voices the thought with it when the kit
names the cue (``voice-lines`` with ``episode_id`` and ``line_id``). The kit
sends no delivery of its own (``finish``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from creation.spine_view import episode_id_for

#: A thought with no ``--until`` lasts about this long a word (and at least :data:`MIN_SECONDS`).
SECONDS_PER_WORD = 0.4
MIN_SECONDS = 1.2


#: The cue's optional fields the server keeps (``DramaInnerVoiceCue``): the words said, their caption and
#: how the thought is acted (drama #1017). Sent back as the server gave them; never invented by the kit.
OPTIONAL_CUE_FIELDS = ("spoken_text", "subtitle_text", "acting_direction")


class InnerVoiceError(ValueError):
    """A cue the command line got wrong; nothing is sent."""


def episode_cues(spine: Mapping[str, Any], *, episode: int) -> list[dict[str, Any]]:
    """The episode's inner-voice cues as the server holds them, in time order.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[dict[str, Any]]
        ``DramaInnerVoiceCue`` objects (only their contract fields). The
        optional ones (:data:`OPTIONAL_CUE_FIELDS`) are kept when the server set
        them, so a PUT of the whole list never drops a thought's spoken words or
        its acting direction.
    """

    episode_id = episode_id_for(spine, episode)
    for summary in spine.get("episode_summaries") or []:
        if isinstance(summary, Mapping) and summary.get("episode_id") == episode_id:
            cues = [
                {
                    **{
                        key: cue.get(key)
                        for key in (
                            "cue_id",
                            "start_ms",
                            "end_ms",
                            "speaker_cast_id",
                            "line",
                        )
                    },
                    **{
                        key: cue[key]
                        for key in OPTIONAL_CUE_FIELDS
                        if cue.get(key) is not None
                    },
                }
                for cue in summary.get("inner_voice") or []
                if isinstance(cue, Mapping)
            ]
            return sorted(cues, key=lambda cue: int(cue.get("start_ms") or 0))
    return []


def cue_listing(
    cues: Sequence[Mapping[str, Any]],
    names: Mapping[str, str],
    spoken: Mapping[str, Mapping[str, str]] | None = None,
    levels: Mapping[str, float] | None = None,
) -> list[str]:
    """One printable row per cue: number, id, time, speaker, words (the words said, when apart; its level)."""

    rows = []
    for number, cue in enumerate(cues, start=1):
        said = str(cue.get("spoken_text") or "") or spoken_for(
            spoken or {}, str(cue.get("cue_id")), str(cue.get("line") or "")
        )
        rows.append(
            f"  {number}. {cue.get('cue_id')}  {int(cue.get('start_ms') or 0) / 1000:.2f}-"
            f"{int(cue.get('end_ms') or 0) / 1000:.2f}s  "
            f"{names.get(str(cue.get('speaker_cast_id')), cue.get('speaker_cast_id'))} (thinks): {cue.get('line')}"
            + (f"  [says: {said}]" if said else "")
            + (
                f"  [acted: {cue.get('acting_direction')}]"
                if cue.get("acting_direction")
                else ""
            )
            + (
                f"  [{(levels or {})[str(cue.get('cue_id'))]:+g} dB]"
                if (levels or {}).get(str(cue.get("cue_id")))
                else ""
            )
        )
    return rows


#: Beside the episode's run notes: ``{"cues": {cue_id: {"line": caption, "spoken_text": words said}}}``.
SPOKEN_FILE = "inner-voice-spoken.json"


def spoken_path(desk: Path, episode: int) -> Path:
    """``<desk>/epNN/inner-voice-spoken.json``."""

    return desk / f"ep{episode:02d}" / SPOKEN_FILE


def load_spoken(desk: Path, episode: int) -> dict[str, dict[str, str]]:
    """The episode's spoken words per cue id (empty when none are saved)."""

    path = spoken_path(desk, episode)
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    cues = raw.get("cues") if isinstance(raw, Mapping) else None
    return {
        str(cue_id): {
            "line": str(entry.get("line") or ""),
            "spoken_text": str(entry.get("spoken_text") or ""),
        }
        for cue_id, entry in (cues or {}).items()
        if isinstance(entry, Mapping)
    }


def _write_spoken(
    desk: Path, episode: int, cues: Mapping[str, Mapping[str, str]]
) -> None:
    path = spoken_path(desk, episode)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"cues": {cue_id: dict(entry) for cue_id, entry in sorted(cues.items())}}
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def save_spoken(
    desk: Path, episode: int, *, cue_id: str, line: str, spoken_text: str
) -> Path:
    """Keep the words a cue's voice says (``--spoken-text``) beside the caption the server holds."""

    cues = load_spoken(desk, episode)
    cues[cue_id] = {
        "line": " ".join(line.split()),
        "spoken_text": " ".join(spoken_text.split()),
    }
    _write_spoken(desk, episode, cues)
    return spoken_path(desk, episode)


def drop_spoken(desk: Path, episode: int, cue_ids: Sequence[str]) -> None:
    """Forget the spoken words of removed cues."""

    cues = load_spoken(desk, episode)
    kept = {
        cue_id: entry for cue_id, entry in cues.items() if cue_id not in set(cue_ids)
    }
    if kept != cues:
        _write_spoken(desk, episode, kept)


#: Beside the spoken words: ``{"cues": {cue_id: dB}}``, each thought's level against a dialogue line
#: (``inner-voice --db``). The server's cue has no field for it either (L-20261006-30).
LEVELS_FILE = "inner-voice-levels.json"
#: The levels ``--db`` takes: quieter than a line by up to 24 dB, louder by up to 6 dB.
LEVEL_RANGE_DB = (-24.0, 6.0)


def levels_path(desk: Path, episode: int) -> Path:
    """``<desk>/epNN/inner-voice-levels.json``."""

    return desk / f"ep{episode:02d}" / LEVELS_FILE


def load_levels(desk: Path, episode: int) -> dict[str, float]:
    """Each cue's level in dB against a dialogue line (empty when none are saved: every thought at 0 dB)."""

    path = levels_path(desk, episode)
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    cues = raw.get("cues") if isinstance(raw, Mapping) else None
    return {
        str(cue_id): float(db)
        for cue_id, db in (cues or {}).items()
        if isinstance(db, (int, float)) and not isinstance(db, bool)
    }


def _write_levels(desk: Path, episode: int, levels: Mapping[str, float]) -> None:
    path = levels_path(desk, episode)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"cues": {cue_id: levels[cue_id] for cue_id in sorted(levels)}}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def check_level(db: float) -> float:
    """``db`` rounded to 0.1, or :class:`InnerVoiceError` outside :data:`LEVEL_RANGE_DB`."""

    low, high = LEVEL_RANGE_DB
    if not low <= db <= high:
        raise InnerVoiceError(
            f"--db {db:g} is out of range: a thought plays {low:g} to +{high:g} dB against a line "
            "(-3 is a little quieter, -6 clearly quieter; 0 is a line's level)"
        )
    return round(db, 1)


def save_level(desk: Path, episode: int, *, cue_id: str, db: float) -> Path:
    """Keep one cue's level (0 dB forgets it: the default)."""

    levels = load_levels(desk, episode)
    if db == 0:
        levels.pop(cue_id, None)
    else:
        levels[cue_id] = check_level(db)
    _write_levels(desk, episode, levels)
    return levels_path(desk, episode)


def drop_levels(desk: Path, episode: int, cue_ids: Sequence[str]) -> None:
    """Forget the levels of removed cues."""

    levels = load_levels(desk, episode)
    kept = {cue_id: db for cue_id, db in levels.items() if cue_id not in set(cue_ids)}
    if kept != levels:
        _write_levels(desk, episode, kept)


def cue_id_for(cues: Sequence[Mapping[str, Any]], ref: str) -> str:
    """A cue's id from its id or its number in :func:`cue_listing`."""

    ids = [str(cue.get("cue_id")) for cue in cues]
    wanted = ids[int(ref) - 1] if ref.isdigit() and 1 <= int(ref) <= len(ids) else ref
    if wanted not in ids:
        raise InnerVoiceError(
            f"no inner-voice cue {ref!r} on this episode; the cues are: {', '.join(ids) or 'none'}"
        )
    return wanted


def spoken_for(spoken: Mapping[str, Mapping[str, str]], cue_id: str, line: str) -> str:
    """The words a cue's voice says, when saved for this very caption; ``""`` otherwise (say the ``line``)."""

    entry = spoken.get(cue_id)
    if not entry or entry.get("line") != " ".join(line.split()):
        return ""
    return str(entry.get("spoken_text") or "")


def default_seconds(text: str) -> float:
    """How long a thought with no ``--until`` is given: about 0.4 s a word, at least 1.2 s."""

    return max(MIN_SECONDS, round(len(text.split()) * SECONDS_PER_WORD, 2))


def new_cue_id(cues: Sequence[Mapping[str, Any]], *, episode: int) -> str:
    """The next free ``iv_epNN_KK`` id."""

    taken = {str(cue.get("cue_id")) for cue in cues}
    number = len(cues) + 1
    while f"iv_ep{episode:02d}_{number:02d}" in taken:
        number += 1
    return f"iv_ep{episode:02d}_{number:02d}"


def add_cue(
    cues: Sequence[Mapping[str, Any]],
    *,
    episode: int,
    speaker_cast_id: str,
    text: str,
    at: float,
    until: float | None,
    spoken_text: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The list with one new cue in it, in time order.

    Parameters
    ----------
    cues
        The episode's cues now.
    episode
        Episode ordinal.
    speaker_cast_id
        Who thinks it (a cast id already on the story).
    text
        The thought.
    at, until
        Seconds on the episode as filmed; ``until`` left out is :func:`default_seconds` after ``at``.
    spoken_text
        The words the voice says when they differ from the caption (``--spoken-text``); kept on the
        server's cue (``spoken_text``), never on the desk.

    Returns
    -------
    tuple[list[dict[str, Any]], dict[str, Any]]
        The whole new list and the added cue.
    """

    words = " ".join(text.split())
    if not words:
        raise InnerVoiceError('--text needs the thought in words: --text "..."')
    if at < 0:
        raise InnerVoiceError(
            "--at is seconds from the start of the episode, 0 or more"
        )
    end = until if until is not None else at + default_seconds(words)
    if end <= at:
        raise InnerVoiceError(f"--until {end:g} must be after --at {at:g}")
    cue = {
        "cue_id": new_cue_id(cues, episode=episode),
        "start_ms": int(round(at * 1000)),
        "end_ms": int(round(end * 1000)),
        "speaker_cast_id": speaker_cast_id,
        "line": words,
    }
    said = " ".join((spoken_text or "").split())
    if said:
        cue["spoken_text"] = said
    merged = [dict(c) for c in cues] + [cue]
    return sorted(merged, key=lambda c: int(c.get("start_ms") or 0)), cue


def remove_cue(
    cues: Sequence[Mapping[str, Any]], ref: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The list without one cue (its id, or its number in :func:`cue_listing`)."""

    ids = [str(cue.get("cue_id")) for cue in cues]
    wanted = ids[int(ref) - 1] if ref.isdigit() and 1 <= int(ref) <= len(ids) else ref
    if wanted not in ids:
        raise InnerVoiceError(
            f"no inner-voice cue {ref!r} on this episode; the cues are: {', '.join(ids) or 'none'}"
        )
    gone = next(dict(cue) for cue in cues if str(cue.get("cue_id")) == wanted)
    return [dict(cue) for cue in cues if str(cue.get("cue_id")) != wanted], gone


def overlaps(cues: Sequence[Mapping[str, Any]], cue: Mapping[str, Any]) -> list[str]:
    """Ids of the other cues whose time overlaps ``cue``."""

    return [
        str(other.get("cue_id"))
        for other in cues
        if other.get("cue_id") != cue.get("cue_id")
        and int(other.get("start_ms") or 0) < int(cue.get("end_ms") or 0)
        and int(cue.get("start_ms") or 0) < int(other.get("end_ms") or 0)
    ]


def request_body(
    spine: Mapping[str, Any], *, episode: int, cues: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The ``DramaInnerVoiceReplaceRequest`` for the episode's whole new list."""

    return {
        "spine_version": spine["spine_version"],
        "episode_ordinal": episode,
        "cues": [dict(cue) for cue in cues],
    }


@dataclass(frozen=True)
class TakeCue:
    """One inner-voice cue on the take it starts in, in seconds on that take.

    Parameters
    ----------
    cue_id, speaker_cast_id, line
        As the server holds the cue (``line`` is the caption).
    start, end
        Seconds on the take (the episode's times less where the take starts).
    episode_start
        Seconds on the episode (as saved with ``inner-voice``).
    seam
        Non-empty when the cue runs past the end of its take: the flag to print.
    """

    cue_id: str
    speaker_cast_id: str
    line: str
    start: float
    end: float
    episode_start: float
    seam: str = ""
    #: The words the voice says when they differ from the caption (:func:`spoken_for`); ``""``: say ``line``.
    spoken_text: str = ""
    #: The thought's level against a dialogue line, dB (``inner-voice --db``, :func:`load_levels`); 0: a line's.
    db: float = 0.0


@dataclass(frozen=True)
class TakeCuePlan:
    """The cues one take lays, and the ones that cannot be placed (each named with why)."""

    cues: tuple[TakeCue, ...] = ()
    problems: tuple[str, ...] = ()
    #: Where the take starts on the episode (seconds); ``None`` when an earlier take is not on the desk.
    offset: float | None = None

    @property
    def any(self) -> bool:
        """True when the take has a cue to lay or one to report."""

        return bool(self.cues or self.problems)


def take_number(take_id: str) -> int:
    """``t2`` -> 2."""

    digits = take_id.strip().lower().removeprefix("t")
    if not digits.isdigit() or int(digits) < 1:
        raise ValueError(f"a take id is t1, t2, ...; got {take_id!r}")
    return int(digits)


def refusal_words(message: str) -> str:
    """Plain words for the server's answer to the inner-voice PUT (the message stays as the server said it)."""

    status = re.search(r"HTTP (\d{3})", message)
    code = status.group(1) if status else ""
    if code == "404":
        return (
            "the server found no such story or episode (or the deploy has no inner-voice route), nothing was "
            "saved. Check "
            "--episode; if it is right, stop and tell engineering (never send it by hand)."
        )
    if code == "409":
        return (
            "the story changed on the server since the kit read it, nothing was saved: run the same command "
            "again (it reads the story fresh)."
        )
    if code in {"400", "422"}:
        return (
            "the server refused the cues, nothing was saved. Read its reason above; a time, a speaker or an "
            "empty line is the usual cause (and --spoken-text on a show performed in English: there the "
            "thought is said as its --text)."
        )
    return "the server did not save the cues."


__all__ = [
    "InnerVoiceError",
    "TakeCue",
    "TakeCuePlan",
    "add_cue",
    "LEVELS_FILE",
    "LEVEL_RANGE_DB",
    "OPTIONAL_CUE_FIELDS",
    "check_level",
    "cue_id_for",
    "cue_listing",
    "default_seconds",
    "drop_levels",
    "drop_spoken",
    "episode_cues",
    "load_levels",
    "load_spoken",
    "new_cue_id",
    "overlaps",
    "refusal_words",
    "remove_cue",
    "request_body",
    "levels_path",
    "save_level",
    "save_spoken",
    "spoken_for",
    "spoken_path",
    "take_number",
]
