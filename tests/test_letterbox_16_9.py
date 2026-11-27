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

from creation.caption_bold import build_bold_ass
from creation.captions import Cue, build_ass
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


def _style(ass: str, name: str) -> list[str]:
    line = next(row for row in ass.splitlines() if row.startswith(f"Style: {name},"))
    return line.split(",")


def test_a_wide_frame_is_captioned_on_the_side_turned_canvas() -> None:
    long_line = (
        "I paid every bill in this house for three years and you never once asked"
    )

    house = build_ass([Cue(0.0, 2.0, long_line)], width=854, height=480)
    bold = build_bold_ass([Cue(0.0, 2.0, "Never once.")], width=854, height=480)

    size, margin_l, margin_r, margin_v = (
        _style(house, "House")[i] for i in (2, 19, 20, 21)
    )
    assert (size, margin_l, margin_r, margin_v) == ("28", "128", "128", "48")
    assert (_style(bold, "Bold")[2], _style(bold, "Bold")[21]) == ("41", "48")
    assert "\\N" in house.splitlines()[-1]


def test_a_tall_frame_keeps_the_portrait_canvas() -> None:
    house = build_ass([Cue(0.0, 1.0, "Wait.")], width=1080, height=1920)

    assert [_style(house, "House")[i] for i in (2, 19, 21)] == ["64", "60", "730"]


@needs_ffmpeg
def test_finish_on_a_landscape_show_burns_captions_on_the_wide_picture(
    post_desk: Path,
) -> None:
    from test_post_finish import FACTS, _board, fake_sfx

    from creation.post.finish import run_finish

    api = post_desk / "ep01" / "api"
    spine = json.loads((api / "03_spine.json").read_text(encoding="utf-8"))
    spine["delivery_format"] = "landscape"
    (api / "03_spine.json").write_text(json.dumps(spine), encoding="utf-8")
    (api / "take-facts-ep01-t1-v1.json").write_text(json.dumps(FACTS))
    make_take(
        post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
        tones=((1.0, 2.0, 440), (3.2, 4.0, 880)),
        size="854x480",
    )
    _board(post_desk)
    out = io.StringIO()

    result = run_finish(post_desk, sfx_render=fake_sfx([]), facts_fetcher=lambda *a: None,
                        cut_meter=lambda _take: (2.5,), stream=out)  # fmt: skip

    captions = next(step for step in result.steps if step.step == "captions")
    assert captions.status == "ran", out.getvalue()
    ass = next(iter((post_desk / "ep01" / "takes").glob("take-ep01-t1-cap-v*.ass")))
    text = ass.read_text(encoding="utf-8")
    assert "PlayResX: 854\nPlayResY: 480" in text
    assert ",128,128,48," in text, text


def test_a_wide_take_is_laid_out_by_the_kit_not_the_9_16_server() -> None:
    from creation.caption_server import server_override
    from creation.captions import portrait_caption_ass

    sent: list[str] = []

    def server(path: str, body: dict) -> tuple[int, object]:
        sent.append(path)
        return 200, {"ass": "SERVER", "cues": [], "findings": []}

    cues = [Cue(0.0, 1.0, "Wait.")]
    with server_override(server):
        wide = portrait_caption_ass(
            cues, width=854, height=480, style="subtle", whole_lines=False
        )
        tall = portrait_caption_ass(
            cues, width=192, height=336, style="subtle", whole_lines=False
        )

    assert wide == build_ass(cues, width=854, height=480, style="subtle")
    assert tall == "SERVER" and len(sent) == 1
