"""The server's plain refusals reach the operator as the server's own sentence (fictora-drama #1406, #1425, #1367).

Every answer comes from a stand-in server (``httpx.MockTransport`` or ``FakeApi``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fake_api import FakeApi
from test_kit_cut_and_blur_on_server import (
    POST_EDIT,
    Server,
    install_stand_in,
)

from creation.cli_produce import main
from creation.post import sound_refresh

DIRECTOR_CUT = "This episode plays a Director cut, which doesn't show take edits yet. Film the take again to change it."
JAPANESE = "この話はディレクターズカットで流れるため、テイクの編集はまだ反映されません。変えるにはテイクを撮り直してください。"
CHANGED = "This episode has been updated since it was finished, so its sound can't be redone yet. Nothing was changed."
CAPTIONS_OFF = "This episode has been updated since it was finished, so captions can't be turned off yet. Nothing was changed."
SOUND_ROUTE = "/v1/spines/sp1/episodes/episode_01/sound-refresh"


@pytest.fixture
def server(post_desk: Path, monkeypatch: pytest.MonkeyPatch) -> Server:
    return install_stand_in(monkeypatch)


def _take_edit(desk: Path, command: str, *settings: str) -> int:
    return main(
        [command, "--desk", str(desk), "--episode", "1", "--take", "t2", *settings]
    )


@pytest.mark.parametrize("sentence", [DIRECTOR_CUT, JAPANESE])
@pytest.mark.parametrize(
    ("command", "settings"),
    [
        ("freeze", ("--at", "1.5", "--hold", "0.6")),
        ("blur", ("--box", "0.1,0.1,0.5,0.2", "--from", "1", "--to", "2")),
    ],
)
def test_a_director_cut_stops_a_non_trim_edit_with_the_servers_sentence_before_any_edit_or_quote(
    command: str,
    settings: tuple[str, ...],
    sentence: str,
    post_desk: Path,
    server: Server,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server.status_body = {"status": "completed", "take_edits_unavailable": sentence}

    code = _take_edit(post_desk, command, *settings)

    assert code == 2
    assert capsys.readouterr().err == f"Stopped: {sentence}\n"
    assert server.requests == []
    assert len(server.status_reads) == 1


def test_a_director_cut_still_takes_a_trim(
    post_desk: Path, server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    server.status_body = {"take_edits_unavailable": DIRECTOR_CUT}

    assert _take_edit(post_desk, "trim", "--cut", "10.17-12.15") == 0

    assert [path for path, _ in server.applied()] == [POST_EDIT]
    assert "Stopped" not in capsys.readouterr().err


def test_a_status_without_the_field_lets_the_edit_run(
    post_desk: Path, server: Server
) -> None:
    server.status_body = {"status": "completed"}

    assert _take_edit(post_desk, "freeze", "--at", "1.5", "--hold", "0.6") == 0

    assert [path for path, _ in server.applied()] == [POST_EDIT]


def test_a_preview_is_stopped_the_same_way(
    post_desk: Path, server: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    server.status_body = {"take_edits_unavailable": DIRECTOR_CUT}

    code = _take_edit(post_desk, "freeze", "--at", "1.5", "--hold", "0.6", "--preview")

    assert code == 2 and server.requests == []
    assert capsys.readouterr().err == f"Stopped: {DIRECTOR_CUT}\n"


@pytest.mark.parametrize("sentence", [DIRECTOR_CUT, JAPANESE])
def test_a_409_on_the_edit_is_the_servers_sentence_not_an_error(
    sentence: str,
    post_desk: Path,
    server: Server,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server.refusal = (
        409,
        {"error": {"code": "take_post_edit_unavailable", "message": sentence}},
    )

    assert _take_edit(post_desk, "freeze", "--at", "1.5", "--hold", "0.6") == 2

    err = capsys.readouterr().err
    assert err == f"Stopped: {sentence}\n"
    assert "Traceback" not in err


def _sound_409(code: str, message: str) -> tuple[int, dict[str, Any]]:
    return 409, {"error": {"code": code, "message": message}}


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("episode_changed_since_finish", CHANGED),
        ("captions_off_after_change", CAPTIONS_OFF),
        (
            "episode_changed_since_finish",
            "この話は完成後に更新されたため、音声はまだやり直せません。何も変更されていません。",
        ),
    ],
)
@pytest.mark.parametrize("yes", [False, True])
def test_a_sound_refresh_refusal_is_the_servers_sentence_only(
    code: str,
    message: str,
    yes: bool,
    desk: Path,
    api: FakeApi,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api.routes[("POST", SOUND_ROUTE)] = _sound_409(code, message)

    result = main(
        [
            "sound-refresh",
            "--desk",
            str(desk),
            "--episode",
            "1",
            *(["--yes"] if yes else []),
        ]
    )

    captured = capsys.readouterr()
    assert result == sound_refresh.NOT_APPLIED
    assert captured.out == f"{message}\n"
    assert "HTTP" not in captured.out + captured.err and "Traceback" not in captured.err


def test_a_refusal_on_apply_after_a_good_plan_is_the_servers_sentence_only(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = {
        "spine_id": "sp1", "episode_id": "episode_01", "state": "planned", "reason": None,
        "summary": "Quieter room.", "takes": [], "cost_usd": 0, "plan_id": "plan_a",
    }  # fmt: skip
    api.routes[("POST", SOUND_ROUTE)] = lambda _m, _p, body: (
        plan if body["dry_run"] else _sound_409("episode_changed_since_finish", CHANGED)
    )

    assert (
        main(["sound-refresh", "--desk", str(desk), "--episode", "1", "--yes"])
        == sound_refresh.NOT_APPLIED
    )

    out = capsys.readouterr().out
    assert out.endswith(f"Cost: free\n{CHANGED}\n")
    assert "HTTP" not in out
