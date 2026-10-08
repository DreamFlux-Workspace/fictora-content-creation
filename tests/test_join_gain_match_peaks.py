"""join's gain match reads a take's body, not its jumpscare (L-20261006-6); legacy desks unchanged."""

from __future__ import annotations

import io
import math
import subprocess
from pathlib import Path

import pytest

from creation.post.join import (
    JoinPart,
    body_loudness,
    match_gains,
    momentary_loudness,
)
from creation.post.media import measure_loudness
from creation.rules_epoch import run_rules_epoch


def _tone(
    path: Path, *, seconds: float, burst: tuple[float, float] | None = None
) -> Path:
    """A steady quiet tone, with a loud burst over ``burst`` (start, length) when given."""

    quiet = "sine=f=440:sample_rate=48000:d={d},volume=0.03".format(d=seconds)
    if burst is None:
        graph = f"{quiet}[a];[a]anull"
    else:
        start, length = burst
        graph = (
            f"{quiet}[q];sine=f=220:sample_rate=48000:d={length},volume=0.9,"
            f"adelay={int(start * 1000)}:all=1,apad=whole_dur={seconds}[b];"
            "[q][b]amix=inputs=2:normalize=0"
        )
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-filter_complex", graph, "-t", str(seconds), str(path)],
        check=True,
    )  # fmt: skip
    return path


def _part(path: Path, take: str) -> JoinPart:
    return JoinPart(episode=1, take_id=take, picture=path, pre_bed=path, record=None)  # type: ignore[arg-type]


@pytest.fixture
def scare_parts(tmp_path: Path) -> list[JoinPart]:
    calm = _tone(tmp_path / "t1.wav", seconds=10)
    scare = _tone(tmp_path / "t2.wav", seconds=10, burst=(8.5, 1.0))
    return [_part(calm, "t1"), _part(scare, "t2")]


def test_body_loudness_leaves_out_a_short_peak(scare_parts: list[JoinPart]) -> None:
    calm, scare = (p.pre_bed for p in scare_parts)
    assert measure_loudness(scare) - measure_loudness(calm) > 6, (
        "the burst lifts integrated loudness"
    )
    assert body_loudness(momentary_loudness(scare)) == pytest.approx(
        body_loudness(momentary_loudness(calm)), abs=0.5
    )


def test_body_loudness_without_peaks_is_integrated(scare_parts: list[JoinPart]) -> None:
    calm = scare_parts[0].pre_bed
    assert body_loudness(momentary_loudness(calm)) == pytest.approx(
        measure_loudness(calm), abs=0.5
    )


def test_body_loudness_of_silence() -> None:
    assert body_loudness([-120.0, -90.0]) == -math.inf
    assert body_loudness([]) == -math.inf


def test_new_desk_does_not_duck_the_scare_take(
    tmp_path: Path, scare_parts: list[JoinPart]
) -> None:
    desk = tmp_path / "desk"
    desk.mkdir()
    gains = match_gains(scare_parts, desk=desk)
    assert all(abs(g) <= 0.5 for g in gains), gains


def test_legacy_desk_still_matches_whole_take_loudness(
    tmp_path: Path, scare_parts: list[JoinPart]
) -> None:
    desk = tmp_path / "desk"
    desk.mkdir()
    run_rules_epoch(desk, set_to="legacy", out=io.StringIO())
    gains = match_gains(scare_parts, desk=desk)
    assert gains[1] < -3, gains


def test_long_quiet_pauses_do_not_read_as_the_body(tmp_path: Path) -> None:
    """Lines over 4 s, near-silent room tone for 6 s: the level is the lines', as R128's gate reads it."""

    path = tmp_path / "pauses.wav"
    graph = (
        "sine=f=440:sample_rate=48000:d=4,volume=0.03[a];"
        "sine=f=440:sample_rate=48000:d=6,volume=0.0005[b];[a][b]concat=n=2:v=0:a=1"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-filter_complex",
            graph,
            str(path),
        ],
        check=True,
    )
    assert body_loudness(momentary_loudness(path)) == pytest.approx(
        measure_loudness(path), abs=1.0
    )
