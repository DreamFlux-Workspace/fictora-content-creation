"""Openers and endings on real media (ffmpeg): the opening checks, the settled tail, the hard bed end,
the optional freeze-black, the held head deboard leaves, and the review's Opening section on any episode."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from conftest import (
    board_array,
    make_take,
    make_tone,
    needs_ffmpeg,
    shot_frames,
    write_frames,
)
from PIL import Image

from creation.post.media import (
    count_frames,
    decode_frames,
    measure_rms_windows,
    probe_video,
)
from creation.post.opening import measure_opening, measure_tail

FPS = 24


def _black_still(path: Path, seconds: float = 2.0) -> Path:
    return make_take(path, seconds=seconds, colour="black", tones=((0.2, 0.6, 440),))


def _moving(
    path: Path, count: int = 48, tint: tuple[int, int, int] = (200, 180, 150)
) -> Path:
    frames = [frame.copy() for frame in shot_frames(count, tint)]
    for index, frame in enumerate(frames):
        # A bright square sweeping across: clear motion every frame.
        x = (index * 3) % (frame.shape[1] - 20)
        frame[60:80, x : x + 20] = 255
    return write_frames(path, frames)


@needs_ffmpeg
def test_a_black_still_opening_is_dark_and_static(tmp_path: Path) -> None:
    reading = measure_opening(_black_still(tmp_path / "dark.mp4"), where="ep03 t1")
    joined = " ".join(reading.warnings)
    assert "ep03 t1 opens dark" in joined and "ep03 t1 is static" in joined
    assert reading.face is None and reading.face_source == "none"


@needs_ffmpeg
def test_a_lit_moving_opening_is_clean_and_the_head_count_stands_in_for_faces(
    tmp_path: Path,
) -> None:
    clip = _moving(tmp_path / "lit.mp4")
    assert measure_opening(clip).warnings == ()
    faceless = measure_opening(clip, head_count_face=0.0)
    assert any("no face on frame 0" in w for w in faceless.warnings)
    silent = measure_opening(clip, head_count_face=0.0, silent_open=True)
    assert silent.warnings == ()


@needs_ffmpeg
def test_a_stand_in_detector_reads_frame_zero(tmp_path: Path) -> None:
    from creation.post.faces import FaceReading

    seen: list[tuple[int, ...]] = []

    def detector(frame: np.ndarray) -> FaceReading:
        seen.append(frame.shape)
        return FaceReading(1, 0.06)

    reading = measure_opening(_moving(tmp_path / "lit.mp4"), detector=detector)
    assert seen == [(480, 270, 3)]
    assert reading.face == pytest.approx(1.0) and reading.faces == 1
    assert reading.face_source == "detector"


@needs_ffmpeg
def test_a_settled_tail_after_the_last_line_is_warned(tmp_path: Path) -> None:
    from creation.post.opening import tail_warning

    frames = [f.copy() for f in shot_frames(48, (200, 180, 150))]
    for index, frame in enumerate(frames[:24]):
        frame[60:80, index * 3 : index * 3 + 20] = 255
    frames[24:] = [
        frames[23].copy() for _ in frames[24:]
    ]  # the last second holds still
    clip = write_frames(tmp_path / "tail.mp4", frames)

    reading = measure_tail(clip, last_mark=0.8)
    assert reading.settled_seconds > 0.8
    assert tail_warning(reading, what="the reel") is not None
    assert measure_tail(clip, last_mark=1.9).settled_seconds < 0.3


@needs_ffmpeg
def test_the_bed_stops_hard_with_the_last_frame(tmp_path: Path) -> None:
    """No 1.5 s fade-out: the bed in the last 0.3 s plays at its level (a 50 ms click guard only)."""

    from creation.post.mix import mix_take

    take = make_take(tmp_path / "take.mp4", seconds=5.0, tones=((0.0, 0.0, 440),))
    bed = make_tone(tmp_path / "bed.wav", seconds=6.0, freq=220, volume=0.5)
    out = mix_take(take, tmp_path / "mix.mp4", bed=bed, bed_db=-10.0).output
    levels = measure_rms_windows(out, window_seconds=0.1)
    body = float(np.median(levels[20:30]))
    end = float(np.median(levels[-4:-1]))
    assert body - end < 2.0, (
        f"the bed fades out at the end: {body:.1f} dB -> {end:.1f} dB"
    )


@needs_ffmpeg
def test_freeze_black_holds_the_last_frame_then_cuts_to_black(tmp_path: Path) -> None:
    from creation.post.ending import apply_ending

    clip = _moving(tmp_path / "reel.mp4")
    out = apply_ending(clip, tmp_path / "ended.mp4", style="freeze-black")

    before, after = probe_video(clip), probe_video(out)
    assert after.duration_seconds == pytest.approx(
        before.duration_seconds + 0.7, abs=0.06
    )
    assert count_frames(out) == count_frames(clip) + round(0.7 * FPS)
    frames = decode_frames(out, width=48, height=84)
    last_real = frames[count_frames(clip) - 1]
    held = frames[count_frames(clip) + 5]
    assert float(np.abs(held - last_real).mean()) < 3.0, "the peak frame holds"
    assert float(frames[-1].mean()) < 4.0, "then black"
    levels = measure_rms_windows(out, window_seconds=0.1)
    assert max(levels[-6:]) < -60, "the sound stops on the peak"
    hard = apply_ending(clip, tmp_path / "hard.mp4", style="hard")
    assert count_frames(hard) == count_frames(clip)


BOARD_FRAMES = 10
TWO_LINES = ((1.0, 2.0, 440), (3.2, 4.0, 880))
FACTS = {
    "job_id": "job_video_scene_1",
    "take_facts": {
        "job_id": "job_video_scene_1",
        "shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5, "speaks": True},
            {"shot_index": 2, "start_seconds": 2.5, "end_seconds": 5.0, "speaks": False},
        ],
        "sfx_cues": [],
    },
}  # fmt: skip


def _fake_bed(spine: dict, music: str | None, target: Path) -> Path:
    return make_tone(target.with_suffix(".wav"), seconds=6.0, freq=220, volume=0.9)


def _board_take(post_desk: Path) -> Path:
    from creation.ops.floor import approve_board

    takes = post_desk / "ep01" / "takes"
    frames = [board_array()] * BOARD_FRAMES + shot_frames(
        120 - BOARD_FRAMES, (40, 60, 200)
    )
    raw = write_frames(takes / "take-ep01-t1-raw-v1.mp4", frames, tones=TWO_LINES)
    board = post_desk / "ep01" / "boards" / "board-ep01-t1-1-v1.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(board_array()).save(board)
    approve_board(post_desk, episode=1, take_id="t1", image=board)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    return raw


@needs_ffmpeg
def test_finish_cuts_the_held_head_after_deboard(post_desk: Path) -> None:
    from creation.post.finish import run_finish
    from creation.post.finish_record import latest_finish_record

    raw = _board_take(post_desk)
    out = io.StringIO()
    result = run_finish(post_desk, bed_maker=_fake_bed, facts_fetcher=lambda *a: None,
                        colour=False, stream=out)  # fmt: skip

    assert result.complete, out.getvalue()
    assert result.steps[0].step == "deboard" and result.steps[0].output is not None
    assert count_frames(result.final) == count_frames(raw) - BOARD_FRAMES, (
        out.getvalue()
    )
    assert "held head cut" in out.getvalue()
    record = latest_finish_record(post_desk, 1, "t1")
    assert record is not None
    edit = next(e for e in record.edits if e.get("op") == "handles")
    assert edit["source"] == "kit-deboard"
    assert edit["start_s"] == pytest.approx(BOARD_FRAMES / FPS, abs=1e-3)
    # Kenji's caption (1.0 s as filmed) moved with the picture.
    master = record.resolve(post_desk, "master")
    assert master is not None
    from creation.post.reel import parse_ass_cues

    first = parse_ass_cues(master.with_suffix(".ass").read_text(encoding="utf-8"))[0]
    assert first.start == pytest.approx(1.0 - BOARD_FRAMES / FPS, abs=0.05)


def test_the_head_cut_never_clips_a_line_under_the_board_frames() -> None:
    from creation.post.deboard import head_cut

    assert head_cut(10, 24.0, first_speech=1.0, known=True)[0] == pytest.approx(
        10 / 24, abs=1e-4
    )
    short, why = head_cut(10, 24.0, first_speech=0.25, known=True)
    assert short is not None and short < 0.25 and "cut short" in why
    assert head_cut(10, 24.0, first_speech=0.02, known=True)[0] is None
    assert head_cut(10, 24.0, first_speech=None, known=False)[0] is None
    assert head_cut(0, 24.0, first_speech=None, known=True)[0] is None


@needs_ffmpeg
def test_review_reads_the_opening_of_any_episodes_first_take(post_desk: Path) -> None:
    from creation.post.review import review_take

    take = _black_still(post_desk / "ep02" / "takes" / "take-ep02-t1-raw-v1.mp4")
    review = review_take(
        post_desk, episode=2, take_id="t1", take_file=take, face_detector=None
    )
    opening = next(s for s in review.sections if s.name == "Opening")
    assert opening.status == "⚠"
    assert any("ep02 t1 opens dark" in d for d in opening.details)
    later = review_take(
        post_desk, episode=2, take_id="t2",
        take_file=_black_still(post_desk / "ep02" / "takes" / "take-ep02-t2-raw-v1.mp4"),
        face_detector=None,
    )  # fmt: skip
    assert not any(s.name == "Opening" for s in later.sections)


def test_the_ending_flag_is_on_reel_and_join() -> None:
    import argparse

    from creation.cli_post import add_post_parsers

    parser = argparse.ArgumentParser()
    add_post_parsers(parser.add_subparsers(dest="command"))
    reel = parser.parse_args(
        ["reel", "--desk", "d", "--episode", "4", "--ending", "freeze-black"]
    )
    assert reel.ending == "freeze-black"
    joined = parser.parse_args(["join", "--desk", "d", "--episode", "4"])
    assert joined.ending is None
    with pytest.raises(SystemExit):
        parser.parse_args(["reel", "--desk", "d", "--episode", "4", "--ending", "fade"])


def test_the_local_detector_finds_no_face_on_a_flat_frame() -> None:
    from creation.post.faces import local_detector

    detector = local_detector()
    if detector is None:
        pytest.skip(
            "opencv-python-headless 4.x is not installed (uv sync --extra faces)"
        )
    reading = detector(np.full((480, 270, 3), 128, dtype=np.uint8))
    assert reading.count == 0 and reading.score == 0.0
