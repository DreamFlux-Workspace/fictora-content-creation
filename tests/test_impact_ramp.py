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


def test_a_stepped_window_shows_whole_frames_and_a_blended_one_blends() -> None:
    import numpy as np

    from creation.post.impact_post import between_frames

    dark, light = np.zeros((32, 32, 3), np.uint8), np.full((32, 32, 3), 200, np.uint8)
    assert (between_frames(dark, light, 0.3, step=True) == dark).all()
    assert (between_frames(dark, light, 0.7, step=True) == light).all()
    assert between_frames(dark, light, 0.5).mean() == pytest.approx(100.0, abs=0.6)


@needs_ffmpeg
def test_finish_lays_each_effect_in_the_ramp_at_its_frames_shown_time(
    tmp_path: Path,
) -> None:
    """The kit asks for the server's ramp before laying effects and moves them by the server's frame list."""

    import copy
    import json
    import re

    from test_impact_server_plan import FIGHT_FACTS, _finish
    from test_sfx_motion import _desk
    from datetime import date

    facts = copy.deepcopy(FIGHT_FACTS)
    facts["take_facts"]["fight"] = True
    facts["take_facts"]["sfx_cues"].append(
        {"shot_index": 2, "sound": "rain on the dock", "kind": "ambience", "start_seconds": 3.45,
         "duration_seconds": 1.0, "source": "sound_line"}
    )  # fmt: skip
    # The server's list is drawn from a slightly different clock than its header numbers say, so the test
    # proves the kit follows the list (the server's mapping) and never its own maths.
    server = ContactRamp(2.9, 3.2, 3.8, 4.4, 0.25)
    frames = [
        [index, round(server.source_seconds(index / FPS) * FPS, 4)]  # type: ignore[operator]
        for index in range(70, 106)
        if server.source_seconds(index / FPS) is not None
    ]
    ramp = {"start_s": 2.9, "slow_start_s": 3.2, "slow_end_s": 3.8, "end_s": 4.4, "speed": 0.3, "fps": 24.0,
            "frames": frames}  # fmt: skip

    def run(with_ramp: bool) -> tuple[list[float], int]:
        root = tmp_path / ("ramp" if with_ramp else "plain")
        desk = _desk(root, date(2026, 10, 14), box_at=3.5)
        (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
            json.dumps(facts)
        )
        calls: list[int] = []

        def planner(body: dict[str, object]):  # type: ignore[no-untyped-def]
            calls.append(1)
            moment = {"time_s": 3.5, "until_s": 4.3, "style": "hit", "label": "Ren elbows the guard",
                      "source": "planned"}  # fmt: skip
            return plan_from_server_answer(
                {"moments": [{**moment, "ramp": ramp} if with_ramp else moment]}
            )

        result, _log = _finish(desk, impact_planner=lambda *a: planner)
        sfx = next(s for s in result.steps if s.step == "sfx")
        return [float(t) for t in re.findall(r"@(\d+\.\d+)s", sfx.detail)], len(calls)

    plain, plain_calls = run(False)
    ramped, ramped_calls = run(True)
    assert plain_calls == 1 and ramped_calls == 1, (
        "asked once: before the effects, reused by the impact step"
    )
    assert len(plain) == len(ramped) >= 2
    # Where the server's list puts each moment (its own clock, 0.25x), not the header's 0.3x maths.
    want = [round(server.screen_seconds(t, FPS), 2) for t in plain]
    assert ramped == pytest.approx(want, abs=0.006), (
        "each effect at its frame's shown time (the server's list)"
    )
    assert any(abs(a - b) > 0.05 for a, b in zip(plain, ramped)), (
        "an effect inside the window moved"
    )


def _video(path: Path, frames: list) -> Path:  # type: ignore[type-arg]
    from creation.post.media import ffmpeg_bin

    height, width = frames[0].shape[:2]
    subprocess.run(
        [ffmpeg_bin(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(path)],
        input=b"".join(f.tobytes() for f in frames), check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
def test_the_window_steps_or_blends_once_from_its_most_changed_tile(
    tmp_path: Path,
) -> None:
    """The server's rule: a spark in one corner barely moves the whole-frame mean, one tile sees it."""

    import numpy as np

    from creation.post.impact_post import ramp_window_steps, tile_difference

    width, height, count = 64, 112, 72
    calm = [np.full((height, width, 3), 60 + k // 3, np.uint8) for k in range(count)]
    burst = [frame.copy() for frame in calm]
    for k in range(1, count, 2):
        burst[k][0:14, 0:10] = 250
    assert float(np.abs(burst[33].astype(int) - burst[34].astype(int)).mean()) < 6.0
    assert tile_difference(burst[33][..., 0], burst[34][..., 0]) > 12.0
    ramp = ContactRamp(1.0, 1.2, 2.0, 2.4, 0.3)
    assert ramp_window_steps(_video(tmp_path / "burst.mp4", burst), ramp) is True
    assert ramp_window_steps(_video(tmp_path / "calm.mp4", calm), ramp) is False


def test_the_hit_frame_is_whole_on_the_kits_own_clock_too() -> None:
    contact = 1.6 + 0.4 / FPS
    ramp = ContactRamp(1.0, contact - 0.5, contact + 0.5, 2.3, 0.3)
    position = ramp.source_position(round(contact * FPS), FPS)
    assert position is not None and position == round(position)


@needs_ffmpeg
def test_finish_moves_a_hand_cue_in_the_ramp_like_the_effects(tmp_path: Path) -> None:
    """A hand cue (``--cue FILE@T``) inside the ramp window is laid where its frame now shows."""

    import copy
    import json
    import re
    from datetime import date

    from conftest import make_tone
    from test_impact_server_plan import FIGHT_FACTS, _finish
    from test_sfx_motion import _desk

    from creation.post.hand import Placed

    facts = copy.deepcopy(FIGHT_FACTS)
    facts["take_facts"]["fight"] = True
    server = ContactRamp(2.9, 3.2, 3.8, 4.4, 0.25)
    frames = [
        [i, round(server.source_seconds(i / FPS) * FPS, 4)]  # type: ignore[operator]
        for i in range(70, 106)
        if server.source_seconds(i / FPS) is not None
    ]
    ramp = {"start_s": 2.9, "slow_start_s": 3.2, "slow_end_s": 3.8, "end_s": 4.4, "speed": 0.3, "fps": 24.0,
            "frames": frames}  # fmt: skip

    def run(with_ramp: bool) -> float:
        root = tmp_path / ("ramp" if with_ramp else "plain")
        desk = _desk(root, date(2026, 10, 14), box_at=3.5)
        (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
            json.dumps(facts)
        )
        cue = make_tone(root / "clang.wav", seconds=0.3, freq=900)
        moment = {"time_s": 3.5, "until_s": 4.3, "style": "hit", "label": "Ren elbows the guard",
                  "source": "planned"}  # fmt: skip
        answer = {"moments": [{**moment, "ramp": ramp} if with_ramp else moment]}
        result, _log = _finish(
            desk,
            impact_planner=lambda *a: lambda body: plan_from_server_answer(answer),
            cues=(Placed(cue, 3.45),),
        )
        step = next(s for s in result.steps if s.step == "cues")
        (start,) = [float(t) for t in re.findall(r"@(\d+\.\d+)s", step.detail)]
        return start

    assert run(False) == pytest.approx(3.45, abs=0.006)
    assert run(True) == pytest.approx(server.screen_seconds(3.45, FPS), abs=0.006), (
        "the server's list, not the kit's maths"
    )


@needs_ffmpeg
def test_a_stepped_window_draws_only_real_frames(tmp_path: Path) -> None:
    """Before the hit (no punch-in yet) every frame of a stepped window is one of the take's own frames."""

    import numpy as np

    width, height, count = 64, 112, 72
    frames = [np.full((height, width, 3), 20 + 3 * k, np.uint8) for k in range(count)]
    for k in range(1, count, 2):
        frames[k][0:14, 0:10] = 250  # a flickering spark: the window steps
    take = _video(tmp_path / "take.mp4", frames)
    answer = _answer(
        {
            "start_s": 0.9,
            "slow_start_s": 1.15,
            "slow_end_s": 1.85,
            "end_s": 2.3,
            "speed": 0.3,
        }
    )
    plan = plan_from_server_answer(answer)
    out = apply_impacts(take, plan, tmp_path / "out.mp4").output
    drawn, source = _gray(out), _gray(take)
    for index in range(round(0.95 * FPS), round(1.45 * FPS)):
        assert min(abs(drawn[index] - s).mean() for s in source) < 1.0, (
            f"frame {index} is a blend"
        )


def _gray(path: Path):  # type: ignore[no-untyped-def]
    import numpy as np

    from creation.post.media import ffmpeg_bin

    raw = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, np.uint8).reshape(-1, 112, 64).astype(np.float64)
