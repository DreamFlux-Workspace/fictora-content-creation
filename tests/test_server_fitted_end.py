"""The kit draws and cuts a take's end exactly as the server says (fictora-drama PR #853 review).

A fight take cut on its last hit's knockback keeps 0.4 s after the hit. The
server fits that hit's punch-in to ease out by the last kept frame
(``hold_s`` / ``release_s``, or ``punch_in: false`` for a frozen strike with
no room) and fades the sound over the last 0.08 s of a cut-short end
(``playable_window.end_fade_s``). The kit's finish draws its own punch-ins and
cuts its own handles, so it must apply those numbers, never work them out.
"""

from __future__ import annotations

import subprocess
from array import array
from pathlib import Path

import pytest
from conftest import make_take, needs_ffmpeg

from creation.post.impact_post import (
    ImpactPlanUnavailable,
    moment_move,
    plan_from_server_answer,
)
from creation.post.take_handles import handles_from, keep_frames

#: Iron Saint ep 1 take 1: the hit at 7.792 s, the take cut at 8.208 s. The server's fitted answer.
FITTED = {
    "moments": [
        {"time_s": 7.792, "until_s": 8.208, "style": "hit", "label": "iron chest impact",
         "source": "planned", "hold_s": 0.1404, "release_s": 0.234}
    ],
    "skipped": [],
    "summary": "iron chest impact: punch-in + shake at 7.79s",
}  # fmt: skip
LAST_KEPT_FRAME = 8.208 - 1 / 24


def test_the_servers_fitted_punch_in_is_back_to_normal_on_the_last_kept_frame() -> None:
    (moment,) = plan_from_server_answer(FITTED).moments
    assert (moment.hold, moment.release, moment.punch) == (0.1404, 0.234, True)
    hit = moment_move(moment, 7.792)
    assert hit is not None and hit.zoom > 1.09
    last = moment_move(moment, LAST_KEPT_FRAME)
    assert last is not None
    assert last.zoom == pytest.approx(1.0, abs=1e-6)
    assert (last.shift_x, last.shift_y) == (
        pytest.approx(0.0, abs=1e-6),
        pytest.approx(0.0, abs=1e-6),
    )


def test_without_the_fit_the_last_kept_frame_was_still_zoomed_in() -> None:
    """Why: the kit's own 0.3 s / 0.5 s numbers left the take zoomed at the cut (the zoom jump)."""

    plain = {"moments": [dict(FITTED["moments"][0], until_s=8.592)]}
    for key in ("hold_s", "release_s"):
        plain["moments"][0].pop(key)
    (moment,) = plan_from_server_answer(plain).moments
    assert (moment.hold, moment.release) == (None, None)
    last = moment_move(moment, LAST_KEPT_FRAME)
    assert last is not None and last.zoom > 1.05


def test_a_fitted_blasts_shake_dies_out_with_its_release() -> None:
    """A blast shakes for 0.5 s, longer than the 0.375 s left: the server's fit puts it at rest too."""

    blast = {"moments": [dict(FITTED["moments"][0], style="blast")]}
    (moment,) = plan_from_server_answer(blast).moments
    last = moment_move(moment, LAST_KEPT_FRAME)
    assert last is not None
    assert (last.zoom, last.shift_x, last.shift_y) == (
        pytest.approx(1.0, abs=1e-6),
        pytest.approx(0.0, abs=1e-6),
        pytest.approx(0.0, abs=1e-6),
    )


def test_a_frozen_strike_without_room_keeps_its_hit_stop_and_no_zoom() -> None:
    frozen = {
        "moments": [
            {"time_s": 7.792, "until_s": 8.208, "style": "hit", "label": "iron chest impact",
             "source": "planned", "freeze_s": 0.2, "punch_in": False}
        ]
    }  # fmt: skip
    (moment,) = plan_from_server_answer(frozen).moments
    assert moment.freeze == 0.2 and moment.punch is False
    move = moment_move(moment, 7.85)
    assert move is not None and (move.zoom, move.shift_x, move.shift_y) == (
        1.0,
        0.0,
        0.0,
    )


@pytest.mark.parametrize(
    "field", [("hold_s", -0.1), ("hold_s", 0.4), ("release_s", 0), ("release_s", True)]
)
def test_a_malformed_fit_is_refused(field: tuple[str, object]) -> None:
    bad = {"moments": [dict(FITTED["moments"][0], **{field[0]: field[1]})]}
    with pytest.raises(ImpactPlanUnavailable):
        plan_from_server_answer(bad)


# --- the server's end fade ---------------------------------------------------------------------------


def test_the_handles_carry_the_servers_end_fade() -> None:
    trim = {"start_s": 0.042, "end_s": 8.208, "source": "auto"}
    window = {"frame_rate": 24.0, "take_duration_s": 9.417, "end_fade_s": 0.08}
    handles = handles_from(trim, window)
    assert handles is not None and handles.end_fade_s == 0.08
    # An older server sends none, and the kit makes none up.
    older = handles_from(trim, {"frame_rate": 24.0, "take_duration_s": 9.417})
    assert older is not None and older.end_fade_s is None


def _peak(path: Path, start: float, seconds: float) -> int:
    run = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{seconds}", "-i", str(path), "-ac", "1",
         "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    )  # fmt: skip
    samples = array("h")
    samples.frombytes(run.stdout[: len(run.stdout) - len(run.stdout) % 2])
    return max((abs(value) for value in samples), default=0)


@needs_ffmpeg
def test_the_kits_cut_fades_a_cut_short_end_over_the_servers_fade(
    tmp_path: Path,
) -> None:
    """The thud on the last hit dies away over the server's 0.08 s instead of stopping dead."""

    take = make_take(tmp_path / "take.mp4", seconds=4.0, tones=((0.0, 4.0, 440),))
    stop = 72  # 3.0 s at 24 fps
    faded = keep_frames(
        take, tmp_path / "faded.mp4", first=0, stop=stop, fps=24.0, end_fade_s=0.08
    )
    plain = keep_frames(take, tmp_path / "plain.mp4", first=0, stop=stop, fps=24.0)
    body = _peak(faded, 2.5, 0.3)
    assert body > 1000
    assert _peak(faded, 2.98, 0.02) < body * 0.3
    assert _peak(faded, 2.8, 0.05) > body * 0.8
    # Without the server's fade (an older server), the tone runs to the cut.
    assert _peak(plain, 2.98, 0.02) > body * 0.8
