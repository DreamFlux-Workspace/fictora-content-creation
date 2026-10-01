"""join --take-file X --from-record R: a re-captioned copy of a recorded take joins in its place (L-20261001-9)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import needs_ffmpeg
from test_post_join import (
    BOX,
    clip,
    finished_take,
    frame_at,
    noise_bed,
    region,
)

from creation.cli_produce import main
from creation.ops.floor import init_series_desk


@pytest.fixture
def join_desk(tmp_path: Path) -> Path:
    return init_series_desk(tmp_path, "Join Test", band="30s", episode_count=1)


def recaption(
    source: Path, out: Path, *, at: tuple[float, float], audio: str = "copy"
) -> Path:
    """What a re-caption does: the same picture with a caption box drawn again, the sound copied."""

    vf = (
        f"drawbox=x={BOX[0]}:y={BOX[1]}:w={BOX[2]}:h={BOX[3]}:color=white:t=fill:"
        f"enable='between(t,{at[0]},{at[1]})'"
    )
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(source),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    args += ["-c:a", "copy"] if audio == "copy" else ["-af", audio, "-c:a", "aac"]
    subprocess.run([*args, str(out)], check=True)
    return out


def _join(desk: Path, *files: str) -> int:
    args = ["join", "--desk", str(desk)]
    for f in files:
        args += f.split("\x00")
    return main(args)


def _tf(path: Path) -> str:
    return f"--take-file\x00{path}"


def _fr(path: Path) -> str:
    return f"--from-record\x00{path}"


@needs_ffmpeg
def test_a_recaptioned_copy_of_the_master_joins_in_its_place_and_is_noted(
    join_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = finished_take(join_desk, 1, "t1", grey=70)
    two = finished_take(join_desk, 1, "t2", grey=90)
    noise_bed(join_desk)
    fixed = recaption(
        two["master"],
        join_desk / "ep01" / "takes" / "take-ep01-t2-recap-v1.mp4",
        at=(0.5, 1.5),
    )

    assert _join(join_desk, _tf(one["final"]), _tf(fixed), _fr(two["master"])) == 0

    marked = Path(capsys.readouterr().out.strip().splitlines()[-1])
    # t2 starts at 2.5 s; its new caption runs 0.5-1.5 s of the take.
    assert region(frame_at(marked, 3.5), BOX) > 200, (
        "the re-captioned picture is what was joined"
    )
    notes = (join_desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")
    assert "take-ep01-t2-recap-v1.mp4 stands in for take-ep01-t2-cap-v1.mp4" in notes


@needs_ffmpeg
def test_without_from_record_the_copy_is_refused_and_the_flag_is_named(
    join_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = finished_take(join_desk, 1, "t1")
    two = finished_take(join_desk, 1, "t2")
    noise_bed(join_desk)
    fixed = recaption(
        two["master"],
        join_desk / "ep01" / "takes" / "take-ep01-t2-recap-v1.mp4",
        at=(0.5, 1.5),
    )

    assert _join(join_desk, _tf(one["final"]), _tf(fixed)) == 2
    err = capsys.readouterr().err
    assert "no finish record names this file" in err and "--from-record" in err


@needs_ffmpeg
def test_a_copy_of_another_length_is_refused(
    join_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = finished_take(join_desk, 1, "t1")
    two = finished_take(join_desk, 1, "t2")
    noise_bed(join_desk)
    other = clip(
        join_desk / "ep01" / "takes" / "take-ep01-t2-recap-v1.mp4", seconds=2.0, grey=70
    )

    assert _join(join_desk, _tf(one["final"]), _tf(other), _fr(two["master"])) == 2
    assert "not the same length" in capsys.readouterr().err


@needs_ffmpeg
def test_a_copy_with_other_sound_is_refused(
    join_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = finished_take(join_desk, 1, "t1")
    two = finished_take(join_desk, 1, "t2")
    noise_bed(join_desk)
    louder = recaption(
        two["master"],
        join_desk / "ep01" / "takes" / "take-ep01-t2-recap-v1.mp4",
        at=(0.5, 1.5),
        audio="volume=8",
    )

    assert _join(join_desk, _tf(one["final"]), _tf(louder), _fr(two["master"])) == 2
    assert "its sound is not the recorded file's" in capsys.readouterr().err


@needs_ffmpeg
def test_a_copy_of_the_marked_final_is_refused(
    join_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = finished_take(join_desk, 1, "t1")
    two = finished_take(join_desk, 1, "t2")
    noise_bed(join_desk)
    fixed = recaption(
        two["final"],
        join_desk / "ep01" / "takes" / "take-ep01-t2-recap-v1.mp4",
        at=(0.5, 1.5),
    )

    assert _join(join_desk, _tf(one["final"]), _tf(fixed), _fr(two["final"])) == 2
    err = capsys.readouterr().err
    assert "marked" in err and "take-ep01-t2-cap-v1.mp4" in err


@needs_ffmpeg
def test_from_record_must_name_a_recorded_file_and_a_file_to_stand_in_for(
    join_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = finished_take(join_desk, 1, "t1")
    two = finished_take(join_desk, 1, "t2")
    noise_bed(join_desk)
    stray = clip(
        join_desk / "ep01" / "takes" / "take-ep01-t2-other-v1.mp4", seconds=2.5, grey=70
    )

    assert _join(join_desk, _tf(one["final"]), _tf(stray), _fr(stray)) == 2
    assert "no finish record names" in capsys.readouterr().err

    assert (
        _join(join_desk, _tf(one["final"]), _tf(two["final"]), _fr(two["master"])) == 2
    )
    assert (
        "every --take-file is already one a finish record names"
        in capsys.readouterr().err
    )
