"""Ask the server to re-do an already-delivered episode's sound under today's sound rules.

The server (``POST /v1/spines/{spine_id}/episodes/{episode_id}/sound-refresh``) owns every
decision: which sounds are no longer laid, which are turned down, whether the room sound
changes, what it costs. Nothing is filmed again and no voice is re-rendered. This module
only asks, prints the server's plan in plain words, and, on ``--yes``, applies it, follows
the server until the new episode is ready and downloads it into the desk as a new numbered
file. It keeps no state of its own.

Without ``--yes`` a ``dry_run`` goes out: free, nothing changes. It is never run on its own:
only when the human asks to fix an older episode's sound.
"""

from __future__ import annotations

import sys
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

#: Seconds between status lines while the server works.
POLL_SECONDS = 15.0
#: How long ``--yes`` waits for the new episode (``--wait``).
DEFAULT_WAIT_SECONDS = 900.0
#: Exit code when the sound was not re-done.
NOT_APPLIED = 2

OLDER_SERVER = "This server can't re-do sound yet. Nothing was changed."

Sleep = Callable[[float], None]
_sleep: Sleep = time.sleep


def _route(spine_id: str, episode_id: str) -> str:
    return f"/v1/spines/{spine_id}/episodes/{episode_id}/sound-refresh"


def _money(value: Any) -> str:
    amount = float(value or 0)
    return "free" if amount <= 0 else f"${amount:.2f}"


def _db(value: Any) -> str:
    return f"{float(value):g} dB"


def _error_text(status: int, body: Any) -> str:
    if isinstance(body, Mapping):
        error = body.get("error")
        message = (
            error.get("message") if isinstance(error, Mapping) else None
        ) or body.get("detail")
        if message:
            return f"HTTP {status}: {message}"
    return f"HTTP {status}: {body}"


def plan_lines(view: Mapping[str, Any], episode: int) -> list[str]:
    """The server's plan in plain words: the summary, each take, the cost."""

    lines = [f"Episode {episode}: {view.get('summary') or 'sound plan'}"]
    for take in view.get("takes") or []:
        if not isinstance(take, Mapping):
            continue
        label = f"  t{take.get('take_index')}"
        if take.get("action") == "keep":
            reason = take.get("reason")
            lines.append(f"{label}: kept as it is" + (f" ({reason})" if reason else ""))
            continue
        parts: list[str] = []
        removed = [str(s) for s in take.get("removed_sounds") or []]
        if removed:
            parts.append("removed " + ", ".join(removed))
        turned = [
            f"{t.get('sound')} {_db(t.get('from_db'))} to {_db(t.get('to_db'))}"
            for t in take.get("turned_down_sounds") or []
            if isinstance(t, Mapping)
        ]
        if turned:
            parts.append("turned down " + ", ".join(turned))
        if take.get("room_changed"):
            parts.append("room sound changed")
        lines.append(f"{label}: " + ("; ".join(parts) or "sound rebuilt, no audible change listed"))
    lines.append(f"Cost: {_money(view.get('cost_usd'))}")
    return lines


def _download(desk: Path, episode: int, url: str, out: TextIO) -> Path | None:
    from creation.post.episode_name import next_episode_path
    from creation.post.server_finish import ServerFinishError, download_file

    folder = desk / f"ep{episode:02d}" / "takes"
    try:
        return download_file(
            url,
            next_episode_path(folder, desk, episode, "draft"),
            kind="video",
            meta={"episode": episode, "video_url": url, "source": "sound-refresh"},
        )
    except ServerFinishError as exc:
        print(f"!! the new episode is ready on the server but did not download: {exc}", file=out)
        return None


def run_sound_refresh(
    desk: Path,
    *,
    episode: int,
    yes: bool = False,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    out: TextIO | None = None,
    sleep: Sleep | None = None,
) -> int:
    """``fictora-produce sound-refresh``: the server's plan, applied only on ``--yes``.

    Returns
    -------
    int
        ``0`` planned, applied, or nothing to change; :data:`NOT_APPLIED` when it did not run.
    """

    from creation import episode_commands as ec
    from creation.post.server_finish import _desk_episode_id

    out = out or sys.stdout
    pause = sleep or _sleep
    desk, state, run = ec._desk_session(desk.expanduser().resolve())
    route = _route(state.spine_id or "", _desk_episode_id(desk, episode))
    try:
        status, plan = run.post_optional(
            route,
            {"dry_run": True},
            idempotency_key=f"{run.prefix}-sound-refresh-ep{episode}-plan-{uuid.uuid4().hex[:8]}",
        )
        if status == 404 and not (isinstance(plan, Mapping) and plan.get("error")):
            print(OLDER_SERVER, file=out)
            return NOT_APPLIED
        if status >= 400 or not isinstance(plan, Mapping):
            print(f"!! the server did not plan it ({_error_text(status, plan)}). Nothing was changed.", file=out)
            return NOT_APPLIED
        if plan.get("state") in ("unavailable", "nothing_to_change", "failed"):
            print(plan.get("reason") or plan.get("summary") or str(plan.get("state")), file=out)
            return 0 if plan.get("state") == "nothing_to_change" else NOT_APPLIED
        for line in plan_lines(plan, episode):
            print(line, file=out)
        if not yes:
            print(
                "Not applied: nothing was changed or spent. To apply: "
                f"fictora-produce sound-refresh --desk {desk} --episode {episode} --yes",
                file=out,
            )
            return 0
        status, view = run.post_optional(
            route,
            {"dry_run": False},
            idempotency_key=f"{run.prefix}-sound-refresh-ep{episode}-apply",
        )
        if status >= 400 or not isinstance(view, Mapping):
            print(f"!! not applied ({_error_text(status, view)}). The old episode still plays.", file=out)
            return NOT_APPLIED
        deadline = time.monotonic() + max(wait_seconds, 0.0)
        while view.get("state") in ("planned", "running"):
            if time.monotonic() >= deadline:
                print(
                    f"Still being re-done on the server after {wait_seconds:.0f} s; the old episode plays until "
                    f"it is done. Check again with: fictora-produce sound-refresh --desk {desk} "
                    f"--episode {episode} --yes",
                    file=out,
                )
                return 0
            print(f"Episode {episode}: the server is re-doing the sound ({view.get('state')})…", file=out)
            pause(POLL_SECONDS)
            status, view = run.get_optional(route)
            if status >= 400 or not isinstance(view, Mapping):
                print(f"!! could not read progress ({_error_text(status, view)}).", file=out)
                return NOT_APPLIED
        state_name = view.get("state")
        if state_name == "done" and view.get("video_url"):
            saved = _download(desk, episode, str(view["video_url"]), out)
            print(
                f"Done: {view.get('summary') or 'sound re-done'}. "
                + (f"New episode file: {saved}" if saved else f"New episode: {view['video_url']}"),
                file=out,
            )
            return 0 if saved else NOT_APPLIED
        if state_name == "nothing_to_change":
            print(view.get("reason") or "Nothing to change.", file=out)
            return 0
        print(
            f"!! {view.get('reason') or state_name}. The old episode still plays.",
            file=out,
        )
        return NOT_APPLIED
    finally:
        run.client.close()
