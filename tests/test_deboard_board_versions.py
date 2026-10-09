"""``deboard`` measures a take against every drawing of its board on the desk (L-20261009-1).

Sweet Racket ep 4 t2: the board was redrawn (v2) and approved, but the take
opened on 12 frames of the v1 grid. finish compared the start with v2 only,
the server had left it as "unsure", and it shipped until it was held by hand.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import numpy as np
import pytest
from conftest import board_array, needs_ffmpeg, shot_frames, write_frames
from PIL import Image

from creation.cli_produce import main as produce_main
from creation.ops.floor import approve_board
from creation.post import deboard as deboard_module
from creation.post.deboard import BoardLeak, opened_on_earlier_drawing, pick_board
from creation.post.desk import board_versions
from creation.post.finish import run_finish

BLUE = (60, 90, 220)
REASON = "the first frame does not look like the board (likeness 0.57 < 0.6)"


def _leak(frames: int, first_db: float) -> BoardLeak:
    return BoardLeak(frames=frames, psnr_db=(first_db,), baseline_db=10.0, capped=False)


def test_pick_board_takes_the_drawing_the_take_opens_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    v2, v1 = tmp_path / "board-ep04-t2-v2.png", tmp_path / "board-ep04-t2-v1.png"
    leaks = {v2: _leak(0, 14.0), v1: _leak(12, 31.0)}
    monkeypatch.setattr(
        deboard_module, "measure_board_leak", lambda take, board, **_: leaks[board]
    )

    assert pick_board(tmp_path / "take.mp4", [v2, v1])[0] == v1
    assert opened_on_earlier_drawing(tmp_path / "take.mp4", [v2, v1]) == v1
    # A tie keeps the approved board; one drawing only is never "earlier".
    leaks[v1] = _leak(0, 14.0)
    assert pick_board(tmp_path / "take.mp4", [v2, v1])[0] == v2
    assert opened_on_earlier_drawing(tmp_path / "take.mp4", [v2, v1]) is None
    assert opened_on_earlier_drawing(tmp_path / "take.mp4", [v2]) is None
    # More frames but frame 0 is closer to the approved board: the doubt stands.
    leaks[v2], leaks[v1] = _leak(2, 30.0), _leak(5, 25.0)
    assert opened_on_earlier_drawing(tmp_path / "take.mp4", [v2, v1]) is None


def _boards(post_desk: Path) -> tuple[Path, Path]:
    boards = post_desk / "ep01" / "boards"
    boards.mkdir(parents=True, exist_ok=True)
    v1 = boards / "board-ep01-t1-v1.png"
    v2 = boards / "board-ep01-t1-v2.png"
    old = board_array()
    Image.fromarray(old).save(v1)
    # v2: the redraw, a clearly different picture (the grid's colours turned round).
    Image.fromarray(np.ascontiguousarray(old[::-1, ::-1])).save(v2)
    os.utime(v1, (1_000_000, 1_000_000))
    approve_board(post_desk, episode=1, take_id="t1", image=v2)
    # Another take's board is never one of t1's drawings.
    Image.fromarray(old).save(boards / "board-ep01-t10-v1.png")
    return v1, v2


def test_board_versions_lists_the_approved_board_first(post_desk: Path) -> None:
    v1, v2 = _boards(post_desk)
    found = board_versions(post_desk, 1, "t1")
    assert [p.name for p in found] == [v2.name, v1.name]


def _setup_take(post_desk: Path, *, unsure: bool) -> Path:
    _boards(post_desk)
    raw = write_frames(
        post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
        [board_array()] * 6 + shot_frames(66, BLUE),
    )
    record: dict[str, object] = {"head_frames": 0, "tail_frames": 0, "head_s": 0.0, "tail_s": 0.0,
                                 "frame_rate": 24.0, "timeline_shift_s": 0.0}  # fmt: skip
    if unsure:
        record["unsure"] = [{"end": "head", "frames": 6, "reason": REASON}]
    facts = post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json"
    facts.parent.mkdir(parents=True, exist_ok=True)
    facts.write_text(
        json.dumps({"take_facts": {"job_id": "j1", "board_frames": record}}),
        encoding="utf-8",
    )
    return raw


@needs_ffmpeg
def test_finish_holds_a_start_on_an_earlier_drawing_of_the_board(
    post_desk: Path,
) -> None:
    _setup_take(post_desk, unsure=True)
    out = io.StringIO()

    result = run_finish(post_desk, facts_fetcher=lambda *a: None, sfx_render=lambda cue, t: t,
                        bed_maker=lambda *a: None, colour=False, thumbnail=False, stream=out)  # fmt: skip

    step = next(s for s in result.steps if s.step == "deboard")
    assert step.status == "ran" and step.output is not None, step.detail
    assert "replaced 6 board frame(s)" in step.detail


@needs_ffmpeg
def test_the_deboard_command_measures_every_drawing(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup_take(post_desk, unsure=False)

    code = produce_main(
        ["deboard", "--desk", str(post_desk), "--episode", "1", "--take", "t1"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "board-ep01-t1-v1.png" in out and "replaced 6 board frame(s)" in out
