"""``music-switch`` reads and sets where episode music is on, on the server, and prints it plainly."""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from creation import music_switch
from creation.music_switch import run_music_switch

ADMIN = "tejas@dreamflux.ai"


def _view(
    mode: str = "off", accounts: list[str] | None = None, by: str | None = None
) -> dict[str, Any]:
    changed = {
        "mode": mode,
        "accounts": accounts or [],
        "changed_by": by,
        "changed_at": "2026-11-03T12:00:00+00:00",
    }
    return {**changed, "history": [changed] if by else []}


class FakeServer:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self.body = _view() if body is None else body
        self.calls: list[tuple[str, str, dict[str, str], Mapping[str, Any] | None]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> tuple[int, Any]:
        self.calls.append((method, url, dict(headers), body))
        return self.status, self.body


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FICTORA_DRAMA_GENERATION_SERVICE_TOKEN", "kit-token")
    monkeypatch.setenv("FICTORA_DRAMA_GENERATION_API_BASE_URL", "https://drama.test/")


def _run(
    server: FakeServer, action: str, *accounts: str, tmp_path: Path
) -> tuple[int, str]:
    out = io.StringIO()
    code = run_music_switch(
        action, accounts, email=ADMIN, out=out, send=server, env_file=tmp_path / ".env"
    )
    return code, out.getvalue()


def test_status_reads_the_server_as_the_admin_and_says_where_music_is_on(
    tmp_path: Path,
) -> None:
    server = FakeServer(body=_view("canary", ["tenant-a", "tenant-b"], ADMIN))
    code, text = _run(server, "status", tmp_path=tmp_path)
    assert code == 0
    assert server.calls == [
        (
            "GET",
            "https://drama.test/v1/internal/music-switch",
            {"Authorization": "Bearer kit-token", "X-Fictora-User-Email": ADMIN},
            None,
        )
    ]
    assert "Episode music is on only for these accounts: tenant-a, tenant-b." in text
    assert f"Last changed by {ADMIN} on 03 Nov 2026 12:00 UTC." in text


def test_a_switch_nobody_changed_reads_as_off_by_default(tmp_path: Path) -> None:
    code, text = _run(FakeServer(), "status", tmp_path=tmp_path)
    assert (
        code == 0
        and "Episode music is off for everyone." in text
        and "off (the default)" in text
    )


@pytest.mark.parametrize(
    ("action", "accounts", "sent"),
    [
        ("on", (), {"mode": "everyone"}),
        ("off", (), {"mode": "off"}),
        (
            "canary",
            ("tenant-a", " tenant-b "),
            {"mode": "canary", "accounts": ["tenant-a", "tenant-b"]},
        ),
    ],
)
def test_on_off_and_canary_send_one_change_to_the_server(
    tmp_path: Path, action: str, accounts: tuple[str, ...], sent: dict[str, Any]
) -> None:
    server = FakeServer(body=_view("everyone", by=ADMIN))
    code, text = _run(server, action, *accounts, tmp_path=tmp_path)
    assert code == 0 and [(m, b) for m, _u, _h, b in server.calls] == [("PUT", sent)]
    assert text.startswith(
        "Changed on the server. Every server picks it up within half a minute."
    )


@pytest.mark.parametrize(
    ("status", "body", "says"),
    [
        (
            403,
            {"detail": {"code": "admin_only", "message": "Only admins can do this."}},
            "is not one",
        ),
        (404, {"detail": "Not Found"}, "has no music switch yet"),
        (401, {"error": {"code": "service_token_invalid"}}, "did not accept"),
        (
            503,
            {"detail": {"code": "music_switch_unavailable"}},
            "cannot reach its database",
        ),
    ],
)
def test_a_refusal_is_said_plainly_and_exits_2(
    tmp_path: Path, status: int, body: Any, says: str
) -> None:
    code, text = _run(FakeServer(status, body), "on", tmp_path=tmp_path)
    assert code == 2 and says in text and "Nothing was changed." in text


def test_canary_needs_an_account_and_only_canary_takes_them(tmp_path: Path) -> None:
    server = FakeServer()
    assert _run(server, "canary", tmp_path=tmp_path)[0] == 2
    assert _run(server, "on", "tenant-a", tmp_path=tmp_path)[0] == 2
    assert server.calls == []


def test_without_a_token_nothing_is_sent_and_no_railway_variable_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FICTORA_DRAMA_GENERATION_SERVICE_TOKEN")

    def no_subprocess(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the kit must not ask Railway for variables")

    monkeypatch.setattr("subprocess.run", no_subprocess)
    server = FakeServer()
    code, text = _run(server, "status", tmp_path=tmp_path)
    assert (
        code == 2
        and server.calls == []
        and "FICTORA_DRAMA_GENERATION_SERVICE_TOKEN" in text
    )


def test_the_cli_needs_the_admin_email(capsys: pytest.CaptureFixture[str]) -> None:
    from creation.cli_produce import main

    with pytest.raises(SystemExit):
        main(["music-switch", "status"])
    assert "--as" in capsys.readouterr().err
    assert music_switch.ROUTE == "/v1/internal/music-switch"
