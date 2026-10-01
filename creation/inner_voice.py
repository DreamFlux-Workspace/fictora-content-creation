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
lays it dry on the timeline. The kit's ``finish`` does that per take
(:func:`take_cues`).

Cue times count from the start of the episode as filmed; the contract names no
take. Take ``tN`` starts at the sum of the raw lengths of ``t1`` .. ``tN-1``
(each measured with ffprobe on its newest raw take), so a cue belongs to the
take whose window holds its start. A cue that runs past its take's end
(straddles a seam) stays on the take where it starts and is flagged. A cue
that starts after the last filmed take ends belongs to no take and is named.

Spoken words and caption, apart (a Japanese thought under an English caption).
The contract's cue has one ``line`` and no other field, so the server holds the
CAPTION (``--text``) and the words the voice says (``--spoken-text``) are kept
on the desk, in ``epNN/inner-voice-spoken.json`` (:func:`save_spoken`), keyed by
cue id and the caption they go with: ``finish`` sends them to the voice as
``voice-line --text/--spoken-text`` does, and captions the ``line``. A cue whose
caption changed (removed and added again) never picks up stale words
(:func:`spoken_for`). Server gap: ``DramaInnerVoiceCue`` has no
``spoken_text``; see ``docs/content-ops/backlog.md``.
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
        ``DramaInnerVoiceCue`` objects (only their contract fields).
    """

    episode_id = episode_id_for(spine, episode)
    for summary in spine.get("episode_summaries") or []:
        if isinstance(summary, Mapping) and summary.get("episode_id") == episode_id:
            cues = [
                {
                    key: cue.get(key)
                    for key in (
                        "cue_id",
                        "start_ms",
                        "end_ms",
                        "speaker_cast_id",
                        "line",
                    )
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
) -> list[str]:
    """One printable row per cue: number, id, time, speaker, words (and the words said, when apart)."""

    rows = []
    for number, cue in enumerate(cues, start=1):
        said = spoken_for(
            spoken or {}, str(cue.get("cue_id")), str(cue.get("line") or "")
        )
        rows.append(
            f"  {number}. {cue.get('cue_id')}  {int(cue.get('start_ms') or 0) / 1000:.2f}-"
            f"{int(cue.get('end_ms') or 0) / 1000:.2f}s  "
            f"{names.get(str(cue.get('speaker_cast_id')), cue.get('speaker_cast_id'))} (thinks): {cue.get('line')}"
            + (f"  [says: {said}]" if said else "")
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


def take_cues(
    cues: Sequence[Mapping[str, Any]],
    *,
    take: int,
    lengths: Sequence[float | None],
    last_filmed: bool,
) -> TakeCuePlan:
    """The episode's cues that fall in take ``take``, in seconds on that take.

    Take N starts at the sum of the lengths of takes 1 .. N-1 (the episode as
    filmed). A cue belongs to the take whose window holds its start; one that
    runs past the take's end stays here and is flagged (``seam``). When an
    earlier take's length is unknown, a cue that may start in this take is named
    as not placed (the sum of the known lengths is only a lower bound).

    Parameters
    ----------
    cues
        :func:`episode_cues` (times in ms on the episode).
    take
        Take number (``t2`` is 2).
    lengths
        Seconds of takes 1 .. ``take`` in order (``None`` for a take with no
        raw file on the desk); the last entry is this take and must be known.
    last_filmed
        No later take is filmed: a cue starting after this take ends belongs to
        no take and is named.

    Returns
    -------
    TakeCuePlan

    Raises
    ------
    ValueError
        When ``lengths`` does not cover takes 1 .. ``take`` or this take's length is unknown.
    """

    if len(lengths) != take or lengths[-1] is None:
        raise ValueError(
            f"take_cues needs the length of takes 1..{take}, this take's known"
        )
    here = f"t{take}"
    this_length = float(lengths[-1])
    earlier = list(lengths[:-1])
    missing = [f"t{i}" for i, length in enumerate(earlier, start=1) if length is None]
    known = round(sum(float(length) for length in earlier if length is not None), 3)
    placed: list[TakeCue] = []
    problems: list[str] = []
    for cue in cues:
        cue_id = str(cue.get("cue_id"))
        start = int(cue.get("start_ms") or 0) / 1000
        end = int(cue.get("end_ms") or 0) / 1000
        if missing:
            if start >= known:
                problems.append(
                    f"{cue_id} at {start:.2f}s on the episode: {', '.join(missing)} has no raw take on the desk, "
                    f"so where {here} starts is unknown; not laid"
                )
            continue
        take_end = known + this_length
        if start < known:
            continue
        if start >= take_end:
            if last_filmed:
                problems.append(
                    f"{cue_id} at {start:.2f}s starts after the last filmed take ({here}) ends at "
                    f"{take_end:.2f}s on the episode; not laid (move it with `inner-voice --remove` and --at)"
                )
            continue
        seam = (
            f"{cue_id} runs {start:.2f}-{end:.2f}s on the episode, past the seam at {take_end:.2f}s where "
            f"{here} ends; laid on {here}, where it starts"
            if end > take_end + 1e-3
            else ""
        )
        placed.append(
            TakeCue(
                cue_id=cue_id,
                speaker_cast_id=str(cue.get("speaker_cast_id") or ""),
                line=" ".join(str(cue.get("line") or "").split()),
                start=round(start - known, 3),
                end=round(end - known, 3),
                episode_start=start,
                seam=seam,
            )
        )
    return TakeCuePlan(tuple(placed), tuple(problems), None if missing else known)


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
            "empty line is the usual cause."
        )
    return "the server did not save the cues."


__all__ = [
    "InnerVoiceError",
    "TakeCue",
    "TakeCuePlan",
    "add_cue",
    "cue_listing",
    "default_seconds",
    "drop_spoken",
    "episode_cues",
    "load_spoken",
    "new_cue_id",
    "overlaps",
    "refusal_words",
    "remove_cue",
    "request_body",
    "save_spoken",
    "spoken_for",
    "spoken_path",
    "take_cues",
    "take_number",
]
