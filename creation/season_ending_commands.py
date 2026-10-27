"""Ending a series: ask the server whether it can end at an episode, then apply the human's pick.

Founder, 27 Oct 2026: a creator who asks to end the series early talks it over
before anything is written. The server reads the story's open threads and says
whether it can close at that episode, suggests a natural last episode when it
cannot yet (never before episode 12), and the human decides: end there, end
here anyway, or keep going. The kit decides nothing: ``ending --episode N``
prints the server's line and options word for word, and ``ending
--last-episode M`` sends the pick. A thin call, like ``cast-exit``.

Founder, 27 Oct 2026: the kit never raises an ending on its own (only when
the human asks), and a series that ended can always be picked back up:
``resume`` asks the server to reopen it, and writing continues as normal.

Founder, 27 Oct 2026 (fictora-drama #1116): when the human ends the series, the server asks once how the
last episode should land ("How should the last episode land?": a proper ending, or a cliffhanger). The
check carries that question as ``ending_question``; ``ending --episode N`` prints it after the options, and
``ending --last-episode M --style closed|open`` sends the answer as ``ending_style``. Without ``--style``
the kit prints the server's question and sends nothing. An older server without the question ends as before.

Routes (fictora-drama early-ending PR): ``GET /v1/spines/{id}/season/ending-check?end_at=N``
and ``POST /v1/spines/{id}/season/end`` with ``last_episode`` and
``expected_spine_version``; ``POST /v1/spines/{id}/season/resume`` with
``expected_spine_version`` reopens an ended series.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from creation import episode_commands as ec

_OLDER_SERVER = (
    "this Drama API has no season ending route yet (an older deploy); nothing was sent. "
    "It needs the fictora-drama early-ending PR deployed."
)


def _refusal(status: int, answer: Any) -> ec.CommandStopped | None:
    if status == 404 and isinstance(answer, Mapping) and "error" not in answer:
        return ec.CommandStopped(_OLDER_SERVER)
    if status >= 400 or not isinstance(answer, Mapping):
        error = answer.get("error") if isinstance(answer, Mapping) else None
        message = (
            str(error.get("message")) if isinstance(error, Mapping) else str(answer)
        )
        return ec.CommandStopped(f"Refused: {message}")
    return None


#: The answers ``--style`` takes, as the server names them (``ending_style``).
ENDING_STYLES = ("closed", "open")


def _print_ending_question(question: Any, out: Any) -> bool:
    """Print the server's "How should the last episode land?" and its answers; ``False`` when there is none."""

    if not isinstance(question, Mapping) or not question.get("line"):
        return False
    print(f"sokii: {question['line']}", file=out)
    for option in question.get("options") or []:
        if isinstance(option, Mapping):
            print(
                f"  --style {option.get('ending_style')}: {_option_label(option)}",
                file=out,
            )
    return True


def _option_label(option: Any) -> str:
    if isinstance(option, Mapping):
        return str(option.get("label") or option.get("text") or "")
    return str(option)


def run_ending(
    desk: Path,
    *,
    episode: int | None = None,
    last_episode: int | None = None,
    style: str | None = None,
    out: Any = None,
) -> dict[str, Any]:
    """Ask whether the series can end at an episode, or end it at the human's pick. Spends nothing.

    Parameters
    ----------
    desk
        Series desk with a story.
    episode
        The episode the human wants to end on: prints the server's answer.
    last_episode
        The episode the human picked as the last one: the server makes it the finale.
    style
        The human's answer to the server's "How should the last episode land?" (``closed`` or ``open``),
        sent as ``ending_style`` with ``last_episode``. Omitted: the kit prints the server's question and sends
        nothing, unless the server asks none (an older deploy), when it ends as before.
    out
        Text stream.

    Returns
    -------
    dict[str, Any]
        The server's answer, as sent.

    Raises
    ------
    CommandStopped
        Neither or both of ``episode`` and ``last_episode``, a server refusal,
        or an older server without the routes. Nothing changed.
    """

    if (episode is None) == (last_episode is None):
        raise ec.CommandStopped(
            "pass --episode N (ask whether it can end there) or --last-episode M (the human's pick)"
        )
    chosen = episode if episode is not None else last_episode
    if chosen is None or chosen < 1:
        raise ec.CommandStopped("the episode is a number: 1 or more")
    if style is not None and (episode is not None or style not in ENDING_STYLES):
        raise ec.CommandStopped("--style is closed or open, sent with --last-episode M")
    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    _, state, run = ec._desk_session(desk)
    refreshed: dict[str, Any] | None = None
    spine_path = f"/v1/spines/{quote(state.spine_id or '', safe='')}/season"
    try:
        if episode is not None:
            status, answer = run.get_optional(
                f"{spine_path}/ending-check?end_at={episode}"
            )
        else:
            if style is None:
                # The server's own question first (free); nothing is sent until the human answers it.
                check_status, check = run.get_optional(
                    f"{spine_path}/ending-check?end_at={last_episode}"
                )
                if check_status < 400 and isinstance(check, Mapping):
                    if _print_ending_question(check.get("ending_question"), out):
                        print(
                            f"(nothing sent: paste this to the human; their answer goes to "
                            f"`ending --last-episode {last_episode} --style closed|open`)",
                            file=out,
                        )
                        return dict(check)
            spine = run.spine(state.spine_id or "")
            status, answer = run.post_optional(
                f"{spine_path}/end",
                {
                    "last_episode": last_episode,
                    "expected_spine_version": spine["spine_version"],
                    **({"ending_style": style} if style is not None else {}),
                },
            )
            # The story as the server now holds it, whatever the answer carries.
            refreshed = run.spine(state.spine_id or "") if status < 400 else None
    finally:
        run.client.close()
    refused = _refusal(status, answer)
    if refused is not None:
        raise refused
    if episode is not None:
        ec._save_desk_json(desk, f"ending-check-ep{episode:02d}", answer)
        if answer.get("line"):
            print(f"sokii: {answer['line']}", file=out)
        for number, option in enumerate(answer.get("options") or [], start=1):
            print(f"{number}. {_option_label(option)}", file=out)
        asks = _print_ending_question(answer.get("ending_question"), out)
        pick = (
            "`ending --last-episode M --style closed|open`"
            if asks
            else "`ending --last-episode M`"
        )
        print(
            f"(paste this to the human as printed; their pick goes to {pick})",
            file=out,
        )
    else:
        ec._save_desk_json(desk, "spine", refreshed)
        landing = f" (ending_style: {style})" if style is not None else ""
        print(
            f"done: episode {last_episode} is the series' last episode, written as its finale{landing}.",
            file=out,
        )
    return dict(answer)


def run_resume(desk: Path, *, out: Any = None) -> dict[str, Any]:
    """Pick an ended series back up: the server reopens it so more episodes can be written. Spends nothing.

    Parameters
    ----------
    desk
        Series desk with a story.
    out
        Text stream.

    Returns
    -------
    dict[str, Any]
        The server's answer, as sent.

    Raises
    ------
    CommandStopped
        A server refusal, or an older server without the route. Nothing changed.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    _, state, run = ec._desk_session(desk)
    spine_path = f"/v1/spines/{quote(state.spine_id or '', safe='')}/season"
    refreshed: dict[str, Any] | None = None
    try:
        spine = run.spine(state.spine_id or "")
        status, answer = run.post_optional(
            f"{spine_path}/resume",
            {"expected_spine_version": spine["spine_version"]},
        )
        refreshed = run.spine(state.spine_id or "") if status < 400 else None
    finally:
        run.client.close()
    refused = _refusal(status, answer)
    if refused is not None:
        raise refused
    ec._save_desk_json(desk, "spine", refreshed)
    line = answer.get("line") if isinstance(answer, Mapping) else None
    if line:
        print(f"sokii: {line}", file=out)
    print(
        "done: the series is open again; write the next episode as normal (`brief`).",
        file=out,
    )
    return dict(answer)
