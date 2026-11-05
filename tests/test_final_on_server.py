"""``approve --gate post`` records "this episode is final" on the server (after fictora-drama #1358).

The server keeps which file is final, who and when; the desk's ``series.json`` post gate and the
``…_final_vN_…`` file are only a convenience copy written after the server said yes. Every call goes
to a stand-in server (``httpx.MockTransport``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from creation.cli_ops import FINAL_NOT_RECORDED
from creation.cli_ops import main as ops_main
from creation.ops.state import load_series
from creation.post import final_approval, server_finish
from creation.post.episode_name import next_episode_path

BASE = "https://drama.example"
WHEN = "2026-11-05T10:00:00Z"


class Server:
    """Records the final it is sent when the digest is a saved copy; standing shows it back."""

    def __init__(self, saved: set[str]) -> None:
        self.saved = saved
        self.final: dict[str, Any] | None = None
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "PUT":
            digest = json.loads(request.content)["export_sha256"]
            if digest not in self.saved:
                return httpx.Response(
                    409,
                    json={
                        "error": {
                            "code": "export_not_on_episode",
                            "message": "no",
                            "details": {},
                        }
                    },
                )
            repeat = self.final is not None and self.final["export_sha256"] == digest
            if not repeat:
                self.final = {
                    "episode_id": "ep_01",
                    "export_sha256": digest,
                    "approved_at": WHEN,
                    "approved_by": "session-0123456789abcdef",
                    "approved_by_kind": "operator",
                    "current": True,
                }
            return httpx.Response(
                200,
                json={"recorded": not repeat, "approval": self.final, "replaced": None},
            )
        row: dict[str, Any] = {"episode_ordinal": 1, "video": "finished"}
        if self.final is not None:
            row["final"] = self.final
        return httpx.Response(200, json={"episodes": [row]})


def _file(desk: Path, body: bytes) -> Path:
    path = next_episode_path(desk / "ep01" / "takes", desk, 1)
    path.write_bytes(body)
    return path


def _stand_in(monkeypatch: pytest.MonkeyPatch, server: Server) -> None:
    monkeypatch.setattr(
        server_finish, "_api", lambda desk: (BASE, {"Authorization": "Bearer t"})
    )
    real = final_approval.approve_final
    monkeypatch.setattr(
        final_approval,
        "approve_final",
        lambda **kw: real(**{**kw, "transport": httpx.MockTransport(server)}),
    )


def _approve(desk: Path, *extra: str) -> int:
    return ops_main(
        ["approve", "--desk", str(desk), "--gate", "post", "--episode", "1", *extra]
    )


def test_the_yes_goes_to_the_server_first_and_the_desk_keeps_only_a_copy(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    watched = _file(post_desk, b"server copy")
    digest = hashlib.sha256(b"server copy").hexdigest()
    server = Server({digest})
    _stand_in(monkeypatch, server)

    assert _approve(post_desk) == 0

    put, standing = server.requests
    assert (put.method, put.url.path) == (
        "PUT",
        "/v1/spines/spine_test/episodes/episode_01/final",
    )
    assert json.loads(put.content)["export_sha256"] == digest
    assert (standing.method, standing.url.path) == (
        "GET",
        "/v1/spines/spine_test/standing",
    )
    out = capsys.readouterr().out
    assert f"Recorded on the server: episode 1 is final = {watched.name}" in out
    post = load_series(post_desk).episodes[0].post
    assert post.status == "approved" and post.at_utc == WHEN, (
        "the desk copy carries the server's time"
    )
    assert post.note is not None and digest[:12] in post.note
    assert any(
        "_final_" in p.name for p in (post_desk / "ep01" / "takes").glob("*.mp4")
    )


def test_a_refused_yes_writes_nothing_on_the_desk(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _file(post_desk, b"finished on this laptop")
    _stand_in(monkeypatch, Server(saved=set()))

    assert _approve(post_desk) == FINAL_NOT_RECORDED

    err = capsys.readouterr().err
    assert (
        "not one of the server's saved copies" in err and "Nothing was recorded" in err
    )
    assert load_series(post_desk).episodes[0].post.status == "pending"
    assert not any(
        "_final_" in p.name for p in (post_desk / "ep01" / "takes").glob("*.mp4")
    )


def test_approving_the_same_file_again_says_nothing_changed(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _file(post_desk, b"server copy")
    server = Server({hashlib.sha256(b"server copy").hexdigest()})
    _stand_in(monkeypatch, server)
    assert _approve(post_desk) == 0
    capsys.readouterr()

    assert _approve(post_desk) == 0

    assert (
        "Already the final on the server (nothing changed)" in capsys.readouterr().out
    )


def test_no_file_on_the_desk_records_nothing(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = Server(saved=set())
    _stand_in(monkeypatch, server)

    assert _approve(post_desk) == FINAL_NOT_RECORDED

    assert server.requests == []
    assert "No finished file for episode 1" in capsys.readouterr().err
    assert load_series(post_desk).episodes[0].post.status == "pending"


def test_a_yes_the_server_does_not_show_back_is_not_taken_as_recorded(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _file(post_desk, b"server copy")
    server = Server({hashlib.sha256(b"server copy").hexdigest()})
    real_call = server.__call__

    def forgetful(request: httpx.Request) -> httpx.Response:
        answer = real_call(request)
        if request.method == "GET":
            return httpx.Response(200, json={"episodes": [{"episode_ordinal": 1}]})
        return answer

    _stand_in(monkeypatch, forgetful)  # type: ignore[arg-type]

    assert _approve(post_desk) == FINAL_NOT_RECORDED
    assert "does not show this file as final" in capsys.readouterr().err
    assert load_series(post_desk).episodes[0].post.status == "pending"
