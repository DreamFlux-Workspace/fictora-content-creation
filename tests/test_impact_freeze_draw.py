"""The kit draws the server's strike freeze exactly as planned (founder, 15 Oct 2026).

The server decides whether and where a fight's strike holds (``freeze_s`` on a
moment of ``POST /v1/jobs/{id}/impact-plan``); the kit only draws it: the held
frames show the hit frame, with the hit frame's own move, and the take keeps
its frame count and its sound.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from conftest import needs_ffmpeg

from creation.post.impact_post import (
    ImpactMoment,
    ImpactPlan,
    ImpactPlanUnavailable,
    apply_impacts,
    plan_from_server_answer,
)
from creation.post.media import ffmpeg_bin

FPS = 24


def _moving_take(path: Path, *, hit_at: float = 1.5, seconds: float = 3.0) -> Path:
    subprocess.run(
        [ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc2=s=180x320:r={FPS}:d={seconds},"
               f"drawbox=x=40:y=110:w=100:h=100:color=white:t=fill:enable='gte(t,{hit_at})'",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _frames(path: Path) -> np.ndarray:
    raw = subprocess.run(
        [
            ffmpeg_bin(),
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 320, 180).astype(np.float64)


def test_the_server_s_freeze_reaches_the_plan_and_a_bad_one_is_no_plan() -> None:
    plan = plan_from_server_answer(
        {
            "moments": [
                {
                    "time_s": 1.5,
                    "until_s": 2.3,
                    "style": "hit",
                    "label": "Ren elbows the guard",
                    "source": "planned",
                    "freeze_s": 0.125,
                }
            ]
        }  # fmt: skip
    )
    (moment,) = plan.moments
    assert moment.freeze == 0.125
    assert (
        moment.one_line()
        == "Ren elbows the guard: punch-in + shake + 0.12s freeze at 1.50s"
    )
    assert [plan.held_from(i, FPS) for i in (36, 37, 39, 40)] == [36, 36, 36, 40]
    with pytest.raises(ImpactPlanUnavailable, match="freeze"):
        plan_from_server_answer(
            {
                "moments": [
                    {
                        "time_s": 1.5,
                        "until_s": 2.3,
                        "style": "hit",
                        "label": "x",
                        "freeze_s": 3.0,
                    }
                ]
            }
        )


@needs_ffmpeg
def test_the_held_frames_show_the_hit_frame_and_the_take_keeps_its_length_and_sound(
    tmp_path: Path,
) -> None:
    take = _moving_take(tmp_path / "t.mp4")
    plan = ImpactPlan((ImpactMoment(1.5, 2.3, "hit", "strike", "planned", 0.125),))

    done = apply_impacts(take, plan, tmp_path / "held.mp4")
    plain = apply_impacts(
        take,
        ImpactPlan((ImpactMoment(1.5, 2.3, "hit", "strike"),)),
        tmp_path / "plain.mp4",
    )

    before, after, unheld = _frames(take), _frames(done.output), _frames(plain.output)
    assert before.shape == after.shape, (
        "same frame count: the hold replaces frames, it never adds them"
    )
    hit = round(1.5 * FPS)
    for index in (hit + 1, hit + 2, hit + 3):
        assert np.abs(after[index] - after[hit]).mean() < 0.05, index
    assert np.abs(after[hit + 4] - after[hit]).mean() > 0.05, (
        "the take moves on after the hold"
    )
    assert np.abs(unheld[hit + 1] - unheld[hit]).mean() > 0.05, "no freeze, no hold"

    def audio(path: Path) -> str:
        return subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip

    assert audio(done.output) == audio(take), "the sound is copied untouched"
