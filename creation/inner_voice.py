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
lays it dry on the timeline. The kit's ``finish`` does not read these cues yet
(no dry line, no caption); the command says so and prints the hand path.
"""

from __future__ import annotations

import re
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
    cues: Sequence[Mapping[str, Any]], names: Mapping[str, str]
) -> list[str]:
    """One printable row per cue: number, id, time, speaker, words."""

    return [
        f"  {number}. {cue.get('cue_id')}  {int(cue.get('start_ms') or 0) / 1000:.2f}-"
        f"{int(cue.get('end_ms') or 0) / 1000:.2f}s  "
        f"{names.get(str(cue.get('speaker_cast_id')), cue.get('speaker_cast_id'))} (thinks): {cue.get('line')}"
        for number, cue in enumerate(cues, start=1)
    ]


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
    "add_cue",
    "cue_listing",
    "default_seconds",
    "episode_cues",
    "new_cue_id",
    "overlaps",
    "refusal_words",
    "remove_cue",
    "request_body",
]
