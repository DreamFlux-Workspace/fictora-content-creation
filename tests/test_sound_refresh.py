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
        "cost_usd": 0.03, "job_id": None, "video_url": None, "plan_id": "plan_a", **extra,
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
    assert (
        "t1: removed hiss, wind; turned down door slam -6 dB to -12.5 dB; room sound changed"
        in text
    )
    assert "t2: kept as it is (already under today's rules)" in text
    assert "Cost: $0.03" in text
    assert "Not applied: nothing was changed or spent" in text


def test_yes_applies_follows_the_server_and_saves_the_new_episode_without_overwriting(
    desk: Path,
    api: FakeApi,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def post(_m: str, _p: str, body: dict[str, Any]) -> dict[str, Any]:
        return _view("planned") if body["dry_run"] else _view("running", job_id="j1")

    api.routes[("POST", ROUTE)] = post
    answers = iter(
        [
            _view("running", job_id="j1"),
            _view("done", video_url="https://media.test/new.mp4"),
        ]
    )
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
        _view("planned")
        if body["dry_run"]
        else _view("failed", reason="the mix step failed")
    )

    assert _run(desk, "--yes") == sound_refresh.NOT_APPLIED

    assert (
        "the mix step failed. The old episode still plays." in capsys.readouterr().out
    )


@pytest.mark.parametrize("state", ["unavailable", "nothing_to_change"])
def test_unavailable_and_nothing_to_change_reasons_are_shown_verbatim(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    api.routes[("POST", ROUTE)] = _view(
        state, reason="This episode already has today's sound."
    )

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


def _keys(api: FakeApi) -> list[str | None]:
    return [
        k
        for m, p, _b, k in api.calls
        if m == "POST" and p == ROUTE and k and "-plan-" not in k
    ]


def _fake_download(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    saved: list[Path] = []

    def download(
        url: str, dest: Path, *, meta: dict[str, Any] | None = None, **_kw: Any
    ) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp4")
        server_finish._write_sidecar(dest, meta or {"video_url": url})
        saved.append(dest)
        return dest

    monkeypatch.setattr(server_finish, "download_file", download)
    return saved


def test_apply_sends_the_plan_id_and_a_new_plan_is_a_new_key(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_download(monkeypatch)
    plan_id = ["plan_a"]

    def post(_m: str, _p: str, body: dict[str, Any]) -> dict[str, Any]:
        if body["dry_run"]:
            return _view("planned", plan_id=plan_id[0])
        return _view(
            "done",
            plan_id=body["plan_id"],
            video_url=f"https://media.test/{body['plan_id']}.mp4",
        )

    api.routes[("POST", ROUTE)] = post

    assert _run(desk, "--yes") == 0
    plan_id[0] = "plan_b"
    assert _run(desk, "--yes") == 0

    applies = [b for b in _posts(api) if not b["dry_run"]]
    assert [b["plan_id"] for b in applies] == ["plan_a", "plan_b"]
    keys = _keys(api)
    assert keys[0].endswith("-sound-refresh-ep1-plan_a") and keys[1].endswith(
        "-sound-refresh-ep1-plan_b"
    )


def test_a_changed_plan_shows_the_fresh_plan_and_applies_nothing(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    fresh = _view("planned", plan_id="plan_new", summary="Louder room fix.")
    fresh["takes"] = fresh["takes"][:1]
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: (
        _view("planned")
        if body["dry_run"]
        else (
            409,
            {
                "error": {
                    "code": "sound_refresh_plan_changed",
                    "message": "changed",
                    "details": fresh,
                }
            },
        )
    )

    assert _run(desk, "--yes") == sound_refresh.NOT_APPLIED

    text = capsys.readouterr().out
    assert "The plan changed" in text and "Louder room fix." in text
    assert "run again with --yes" in text
    assert [m for m, *_ in api.calls if m == "GET"] == []


def test_a_rerun_after_a_timeout_downloads_the_done_file_once(
    desk: Path,
    api: FakeApi,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    saved = _fake_download(monkeypatch)
    api.routes[("POST", ROUTE)] = _view(
        "nothing_to_change",
        reason="Already under today's sound.",
        video_url="https://media.test/new.mp4",
    )

    assert _run(desk) == 0
    assert _run(desk) == 0

    assert len(saved) == 1
    text = capsys.readouterr().out
    assert "New episode file:" in text and "Already on the desk:" in text
    assert _posts(api) == [{"dry_run": True}, {"dry_run": True}]


def test_a_timeout_exits_not_applied_and_says_how_to_pick_it_up(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: (
        _view("planned") if body["dry_run"] else _view("running", job_id="j1")
    )
    api.routes[("GET", ROUTE)] = _view("running", job_id="j1")

    assert _run(desk, "--yes", "--wait", "0") == sound_refresh.NOT_APPLIED

    text = capsys.readouterr().out
    assert (
        "the old episode plays until it is done" in text and "Run again later" in text
    )


def test_wait_zero_reads_the_status_exactly_once(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: (
        _view("planned") if body["dry_run"] else _view("running", job_id="j1")
    )
    api.routes[("GET", ROUTE)] = _view("running", job_id="j1")

    _run(desk, "--yes", "--wait", "0")

    assert [m for m, p, *_ in api.calls if m == "GET" and p == ROUTE] == ["GET"]


def test_a_dry_run_that_finds_one_running_says_so_and_offers_to_follow(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("POST", ROUTE)] = _view("running", job_id="j1")

    assert _run(desk) == 0

    text = capsys.readouterr().out
    assert "a sound fix is already running" in text and "--yes" in text
    assert "Cost:" not in text
    assert _posts(api) == [{"dry_run": True}]


def test_yes_on_a_running_fix_follows_it_without_applying_again(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = _fake_download(monkeypatch)
    api.routes[("POST", ROUTE)] = _view("running", job_id="j1")
    api.routes[("GET", ROUTE)] = _view("done", video_url="https://media.test/new.mp4")

    assert _run(desk, "--yes") == 0

    assert _posts(api) == [{"dry_run": True}] and len(saved) == 1


def test_a_4xx_while_polling_says_the_fix_was_accepted(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: (
        _view("planned") if body["dry_run"] else _view("running", job_id="j1")
    )
    api.routes[("GET", ROUTE)] = (403, {"detail": "nope"})

    assert _run(desk, "--yes") == sound_refresh.NOT_APPLIED

    assert "The fix was accepted" in capsys.readouterr().out


def test_yes_on_a_shared_story_is_refused_but_its_plan_is_not(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    copy = desk.parent / "2026-10-01-closing-time-copy"
    shutil.copytree(desk, copy)
    api.routes[("POST", ROUTE)] = _view("planned")

    assert _run(desk) == 0
    assert _run(desk, "--yes") == 2

    assert "Stopped: this desk's story" in capsys.readouterr().err
    assert _posts(api) == [{"dry_run": True}]
