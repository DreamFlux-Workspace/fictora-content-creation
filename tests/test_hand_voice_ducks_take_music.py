"""A hand-laid line over a take that carries the harness's music: the take drops under it (L-20261004-5).

New desks only. A legacy desk, or a take whose music is a bed the kit lays (the mix ducks that bed), lays
the line over the take as filmed.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from conftest import make_take, make_tone, needs_ffmpeg
from test_post_finish import fake_sfx
from test_target_audio_soundtrack import NATIVE, facts_with

from creation.post.finish import run_finish
from creation.post.hand import (
    TAKE_UNDER_VOICE_DUCK_DB,
    Placed,
    check_hand_plan,
    duck_expression,
    lay_voice,
)
from creation.post.media import measure_rms_windows
from creation.rules_epoch import run_rules_epoch

WINDOW = 0.1
#: The line sits at 2.0-2.5 s; the take is one steady low tone throughout.
LINE_AT = 2.0


def _take_level(path: Path, start: float, end: float, scratch: Path) -> float:
    """The take's own 440 Hz tone (dB) between ``start`` and ``end``, the 3 kHz line filtered out."""

    low = scratch / f"low-{path.stem}.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-vn",
         "-af", "lowpass=f=800,lowpass=f=800,lowpass=f=800", str(low)],
        check=True,
    )  # fmt: skip
    levels = measure_rms_windows(low, window_seconds=WINDOW)
    window = levels[round(start / WINDOW) : round(end / WINDOW)]
    return sum(window) / len(window)


def _line(path: Path) -> Path:
    return make_tone(path, seconds=0.5, freq=3000, volume=0.3)


def test_duck_expression_is_flat_without_windows() -> None:
    assert duck_expression((), 10.0) == "anull"
    two = duck_expression(((1.0, 2.0), (3.0, 4.0)), 10.0)
    assert two.startswith("volume='min(") and two.endswith(":eval=frame")


@needs_ffmpeg
@pytest.mark.parametrize("duck", [TAKE_UNDER_VOICE_DUCK_DB, None])
def test_lay_voice_ducks_the_take_only_when_asked(
    tmp_path: Path, duck: float | None
) -> None:
    take = make_take(tmp_path / "take.mp4", tones=((0.0, 5.0, 440),))
    plan = check_hand_plan(5.0, voices=(Placed(_line(tmp_path / "line.wav"), LINE_AT),))

    out = lay_voice(take, tmp_path / "voice.mp4", plan, duck_take_db=duck)

    under = _take_level(out, LINE_AT + 0.1, LINE_AT + 0.4, tmp_path)
    before = _take_level(out, 0.5, 1.5, tmp_path)
    after = _take_level(out, 3.5, 4.5, tmp_path)
    if duck is None:
        assert abs(under - before) < 1.0, (under, before)
    else:
        assert before - under == pytest.approx(duck, abs=1.5), (under, before)
    assert abs(after - before) < 1.0, "the take comes back up after the line"


def _finish_voice_step(desk: Path, *, music: bool) -> Path:
    raw = make_take(
        desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=((0.0, 5.0, 440),)
    )
    facts = facts_with({**NATIVE, "music": {"laid": True}} if music else NATIVE)
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(json.dumps(facts))
    line = _line(desk / "ep01" / "voices" / "voice-ep01-aya-v1.wav")
    bed = make_tone(desk / "bed.wav", seconds=6.0, freq=220, volume=0.2)
    result = run_finish(
        desk, sfx_render=fake_sfx([]), bed_maker=lambda *_a: bed, facts_fetcher=lambda *a: None,
        colour=False, thumbnail=False, voices=(Placed(line, LINE_AT),), stream=io.StringIO(),
    )  # fmt: skip
    step = next(s for s in result.steps if s.step == "voice")
    assert step.status == "ran" and step.output is not None, step.detail
    assert raw.is_file()
    return step.output


def _ducked_by(voiced: Path, scratch: Path) -> float:
    return _take_level(voiced, 0.5, 1.5, scratch) - _take_level(
        voiced, LINE_AT + 0.1, LINE_AT + 0.4, scratch
    )


@needs_ffmpeg
def test_finish_ducks_the_takes_music_under_a_hand_line_on_a_new_desk(
    post_desk: Path, tmp_path: Path
) -> None:
    voiced = _finish_voice_step(post_desk, music=True)
    assert _ducked_by(voiced, tmp_path) == pytest.approx(
        TAKE_UNDER_VOICE_DUCK_DB, abs=1.5
    )


@needs_ffmpeg
def test_finish_leaves_the_take_alone_when_the_kit_lays_the_bed(
    post_desk: Path, tmp_path: Path
) -> None:
    voiced = _finish_voice_step(post_desk, music=False)
    assert abs(_ducked_by(voiced, tmp_path)) < 1.0


@needs_ffmpeg
def test_a_legacy_desk_lays_the_line_over_the_take_as_filmed(
    post_desk: Path, tmp_path: Path
) -> None:
    run_rules_epoch(post_desk, set_to="legacy", out=io.StringIO())
    voiced = _finish_voice_step(post_desk, music=True)
    assert abs(_ducked_by(voiced, tmp_path)) < 1.0
