"""``collect-takes``: collect the filmed takes of a film job that stopped moving (L-20261006-4). Spends nothing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import episode_commands, orchestrate
from creation.collect_stuck import run_collect_takes
from creation.episode_commands import CommandStopped
from creation.harness import raw_video
from creation.ops.floor import approve_board, init_series_desk, record_spend
from creation.ops.folder import next_versioned_path
from creation.ops.state import episode_by_ordinal, load_series
from creation.post.desk import take_job_id, take_stored_url
from creation.post.sfx import saved_take_facts
from creation.production_state import (
    ensure_production,
    load_production,
    save_production,
)
from fake_api import FakeApi, png_bytes, spine_fixture

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
PARENT = "/v1/jobs/job_video_1"


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    """A 30s desk (two takes an episode) on episode 2, whose step film is waiting on job_video_1."""

    path = init_series_desk(tmp_path, "Closing Time", band="30s", episode_count=2)
    state = ensure_production(
        path,
        prompt="A shop at closing time.",
        preset_id="modern-romance",
        preset_version="2",
    )
    state.spine_id = "sp1"
    state.episode_ordinal = 2
    save_production(path, state)
    board = path / "ep02" / "boards" / "board.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_bytes(png_bytes())
    for take_id in ("t1", "t2"):
        approve_board(path, episode=2, take_id=take_id, image=board)
    api_dir = path / "ep02" / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "16_video_enrol.json").write_text(
        json.dumps({"job_id": "job_video_1"}), encoding="utf-8"
    )
    # The step's poll gave up on the stuck job: the desk stopped while waiting on it.
    set_phase(
        path,
        "failed",
        failed_phase="ready_video",
        last_error="timed out waiting for raw scene clips",
    )
    return path


@pytest.fixture
def api(desk: Path, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi(desk / "ep02" / "api", spine=spine_fixture())
    monkeypatch.setattr(orchestrate, "_open_run", lambda _desk, _state: fake)
    monkeypatch.setattr(episode_commands, "_open_run", lambda _desk, _state: fake)

    def download(
        client: Any,
        url: str,
        directory: Path,
        stem: str,
        *,
        default_suffix: str = ".png",
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        target = next_versioned_path(directory, stem, default_suffix)
        target.write_bytes(b"mp4")
        fake.events.append(("download", {"url": url, "path": str(target)}))
        return target

    monkeypatch.setattr(orchestrate, "download_to_versioned", download)
    return fake


def _child(take: int, *, status: str = "completed") -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": status,
        "episode_ids": ["ep_02"],
        "relation": {"id": f"scene_ep_02_set{take:02d}"},
    }
    if status == "completed":
        body["result"] = {"video": {"url": f"https://r2.example/ep2-t{take}.mp4"}}
    return body


def _stuck(
    api: FakeApi,
    *,
    idle_minutes: float = 60,
    status: str = "running",
    children: tuple[str, ...] = ("job_take_a", "job_take_b"),
    second: str = "completed",
) -> None:
    """The film job sits at 50 % with its take jobs done (NOCLIP ep 1, 6 Oct)."""

    api.routes[("GET", PARENT)] = {
        "status": status,
        "progress": 50,
        "updated_at": _iso(NOW - timedelta(minutes=idle_minutes)),
        "depends_on": list(children),
        **(
            {"error": {"code": "film_job_stalled", "message": "the film job stopped"}}
            if status == "failed"
            else {}
        ),
    }
    api.routes[("GET", "/v1/jobs/job_take_a")] = _child(1)
    api.routes[("GET", "/v1/jobs/job_take_b")] = _child(2, status=second)
    for job in ("job_take_a", "job_take_b"):
        api.routes[("GET", f"/v1/jobs/{job}/take-facts")] = {
            "take_facts": {
                "job_id": job,
                "duration_seconds": 15.0,
                "sfx_cues": [
                    {
                        "shot_index": 0,
                        "sound": "door chime",
                        "start_seconds": 1.0,
                        "duration_seconds": 0.5,
                    }
                ],
            }
        }


def _desk_files(desk: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(desk)): path.read_bytes()
        for path in sorted(desk.rglob("*"))
        if path.is_file()
    }


def _record(desk: Path, name: str = "17_raw_scene_clips.json") -> dict[str, Any]:
    return json.loads((desk / "ep02" / "api" / name).read_text(encoding="utf-8"))


# --- Collects --------------------------------------------------------------------------------------


def test_every_take_filmed_and_the_job_idle_ten_minutes_is_collected_and_marked_done(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _stuck(api, idle_minutes=10)

    text = run_collect_takes(desk, now=NOW)

    assert [method for method, *_ in api.calls if method != "GET"] == [], (
        "GET only: nothing paid"
    )
    downloads = [payload["url"] for phase, payload in api.events if phase == "download"]
    assert downloads == [
        "https://r2.example/ep2-t1.mp4",
        "https://r2.example/ep2-t2.mp4",
    ]
    state = load_production(desk)
    assert (
        state.phase == "complete"
        and state.failed_phase is None
        and state.last_error is None
    )
    assert state.last_video_job_id == "job_video_1"
    slot = episode_by_ordinal(load_series(desk), 2)
    assert [take.filmed_count for take in slot.takes] == [1, 1]
    # finish finds each take's job and its facts, so it lays SFX.
    for take, job in (("t1", "job_take_a"), ("t2", "job_take_b")):
        assert take_job_id(desk, 2, take) == job
        assert saved_take_facts(desk, 2, take) is not None
    assert "COLLECTED FROM A STUCK JOB, not a completed one" in text
    assert "has not moved for 10 min" in text
    notes = (desk / "ep02" / "run-notes.md").read_text(encoding="utf-8")
    assert (
        "COLLECTED FROM A STUCK FILM JOB (not a completed one)" in notes
        and "job_video_1" in notes
    )
    stuck = _record(desk)["collected_from_stuck_job"]
    assert stuck["by"] == "collect-takes" and stuck["film_job_status"] == "running"
    assert stuck["film_job_progress"] == 50


def test_a_film_job_that_failed_after_every_take_was_filmed_is_collected(
    desk: Path, api: FakeApi
) -> None:
    _stuck(api, idle_minutes=1, status="failed")

    text = run_collect_takes(desk, now=NOW)

    assert "ended failed after all 2 take(s) were filmed" in text
    assert load_production(desk).phase == "complete"


def test_a_film_unit_the_desk_still_waits_on_is_collected_and_cleared(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "complete", failed_phase=None)
    state = load_production(desk)
    state.pending["film-ep02-t2-s2"] = {"key": "k-1", "job_id": "job_video_1"}
    state.film_estimates["ep02-t2"] = 1.20
    save_production(desk, state)
    _stuck(api, children=("job_take_b",))

    run_collect_takes(desk, now=NOW)

    state = load_production(desk)
    assert state.pending == {} and state.film_estimates == {}
    assert (
        _record(desk, "film-ep02-t2-s2-raw-scene-clips.json")["clips"][0]["job_id"]
        == "job_take_b"
    )
    assert not (desk / "ep02" / "api" / "17_raw_scene_clips.json").exists()
    assert take_stored_url(desk, 2, "t2") == "https://r2.example/ep2-t2.mp4"


def test_a_failed_film_the_desk_let_go_is_found_by_its_job_id(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "complete", failed_phase=None)
    (desk / "api").mkdir(exist_ok=True)
    (desk / "api" / "film-ep02-t2-s2-enrol-v1.json").write_text(
        json.dumps({"job_id": "job_video_1"}), encoding="utf-8"
    )
    _stuck(api, status="failed", children=("job_take_b",))

    with pytest.raises(CommandStopped, match="not waiting on a film job"):
        run_collect_takes(desk, now=NOW)
    run_collect_takes(desk, job_id="job_video_1", now=NOW)

    assert take_job_id(desk, 2, "t2") == "job_take_b"


def test_take_facts_collect_did_not_bring_are_refreshed(
    desk: Path, api: FakeApi
) -> None:
    """The operator booked a take by hand already: collecting skips its facts, so they are refreshed."""

    record_spend(
        desk, episode=2, usd=0.6, take_id="t1", unit="take 15s", job_id="job_take_a"
    )
    _stuck(api)

    text = run_collect_takes(desk, now=NOW)

    assert saved_take_facts(desk, 2, "t1") is not None
    assert "t1: take facts refreshed" in text


# --- Refuses ---------------------------------------------------------------------------------------


def test_a_take_missing_from_the_film_jobs_partial_list_is_not_collected(
    desk: Path, api: FakeApi
) -> None:
    """L-20260925-1: the film job lists take jobs as it starts them; count against the takes asked for."""

    _stuck(api, children=("job_take_a",))
    before = _desk_files(desk)

    with pytest.raises(CommandStopped) as stopped:
        run_collect_takes(desk, now=NOW)

    assert "1 of 2 take(s) are filmed" in str(stopped.value)
    assert "lists only 1 take job(s) so far" in str(stopped.value)
    assert _desk_files(desk) == before


def test_a_take_still_filming_is_not_collected(desk: Path, api: FakeApi) -> None:
    _stuck(api, second="running")

    with pytest.raises(
        CommandStopped,
        match=r"1 of 2 take\(s\) are filmed \(job_take_b still filming\)",
    ):
        run_collect_takes(desk, now=NOW)

    assert not (desk / "ep02" / "api" / "17_raw_scene_clips.json").exists()


def test_a_film_job_still_moving_is_not_collected_and_says_when_to_retry(
    desk: Path, api: FakeApi
) -> None:
    _stuck(api, idle_minutes=4)
    before = _desk_files(desk)

    with pytest.raises(CommandStopped) as stopped:
        run_collect_takes(desk, now=NOW)

    message = str(stopped.value)
    assert "last moved 4 min ago" in message and "by 12:06 UTC" in message
    assert _desk_files(desk) == before
    assert load_production(desk).phase == "failed"


def test_a_failed_take_job_is_not_collected(desk: Path, api: FakeApi) -> None:
    _stuck(api, second="failed")

    with pytest.raises(CommandStopped, match="a take job failed"):
        run_collect_takes(desk, now=NOW)


def test_a_film_job_that_finished_normally_points_to_the_normal_path(
    desk: Path, api: FakeApi
) -> None:
    _stuck(api, status="completed")

    with pytest.raises(CommandStopped, match="finished normally"):
        run_collect_takes(desk, now=NOW)


# --- Dry run ---------------------------------------------------------------------------------------


def test_a_dry_run_says_what_it_would_collect_and_writes_nothing(
    desk: Path, api: FakeApi
) -> None:
    _stuck(api)
    before = _desk_files(desk)

    text = run_collect_takes(desk, dry_run=True, now=NOW)

    assert _desk_files(desk) == before
    assert api.events == []
    assert (
        "would collect from a stuck job" in text
        and "job_take_a" in text
        and "job_take_b" in text
    )
    assert "Dry run: nothing was written" in text


def test_the_cli_runs_collect_takes(desk: Path, api: FakeApi) -> None:
    from creation.cli_produce import main

    _stuck(api, idle_minutes=600_000)  # long idle against the real clock

    assert main(["collect-takes", "--desk", str(desk), "--dry-run"]) == 0


# --- Same record as the film path -------------------------------------------------------------------


def test_the_clip_record_is_the_one_the_film_path_writes(
    desk: Path, api: FakeApi
) -> None:
    _stuck(api)
    run_collect_takes(desk, now=NOW)
    collected = _record(desk)

    api.routes[("GET", PARENT)] = {
        "status": "completed",
        "progress": 100,
        "depends_on": ["job_take_a", "job_take_b"],
    }
    filmed = raw_video.wait_for_raw_scene_clips(
        api, "job_video_1", save_as="filmed.json", expected_clips=2
    )

    stuck = collected.pop("collected_from_stuck_job")
    assert stuck["by"] == "collect-takes"
    assert collected == filmed


# --- film's own poll points at collect-takes ---------------------------------------------------------


class _Clock:
    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def monotonic(self) -> float:
        return self.now

    def sleep(self, _seconds: float) -> None:
        self.now += self.step


@pytest.mark.parametrize("second", ["completed", "running"])
def test_the_film_poll_suggests_collect_takes_only_when_every_take_is_filmed(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch, second: str
) -> None:
    clock = _Clock(step=300.0)
    monkeypatch.setattr(raw_video.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(raw_video.time, "sleep", clock.sleep)
    _stuck(api, second=second)
    warnings: list[str] = []

    with pytest.raises(SystemExit, match="NOT done"):
        raw_video.wait_for_raw_scene_clips(
            api, "job_video_1", deadline_seconds=700, interval_seconds=0,
            expected_clips=2, warn=warnings.append,
        )  # fmt: skip

    assert len(warnings) == 1
    if second == "completed":
        assert "All 2 take(s) are filmed" in warnings[0]
        assert "collect-takes --desk" in warnings[0]
    else:
        assert "collect-takes" not in warnings[0]
