"""``fictora-produce hook-line``: list or choose an episode's on-screen hook line (free).

The writer drafts up to three hook lines per episode
(``episode_summaries[].hook_line_options``) and selects one
(``hook_line_selected``). The creator changes it in the app's opening picker;
the operator changes it here, on the same route the app uses:
``PATCH /v1/spines/{spine_id}/episodes/{episode_id}/opening`` with
``{spine_version, hook_line: {kind: option, index} | {kind: custom, text} | {kind: off}}``
(fictora-drama ``DramaEpisodeOpeningEditRequest``). Nothing is redrawn or
filmed; it spends nothing.

* ``--list`` prints the options from the saved spine (no call).
* ``--pick K`` selects option K (1-based, as listed); ``--text "..."`` sets the
  operator's own line; ``--off`` shows none.
* A stale ``spine_version`` (409 ``spine_version_conflict``) is read again and
  sent once more.
* An older server (no route: a bare 404 or 405), or one that refuses custom
  text, gets the pick recorded on the desk (``shared/hook-line.json``):
  ``finish`` and ``join`` use it, and the run says the app still shows the
  server's pick.

``finish`` burns the hook line on a portrait show's first take, and a
letterbox show's title block carries it for the whole video
(:mod:`creation.post.letterbox`).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, TextIO

from creation.harness.http_util import api_error_text
from creation.post.desk import saved_spine, spine_body
from creation.post.letterbox import (
    HOOK_LINE_FILE,
    clear_desk_hook_line,
    desk_hook_line,
    record_desk_hook_line,
)

#: The server's opening route (the app's opening picker uses it too).
OPENING_ROUTE = "/v1/spines/{spine_id}/episodes/{episode_id}/opening"
#: 404 codes the route answers on purpose; any other 404 means the server predates it.
KNOWN_NOT_FOUND = frozenset(
    {
        "episode_not_found",
        "hook_line_option_not_found",
        "opening_option_not_found",
        "spine_not_found",
    }
)
#: Stale-version codes: the server's, and the name the kit's plan routes use.
STALE_CODES = frozenset({"spine_version_conflict", "spine_version_stale"})


class _Api(Protocol):
    """The slice of :class:`creation.harness.session.DramaApiRunSession` used here."""

    def patch_optional(self, path: str, body: dict[str, Any]) -> tuple[int, Any]: ...

    def spine(self, spine_id: str) -> dict[str, Any]: ...


def hook_line_options(
    spine: Mapping[str, Any], episode: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The episode's drafted hook lines and its selection (``{}`` when none)."""

    from creation.spine_view import episode_summary

    summary = episode_summary(spine_body(spine), episode)
    options = [
        dict(o)
        for o in summary.get("hook_line_options") or []
        if isinstance(o, Mapping)
    ]
    selected = summary.get("hook_line_selected")
    return options, dict(selected) if isinstance(selected, Mapping) else {}


def list_lines(desk: Path, spine: Mapping[str, Any], episode: int) -> list[str]:
    """What ``--list`` prints: each option numbered, the selected one marked, and a desk pick if any."""

    options, selected = hook_line_options(spine, episode)
    rows = [
        f"Hook lines for episode {episode} (from the saved spine; nothing was sent):"
    ]
    if not options:
        rows.append("  (the writer drafted none for this episode; set one with --text)")
    for index, option in enumerate(options):
        mark = (
            "*"
            if selected.get("kind") == "option" and selected.get("index") == index
            else " "
        )
        style = f" [{option['style']}]" if option.get("style") else ""
        rows.append(f" {mark}{index + 1}. {option.get('text', '')}{style}")
    kind = selected.get("kind")
    if kind == "custom":
        rows.append(f" * own line: {selected.get('text', '')}")
    elif kind == "off":
        rows.append(" * off: no hook line shows")
    picked = desk_hook_line(desk, episode)
    if picked:
        rows.append(
            f"Desk pick (finish and join use it; the app still shows the server's): {picked['text']!r} "
            f"({HOOK_LINE_FILE})"
        )
    rows.append(
        f'Choose: hook-line --desk {desk} --episode {episode} --pick K | --text "..." | --off'
    )
    return rows


def _error_code(answer: Any) -> str:
    error = answer.get("error") if isinstance(answer, Mapping) else None
    return str(error.get("code") or "") if isinstance(error, Mapping) else ""


def choose_hook_line(
    desk: Path,
    run: _Api,
    spine: Mapping[str, Any],
    *,
    episode: int,
    choice: Mapping[str, Any],
    out: TextIO,
) -> dict[str, Any] | None:
    """Send the choice to the opening route; on an older server record it on the desk.

    Parameters
    ----------
    desk
        Series desk.
    run
        Open API session.
    spine
        The spine as just read from the server.
    episode
        Episode ordinal.
    choice
        ``{kind: option, index}`` (0-based), ``{kind: custom, text}`` or ``{kind: off}``.
    out
        Text stream.

    Returns
    -------
    dict[str, Any] | None
        The route's answer, or ``None`` when the pick was recorded on the desk instead.

    Raises
    ------
    RuntimeError
        Any other refusal (an option that does not exist, a job open on the story, ...).
    """

    from creation.spine_view import episode_id_for

    body = spine_body(spine)
    spine_id = str(body.get("spine_id") or "")
    path = OPENING_ROUTE.format(
        spine_id=spine_id, episode_id=episode_id_for(body, episode)
    )
    for attempt in (1, 2):
        status, answer = run.patch_optional(
            path,
            {"spine_version": body.get("spine_version"), "hook_line": dict(choice)},
        )
        if 200 <= status < 300:
            return dict(answer) if isinstance(answer, Mapping) else {}
        code = _error_code(answer)
        if status == 409 and code in STALE_CODES and attempt == 1:
            # The spine moved (another edit landed): read its version again and send the same choice once more.
            body = spine_body(run.spine(spine_id))
            continue
        older = (status == 404 and code not in KNOWN_NOT_FOUND) or status == 405
        custom_refused = choice.get("kind") == "custom" and status == 422
        if older or custom_refused:
            text = _choice_text(body, episode, choice) or ""
            why = (
                "this Drama API has no hook-line route yet (an older deploy)"
                if older
                else f"this Drama API does not take your own hook line yet ({api_error_text(answer)})"
            )
            saved = record_desk_hook_line(
                desk, episode, {**dict(choice), "text": text}, why=why
            )
            shown = repr(text) if text else "no hook line"
            print(
                f"Recorded on the desk, not on the server: {why}. finish and join use {shown} "
                f"(`{saved.relative_to(desk)}`); the app will still show the server's pick.",
                file=out,
            )
            return None
        raise RuntimeError(
            f"the server refused the hook line (HTTP {status}): {api_error_text(answer)}"
        )
    raise AssertionError("unreachable")


def _choice_text(
    body: Mapping[str, Any], episode: int, choice: Mapping[str, Any]
) -> str | None:
    if choice.get("kind") == "custom":
        return str(choice.get("text") or "").strip() or None
    if choice.get("kind") == "option":
        options, _ = hook_line_options(body, episode)
        index = int(choice.get("index", -1))
        if not 0 <= index < len(options):
            raise ValueError(
                f"episode {episode} has {len(options)} hook line option(s); there is no option {index + 1} "
                "(`hook-line --list` shows them)"
            )
        return str(options[index].get("text") or "").strip() or None
    return None


def _read_spine(run: _Api, desk: Path, episode: int) -> dict[str, Any]:
    """Read the spine from the server (free) and save it where the episode flow keeps it."""

    from creation.orchestrate import save_spine_snapshot
    from creation.post.desk import spine_id

    body = spine_body(run.spine(spine_id(desk)))
    save_spine_snapshot(desk, episode, body)
    return body


def run_hook_line(
    desk: Path,
    *,
    episode: int,
    list_only: bool = False,
    pick: int | None = None,
    text: str | None = None,
    off: bool = False,
    out: TextIO | None = None,
    api: _Api | None = None,
) -> int:
    """``hook-line --desk D --episode N [--list | --pick K | --text "..." | --off]`` (free).

    Returns
    -------
    int
        ``0``.
    """

    from creation.post.desk import open_api

    stream: TextIO = out if out is not None else sys.stdout
    desk = desk.expanduser().resolve()
    asked = [list_only, pick is not None, text is not None, off]
    if sum(bool(a) for a in asked) != 1:
        raise ValueError(
            'hook-line needs exactly one of --list, --pick K, --text "...", --off'
        )
    if list_only:
        found = saved_spine(desk, episode)
        if found is None:
            raise FileNotFoundError(
                f"no spine saved for episode {episode} on the desk; run `spine --refresh` first"
            )
        for row in list_lines(desk, found[0], episode):
            print(row, file=stream)
        return 0
    if pick is not None and pick < 1:
        raise ValueError(
            "--pick counts from 1, as `hook-line --list` numbers the options"
        )
    if text is not None and not text.strip():
        raise ValueError("--text needs the words of the hook line")
    choice: dict[str, Any] = (
        {"kind": "option", "index": pick - 1}
        if pick is not None
        else {"kind": "custom", "text": " ".join(text.split())}
        if text is not None
        else {"kind": "off"}
    )
    session = open_api(desk, episode) if api is None else None
    run: _Api = api if api is not None else session  # type: ignore[assignment]
    try:
        spine = _read_spine(run, desk, episode)
        if choice["kind"] == "option":
            # An option that does not exist is refused before anything is sent.
            _choice_text(spine, episode, choice)
        answer = choose_hook_line(
            desk, run, spine, episode=episode, choice=choice, out=stream
        )
        if answer is not None:
            _read_spine(run, desk, episode)
            cleared = clear_desk_hook_line(desk, episode)
            shown = answer.get("hook_line")
            print(
                f"Hook line for episode {episode}: "
                + (repr(shown) if shown else "off (no hook line shows)")
                + (
                    " (the desk's earlier pick is dropped: the server holds it now)"
                    if cleared
                    else ""
                ),
                file=stream,
            )
            for warning in answer.get("warnings") or []:
                message = (
                    warning.get("message") if isinstance(warning, Mapping) else warning
                )
                print(f"note: {message}", file=stream)
    finally:
        if session is not None:
            session.client.close()
    print(
        "Next: finish (and join) put it on: a portrait show's first take opens on it; "
        "a letterbox show's title block carries it for the whole video.",
        file=stream,
    )
    return 0


__all__ = [
    "OPENING_ROUTE",
    "choose_hook_line",
    "hook_line_options",
    "list_lines",
    "run_hook_line",
]
