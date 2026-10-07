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

**Setup line** (a letterbox show's white title line, fictora-drama #628):
``--setup-line "..."`` (at most 60 characters) sets the episode's own line on
the same route (``title_line: {kind: custom, text}``) and
``--default-setup-line`` clears it (``{kind: default}``: the series title).
It goes as its own send, after any hook-line choice. A server without the
field (it refuses it, has no route, or answers without ``title_line``) gets
the line kept on the desk (``shared/setup-line.json``) for finish, join and
reel; a later one the server stores drops it.
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
    SETUP_LINE_FILE,
    SETUP_LINE_MAX_CHARS,
    clear_desk_hook_line,
    clear_desk_setup_line,
    desk_hook_line,
    desk_setup_line,
    record_desk_hook_line,
    record_desk_setup_line,
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
#: The longest hook line the server takes (fictora-drama ``HOOK_LINE_MAX_CHARS``); longer is a 422.
HOOK_LINE_MAX_CHARS = 120
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
    from creation.spine_view import episode_summary

    own = episode_summary(spine_body(spine), episode).get("title_line")
    kept = desk_setup_line(desk, episode)
    if kept is not None:
        shown = repr(kept["text"]) if kept["kind"] == "custom" else "the series title"
        rows.append(
            f"Setup line kept on the desk (finish and join use it; the app still shows the server's): {shown} "
            f"({SETUP_LINE_FILE})"
        )
    elif own:
        rows.append(f"Setup line (letterbox white line): {own!r}")
    rows.append(
        f'Choose: hook-line --desk {desk} --episode {episode} --pick K | --text "..." | --off; '
        'a letterbox setup line: --setup-line "..." | --default-setup-line'
    )
    return rows


def gate_hook_line_lines(
    desk: Path, spine: Mapping[str, Any] | None, episode: int
) -> list[str]:
    """What the pitch card and the script gate print about the hook line (founder, 7 Oct 2026).

    The text that will be burned (the desk's pick from ``hook-line`` wins over
    the server's selection, as ``finish`` and ``join`` read it), where it goes,
    and the writer's options, so the producer approves the words, not a yes/no
    (L-20261005-11: "Rescue sees the knife first" gave the twist away). A
    selected ``twist`` option gets a plain warning: the reveal comes after the
    commitment (house rule H5).
    """

    from creation.post.hook_overlay import delivery_format

    body = spine_body(spine) if isinstance(spine, Mapping) else {}
    if not body:
        return [
            "  Hook line text: no spine on the desk yet; "
            f"`fictora-produce hook-line --desk {desk} --episode {episode} --list` once the outline exists"
        ]
    options, selected = hook_line_options(body, episode)
    where = (
        "title band, the whole episode"
        if delivery_format(body) == "letterbox"
        else "on screen over the first ~3 s"
    )
    picked = desk_hook_line(desk, episode)
    style = ""
    if picked is not None:
        text = str(picked.get("text") or "") if picked.get("kind") != "off" else ""
        source = "the desk's pick from `hook-line`"
        if picked.get("kind") == "option" and isinstance(picked.get("index"), int):
            index = picked["index"]
            style = (
                str(options[index].get("style") or "")
                if 0 <= index < len(options)
                else ""
            )
    elif selected.get("kind") == "off":
        text, source = "", "turned off"
    elif selected.get("kind") == "custom":
        text, source = str(selected.get("text") or ""), "your own line"
    else:
        index = selected.get("index")
        text = str(selected.get("text") or "")
        source = (
            f"the writer's option {index + 1}"
            if isinstance(index, int)
            else "the server's selection"
        )
        if isinstance(index, int) and 0 <= index < len(options):
            style = str(options[index].get("style") or "")
    text = " ".join(text.split())
    rows = [
        f"  Hook line text ({where}): {text!r}"
        + (f" [{style}]" if style else "")
        + f" ({source})"
        if text
        else f"  Hook line text: none ({source if selected or picked else 'none drafted'})"
    ]
    if style == "twist":
        rows.append(
            "  !! The hook line is the twist option: check it does not name the reveal or the ending "
            "(the reveal comes after the commitment); pick the stakes or rule line if it does."
        )
    if options:
        rows.append("  Hook line options:")
        for index, option in enumerate(options):
            label = f" [{option['style']}]" if option.get("style") else ""
            rows.append(f"    {index + 1}. {option.get('text', '')}{label}")
    rows.append(
        f'  Change it: fictora-produce hook-line --desk {desk} --episode {episode} --pick K | --text "..." | --off'
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


def choose_setup_line(
    desk: Path,
    run: _Api,
    spine: Mapping[str, Any],
    *,
    episode: int,
    choice: Mapping[str, Any],
    out: TextIO,
) -> dict[str, Any] | None:
    """Send the episode's setup line to the opening route; on an older server keep it on the desk.

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
        ``{kind: custom, text}`` or ``{kind: default}``.
    out
        Text stream.

    Returns
    -------
    dict[str, Any] | None
        The route's answer, or ``None`` when the line was kept on the desk instead.

    Raises
    ------
    RuntimeError
        Any other refusal (an unknown episode, a job open on the story, ...).
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
            {"spine_version": body.get("spine_version"), "title_line": dict(choice)},
        )
        code = _error_code(answer)
        if status == 409 and code in STALE_CODES and attempt == 1:
            body = spine_body(run.spine(spine_id))
            continue
        stored = (
            200 <= status < 300
            and isinstance(answer, Mapping)
            and "title_line" in answer
        )
        if stored:
            return dict(answer)
        # The line was checked here (1-60 characters), so a 422 is a server without the field.
        older = (
            (200 <= status < 300)
            or status == 422
            or (status == 404 and code not in KNOWN_NOT_FOUND)
            or status == 405
        )
        if older:
            why = "this Drama API does not keep an episode's setup line yet (before fictora-drama #628)"
            saved = record_desk_setup_line(desk, episode, dict(choice), why=why)
            shown = (
                repr(choice.get("text"))
                if choice.get("kind") == "custom"
                else "the series title"
            )
            print(
                f"Recorded on the desk, not on the server: {why}. finish, join and reel use {shown} "
                f"(`{saved.relative_to(desk)}`); the app will still show the server's.",
                file=out,
            )
            return None
        raise RuntimeError(
            f"the server refused the setup line (HTTP {status}): {api_error_text(answer)}"
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
    setup_line: str | None = None,
    default_setup_line: bool = False,
    out: TextIO | None = None,
    api: _Api | None = None,
) -> int:
    """``hook-line --desk D --episode N [--list | --pick K | --text "..." | --off] [--setup-line "..."]`` (free).

    ``setup_line`` / ``default_setup_line`` set or clear a letterbox episode's
    own setup line (:func:`choose_setup_line`), alone or after a hook-line choice.

    Returns
    -------
    int
        ``0``.
    """

    from creation.post.desk import open_api

    stream: TextIO = out if out is not None else sys.stdout
    desk = desk.expanduser().resolve()
    asked = [list_only, pick is not None, text is not None, off]
    if setup_line is not None and default_setup_line:
        raise ValueError("--setup-line or --default-setup-line, not both")
    setup: dict[str, Any] | None = None
    if setup_line is not None:
        words = " ".join(setup_line.split())
        if not words:
            raise ValueError("--setup-line needs the words of the setup line")
        if len(words) > SETUP_LINE_MAX_CHARS:
            raise ValueError(
                f"--setup-line is {len(words)} characters; a setup line is at most {SETUP_LINE_MAX_CHARS} "
                "(the server refuses longer). Use fewer words."
            )
        setup = {"kind": "custom", "text": words}
    elif default_setup_line:
        setup = {"kind": "default"}
    if sum(bool(a) for a in asked) > 1 or (not any(asked) and setup is None):
        raise ValueError(
            'hook-line needs exactly one of --list, --pick K, --text "...", --off '
            '(and/or --setup-line "..." / --default-setup-line)'
        )
    if list_only and setup is not None:
        raise ValueError("--list sends nothing; give --setup-line on its own")
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
    if text is not None and len(" ".join(text.split())) > HOOK_LINE_MAX_CHARS:
        raise ValueError(
            f"--text is {len(' '.join(text.split()))} characters; a hook line is at most "
            f"{HOOK_LINE_MAX_CHARS} (the server refuses longer). Use fewer words."
        )
    choice: dict[str, Any] | None = (
        {"kind": "option", "index": pick - 1}
        if pick is not None
        else {"kind": "custom", "text": " ".join(text.split())}
        if text is not None
        else {"kind": "off"}
        if off
        else None
    )
    session = open_api(desk, episode) if api is None else None
    run: _Api = api if api is not None else session  # type: ignore[assignment]
    try:
        spine = _read_spine(run, desk, episode)
        if choice is not None and choice["kind"] == "option":
            # An option that does not exist is refused before anything is sent.
            _choice_text(spine, episode, choice)
        answer = (
            choose_hook_line(
                desk, run, spine, episode=episode, choice=choice, out=stream
            )
            if choice is not None
            else None
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
        if setup is not None:
            if choice is not None:
                # The hook-line send moved the spine's version: read it again first.
                spine = _read_spine(run, desk, episode)
            said = choose_setup_line(
                desk, run, spine, episode=episode, choice=setup, out=stream
            )
            if said is not None:
                _read_spine(run, desk, episode)
                cleared = clear_desk_setup_line(desk, episode)
                shown = said.get("title_line")
                print(
                    f"Setup line for episode {episode}: "
                    + (
                        repr(shown)
                        if shown
                        else "none of its own (the series title shows)"
                    )
                    + (
                        " (the desk's earlier line is dropped: the server holds it now)"
                        if cleared
                        else ""
                    ),
                    file=stream,
                )
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
    "choose_setup_line",
    "gate_hook_line_lines",
    "hook_line_options",
    "list_lines",
    "run_hook_line",
]
