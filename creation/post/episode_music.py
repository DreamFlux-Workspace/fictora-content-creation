"""``fictora-produce music``: Remove, New or Add music on one finished episode, priced first.

The server (``GET``/``POST /v1/spines/{spine_id}/episodes/{episode_id}/music``) owns all of it:
what the episode plays now, which changes it allows, the price, the plan, the run and the new
cut. The kit keeps nothing. It prints the server's answer, sends the operator's choice as a plan
first (free, ``dry_run``), and only with ``--yes`` sends that plan's ``plan_id`` to apply it,
waits for the change and downloads the new cut the usual way (``finish``, the next
``_draft_vN_`` file; the old file is kept).

``add`` is New music on an episode that plays no music; the server's action is ``new``.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TextIO

from creation.harness.http_util import envelope_code, older_server
from creation.music_feeling import _error_text

#: Exit code when nothing was applied (refused, not possible here, or the server said no).
NOT_APPLIED = 2
#: What the operator can ask for; ``add`` is the server's ``new`` on an episode with no music.
Action = Literal["remove", "new", "add"]
PLAN_CHANGED = "episode_music_plan_changed"
OLDER_SERVER = (
    "This server can't change a finished episode's music yet. Nothing was changed."
)


def route(spine_id: str, episode_id: str) -> str:
    return f"/v1/spines/{spine_id}/episodes/{episode_id}/music"


def _price(price: Any) -> str:
    price = price if isinstance(price, Mapping) else {}
    tokens = int(price.get("tokens") or 0)
    if not tokens:
        return "free"
    return f"{tokens} tokens (${float(price.get('usd') or 0.0):.2f})"


def _command(desk: Path, episode: int) -> str:
    return f"fictora-produce music --desk {desk} --episode {episode}"


def status_lines(view: Mapping[str, Any], desk: Path, episode: int) -> list[str]:
    """What the episode plays now, its latest change, and each action it allows with its price."""

    playing = view.get("delivered") == "music"
    lines = [
        f"Episode {episode} music: "
        + ("music is playing." if playing else "no music is playing.")
    ]
    latest = view.get("latest")
    if isinstance(latest, Mapping):
        what = {"remove": "Remove music", "new": "New music"}.get(
            str(latest.get("action")), "A music change"
        )
        tail = (
            f": {latest['reason']}"
            if latest.get("state") == "failed" and latest.get("reason")
            else ""
        )
        lines.append(f"Latest change: {what}, {latest.get('state')}{tail}.")
    command = _command(desk, episode)
    new_price = _price(view.get("new_music_price"))
    can_add = bool(view.get("can_add"))
    options = []
    if view.get("can_remove"):
        options.append(f"  remove: take the music out (free). {command} remove")
    if can_add:
        verb, what = (
            ("new", "replace it with new music") if playing else ("add", "add music")
        )
        options.append(
            f'  {verb}: {what} ({new_price}). {command} {verb} [--feeling "words"]'
        )
    lines += (
        ["You can:", *options]
        if options
        else ["Nothing can change on this episode's music now."]
    )
    if not can_add:
        reason = view.get("unavailable_reason") or "the server says it can't here"
        lines.append(f"{'New' if playing else 'Add'} music is not possible: {reason}")
    lines.append(
        "Each one shows its plan and price first; nothing is changed or spent without --yes."
    )
    return lines


def _refusal(view: Mapping[str, Any], action: Action) -> str | None:
    """Why the episode can't take this action, from the server's own flags (``None`` when it can)."""

    playing = view.get("delivered") == "music"
    if action == "remove" and not view.get("can_remove"):
        return "This episode plays no music, so there is nothing to take out."
    if action == "add" and playing:
        return "This episode already has music. Use `new` to replace it, or `remove` to take it out."
    if action in ("new", "add") and not view.get("can_add"):
        return str(
            view.get("unavailable_reason")
            or "The server says new music can't be made for this episode."
        )
    return None


def plan_lines(plan: Mapping[str, Any]) -> list[str]:
    """The server's plan in plain words: what changes, the price, how long, and its warnings."""

    eta = int(plan.get("eta_seconds") or 0)
    lines = [
        f"  What changes: {plan.get('what_changes')}",
        f"  Price: {_price(plan.get('price'))}; "
        + (f"takes about {eta} s." if eta else "done at once."),
    ]
    lines += [f"  Note: {warning}" for warning in plan.get("warnings") or []]
    return lines


def _body(
    action: Action, feeling: str | None, *, plan_id: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"action": "remove" if action == "remove" else "new"}
    if feeling is not None:
        body["feeling"] = {"kind": "own_words", "text": feeling}
    if plan_id is None:
        body["dry_run"] = True
    else:
        body.update(dry_run=False, plan_id=plan_id)
    return body


def change_music(
    run: Any,
    *,
    desk: Path,
    spine_id: str,
    episode_id: str,
    episode: int,
    action: Action | None,
    feeling: str | None,
    yes: bool,
    out: TextIO,
) -> int:
    """Show the episode's music, or plan (and with ``yes`` apply) one change, then download the new cut.

    Returns
    -------
    int
        ``0`` shown, planned, or applied with the new cut down; :data:`NOT_APPLIED` when nothing was
        applied; :data:`~creation.post.music_rescore.REBUILD_NOT_DONE` when the change ran but the
        new cut is not on this laptop yet (the old file stays).
    """

    from creation.post.music_rescore_new import (
        download_changed_episode,
        wait_for_change,
    )

    path = route(spine_id, episode_id)
    status, view = run.get_optional(path)
    if older_server(status, view):
        print(OLDER_SERVER, file=out)
        return NOT_APPLIED
    if status >= 400 or not isinstance(view, Mapping):
        print(
            f"!! could not read episode {episode}'s music ({_error_text(status, view)}).",
            file=out,
        )
        return NOT_APPLIED
    if action is None:
        for line in status_lines(view, desk, episode):
            print(line, file=out)
        return 0
    if feeling is not None and action == "remove":
        print("!! --feeling goes with new or add only. Nothing was changed.", file=out)
        return NOT_APPLIED
    refused = _refusal(view, action)
    if refused:
        print(f"Not possible: {refused} Nothing was changed.", file=out)
        return NOT_APPLIED

    key = f"{run.prefix}-episode-music-{episode_id}"
    status, plan = run.post_optional(
        path,
        _body(action, feeling),
        idempotency_key=f"{key}-plan-{uuid.uuid4().hex[:8]}",
    )
    if status >= 400 or not isinstance(plan, Mapping):
        print(
            f"!! the server did not plan it: {_error_text(status, plan)} Nothing was changed.",
            file=out,
        )
        return NOT_APPLIED
    word = {"remove": "Remove music", "new": "New music", "add": "Add music"}[action]
    print(
        f"{word} on episode {episode}: the server's plan (nothing is changed or spent yet)",
        file=out,
    )
    for line in plan_lines(plan):
        print(line, file=out)
    if plan.get("state") == "nothing_to_change" or not plan.get("plan_id"):
        print("Nothing to change. Nothing was spent.", file=out)
        return 0
    if not yes:
        again = f"{_command(desk, episode)} {action}" + (
            f' --feeling "{feeling}"' if feeling else ""
        )
        print(
            f"Not applied: nothing was changed or spent. Applying costs {_price(plan.get('price'))}. "
            f"To apply: {again} --yes",
            file=out,
        )
        return 0

    plan_id = str(plan["plan_id"])
    # A fresh key each time: the server charges once per plan_id, and a failed change may be asked again with it.
    status, change = run.post_optional(
        path,
        _body(action, feeling, plan_id=plan_id),
        idempotency_key=f"{key}-apply-{plan_id}-{uuid.uuid4().hex[:8]}",
    )
    if status == 409 and envelope_code(change) == PLAN_CHANGED:
        print(
            "!! not applied: the plan changed on the server since it was read (the episode or the price moved). "
            "Nothing was spent. Run the same command again to see the new plan.",
            file=out,
        )
        return NOT_APPLIED
    if status >= 400 or not isinstance(change, Mapping):
        print(
            f"!! not applied: {_error_text(status, change)} Nothing was spent.",
            file=out,
        )
        return NOT_APPLIED
    print(f"Applied: {change.get('state')}.", file=out)
    latest: Mapping[str, Any] = change
    if change.get("state") not in ("done", "failed"):
        latest = wait_for_change(run, spine_id, episode_id, change.get("job_id"), out)
    head = "rebuilt without music" if action == "remove" else "rebuilt with new music"
    note = {
        "remove": "Music removed on the server",
        "new": "New music written for this episode on the server",
        "add": "Music added on the server",
    }[action]
    return download_changed_episode(
        desk, episode, latest, run=run, spine_id=spine_id, out=out, head=head, note=note
    )


def run_episode_music(
    desk: Path,
    *,
    episode: int,
    action: Action | None = None,
    feeling: str | None = None,
    yes: bool = False,
    out: TextIO | None = None,
) -> int:
    """``fictora-produce music``: the episode's music and what can change, or one change, planned first.

    Returns
    -------
    int
        See :func:`change_music`.
    """

    from creation import episode_commands as ec
    from creation.post.server_finish import _desk_episode_id

    desk, state, run = ec._desk_session(desk.expanduser().resolve())
    try:
        return change_music(
            run,
            desk=desk,
            spine_id=state.spine_id or "",
            episode_id=_desk_episode_id(desk, episode),
            episode=episode,
            action=action,
            feeling=feeling,
            yes=yes,
            out=out or sys.stdout,
        )
    finally:
        run.client.close()
