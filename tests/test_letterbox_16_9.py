"""New letterbox shows film 16:9 (founder, 27 Nov 2026); landscape shows carry no words on the picture.

The 16:9 picture sits 1080x608 under the 4:3 picture's top (y 555), so the title and the
mark stay where they are and the caption rows rise with the picture's bottom (202 px):
fictora-drama ``delivery_geometry.LETTERBOX_LAYOUT_16_9``. A 4:3 show is unchanged.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path

import pytest
from tests import test_hook_line_command as _hook_line_tests
from tests.test_letterbox_title_card_kit import CardApi

from creation.hook_line import run_hook_line
from creation.phone_frame_copy import run_phone_frame_copy
from creation.post.delivery_geometry import Box, layout
from creation.post.finish_record import write_finish_record
from creation.post.safe_zones import letterbox_file

hook_desk = _hook_line_tests.hook_desk


def test_a_4_3_picture_keeps_every_row() -> None:
    place = layout()
    assert place.picture == Box(0, 555, 1080, 810)
    assert (place.caption.y, place.caption.bottom, place.caption_highest_top) == (
        1407,
        1536,
        1385,
    )


def test_a_16_9_picture_sits_under_the_same_top_with_its_captions_202_px_higher() -> (
    None
):
    four_three, wide = layout(), layout(picture="16:9")

    assert wide.picture == Box(0, 555, 1080, 608)
    assert (wide.caption.y, wide.caption.bottom, wide.caption_highest_top) == (
        1205,
        1334,
        1183,
    )
    assert (wide.title, wide.mark) == (four_three.title, four_three.mark)


def test_another_picture_shape_has_no_layout() -> None:
    with pytest.raises(ValueError, match="1:1"):
        layout(picture="1:1")


def _record(desk: Path, letterbox: dict | None) -> Path:
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True)
    (desk / "series.json").write_text("{}", encoding="utf-8")
    final = takes / "take-ep01-t1-letterbox-v1.mp4"
    final.write_bytes(b"")
    write_finish_record(
        desk, episode=1, take_id="t1", complete=True, pre_bed=final, master=final,
        final=final, bed=None, bed_db=None, duck_db=None, letterbox=letterbox,
    )  # fmt: skip
    return final


def test_a_finish_record_names_its_picture_shape(tmp_path: Path) -> None:
    final = _record(
        tmp_path,
        {"letterbox": True, "caption_colour": "yellow", "picture_aspect": "16:9"},
    )
    assert letterbox_file(final, desk=tmp_path) == "16:9"


def test_an_older_letterbox_record_is_4_3(tmp_path: Path) -> None:
    final = _record(tmp_path, {"letterbox": True, "caption_colour": "yellow"})
    assert letterbox_file(final, desk=tmp_path) == "4:3"


def test_a_portrait_record_is_not_letterbox(tmp_path: Path) -> None:
    assert letterbox_file(_record(tmp_path, None), desk=tmp_path) is None


def test_a_landscape_show_has_no_title_card(hook_desk: tuple[Path, dict]) -> None:
    desk, spine = hook_desk
    landscape = {**copy.deepcopy(spine), "delivery_format": "landscape"}
    api = CardApi(landscape, [])

    with pytest.raises(ValueError, match="phone-frame copy"):
        run_hook_line(desk, episode=1, titles=True, out=io.StringIO(), api=api)

    assert api.gets == [] and api.sent == []


class CopyApi:
    def __init__(self, spine: dict, answer: tuple[int, object]) -> None:
        self.stored, self.answer = spine, answer
        self.posted: list[str] = []

    def spine(self, spine_id: str) -> dict:
        return copy.deepcopy(self.stored)

    def post_optional(self, path: str, body: dict) -> tuple[int, object]:
        self.posted.append(path)
        return self.answer


def _copy(
    desk: Path, spine: dict, answer: tuple[int, object]
) -> tuple[int, str, CopyApi]:
    api = CopyApi(spine, answer)
    out = io.StringIO()
    code = run_phone_frame_copy(desk, episode=1, out=out, api=api)
    return code, out.getvalue(), api


def test_a_landscape_episode_asks_the_server_for_its_phone_frame_copy(
    hook_desk: tuple[Path, dict],
) -> None:
    desk, spine = hook_desk
    landscape = {**spine, "delivery_format": "landscape"}

    code, text, api = _copy(
        desk, landscape, (202, {"line": "Pick a title for the copy."})
    )

    assert code == 0 and text.strip() == "Pick a title for the copy."
    assert api.posted == ["/v1/spines/spine_test/episodes/episode_01/letterboxed-copy"]


def test_an_older_server_without_the_route_makes_nothing(
    hook_desk: tuple[Path, dict],
) -> None:
    desk, spine = hook_desk
    landscape = {**spine, "delivery_format": "landscape"}

    code, text, _api = _copy(desk, landscape, (404, {"detail": "Not Found"}))

    assert code == 2 and "older deploy" in text


def test_the_caption_rows_are_the_servers_when_it_serves_them() -> None:
    from creation.caption_server import server_override
    from creation.post.letterbox_rows import letterbox_layout

    served = {
        "caption_top": 1200,
        "caption_highest_top": 1180,
        "caption_bottom_limit": 1330,
    }
    asked: list[str] = []

    def answer(path: str, body: dict) -> tuple[int, object]:
        asked.append(path)
        return 200, {"master_aspect": "16:9", **served}

    with server_override(answer):
        place = letterbox_layout(picture="16:9")

    assert asked == ["/v1/letterbox-layout?master_aspect=16%3A9"]
    assert (place.caption.y, place.caption_highest_top, place.caption.bottom) == (
        1200,
        1180,
        1330,
    )


def test_an_older_server_leaves_the_kits_copy() -> None:
    from creation.post.letterbox_rows import letterbox_layout

    assert letterbox_layout(picture="16:9") == layout(picture="16:9")
