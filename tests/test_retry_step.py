"""retry-step: a failed stage goes back to its ready phase with a fresh key, and nothing else is touched."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import SHOWN_PRICES, set_phase
from creation import orchestrate
from creation.cli_produce import main as produce_main
from creation.production_state import load_production
from creation.recover import infer_failed_phase, retry_failed_step
from fake_api import FakeApi

BOARDS = "/v1/spines/sp1/boards/enrol"
TOO_LONG = "failed provider_limit: row board prompt exceeds GPT Image 2 provider limit"


def _boards_fail_then_succeed(api: FakeApi) -> None:
    jobs = iter(["job_boards_bad", "job_boards_ok"])
    api.routes[("POST", BOARDS)] = lambda *_: {"job_id": next(jobs)}
    api.jobs["job_boards_bad"] = {
        "status": "failed",
        "error": {"code": "provider_limit", "message": TOO_LONG},
    }
    api.jobs["job_boards_ok"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {"boards": []}


def _board_keys(api: FakeApi) -> list[str | None]:
    return [
        key for method, path, _, key in api.calls if (method, path) == ("POST", BOARDS)
    ]


def test_failed_boards_retry_to_ready_boards_enrol_with_a_new_key(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _boards_fail_then_succeed(api)
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)

    with pytest.raises(SystemExit):
        orchestrate.run_step(desk, confirm_spend=True)
    failed = load_production(desk)
    assert failed.phase == "failed" and failed.failed_phase == "ready_boards_enrol"
    before = (desk / "production.json").read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="retry-step"):
        orchestrate.run_step(desk)  # a failed desk points at retry-step, sends nothing
    assert len(_board_keys(api)) == 1

    assert (
        produce_main(["retry-step", "--desk", str(desk), "--cause", "shorter rows"])
        == 0
    )
    out = capsys.readouterr().out
    assert "phase=ready_boards_enrol" in out
    assert "boards: $0.30 per board" in out and "(paid)" in out
    assert "Next: fictora-produce step" in out
    assert len(_board_keys(api)) == 1  # retry-step itself sends nothing

    state = load_production(desk)
    assert state.phase == "ready_boards_enrol"
    assert state.last_error is None and state.failed_phase is None
    backup = desk / "api" / "production-backup-v1.json"
    assert backup.read_text(encoding="utf-8") == before
    assert json.loads(backup.read_text(encoding="utf-8"))["phase"] == "failed"

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert result.phase == "wait_board"
    first, second = _board_keys(api)
    assert first != second
    assert (
        second
        == f"{state.idempotency_prefix}-step-retry-ep01-ready_boards_enrol-r1-ep1-boards-enrol"
    )

    # A second failure and retry gets yet another key, and a second backup beside the first.
    set_phase(desk, "failed", failed_phase="ready_boards_enrol", last_error="boards x")
    retried = retry_failed_step(desk)
    assert retried.key_prefix.endswith("-r2")
    assert (desk / "api" / "production-backup-v2.json").is_file() and backup.is_file()


@pytest.mark.parametrize("phase", ["wait_board", "ready_boards_enrol", "complete"])
def test_retry_step_refuses_a_desk_that_did_not_fail(
    desk: Path, api: FakeApi, phase: str, capsys: pytest.CaptureFixture[str]
) -> None:
    set_phase(desk, phase)
    before = (desk / "production.json").read_text(encoding="utf-8")

    assert produce_main(["retry-step", "--desk", str(desk)]) == 2

    assert (
        f"only for a failed step; this desk is at `{phase}`" in capsys.readouterr().err
    )
    assert (desk / "production.json").read_text(encoding="utf-8") == before
    assert not list((desk / "api").glob("production-backup-*"))


def test_a_legacy_failed_desk_is_read_from_its_error(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "failed", last_error=f"boards {TOO_LONG}")
    path = desk / "production.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("failed_phase")  # written before the field existed
    path.write_text(json.dumps(raw), encoding="utf-8")

    retried = retry_failed_step(desk)

    assert retried.phase == "ready_boards_enrol" and retried.inferred
    assert load_production(desk).phase == "ready_boards_enrol"


def test_a_legacy_desk_whose_error_names_no_stage_asks_for_the_phase(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "failed", last_error="HTTP 500: internal")

    with pytest.raises(RuntimeError, match="--phase"):
        retry_failed_step(desk)
    assert load_production(desk).phase == "failed"

    assert retry_failed_step(desk, phase="ready_cast_enrol").phase == "ready_cast_enrol"


def test_a_failed_take_is_sent_to_retry_video_not_retried_here(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "failed", failed_phase="ready_video", last_error="video failed x")

    with pytest.raises(RuntimeError, match="retry-video"):
        retry_failed_step(desk)
    assert load_production(desk).phase == "failed"


@pytest.mark.parametrize(
    ("error", "phase"),
    [
        (f"boards {TOO_LONG}", "ready_boards_enrol"),
        ("cast failed x", "ready_cast_enrol"),
        ("plan failed authoring_validation_failed: y", "new"),
        ("spine has no episode_summaries", "ready_estimate"),
        (
            "HTTP 409 POST https://h/v1/spines/sp1/boards/enrol: conflict",
            "ready_boards_enrol",
        ),
        ("HTTP 422 POST https://h/v1/video-generations: nope", "ready_video"),
        ("something else", None),
    ],
)
def test_the_stage_is_read_from_the_saved_error(error: str, phase: str | None) -> None:
    assert infer_failed_phase(error) == phase
