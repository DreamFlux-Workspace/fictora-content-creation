"""The letterbox caption rows for each picture shape, read from the server (server-heavy rule, 15 Oct 2026).

fictora-drama owns where the captions sit under a 4:3 or 16:9 picture
(``delivery_geometry.letterbox_layout_json``) and serves the numbers; the kit's
local letterbox finish reads them here, once per shape and process. With no
desk, an older server or no connection, the kit's own copy in
:mod:`creation.post.delivery_geometry` is used and a ``NOTE`` says so.

Needs fictora-drama's letterbox layout route (the 16:9 frames finish PR) live.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from creation.post.delivery_geometry import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    LetterboxLayout,
    layout,
)

#: The server's route (GET, one picture shape).
LETTERBOX_LAYOUT_ROUTE = "/v1/letterbox-layout?master_aspect={master_aspect}"
#: The numbers the kit takes from the answer (1080x1920 pixels).
ROW_KEYS = ("caption_top", "caption_highest_top", "caption_bottom_limit")

_read: dict[str, dict[str, int] | None] = {}


def server_rows(picture: str, desk: Path | None) -> dict[str, int] | None:
    """The server's caption rows for one picture shape, or ``None`` (no desk, older server, no connection).

    Parameters
    ----------
    picture
        ``4:3`` or ``16:9``.
    desk
        The desk whose API session asks.

    Returns
    -------
    dict[str, int] | None
        :data:`ROW_KEYS` on a 1080x1920 canvas.
    """

    from creation.caption_server import _OVERRIDE

    override = _OVERRIDE.get()
    if override is not None:
        # Tests answer the server's routes (``creation.caption_server.server_override``); never cached.
        status, answer = override(_path(picture), {})
        return _rows(answer) if 200 <= status < 300 else None
    if desk is None:
        return None
    if picture not in _read:
        _read[picture] = _ask(picture, desk)
    return _read[picture]


def letterbox_layout(
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
    *,
    picture: str = "4:3",
    desk: Path | None = None,
) -> LetterboxLayout:
    """:func:`creation.post.delivery_geometry.layout` with the server's caption rows when it has them.

    Parameters
    ----------
    width, height
        The canvas.
    picture
        ``4:3`` or ``16:9``.
    desk
        The desk whose API session asks; ``None`` uses the kit's copy.

    Returns
    -------
    LetterboxLayout
        The layout.
    """

    return layout(width, height, picture=picture, rows=server_rows(picture, desk))


def _ask(picture: str, desk: Path) -> dict[str, int] | None:
    from creation.post.desk import open_api

    path = _path(picture)
    try:
        run = open_api(desk, 1)
        try:
            status, answer = run.get_optional(path)
        finally:
            run.client.close()
    except (OSError, httpx.HTTPError, ValueError, SystemExit) as exc:
        return _fallback(picture, f"the server could not be asked ({exc})")
    rows = _rows(answer) if 200 <= status < 300 else None
    if rows is None:
        return _fallback(
            picture, f"the server sent no letterbox layout (HTTP {status})"
        )
    return rows


def _path(picture: str) -> str:
    return LETTERBOX_LAYOUT_ROUTE.format(master_aspect=quote(picture, safe=""))


def _rows(answer: Any) -> dict[str, int] | None:
    if not isinstance(answer, Mapping):
        return None
    values = {key: answer.get(key) for key in ROW_KEYS}
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in values.values()):
        return None
    return {key: int(value) for key, value in values.items()}


def _fallback(picture: str, why: str) -> None:
    print(
        f"NOTE: {why}; the {picture} letterbox captions use the kit's copy of the server's rows",
        file=sys.stderr,
    )
    return None


__all__ = ["LETTERBOX_LAYOUT_ROUTE", "letterbox_layout", "server_rows"]
