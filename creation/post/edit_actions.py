"""``cut-rename``, ``cut-retry`` and ``cut-save``: the server renames, runs again and saves a punchy edit.

Founder, 5 Nov 2026. Every action names the edit by the id ``cut`` prints. The server does the work and
keeps every state: the name, the next attempt and the marked copy. The kit sends one ask, waits where
the server says to, downloads the saved file and prints what happened. Nothing is kept on this laptop
but the saved video.

* ``cut-rename ID NAME`` renames the edit (an empty NAME gives it back its first show's name). Its video
  is not made again.
* ``cut-retry ID`` runs the same edit again when it failed, stalled or its new music did not arrive, and
  waits for it. It never sends the picks again, so it never makes a second edit.
* ``cut-save ID --part N [--no-music]`` saves one part with the Sokii mark,
  ready to post. The edit the app plays stays unmarked.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from creation.harness.http_util import api_error_text
from creation.post import picked_cut
from creation.post.picked_cut import CUT_ROUTE, CutStop, wait_for_cut
from creation.post.server_finish import (
    BUSY_STATUSES,
    DEFAULT_WAIT_SECONDS,
    PROGRESS_EVERY_SECONDS,
    ServerFinishError,
    _body,
    _duration,
    download_file,
)

CUT_ACTION_COMMANDS = frozenset({"cut-rename", "cut-retry", "cut-save"})
RETRY_ROUTE = "/v1/series-cuts/{series_cut_id}/retry"
EXPORT_ROUTE = "/v1/series-cuts/{series_cut_id}/export"
EXPORT_POLL_SECONDS = 4.0
DEFAULT_SAVE_FOLDER = Path("~/Downloads")
PART_REQUIRED = "Name the part to save: cut-save ID --part N."

#: What the operator can do next, for each refusal the server names. The server's own words print first.
NEXT_STEP = {
    "series_cut_not_found": "Check the edit id: cut prints it when the edit is made.",
    "series_cut_not_ready": "Run cut-retry with the same id to wait for it, then save it.",
    "series_cut_part_not_found": "Use a part number that cut printed.",
    "series_cut_no_music_copy": "Leave out --no-music to save it with its music.",
    "series_cut_remake_needed": (
        "Run cut-retry with the same id: the server makes it again with the same picks, name and music. "
        "Then run the same cut-save."
    ),
    "series_cut_show_gone": "",
    "series_cut_export_not_found": "Run the same cut-save again.",
    "invalid_request": "",
    "title_invalid": "",
}

_MISSING_DETAILS = frozenset({"Not Found", "Method Not Allowed"})


def route_missing(response: httpx.Response) -> bool:
    """A server without this route yet: FastAPI's bare 404 or 405 for a path or method it does not know."""

    body = _body(response)
    return (
        response.status_code in (404, 405)
        and isinstance(body, dict)
        and body.get("detail") in _MISSING_DETAILS
    )


def server_answer(
    response: httpx.Response,
    *,
    missing: str,
    nothing: str,
    next_step: Mapping[str, str] = NEXT_STEP,
) -> dict[str, Any]:
    """The JSON body of a good answer, or the plain stop line for a refusal.

    Parameters
    ----------
    response
        The server's answer.
    missing
        The line for a server that does not have the route yet.
    nothing
        What did not happen, closing every stop line ("Nothing was changed.").
    next_step
        What the operator can do next, by the server's refusal code.

    Raises
    ------
    CutStop
        With the plain NOT DONE line when the answer is not a success.
    """

    body = _body(response)
    if route_missing(response):
        raise CutStop(missing)
    if response.status_code in BUSY_STATUSES:
        raise CutStop(
            f"NOT DONE: the server is busy or restarting (HTTP {response.status_code}). Run the same command again "
            f"in a few minutes. {nothing}"
        )
    if response.is_success and isinstance(body, dict):
        return body
    error = picked_cut._error(body)
    code = str(
        (body.get("code") if isinstance(body, dict) else None)
        or error.get("code")
        or ""
    )
    message = str(
        (body.get("message") if isinstance(body, dict) else None)
        or error.get("message")
        or ""
    ).strip()
    if code == "series_cut_part_required":
        raise CutStop(PART_REQUIRED, 2)
    if code in next_step and message:
        step = next_step[code]
        raise CutStop(
            " ".join(part for part in ("NOT DONE:", message, nothing, step) if part)
        )
    raise CutStop(
        f"NOT DONE: the server said no (HTTP {response.status_code}: {api_error_text(body)[:200]}). {nothing}"
    )


def _send(call: Callable[[], httpx.Response]) -> httpx.Response:
    try:
        return call()
    except httpx.HTTPError as exc:
        raise CutStop(
            f"NOT DONE: could not reach the server ({type(exc).__name__}). Check the internet connection, then run "
            "it again."
        ) from exc


def rename_cut(
    cut_id: str,
    name: str,
    *,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """Give an edit a new name on the server; an empty ``name`` gives it back its first show's name.

    Raises
    ------
    CutStop
        With the plain NOT DONE line when the server did not rename it.
    """

    client, base, headers = picked_cut._client(transport, api)
    with client:
        response = _send(
            lambda: client.patch(
                base + CUT_ROUTE.format(series_cut_id=cut_id),
                headers=headers,
                json={"title": name.strip() or None},
            )
        )
    view = server_answer(
        response,
        missing="NOT DONE: the server can't rename an edit yet. Tell the core team; nothing was changed.",
        nothing="Nothing was changed.",
    )
    title = view.get("title")
    kept = "Its video is not made again, so the title card in it keeps the name it was made with."
    if title:
        return [f'The edit {cut_id} is now called "{title}". {kept}']
    return [
        f"The edit {cut_id} has no name of its own now; it goes by its first show's name again. {kept}"
    ]


def retry_cut(
    cut_id: str,
    *,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    progress: Callable[[str], None] = lambda line: print(
        line, file=sys.stderr, flush=True
    ),
) -> list[str]:
    """Run the same edit again by its id (only when it failed or stalled) and wait for it.

    Raises
    ------
    CutStop
        With the plain NOT DONE line when the edit failed again or is still being made at the deadline.
    """

    client, base, headers = picked_cut._client(transport, api)
    with client:
        response = _send(
            lambda: client.post(
                base + RETRY_ROUTE.format(series_cut_id=cut_id), headers=headers
            )
        )
        accepted = server_answer(
            response,
            missing="NOT DONE: the server can't try an edit again by its id yet. Tell the core team; nothing was run.",
            nothing="Nothing was run again.",
        )
        status = str(accepted.get("status") or "")
        if status == "queued":
            progress(
                "Making the same edit again (same picks, same name); no second edit is made."
            )
        elif status == "making":
            progress("This edit is still being made; waiting for it.")
        return wait_for_cut(
            client,
            base,
            headers,
            cut_id,
            picked=None,
            wait_seconds=wait_seconds,
            sleep=sleep,
            clock=clock,
            progress=progress,
        )


def save_cut(
    cut_id: str,
    *,
    part: int,
    music: bool = True,
    folder: Path = DEFAULT_SAVE_FOLDER,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    media_transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    progress: Callable[[str], None] = lambda line: print(
        line, file=sys.stderr, flush=True
    ),
) -> list[str]:
    """Have the server make the Sokii-marked copy of one part, then download it.

    Raises
    ------
    CutStop
        With the plain NOT DONE line when the server did not save it or the file did not download.
    """

    missing = "NOT DONE: the server can't save an edit with the Sokii mark yet. Tell the core team; nothing was saved."
    nothing = "Nothing was saved."
    client, base_url, headers = picked_cut._client(transport, api)
    url = base_url + EXPORT_ROUTE.format(series_cut_id=cut_id)
    query = {"music": "true" if music else "false", "part": str(part)}
    read_url = str(httpx.URL(url, params=query))

    def check(response: httpx.Response) -> dict[str, Any]:
        return server_answer(response, missing=missing, nothing=nothing)

    with client:
        copy = check(
            _send(
                lambda: client.post(
                    url, headers=headers, json={"part": part, "music": music}
                )
            )
        )
        deadline, said = clock() + wait_seconds, clock()
        while str(copy.get("status") or "") not in ("ready", "failed"):
            if clock() >= deadline:
                raise CutStop(
                    f"NOT DONE: the marked copy is still being made after {_duration(wait_seconds)}. Run the same "
                    "cut-save again later; the server keeps making the same copy and never makes two."
                )
            if clock() - said >= PROGRESS_EVERY_SECONDS:
                progress(
                    f"Still adding the Sokii mark ({copy.get('status') or 'working'}); waiting up to "
                    f"{_duration(wait_seconds)}."
                )
                said = clock()
            sleep(EXPORT_POLL_SECONDS)
            copy = picked_cut._poll(
                client,
                read_url,
                headers,
                deadline=deadline,
                clock=clock,
                sleep=sleep,
                check=check,
            )
    if copy.get("status") == "failed":
        reason = str(copy.get("reason") or "the server did not say why")
        raise CutStop(
            f"NOT DONE: the server could not save the edit with the Sokii mark ({reason}). Run the same cut-save "
            "again to start it again."
        )
    return _saved_lines(copy, folder, part=part, media_transport=media_transport)


def _saved_lines(
    copy: Mapping[str, Any],
    folder: Path,
    *,
    part: int,
    media_transport: httpx.BaseTransport | None,
) -> list[str]:
    export_url = str(copy.get("export_url") or "")
    if not export_url:
        raise CutStop(
            "NOT DONE: the server said the marked copy is ready but sent no file. Tell the core team."
        )
    dest = _free_path(folder.expanduser(), Path(str(copy.get("file_name") or "")).name)
    try:
        download_file(
            export_url,
            dest,
            transport=media_transport,
            kind="marked edit",
            again="cut-save",
        )
    except ServerFinishError as exc:
        raise CutStop(f"NOT DONE: {exc}") from exc
    sound = "with the edit's music" if copy.get("music") else "with no added music"
    return [
        f"Saved part {part} with the Sokii mark, {sound}, ready to post: {dest}",
        f"Link: {export_url}",
        *(f"Note: {warning}" for warning in copy.get("warnings") or []),
    ]


def _free_path(folder: Path, name: str) -> Path:
    """``folder/name``, or the next numbered name when a file already has it (never written over)."""

    stem, suffix = Path(name).stem or "edit-sokii", Path(name).suffix or ".mp4"
    path, n = folder / f"{stem}{suffix}", 2
    while path.exists():
        path, n = folder / f"{stem}-{n}{suffix}", n + 1
    return path


def add_cut_action_parsers(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``cut-rename``, ``cut-retry`` and ``cut-save``.

    Parameters
    ----------
    sub
        ``fictora-produce`` subparsers.
    """

    rename = sub.add_parser(
        "cut-rename",
        help='Rename a punchy edit on the server, by the id cut printed. An empty NAME ("") gives it back its '
        "first show's name. Its video is not made again.",
    )
    rename.add_argument("id", metavar="ID", help="The edit id cut printed.")
    rename.add_argument(
        "name", metavar="NAME", help="The new name (up to 80 characters)."
    )
    retry = sub.add_parser(
        "cut-retry",
        help="Try a punchy edit again by the id cut printed, when it failed, stalled or its new music did not "
        "arrive, and wait for it. Never makes a second edit.",
    )
    retry.add_argument("id", metavar="ID", help="The edit id cut printed.")
    save = sub.add_parser(
        "cut-save",
        help="Save a punchy edit with the Sokii mark, ready to post: the server adds the mark and the "
        "kit downloads the file. The edit the app plays stays unmarked.",
    )
    save.add_argument("id", metavar="ID", help="The edit id cut printed.")
    save.add_argument(
        "--part", type=int, default=None, metavar="N",
        help="Save this part (the numbers cut printed). Required.",
    )  # fmt: skip
    save.add_argument(
        "--no-music", action="store_true",
        help="Save the copy without the edit's new music (only for an edit made with --new-music).",
    )  # fmt: skip
    save.add_argument(
        "--to", type=Path, default=DEFAULT_SAVE_FOLDER, metavar="FOLDER",
        help="Folder to save into (default ~/Downloads). A file already there is never written over.",
    )  # fmt: skip
    for parser in (retry, save):
        parser.add_argument(
            "--wait",
            type=float,
            default=None,
            metavar="SECONDS",
            help=f"Longest wait (default {DEFAULT_WAIT_SECONDS / 60:g} minutes).",
        )
    for parser in (rename, retry, save):
        parser.add_argument(
            "--json", action="store_true", help="Print the result as JSON on stdout."
        )


def cut_action_command(args: Any) -> int:
    """Run ``cut-rename``, ``cut-retry`` or ``cut-save``: returns the exit code (0 done, 5 NOT DONE, 2 bad ask)."""

    wait = getattr(args, "wait", None)
    wait = wait if wait is not None else DEFAULT_WAIT_SECONDS
    try:
        if args.command == "cut-rename":
            lines = rename_cut(args.id, args.name)
        elif args.command == "cut-retry":
            lines = retry_cut(args.id, wait_seconds=wait)
        else:
            if args.part is None:
                raise CutStop(PART_REQUIRED, 2)
            if args.part < 1:
                raise CutStop("Stopped: --part starts at 1. Nothing was saved.", 2)
            lines = save_cut(
                args.id,
                part=args.part,
                music=not args.no_music,
                folder=args.to,
                wait_seconds=wait,
            )
    except CutStop as stop:
        return picked_cut._emit(args, False, [stop.line], stop.code)
    return picked_cut._emit(args, True, lines)


__all__ = [
    "CUT_ACTION_COMMANDS",
    "add_cut_action_parsers",
    "cut_action_command",
    "rename_cut",
    "retry_cut",
    "save_cut",
]
