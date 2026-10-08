"""The episode's opening hook is never awkwardly loud (fictora-drama, 6 Oct 2026).

A writer's own opening event ("the wok crashes against wet steel") now reaches
the take facts as the hook, ending "on the first frame", like a picked one. The
kit lays it at 0 s, measured and levelled under the take the same way the
server does: its loudest 400 ms at or under the take's loudness, its true peak
at or under the take's, never louder than the planned hook level, and a quiet
source never raised.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from conftest import make_take, needs_ffmpeg

from creation.post.media import AudioLevels, measure_levels
from creation.post.sfx import (
    OPENING_HOOK_REFERENCE_LUFS,
    OPENING_HOOK_REFERENCE_PEAK_DBTP,
    OPENING_SOUND_GAIN_DB,
    SFX_GAIN_DB,
    SfxCue,
    SfxPlan,
    lay_sfx,
    opening_hook_gain_db,
)

HOOK = "the wok crashes against wet steel, on the first frame"
TAKE = AudioLevels(integrated_lufs=-20.0, max_momentary_lufs=-16.0, true_peak_dbtp=-4.0)
#: Rounding, the fade and the AAC encode.
TOLERANCE_DB = 0.5


def test_the_hook_stands_at_most_3_lu_over_an_ordinary_effect() -> None:
    assert 2.0 <= OPENING_SOUND_GAIN_DB - SFX_GAIN_DB <= 3.0


def test_a_loud_hook_is_levelled_under_the_take_and_a_quiet_one_is_never_raised() -> (
    None
):
    crash = AudioLevels(
        integrated_lufs=-12.0, max_momentary_lufs=-6.0, true_peak_dbtp=0.0
    )
    assert (
        opening_hook_gain_db(OPENING_SOUND_GAIN_DB, crash, TAKE) == -14.0
    )  # loudness: -20 - (-6)
    bright = AudioLevels(
        integrated_lufs=-30.0, max_momentary_lufs=-26.0, true_peak_dbtp=0.0
    )
    assert (
        opening_hook_gain_db(
            OPENING_SOUND_GAIN_DB, bright, replace(TAKE, true_peak_dbtp=-9.0)
        )
        == -9.0
    )  # peak
    quiet = AudioLevels(
        integrated_lufs=-40.0, max_momentary_lufs=-36.0, true_peak_dbtp=-20.0
    )
    assert (
        opening_hook_gain_db(OPENING_SOUND_GAIN_DB, quiet, TAKE)
        == OPENING_SOUND_GAIN_DB
    )
    # An unmeasurable take: held under a spoken line's level.
    assert opening_hook_gain_db(OPENING_SOUND_GAIN_DB, crash, None) == min(
        OPENING_HOOK_REFERENCE_LUFS + 6.0, OPENING_HOOK_REFERENCE_PEAK_DBTP
    )


def _crash(path: Path, seconds: float) -> Path:
    """A full-scale crash: white noise with a fast decay."""

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=white:a=1:r=48000:s=1",
         "-af", "volume='exp(-t/0.25)':eval=frame,alimiter=limit=0.99:level=disabled", str(path)],
        check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
def test_a_loud_writer_hook_is_laid_under_the_takes_own_audio(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", seconds=4.0, tones=((1.2, 3.2, 220),))
    plan = SfxPlan(
        cues=(SfxCue(1, HOOK, "event", 0.0, 1.0, gain_db=OPENING_SOUND_GAIN_DB),),
        speech=((1.2, 3.2),),
    )

    result = lay_sfx(
        take,
        plan,
        cache_dir=tmp_path / "sfx",
        output=tmp_path / "out.mp4",
        render=lambda cue, target: _crash(target, cue.seconds),
    )
    opening = tmp_path / "opening.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(result.output), "-t", "1.1", "-vn", str(opening)],
        check=True,
    )  # fmt: skip
    laid, before = measure_levels(opening), measure_levels(take)

    assert result.mixed[0].gain_db < OPENING_SOUND_GAIN_DB
    assert laid.max_momentary_lufs is not None and before.integrated_lufs is not None
    assert laid.max_momentary_lufs <= before.integrated_lufs + TOLERANCE_DB
    assert laid.true_peak_dbtp is not None and before.true_peak_dbtp is not None
    assert laid.true_peak_dbtp <= before.true_peak_dbtp + TOLERANCE_DB
