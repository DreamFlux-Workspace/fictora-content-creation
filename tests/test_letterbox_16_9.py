"""New letterbox shows film 16:9; landscape shows carry no words on the picture."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest
from conftest import make_take, needs_ffmpeg
from tests import test_hook_line_command as _hook_line_tests
from tests.test_letterbox_title_card_kit import CardApi

from creation.captions import resolve_caption_style
from creation.hook_line import run_hook_line
from creation.phone_frame_copy import copy_request, run_phone_frame_copy
from creation.post.delivery_geometry import Box, layout
from creation.post.finish_record import write_finish_record
from creation.post.safe_zones import letterbox_picture_of

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
    assert letterbox_picture_of(final, desk=tmp_path) == "16:9"


def test_an_older_letterbox_record_is_4_3(tmp_path: Path) -> None:
    final = _record(tmp_path, {"letterbox": True, "caption_colour": "yellow"})
    assert letterbox_picture_of(final, desk=tmp_path) == "4:3"


def test_a_portrait_record_is_not_letterbox(tmp_path: Path) -> None:
    assert letterbox_picture_of(_record(tmp_path, None), desk=tmp_path) is None


def test_a_landscape_show_has_no_title_card(hook_desk: tuple[Path, dict]) -> None:
    desk, spine = hook_desk
    landscape = {**copy.deepcopy(spine), "delivery_format": "landscape"}
    api = CardApi(landscape, [])

    with pytest.raises(ValueError, match="phone-frame copy"):
        run_hook_line(desk, episode=1, titles=True, out=io.StringIO(), api=api)

    assert api.gets == [] and api.sent == []


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
        four_three = {
            "caption_top": 1407,
            "caption_highest_top": 1385,
            "caption_bottom_limit": 1536,
        }
        return 200, {
            "layouts": [
                {"master_aspect": "4:3", "picture_height": 810, **four_three},
                {"master_aspect": "16:9", "picture_height": 608, **served},
            ]
        }

    with server_override(answer):
        place = letterbox_layout(picture="16:9")

    assert asked == ["/v1/letterbox/layouts"]
    assert (place.caption.y, place.caption_highest_top, place.caption.bottom) == (
        1200,
        1180,
        1330,
    )


def test_an_older_server_leaves_the_kits_copy() -> None:
    from creation.post.letterbox_rows import letterbox_layout

    assert letterbox_layout(picture="16:9") == layout(picture="16:9")


class CopyApi:
    def __init__(self, answer: tuple[int, object]) -> None:
        self.answer = answer
        self.sent: list[tuple[str, dict]] = []
        self.keys: list[str | None] = []

    def post_optional(
        self, path: str, body: dict, *, idempotency_key: str | None = None
    ) -> tuple[int, object]:
        self.sent.append((path, body))
        self.keys.append(idempotency_key)
        return self.answer


@pytest.fixture
def filmed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from creation.post import server_finish, take_handles

    monkeypatch.setattr(
        take_handles, "take_coordinate",
        lambda desk, episode, take_id: take_handles.TakeCoordinate("job_video_1", 1, "job_take_1"),
    )  # fmt: skip
    monkeypatch.setattr(
        server_finish, "_desk_episode_id", lambda desk, episode: "episode_01"
    )
    return tmp_path


def test_a_phone_frame_copy_sends_only_the_choices_made(filmed: Path) -> None:
    made = {"video_url": "https://media.example/copy.mp4", "captions": False}
    api = CopyApi((200, {"letterboxed_copy": made}))
    out = io.StringIO()
    body = copy_request(setup_line="Three Payments Late", captions=False)

    code = run_phone_frame_copy(filmed, episode=1, body=body, out=out, api=api)

    assert code == 0
    assert out.getvalue().strip() == (
        "The server made the phone-frame copy (without captions): https://media.example/copy.mp4"
    )
    assert api.sent == [
        (
            "/v1/video-generations/job_video_1/episodes/episode_01/letterboxed-copy",
            {"setup_line": "Three Payments Late", "captions": False},
        )
    ]
    assert api.keys[0]


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        ((404, {"detail": "Not Found"}), "older deploy"),
        ((404, "Not Found"), "older deploy"),
        (
            (404, {"error": {"code": "episode_not_found", "message": "No episode 1."}}),
            "No episode 1.",
        ),
        (
            (
                404,
                {
                    "error": {
                        "code": "episode_video_not_delivered",
                        "message": "Episode 1 is not delivered yet.",
                    }
                },
            ),
            "Episode 1 is not delivered yet.",
        ),
    ],
)
def test_a_refusal_or_an_older_server_makes_nothing(
    filmed: Path, answer: tuple[int, object], says: str
) -> None:
    out = io.StringIO()

    code = run_phone_frame_copy(
        filmed, episode=1, body={}, out=out, api=CopyApi(answer)
    )

    assert code == 2 and says in out.getvalue()


@pytest.mark.parametrize(
    "spine",
    [
        {"delivery_format": "landscape"},
        {"delivery_format": "landscape", "captions_enabled": False},
        {"delivery": {"delivery_format": "landscape"}, "caption_style_choice": "none"},
    ],
)
def test_a_show_the_server_keeps_caption_free_burns_none(
    tmp_path: Path, spine: dict
) -> None:
    assert resolve_caption_style(tmp_path, spine=spine) == ("none", "")
    style, note = resolve_caption_style(tmp_path, "bold", spine=spine)
    assert style == "none" and "phone-frame copy" in note


def test_a_landscape_desk_without_a_spine_burns_none(tmp_path: Path) -> None:
    from creation.production_config import ProductionConfig, save_production_config

    save_production_config(tmp_path, ProductionConfig(delivery_format="landscape"))
    assert resolve_caption_style(tmp_path) == ("none", "")
    portrait = {"delivery_format": "portrait"}
    assert resolve_caption_style(tmp_path, spine=portrait)[0] != "none"
    save_production_config(tmp_path, ProductionConfig(delivery_format="letterbox"))
    assert resolve_caption_style(tmp_path)[0] != "none"


@needs_ffmpeg
def test_finish_on_a_landscape_show_burns_no_captions(post_desk: Path) -> None:
    from test_post_finish import FACTS, _board, fake_sfx

    from creation.post.finish import run_finish

    api = post_desk / "ep01" / "api"
    spine = json.loads((api / "03_spine.json").read_text(encoding="utf-8"))
    spine["delivery_format"] = "landscape"
    (api / "03_spine.json").write_text(json.dumps(spine), encoding="utf-8")
    (api / "take-facts-ep01-t1-v1.json").write_text(json.dumps(FACTS))
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    _board(post_desk)
    out = io.StringIO()

    result = run_finish(post_desk, sfx_render=fake_sfx([]), facts_fetcher=lambda *a: None,
                        cut_meter=lambda _take: (2.5,), stream=out)  # fmt: skip

    captions = next(step for step in result.steps if step.step == "captions")
    assert captions.status == "skipped", out.getvalue()
    assert not list((post_desk / "ep01" / "takes").glob("take-ep01-t1-cap-v*"))


@pytest.mark.parametrize("shape", ["portrait", "letterbox"])
def test_captions_off_on_a_non_landscape_spine_keeps_the_flag(
    tmp_path: Path, shape: str
) -> None:
    spine = {
        "delivery_format": shape,
        "captions_enabled": False,
        "caption_style_choice": "none",
    }

    assert resolve_caption_style(tmp_path, "bold", spine=spine) == ("bold", "")
