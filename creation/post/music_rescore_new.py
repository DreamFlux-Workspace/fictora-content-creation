"""``rescore-music`` when the server answers with new music: print its plan, wait, download the new cut.

While music is on for the show (fictora-drama, 4 Nov 2026), the server's re-score
is New music: each finished episode gets a track written from its own scenes,
laid under the whole episode (``kind`` ``new_music``). The server plans it, prices
it and runs it; the kit only prints the plan, waits for each episode's change
(``GET /v1/spines/{spine_id}/episodes/{episode_id}/music``) and then downloads the
finished episode the usual way (``finish``). Nothing is decided here.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

#: How long the kit waits for one episode's new music (seconds); it takes about a minute.
WAIT_SECONDS = 6 * 60.0
#: Seconds between two reads of an episode's change.
POLL_SECONDS = 10.0
#: Seconds between "still writing" lines.
PROGRESS_EVERY_SECONDS = 30.0
#: The changes still running, and the ones that ended.
_RUNNING = frozenset({"running"})
_ENDED = frozenset({"done", "failed"})

#: The seams tests replace.
_sleep = time.sleep
_clock = time.monotonic


def _rows(answer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = [row for row in answer.get("new_music") or [] if isinstance(row, Mapping)]
    return sorted(rows, key=lambda row: int(row.get("episode_ordinal") or 0))


def _change(row: Mapping[str, Any]) -> Mapping[str, Any]:
    change = row.get("change")
    return change if isinstance(change, Mapping) else {}


def _price(change: Mapping[str, Any]) -> tuple[int, float]:
    price = change.get("price") or {}
    return int(price.get("tokens") or 0), float(price.get("usd") or 0.0)


def price_words(answer: Mapping[str, Any]) -> str:
    """The plan's price in tokens and dollars, e.g. ``16 tokens ($0.08)``.

    Parameters
    ----------
    answer
        The ``music-rescore`` response (``kind`` ``new_music``).

    Returns
    -------
    str
        The price of every planned episode together.
    """

    planned = [
        _change(row) for row in _rows(answer) if _change(row).get("state") == "planned"
    ]
    tokens = sum(_price(change)[0] for change in planned)
    usd = sum(_price(change)[1] for change in planned)
    return f"{tokens} tokens (${usd:.2f})"


def planned_count(answer: Mapping[str, Any]) -> int:
    """How many episodes the plan would give new music.

    Parameters
    ----------
    answer
        The ``music-rescore`` response.

    Returns
    -------
    int
        ``0`` for a library re-score or when every episode is skipped.
    """

    if answer.get("kind") != "new_music":
        return 0
    return sum(1 for row in _rows(answer) if _change(row).get("state") == "planned")


def plan_lines(answer: Mapping[str, Any]) -> list[str]:
    """The server's new-music plan (or what started) in plain words, one line per episode.

    Parameters
    ----------
    answer
        The ``music-rescore`` response (``kind`` ``new_music``).

    Returns
    -------
    list[str]
        Operator lines.
    """

    rows = _rows(answer)
    lines = ["  Episodes:" if rows else "  Episodes: none finished in scope."]
    for row in rows:
        label = f"ep{int(row.get('episode_ordinal') or 0):02d}"
        if row.get("skipped"):
            lines.append(f"    {label}: skipped: {row['skipped']}")
            continue
        change = _change(row)
        tokens, usd = _price(change)
        state = change.get("state")
        tail = {"running": " - started", "done": " - done"}.get(str(state), "")
        lines.append(
            f"    {label}: new music written from this episode's own scenes, laid under the whole episode; "
            f"{tokens} tokens (${usd:.2f}), about a minute{tail}"
        )
    cost = answer.get("cost") or {}
    spent = float(cost.get("spent_usd") or 0.0)
    lines.append(
        f"  Cost: ${float(cost.get('new_cues_usd') or 0.0):.2f} in all; nothing is filmed"
        + (f"; spent by this call ${spent:.2f}" if spent else "")
    )
    if answer.get("message"):
        lines.append(f"  Server says: {answer['message']}")
    return lines


def wait_for_change(
    run: Any, spine_id: str, episode_id: str, job_id: str | None, out: TextIO
) -> Mapping[str, Any]:
    """Wait for one episode's new music to finish, printing a line now and then.

    Parameters
    ----------
    run
        Server session.
    spine_id
        The story.
    episode_id
        The episode.
    job_id
        The change the apply started.
    out
        Where the lines go.

    Returns
    -------
    Mapping[str, Any]
        The episode's latest change (``done``, ``failed``, or still ``running`` after :data:`WAIT_SECONDS`).
    """

    path = f"/v1/spines/{spine_id}/episodes/{episode_id}/music"
    started = last_line = _clock()
    latest: Mapping[str, Any] = {"state": "running"}
    while True:
        status, view = run.get_optional(path)
        if (
            status == 200
            and isinstance(view, Mapping)
            and isinstance(view.get("latest"), Mapping)
        ):
            seen = view["latest"]
            if job_id is None or seen.get("job_id") == job_id:
                latest = seen
        if latest.get("state") in _ENDED:
            return latest
        now = _clock()
        if now - started >= WAIT_SECONDS:
            return latest
        if now - last_line >= PROGRESS_EVERY_SECONDS:
            print(
                f"    still writing the new music ({int(now - started)} s)...", file=out
            )
            last_line = now
        _sleep(POLL_SECONDS)


def follow_new_music(
    desk: Path,
    answer: Mapping[str, Any],
    *,
    run: Any,
    spine_id: str,
    episode: int | None,
    out: TextIO,
) -> int:
    """After an apply: wait for each episode's new music, then download its new cut, in episode order.

    Parameters
    ----------
    desk
        Series desk.
    answer
        The applied ``music-rescore`` response (``kind`` ``new_music``).
    run
        Server session.
    spine_id
        The story.
    episode
        The episode asked for; ``None`` for the whole show.
    out
        Where the lines go.

    Returns
    -------
    int
        ``0`` when every started episode's file was rebuilt (or there was none here);
        :data:`~creation.post.music_rescore.REBUILD_NOT_DONE` otherwise.
    """

    from creation.ops.notes import append_run_note
    from creation.post.finish import book
    from creation.post.music_rescore import REBUILD_NOT_DONE, rebuild_episode

    started = [
        row for row in _rows(answer) if _change(row).get("state") in _RUNNING | _ENDED
    ]
    spent = float((answer.get("cost") or {}).get("spent_usd") or 0.0)
    if spent > 0:
        first = min(
            (int(row.get("episode_ordinal") or 1) for row in started),
            default=episode or 1,
        )
        book(desk, episode=first, usd=spent, stream=out, unit="music-rescore")
    if started:
        print(
            "Your episode files on this laptop (new music, in episode order):", file=out
        )
    worst = 0
    for row in started:
        number = int(row.get("episode_ordinal") or 1)
        change = _change(row)
        latest = wait_for_change(
            run, spine_id, str(row.get("episode_id")), change.get("job_id"), out
        )
        if latest.get("state") != "done":
            reason = latest.get("reason") or "it is still being written"
            print(
                f"  ep{number:02d}: !! NOT rebuilt, the old file stays: {reason}",
                file=out,
            )
            print(
                f"    Run `fictora-produce finish --desk {desk} --episode {number}` again in a few minutes.",
                file=out,
            )
            worst = REBUILD_NOT_DONE
            continue
        rebuilt = rebuild_episode(desk, number, [], run=run, spine_id=spine_id, out=out)
        head = {
            "rebuilt": "rebuilt with new music",
            "nothing_to_rebuild": "nothing to rebuild",
            "not_rebuilt": "!! NOT rebuilt, the old file stays",
        }[rebuilt.state]
        print(f"  ep{number:02d}: {head}", file=out)
        for line in rebuilt.lines:
            print(f"    {line}", file=out)
        if rebuilt.state == "not_rebuilt":
            worst = REBUILD_NOT_DONE
        run_dir = desk / f"ep{number:02d}"
        if (run_dir / "run-notes.md").is_file():
            name = (
                rebuilt.file.name if rebuilt.file else rebuilt.state.replace("_", " ")
            )
            append_run_note(
                run_dir,
                f"Music re-scored with new music written from the episode's scenes; kit file {name}",
            )
    if started:
        print(
            "  To take the music off again: Remove music (free) in the app. Reels and final copies made before "
            "keep the old music: make the reel again and approve the new draft when the human says it is final.",
            file=out,
        )
    return worst


__all__: Sequence[str] = (
    "follow_new_music",
    "plan_lines",
    "planned_count",
    "price_words",
    "wait_for_change",
)
