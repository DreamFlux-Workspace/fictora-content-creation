"""``show-rename SHOW NAME``: rename a show on the server, at any stage (founder, 5 Nov 2026).

A rename is a story edit that carries only the new title and the story's current version. The server
checks the name and keeps it; the kit keeps no copy. Nothing is drawn, voiced or filmed again: covers,
title bands and download names made after the rename use the new name, and what was made before keeps
the old one until it is made again. When someone else changed the story in between (a version
conflict), the kit reads the story again and sends the rename once more.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from creation.post import picked_cut
from creation.post.desk import spine_body
from creation.post.edit_actions import server_answer
from creation.post.picked_cut import CutStop
from creation.post.server_finish import _body

SHOW_RENAME_COMMANDS = frozenset({"show-rename"})
SPINE_ROUTE = "/v1/spines/{spine_id}"
SESSION_ID = "kit-show-rename"
NOTHING = "Nothing was changed."
MISSING = (
    "NOT DONE: the server can't rename shows. Tell the core team; nothing was changed."
)

NEXT_STEP = {
    "spine_not_found": "Check the show id (cut --list shows it) or the desk folder.",
    "invalid_request": "",
    "cascade_required": (
        "This server can't rename a show after its script is approved yet. Tell the core team; it needs the "
        "show-rename update."
    ),
    "spine_version_conflict": "The show kept changing while it was renamed. Run the same show-rename again.",
}


def resolve_show(raw: str) -> str:
    """The show id for ``raw``: a desk folder's own id (read from its ``production.json``), or ``raw`` itself."""

    desk = Path(raw).expanduser()
    if desk.is_dir():
        from creation.post.desk import spine_id

        try:
            return spine_id(desk.resolve())
        except (ValueError, FileNotFoundError) as exc:
            raise CutStop(f"Stopped: {desk}: {exc}. {NOTHING}", 2) from exc
    if "/" in raw or raw.startswith(("~", ".")):
        raise CutStop(f"Stopped: no desk folder at {raw}. {NOTHING}", 2)
    return raw


def rename_show(
    show: str,
    name: str,
    *,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """Rename the show (its id or a desk folder) on the server and return the lines to print.

    Raises
    ------
    CutStop
        With the plain NOT DONE line when the server did not rename it.
    """

    spine_id = resolve_show(show)
    url = SPINE_ROUTE.format(spine_id=spine_id)
    client, base, headers = picked_cut._client(transport, api, SESSION_ID)
    with client:

        def read() -> dict[str, Any]:
            return spine_body(_answer(lambda: client.get(base + url, headers=headers)))

        def patch(version: str) -> httpx.Response:
            body = {"spine_version": version, "patch": {"title": name}}
            return _call(lambda: client.patch(base + url, headers=headers, json=body))

        before = read()
        response = patch(str(before.get("spine_version") or ""))
        if _conflict(response):
            before = read()
            response = patch(str(before.get("spine_version") or ""))
        after = spine_body(
            server_answer(
                response, missing=MISSING, nothing=NOTHING, next_step=NEXT_STEP
            )
        )
    old, new = before.get("title"), after.get("title")
    if old == new:
        return [f'The show is already called "{new}". {NOTHING}']
    return [
        f'The show "{old}" is now called "{new}". Nothing is drawn, voiced or filmed again. Covers, title bands '
        "and saved files made from now on use the new name; episodes and edits already made keep the old name "
        "until they are made again."
    ]


def _conflict(response: httpx.Response) -> bool:
    body = _body(response)
    return (
        response.status_code == 409
        and picked_cut._error(body).get("code") == "spine_version_conflict"
    )


def _call(call: Any) -> httpx.Response:
    try:
        return call()
    except httpx.HTTPError as exc:
        raise CutStop(
            f"NOT DONE: could not reach the server ({type(exc).__name__}). Check the internet connection, then run "
            "it again."
        ) from exc


def _answer(call: Any) -> dict[str, Any]:
    return server_answer(
        _call(call), missing=MISSING, nothing=NOTHING, next_step=NEXT_STEP
    )


def add_show_rename_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register ``show-rename``.

    Parameters
    ----------
    sub
        ``fictora-produce`` subparsers.
    """

    rename = sub.add_parser(
        "show-rename",
        help="Rename a show on the server, at any stage. Nothing is drawn, voiced or filmed again; covers, title "
        "bands and saved files made from now on use the new name.",
    )
    rename.add_argument(
        "show",
        metavar="SHOW",
        help="The show id (cut --list shows it) or its desk folder.",
    )
    rename.add_argument(
        "name", metavar="NAME", help="The new name (up to 80 characters)."
    )
    rename.add_argument(
        "--json", action="store_true", help="Print the result as JSON on stdout."
    )


def show_rename_command(args: Any) -> int:
    """Run ``show-rename``: returns the exit code (0 done, 5 NOT DONE, 2 bad ask)."""

    try:
        lines = rename_show(args.show, args.name)
    except CutStop as stop:
        return picked_cut._emit(args, False, [stop.line], stop.code)
    return picked_cut._emit(args, True, lines)


__all__ = [
    "SHOW_RENAME_COMMANDS",
    "add_show_rename_parser",
    "rename_show",
    "show_rename_command",
]
