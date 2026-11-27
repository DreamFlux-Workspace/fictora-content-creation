"""The letterbox layout for each picture shape, read from the server once per desk and process.

With no desk, an older server or no connection, the kit's own copy in
:mod:`creation.post.delivery_geometry` is used and a ``NOTE`` says so.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx

from creation.post.delivery_geometry import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    LetterboxLayout,
    Picture,
    layout,
)

LETTERBOX_LAYOUTS_ROUTE = "/v1/letterbox/layouts"
#: What the kit reads from each layout: the picture, the title anchor, the mark and the caption rows.
#: A server that sends fewer (an older #1854) is treated as having none.
LAYOUT_KEYS = (
    "picture_y",
    "picture_height",
    "title_bottom",
    "mark_x",
    "mark_y",
    "caption_top",
    "caption_highest_top",
    "caption_bottom_limit",
)

_read: dict[Path, dict[str, dict[str, int]] | None] = {}


def server_layout(picture: Picture, desk: Path | None) -> dict[str, int] | None:
    """The server's layout for one picture shape, or ``None`` (no desk, older server, no connection).

    Parameters
    ----------
    picture
        ``4:3`` or ``16:9``.
    desk
        The desk whose API session asks.

    Returns
    -------
    dict[str, int] | None
        :data:`LAYOUT_KEYS` on a 1080x1920 canvas.
    """

    from creation.caption_server import _OVERRIDE

    override = _OVERRIDE.get()
    if override is not None:
        status, answer = override(LETTERBOX_LAYOUTS_ROUTE, {})
        layouts = _layouts(answer) if 200 <= status < 300 else None
    elif desk is None:
        return None
    else:
        if desk not in _read:
            _read[desk] = _ask(desk)
        layouts = _read[desk]
    return (layouts or {}).get(picture)


def letterbox_layout(
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
    *,
    picture: Picture = "4:3",
    desk: Path | None = None,
) -> LetterboxLayout:
    """:func:`creation.post.delivery_geometry.layout` with the server's numbers when it has them.

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

    return layout(width, height, picture=picture, rows=server_layout(picture, desk))


def _ask(desk: Path) -> dict[str, dict[str, int]] | None:
    from creation.post.desk import open_api

    try:
        run = open_api(desk, 1)
        try:
            status, answer = run.get_optional(LETTERBOX_LAYOUTS_ROUTE)
        finally:
            run.client.close()
    except (OSError, httpx.HTTPError, ValueError, SystemExit) as exc:
        return _fallback(f"the server could not be asked ({exc})")
    layouts = _layouts(answer) if 200 <= status < 300 else None
    if not layouts:
        return _fallback(f"the server sent no letterbox layouts (HTTP {status})")
    return layouts


def _layouts(answer: Any) -> dict[str, dict[str, int]] | None:
    entries = answer.get("layouts") if isinstance(answer, Mapping) else None
    if not isinstance(entries, list):
        return None
    found: dict[str, dict[str, int]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        values = [entry.get(key) for key in LAYOUT_KEYS]
        if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            found[str(entry.get("master_aspect"))] = dict(zip(LAYOUT_KEYS, values))
    return found or None


def _fallback(why: str) -> None:
    print(
        f"NOTE: {why}; the letterbox uses the kit's copy of the server's layout",
        file=sys.stderr,
    )
    return None


__all__ = [
    "LAYOUT_KEYS",
    "LETTERBOX_LAYOUTS_ROUTE",
    "letterbox_layout",
    "server_layout",
]
