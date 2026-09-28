"""A take's saved facts and the story's sound notes: stamp, save, compare, and say when they are stale.

``finish`` lays a take's automatic sound effects from the take facts saved on
the desk (``epNN/api/take-facts-epNN-tK-vN.json``). The server builds those
facts from the story as it is when they are fetched: a sound note that adds a
sound to a take (``sound-note ... "add a dry stone crack at the end"``) lands in
the facts fetched after it, never in a file saved before it.

So every facts file the kit saves is stamped with the add notes it already
carries for its take (:data:`SOUND_NOTES_KEY`), and :func:`stale_facts_reason`
compares that stamp with the story on the desk. A file saved before this stamp
existed reads as carrying none.

Drop and level notes ("no purring", "louder rain") are applied by the
server's own mix. ``finish`` on this laptop does not read them: the operator
passes ``finish --sfx-adjust`` for the same change (:func:`level_notes`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from creation.ops.folder import next_versioned_path
from creation.spine_view import episode_id_for

#: The kit's stamp on a saved facts file: the add notes for this take the facts already carry.
SOUND_NOTES_KEY = "desk_sound_notes"


def take_number(take_id: str) -> int:
    """``t2`` -> ``2``: the take's storyboard set, the number the server's notes use.

    Raises
    ------
    ValueError
        When ``take_id`` is not ``tN``.
    """

    raw = take_id.strip().lower().removeprefix("t")
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"a take is t1, t2 ...; got {take_id!r}")
    return int(raw)


def _notes(spine: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not spine:
        return []
    return [
        note for note in spine.get("sound_notes") or [] if isinstance(note, Mapping)
    ]


def take_add_notes(
    spine: Mapping[str, Any] | None, *, episode: int, take_id: str
) -> list[dict[str, Any]]:
    """The story's sound notes that add a sound to this one take (the server saved them with its scope).

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` JSON (or ``None``).
    episode
        Episode ordinal.
    take_id
        ``t1``, ``t2`` ...

    Returns
    -------
    list[dict[str, Any]]
        ``{note_id, text, shot}`` per note, oldest first.
    """

    if not spine:
        return []
    episode_id = episode_id_for(spine, episode)
    take = take_number(take_id)
    return [
        {
            "note_id": str(note.get("note_id") or ""),
            "text": str(note.get("text") or ""),
            "shot": note.get("shot"),
        }
        for note in _notes(spine)
        if note.get("episode_id") == episode_id and note.get("take") == take
    ]


def level_notes(spine: Mapping[str, Any] | None) -> list[str]:
    """The story's drop and level notes (no take named): the server's mix reads them, ``finish`` does not."""

    return [
        str(note.get("text") or "")
        for note in _notes(spine)
        if note.get("take") is None and note.get("episode_id") is None
    ]


def stamp_facts(
    facts: Mapping[str, Any],
    spine: Mapping[str, Any] | None,
    *,
    episode: int,
    take_id: str,
) -> dict[str, Any]:
    """The facts with the add notes they carry for this take recorded under :data:`SOUND_NOTES_KEY`.

    Call it with the story as it was when the facts were fetched.
    """

    stamped = dict(facts)
    stamped[SOUND_NOTES_KEY] = take_add_notes(spine, episode=episode, take_id=take_id)
    return stamped


def save_take_facts(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    facts: Mapping[str, Any],
    spine: Mapping[str, Any] | None,
) -> Path:
    """Write the next ``epNN/api/take-facts-epNN-tK-vN.json`` (never over an older one), stamped.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1`` ...
    facts
        ``take_facts`` from ``GET /v1/jobs/{take_job}/take-facts``.
    spine
        The story the facts were fetched against (its sound notes are the stamp).

    Returns
    -------
    Path
        The new file.
    """

    folder = desk / f"ep{episode:02d}" / "api"
    folder.mkdir(parents=True, exist_ok=True)
    path = next_versioned_path(
        folder,
        f"take-facts-ep{episode:02d}-{take_id}",
        ".json",
    )
    stamped = stamp_facts(facts, spine, episode=episode, take_id=take_id)
    path.write_text(
        json.dumps(stamped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def stale_facts_reason(
    facts: Mapping[str, Any],
    spine: Mapping[str, Any] | None,
    *,
    episode: int,
    take_id: str,
) -> str | None:
    """Say why saved take facts are older than the story's sound notes for this take, or ``None``.

    Parameters
    ----------
    facts
        A saved facts file (``{"take_facts": {...}}`` or the facts alone).
    spine
        The story on the desk.
    episode
        Episode ordinal.
    take_id
        ``t1`` ...

    Returns
    -------
    str | None
        What changed since the facts were saved; ``None`` when they carry every note.
    """

    body = facts.get("take_facts", facts)
    carried = {
        str(note.get("note_id"))
        for note in body.get(SOUND_NOTES_KEY) or []
        if isinstance(note, Mapping)
    }
    current = take_add_notes(spine, episode=episode, take_id=take_id)
    now = {note["note_id"] for note in current}
    added = [note["text"] for note in current if note["note_id"] not in carried]
    removed = len(carried - now)
    if not added and not removed:
        return None
    parts = []
    if added:
        parts.append(
            f"{len(added)} sound note(s) added to this take since ("
            + "; ".join(f"'{text}'" for text in added)
            + ")"
        )
    if removed:
        parts.append(f"{removed} removed since")
    return ", ".join(parts)


def _cue_line(cue: Mapping[str, Any]) -> str:
    start = float(cue.get("start_seconds") or 0.0)
    seconds = float(cue.get("duration_seconds") or 0.0)
    source = str(cue.get("source") or "sound_line").replace("_", " ")
    return (
        f"shot {cue.get('shot_index')} at {start:.2f}s for {seconds:.2f}s: "
        f"{cue.get('sound')} ({cue.get('kind') or 'event'}, {source})"
    )


def sfx_plan_changes(
    old: Mapping[str, Any] | None, new: Mapping[str, Any]
) -> list[str]:
    """What moved in the SFX cue plan between two facts files, one line per cue.

    Parameters
    ----------
    old
        The previous facts (``None``: there were none on the desk).
    new
        The fresh facts.

    Returns
    -------
    list[str]
        ``+ …`` for a new cue, ``- …`` for one no longer planned; empty when the plan is the same.
    """

    def cues(facts: Mapping[str, Any] | None) -> list[str]:
        if not facts:
            return []
        body = facts.get("take_facts", facts)
        return [
            _cue_line(cue)
            for cue in body.get("sfx_cues") or []
            if isinstance(cue, Mapping) and str(cue.get("sound") or "").strip()
        ]

    before, after = cues(old), cues(new)
    gone = list(before)
    added: list[str] = []
    for line in after:
        if line in gone:
            gone.remove(line)
        else:
            added.append(line)
    return [f"+ {line}" for line in added] + [f"- {line}" for line in gone]


__all__ = [
    "SOUND_NOTES_KEY",
    "level_notes",
    "save_take_facts",
    "sfx_plan_changes",
    "stale_facts_reason",
    "stamp_facts",
    "take_add_notes",
    "take_number",
]
