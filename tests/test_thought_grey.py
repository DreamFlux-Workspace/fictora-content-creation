"""Thoughts come from the server's thoughts route: the kit sends lengths, applies the answer (24 Oct 2026)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import SPINE, make_take, needs_ffmpeg
from test_finish_inner_voice import ServerStandIn, _cue

from creation.post import thought_grey as grey_route
from creation.post.finish_record import FinishRecord
from creation.post.join import JoinPart, thought_grey_lines

ANSWER: dict[str, Any] = {
    "takes": [
        {
            "take": 1,
            "take_seconds": 15.104,
            "windows": [
                {
                    "source": "line",
                    "id": "line_episode_01_03",
                    "speaker_cast_id": "cast_ines",
                    "text": "Dad planned this.",
                    "acting_direction": "soft, breaking",
                    "start_s": 13.5,
                    "end_s": 14.81,
                    "grey": True,
                },
            ],  # fmt: skip
            "grey_spans": [[13.5, 14.81]],
            "grey_filter": "hue=s='1-clip((t-13.450)/0.100\\,0\\,1)*clip((14.860-t)/0.100\\,0\\,1)'",
            "track_read": True,
        }
    ],
    "unplaced": [],
    "grey_on": True,
    "grey": {"ffmpeg_filter": "hue", "ramp_seconds": 0.1},
}


def test_the_answer_is_read_as_sent() -> None:
    read = grey_route.parse_answer(ANSWER)
    take = read.take(1)
    assert take is not None and read.take(2) is None
    assert take.grey_spans == ((13.5, 14.81),)
    assert take.grey_filter == ANSWER["takes"][0]["grey_filter"], (
        "the filter is applied exactly as the server sent it"
    )
    (window,) = take.windows
    assert (window.source, window.id, window.start, window.end) == (
        "line",
        "line_episode_01_03",
        13.5,
        14.81,
    )


@pytest.mark.parametrize(
    ("status", "words"),
    [(404, "no thoughts route yet"), (500, "HTTP 500")],
)
def test_a_server_that_does_not_answer_is_a_plain_note(status: int, words: str) -> None:
    with pytest.raises(grey_route.ThoughtsUnavailable, match=words):
        grey_route.fetch_thoughts(lambda body: (status, {"detail": "x"}), {"takes": []})


def test_no_api_is_a_note_too() -> None:
    def broken(body: dict[str, Any]) -> tuple[int, Any]:
        raise OSError("no credentials")

    with pytest.raises(grey_route.ThoughtsUnavailable, match="could not be asked"):
        grey_route.fetch_thoughts(broken, {"takes": []})


def test_the_body_sends_each_takes_real_length_and_its_cuts(post_desk: Path) -> None:
    body = grey_route.request_body(
        post_desk, 1, [15.1041, 4.0], cuts={1: [4.0416, 13.25]}
    )
    assert [take["take_seconds"] for take in body["takes"]] == [15.104, 4.0]
    assert (
        body["takes"][0]["cuts_s"] == [4.042, 13.25]
        and "cuts_s" not in body["takes"][1]
    )


def test_only_an_episode_with_a_thought_asks_the_server() -> None:
    spine = copy.deepcopy(SPINE)
    assert grey_route.episode_has_thoughts(spine, 1) is False
    spine["thought_line_ids"] = [spine["beats"][0]["dialogue_lines"][0]["line_id"]]
    assert grey_route.episode_has_thoughts(spine, 1) is True


@needs_ffmpeg
def test_grey_take_uses_the_servers_filter_and_copies_the_sound(tmp_path: Path) -> None:
    from creation.post.media import probe_video

    take = make_take(tmp_path / "take.mp4", seconds=2.0, colour="red")
    out = grey_route.grey_take(take, "hue=s='1-gte(t\\,1.000)'", tmp_path / "grey.mp4")
    assert abs(probe_video(out).duration_seconds - 2.0) < 0.1


# --- join -------------------------------------------------------------------------------------------


def _record(
    post_desk: Path, take_id: str, *, grey: dict[str, Any] | None
) -> FinishRecord:
    return FinishRecord(
        episode=1, take_id=take_id, complete=True, pre_bed=None, master="m.mp4", final="f.mp4",
        bed=None, bed_db=None, duck_db=None, thought_grey=grey,
    )  # fmt: skip


def _part(post_desk: Path, take_id: str, *, grey: dict[str, Any] | None) -> JoinPart:
    return JoinPart(episode=1, take_id=take_id, picture=post_desk / "m.mp4", pre_bed=post_desk / "p.mp4",
                    record=_record(post_desk, take_id, grey=grey))  # fmt: skip


def _spine_with(post_desk: Path, cues: list[dict[str, Any]]) -> None:
    body = copy.deepcopy(SPINE)
    body["episode_summaries"][0]["inner_voice"] = cues
    (post_desk / "ep01" / "api" / "03_spine.json").write_text(
        json.dumps(body), encoding="utf-8"
    )


@needs_ffmpeg
def test_join_names_a_take_finished_before_thoughts_played_grey(
    post_desk: Path,
) -> None:
    takes = post_desk / "ep01" / "takes"
    make_take(takes / "take-ep01-t1-raw-v1.mp4", seconds=4.0)
    make_take(takes / "take-ep01-t2-raw-v1.mp4", seconds=5.0)
    cues = [_cue("iv_ep01_01", 1.0, 2.0), _cue("iv_ep01_02", 6.0, 7.0)]
    _spine_with(post_desk, cues)
    server = ServerStandIn(cues)
    parts = [
        _part(post_desk, "t1", grey={"spans": [[1.0, 2.0]], "filter": "hue=s='0'"}),
        _part(post_desk, "t2", grey=None),
    ]

    lines = thought_grey_lines(post_desk, parts, fetcher=server)

    ((first, second),) = [body["takes"] for body in server.bodies]
    assert (
        abs(first["take_seconds"] - 4.0) < 0.05
        and abs(second["take_seconds"] - 5.0) < 0.05
    )
    assert (
        lines[0]
        == "thoughts: ep01 t1 plays its thought(s) in black and white (1.00-2.00s, from finish)"
    )
    assert lines[1].startswith(
        "!! ep01 t2 has a thought at 2.00-3.00s that plays in colour"
    )
    assert "Run finish on t2 again (free" in lines[1]


@needs_ffmpeg
def test_join_on_an_older_server_is_a_note_and_a_show_with_no_thought_asks_nothing(
    post_desk: Path,
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", seconds=4.0)
    cues = [_cue("iv_ep01_01", 1.0, 2.0)]
    parts = [_part(post_desk, "t1", grey=None)]
    _spine_with(post_desk, [])
    quiet = ServerStandIn([])
    assert (
        thought_grey_lines(post_desk, parts, fetcher=quiet) == [] and quiet.bodies == []
    )

    _spine_with(post_desk, cues)
    (line,) = thought_grey_lines(
        post_desk, parts, fetcher=ServerStandIn(cues, status=404)
    )
    assert line.startswith(
        "thoughts: !! no black and white checked for ep01: the server has no thoughts route yet"
    )
