"""The Sokii mark goes where the server says (fictora-drama #1039, 24 Oct 2026).

The server burns the mark 3% in from the left (the founder's approved reels; it was 12% for a day for the app's full-bleed feed on
a 19.5:9 or 20:9 phone never clips it, and answers ``POST /v1/mark/placement``
for any frame. The kit asks it; an older server (404/405) or no connection
falls back to the same numbers kept in the kit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from creation.caption_server import server_override
from creation.post.delivery_geometry import layout
from creation.post.watermark import (
    MARK_PLACEMENT_ROUTE,
    MARK_X_FRACTION,
    MARK_Y_FRACTION,
    mark_position,
    server_mark_position,
)


def test_the_kit_uses_the_servers_place() -> None:
    asked: list[tuple[str, dict[str, Any]]] = []

    def poster(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        asked.append((path, body))
        return 200, {"x": 101, "y": 131, "opacity": 0.6}

    with server_override(poster):
        assert mark_position(768, 1344, desk=Path("/desk")) == (101, 131)
        assert server_mark_position(1080, 1920, letterbox=True) == (101, 131)

    assert asked[0] == (
        MARK_PLACEMENT_ROUTE,
        {"width": 768, "height": 1344, "letterbox": False},
    )
    assert asked[1][1]["letterbox"] is True


def test_an_older_server_falls_back_to_the_same_numbers() -> None:
    with server_override(lambda _path, _body: (404, {"detail": "Not Found"})):
        assert mark_position(768, 1344, desk=Path("/desk")) == (
            round(768 * MARK_X_FRACTION),
            round(1344 * MARK_Y_FRACTION),
        )
    # One fallback x for portrait and letterbox (the server's, fictora-drama #1133).
    x, _ = mark_position(1080, 1920)
    assert x == round(1080 * MARK_X_FRACTION) == layout().mark[0]
