"""Local edit tools on tiny synthetic takes (real ffmpeg): deboard, trim, freeze, tempo, soften."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from conftest import board_array, needs_ffmpeg, shot_frames, write_frames
from PIL import Image

from creation.cli_produce import main
from creation.ops.floor import approve_board
from creation.post.deboard import deboard, measure_board_leak
from creation.post.edit import (
    change_tempo,
    freeze_frame,
    measure_cuts,
    shift_timed_items,
    snap_to_shot_change,
    soften_seams,
    trim_take,
)
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
@pytest.mark.parametrize("leaked", [1, 3])
def test_deboard_replaces_exactly_the_measured_board_frames_and_keeps_the_timeline(
    post_desk: Path, leaked: int, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    raw = _leak_take(takes / "take-ep01-t1-raw-v1.mp4", leaked)
    board = _board_png(post_desk / "ep01" / "boards" / "board-ep01-t1-v1.png")
    assert measure_board_leak(raw, board).frames == leaked

    assert main(["deboard", "--desk", str(post_desk), "--board", str(board)]) == 0

    out = takes / "take-ep01-t1-deboard-v1.mp4"
    assert out.is_file()
    assert f"replaced {leaked} board frame(s)" in capsys.readouterr().out
    assert count_frames(out) == count_frames(raw), (
        "same frame count: nothing after the head moves"
    )
    assert abs(media_duration(out) - media_duration(raw)) < 0.05
    frames = _frames(out)
    for index in range(leaked):
        assert np.abs(frames[index] - frames[leaked]).mean() < 3, (
            f"frame {index} is the first real frame now"
        )
    assert measure_board_leak(out, board).frames == 0
    assert "Deboard" in (post_desk / "ep01" / "run-notes.md").read_text()


@needs_ffmpeg
def test_deboard_with_no_board_frames_writes_nothing_and_says_so(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    _leak_take(takes / "take-ep01-t1-raw-v1.mp4", 0)
    board = _board_png(post_desk / "ep01" / "boards" / "board-ep01-t1-v1.png")
    before = {p.name for p in takes.glob("*.mp4")}

    assert main(["deboard", "--desk", str(post_desk), "--board", str(board)]) == 0

    assert "no board frames" in capsys.readouterr().out
    assert _no_new_mp4(takes, before)


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

    result = run_finish(post_desk, sfx_render=_fake_sfx(cues), bed_maker=_fake_bed, facts_fetcher=lambda *a: None,
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
    final = _frames(result.final)
    assert np.abs(final[0] - final[2]).mean() < 3, (
        "the finished take no longer opens on the board"
    )


@needs_ffmpeg
def test_finish_no_deboard_keeps_the_board_frames(post_desk: Path) -> None:
    takes = post_desk / "ep01" / "takes"
    _leak_take(takes / "take-ep01-t1-raw-v1.mp4", 2, total=120, tones=TWO_LINES)
    board = _board_png(post_desk / "ep01" / "boards" / "board-ep01-t1-1-v1.png")
    approve_board(post_desk, episode=1, take_id="t1", image=board)

    result = run_finish(post_desk, deboard=False, bed_maker=_fake_bed, facts_fetcher=lambda *a: None,
                        colour=False, stream=io.StringIO())  # fmt: skip

    assert result.steps[0].step == "deboard" and result.steps[0].status == "skipped"
    assert not (takes / "take-ep01-t1-deboard-v1.mp4").exists()


# ================================================================================================
# trim
# ================================================================================================


@needs_ffmpeg
def test_trim_snaps_the_cut_to_the_shot_change_and_prints_the_shift(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    finished = _cut_take(takes / "take-ep01-t1-sokii-v1.mp4")
    cues = post_desk / "ep01" / "cues-v1.json"
    cues.write_text(
        json.dumps(
            [{"start": 0.5, "end": 1.0}, {"start": 1.8}, {"start": 2.75, "end": 2.9}]
        )
    )

    code = main(["trim", "--desk", str(post_desk), "--take-file", str(finished), "--cut", "1.46-2.5",
                 "--cues-json", str(cues)])  # fmt: skip

    assert code == 0
    printed = capsys.readouterr().out
    assert "1.460 s -> 1.500 s (frame 36, shot change)" in printed, printed
    out = takes / "take-ep01-t1-trim-v1.mp4"
    assert count_frames(out) == 72 - 24
    before, after = _frames(finished), _frames(out)
    assert np.abs(after[35] - before[35]).mean() < 4, "the blue shot runs to the cut"
    assert np.abs(after[36] - before[60]).mean() < 4, (
        "the first frame after the cut is the kept frame 60"
    )
    shifted = json.loads((post_desk / "ep01" / "cues-trim-v1.json").read_text())
    assert shifted == [{"start": 0.5, "end": 1.0}, {"start": 1.75, "end": 1.9}]


@needs_ffmpeg
def test_trimming_take_two_writes_a_take_two_file(post_desk: Path) -> None:
    """--take defaulted to t1, so a trimmed take 2 was written as take-ep01-t1-trim."""

    takes = post_desk / "ep01" / "takes"
    finished = _cut_take(takes / "take-ep01-t2-sokii-v1.mp4")

    assert (
        main(
            [
                "trim",
                "--desk",
                str(post_desk),
                "--take-file",
                str(finished),
                "--cut",
                "1.46-2.5",
            ]
        )
        == 0
    )

    assert (takes / "take-ep01-t2-trim-v1.mp4").is_file()
    assert not list(takes.glob("take-ep01-t1-trim-*.mp4"))


def test_snap_keeps_the_nearest_frame_when_no_shot_change_is_near() -> None:
    diffs = np.array([0.0] + [2.0] * 40 + [100.0] + [2.0] * 30)
    assert snap_to_shot_change(1.0, fps=24.0, diffs=diffs).frame == 24
    snapped = snap_to_shot_change(1.66, fps=24.0, diffs=diffs)
    assert (snapped.frame, snapped.snapped) == (41, True)


def test_shift_timed_items_clips_what_runs_into_the_cut() -> None:
    kept, notes = shift_timed_items(
        [{"start": 1.0, "end": 2.0}, {"start": 2.2, "end": 3.5}], 1.5, 3.0
    )
    assert kept == [{"start": 1.0, "end": 1.5}, {"start": 1.5, "end": 2.0}]
    assert len(notes) == 2


@needs_ffmpeg
def test_trim_refuses_the_raw_take_and_a_cut_outside_the_take(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    raw = _cut_take(takes / "take-ep01-t1-raw-v1.mp4")
    before = {p.name for p in takes.glob("*.mp4")}

    assert main(["trim", "--desk", str(post_desk), "--cut", "1.0-2.0"]) == 2
    assert "finish the raw take first" in capsys.readouterr().err
    with pytest.raises(ValueError, match="outside the take"):
        trim_take(raw, (1.0, 9.0), takes / "x.mp4")
    assert _no_new_mp4(takes, before)


# ================================================================================================
# freeze
# ================================================================================================


@needs_ffmpeg
def test_freeze_holds_the_frame_over_the_picture_and_keeps_length_and_sound(
    tmp_path: Path,
) -> None:
    take = write_frames(tmp_path / "take.mp4", shot_frames(72, BLUE))
    out = freeze_frame(take, tmp_path / "freeze.mp4", at=1.0, hold=0.5)

    assert out.at_seconds == 1.0 and abs(out.hold_seconds - 0.5) < 1e-9
    assert count_frames(out.output) == count_frames(take)
    assert abs(media_duration(out.output) - media_duration(take)) < 0.06
    before, after = _frames(take), _frames(out.output)
    for index in range(24, 36):
        assert np.abs(after[index] - before[24]).mean() < 3, (
            f"frame {index} holds frame 24"
        )
    assert np.abs(after[36] - before[36]).mean() < 3, (
        "the picture picks up where it was"
    )
    assert np.abs(before[35] - before[24]).mean() > 5, (
        "(the source moves over the hold)"
    )


@needs_ffmpeg
def test_freeze_refuses_a_hold_past_the_end(tmp_path: Path) -> None:
    take = write_frames(tmp_path / "take.mp4", shot_frames(72, BLUE))
    with pytest.raises(ValueError, match="runs past"):
        freeze_frame(take, tmp_path / "freeze.mp4", at=2.5, hold=1.0)
    with pytest.raises(ValueError, match="more than 0"):
        freeze_frame(take, tmp_path / "freeze.mp4", at=1.0, hold=0.0)
    assert not (tmp_path / "freeze.mp4").exists()


# ================================================================================================
# tempo
# ================================================================================================


def _audio(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64)


def _dominant_hz(path: Path) -> float:
    samples = _audio(path)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1 / 8000)[int(np.argmax(spectrum))])


@needs_ffmpeg
def test_tempo_slows_picture_and_sound_together_with_the_pitch_kept(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    take = write_frames(
        takes / "take-ep01-t1-sokii-v1.mp4", shot_frames(72, BLUE), tone=440
    )

    assert (
        main(
            [
                "tempo",
                "--desk",
                str(post_desk),
                "--take-file",
                str(take),
                "--factor",
                "0.9",
            ]
        )
        == 0
    )

    out = takes / "take-ep01-t1-tempo-v1.mp4"
    assert "Tempo 0.9x" in capsys.readouterr().out
    assert abs(media_duration(out) - 3.0 / 0.9) < 0.1
    assert abs(count_frames(out) / 24.0 - 3.0 / 0.9) < 0.1, "the picture is slowed"
    assert abs(len(_audio(out)) / 8000 - 3.0 / 0.9) < 0.1, "and the sound with it"
    assert abs(_dominant_hz(out) - 440) < 8, "atempo keeps the pitch"


@needs_ffmpeg
def test_tempo_refuses_a_factor_out_of_range(tmp_path: Path) -> None:
    take = write_frames(tmp_path / "take.mp4", shot_frames(24, BLUE))
    with pytest.raises(ValueError, match="0.5-2.0"):
        change_tempo(take, tmp_path / "t.mp4", factor=0.3)
    assert not (tmp_path / "t.mp4").exists()


# ================================================================================================
# soften
# ================================================================================================


@needs_ffmpeg
def test_soften_finds_the_hard_cut_and_fades_the_last_frame_over_it(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    raw = _cut_take(takes / "take-ep01-t1-raw-v1.mp4")
    assert measure_cuts(raw) == (1.5,)

    assert main(["soften", "--desk", str(post_desk)]) == 0

    assert "Softened 1 cut(s) found at 1.50s" in capsys.readouterr().out
    out = takes / "take-ep01-t1-soften-v1.mp4"
    assert count_frames(out) == count_frames(raw)
    before, after = _frames(raw), _frames(out)
    assert np.abs(after[37] - before[37]).mean() > 20, (
        "the held blue frame lies over the new shot"
    )
    assert np.abs(after[37] - before[35]).mean() > 5, (
        "and it is fading, not a plain hold"
    )
    assert np.abs(after[48] - before[48]).mean() < 4, (
        "past 0.33 s the new shot is clean"
    )
    assert np.abs(after[20] - before[20]).mean() < 4, "before the cut nothing changes"


@needs_ffmpeg
def test_soften_with_no_hard_cut_writes_nothing(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    write_frames(takes / "take-ep01-t1-raw-v1.mp4", shot_frames(72, BLUE))
    before = {p.name for p in takes.glob("*.mp4")}

    assert main(["soften", "--desk", str(post_desk)]) == 0

    assert "No hard cuts" in capsys.readouterr().out
    assert _no_new_mp4(takes, before)
    with pytest.raises(ValueError, match="no cuts"):
        soften_seams(takes / "take-ep01-t1-raw-v1.mp4", takes / "x.mp4", ())


# ================================================================================================
# the attached cover (finish's episode thumbnail) survives every edit
# ================================================================================================


def _streams(path: Path) -> list[dict]:
    probed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name:stream_disposition=attached_pic", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return json.loads(probed.stdout)["streams"]


def _covered_take(takes: Path) -> Path:
    from creation.post.thumbnail import embed_attached_cover

    plain = _cut_take(takes / "take-ep01-t1-sokii-v1.mp4")
    cover = takes / "take-ep01-t1-thumb-v1.jpg"
    Image.new("RGB", (W, H), (200, 30, 30)).save(cover, format="JPEG")
    out = takes / "take-ep01-t1-sokii-cover-v1.mp4"
    embed_attached_cover(video=plain, cover=cover, out=out)
    return out


@needs_ffmpeg
@pytest.mark.parametrize(
    ("command", "flags"),
    [
        ("trim", ["--cut", "1.46-2.5"]),
        ("freeze", ["--at", "0.5", "--hold", "0.5"]),
        ("tempo", ["--factor", "0.9"]),
        ("soften", ["--cut", "1.5"]),
    ],
)
def test_edits_keep_the_attached_cover_and_say_so_in_the_name(
    post_desk: Path, command: str, flags: list[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    covered = _covered_take(takes)
    frames_before = count_frames(covered)

    code = main(
        [command, "--desk", str(post_desk), "--take-file", str(covered), *flags]
    )

    assert code == 0
    out = takes / f"take-ep01-t1-{command}-cover-v1.mp4"
    assert out.is_file(), sorted(p.name for p in takes.glob("*.mp4"))
    streams = _streams(out)
    kinds = [(s["codec_type"], s["disposition"]["attached_pic"]) for s in streams]
    assert kinds.count(("video", 1)) == 1, kinds  # the cover
    assert kinds.count(("video", 0)) == 1, kinds  # the picture, re-encoded
    assert ("audio", 0) in kinds
    cover = next(s for s in streams if s["disposition"]["attached_pic"])
    assert cover["codec_name"] == "mjpeg"
    # The edit worked on the picture, not on the one-frame cover.
    expected = {
        "trim": frames_before - 24,
        "freeze": frames_before,
        "soften": frames_before,
    }
    if command in expected:
        assert count_frames(out) == expected[command]
    else:
        assert count_frames(out) > frames_before
