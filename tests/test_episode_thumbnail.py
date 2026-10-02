"""Episode thumbnail draw at finish: server POST + attached cover on the deliverable."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import make_take, make_tone, needs_ffmpeg
from PIL import Image

from creation import post
from creation.ops.floor import approve_board
from creation.ops.folder import next_versioned_path
from creation.ops.state import load_series
from creation.post.finish import run_finish
from creation.post.sfx import SfxCue
from creation.post.thumbnail import saved_cover
from fake_api import FakeApi, openapi_doc

ROUTE = "/v1/spines/spine_test/episodes/1/thumbnail"
THUMB_URL = "https://cdn.example/tenants/abc/drama/thumbnails/ep01.jpg"
CLIP_URL = "https://r2.example/takes/ep01-t1.mp4"
TWO_LINES = ((1.0, 2.0, 440), (3.2, 4.0, 880))
FACTS = {
    "job_id": "job_video_scene_1",
    "take_facts": {
        "job_id": "job_video_scene_1",
        "endpoint_id": "minimax/h3-max/reference-to-video",
        "media_kind": "video",
        "reference_image_count": 3,
        "spoken_line_count": 1,
        "shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 5.0, "speaks": True}
        ],
        "sfx_cues": [],
    },
}


def _board(post_desk: Path) -> None:
    board = post_desk / "ep01" / "boards" / "board-ep01-t1-1-v1.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (192, 336), (200, 150, 110)).save(board)
    approve_board(post_desk, episode=1, take_id="t1", image=board)


def _desk_with_take(post_desk: Path, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    (post_desk / "ep01" / "api" / "17_raw_scene_clips.json").write_text(
        json.dumps(
            {
                "coordinator_job_id": "job_video_1",
                "clips": [
                    {
                        "job_id": "job_take_1",
                        "url": CLIP_URL,
                        "relation_id": "scene_episode_01_set01",
                        "set_index": 1,
                        "episode_id": "episode_01",
                    }
                ],
            }
        )
    )
    _board(post_desk)

    fake = FakeApi(post_desk / "ep01" / "api")
    fake.routes[("GET", "/openapi.json")] = openapi_doc(episode_thumbnail=True)
    fake.routes[("POST", ROUTE)] = {
        "schema_version": "fictora.drama-episode-thumbnail-response.v1",
        "image_url": THUMB_URL,
        "width": 720,
        "height": 1280,
        "cached": False,
        "cost_usd": 0.3,
    }

    def fake_download(
        client, url: str, directory: Path, stem: str, **kwargs: object
    ) -> Path:
        assert url == THUMB_URL
        path = next_versioned_path(directory, stem, ".jpg")
        Image.new("RGB", (72, 128), (40, 60, 90)).save(path, format="JPEG")
        return path

    monkeypatch.setattr(post.thumbnail, "download_to_versioned", fake_download)
    monkeypatch.setattr(post.thumbnail, "open_api", lambda desk, episode: fake)
    return fake


def _finish(post_desk: Path, stream: io.StringIO | None = None, **kwargs: object):
    def fake_sfx(cue: SfxCue, target: Path) -> Path:
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.8)

    def fake_bed(spine: dict, music: str | None, target: Path) -> Path:
        return make_tone(target.with_suffix(".wav"), seconds=6.0, freq=220, volume=0.9)

    return run_finish(
        post_desk,
        sfx_render=fake_sfx,
        bed_maker=fake_bed,
        facts_fetcher=lambda *a: None,
        stream=stream or io.StringIO(),
        **kwargs,
    )


def _posted(fake: FakeApi) -> list:
    return [c for c in fake.calls if c[0] == "POST" and c[1] == ROUTE]


@needs_ffmpeg
def test_finish_embeds_the_server_thumbnail_on_the_marked_take(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _desk_with_take(post_desk, monkeypatch)
    out = io.StringIO()

    result = _finish(post_desk, out, draw_thumbnail=True)

    thumb_step = next(s for s in result.steps if s.step == "thumbnail")
    assert thumb_step.status == "ran", thumb_step.detail
    assert result.final.name == "take-ep01-t1-sokii-cover-v1.mp4"
    assert list((post_desk / "ep01" / "takes").glob("take-ep01-t1-thumb-v*.jpg"))
    posted = _posted(fake)
    assert posted and posted[0][2] == {"video_url": CLIP_URL}
    # The price is said before the server is asked, and the spend is booked as a cover.
    assert "$0.30" in out.getvalue()
    log = load_series(post_desk).spend_log
    assert [(e.unit, e.usd) for e in log if e.usd == 0.3] == [("thumbnail", 0.3)]


@needs_ffmpeg
def test_finish_never_draws_a_cover_without_the_opt_in(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _desk_with_take(post_desk, monkeypatch)

    result = _finish(post_desk)

    thumb_step = next(s for s in result.steps if s.step == "thumbnail")
    assert thumb_step.status == "skipped"
    assert "$0.30" in thumb_step.detail and "--thumbnail" in thumb_step.detail
    assert _posted(fake) == []
    assert result.final.name.startswith("take-ep01-t1-sokii-v")
    assert all(e.unit != "thumbnail" for e in load_series(post_desk).spend_log)


@needs_ffmpeg
def test_refinish_reuses_the_saved_cover_without_asking_the_server(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _desk_with_take(post_desk, monkeypatch)
    _finish(post_desk, draw_thumbnail=True)
    assert len(_posted(fake)) == 1

    again = _finish(post_desk, draw_thumbnail=True)

    thumb_step = next(s for s in again.steps if s.step == "thumbnail")
    assert thumb_step.status == "ran", thumb_step.detail
    assert "take-ep01-t1-thumb-v1.jpg" in thumb_step.detail
    assert thumb_step.cost_usd == 0.0
    assert len(_posted(fake)) == 1
    assert again.final.name == "take-ep01-t1-sokii-cover-v2.mp4"
    assert [e.unit for e in load_series(post_desk).spend_log].count("thumbnail") == 1


@needs_ffmpeg
def test_an_audio_error_from_the_cover_route_skips_the_cover_and_keeps_the_take(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _desk_with_take(post_desk, monkeypatch)
    fake.routes[("POST", ROUTE)] = SystemExit(
        "HTTP 502 POST https://drama.example/thumbnail: "
        "operator_audio_failed: The audio could not be made"
    )
    out = io.StringIO()

    result = _finish(post_desk, out, draw_thumbnail=True)

    thumb_step = next(s for s in result.steps if s.step == "thumbnail")
    assert thumb_step.status == "skipped"
    assert "operator_audio_failed" in thumb_step.detail
    assert "still from the take" in thumb_step.detail
    assert result.final.name.startswith("take-ep01-t1-sokii-v")
    assert all(e.unit != "thumbnail" for e in load_series(post_desk).spend_log)
    assert "operator_audio_failed" in out.getvalue()


def test_a_cover_drawn_from_another_clip_is_not_reused(tmp_path: Path) -> None:
    takes = tmp_path
    Image.new("RGB", (8, 8)).save(takes / "take-ep01-t1-thumb-v1.jpg")
    (takes / "take-ep01-t1-thumb-meta-v1.json").write_text(
        json.dumps(
            {"cover_path": "take-ep01-t1-thumb-v1.jpg", "video_url": "https://old"}
        )
    )

    assert saved_cover(takes, "take-ep01-t1", CLIP_URL) is None
    assert saved_cover(takes, "take-ep01-t1", "https://old") == (
        takes / "take-ep01-t1-thumb-v1.jpg"
    )
