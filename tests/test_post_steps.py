"""Local post steps one by one: SFX, mix level and duck, mark and caption position, hosted-post refusals."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

import httpx
import numpy as np
import pytest
from conftest import make_take, make_tone, needs_ffmpeg

from creation.captions import CAPTION_BAND, build_ass, caption_margin_v, fitted_size
from creation.harness.http_util import hosted_post_off
from creation.harness.session import DramaApiRunSession
from creation.post.media import measure_loudness, measure_rms_windows
from creation.post.mix import duck_expression, mix_take, pick_gain
from creation.post.sfx import (
    SfxCue,
    SfxPlan,
    lay_sfx,
    parse_adjustment,
    plan_from_take_facts,
    shape_problem,
)
from creation.post.watermark import mark_position, watermark

# --- SFX -------------------------------------------------------------------------------------


def test_plan_reads_cues_and_speaking_shots_from_take_facts_only() -> None:
    plan = plan_from_take_facts(
        {"take_facts": {
            "shots": [{"shot_index": 1, "start_seconds": 0, "end_seconds": 4, "speaks": True},
                      {"shot_index": 2, "start_seconds": 4, "end_seconds": 8, "speaks": False}],
            "sfx_cues": [{"shot_index": 2, "sound": "rain on glass", "kind": "sustained",
                          "start_seconds": 4, "duration_seconds": 4}],
        }}
    )  # fmt: skip
    assert plan.speech == ((0.0, 4.0),)
    [cue] = plan.cues
    assert (cue.sound, cue.kind, cue.start, cue.seconds, cue.gain_db) == ("rain on glass", "sustained", 4.0, 4.0, -8.0)


def test_adjustments_parse_and_refuse_a_no_op() -> None:
    assert parse_adjustment("door slam=-6").gain_change_db == -6
    assert parse_adjustment("shot:3=drop").shot_index == 3
    with pytest.raises(ValueError):
        parse_adjustment("door=0")


def test_shape_check_rejects_a_late_event_and_a_collapsing_hum() -> None:
    assert shape_problem("event", (-20.0, -25.0, -40.0)) is None
    assert shape_problem("event", (-60.0, -60.0, -10.0)) == "event starts late"
    assert shape_problem("sustained", (-10.0, -80.0, -80.0, -80.0)) == "sustained sound collapses"
    assert shape_problem("event", (-90.0,)) == "silent"


@needs_ffmpeg
def test_sfx_is_laid_under_the_take_cached_and_dropped_on_request(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", tones=((0.5, 1.0, 440),))
    plan = SfxPlan((SfxCue(2, "a door slams", "event", 2.0, 1.0),), ())
    calls: list[str] = []

    def render(cue: SfxCue, target: Path) -> Path:
        calls.append(cue.sound)
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.8)

    first = lay_sfx(take, plan, cache_dir=tmp_path / "sfx", output=tmp_path / "sfx-v1.mp4", render=render)
    levels = measure_rms_windows(first.output, window_seconds=0.5)
    assert max(levels[4:6]) > -40.0, "the cue is heard at 2-3 s"
    assert first.rendered == 1 and first.cost_usd == pytest.approx(0.002)

    second = lay_sfx(take, plan, cache_dir=tmp_path / "sfx", output=tmp_path / "sfx-v2.mp4", render=render)
    assert calls == ["a door slams"] and second.rendered == 0 and second.cost_usd == 0

    with pytest.raises(RuntimeError, match="no sound effect"):
        lay_sfx(take, plan, cache_dir=tmp_path / "sfx", output=tmp_path / "sfx-v3.mp4", render=render,
                adjustments=(parse_adjustment("door=drop"),))  # fmt: skip
    with pytest.raises(FileExistsError):
        lay_sfx(take, plan, cache_dir=tmp_path / "sfx", output=first.output, render=render)


# --- mix -------------------------------------------------------------------------------------


@needs_ffmpeg
def test_mix_measures_the_gain_and_lands_near_minus_18(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", seconds=6.0, tones=((1.0, 4.0, 440),))
    bed = make_tone(tmp_path / "bed.wav", seconds=2.0, freq=220, volume=0.2)
    result = mix_take(take, tmp_path / "mix.mp4", bed=bed)
    assert -20.0 <= result.mix_lufs <= -15.0
    assert result.gain_db == pick_gain(result.take_lufs) != 0.0
    assert result.passes == 1, "the measured gain lands the first mix; no correction pass needed"
    assert measure_loudness(result.output) == pytest.approx(result.mix_lufs, abs=0.2)
    levels = measure_rms_windows(result.output, window_seconds=0.5)
    assert levels[9] > -70.0, "4.5-5 s (after the line, before the fade) still has the 2 s bed: it loops"


@needs_ffmpeg
def test_duck_db_drops_the_bed_by_that_depth_under_the_voice(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", seconds=6.0, tones=((2.0, 4.0, 440),))
    silent = make_take(tmp_path / "silent.mp4", seconds=6.0, tones=((0.0, 0.0, 440),))
    bed = make_tone(tmp_path / "bed.wav", seconds=6.0, freq=220, volume=0.2)
    # Bed only (the take is silent) but ducked on the speaking take's windows: depth is measurable.
    result = mix_take(silent, tmp_path / "mix.mp4", bed=bed, duck_db=12.0, voice_source=take)
    levels = np.array(measure_rms_windows(result.output, window_seconds=0.25))
    outside, under = float(np.median(levels[2:6])), float(np.median(levels[10:14]))
    assert outside - under == pytest.approx(12.0, abs=1.5)
    assert "12 dB in 1 voice window" in result.ducking
    with pytest.raises(ValueError, match="--duck-db"):
        mix_take(take, tmp_path / "bad.mp4", bed=bed, duck_db=0.5)


def test_duck_expression_is_exact_depth_and_empty_without_windows() -> None:
    assert duck_expression([], 10.0) == "anull"
    expr = duck_expression([(1.0, 2.0)], 20.0)
    assert f"1-{1 - 10 ** (-1.0):.6f}" in expr and expr.endswith(":eval=frame")


# --- mark and captions -----------------------------------------------------------------------


def test_mark_sits_top_left_under_the_covered_strip() -> None:
    assert mark_position(768, 1344) == (23, 121)
    assert mark_position(720, 1280) == (22, 115)
    assert mark_position(768, 1344, y=0) == (23, math.ceil(1344 * 0.08))


@needs_ffmpeg
def test_mark_is_drawn_where_the_position_says(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", colour="black", size="768x1344", seconds=1.0)
    marked = watermark(take, tmp_path / "marked.mp4")
    frame = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(marked), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    image = np.frombuffer(frame, dtype=np.uint8).reshape(1344, 768)
    assert image[121:184, 23:95].max() > 60, "the mark is in its box"
    assert image[: math.floor(1344 * 0.08)].max() < 30, "nothing in the top strip"
    assert image[121:184, 700:].max() < 30, "nothing top right"


def test_caption_bottom_edge_is_at_62_percent_and_one_line_stays_in_band() -> None:
    assert caption_margin_v(1344) == 511
    ass = build_ass([], width=768, height=1344)
    assert ass.split("Style: House,")[1].split("\n")[0].endswith(",2,10,10,511,1")
    assert "WrapStyle: 2" in ass
    size = 50
    bottom = 1344 - caption_margin_v(1344)
    top = bottom - size * 1.25
    assert CAPTION_BAND[0] * 1344 <= top and bottom <= CAPTION_BAND[1] * 1344


def test_a_caption_too_wide_for_one_line_is_set_smaller_not_wrapped() -> None:
    assert fitted_size("Go now.", 50, 768) == 50
    long = "Extraordinarily unbelievable circumstances"
    small = fitted_size(long, 50, 768)
    assert small < 50
    from creation.captions import Cue

    ass = build_ass([Cue(0.0, 1.0, long)], width=768, height=1344)
    assert f"{{\\fs{small}}}{long}" in ass


# --- hosted post is off ----------------------------------------------------------------------


def test_hosted_post_refusals_are_recognised_and_a_real_outage_is_not() -> None:
    coded = {"error": {"code": "hosted_post_off", "message": "off"}, "request_id": "r"}
    legacy = {"error": {"code": "restate_unavailable", "message": "Durable generation orchestration is unavailable."}}
    assert hosted_post_off(409, coded, "https://x/v1/video-generations/j/post-production-runs")
    assert hosted_post_off(503, legacy, "https://x/v1/video-generations/j/post-production-runs")
    assert not hosted_post_off(503, legacy, "https://x/v1/video-generations")


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (409, {"error": {"code": "hosted_post_off", "message": "off"}, "request_id": "r"}),
        (503, {"error": {"code": "restate_unavailable", "message": "unavailable"}, "request_id": "r"}),
    ],
)
def test_the_session_tells_the_operator_to_run_finish(tmp_path: Path, status: int, body: dict) -> None:
    run = DramaApiRunSession(base_url="https://drama.test", token="t", out_dir=tmp_path, session_id="s")
    run.client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))
    with pytest.raises(SystemExit, match="fictora-produce finish"):
        run.post("/v1/video-generations/job_1/post-production-runs", {})
    with pytest.raises(SystemExit) as outage:
        run.post("/v1/video-generations", {})
    assert ("fictora-produce finish" in str(outage.value)) is (status == 409)


def test_the_drama_api_audio_service_refuses_until_its_routes_are_wired(tmp_path: Path) -> None:
    from creation.post.audio_service import AudioServicePending, DramaApiAudio

    audio = DramaApiAudio(tmp_path)
    with pytest.raises(AudioServicePending, match="no provider key is used on this laptop"):
        audio.sfx_cue(sound="a door slams", kind="event", seconds=1.0, key="k")
    with pytest.raises(AudioServicePending):
        audio.transcribe(media=tmp_path / "a.wav", key="k")


def test_the_sfx_renderer_sends_the_authored_label_not_a_prompt(tmp_path: Path) -> None:
    from creation.post import sfx as sfx_mod

    asked: list[dict] = []

    class Audio:
        def sfx_cue(self, **kwargs: object) -> str:
            asked.append(kwargs)
            return "https://audio.test/cue.mp3"

    sfx_mod_download = sfx_mod.download
    sfx_mod.download = lambda url, target: target  # type: ignore[assignment]
    try:
        render = sfx_mod.service_renderer(Audio())  # type: ignore[arg-type]
        cue = SfxCue(2, "a door slams", "event", 2.0, 0.2)
        render(cue, tmp_path / "c.mp3")
        render(cue, tmp_path / "c.mp3")
    finally:
        sfx_mod.download = sfx_mod_download  # type: ignore[assignment]
    assert asked[0] == {"sound": "a door slams", "kind": "event", "seconds": 0.5, "key": f"sfx-{cue.cache_key}"}
    assert asked[0]["key"] == asked[1]["key"], "a re-run sends the same idempotency key"
