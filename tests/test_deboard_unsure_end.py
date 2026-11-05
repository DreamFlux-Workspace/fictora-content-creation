"""``deboard`` never holds an end the server left as filmed (fictora-drama #559 ``board_frames.unsure``).

The server holds only frames that are surely the board; a run it was not sure
of stays as filmed and is listed under ``unsure``. If the kit's own PSNR count
then held it, the guess the server refused to make would come back. Both the
``deboard`` command and finish's automatic deboard ask the producer instead.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from conftest import board_array, needs_ffmpeg, shot_frames, write_frames
from PIL import Image

from creation.cli_produce import main as produce_main
from creation.ops.floor import approve_board
from creation.post.finish import run_finish

BLUE = (60, 90, 220)
REASON = "the first frame does not look like the board (likeness 0.31 < 0.6)"


def _setup(post_desk: Path, *, unsure: list | None) -> Path:
    board = post_desk / "ep01" / "boards" / "board-ep01-t1-1-v1.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(board_array()).save(board)
    approve_board(post_desk, episode=1, take_id="t1", image=board)
    raw = write_frames(
        post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
        [board_array()] * 6 + shot_frames(66, BLUE),
    )
    record = {"head_frames": 0, "tail_frames": 0, "head_s": 0.0, "tail_s": 0.0, "frame_rate": 24.0,
              "timeline_shift_s": 0.0}  # fmt: skip
    if unsure is not None:
        record["unsure"] = unsure
    facts = post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json"
    facts.write_text(
        json.dumps({"take_facts": {"job_id": "j1", "board_frames": record}}),
        encoding="utf-8",
    )
    return raw


def _deboard(post_desk: Path, *extra: str) -> int:
    return produce_main(
        ["deboard", "--desk", str(post_desk), "--episode", "1", "--take", "t1", *extra]
    )


@needs_ffmpeg
def test_finish_does_not_auto_hold_an_unsure_start_and_says_how_to(
    post_desk: Path,
) -> None:
    _setup(post_desk, unsure=[{"end": "head", "frames": 6, "reason": REASON}])
    out = io.StringIO()

    result = run_finish(post_desk, facts_fetcher=lambda *a: None, sfx_render=lambda cue, t: t,
                        colour=False, cover=False, stream=out)  # fmt: skip

    step = next(s for s in result.steps if s.step == "deboard")
    assert step.status == "skipped" and step.output is None
    assert REASON in step.detail and "--hold-unsure" in step.detail
    assert not list((post_desk / "ep01" / "takes").glob("*deboard*"))
