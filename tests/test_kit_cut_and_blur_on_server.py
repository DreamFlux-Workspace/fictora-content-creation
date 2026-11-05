"""``trim --cut`` and ``blur --box`` are take edits saved on the server (after kit #316 and fictora-drama #1189).

Since the kit's finish downloads the server's finished episode, an edit made on
a laptop file never reached the posted video. Both now go to the take's
``post-edit`` route with ``add_to_current_edit``, and nothing on the desk is
written. Every call goes to a stand-in server (``httpx.MockTransport``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from creation.cli_produce import main
from creation.post import server_finish, take_edit, take_handles

BASE = "https://drama.example"
JOB = "job_video_1"
POST_EDIT = f"/v1/video-generations/{JOB}/takes/2/post-edit"
SAVED = "The edit is saved. The episode is being put back together with it and plays the new cut soon."


class Server:
    """Keeps every request; answers a post edit as taken and a quote as free."""

    def __init__(self, quote_tokens: int = 0) -> None:
        self.requests: list[httpx.Request] = []
        self.quote_tokens = quote_tokens

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/revoice") and json.loads(request.content).get(
            "dry_run"
        ):
            return httpx.Response(200, json={"quote": {"tokens": self.quote_tokens}})
        if request.url.path.endswith("/quote"):
            return httpx.Response(
                200,
                json={
                    "tokens": self.quote_tokens,
                    "voiced_lines": 0,
                    "voiced_characters": 0,
                    "new_sounds": 0,
                },
            )
        return httpx.Response(200, json={
            "job_id": JOB, "take_index": 2, "tokens": 0, "rendered_cue_count": 0, "synthesized_line_count": 0,
            "cost_usd": 0.0, "episode": None, "episode_not_redelivered": SAVED})  # fmt: skip

    def sent(self) -> list[tuple[str, dict[str, Any]]]:
        return [(r.url.path, json.loads(r.content)) for r in self.requests]

    def applied(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (path, body)
            for path, body in self.sent()
            if not path.endswith("/quote") and not body.get("dry_run")
        ]


def install_stand_in(monkeypatch: pytest.MonkeyPatch) -> Server:
    stand_in = Server()
    monkeypatch.setattr(
        server_finish, "_api", lambda desk: (BASE, {"Authorization": "Bearer t"})
    )
    monkeypatch.setattr(
        take_handles,
        "take_coordinate",
        lambda desk, episode, take_id: take_handles.TakeCoordinate(JOB, 2, "job_t2"),
    )
    real = take_edit.send_take_edit
    monkeypatch.setattr(
        take_edit,
        "send_take_edit",
        lambda **kw: real(**{**kw, "transport": httpx.MockTransport(stand_in)}),
    )
    return stand_in


@pytest.fixture
def server(post_desk: Path, monkeypatch: pytest.MonkeyPatch) -> Server:
    return install_stand_in(monkeypatch)


def _files(desk: Path) -> set[Path]:
    return {path for path in desk.rglob("*") if path.is_file()}


def test_a_cut_is_saved_on_the_server_and_no_file_on_the_desk_changes(
    post_desk: Path, server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _files(post_desk)

    code = main(
        [
            "trim",
            "--desk",
            str(post_desk),
            "--episode",
            "1",
            "--take",
            "t2",
            "--cut",
            "10.17-12.15",
        ]
    )

    assert code == 0
    assert server.applied() == [
        (
            POST_EDIT,
            {
                "trims": [{"start_seconds": 10.17, "end_seconds": 12.15}],
                "add_to_current_edit": True,
            },
        )
    ]
    assert (
        server.requests[-1].headers["Idempotency-Key"].startswith("kit-take-edit-t2-")
    )
    assert _files(post_desk) == before, "the kit edits no file"
    out = capsys.readouterr().out
    assert "t2 edited on the server (1 cut stretch): free (0 tokens)." in out
    assert "saved there as a take edit; nothing on this laptop was changed" in out
    assert SAVED in out
    assert "No re-join to run" in out and "every later join keeps it" in out
    assert "run a plain finish in a few minutes" in out


def test_blur_boxes_go_as_fractions_of_the_frame_over_one_window(
    post_desk: Path, server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _files(post_desk)

    code = main(["blur", "--desk", str(post_desk), "--episode", "1", "--take", "t2",
                 "--box", "0.1,0.25,0.4,0.1", "--box", "77,410,307,137@768x1366",
                 "--from", "2.38", "--to", "6.62"])  # fmt: skip

    assert code == 0
    assert server.applied() == [(POST_EDIT, {
        "blurs": [
            {"start_seconds": 2.38, "end_seconds": 6.62, "x": 0.1, "y": 0.25, "width": 0.4, "height": 0.1},
            {"start_seconds": 2.38, "end_seconds": 6.62, "x": 0.1003, "y": 0.3001, "width": 0.3997, "height": 0.1003},
        ],
        "add_to_current_edit": True,
    })]  # fmt: skip
    assert _files(post_desk) == before
    out = capsys.readouterr().out
    assert "t2 edited on the server (2 blurred boxes): free (0 tokens)." in out
    assert "PATCH, not a fix" in out and "No re-join to run" in out


def test_a_preview_asks_the_quote_and_changes_nothing(
    post_desk: Path, server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["blur", "--desk", str(post_desk), "--episode", "1", "--take", "t2",
                 "--box", "0.1,0.1,0.2,0.2", "--from", "1", "--to", "2", "--preview"])  # fmt: skip

    assert code == 0
    assert [path for path, _ in server.sent()] == [POST_EDIT + "/quote"]
    assert "Idempotency-Key" not in server.requests[0].headers
    out = capsys.readouterr().out
    assert out.splitlines()[-2:] == [
        "The t2 edit (1 blurred box) would cost free (0 tokens).",
        "Preview only: nothing was changed or charged.",
    ]
    assert "No re-join to run" not in out


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["trim", "--episode", "1", "--take", "t2", "--take-file", "F.mp4", "--cut", "1-2"],
         "trim --cut is a take edit on the server now and edits no file: drop --take-file"),
        (["blur", "--episode", "1", "--take", "t2", "--take-file", "F.mp4", "--box", "0.1,0.1,0.2,0.2",
          "--from", "1", "--to", "2"], "blur is a take edit on the server now"),
        (["trim", "--episode", "1", "--cut", "1-2"], "trim --cut edits one take on the server: add --take tK."),
        (["blur", "--take", "t2", "--box", "0.1,0.1,0.2,0.2", "--from", "1", "--to", "2"],
         "blur edits one take on the server"),
        (["blur", "--episode", "1", "--take", "t2", "--box", "77,410,307,137", "--from", "1", "--to", "2"],
         "these look like pixels; add the frame grab's size, like 77,410,307,137@768x1366"),
        (["trim", "--episode", "1", "--take", "t2", "--cut", "ten-twelve"], "trim --cut 'ten-twelve': write A-B"),
    ],
)  # fmt: skip
def test_what_the_kit_cannot_send_is_a_plain_stop_before_any_call(
    post_desk: Path,
    server: Server,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    said: str,
) -> None:
    command, *rest = argv

    assert main([command, "--desk", str(post_desk), *rest]) == 2

    assert server.requests == []
    err = capsys.readouterr().err
    assert err.startswith("Stopped: ") and said in err, err


def test_the_laptop_blur_settings_are_gone(post_desk: Path, server: Server) -> None:
    with pytest.raises(SystemExit):
        main(["blur", "--desk", str(post_desk), "--episode", "1", "--take", "t2", "--box", "0.1,0.1,0.2,0.2",
              "--from", "1", "--to", "2", "--strength", "30"])  # fmt: skip
    assert server.requests == []
