"""The episode cover at finish: the server makes it for free; the kit saves it beside the video."""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import quote

import pytest
from conftest import make_take, make_tone, needs_ffmpeg
from PIL import Image

from creation.ops.floor import approve_board
from creation.ops.folder import next_versioned_path
from creation.post import episode_cover
from creation.post.episode_cover import cover_path, fetch_episode_cover
from creation.post.finish import run_finish
from creation.post.finish_record import latest_finish_record
from creation.post.sfx import SfxCue
from fake_api import FakeApi

ROUTE = "/v1/spines/spine_test/episodes/1/cover"
COVER_URL = "https://cdn.example/tenants/abc/drama/covers/ep01.jpg"
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
ANSWER = {
    "schema_version": "fictora.drama-episode-cover-response.v1",
    "cover_url": COVER_URL,
    "episode_id": "episode_01",
    "generation_job_id": None,
    "cached": False,
    "width": 1080,
    "height": 1920,
}


def _clips(post_desk: Path) -> None:
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


def _fake(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, answer: object = ANSWER
) -> FakeApi:
    fake = FakeApi(post_desk / "ep01" / "api")
    fake.routes[("GET", ROUTE)] = answer
    monkeypatch.setattr(episode_cover, "open_api", lambda desk, episode: fake)
    return fake


def _downloads(calls: list[str]):
    def download(url: str, directory: Path, stem: str) -> Path:
        calls.append(url)
        path = next_versioned_path(directory, stem, ".jpg")
        Image.new("RGB", (108, 192), (40, 60, 90)).save(path, format="JPEG")
        return path

    return download


def _asked(fake: FakeApi) -> list[str]:
    return [c[1] for c in fake.calls if c[0] == "GET" and c[1].startswith(ROUTE)]


def test_the_cover_route_carries_the_takes_stored_clip() -> None:
    assert cover_path("sp1", 3, None) == "/v1/spines/sp1/episodes/3/cover"
    assert cover_path("sp1", 3, CLIP_URL) == (
        f"/v1/spines/sp1/episodes/3/cover?video_url={quote(CLIP_URL, safe='')}"
    )


def test_the_cover_is_saved_beside_the_video_and_reused_when_it_is_the_same(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clips(post_desk)
    fake = _fake(post_desk, monkeypatch)
    takes = post_desk / "ep01" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []

    first = fetch_episode_cover(
        post_desk,
        episode=1,
        take_id="t1",
        takes_dir=takes,
        downloader=_downloads(downloaded),
    )
    again = fetch_episode_cover(
        post_desk,
        episode=1,
        take_id="t1",
        takes_dir=takes,
        downloader=_downloads(downloaded),
    )

    assert first.path == takes / "cover-ep01-v1.jpg" and first.path.is_file()
    assert not first.reused and again.reused and again.path == first.path
    assert downloaded == [COVER_URL], "the same server cover is downloaded once"
    assert _asked(fake) == [f"{ROUTE}?video_url={quote(CLIP_URL, safe='')}"] * 2
    meta = json.loads((takes / "cover-ep01-v1.json").read_text(encoding="utf-8"))
    assert meta["cover_url"] == COVER_URL and meta["video_url"] == CLIP_URL
    assert not any(c[0] == "POST" for c in fake.calls), "a cover is never a paid draw"


def test_a_new_cover_gets_a_new_version(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clips(post_desk)
    takes = post_desk / "ep01" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    _fake(post_desk, monkeypatch)
    fetch_episode_cover(
        post_desk,
        episode=1,
        take_id="t1",
        takes_dir=takes,
        downloader=_downloads(downloaded),
    )
    _fake(post_desk, monkeypatch, {**ANSWER, "cover_url": COVER_URL + "?v=2"})

    second = fetch_episode_cover(
        post_desk,
        episode=1,
        take_id="t1",
        takes_dir=takes,
        downloader=_downloads(downloaded),
    )

    assert second.path == takes / "cover-ep01-v2.jpg"
    assert (takes / "cover-ep01-v1.jpg").is_file(), (
        "an older cover is never overwritten"
    )


def test_without_a_stored_clip_the_server_gives_the_delivered_episodes_cover(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(post_desk, monkeypatch)
    takes = post_desk / "ep01" / "takes"
    takes.mkdir(parents=True, exist_ok=True)

    saved = fetch_episode_cover(
        post_desk, episode=1, take_id="t1", takes_dir=takes, downloader=_downloads([])
    )

    assert _asked(fake) == [ROUTE]
    assert saved.path is not None


@pytest.mark.parametrize(
    ("status", "body", "words"),
    [
        (404, {"detail": "Not Found"}, "no episode cover route yet"),
        (
            404,
            {"error": {"code": "episode_video_not_delivered", "message": "no video"}},
            "HTTP 404",
        ),
        (
            502,
            {"error": {"code": "episode_cover_failed", "message": "could not be made"}},
            "HTTP 502",
        ),
    ],
)
def test_a_cover_the_server_cannot_give_saves_nothing_and_says_why(
    post_desk: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: dict,
    words: str,
) -> None:
    _clips(post_desk)
    fake = _fake(post_desk, monkeypatch)
    monkeypatch.setattr(fake, "get_optional", lambda path: (status, body))
    takes = post_desk / "ep01" / "takes"
    takes.mkdir(parents=True, exist_ok=True)

    saved = fetch_episode_cover(
        post_desk, episode=1, take_id="t1", takes_dir=takes, downloader=_downloads([])
    )

    assert saved.path is None and words in saved.note
    assert not list(takes.glob("cover-*"))


# --- the cover step in finish ------------------------------------------------------------------------


def _desk_with_take(post_desk: Path) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    _clips(post_desk)
    board = post_desk / "ep01" / "boards" / "board-ep01-t1-1-v1.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (192, 336), (200, 150, 110)).save(board)
    approve_board(post_desk, episode=1, take_id="t1", image=board)


def _finish(post_desk: Path, **kwargs: object):
    def fake_sfx(cue: SfxCue, target: Path) -> Path:
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.8)

    return run_finish(
        post_desk,
        sfx_render=fake_sfx,
        facts_fetcher=lambda *a: None,
        stream=io.StringIO(),
        **kwargs,
    )


@needs_ffmpeg
def test_finish_saves_the_cover_and_names_it_in_the_record(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, server_cover: None
) -> None:
    _desk_with_take(post_desk)
    _fake(post_desk, monkeypatch)
    monkeypatch.setattr(episode_cover, "_download", _downloads([]))

    result = _finish(post_desk)

    step = next(s for s in result.steps if s.step == "cover")
    assert step.status == "ran" and step.cost_usd == 0, step
    assert step.output is None, "the finished video is not changed by the cover"
    assert result.final.name.startswith("take-ep01-t1-sokii-v"), result.final
    record = latest_finish_record(post_desk, 1, "t1")
    assert record is not None
    body = json.loads(record.path.read_text(encoding="utf-8"))
    assert body["cover"] == "ep01/takes/cover-ep01-v1.jpg"


@needs_ffmpeg
def test_no_cover_skips_the_ask(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _desk_with_take(post_desk)
    fake = _fake(post_desk, monkeypatch)

    result = _finish(post_desk, cover=False)

    step = next(s for s in result.steps if s.step == "cover")
    assert step.status == "skipped" and step.detail == "--no-cover"
    assert _asked(fake) == []
