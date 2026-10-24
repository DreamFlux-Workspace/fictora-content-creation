"""The letterbox title block goes where the server says (fictora-drama #1049, 24 Oct 2026).

A show stamped ``letterbox_part_titles`` has its title block under the Sokii
mark (the server's anchor y 512 on 1920, four lines at size 52 or smaller).
The kit asks ``POST /v1/mark/placement`` with the show's spine id, so its bands
match the app's; an older server (no title fields, or 404) keeps the old place.
"""

from __future__ import annotations

import re
from typing import Any

from creation.caption_server import server_override
from creation.post.delivery_geometry import layout
from creation.post.letterbox import TitleBlock, title_ass, under_server_anchor
from creation.post.watermark import MARK_PLACEMENT_ROUTE, server_letterbox_place

HOOK = "He came to take her arm. It came with a secret."


def _positions(text: str) -> list[tuple[int, int]]:
    return [(int(x), int(y)) for x, y in re.findall(r"\\pos\((\d+),(\d+)\)", text)]


def _size(text: str) -> int:
    match = re.search(r"Style: Title,[^,]+,(\d+),", text)
    assert match
    return int(match.group(1))


def test_the_kit_asks_with_the_spine_and_reads_the_title_anchor() -> None:
    asked: list[tuple[str, dict[str, Any]]] = []

    def poster(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        asked.append((path, body))
        return 200, {
            "x": 32,
            "y": 172,
            "opacity": 0.6,
            "title_bottom": 512,
            "title_four_line_size": 52,
        }

    with server_override(poster):
        place = server_letterbox_place(1080, 1920, spine_id="spine-1")

    assert asked == [
        (
            MARK_PLACEMENT_ROUTE,
            {"width": 1080, "height": 1920, "letterbox": True, "spine_id": "spine-1"},
        )
    ]
    assert place is not None
    assert (place.x, place.y, place.title_bottom, place.title_four_line_size) == (
        32,
        172,
        512,
        52,
    )


def test_an_older_server_gives_no_title_anchor() -> None:
    with server_override(
        lambda _path, _body: (200, {"x": 32, "y": 172, "opacity": 0.6})
    ):
        place = server_letterbox_place(1080, 1920, spine_id="spine-1")
    assert (
        place is not None
        and place.title_bottom is None
        and place.title_four_line_size is None
    )
    with server_override(lambda _path, _body: (404, {"detail": "Not Found"})):
        assert server_letterbox_place(1080, 1920) is None


def test_under_the_mark_the_block_moves_down_64_px_and_without_it_nothing_moves() -> (
    None
):
    block = TitleBlock("Three Payments Late \u00b7 Part 2", HOOK)
    before, _ = title_ass(block)
    same, _ = title_ass(block, title_bottom=448)
    under, _ = title_ass(block, title_bottom=512, four_line_size=52)

    assert before == same
    assert [y + 64 for _x, y in _positions(before)] == [
        y for _x, y in _positions(under)
    ]
    assert under_server_anchor(layout(), None, 1920) == layout()


def test_four_lines_under_the_mark_step_down_to_the_servers_size() -> None:
    block = TitleBlock(
        "The Woman Who Paid Three Months Late \u00b7 Part 12",
        "He came to take her arm and found the secret inside it.",
    )
    under, fitted = title_ass(block, title_bottom=512, four_line_size=52)
    assert len(fitted.setup) + len(fitted.hook) == 4
    assert _size(under) <= 52
