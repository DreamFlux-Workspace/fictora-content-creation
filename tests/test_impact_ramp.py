"""The kit draws the server's contact ramp exactly as planned (founder, 16 Oct 2026).

The server decides whether and where a fight take slows a planned contact
(``ramp`` on a moment of ``POST /v1/jobs/{id}/impact-plan``: fictora-drama
``impact_post.ContactRamp``); the kit only draws it: inside the window each
frame is the take's frame on the ramp's clock, outside it every frame is its
own, and the take keeps its frame count and its sound.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import needs_ffmpeg
from test_impact_freeze_draw import _frames, _moving_take

from creation.post.impact_post import (
    ContactRamp,
    ImpactPlan,
    ImpactPlanUnavailable,
    apply_impacts,
    plan_from_server_answer,
)
from creation.post.media import ffprobe_bin

FPS = 24
RAMP = {
    "start_s": 0.9,
    "slow_start_s": 1.15,
    "slow_end_s": 1.85,
    "end_s": 2.3,
    "speed": 0.45,
}


def _answer(ramp: object) -> dict[str, object]:
    return {
        "moments": [
            {"time_s": 1.5, "until_s": 2.3, "style": "hit", "label": "Ren's sword meets the scythe",
             "source": "planned", "ramp": ramp}
        ]
    }  # fmt: skip


def test_the_servers_ramp_reaches_the_plan_and_a_bad_one_is_no_plan() -> None:
    (moment,) = plan_from_server_answer(_answer(RAMP)).moments
    assert moment.ramp == ContactRamp(0.9, 1.15, 1.85, 2.3, 0.45)
    assert moment.freeze == 0
    assert (
        moment.one_line()
        == "Ren's sword meets the scythe: punch-in + shake + 0.70s slow-mo ramp (0.45x) at 1.50s"
    )
    (plain,) = plan_from_server_answer(_answer(None)).moments
    assert plain.ramp is None
    for bad in (
        {**RAMP, "speed": 1.5},
        {**RAMP, "slow_start_s": 1.6},
        {"start_s": 1},
        "fast",
    ):
        with pytest.raises(ImpactPlanUnavailable):
            plan_from_server_answer(_answer(bad))


def test_the_ramps_clock_matches_the_servers() -> None:
    ramp = ContactRamp(0.9, 1.15, 1.85, 2.3, 0.45)
    assert ramp.source_seconds(0.9) == pytest.approx(0.9)
    assert ramp.source_seconds(1.5) == pytest.approx(1.5), (
        "the contact shows at its own time"
    )
    assert ramp.source_seconds(2.3) == pytest.approx(2.3)
    assert ramp.source_seconds(1.7) - ramp.source_seconds(1.5) == pytest.approx(
        0.45 * 0.2
    )  # type: ignore[operator]
    assert ramp.source_seconds(0.5) is None


@needs_ffmpeg
def test_a_drawn_ramp_moves_only_its_window_and_keeps_the_length_and_sound(
    tmp_path: Path,
) -> None:
    take = _moving_take(tmp_path / "take.mp4")
    plan = plan_from_server_answer(_answer(RAMP))
    plain = ImpactPlan(tuple(replace(m, ramp=None) for m in plan.moments))
    ramped = apply_impacts(take, plan, tmp_path / "ramped.mp4").output
    still = apply_impacts(take, plain, tmp_path / "plain.mp4").output

    a, b = _frames(ramped), _frames(still)
    assert a.shape == b.shape == _frames(take).shape, "same frame count"
    for index in range(len(a)):
        t = index / FPS
        if t < 0.9 - 1 / FPS or t > 2.3 + 1 / FPS:
            assert abs(a[index] - b[index]).mean() < 1.0, index
    inside = [
        i
        for i in range(len(a))
        if 0.95 < i / FPS < 2.25 and abs(i / FPS - 1.5) > 1 / FPS
    ]
    assert sum(abs(a[i] - b[i]).mean() > 1.0 for i in inside) >= len(inside) // 2

    def audio(path: Path) -> str:
        return subprocess.run(
            [ffprobe_bin(), "-v", "error", "-select_streams", "a", "-show_entries", "stream=duration",
             "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip

    assert audio(ramped) == audio(take), "the sound is copied untouched"


def test_the_kit_draws_the_servers_frame_list_and_never_redoes_the_clock() -> None:
    listed = {**RAMP, "fps": 24.0, "frames": [[22, 21.5], [23, 22.25]]}
    (moment,) = plan_from_server_answer(_answer(listed)).moments
    assert moment.ramp is not None
    assert moment.ramp.source_position(22, 24.0) == 21.5, (
        "the server's number, not the kit's maths"
    )
    assert moment.ramp.source_position(30, 24.0) is None, (
        "a frame the server did not list is its own"
    )
    assert moment.ramp.source_position(30, 30.0) == pytest.approx(
        moment.ramp.source_seconds(1.0) * 30.0
    ), "another frame rate falls back to the clock"
    with pytest.raises(ImpactPlanUnavailable):
        plan_from_server_answer(_answer({**RAMP, "frames": [[22, 21.5]]}))  # no fps
    with pytest.raises(ImpactPlanUnavailable):
        plan_from_server_answer(_answer({**RAMP, "fps": 24.0, "frames": [["x", 1]]}))


def test_very_different_neighbours_step_instead_of_blending() -> None:
    import numpy as np

    from creation.post.impact_post import between_frames

    dark, light = np.zeros((32, 32, 3), np.uint8), np.full((32, 32, 3), 200, np.uint8)
    assert (between_frames(dark, light, 0.3) == dark).all()
    assert (between_frames(dark, light, 0.7) == light).all()
    near = np.full((32, 32, 3), 4, np.uint8)
    assert between_frames(dark, near, 0.5).mean() == pytest.approx(2.0, abs=0.6)
