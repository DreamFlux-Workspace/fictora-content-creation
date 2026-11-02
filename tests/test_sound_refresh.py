"""``sound-refresh`` asks the server for a plan first and applies only on ``--yes``, then follows it to the new file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fake_api import FakeApi

from creation.cli_produce import main
from creation.post import server_finish, sound_refresh

ROUTE = "/v1/spines/sp1/episodes/episode_01/sound-refresh"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sound_refresh, "_sleep", lambda _s: None)


def _view(state: str, **extra: Any) -> dict[str, Any]:
    return {
        "spine_id": "sp1", "episode_id": "episode_01", "state": state, "reason": None,
        "summary": "Quieter background between lines, 1 sound turned down, 2 sounds removed.",
        "takes": [
            {"take_index": 1, "action": "rebuild", "reason": None, "removed_sounds": ["hiss", "wind"],
             "turned_down_sounds": [{"sound": "door slam", "from_db": -6.0, "to_db": -12.5}],
             "room_changed": True},
            {"take_index": 2, "action": "keep", "reason": "already under today's rules",
             "removed_sounds": [], "turned_down_sounds": [], "room_changed": False},
        ],
        "cost_usd": 0.03, "job_id": None, "video_url": None, **extra,
    }  # fmt: skip


def _posts(api: FakeApi) -> list[dict[str, Any]]:
    return [body or {} for m, p, body, _ in api.calls if m == "POST" and p == ROUTE]


def _run(desk: Path, *extra: str) -> int:
    return main(["sound-refresh", "--desk", str(desk), "--episode", "1", *extra])


def test_without_yes_prints_the_plan_in_plain_words_and_changes_nothing(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("POST", ROUTE)] = _view("planned")

    assert _run(desk) == 0

    assert _posts(api) == [{"dry_run": True}]
    text = capsys.readouterr().out
    assert "Quieter background between lines" in text
    assert "t1: removed hiss, wind; turned down door slam -6 dB to -12.5 dB; room sound changed" in text
    assert "t2: kept as it is (already under today's rules)" in text
    assert "Cost: $0.03" in text
    assert "Not applied: nothing was changed or spent" in text


def test_yes_applies_follows_the_server_and_saves_the_new_episode_without_overwriting(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def post(_m: str, _p: str, body: dict[str, Any]) -> dict[str, Any]:
        return _view("planned") if body["dry_run"] else _view("running", job_id="j1")

    api.routes[("POST", ROUTE)] = post
    answers = iter([_view("running", job_id="j1"), _view("done", video_url="https://media.test/new.mp4")])
    api.routes[("GET", ROUTE)] = lambda *_: next(answers)
    saved: list[Path] = []

    def fake_download(url: str, dest: Path, **_kw: Any) -> Path:
        assert url == "https://media.test/new.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp4")
        saved.append(dest)
        return dest

    monkeypatch.setattr(server_finish, "download_file", fake_download)
    old = desk / "ep01" / "takes"
    old.mkdir(parents=True, exist_ok=True)
    existing = old / "closing-time_ep01_draft_v1_2026-11-02.mp4"
    existing.write_bytes(b"old")

    assert _run(desk, "--yes") == 0

    assert [b["dry_run"] for b in _posts(api)] == [True, False]
    assert saved and saved[0] != existing and existing.read_bytes() == b"old"
    text = capsys.readouterr().out
    assert text.count("the server is re-doing the sound") == 2
    assert f"New episode file: {saved[0]}" in text


def test_a_failed_refresh_says_why_and_the_old_episode_still_plays(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: (
        _view("planned") if body["dry_run"] else _view("failed", reason="the mix step failed")
    )

    assert _run(desk, "--yes") == sound_refresh.NOT_APPLIED

    assert "the mix step failed. The old episode still plays." in capsys.readouterr().out


@pytest.mark.parametrize("state", ["unavailable", "nothing_to_change"])
def test_unavailable_and_nothing_to_change_reasons_are_shown_verbatim(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    api.routes[("POST", ROUTE)] = _view(state, reason="This episode already has today's sound.")

    code = _run(desk, "--yes")

    assert code == (0 if state == "nothing_to_change" else sound_refresh.NOT_APPLIED)
    assert "This episode already has today's sound." in capsys.readouterr().out
    assert _posts(api) == [{"dry_run": True}]


def test_an_older_server_without_the_route_is_told_plainly(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("POST", ROUTE)] = (404, {"detail": "Not Found"})

    assert _run(desk, "--yes") == sound_refresh.NOT_APPLIED

    assert "This server can't re-do sound yet." in capsys.readouterr().out
    assert len(_posts(api)) == 1
