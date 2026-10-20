"""No music bed: delivered videos carry no music; this module keeps the music change notes.

Founder decision, 20 Oct 2026: every video the kit delivers (``finish``,
``join``, the Instagram reel and the TikTok part) ships with NO harness music:
voice, sound effects and room ambience only. When the video is posted, a
trending sound is added inside Instagram or TikTok, picked to fit the account
(eerie for horror, soft for BL and food, playful-spooky for horror-comedy).
Captions stay on.

The kit lays no bed, finds none, makes none and pins none (the old show bed,
its level and ``--bed-db`` are gone). If a reel truly needs its own score
(say a jumpscare sting), the server makes a separate "with score" version on
request from the stored stems ($0); the no-music version stays the default and
the operator chooses at posting time. Older episodes keep their music: nothing
is stripped automatically; a "no music" note removes it on request (free). The music,
and every rule about it, is
the server's (fictora-drama); the kit keeps no music logic or state of its own.

What stays here: an operator's music change note (``music-note`` or
``finish --music``) is still saved on the desk in ``shared/music-notes.jsonl``
(one entry per note, with a stable ``id``) and sent to the harness by
``music-note`` (:mod:`creation.post.music_send`: a plan first, applied only
with ``--yes``), which writes back what the harness did. Nothing is rendered
from it here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from creation.ops.notes import append_run_note

#: Where the music change notes for the harness are kept on the desk.
MUSIC_NOTES_FILE = Path("shared") / "music-notes.jsonl"

#: What an operator is told wherever the kit used to lay or take music (founder decision, 20 Oct 2026).
NO_MUSIC_RULE = (
    "Delivered videos carry no music: voice, sound effects and room sound only. When you post, add a "
    "trending sound inside Instagram or TikTok that fits the account (eerie for horror, soft for BL and "
    "food, playful-spooky for horror-comedy). If one reel truly needs its own score (a jumpscare sting), "
    'ask the Fictora team for its "with score" version from the server; the no-music version stays the '
    "default and you choose which to post. An older episode keeps its music unless you ask: send it the "
    'note "no music" (free); clips whose music the video model made can only change by filming again.'
)


#: What a sent note's ``status`` can be: ``planned`` (a dry run answered), ``applied`` (the harness ran it),
#: ``blocked`` (a new bed was needed and provider spend is off), ``refused`` (the server said no),
#: ``held`` (the note asks for no music but the harness's plan would make new music: never applied, see
#: :func:`creation.post.music_send.asks_for_no_music`).
SENT_STATUSES = ("planned", "applied", "blocked", "refused", "held")


def _scope_of(entry: Mapping[str, Any]) -> tuple[int | None, str | None]:
    episode = entry.get("episode")
    take = entry.get("take")
    return (int(episode) if episode is not None else None, str(take) if take else None)


def music_note_entries(desk: Path) -> list[dict[str, Any]]:
    """Every saved music note entry, oldest first, each with its ``id``.

    Entries written before notes had ids get one derived from their line, so
    it stays the same until the entry is next written back (with that id).
    """

    path = desk / MUSIC_NOTES_FILE
    if not path.is_file():
        return []
    found: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if not entry.get("id"):
            entry["id"] = "n" + hashlib.sha256(raw.encode()).hexdigest()[:11]
        found.append(entry)
    return found


def _write_entries(desk: Path, entries: list[dict[str, Any]]) -> Path:
    path = desk / MUSIC_NOTES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def update_music_note(desk: Path, note_id: str, **fields: Any) -> dict[str, Any]:
    """Write fields onto one saved note (what the harness answered); returns the entry.

    Raises
    ------
    KeyError
        When no saved note has that id.
    """

    entries = music_note_entries(desk)
    for entry in entries:
        if entry["id"] == note_id:
            entry.update(
                {key: value for key, value in fields.items() if value is not None}
            )
            _write_entries(desk, entries)
            return entry
    raise KeyError(f"no saved music note {note_id}")


def save_music_note(
    desk: Path,
    note: str | None = None,
    *,
    episode: int | None = None,
    take_id: str | None = None,
    via: str = "music-note",
    revert_to_version: int | None = None,
) -> tuple[dict[str, Any], bool]:
    """Save one music change note (or a revert) on the desk; the same unsent note is not saved twice.

    Parameters
    ----------
    desk
        Series desk.
    note
        The change in the operator's words ("calmer"); ``None`` with ``revert_to_version``.
    episode, take_id
        Where it applies; ``None`` is the whole show.
    via
        The command that took it.
    revert_to_version
        Put this earlier music version back instead of a note.

    Returns
    -------
    tuple[dict[str, Any], bool]
        The entry (with its stable ``id``) and whether it is new.

    Raises
    ------
    ValueError
        When the note is empty, or both or neither of a note and a revert are given.
    """

    if (note is None) == (revert_to_version is None):
        raise ValueError("a music note or a version to revert to, not both")
    text = " ".join(note.split()) if note is not None else None
    if note is not None and not text:
        raise ValueError(
            "a music change note needs words: what should change about the music"
        )
    if take_id is not None and episode is None:
        raise ValueError("--take goes with --episode")
    entries = music_note_entries(desk)
    for entry in entries:
        if (
            entry.get("status") != "applied"
            and _scope_of(entry) == (episode, take_id)
            and (
                (text is not None and entry.get("note") == text)
                or (
                    revert_to_version is not None
                    and entry.get("revert_to_version") == revert_to_version
                )
            )
        ):
            return entry, False
    entry: dict[str, Any] = {"id": uuid.uuid4().hex[:12], "via": via}
    if text is not None:
        entry["note"] = text
    else:
        entry["revert_to_version"] = revert_to_version
    if episode is not None:
        entry["episode"] = episode
    if take_id is not None:
        entry["take"] = take_id
    path = desk / MUSIC_NOTES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    if (
        text is not None
        and episode is not None
        and (desk / f"ep{episode:02d}" / "run-notes.md").is_file()
    ):
        where = f" ({take_id})" if take_id else ""
        append_run_note(
            desk / f"ep{episode:02d}",
            f"Music change note for the harness{where}: {text}",
        )
    return entry, True


def record_music_note(
    desk: Path,
    note: str,
    *,
    episode: int | None = None,
    take_id: str | None = None,
    via: str = "music-note",
) -> Path:
    """Save one music change note for the harness on the desk, and in the episode's run notes.

    Parameters
    ----------
    desk
        Series desk.
    note
        The change in the operator's words ("calmer", "quieter under the lines").
    episode, take_id
        Where it applies; ``None`` is the whole show.
    via
        The command that took it (``music-note`` or ``finish --music``).

    Returns
    -------
    Path
        The notes file (``shared/music-notes.jsonl``).

    Raises
    ------
    ValueError
        When the note is empty.
    """

    save_music_note(desk, note, episode=episode, take_id=take_id, via=via)
    return desk / MUSIC_NOTES_FILE


def music_notes(
    desk: Path, *, episode: int | None = None, take_id: str | None = None
) -> list[str]:
    """The saved music change notes that apply here (show-wide, this episode, this take), oldest first.

    A note the harness already applied says so (``applied v3``); an unsent one is the note and its scope.
    """

    found: list[str] = []
    for entry in music_note_entries(desk):
        if not str(entry.get("note") or "").strip():
            continue
        if (
            entry.get("episode") is not None
            and episode is not None
            and entry["episode"] != episode
        ):
            continue
        if (
            entry.get("take") is not None
            and take_id is not None
            and entry["take"] != take_id
        ):
            continue
        scope = (
            f"ep{int(entry['episode']):02d}"
            + (f" {entry['take']}" if entry.get("take") else "")
            if entry.get("episode") is not None
            else "show"
        )
        if entry.get("status") == "applied":
            scope += "; applied" + (
                f" v{entry['version']}" if entry.get("version") else ""
            )
        found.append(f"{entry['note']} ({scope})")
    return found
