"""``redraw-board --note``: turn "what's wrong with this board" into shot edits before the take is redrawn.

The product does this through its director: a creator types what is wrong,
``POST /v1/spines/{id}/director/turns`` answers with a ``patch_story`` step that
re-stages the take's beats (their ``motion_intent``), the change is applied (a
plain patch before the script gate, a cascade after it), and the boards
regenerate route re-authors the take's frames from the edited beats before it
draws (``DramaFrame.edited_beat_ids`` on the server). The kit sends the note
down that same path; nothing here plans shots itself.

When the deployed regenerate route takes a ``note`` of its own (its request
schema lists ``note`` in ``/openapi.json``), the note goes on the redraw
instead and the server does the whole thing in one call: it re-authors the
take's frames from the note and draws the board with the note as a correction.
When ``/openapi.json`` cannot be read, the note is tried on the redraw; a
server that refuses the field (422 naming ``note``) gets it as shot edits
instead (:func:`note_refused_by_server`).

A redraw without a note of a board nothing has changed for is refused by
``redraw-board`` itself (``board_changes``); this module only serves the note.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from creation.spine_view import (
    ShotRow,
    beats_by_take,
    frames_by_set,
    shot_rows,
)

#: The regenerate route, as ``/openapi.json`` lists it (any version prefix).
REGENERATE_PATH_SUFFIX = "/episodes/{ordinal}/boards/{set_index}/regenerate"
#: Where the creator is standing when they say what is wrong with a board (the app's stage name).
DIRECTOR_STAGE = "storyboard"
#: The director tool that re-stages beats.
PATCH_TOOL = "patch_story"


def regenerate_takes_note(openapi: Any) -> bool:
    """Whether the deployed boards regenerate route accepts a ``note`` field.

    Parameters
    ----------
    openapi
        ``/openapi.json`` as read from the deploy (anything else reads as no).

    Returns
    -------
    bool
        ``True`` only when the route's request schema lists ``note``.
    """

    if not isinstance(openapi, Mapping):
        return False
    schemas = (openapi.get("components") or {}).get("schemas") or {}
    for path, item in (openapi.get("paths") or {}).items():
        if not str(path).endswith(REGENERATE_PATH_SUFFIX) or not isinstance(
            item, Mapping
        ):
            continue
        body = ((item.get("post") or {}).get("requestBody") or {}).get("content") or {}
        schema = (body.get("application/json") or {}).get("schema") or {}
        ref = str(schema.get("$ref") or "")
        resolved = schemas.get(ref.rsplit("/", 1)[-1]) if ref else schema
        if isinstance(resolved, Mapping) and "note" in (
            resolved.get("properties") or {}
        ):
            return True
    return False


def regenerate_note_support(status: int, openapi: Any) -> bool | None:
    """Whether the deploy's regenerate route takes ``note``, as far as ``/openapi.json`` says.

    Parameters
    ----------
    status
        HTTP status of the ``/openapi.json`` read.
    openapi
        Its body.

    Returns
    -------
    bool | None
        ``True`` or ``False`` from a readable schema; ``None`` when the schema
        could not be read (the note is then tried on the redraw, and a refusal
        falls back to shot edits).
    """

    if not 200 <= status < 300 or not isinstance(openapi, Mapping):
        return None
    return regenerate_takes_note(openapi)


def note_refused_by_server(message: Any) -> bool:
    """Whether a failed redraw was refused only because the server does not know ``note``.

    Parameters
    ----------
    message
        The error the redraw POST stopped with (``HTTP 422 POST …: invalid_request: …``).

    Returns
    -------
    bool
        ``True`` for a 422 whose refusal names the ``note`` field as not permitted.
    """

    text = str(message or "")
    if "HTTP 422" not in text:
        return False
    names_note = '"note"' in text or "note:" in text
    unknown = "extra_forbidden" in text or "not permitted" in text
    return names_note and unknown


def take_beats(
    spine: Mapping[str, Any], *, episode: int, set_index: int, take_count: int
) -> list[tuple[int, dict[str, Any]]]:
    """The beats one take draws, each with the board row its frame sits on.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    set_index
        The take (storyboard set), 1-based.
    take_count
        Takes on the desk for the episode.

    Returns
    -------
    list[tuple[int, dict[str, Any]]]
        ``(row, beat)`` in beat order; the row is the beat's frame's ``board_row``
        (its position in the take when the frame is not found).
    """

    grouped = beats_by_take(
        spine, episode=episode, take_count=max(take_count, set_index)
    )
    beats = grouped[set_index - 1] if set_index <= len(grouped) else []
    frames = {
        str(frame.get("frame_id")): frame
        for frame in frames_by_set(spine, episode=episode).get(set_index, [])
    }
    out: list[tuple[int, dict[str, Any]]] = []
    for position, beat in enumerate(beats, start=1):
        frame = frames.get(str(beat.get("frame_id")))
        raw = frame.get("board_row") if frame else None
        row = int(raw) if isinstance(raw, int) or str(raw or "").isdigit() else position
        out.append((row, beat))
    return out


def director_message(
    *,
    take_id: str,
    episode: int,
    beats: Sequence[tuple[int, Mapping[str, Any]]],
    note: str,
) -> str:
    """The creator's note as the director reads it, scoped to one take's beats.

    Parameters
    ----------
    take_id
        ``t1``, ``t2`` ...
    episode
        Episode ordinal.
    beats
        :func:`take_beats`.
    note
        What is wrong with the board, in the operator's words.

    Returns
    -------
    str
        One message for ``POST /v1/spines/{id}/director/turns``.
    """

    named = ", ".join(f"{beat.get('beat_id')} (row {row})" for row, beat in beats)
    return (
        f"Storyboard take {take_id[1:]} of episode {episode} is drawn wrong. It draws these beats: {named}. "
        f"What is wrong with the drawing: {note.strip()} "
        "Change what happens in those beats (their motion) so the take is drawn that way, and change nothing "
        "else. Do not redraw anything: the take is redrawn right after your change."
    )


def take_patch(
    turn: Mapping[str, Any], *, allowed_beat_ids: set[str]
) -> tuple[dict[str, Any], list[str]]:
    """The beat edits a director turn is waiting to apply to this take, and what was left out.

    Steps already ``done`` were applied by the server (before the script gate);
    ``needs_confirmation`` and ``pending`` ``patch_story`` steps are what the
    creator's tap would apply. Only beats of this take are kept: an edit to
    another take would redraw a board nobody asked about. Lines, titles and
    summaries are left out the same way (a board does not draw the words).

    Parameters
    ----------
    turn
        ``DramaDirectorTurnResponse`` JSON.
    allowed_beat_ids
        The take's beat ids.

    Returns
    -------
    tuple[dict[str, Any], list[str]]
        ``{"beats": [...]}`` (empty dict when nothing is left) and one line per thing left out.
    """

    beats: dict[str, str] = {}
    dropped: list[str] = []
    for step in turn.get("steps") or []:
        if not isinstance(step, Mapping) or step.get("tool") != PATCH_TOOL:
            continue
        if step.get("state") not in {"needs_confirmation", "pending"}:
            continue
        args = step.get("input") or {}
        for beat in args.get("beats") or []:
            if not isinstance(beat, Mapping):
                continue
            beat_id = str(beat.get("beat_id") or "")
            intent = str(beat.get("motion_intent") or "").strip()
            if not beat_id or not intent:
                continue
            if beat_id not in allowed_beat_ids:
                dropped.append(f"{beat_id} is not in this take")
                continue
            beats[beat_id] = intent
        if args.get("dialogue_lines"):
            dropped.append("line wording (a board does not draw the words)")
        if args.get("title") or args.get("summary"):
            dropped.append("the episode's title/summary")
    patch = (
        {
            "beats": [
                {"beat_id": beat_id, "motion_intent": intent}
                for beat_id, intent in beats.items()
            ]
        }
        if beats
        else {}
    )
    return patch, dropped


def _short(text: Any, size: int = 220) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= size else flat[: size - 3] + "..."


def beat_change_lines(
    before: Sequence[tuple[int, Mapping[str, Any]]],
    after: Mapping[str, Any],
) -> list[str]:
    """Row by row, what the take's beats say now against what they said before the note.

    Parameters
    ----------
    before
        :func:`take_beats` before the edit.
    after
        The spine after the edit.

    Returns
    -------
    list[str]
        Printable lines; empty when no beat of the take changed.
    """

    now = {
        str(beat.get("beat_id")): beat
        for beat in after.get("beats") or []
        if isinstance(beat, Mapping)
    }
    lines: list[str] = []
    for row, beat in before:
        beat_id = str(beat.get("beat_id"))
        was = str(beat.get("motion_intent") or "")
        is_now = str((now.get(beat_id) or {}).get("motion_intent") or "")
        if is_now and is_now != was:
            lines.append(f"  row {row} ({beat_id}):")
            lines.append(f"    was: {_short(was)}")
            lines.append(f"    now: {_short(is_now)}")
    return lines


def row_change_lines(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Board rows whose shot changed in the redraw, as ``row N: old -> new``.

    Parameters
    ----------
    before, after
        One take's frames before and after the redraw.

    Returns
    -------
    list[str]
        Printable lines; empty when every row reads the same.
    """

    old: dict[int, ShotRow] = {row.row: row for row in shot_rows(before)}
    lines: list[str] = []
    for row in shot_rows(after):
        was = old.get(row.row)
        if was is None or was.one_line() != row.one_line():
            lines.append(
                f"  was {was.one_line() if was else f'row {row.row}: (new row)'}"
            )
            lines.append(f"  now {row.one_line()}")
    return lines


__all__ = [
    "note_refused_by_server",
    "regenerate_note_support",
    "DIRECTOR_STAGE",
    "beat_change_lines",
    "director_message",
    "regenerate_takes_note",
    "row_change_lines",
    "take_beats",
    "take_patch",
]
