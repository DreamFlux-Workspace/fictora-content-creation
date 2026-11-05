"""freeze, soften, zoom, voice-fx, revoice and caption are made and saved on the server (5 Nov 2026).

Gap 2 of the audit: these commands edited a file on the laptop, so the server
never had the video the creator posted. Each now sends its edit to the server
and prints the answer; no file on the desk is written. Every call goes to a
stand-in server (``httpx.MockTransport``).
"""

# The stand-in server fixture is imported, so the tests that take it name it again.
# ruff: noqa: F401, F811

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_kit_cut_and_blur_on_server import JOB, POST_EDIT, Server, _files, server

from creation.cli_produce import main
from creation.post import episode_caption, server_finish

REVOICE = f"/v1/video-generations/{JOB}/takes/2/revoice"
TAKE = ["--desk", "{desk}", "--episode", "1", "--take", "t2"]


def _run(desk: Path, command: str, *settings: str) -> int:
    return main([command, *(part.format(desk=desk) for part in TAKE), *settings])


@pytest.mark.parametrize(
    ("command", "settings", "sent", "said"),
    [
        (
            "freeze",
            ("--at", "1.5", "--hold", "0.6"),
            {"freeze_frames": [{"at_seconds": 1.5, "hold_seconds": 0.6}]},
            "1 freeze",
        ),
        (
            "soften",
            ("--cut", "2.0", "--cut", "4.25"),
            {
                "soften": [
                    {"at_seconds": 2.0, "snap_to_cut": True},
                    {"at_seconds": 4.25, "snap_to_cut": True},
                ]
            },
            "2 softened jumps",
        ),
        (
            "zoom",
            (
                "--from",
                "3",
                "--to",
                "4.5",
                "--factor",
                "1.3",
                "--center",
                "384,560@768x1366",
            ),
            {
                "zooms": [
                    {
                        "start_seconds": 3.0,
                        "end_seconds": 4.5,
                        "zoom": 1.3,
                        "center_x": 0.5,
                        "center_y": 0.41,
                    }
                ]
            },
            "1 zoom",
        ),
        (
            "voice-fx",
            ("--range", "0-1.5", "--range", "3-4", "--preset", "phone"),
            {
                "voice_fx": [
                    {"start_seconds": 0.0, "end_seconds": 1.5, "preset": "phone"},
                    {"start_seconds": 3.0, "end_seconds": 4.0, "preset": "phone"},
                ]
            },
            "2 voice effects",
        ),
    ],
)
def test_each_edit_is_sent_to_the_takes_post_edit_and_no_file_changes(
    post_desk: Path,
    server: Server,
    capsys: pytest.CaptureFixture[str],
    command: str,
    settings: tuple[str, ...],
    sent: dict[str, Any],
    said: str,
) -> None:
    before = _files(post_desk)

    assert _run(post_desk, command, *settings) == 0

    assert server.sent() == [(POST_EDIT, {**sent, "add_to_current_edit": True})]
    assert _files(post_desk) == before, "the kit edits no file"
    out = capsys.readouterr().out
    assert (
        f"t2 edited on the server ({said})" in out
        and "nothing on this laptop was changed" in out
    )


def test_a_preview_prices_the_edit_and_changes_nothing(
    post_desk: Path,
    server: Server,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run(post_desk, "freeze", "--at", "1", "--hold", "0.5", "--preview") == 0

    ((path, _),) = server.sent()
    assert path == POST_EDIT + "/quote"
    assert "Preview only: nothing was changed or charged." in capsys.readouterr().out


def test_revoice_asks_the_server_to_revoice_the_character_by_its_cast_id(
    post_desk: Path,
    server: Server,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _files(post_desk)

    assert _run(post_desk, "revoice", "--cast", "kenji", "--yes") == 0

    (request,) = server.requests
    assert request.url.path == REVOICE
    assert json.loads(request.content) == {"cast_id": "cast_kenji", "dry_run": False}
    assert _files(post_desk) == before
    assert "Kenji's lines in take t2 re-voiced on the server" in capsys.readouterr().out


def test_revoice_without_yes_prints_the_price_and_spends_nothing(
    post_desk: Path,
    server: Server,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run(post_desk, "revoice", "--cast", "kenji") == 2

    (request,) = server.requests
    assert request.url.path == REVOICE
    assert json.loads(request.content) == {"cast_id": "cast_kenji", "dry_run": True}
    err = capsys.readouterr().err
    assert "Re-voicing Kenji's lines in take t2 would cost" in err
    assert "Nothing was changed or charged; add --yes to apply." in err


def test_revoice_reads_the_cast_from_a_desk_written_with_a_tilde(
    post_desk: Path,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(post_desk.parent))

    code = main(
        ["revoice", "--desk", f"~/{post_desk.name}", "--episode", "1", "--take", "t2",
         "--cast", "Kenji", "--yes"]
    )  # fmt: skip

    assert code == 0
    assert json.loads(server.requests[0].content)["cast_id"] == "cast_kenji"


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (["revoice", *TAKE, "--cast", "kenji", "--take-file", "F.mp4"],
         "revoice is a take edit on the server now and edits no file: drop --take-file"),
        (["voice-fx", *TAKE, "--range", "0-1", "--preset", "phone", "--file", "F.mp4"],
         "voice-fx is a take edit on the server now and edits no file: drop --file"),
        (["caption", "--desk", "{desk}", "--episode", "1", "--take", "F.mp4"],
         "caption is made on the server now and edits no file: drop --take"),
        (["tempo", "--desk", "{desk}", "--factor", "0.9"],
         "tempo edits one take on the server: add --episode N --take tK"),
        (["trim", "--desk", "{desk}", "--episode", "1", "--start", "0.5"],
         "trim edits one take on the server: add --episode N --take tK"),
    ],
)  # fmt: skip
def test_an_old_way_of_naming_the_take_is_one_plain_stop(
    argv: list[str],
    says: str,
    post_desk: Path,
    server: Server,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _files(post_desk)

    assert main([part.format(desk=post_desk) for part in argv]) == 2

    assert server.requests == []
    assert _files(post_desk) == before
    err = capsys.readouterr().err
    assert says in err and "Nothing was changed or charged." in err
    assert "usage:" not in err


def test_a_take_file_is_refused_before_any_call(
    post_desk: Path,
    server: Server,
    capsys: pytest.CaptureFixture[str],
) -> None:
    take = post_desk / "ep01" / "takes" / "take-ep01-t2-raw-v1.mp4"

    assert (
        _run(post_desk, "zoom", "--from", "1", "--to", "2", "--take-file", str(take))
        == 2
    )

    assert server.requests == []
    assert "edits no file: drop --take-file" in capsys.readouterr().err


class CaptionServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/recaption"):
            return httpx.Response(
                200,
                json={
                    "episode": {"video_url": "https://media.example/recaptioned.mp4"}
                },
            )
        return httpx.Response(
            200,
            json={
                "status": "saved",
                "video_url": "https://media.example/captioned.mp4",
            },
        )


@pytest.fixture
def caption_server(
    post_desk: Path, server: Server, monkeypatch: pytest.MonkeyPatch
) -> CaptionServer:
    stand_in = CaptionServer()
    monkeypatch.setattr(
        server_finish, "_desk_episode_id", lambda desk, episode: "episode_01"
    )
    real = episode_caption.send_caption
    monkeypatch.setattr(
        episode_caption,
        "send_caption",
        lambda **kw: real(**{**kw, "transport": httpx.MockTransport(stand_in)}),
    )
    return stand_in


@pytest.mark.parametrize(
    ("style", "recipe"), [("bold", "bold"), ("subtle", "house"), ("plain", "house")]
)
def test_caption_asks_the_server_to_burn_the_shows_captions(
    post_desk: Path,
    caption_server: CaptionServer,
    capsys: pytest.CaptureFixture[str],
    style: str,
    recipe: str,
) -> None:
    before = _files(post_desk)

    assert (
        main(
            [
                "caption",
                "--desk",
                str(post_desk),
                "--episode",
                "1",
                "--caption-style",
                style,
            ]
        )
        == 0
    )

    (request,) = caption_server.requests
    assert request.method == "PUT"
    assert (
        request.url.path
        == f"/v1/video-generations/{JOB}/episodes/episode_01/captions/enable"
    )
    assert json.loads(request.content) == {"caption_style": recipe}
    assert request.headers["Idempotency-Key"].startswith("kit-caption-episode_01-")
    assert _files(post_desk) == before, "nothing is burned on this laptop"
    assert "https://media.example/captioned.mp4" in capsys.readouterr().out


def test_caption_retime_asks_the_server_to_recaption(
    post_desk: Path, caption_server: CaptionServer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(["caption", "--desk", str(post_desk), "--episode", "1", "--retime"]) == 0
    )

    (request,) = caption_server.requests
    assert (request.method, request.url.path) == (
        "POST",
        f"/v1/video-generations/{JOB}/episodes/episode_01/recaption",
    )
    assert "re-timed its captions" in capsys.readouterr().out


def test_a_show_without_captions_sends_nothing(
    post_desk: Path, caption_server: CaptionServer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "caption",
                "--desk",
                str(post_desk),
                "--episode",
                "1",
                "--caption-style",
                "none",
            ]
        )
        == 0
    )

    assert caption_server.requests == []
    assert "nothing to caption" in capsys.readouterr().out


def test_a_caption_refusal_is_one_plain_line(
    post_desk: Path,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        server_finish, "_desk_episode_id", lambda desk, episode: "episode_01"
    )

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {"code": "captions_already_on", "message": "Captions are on."}
            },
        )

    real = episode_caption.send_caption
    monkeypatch.setattr(
        episode_caption,
        "send_caption",
        lambda **kw: real(**{**kw, "transport": httpx.MockTransport(refuse)}),
    )

    assert (
        main(
            [
                "caption",
                "--desk",
                str(post_desk),
                "--episode",
                "1",
                "--caption-style",
                "bold",
            ]
        )
        == 2
    )

    out = capsys.readouterr().out
    assert (
        "Refused: The server would not caption it (HTTP 409): Captions are on." in out
    )
    assert "The delivered episode was not changed." in out


def test_tempo_never_changes_the_take_on_this_laptop(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.post import take_handles

    monkeypatch.setattr(
        take_handles, "take_coordinate", lambda desk, episode, take_id: None
    )
    before = _files(post_desk)

    assert _run(post_desk, "tempo", "--factor", "0.9") == 2

    assert _files(post_desk) == before, "no local tempo"
    assert "the server cannot find it to change its speed" in capsys.readouterr().err
