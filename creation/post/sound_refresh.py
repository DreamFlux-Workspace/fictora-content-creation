"""Ask the server to re-do an already-delivered episode's sound under today's sound rules.

The server (``POST /v1/spines/{spine_id}/episodes/{episode_id}/sound-refresh``) owns every
decision: which sounds are no longer laid, which are turned down, whether the room sound
changes, what it costs. Nothing is filmed again and no voice is re-rendered. This module
only asks, prints the server's plan in plain words, and, on ``--yes``, applies it, follows
the server until the new episode is ready and downloads it into the desk as a new numbered
file. It keeps no state of its own.

Without ``--yes`` a ``dry_run`` goes out: free, nothing changes. It is never run on its own:
only when the human asks to fix an older episode's sound.

The apply is keyed on the dry run's ``plan_id`` so a new rules version or a changed plan is a new
request, never a replay of an older answer. A finished episode file the server holds is downloaded
whenever it is not on the desk yet, so a timed-out or failed download is recovered by running again.
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
#: Exit code when the sound was not re-done (also: still running when ``--wait`` ran out).
NOT_APPLIED = 2
PLAN_CHANGED = "sound_refresh_plan_changed"

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


def _error_code(body: Any) -> str | None:
    error = body.get("error") if isinstance(body, Mapping) else None
    return str(error["code"]) if isinstance(error, Mapping) and error.get("code") else None


def _fresh_plan(body: Any) -> Mapping[str, Any] | None:
    """The plan view the server put in a ``sound_refresh_plan_changed`` error, wherever it nested it."""

    if not isinstance(body, Mapping):
        return None
    error = body.get("error")
    details = error.get("details") if isinstance(error, Mapping) else None
    candidates = [details.get("plan") if isinstance(details, Mapping) else None, details]
    candidates += [body.get("plan"), body.get("details"), body]
    for candidate in candidates:
        if isinstance(candidate, Mapping) and "takes" in candidate:
            return candidate
    return None


def _deliver(desk: Path, episode: int, url: str, out: TextIO) -> tuple[Path | None, bool]:
    """The server's finished file on the desk: ``(path, downloaded_now)``; never over an existing file."""

    from creation.post.episode_name import next_episode_path
    from creation.post.server_finish import ServerFinishError, _saved_video, download_file

    folder = desk / f"ep{episode:02d}" / "takes"
    have = _saved_video(folder, episode, url)
    if have is not None:
        return have, False
    try:
        saved = download_file(
            url,
            next_episode_path(folder, desk, episode, "draft"),
            kind="video",
            meta={"episode": episode, "video_url": url, "source": "sound-refresh"},
        )
    except ServerFinishError as exc:
        print(f"!! the new episode is ready on the server but did not download: {exc}", file=out)
        return None, False
    return saved, True


def _report_file(desk: Path, episode: int, view: Mapping[str, Any], out: TextIO) -> int:
    url = str(view["video_url"])
    saved, fresh = _deliver(desk, episode, url, out)
    summary = view.get("summary") or "sound re-done"
    if saved is None:
        print(f"Done: {summary}. New episode: {url}", file=out)
        return NOT_APPLIED
    print(f"Done: {summary}. " + ("New episode file: " if fresh else "Already on the desk: ") + f"{saved}", file=out)
    return 0


def _follow(
    run: Any, route: str, view: Mapping[str, Any], *, desk: Path, episode: int,
    wait_seconds: float, pause: Sleep, out: TextIO,
) -> int:  # fmt: skip
    """Poll the server until the refresh leaves ``planned``/``running``, then show how it ended."""

    again = f"fictora-produce sound-refresh --desk {desk} --episode {episode}"
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    polled = False
    while view.get("state") in ("planned", "running"):
        if polled and time.monotonic() >= deadline:
            print(
                f"Still being re-done on the server after {wait_seconds:.0f} s; the old episode plays until "
                f"it is done. Run again later to pick up the new file: {again}",
                file=out,
            )
            return NOT_APPLIED
        print(f"Episode {episode}: the server is re-doing the sound ({view.get('state')})…", file=out)
        if wait_seconds > 0:
            pause(POLL_SECONDS)
        status, view = run.get_optional(route)
        polled = True
        if status >= 400 or not isinstance(view, Mapping):
            print(
                f"!! could not read progress ({_error_text(status, view)}). The fix was accepted and the server "
                f"keeps working on it; the old episode still plays until it finishes. Run again later: {again}",
                file=out,
            )
            return NOT_APPLIED
    state_name = view.get("state")
    if state_name == "done" and view.get("video_url"):
        return _report_file(desk, episode, view, out)
    if state_name == "nothing_to_change":
        print(view.get("reason") or "Nothing to change.", file=out)
        return 0
    print(f"!! {view.get('reason') or state_name}. The old episode still plays.", file=out)
    return NOT_APPLIED


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
        ``0`` planned, applied (file on the desk), or nothing to change; :data:`NOT_APPLIED` when it did
        not run, failed, is still running after ``--wait``, or its file could not be downloaded.
    """

    from creation import episode_commands as ec
    from creation.post.server_finish import _desk_episode_id

    out = out or sys.stdout
    pause = sleep or _sleep
    desk, state, run = ec._desk_session(desk.expanduser().resolve())
    route = _route(state.spine_id or "", _desk_episode_id(desk, episode))
    follow = {"desk": desk, "episode": episode, "wait_seconds": wait_seconds, "pause": pause, "out": out}
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
        plan_state = plan.get("state")
        if plan_state == "running":
            print(f"Episode {episode}: a sound fix is already running on the server.", file=out)
            if not yes:
                print(
                    "Nothing new was started. To follow it and download the new episode when it is done: "
                    f"fictora-produce sound-refresh --desk {desk} --episode {episode} --yes",
                    file=out,
                )
                return 0
            return _follow(run, route, plan, **follow)
        if plan_state in ("unavailable", "nothing_to_change", "failed", "done"):
            print(plan.get("reason") or plan.get("summary") or str(plan_state), file=out)
            if plan_state in ("nothing_to_change", "done") and plan.get("video_url"):
                return _report_file(desk, episode, plan, out)
            return 0 if plan_state == "nothing_to_change" else NOT_APPLIED
        for line in plan_lines(plan, episode):
            print(line, file=out)
        if not yes:
            print(
                "Not applied: nothing was changed or spent. To apply: "
                f"fictora-produce sound-refresh --desk {desk} --episode {episode} --yes",
                file=out,
            )
            return 0
        plan_id = plan.get("plan_id")
        status, view = run.post_optional(
            route,
            {"dry_run": False, "plan_id": plan_id},
            idempotency_key=f"{run.prefix}-sound-refresh-ep{episode}-{plan_id or uuid.uuid4().hex[:8]}",
        )
        if status == 409 and _error_code(view) == PLAN_CHANGED:
            print("The plan changed since it was shown, so nothing was applied. The new plan:", file=out)
            for line in plan_lines(_fresh_plan(view) or {}, episode):
                print(line, file=out)
            print(
                "Nothing was changed or spent. If the new plan is fine, run again with --yes: "
                f"fictora-produce sound-refresh --desk {desk} --episode {episode} --yes",
                file=out,
            )
            return NOT_APPLIED
        if status >= 400 or not isinstance(view, Mapping):
            print(f"!! not applied ({_error_text(status, view)}). The old episode still plays.", file=out)
            return NOT_APPLIED
        return _follow(run, route, view, **follow)
    finally:
        run.client.close()
