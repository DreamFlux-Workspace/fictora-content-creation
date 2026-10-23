"""The episode cover: the kit uploads the finished episode once and saves the server's free cover beside it.

Every call here goes to a stand-in server: the upload slot, the signed PUT, the
cover route and the JPEG download are all faked. Nothing reaches a real server.
"""

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
from creation.post.episode_cover import cover_path, fetch_episode_cover, other_takes
from creation.post.finish import run_finish
from creation.post.finish_record import latest_finish_record
from creation.post.sfx import SfxCue
from fake_api import FakeApi

ROUTE = "/v1/spines/spine_test/episodes/1/cover"
COVER_URL = "https://cdn.example/tenants/abc/drama/covers/ep01.jpg"
STORED_URL = "https://r2.example/tenants/abc/uploads/episode.mp4"
SIGNED_PUT = "https://r2.example/signed-put?sig=1"
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
SLOT = {
    "schema_version": "fictora.drama-operator-upload-response.v1",
    "upload_url": SIGNED_PUT,
    "method": "PUT",
    "headers": {"Content-Type": "video/mp4", "Content-Length": "9"},
    "audio_url": STORED_URL,
    "file_url": STORED_URL,
    "expires_at": "2026-10-23T12:00:00Z",
    "max_bytes": 9,
}


class Transfers:
    """The signed PUTs and JPEG downloads a test saw."""

    def __init__(self, put_status: int = 200) -> None:
        self.puts: list[tuple[str, int, dict]] = []
        self.downloads: list[str] = []
        self.put_status = put_status

    def put(self, url: str, content: bytes, headers: dict) -> int:
        self.puts.append((url, len(content), dict(headers)))
        return self.put_status

    def download(self, url: str, directory: Path, stem: str) -> Path:
        self.downloads.append(url)
        path = next_versioned_path(directory, stem, ".jpg")
        Image.new("RGB", (108, 192), (40, 60, 90)).save(path, format="JPEG")
        return path


def _fake(
    post_desk: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: object = ANSWER,
    slot: object = SLOT,
) -> FakeApi:
    fake = FakeApi(post_desk / "ep01" / "api")
    fake.routes[("GET", ROUTE)] = answer
    fake.routes[("POST", "/v1/uploads")] = slot
    monkeypatch.setattr(episode_cover, "open_api", lambda desk, episode: fake)
    return fake


def _episode_file(post_desk: Path, body: bytes = b"episode-1") -> Path:
    video = post_desk / "ep01" / "takes" / "post-test_ep01_draft_v1_2026-10-23.mp4"
    video.write_bytes(body)
    return video


def _asked(fake: FakeApi) -> list[str]:
    return [c[1] for c in fake.calls if c[0] == "GET" and c[1].startswith(ROUTE)]


def _uploads(fake: FakeApi) -> list[dict]:
    return [c[2] for c in fake.calls if c[0] == "POST" and c[1] == "/v1/uploads"]


def test_the_cover_route_carries_the_uploaded_episode() -> None:
    assert cover_path("sp1", 3, STORED_URL) == (
        f"/v1/spines/sp1/episodes/3/cover?video_url={quote(STORED_URL, safe='')}"
    )


def test_the_finished_episode_is_uploaded_once_and_its_cover_saved_beside_it(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(post_desk, monkeypatch)
    video = _episode_file(post_desk)
    seen = Transfers()

    first = fetch_episode_cover(
        post_desk, episode=1, video=video, downloader=seen.download, putter=seen.put
    )
    again = fetch_episode_cover(
        post_desk, episode=1, video=video, downloader=seen.download, putter=seen.put
    )

    assert first.path == video.parent / "cover-ep01-v1.jpg" and first.path.is_file()
    assert not first.reused and again.reused and again.path == first.path
    assert _uploads(fake) == [{"content_type": "video/mp4", "size_bytes": 9}], (
        "the same file is uploaded once"
    )
    assert seen.puts == [(SIGNED_PUT, 9, SLOT["headers"])]
    assert _asked(fake) == [f"{ROUTE}?video_url={quote(STORED_URL, safe='')}"] * 2
    assert seen.downloads == [COVER_URL], "the same server cover is downloaded once"
    meta = json.loads((video.parent / "cover-ep01-v1.json").read_text(encoding="utf-8"))
    assert meta["cover_url"] == COVER_URL and meta["video_url"] == STORED_URL
    assert meta["video"] == video.name


def test_a_new_cut_is_uploaded_again_and_its_cover_gets_the_next_version(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = Transfers()
    fake = _fake(post_desk, monkeypatch)
    video = _episode_file(post_desk)
    fetch_episode_cover(
        post_desk, episode=1, video=video, downloader=seen.download, putter=seen.put
    )
    newer = _episode_file(post_desk, b"episode-1-recut")
    fake.routes[("GET", ROUTE)] = {**ANSWER, "cover_url": COVER_URL + "?v=2"}

    second = fetch_episode_cover(
        post_desk, episode=1, video=newer, downloader=seen.download, putter=seen.put
    )

    assert second.path == video.parent / "cover-ep01-v2.jpg"
    assert (video.parent / "cover-ep01-v1.jpg").is_file(), (
        "an older cover is never overwritten"
    )
    assert len(_uploads(fake)) == 2


@pytest.mark.parametrize(
    ("route", "status", "body", "words"),
    [
        ("cover", 404, {"detail": "Not Found"}, "no episode cover route yet"),
        (
            "cover",
            422,
            {"error": {"code": "video_url_not_owned", "message": "not ours"}},
            "HTTP 422",
        ),
        (
            "cover",
            502,
            {"error": {"code": "episode_cover_failed", "message": "could not"}},
            "HTTP 502",
        ),
        (
            "upload",
            409,
            {"error": {"code": "operator_upload_unavailable", "message": "off"}},
            "upload",
        ),
    ],
)
def test_a_cover_that_cannot_be_had_saves_nothing_and_says_why_in_one_line(
    post_desk: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    status: int,
    body: dict,
    words: str,
) -> None:
    fake = _fake(post_desk, monkeypatch)
    if route == "cover":
        monkeypatch.setattr(fake, "get_optional", lambda path: (status, body))
    else:
        monkeypatch.setattr(
            fake, "post_optional", lambda path, payload, **_k: (status, body)
        )
    video = _episode_file(post_desk)
    seen = Transfers()

    saved = fetch_episode_cover(
        post_desk, episode=1, video=video, downloader=seen.download, putter=seen.put
    )

    assert saved.path is None and words in saved.note and "\n" not in saved.note
    assert saved.note.startswith("no cover this time")
    assert not list(video.parent.glob("cover-*")) and seen.downloads == []


def test_a_storage_refusal_stops_before_the_cover_is_asked(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(post_desk, monkeypatch)
    seen = Transfers(put_status=403)

    saved = fetch_episode_cover(
        post_desk,
        episode=1,
        video=_episode_file(post_desk),
        downloader=seen.download,
        putter=seen.put,
    )

    assert saved.path is None and "storage refused the upload (HTTP 403)" in saved.note
    assert _asked(fake) == []


def test_other_takes_reads_the_planned_and_finished_takes(post_desk: Path) -> None:
    assert other_takes(post_desk, 1, "t1") == [], "a 15 s episode is one take"
    (post_desk / "ep01" / "takes" / "take-ep01-t2-finish-v1.json").write_text("{}")
    assert other_takes(post_desk, 1, "t1") == ["t2"]


# --- once per episode: finish of a one-take episode, or join -------------------------------------


def _desk_with_take(post_desk: Path) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
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


def _transfers(monkeypatch: pytest.MonkeyPatch) -> Transfers:
    seen = Transfers()
    monkeypatch.setattr(episode_cover, "_put", seen.put)
    monkeypatch.setattr(episode_cover, "_download", seen.download)
    return seen


@needs_ffmpeg
def test_finish_of_a_one_take_episode_uploads_the_finished_file_and_records_the_cover(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, server_cover: None
) -> None:
    _desk_with_take(post_desk)
    fake = _fake(post_desk, monkeypatch)
    seen = _transfers(monkeypatch)

    result = _finish(post_desk)

    step = next(s for s in result.steps if s.step == "cover")
    assert step.status == "ran" and step.cost_usd == 0, step
    assert step.output is None, "the finished video is not changed by the cover"
    assert result.final.name.startswith("take-ep01-t1-sokii-v"), result.final
    assert _uploads(fake) == [
        {"content_type": "video/mp4", "size_bytes": result.final.stat().st_size}
    ], "the finished, marked file is what is uploaded"
    assert len(seen.puts) == 1 and seen.downloads == [COVER_URL]
    record = latest_finish_record(post_desk, 1, "t1")
    assert record is not None
    body = json.loads(record.path.read_text(encoding="utf-8"))
    assert body["cover"] == "ep01/takes/cover-ep01-v1.jpg"


@needs_ffmpeg
def test_finish_of_one_take_of_a_longer_episode_leaves_the_cover_to_join(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, server_cover: None
) -> None:
    _desk_with_take(post_desk)
    (post_desk / "ep01" / "takes" / "take-ep01-t2-finish-v1.json").write_text("{}")
    fake = _fake(post_desk, monkeypatch)

    result = _finish(post_desk)

    step = next(s for s in result.steps if s.step == "cover")
    assert step.status == "skipped" and "comes after `join`" in step.detail
    assert _uploads(fake) == [] and _asked(fake) == []


@needs_ffmpeg
def test_no_cover_skips_the_upload(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, server_cover: None
) -> None:
    _desk_with_take(post_desk)
    fake = _fake(post_desk, monkeypatch)

    result = _finish(post_desk, cover=False)

    step = next(s for s in result.steps if s.step == "cover")
    assert step.status == "skipped" and step.detail == "--no-cover"
    assert _asked(fake) == [] and _uploads(fake) == []


@needs_ffmpeg
def test_join_uploads_the_marked_episode_once_and_saves_one_cover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server_cover: None
) -> None:
    from test_join_seam_fix import finished

    from creation.ops.floor import init_series_desk
    from creation.post.join import run_join

    desk = init_series_desk(tmp_path, "Seam Fix", band="30s", episode_count=1)
    finished(desk, "t1", seconds=3.0, level=0.05)
    finished(desk, "t2", seconds=3.0, level=0.05, seed=2)
    fake = FakeApi(desk / "ep01" / "api")
    fake.routes[("GET", "/v1/spines/sp_join/episodes/1/cover")] = ANSWER
    fake.routes[("POST", "/v1/uploads")] = SLOT
    monkeypatch.setattr(episode_cover, "open_api", lambda d, e: fake)
    monkeypatch.setattr(episode_cover, "spine_id", lambda d: "sp_join")
    seen = _transfers(monkeypatch)

    result = run_join(desk, episodes=(1,), gain_match=False, stream=io.StringIO())

    assert result.complete and result.marked is not None
    assert result.cover == result.marked.parent / "cover-ep01-v1.jpg"
    assert _uploads(fake) == [
        {"content_type": "video/mp4", "size_bytes": result.marked.stat().st_size}
    ]
    assert len(seen.puts) == 1 and seen.downloads == [COVER_URL]
    assert any(
        n.startswith("cover: cover saved as `cover-ep01-v1.jpg`") for n in result.notes
    )
    assert result.as_json()["cover"] == str(result.cover)


@needs_ffmpeg
def test_join_finishes_anyway_when_the_cover_cannot_be_had(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server_cover: None
) -> None:
    from test_join_seam_fix import finished

    from creation.ops.floor import init_series_desk
    from creation.post.join import run_join

    desk = init_series_desk(tmp_path, "Seam Fix", band="30s", episode_count=1)
    finished(desk, "t1", seconds=3.0, level=0.05)
    finished(desk, "t2", seconds=3.0, level=0.05, seed=2)
    fake = FakeApi(desk / "ep01" / "api")
    fake.routes[("POST", "/v1/uploads")] = SLOT
    monkeypatch.setattr(
        fake,
        "get_optional",
        lambda path: (502, {"error": {"code": "episode_cover_failed"}}),
    )
    monkeypatch.setattr(episode_cover, "open_api", lambda d, e: fake)
    monkeypatch.setattr(episode_cover, "spine_id", lambda d: "sp_join")
    _transfers(monkeypatch)

    result = run_join(desk, episodes=(1,), gain_match=False, stream=io.StringIO())

    assert result.complete and result.marked is not None and result.cover is None
    assert any(
        n.startswith("cover: no cover this time")
        and n.endswith("the joined episode is unchanged")
        for n in result.notes
    ), result.notes
