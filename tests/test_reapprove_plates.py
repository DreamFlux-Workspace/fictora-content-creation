"""approve --gate plates --again: the plates approval sent again outside wait_plates (issue #49)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import set_phase
from creation import orchestrate
from creation.cli_produce import main as produce_main
from creation.ops.floor import approve_series_gate
from creation.ops.state import load_series
from creation.production_state import load_production
from fake_api import FakeApi

APPROVE = "/v1/spines/sp1/cast/approve"
BOARDS = "/v1/spines/sp1/boards/enrol"
REFUSED = (
    "HTTP 422 POST /v1/spines/sp1/boards/enrol: cast_not_approved: "
    "cast_the-statue plates are not approved"
)


def _approve_keys(api: FakeApi) -> list[str | None]:
    return [
        key for method, path, _, key in api.calls if (method, path) == ("POST", APPROVE)
    ]


def test_boards_refused_for_unapproved_cast_print_the_reapprove_command(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_boards_enrol")
    api.routes[("POST", BOARDS)] = SystemExit(REFUSED)

    with pytest.raises(SystemExit) as raised:
        orchestrate.run_step(desk)

    assert "approve --desk" in str(raised.value.code)
    assert "--gate plates --again" in str(raised.value.code)
    assert "retry-step" in str(raised.value.code)
    assert load_production(desk).phase == "failed"
    # The failed desk says the same when step is run again.
    with pytest.raises(RuntimeError, match="--gate plates --again"):
        orchestrate.run_step(desk)


def test_again_resends_the_approval_on_the_current_version_with_a_fresh_key(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    approve_series_gate(desk, "plates", path="plates/cast_the-statue-v2.png")
    set_phase(desk, "failed", failed_phase="ready_boards_enrol", last_error=REFUSED)
    api.routes[("POST", APPROVE)] = {"approved": True}
    version = api.spine_doc["spine_version"]

    assert (
        produce_main(["approve", "--desk", str(desk), "--gate", "plates", "--again"])
        == 0
    )
    assert (
        produce_main(["approve", "--desk", str(desk), "--gate", "plates", "--again"])
        == 0
    )

    assert api.posted(APPROVE) == [{"spine_version": version}] * 2
    first, second = _approve_keys(api)
    assert first != second and "reapprove" in (first or "")
    out = capsys.readouterr().out
    assert "$0" in out and "retry-step" in out
    saved = json.loads((desk / "api" / "spine.json").read_text(encoding="utf-8"))
    assert saved["spine_version"] == version
    gate = load_series(desk).plates
    assert gate.status == "approved" and "approved again" in (gate.note or "")
    # Nothing else moved: the failed step still waits for retry-step.
    state = load_production(desk)
    assert state.phase == "failed" and state.failed_phase == "ready_boards_enrol"
    assert api.posted(BOARDS) == []


def test_again_refuses_before_the_first_yes_and_on_other_gates(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    set_phase(desk, "ready_boards_enrol")
    assert (
        produce_main(["approve", "--desk", str(desk), "--gate", "plates", "--again"])
        == 2
    )
    assert "never approved" in capsys.readouterr().err

    set_phase(desk, "wait_plates")
    assert (
        produce_main(["approve", "--desk", str(desk), "--gate", "plates", "--again"])
        == 2
    )
    assert "without --again" in capsys.readouterr().err

    assert (
        produce_main(["approve", "--desk", str(desk), "--gate", "script", "--again"])
        == 2
    )
    assert "plates only" in capsys.readouterr().err
    assert api.posted(APPROVE) == []
