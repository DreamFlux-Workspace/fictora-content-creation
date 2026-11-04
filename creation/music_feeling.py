"""What one episode's music should feel like: the server's question, shown and answered from the desk.

The server (``GET``/``POST /v1/spines/{spine_id}/episodes/{episode_id}/music-feeling``) owns the
question, its four options and their order (two suggested feelings, No music, Something else…),
and keeps the answer per episode. The kit keeps nothing: it prints the options as 1 to 4 in the
server's order and sends the picked option's ``kind`` (and ``index`` or the operator's words) back.

Filming never waits on it. Before a film the question is printed with how to answer; an episode
nobody answered gets the music the server picks for the show. A server without the route (404)
is skipped quietly.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, TextIO

import httpx

from creation.harness.http_util import envelope_code, older_server

#: Exit code when the answer was not saved.
NOT_SAVED = 2
VERSION_CONFLICT = "spine_version_conflict"
NOT_ASKED = "music_feeling_not_asked"
EPISODE_FINISHED = "music_feeling_episode_finished"

OLDER_SERVER = "This server doesn't ask about music yet. Nothing was changed."


class _Api(Protocol):
    def get_optional(self, path: str) -> tuple[int, Any]: ...

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]: ...


def route(spine_id: str, episode_id: str) -> str:
    return f"/v1/spines/{spine_id}/episodes/{episode_id}/music-feeling"


def _error_text(status: int, body: Any) -> str:
    if isinstance(body, Mapping):
        error = body.get("error")
        message = (
            error.get("message") if isinstance(error, Mapping) else None
        ) or body.get("detail")
        if message:
            return f"HTTP {status}: {message}"
    return f"HTTP {status}: {body}"


def _options(view: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [o for o in view.get("options") or [] if isinstance(o, Mapping)]


def answer_words(view: Mapping[str, Any]) -> str:
    """The saved answer as the operator reads it, using the server's own option labels."""

    answer = view.get("answer")
    if not isinstance(answer, Mapping):
        return "not answered yet, so the server picks the music it uses for this show"
    if answer.get("kind") == "server_pick":
        return "music, in a feeling the server picks (New music was asked for with no feeling)"
    for option in _options(view):
        if option.get("kind") != answer.get("kind"):
            continue
        if option.get("kind") == "suggested" and option.get("index") != answer.get(
            "index"
        ):
            continue
        if option.get("kind") == "own_words":
            return f'{option.get("label")} "{answer.get("text")}"'
        return str(option.get("label"))
    return str(answer.get("kind"))


def question_lines(view: Mapping[str, Any], episode: int) -> list[str]:
    """The server's question, its options numbered 1 to 4 in the server's order, and the saved answer."""

    lines = [f"Music for episode {episode}: {view.get('line')}"]
    for number, option in enumerate(_options(view), start=1):
        description = option.get("description")
        lines.append(
            f"  {number}. {option.get('label')}"
            + (f": {description}" if description else "")
        )
    lines.append(f"Answer: {answer_words(view)}.")
    return lines


def answer_body(
    view: Mapping[str, Any], choice: int, words: str | None
) -> dict[str, Any] | str:
    """The ``POST`` body for option ``choice`` (1-based, the server's order), or why it can't be sent."""

    options = _options(view)
    if not 1 <= choice <= len(options):
        return f"pick 1 to {len(options)}"
    option = options[choice - 1]
    kind = option.get("kind")
    if kind == "own_words":
        if not (words or "").strip():
            return (
                f'{choice} needs the words too, in quotes: {choice} "warm and hopeful"'
            )
        answer: dict[str, Any] = {"kind": kind, "text": words.strip()}
    elif words is not None:
        return f"only {_own_words_number(options)} takes your own words"
    elif kind == "suggested":
        answer = {"kind": kind, "index": option.get("index")}
    else:
        answer = {"kind": kind}
    return {"spine_version": view.get("spine_version"), "answer": answer}


def _own_words_number(options: list[Mapping[str, Any]]) -> int:
    return next(
        (n for n, o in enumerate(options, start=1) if o.get("kind") == "own_words"),
        len(options),
    )


def how_to_answer(desk: Path, episode: int, view: Mapping[str, Any]) -> str:
    command = f"fictora-produce music-feeling --desk {desk} --episode {episode}"
    own = _own_words_number(_options(view))
    picks = "|".join(str(n) for n in range(1, own))
    return f'To answer: {command} {picks}, or {command} {own} "your own words".'


def film_lines(desk: Path, run: _Api, spine_id: str, episode: int) -> list[str]:
    """The question to print before a film; empty on an older server, a finished episode or a failed read.

    Never stops the film. Read this before the film reads the spine: the first read of the question
    writes the episode's two feelings onto the spine, which moves its version.
    """

    from creation.post.server_finish import _desk_episode_id

    try:
        status, view = run.get_optional(
            route(spine_id, _desk_episode_id(desk, episode))
        )
    except httpx.HTTPError:
        return []
    if status >= 400 or not isinstance(view, Mapping) or not view.get("answerable"):
        return []
    return [
        *question_lines(view, episode),
        how_to_answer(desk, episode, view),
        "Filming goes ahead either way; the answer can change until the episode is finished.",
    ]


def _post(
    run: _Api, path: str, view: Mapping[str, Any], choice: int, words: str | None
) -> tuple[int, Any] | str:
    body = answer_body(view, choice, words)
    if isinstance(body, str):
        return body
    return run.post_optional(path, body)


def ask(
    run: _Api,
    *,
    desk: Path,
    spine_id: str,
    episode_id: str,
    episode: int,
    choice: int | None,
    words: str | None,
    out: TextIO,
) -> int:
    """Print the question, or send the answer (one refetch and retry on a stale version)."""

    path = route(spine_id, episode_id)
    status, view = run.get_optional(path)
    if older_server(status, view):
        print(OLDER_SERVER, file=out)
        return 0
    if status >= 400 or not isinstance(view, Mapping):
        print(
            f"!! could not read the question ({_error_text(status, view)}).", file=out
        )
        return NOT_SAVED
    if choice is None:
        for line in question_lines(view, episode):
            print(line, file=out)
        if view.get("answerable"):
            print(how_to_answer(desk, episode, view), file=out)
        else:
            print("Episode finished: its music can't change now.", file=out)
        return 0
    sent = _post(run, path, view, choice, words)
    if (
        not isinstance(sent, str)
        and sent[0] == 409
        and envelope_code(sent[1])
        in (
            VERSION_CONFLICT,
            NOT_ASKED,
        )
    ):
        status, view = run.get_optional(path)
        if status >= 400 or not isinstance(view, Mapping):
            print(f"!! not saved ({_error_text(status, view)}).", file=out)
            return NOT_SAVED
        sent = _post(run, path, view, choice, words)
    if isinstance(sent, str):
        print(f"!! not saved: {sent}.", file=out)
        return NOT_SAVED
    status, saved = sent
    if status == 409 and envelope_code(saved) == EPISODE_FINISHED:
        print(
            f"Episode {episode} is finished, so its music can't change now. Nothing was changed.",
            file=out,
        )
        return NOT_SAVED
    if older_server(status, saved):
        print(OLDER_SERVER, file=out)
        return 0
    if status >= 400 or not isinstance(saved, Mapping):
        print(f"!! not saved ({_error_text(status, saved)}).", file=out)
        return NOT_SAVED
    print("Saved on the server.", file=out)
    for line in question_lines(saved, episode):
        print(line, file=out)
    return 0


def run_music_feeling(
    desk: Path,
    *,
    episode: int,
    choice: int | None = None,
    words: str | None = None,
    out: TextIO | None = None,
) -> int:
    """``fictora-produce music-feeling``: the server's question, or the operator's answer to it.

    Returns
    -------
    int
        ``0`` shown, saved, or skipped on an older server; :data:`NOT_SAVED` when an answer was not saved.
    """

    from creation import episode_commands as ec
    from creation.post.server_finish import _desk_episode_id

    desk, state, run = ec._desk_session(desk.expanduser().resolve())
    try:
        return ask(
            run,
            desk=desk,
            spine_id=state.spine_id or "",
            episode_id=_desk_episode_id(desk, episode),
            episode=episode,
            choice=choice,
            words=words,
            out=out or sys.stdout,
        )
    finally:
        run.client.close()
