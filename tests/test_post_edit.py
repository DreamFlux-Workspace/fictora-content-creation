"""The local deboard step of finish, the cut trace, and ``deboard`` as a command (gone) on tiny synthetic takes."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from conftest import board_array, needs_ffmpeg, shot_frames, write_frames
from PIL import Image

from creation.cli_produce import main
from creation.ops.floor import approve_board
from creation.post.deboard import deboard
from creation.post.edit import measure_cuts
from creation.post.finish import run_finish
from creation.post.media import count_frames, decode_frames, media_duration
from creation.post.sfx import SfxCue

BLUE = (40, 60, 200)
YELLOW = (250, 220, 60)
W, H = 96, 168

#: Kenji at 1-2 s, Aya at 3.2-4 s (the spine's two lines), as in the finish tests.
TWO_LINES = ((1.0, 2.0, 440), (3.2, 4.0, 880))
FACTS = {
    "job_id": "job_video_scene_1",
    "take_facts": {
        "job_id": "job_video_scene_1",
        "endpoint_id": "minimax/h3-max-turbo/image-to-video",
        "media_kind": "video",
        "spoken_line_count": 1,
        "shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5, "speaks": True},
            {
                "shot_index": 2,
                "start_seconds": 2.5,
                "end_seconds": 5.0,
                "speaks": False,
            },
        ],
        "sfx_cues": [
            {
                "shot_index": 2,
                "sound": "a door slams",
                "kind": "event",
                "start_seconds": 3.0,
                "duration_seconds": 1.0,
            }
        ],
    },
}


def _frames(path: Path) -> np.ndarray:
    return decode_frames(path, width=W, height=H)


def _board_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(board_array()).save(path)
    return path


def _leak_take(
    path: Path, board_frames: int, *, total: int = 72, **audio: object
) -> Path:
    board = board_array()
    frames = [board] * board_frames + shot_frames(total - board_frames, BLUE)
    return write_frames(path, frames, **audio)  # type: ignore[arg-type]


def _cut_take(path: Path) -> Path:
    """3 s: blue shot for 36 frames, a hard cut at 1.5 s (frame 36), then a yellow shot."""

    return write_frames(path, shot_frames(36, BLUE) + shot_frames(36, YELLOW, start=36))


def _no_new_mp4(takes: Path, before: set[str]) -> bool:
    return {p.name for p in takes.glob("*.mp4")} == before


# ================================================================================================
# deboard
# ================================================================================================


@needs_ffmpeg
def test_deboard_is_gone_and_points_at_the_servers_trim(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    _leak_take(takes / "take-ep01-t1-raw-v1.mp4", 3)
    before = {p.name for p in takes.glob("*.mp4")}

    assert main(["deboard", "--desk", str(post_desk)]) == 2

    err = capsys.readouterr().err
    assert (
        "the server cuts the storyboard frames" in err
        and "trim --episode N --take tK --start S" in err
    )
    assert _no_new_mp4(takes, before), "nothing is written on this laptop"


@needs_ffmpeg
def test_deboard_refuses_to_overwrite(tmp_path: Path) -> None:
    raw = _leak_take(tmp_path / "raw.mp4", 2)
    board = _board_png(tmp_path / "board.png")
    taken = tmp_path / "out.mp4"
    taken.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        deboard(raw, board, taken)
    assert taken.read_bytes() == b"keep"


def _fake_sfx(calls: list[SfxCue]):
    from conftest import make_tone

    def render(cue: SfxCue, target: Path) -> Path:
        calls.append(cue)
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.8)

    return render


def _fake_bed(spine: dict, music: str | None, target: Path) -> Path:
    from conftest import make_tone

    return make_tone(target.with_suffix(".wav"), seconds=6.0, freq=220, volume=0.9)


@needs_ffmpeg
def test_finish_deboards_first_and_later_steps_keep_the_raw_timeline(
    post_desk: Path,
) -> None:
    takes = post_desk / "ep01" / "takes"
    raw = _leak_take(takes / "take-ep01-t1-raw-v1.mp4", 2, total=120, tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    board = _board_png(post_desk / "ep01" / "boards" / "board-ep01-t1-1-v1.png")
    approve_board(post_desk, episode=1, take_id="t1", image=board)
    cues: list[SfxCue] = []
    out = io.StringIO()

    result = run_finish(post_desk, sfx_render=_fake_sfx(cues), facts_fetcher=lambda *a: None,
                        colour=False, stream=out)  # fmt: skip

    assert result.complete, out.getvalue()
    steps = [s.step for s in result.steps]
    assert steps[0] == "deboard" and steps[1] == "sfx", steps
    deboarded = takes / "take-ep01-t1-deboard-v1.mp4"
    assert result.steps[0].status == "ran" and result.steps[0].output == deboarded
    # The chain carries on from the deboarded file, and nothing after it moves: same length, same frames,
    # the take-facts cue lands at its filmed time (no offset shift is due), captions on their lines.
    assert count_frames(deboarded) == count_frames(raw)
    assert abs(media_duration(deboarded) - media_duration(raw)) < 0.05
    assert [round(c.start, 2) for c in cues] == [3.0]
    assert "a door slams @3.00s" in result.steps[1].detail
    captions = next(s for s in result.steps if s.step == "captions")
    assert (
        captions.status == "ran"
        and "1.0" in captions.detail
        and "3.2" in captions.detail
    ), captions.detail
    # The clones (a held still) are cut off last: frame 0 is the first real frame, the picture moves at once.
    assert count_frames(result.final) == count_frames(raw) - 2
    assert "held head cut" in out.getvalue()
    final = _frames(result.final)
    board_px = _frames(raw)[0]
    assert np.abs(final[0] - board_px).mean() > 10, (
        "the finished take no longer opens on the board"
    )


@needs_ffmpeg
def test_finish_no_deboard_keeps_the_board_frames(post_desk: Path) -> None:
    takes = post_desk / "ep01" / "takes"
    _leak_take(takes / "take-ep01-t1-raw-v1.mp4", 2, total=120, tones=TWO_LINES)
    board = _board_png(post_desk / "ep01" / "boards" / "board-ep01-t1-1-v1.png")
    approve_board(post_desk, episode=1, take_id="t1", image=board)

    result = run_finish(post_desk, deboard=False, facts_fetcher=lambda *a: None,
                        colour=False, stream=io.StringIO())  # fmt: skip

    assert result.steps[0].step == "deboard" and result.steps[0].status == "skipped"
    assert not (takes / "take-ep01-t1-deboard-v1.mp4").exists()


# ================================================================================================
# cut trace
# ================================================================================================


@needs_ffmpeg
def test_measure_cuts_finds_the_hard_cut(post_desk: Path) -> None:
    raw = _cut_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")

    assert measure_cuts(raw) == (1.5,)
