"""The client of the server's reel engine: uploads, the route, refusals and silence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import needs_ffmpeg
from test_reel_via_server import legacy_reel_desk, reel_desk  # noqa: F401  (fixtures)

from creation.post.reel_via_server import run_reel
from creation.post.reel_server import (
    ReelServer,
    ReelServerError,
    ReelServerUnreachable,
    content_type,
    operator_mode_supported,
)
from reel_fake_server import FakeReelServer

BASE = "https://drama.test"


class _Run:
    """The bits of ``DramaApiRunSession`` the client uses, on a mock transport."""

    def __init__(self, handler: Any) -> None:
        self.client = httpx.Client(transport=httpx.MockTransport(handler))

    def url(self, path: str) -> str:
        return BASE + path

    def headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        return {"Authorization": "Bearer t", "X-Drama-Session-Id": "s"}

    def get_optional(self, path: str) -> tuple[int, Any]:
        response = self.client.get(self.url(path))
        return response.status_code, response.json()

    def post_optional(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        response = self.client.post(self.url(path), headers=self.headers(), json=body)
        return response.status_code, response.json()


def _server(tmp_path: Path, handler: Any) -> ReelServer:
    return ReelServer(
        tmp_path,
        1,
        run=_Run(handler),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_an_upload_is_signed_put_and_remembered_by_digest(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.path == "/v1/uploads":
            body = json.loads(request.content)
            assert body == {"content_type": "video/mp4", "size_bytes": 5}
            return httpx.Response(
                200,
                json={
                    "upload_url": "https://r2.test/put?sig=1",
                    "headers": {"Content-Type": "video/mp4", "Content-Length": "5"},
                    "file_url": "https://assets.test/tenants/x/drama/operator-uploads/a.mp4",
                    "audio_url": "https://assets.test/tenants/x/drama/operator-uploads/a.mp4",
                },
            )
        assert request.method == "PUT" and request.content == b"hello"
        assert request.headers["Content-Type"] == "video/mp4"
        return httpx.Response(200)

    take = tmp_path / "take.mp4"
    take.write_bytes(b"hello")
    server = _server(tmp_path, handler)

    url, sha = server.upload(take, kind="video")
    again, _ = server.upload(take, kind="video")

    assert url == again == "https://assets.test/tenants/x/drama/operator-uploads/a.mp4"
    assert len(sha) == 64 and server.reused is True
    assert [m for m, _ in calls] == ["POST", "PUT"]  # the second upload sent nothing
    remembered = json.loads((tmp_path / "reels" / "ep01" / "uploads.json").read_text())
    assert remembered[sha]["url"] == url and remembered[sha]["file"] == "take.mp4"


OPENAPI_WITH_OPERATOR = {
    "paths": {
        "/v1/video-generations/{job_id}/episodes/{episode_id}/reel": {"post": {}}
    },
    "components": {
        "schemas": {"DramaEpisodeReelRequest": {"properties": {"operator": {}}}}
    },
}


@pytest.mark.parametrize(
    ("status", "body", "openapi", "error", "words"),
    [
        (502, {"error": {"code": "episode_reel_failed", "message": "nothing was saved"}}, OPENAPI_WITH_OPERATOR,
         ReelServerUnreachable, "episode_reel_failed"),
        (404, {"detail": "Not Found"}, {"paths": {}}, ReelServerError, "no operator mode yet"),
        (422, {"error": {"code": "invalid_request", "message": "operator: extra"}},
         {"paths": OPENAPI_WITH_OPERATOR["paths"], "components": {"schemas": {}}}, ReelServerError,
         "no operator mode yet"),
        (422, {"error": {"code": "episode_reel_invalid", "message": "plan: segments name t9"}},
         OPENAPI_WITH_OPERATOR, ReelServerError, "episode_reel_invalid"),
    ],
)  # fmt: skip
def test_the_route_answers_are_named(
    tmp_path: Path,
    status: int,
    body: dict[str, Any],
    openapi: dict[str, Any],
    error: type[Exception],
    words: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json=openapi)
        return httpx.Response(status, json=body)

    server = _server(tmp_path, handler)

    with pytest.raises(error, match=words):
        server.make("video-1", "episode_01", {"operator": {}})


def test_a_server_that_does_not_answer_is_unreachable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    server = _server(tmp_path, handler)
    take = tmp_path / "take.mp4"
    take.write_bytes(b"x")

    with pytest.raises(ReelServerUnreachable):
        server.make("video-1", "episode_01", {})
    with pytest.raises(ReelServerUnreachable):
        server.upload(take, kind="video")


def test_the_route_posts_to_the_episode_of_the_video_job(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"reel_video_url": "https://assets.test/r.mp4"})

    answer = _server(tmp_path, handler).make("video-1", "episode_01", {"seconds": 15})

    assert seen == ["/v1/video-generations/video-1/episodes/episode_01/reel"]
    assert answer["reel_video_url"] == "https://assets.test/r.mp4"


def test_upload_types() -> None:
    assert content_type(Path("a.mov"), kind="video") == "video/mp4"
    assert content_type(Path("bed.WAV"), kind="audio") == "audio/wav"
    assert content_type(Path("cover.jpeg"), kind="image") == "image/jpeg"
    with pytest.raises(ReelServerError):
        content_type(Path("bed.aiff"), kind="audio")


def test_operator_mode_is_read_from_the_deploys_openapi() -> None:
    route = {"/v1/video-generations/{job_id}/episodes/{episode_id}/reel": {"post": {}}}
    with_operator = {
        "paths": route,
        "components": {
            "schemas": {
                "DramaEpisodeReelRequest": {
                    "properties": {"operator": {}, "seconds": {}}
                }
            }
        },
    }
    app_only = {
        "paths": route,
        "components": {
            "schemas": {"DramaEpisodeReelRequest": {"properties": {"seconds": {}}}}
        },
    }

    assert operator_mode_supported(with_operator) is True
    assert operator_mode_supported(app_only) is False
    assert operator_mode_supported({"paths": {}}) is False
    assert operator_mode_supported("not json") is False


@needs_ffmpeg
def test_reused_uploads_the_server_cannot_read_are_sent_again_once(
    reel_desk: Path,  # noqa: F811
) -> None:
    class Forgetful(FakeReelServer):
        failures = 1
        forgot = 0

        def upload(self, path: Path, *, kind: str) -> tuple[str, str]:
            self.reused = (
                self.forgot == 0
            )  # the first run's uploads came from uploads.json
            return super().upload(path, kind=kind)

        def make(
            self, job_id: str, episode_id: str, body: dict[str, Any]
        ) -> dict[str, Any]:
            if self.failures:
                self.failures -= 1
                raise ReelServerUnreachable("HTTP 502: episode_reel_failed")
            return super().make(job_id, episode_id, body)

        def forget_uploads(self) -> None:
            self.forgot += 1

    server = Forgetful()

    result = run_reel(reel_desk, episode=1, seconds=6.0, server=server)

    assert server.forgot == 1 and result.video is not None
    assert len(server.requests) == 1
