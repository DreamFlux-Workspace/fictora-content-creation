"""Preflight: human gates block (exit 3); quality checks and budgets warn (exit 4) until confirmed per unit."""

from __future__ import annotations

from pathlib import Path

import pytest

from creation.cli_ops import main as ops_main
from creation.ops.floor import (
    approve_board,
    approve_script,
    approve_series_gate,
    confirm_preflight,
    init_series_desk,
    preflight_take,
    record_estimate,
    record_spend,
    set_take_lines,
)
from creation.ops.state import SpokenLine, episode_by_ordinal, load_series
from fake_api import png_bytes


@pytest.fixture
def ready_desk(tmp_path: Path) -> Path:
    desk = init_series_desk(tmp_path, "Warn Test", band="15s", episode_count=1)
    approve_series_gate(desk, "look")
    approve_series_gate(desk, "plates")
    approve_script(desk, episode=1)
    board = desk / "ep01" / "boards" / "board-ep01-t1-v1.png"
    board.write_bytes(png_bytes(gray=15))  # a very dark board
    approve_board(desk, episode=1, take_id="t1", image=board)
    record_estimate(desk, episode=1, take_id="t1", usd=1.20)
    return desk


def test_a_dark_board_is_information_not_a_warning(ready_desk: Path) -> None:
    report = preflight_take(ready_desk, episode=1, take_id="t1")
    luma = next(check for check in report.checks if check.code == "board_luma")
    assert luma.ok and "brightness" in luma.detail
    assert report.status() == "PASS"


def test_past_twice_the_envelope_warns_and_a_named_confirmation_clears_one_film(
    ready_desk: Path,
) -> None:
    record_spend(
        ready_desk, episode=1, usd=10.0
    )  # $11.20 projected on a $5.50 first-episode envelope
    report = preflight_take(ready_desk, episode=1, take_id="t1")
    assert not report.blocked and not report.cleared and report.status() == "WARN"
    assert "$5.50 envelope" in next(c.detail for c in report.warnings())
    assert (
        ops_main(
            ["preflight", "--desk", str(ready_desk), "--episode", "1", "--take", "t1"]
        )
        == 4
    )

    with pytest.raises(ValueError, match="must name this unit exactly: ep01-t1"):
        confirm_preflight(ready_desk, episode=1, take_id="t1", confirm_unit="ep01-t2")
    code = ops_main(
        [
            "preflight",
            "--desk",
            str(ready_desk),
            "--episode",
            "1",
            "--take",
            "t1",
            "--proceed-anyway",
            "ep01-t1",
        ]
    )
    assert code == 0
    take = episode_by_ordinal(load_series(ready_desk), 1).takes[0]
    assert (
        take.overrides[0].codes == ["envelope"] and take.overrides[0].filmed_count == 0
    )


def test_an_open_gate_blocks_and_cannot_be_overridden(tmp_path: Path) -> None:
    desk = init_series_desk(tmp_path, "Gate Test", band="15s", episode_count=1)
    assert (
        ops_main(["preflight", "--desk", str(desk), "--episode", "1", "--take", "t1"])
        == 3
    )
    with pytest.raises(ValueError, match="cannot be overridden"):
        confirm_preflight(desk, episode=1, take_id="t1", confirm_unit="ep01-t1")


def test_four_lines_are_approved_with_a_warning_not_refused(tmp_path: Path) -> None:
    desk = init_series_desk(tmp_path, "Lines Test", band="15s", episode_count=1)
    set_take_lines(
        desk,
        episode=1,
        take_id="t1",
        lines=[SpokenLine("Hana", f"line {n}") for n in range(4)],
    )
    record = approve_script(desk, episode=1)
    assert record.status == "approved" and record.note.startswith(
        "WARNING: t1 has 4 lines"
    )
