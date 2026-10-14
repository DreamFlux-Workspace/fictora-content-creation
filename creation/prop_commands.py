"""Story objects from the desk: list them, redraw one with a note, remove one (L-20261014-1).

The server gives the objects a story turns on (``spine.props``: a carved shop
sign, a ring) one picture each, drawn with the cast plates and approved with
them. The kit had no command for them: the skill told operators to POST
``/props/{id}/redraw`` by hand "then `step` draws it", but ``step`` only draws
at ``ready_cast_enrol`` and sent the key of the plates job that had already
finished, so a hand-rewound desk got that old job back: "Cast drawn", nothing
drawn. These commands do it in one go, never touching the desk's phase:

- ``props``: free, read-only. Each object, its id, where its picture stands
  (none / drawn / stale-redraw-asked / approved) and the local file.
- ``redraw-prop --prop NAME --note "..."``: records the note on the object
  (free; skipped when that note is already there) and runs one picture job
  (``cast/enrol``) under a key of its own per note. The server draws only what
  is owed ($0.30 a picture); the kit prices it first.
- ``remove-prop --prop NAME``: free. Takes an object off the story (refused
  once the episode has a drawn board); its local pictures move to
  ``objects/removed/``.

The picture's yes stays with the cast: ``approve --gate plates``.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from creation import orchestrate as _orchestrate
from creation.board_note import CAST_NOTE_MAX, check_note_length
from creation.episode_commands import (
    STALE_DRAW_CODE,
    CommandStopped,
    _desk_session,
    _hold_for_look,
    _hold_for_pitch,
    _note,
    _note_key,
    _plates_home,
    _save_desk_json,
    run_unit,
)
from creation.harness.http_util import api_error_text
from creation.harness.stages_gated import scene_prompt
from creation.harness.visual_first_ep1 import reuse_generation_body
from creation.ops.floor import record_spend
from creation.ops.folder import next_versioned_path
from creation.orchestrate import save_spine_snapshot
from creation.outdated_boards import outdated_boards_notice
from creation.prices import STILL_USD
from creation.production_state import load_production, save_production
from creation.spine_view import episode_id_for
from creation.desk_media_urls import drawn_cast_rows
from creation.story_objects import (
    object_slug,
    owed_pictures,
    picture_state,
    prop_cards,
)

#: The advice line inside an object redraw's price (founder wording, 14 Oct 2026); never blocks.
REDRAW_COST_ADVICE = (
    "Redrawing adds $0.30 per object to this episode. Objects usually read fine on the board, "
    "so only redraw if the object is wrong for the story."
)


def redraw_price_message(
    *,
    episode: int,
    what: str,
    usd: float,
    command: str,
    again: bool = False,
) -> str:
    """The price an object redraw shows before its yes, in the shape ``step`` prices plates and boards.

    ``Objects for episode N: <what>, about $X.XX. Nothing drawn yet.``, the advice
    line, then ``Human yes, then `<command> --confirm-spend`.``

    Parameters
    ----------
    episode
        The desk's episode.
    what
        What will be drawn.
    usd
        The real total.
    command
        The command to run again with ``--confirm-spend``.
    again
        ``--confirm-spend`` came without this total having been shown.
    """

    lead = "No price shown for this redraw yet. " if again else ""
    return (
        f"{lead}Objects for episode {episode}: {what}, about ${usd:.2f}. Nothing drawn yet.\n"
        f"{REDRAW_COST_ADVICE}\n"
        f"Human yes, then `{command} --confirm-spend`."
    )


#: How long one picture job is watched (as a plate redraw).
PICTURE_DEADLINE_SECONDS = 900.0


# --- Shared ------------------------------------------------------------------------------------


def find_prop(spine: Mapping[str, Any], wanted: str) -> Mapping[str, Any]:
    """The object named ``wanted`` (its name, any case, or its ``prop_id``).

    Parameters
    ----------
    spine
        Spine JSON.
    wanted
        Name or ``prop_id``.

    Returns
    -------
    Mapping[str, Any]
        The prop card.

    Raises
    ------
    CommandStopped
        No such object: the message lists every object's name and id.
    """

    cards = prop_cards(spine)
    key = " ".join(wanted.split()).casefold()
    for card in cards:
        if str(card["prop_id"]) == wanted.strip() or (
            " ".join(str(card.get("name") or "").split()).casefold() == key
        ):
            return card
    if not cards:
        raise CommandStopped(
            "this story has no objects yet (the server gives objects a picture when an episode is written)"
        )
    names = ", ".join(f"{c.get('name')} ({c['prop_id']})" for c in cards)
    raise CommandStopped(
        f"no object {wanted!r} on the story; nothing was sent. The objects are: {names}"
    )


def local_pictures(desk: Path, card: Mapping[str, Any]) -> list[Path]:
    """Every picture of this object saved on the desk (``epNN/plates/object-NAME-epNN-vK``), oldest first."""

    stem = object_slug(card)
    found = [
        path
        for path in desk.glob(f"ep[0-9][0-9]/plates/{stem}-ep[0-9][0-9]-v*.*")
        if path.is_file()
    ]
    return sorted(found, key=lambda p: (p.stat().st_mtime_ns, p.name))


def _error_code(body: Any) -> str:
    if not isinstance(body, Mapping):
        return ""
    for holder in (body.get("error"), body.get("detail"), body):
        if isinstance(holder, Mapping) and holder.get("code"):
            return str(holder["code"])
    return ""


# --- props -------------------------------------------------------------------------------------


def run_props(desk: Path, *, out: Any = None) -> list[dict[str, str]]:
    """List the story's objects: name, id, where the picture stands, the local file. Free, read-only.

    Parameters
    ----------
    desk
        Series desk.
    out
        Text stream.

    Returns
    -------
    list[dict[str, str]]
        One row per object (``name``, ``prop_id``, ``picture``, ``file``).
    """

    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    rows: list[dict[str, str]] = []
    for card in prop_cards(spine):
        prop_id = str(card["prop_id"])
        files = local_pictures(desk, card)
        rows.append(
            {
                "name": str(card.get("name") or prop_id),
                "prop_id": prop_id,
                "picture": picture_state(spine, prop_id),
                "file": str(files[-1]) if files else "",
                "from_episode": str(card.get("intro_episode_ordinal") or 1),
                "notes": str(len(card.get("creator_notes") or [])),
            }
        )
    if not rows:
        print("No story objects on this story yet. Nothing was sent or paid.", file=out)
        return rows
    print(f"{len(rows)} story object(s) (free, nothing sent):", file=out)
    for row in rows:
        print(
            f"  {row['name']}  ({row['prop_id']})  picture: {row['picture']}  "
            f"from ep {row['from_episode']}  notes: {row['notes']}\n"
            f"    file: {row['file'] or '(none saved on the desk)'}",
            file=out,
        )
    print(
        f'Redraw one: fictora-produce redraw-prop --desk {desk} --prop NAME --note "what is wrong"  '
        f"| remove one: fictora-produce remove-prop --desk {desk} --prop NAME",
        file=out,
    )
    outdated = outdated_boards_notice(spine, desk=desk)
    if outdated:
        print(outdated, file=out)
    return rows


# --- redraw-prop -------------------------------------------------------------------------------


def _price_what(
    name: str,
    cast: list[tuple[str, str]],
    props: list[tuple[str, str]],
    prop_id: str,
) -> tuple[int, float, str]:
    count = len(cast) + len(props)
    usd = round(float(STILL_USD) * count, 2)
    what = f"{count} picture(s): {name} redrawn"
    others = [n for pid, n in props if pid != prop_id] + [n for _, n in cast]
    if others:
        what += f", plus {', '.join(others)} (already owed a picture)"
    return count, usd, what


def run_redraw_prop(
    desk: Path,
    *,
    prop: str,
    note: str,
    fresh: bool = False,
    confirm_spend: bool = False,
    out: Any = None,
) -> Path | None:
    """Correct ONE story object and draw its picture again; nothing current is redrawn.

    ``POST /v1/spines/{id}/props/{prop_id}/redraw`` records the note on the
    object and marks its picture owed (free; skipped when the same note is
    already there and the picture is still owed). Then one picture job,
    ``POST /v1/spines/{id}/cast/enrol``, draws only what the story owes ($0.30
    each; current plates and pictures are reused at $0). Its key is
    ``<desk prefix>-prop-<prop_id>-<note hash>[-fresh]-aN``: one per note, so it
    never collides with the plates step's key and a run cut off mid-job picks
    up the same job. It always asks first: the first run prints
    :func:`redraw_price_message` (the shape ``step`` prices plates and boards
    in, with :data:`REDRAW_COST_ADVICE` inside) with the real total (this
    object plus anything else the story owes a picture) and sends nothing; only ``--confirm-spend``
    after that total was shown draws (founder, 14 Oct 2026). The desk's phase is never changed. The picture is saved
    as a new ``object-NAME-epNN-vK`` file (never over an older one), each
    picture drawn is booked on the ledger, and the human's yes is asked for
    again with the cast (``approve --gate plates``).

    Parameters
    ----------
    desk
        Series desk.
    prop
        The object's name (any case) or ``prop_id``.
    note
        What is wrong with the object, in the creator's words.
    fresh
        Draw again under a job key of its own (``...-fresh-aN``), also when
        this note was already drawn (the note is then sent again to mark the
        picture owed). It changes only the key: the server's redraw body has
        no ``fresh`` field (fictora-drama #786).
    confirm_spend
        The human said yes to the printed total (always needed; the first run
        only asks).
    out
        Text stream.

    Returns
    -------
    Path | None
        The new picture, or ``None`` when nothing was drawn (a price stop, or
        the note was already drawn).

    Raises
    ------
    CommandStopped
        Empty note, unknown object, a failed job, or no new picture after it.
    """

    out = out or sys.stdout
    note = " ".join(note.split())
    if not note:
        raise CommandStopped(
            "--note is required: what is wrong with the object, in your words"
        )
    try:
        check_note_length(note, limit=CAST_NOTE_MAX, command="redraw-prop")
    except ValueError as exc:
        raise CommandStopped(str(exc)) from None
    _hold_for_pitch(desk, "redraw-prop")
    _hold_for_look(desk, "redraw-prop")
    desk, state, run = _desk_session(desk)
    ep = state.episode_ordinal
    digest = hashlib.sha256(_note_key(note).encode()).hexdigest()[:10]
    try:
        spine = run.spine(state.spine_id or "")
        card = find_prop(spine, prop)
        prop_id, name = str(card["prop_id"]), str(card.get("name") or card["prop_id"])
        noted = {
            _note_key(str(n.get("text") or ""))
            for n in card.get("creator_notes") or []
            if isinstance(n, Mapping)
        }
        already = _note_key(note) in noted
        now = picture_state(spine, prop_id)
        if already and now in ("drawn", "approved") and not fresh:
            files = local_pictures(desk, card)
            print(
                f"{name} already carries this note and its picture was drawn with it"
                + (f" ({files[-1]})" if files else "")
                + ". Nothing was sent or paid. To change it again, write a different note.",
                file=out,
            )
            return None
        cast_owed, props_owed = owed_pictures(spine)
        if prop_id not in {pid for pid, _ in props_owed}:
            props_owed = [*props_owed, (prop_id, name)]
        count, usd, what = _price_what(name, cast_owed, props_owed, prop_id)
        price_key = f"prop-{prop_id}-{digest}" + ("-fresh" if fresh else "")
        shown = load_production(desk).drawing_estimates.get(price_key)
        if not confirm_spend or shown is None or abs(shown - usd) > 0.001:
            asked = load_production(desk)
            asked.drawing_estimates[price_key] = usd
            save_production(desk, asked)
            flag = " --fresh" if fresh else ""
            print(
                redraw_price_message(
                    episode=ep,
                    what=what,
                    usd=usd,
                    command=f'fictora-produce redraw-prop --desk {desk} --prop "{name}" --note "{note}"{flag}',
                    again=confirm_spend,
                ),
                file=out,
            )
            return None
        print(
            f"Redrawing {name} with the human's yes: {count} picture(s), ${usd:.2f}.",
            file=out,
        )
        if not already or (fresh and now in ("drawn", "approved")):
            # The server's redraw body is ``{spine_version, text}``: --fresh changes only the job key.
            run.post(
                f"/v1/spines/{state.spine_id}/props/{quote(prop_id, safe='')}/redraw",
                {"spine_version": spine["spine_version"], "text": note},
            )
            spine = run.spine(state.spine_id or "")
        else:
            print(
                f"[object] {name} already carries this note; not added again.",
                file=sys.stderr,
            )
        before_cast, before_props = owed_pictures(spine)
        terminal: dict[str, Any] = {}
        for attempt in (1, 2):
            enrol = reuse_generation_body(
                prompt=scene_prompt(spine, state.prompt),
                spine=spine,
                preset_id=state.preset_id,
                preset_version=state.preset_version,
                video_lane=state.video_lane,
            )
            try:
                terminal = run_unit(
                    desk,
                    run,
                    unit=price_key,
                    path=f"/v1/spines/{state.spine_id}/cast/enrol",
                    body=enrol,
                    video_route=True,
                    deadline_seconds=PICTURE_DEADLINE_SECONDS,
                )
            except CommandStopped as exc:
                if attempt == 2 or STALE_DRAW_CODE not in str(exc):
                    raise
                print(
                    f"[object] the story changed while {name}'s picture was being drawn ({STALE_DRAW_CODE}); "
                    "reading it again and drawing once more (the note is not sent again).",
                    file=sys.stderr,
                )
                spine = run.spine(state.spine_id or "")
                continue
            break
        _save_desk_json(desk, f"prop-redraw-{prop_id}-terminal", terminal)
        spine = run.spine(state.spine_id or "")
        save_spine_snapshot(desk, ep, spine)
        if picture_state(spine, prop_id) not in ("drawn", "approved"):
            raise CommandStopped(
                f"the picture job finished but {name} has no new picture on the story "
                "(the server may have handed back an earlier job). Nothing was booked; "
                "check `fictora-produce props` and tell the Fictora team."
            )
        after_cast, after_props = owed_pictures(spine)
        drawn_props = [
            (pid, n)
            for pid, n in before_props
            if pid not in {p for p, _ in after_props}
        ]
        drawn_cast = [
            (cid, n) for cid, n in before_cast if cid not in {c for c, _ in after_cast}
        ]
        if prop_id not in {pid for pid, _ in drawn_props}:
            drawn_props.insert(0, (prop_id, name))
        path = _save_pictures(desk, spine, ep=ep, props=drawn_props, cast=drawn_cast)[
            prop_id
        ]
    finally:
        run.client.close()
    for pid, n in drawn_props:
        record_spend(
            desk,
            episode=ep,
            usd=float(STILL_USD),
            unit=f"object-redraw:{pid}" if pid == prop_id else f"object-picture:{pid}",
        )
    for cid, _ in drawn_cast:
        record_spend(desk, episode=ep, usd=float(STILL_USD), unit=f"plate-owed:{cid}")
    state = load_production(desk)
    state.drawing_estimates.pop(price_key, None)
    save_production(desk, state)
    total = len(drawn_props) + len(drawn_cast)
    _note(
        desk,
        ep,
        f"object redraw: {name} ({prop_id}) -> `{path.name}`, {total} picture(s), "
        f"${float(STILL_USD) * total:.2f}. Note: {note}",
    )
    print(str(path), file=out)
    print(
        f"{name} redrawn (${float(STILL_USD) * total:.2f}, {total} picture(s) drawn); "
        "the desk's step is where it was.",
        file=out,
    )
    if state.phase == "wait_plates":
        print(
            f"Show {path.name} to the human with the cast plates; one yes covers the cast and objects: "
            f"fictora-produce approve --desk {desk} --gate plates",
            file=out,
        )
    else:
        print(
            f"!! The cast and objects need the human's yes again: the new picture of {name} is not approved, "
            f"and boards refuse an unapproved object. Show {path.name}, then: "
            f"fictora-produce approve --desk {desk} --gate plates --again --path {path} "
            "($0, draws nothing). Boards drawn before now still show the old object; redraw-board the takes it is in.",
            file=out,
        )
    return path


def _save_pictures(
    desk: Path,
    spine: Mapping[str, Any],
    *,
    ep: int,
    props: list[tuple[str, str]],
    cast: list[tuple[str, str]],
) -> dict[str, Path]:
    """Download each picture drawn, as a new version next to the plates; never over an older file."""

    cards = {str(c["prop_id"]): c for c in prop_cards(spine)}
    pictures = {
        (str(a.get("relation_type")), str(a.get("relation_id"))): str(a["url"])
        for a in spine.get("media_assets") or []
        if isinstance(a, Mapping) and a.get("url") and not a.get("stale")
    }
    saved: dict[str, Path] = {}
    folder = desk / f"ep{ep:02d}" / "plates"
    folder.mkdir(parents=True, exist_ok=True)
    plates, plates_ep = _plates_home(desk)
    order = [str(row["cast_id"]) for row in drawn_cast_rows(dict(spine))]
    fetch = httpx.Client(timeout=120.0)
    try:
        for prop_id, _ in props:
            url = pictures.get(("prop", prop_id))
            if url and prop_id in cards:
                saved[prop_id] = _orchestrate.download_to_versioned(
                    fetch, url, folder, f"{object_slug(cards[prop_id])}-ep{ep:02d}"
                )
        for cast_id, _ in cast:
            url = pictures.get(("cast_card", cast_id))
            if url and cast_id in order:
                plates.mkdir(parents=True, exist_ok=True)
                saved[cast_id] = _orchestrate.download_to_versioned(
                    fetch,
                    url,
                    plates,
                    f"plate-ep{plates_ep:02d}-{order.index(cast_id) + 1}",
                )
    finally:
        fetch.close()
    return saved


# --- remove-prop -------------------------------------------------------------------------------


def _episode_has_drawn_board(desk: Path, spine: Mapping[str, Any], ep: int) -> bool:
    wanted = episode_id_for(spine, ep)
    on_story = any(
        isinstance(a, Mapping)
        and a.get("relation_type") == "episode"
        and a.get("relation_id") == wanted
        and a.get("url")
        for a in spine.get("media_assets") or []
    )
    on_desk = any((desk / f"ep{ep:02d}" / "boards").glob("board-*.png"))
    return on_story or on_desk


def run_remove_prop(desk: Path, *, prop: str, out: Any = None) -> list[Path]:
    """Take one story object off the story. Free; refused once the episode has a drawn board.

    Sends ``DELETE /v1/spines/{id}/props/{prop_id}`` (fictora-drama #786) with no
    body and a key of its own; a repeat answers 200 with the story. A
    server that refuses because a drawn board already used the object
    (``409 prop_on_board``) or no longer has it (``404 prop_not_found``) is
    answered in plain words; a server without the route (404/405) says it
    needs the server update. The object's local pictures move to
    ``objects/removed/`` on the desk (never deleted).

    Parameters
    ----------
    desk
        Series desk.
    prop
        The object's name (any case) or ``prop_id``.
    out
        Text stream.

    Returns
    -------
    list[Path]
        Where the local pictures were moved.

    Raises
    ------
    CommandStopped
        Unknown object, a drawn board, or the server's refusal (nothing changed).
    """

    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    ep = state.episode_ordinal
    try:
        spine = run.spine(state.spine_id or "")
        card = find_prop(spine, prop)
        prop_id, name = str(card["prop_id"]), str(card.get("name") or card["prop_id"])
        if _episode_has_drawn_board(desk, spine, ep):
            raise CommandStopped(
                f"episode {ep} already has a drawn board, so {name} stays on the story (the boards were "
                "drawn with it). Nothing was sent. If it is wrong, redraw it instead: "
                f'fictora-produce redraw-prop --desk {desk} --prop "{name}" --note "what is wrong"'
            )
        status, answer = run.delete_optional(
            f"/v1/spines/{state.spine_id}/props/{quote(prop_id, safe='')}",
            None,  # no body (fictora-drama #786); the key makes a repeat delete answer 200
            idempotency_key=f"{state.idempotency_prefix}-prop-remove-{prop_id}",
        )
        code = _error_code(answer)
        if status == 409 and code == "prop_on_board":
            raise CommandStopped(
                f"{name} is already on a drawn board, so it can't be removed (the board was drawn with it). "
                "Nothing changed. If it is wrong, redraw it instead: "
                f'fictora-produce redraw-prop --desk {desk} --prop "{name}" --note "what is wrong"'
            )
        if status == 404 and code == "prop_not_found":
            raise CommandStopped(
                f"{name} ({prop_id}) is no longer on the story (already removed?). Nothing changed; "
                f"see `fictora-produce props --desk {desk}`."
            )
        if status == 400 and code == "idempotency_key_required":
            raise CommandStopped(
                "the server wants an Idempotency-Key on the remove and did not get one; nothing changed. "
                "This is a kit bug: tell the Fictora team."
            )
        if status in (404, 405):
            raise CommandStopped(
                "removing objects needs the server update (this server has no remove route). "
                "Nothing changed or was paid."
            )
        if not 200 <= status < 300:
            raise CommandStopped(
                f"the server did not remove {name}: HTTP {status} {api_error_text(answer)}. Nothing changed."
            )
        # A 200 is the whole updated story (DramaSpineResponse); an older answer is read again.
        spine = (
            dict(answer)
            if isinstance(answer, Mapping) and answer.get("spine_id")
            else run.spine(state.spine_id or "")
        )
        save_spine_snapshot(desk, ep, spine)
    finally:
        run.client.close()
    moved: list[Path] = []
    files = local_pictures(desk, card)
    if files:
        removed = desk / "objects" / "removed"
        removed.mkdir(parents=True, exist_ok=True)
        for path in files:
            target = removed / path.name
            if target.exists():
                target = next_versioned_path(removed, path.stem, path.suffix)
            shutil.move(str(path), str(target))
            moved.append(target)
    _note(
        desk,
        ep,
        f"object removed: {name} ({prop_id}), $0; {len(moved)} local picture(s) moved to objects/removed/.",
    )
    print(f"{name} ({prop_id}) is off the story. Free; nothing was drawn.", file=out)
    if moved:
        print(f"Its picture(s) moved to {moved[0].parent} (not deleted).", file=out)
    return moved


__all__ = [
    "REDRAW_COST_ADVICE",
    "redraw_price_message",
    "find_prop",
    "local_pictures",
    "run_props",
    "run_redraw_prop",
    "run_remove_prop",
]
