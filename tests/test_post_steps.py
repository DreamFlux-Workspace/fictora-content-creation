"""Local post steps one by one: SFX, mix level and duck, mark and caption position, hosted-post refusals."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import httpx
import numpy as np
import pytest
from conftest import make_take, make_tone, needs_ffmpeg

from creation.captions import (
    CAPTION_BAND,
    build_ass,
    caption_margin_v,
    house_font_size,
    wrap_caption,
)
from creation.harness.http_util import hosted_post_off
from creation.harness.session import DramaApiRunSession
from creation.post.media import measure_loudness, measure_rms_windows
from creation.post.mix import (
    CueLevel,
    duck_expression,
    mix_take,
    pick_gain,
    quiet_cue_warnings,
)
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
    assert (cue.sound, cue.kind, cue.start, cue.seconds, cue.gain_db) == (
        "rain on glass",
        "sustained",
        4.0,
        4.0,
        -8.0,
    )


def test_adjustments_parse_and_refuse_a_no_op() -> None:
    assert parse_adjustment("door slam=-6").gain_change_db == -6
    assert parse_adjustment("shot:3=drop").shot_index == 3
    with pytest.raises(ValueError):
        parse_adjustment("door=0")


def test_shape_check_rejects_a_late_event_and_a_collapsing_hum() -> None:
    assert shape_problem("event", (-20.0, -25.0, -40.0)) is None
    assert shape_problem("event", (-60.0, -60.0, -10.0)) == "event starts late"
    assert (
        shape_problem("sustained", (-10.0, -80.0, -80.0, -80.0))
        == "sustained sound collapses"
    )
    assert shape_problem("event", (-90.0,)) == "silent"


def _noise(path: Path, *, seconds: float, decay_db_per_second: float = 0.0) -> Path:
    """Pink noise, steady or falling ``decay_db_per_second`` dB each second."""

    gain = f"volume='pow(10,-{decay_db_per_second}*t/20)':eval=frame"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"anoisesrc=color=pink:amplitude=0.3:d={seconds}:sample_rate=48000",
         "-af", gain, str(path)],
        check=True,
    )  # fmt: skip
    return path


@needs_ffmpeg
def test_a_constant_cue_that_fades_away_fails_the_shape_check_on_a_new_desk(
    tmp_path: Path,
) -> None:
    """L-20261001-4: a "constant" bed decaying ~25 dB over 5 s passed (half its tail within 18 dB)."""

    steady = measure_rms_windows(_noise(tmp_path / "steady.wav", seconds=5.0))
    fading = measure_rms_windows(
        _noise(tmp_path / "fade.wav", seconds=5.0, decay_db_per_second=5.0)
    )

    assert shape_problem("sustained", steady, legacy=False) is None
    assert shape_problem("sustained", steady, legacy=True) is None
    problem = shape_problem("sustained", fading, legacy=False)
    assert problem is not None and problem.startswith("sustained sound fades"), (
        fading,
        problem,
    )
    # Legacy desks keep the old rule: the same fade still passes there.
    assert shape_problem("sustained", fading, legacy=True) is None


def test_the_hold_rule_skips_the_first_second_and_short_cues() -> None:
    swell = (-40.0, -30.0, -12.0, -12.0, -12.0, -12.0, -12.0, -12.0)
    assert shape_problem("sustained", swell, legacy=False) is None
    assert shape_problem("sustained", (-12.0, -30.0, -30.0), legacy=False) is None
    # The rule follows the running command's rules when not forced.
    from creation.rules_epoch import _ACTIVE

    decay = (-12.0, -12.0, -16.0, -18.0, -24.0, -26.0, -30.0, -32.0)
    token = _ACTIVE.set(True)
    try:
        assert shape_problem("sustained", decay) is None
    finally:
        _ACTIVE.reset(token)
    assert shape_problem("sustained", decay).startswith("sustained sound fades")


@needs_ffmpeg
def test_sfx_is_laid_under_the_take_cached_and_dropped_on_request(
    tmp_path: Path,
) -> None:
    take = make_take(tmp_path / "take.mp4", tones=((0.5, 1.0, 440),))
    plan = SfxPlan((SfxCue(2, "a door slams", "event", 2.0, 1.0),), ())
    calls: list[str] = []

    def render(cue: SfxCue, target: Path) -> Path:
        calls.append(cue.sound)
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.8)

    first = lay_sfx(
        take,
        plan,
        cache_dir=tmp_path / "sfx",
        output=tmp_path / "sfx-v1.mp4",
        render=render,
    )
    levels = measure_rms_windows(first.output, window_seconds=0.5)
    assert max(levels[4:6]) > -40.0, "the cue is heard at 2-3 s"
    assert first.rendered == 1 and first.cost_usd == pytest.approx(0.002)
    [peak] = first.peaks_db
    assert peak == pytest.approx(
        max(measure_rms_windows(tmp_path / "sfx" / f"{plan.cues[0].cache_key}.mp3"))
        - 8.0,
        abs=0.2,
    )

    second = lay_sfx(
        take,
        plan,
        cache_dir=tmp_path / "sfx",
        output=tmp_path / "sfx-v2.mp4",
        render=render,
    )
    assert calls == ["a door slams"] and second.rendered == 0 and second.cost_usd == 0

    with pytest.raises(RuntimeError, match="no sound effect"):
        lay_sfx(take, plan, cache_dir=tmp_path / "sfx", output=tmp_path / "sfx-v3.mp4", render=render,
                adjustments=(parse_adjustment("door=drop"),))  # fmt: skip
    with pytest.raises(FileExistsError):
        lay_sfx(
            take, plan, cache_dir=tmp_path / "sfx", output=first.output, render=render
        )


# --- mix -------------------------------------------------------------------------------------


@needs_ffmpeg
def test_mix_measures_the_gain_and_lands_near_minus_18(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", seconds=6.0, tones=((1.0, 4.0, 440),))
    bed = make_tone(tmp_path / "bed.wav", seconds=2.0, freq=220, volume=0.2)
    result = mix_take(take, tmp_path / "mix.mp4", bed=bed)
    assert -20.0 <= result.mix_lufs <= -15.0
    assert result.gain_db == pick_gain(result.take_lufs) != 0.0
    assert result.passes == 1, (
        "the measured gain lands the first mix; no correction pass needed"
    )
    assert measure_loudness(result.output) == pytest.approx(result.mix_lufs, abs=0.2)
    levels = measure_rms_windows(result.output, window_seconds=0.5)
    assert levels[9] > -70.0, (
        "4.5-5 s (after the line, before the fade) still has the 2 s bed: it loops"
    )


@needs_ffmpeg
def test_duck_db_drops_the_bed_by_that_depth_under_the_voice(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", seconds=6.0, tones=((2.0, 4.0, 440),))
    silent = make_take(tmp_path / "silent.mp4", seconds=6.0, tones=((0.0, 0.0, 440),))
    bed = make_tone(tmp_path / "bed.wav", seconds=6.0, freq=220, volume=0.2)
    # Bed only (the take is silent) but ducked on the speaking take's windows: depth is measurable.
    result = mix_take(
        silent, tmp_path / "mix.mp4", bed=bed, duck_db=12.0, voice_source=take
    )
    levels = np.array(measure_rms_windows(result.output, window_seconds=0.25))
    outside, under = float(np.median(levels[2:6])), float(np.median(levels[10:14]))
    assert outside - under == pytest.approx(12.0, abs=1.5)
    assert "12 dB in 1 voice window" in result.ducking
    with pytest.raises(ValueError, match="--duck-db"):
        mix_take(take, tmp_path / "bad.mp4", bed=bed, duck_db=0.5)


def test_a_cue_far_under_the_bed_is_named_with_the_fix_and_a_heard_one_is_not() -> None:
    bed = (
        -20.0,
    ) * 4  # a 2 s bed of 0.5 s windows at -20 dB RMS; it loops under the 6 s take
    crack = CueLevel(
        "neck crack", 3.2, 0.5, peak_db=-39.0, gain_db=-8.0
    )  # 19 dB under: buried
    door = CueLevel(
        "a door slams", 1.0, 1.0, peak_db=-30.0, gain_db=-8.0
    )  # 10 dB under: heard

    [warning] = quiet_cue_warnings(
        [crack, door], bed_levels=bed, bed_db=0.0, take_gain_db=0.0
    )

    assert warning.startswith(
        "!! cue 'neck crack' @3.20s peaks 19 dB under the music bed (-39 dB vs -20 dB)"
    )
    # 13 dB to reach 6 under the bed: 8 from the cue (it tops out at 0 dB), 5 from the bed.
    assert (
        '`--sfx-adjust "neck crack=+8"`' in warning
        and "lower the bed about 5 dB (`--bed-db`)" in warning
    )


def test_a_cue_just_under_the_bed_is_not_warned_and_the_take_gain_counts() -> None:
    bed = (-20.0,) * 4
    cue = CueLevel("rain", 0.0, 2.0, peak_db=-31.0, gain_db=-8.0)

    assert (
        quiet_cue_warnings([cue], bed_levels=bed, bed_db=0.0, take_gain_db=0.0) == []
    )  # 11 dB under
    assert (
        quiet_cue_warnings([cue], bed_levels=bed, bed_db=0.0, take_gain_db=-2.0) != []
    )  # 13 dB under
    assert quiet_cue_warnings([cue], bed_levels=(), bed_db=0.0, take_gain_db=-2.0) == []


@needs_ffmpeg
def test_the_mix_warns_on_a_cue_the_bed_buries(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", seconds=6.0, tones=((1.0, 2.0, 440),))
    bed = make_tone(tmp_path / "bed.wav", seconds=6.0, freq=220, volume=0.9)
    buried = CueLevel(
        "neck crack", 4.0, 0.5, peak_db=-90.0, gain_db=-8.0
    )  # the bed sits near -38 dB

    result = mix_take(take, tmp_path / "mix.mp4", bed=bed, cues=[buried])

    assert len(result.warnings) == 1 and "neck crack" in result.warnings[0]
    assert "\n!! cue 'neck crack'" in result.one_line()
    assert mix_take(take, tmp_path / "mix2.mp4", bed=None, cues=[buried]).warnings == ()


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
    take = make_take(
        tmp_path / "take.mp4", colour="black", size="768x1344", seconds=1.0
    )
    marked = watermark(take, tmp_path / "marked.mp4")
    frame = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(marked), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    image = np.frombuffer(frame, dtype=np.uint8).reshape(1344, 768)
    assert image[121:184, 23:95].max() > 60, "the mark is in its box"
    assert image[: math.floor(1344 * 0.08)].max() < 30, "nothing in the top strip"
    assert image[121:184, 700:].max() < 30, "nothing top right"


@pytest.mark.parametrize(("width", "height"), [(768, 1344), (1080, 1920)])
def test_caption_bottom_edge_is_at_62_percent_and_two_lines_stay_in_band(
    width: int, height: int
) -> None:
    ass = build_ass([], width=width, height=height)
    margin = caption_margin_v(height)
    assert ass.split("Style: House,")[1].split("\n")[0].endswith(f",{margin},1")
    assert "WrapStyle: 2" in ass  # the kit breaks lines itself; libass adds none
    size = house_font_size(height)
    bottom = height - margin
    top = (
        bottom - 2 * size
    )  # libass line height is the Fontsize (winAscent + winDescent)
    assert CAPTION_BAND[0] * height <= top and bottom <= CAPTION_BAND[1] * height


def test_a_caption_too_wide_for_one_line_wraps_instead_of_shrinking() -> None:
    assert wrap_caption("Go now.", 45, 768) == (["Go now."], 45)
    long = "I have been waiting for you all day long"
    lines, size = wrap_caption(long, 45, 768)
    assert size == 45 and len(lines) == 2 and " ".join(lines) == long
    from creation.captions import Cue

    ass = build_ass([Cue(0.0, 1.0, long)], width=768, height=1344)
    assert f"House,,0,0,0,,{lines[0]}\\N{lines[1]}" in ass
    assert "\\fs" not in ass


# --- hosted post is off ----------------------------------------------------------------------


def test_hosted_post_refusals_are_recognised_and_a_real_outage_is_not() -> None:
    coded = {"error": {"code": "hosted_post_off", "message": "off"}, "request_id": "r"}
    legacy = {
        "error": {
            "code": "restate_unavailable",
            "message": "Durable generation orchestration is unavailable.",
        }
    }
    assert hosted_post_off(
        409, coded, "https://x/v1/video-generations/j/post-production-runs"
    )
    assert hosted_post_off(
        503, legacy, "https://x/v1/video-generations/j/post-production-runs"
    )
    assert not hosted_post_off(503, legacy, "https://x/v1/video-generations")


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (
            409,
            {"error": {"code": "hosted_post_off", "message": "off"}, "request_id": "r"},
        ),
        (
            503,
            {
                "error": {"code": "restate_unavailable", "message": "unavailable"},
                "request_id": "r",
            },
        ),
    ],
)
def test_the_session_tells_the_operator_to_run_finish(
    tmp_path: Path, status: int, body: dict
) -> None:
    run = DramaApiRunSession(
        base_url="https://drama.test", token="t", out_dir=tmp_path, session_id="s"
    )
    run.client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    )
    with pytest.raises(SystemExit, match="fictora-produce finish"):
        run.post("/v1/video-generations/job_1/post-production-runs", {})
    with pytest.raises(SystemExit) as outage:
        run.post("/v1/video-generations", {})
    assert ("fictora-produce finish" in str(outage.value)) is (status == 409)


class Recorder:
    """A MockTransport that answers each request from a queue and records what came in."""

    def __init__(self, answers: list[httpx.Response]) -> None:
        self.answers = answers
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answers.pop(0)


def _service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[httpx.Response]
):
    from creation.post import audio_service
    from creation.post import desk as desk_mod

    recorder = Recorder(answers)

    def open_api(desk: Path, episode: int) -> DramaApiRunSession:
        run = DramaApiRunSession(
            base_url="https://drama.test",
            token="t",
            out_dir=tmp_path,
            session_id="sess-1",
        )
        run.client = httpx.Client(transport=httpx.MockTransport(recorder))
        return run

    monkeypatch.setattr(desk_mod, "open_api", open_api)
    waits: list[float] = []
    return audio_service.DramaApiAudio(tmp_path, sleep=waits.append), recorder, waits


def test_each_audio_route_gets_its_path_body_session_and_idempotency_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def ok(body: object) -> httpx.Response:
        return httpx.Response(200, json=body)

    audio, rec, _ = _service(tmp_path, monkeypatch, [ok({"candidates": []}), ok({"audio_url": "u"}),
                                                      ok({"audio_url": "u"}), ok({"audio_url": "u"}), ok({"words": []})])  # fmt: skip
    audio.render_auditions(
        spine_id="sp",
        cast_id="cast_k",
        spine_version="sha256:v",
        lines=["Hi."],
        count=4,
        key="k1",
    )
    audio.voice_line(
        spine_id="sp", cast_id="cast_k", text="Hi.", language="en", key="k2"
    )
    audio.sfx_cue(spine_id="sp", sound="a door slams", seconds=1.0, key="k3")
    audio.music_bed(spine_id="sp", brief=None, key="k4")
    audio.transcribe(
        audio_url="https://media.test/tenants/t/drama/take.mp4",
        language="en",
        spine_id="sp",
        key="k5",
    )

    seen = [(r.url.path, json.loads(r.content), r.headers["Idempotency-Key"], r.headers["X-Drama-Session-Id"])
            for r in rec.requests]  # fmt: skip
    assert seen == [
        ("/v1/spines/sp/cast/cast_k/voice-auditions/render",
         {"spine_version": "sha256:v", "lines": ["Hi."], "candidate_count": 4}, "k1", "sess-1"),
        ("/v1/spines/sp/cast/cast_k/voice-lines", {"text": "Hi.", "language": "en"}, "k2", "sess-1"),
        ("/v1/spines/sp/sfx-cues", {"sound": "a door slams", "seconds": 1.0}, "k3", "sess-1"),
        ("/v1/spines/sp/audio-bed/render", {"pin": False}, "k4", "sess-1"),
        ("/v1/transcripts", {"audio_url": "https://media.test/tenants/t/drama/take.mp4", "language": "en",
                             "spine_id": "sp"}, "k5", "sess-1"),
    ]  # fmt: skip
    assert all(r.headers["Authorization"] == "Bearer t" for r in rec.requests)


def test_audition_new_wording_and_named_voices_are_sent_as_the_routes_text_and_voices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ok = httpx.Response(200, json={"candidates": []})
    audio, rec, _ = _service(tmp_path, monkeypatch, [ok, ok])
    audio.render_auditions(spine_id="sp", cast_id="cast_k", spine_version="sha256:v", lines=[], count=8, key="k1",
                           text="Do not break eye contact.", voices=["Sarah", "Laura"])  # fmt: skip
    audio.render_auditions(spine_id="sp", cast_id="cast_k", spine_version="sha256:v", lines=["Hi."], count=8,
                           key="k2", voices=["Callum"])  # fmt: skip

    assert [json.loads(r.content) for r in rec.requests] == [
        {
            "spine_version": "sha256:v",
            "text": "Do not break eye contact.",
            "voices": ["Sarah", "Laura"],
        },
        {"spine_version": "sha256:v", "lines": ["Hi."], "voices": ["Callum"]},
    ], "text replaces lines and voices replaces the slate size; nothing padded"


def test_rate_limits_and_in_progress_are_waited_out_and_a_failed_render_is_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def err(status: int, code: str, **h: str) -> httpx.Response:
        return httpx.Response(
            status, json={"error": {"code": code, "message": "m"}}, headers=h
        )

    audio, rec, waits = _service(tmp_path, monkeypatch, [
        err(429, "rate_limited", **{"Retry-After": "6"}),
        err(409, "operator_audio_in_progress", **{"Retry-After": "15"}),
        err(502, "operator_audio_failed"),
        httpx.Response(200, json={"audio_url": "u"}),
    ])  # fmt: skip
    assert (
        audio.sfx_cue(spine_id="sp", sound="hum", seconds=2.0, key="same")["audio_url"]
        == "u"
    )
    assert waits == [6.0, 15.0]
    assert {r.headers["Idempotency-Key"] for r in rec.requests} == {"same"}, (
        "every retry replays the same key"
    )


@pytest.mark.parametrize(
    ("status", "code", "hint"),
    [
        (409, "operator_audio_unavailable", "tell engineering"),
        (409, "operator_upload_unavailable", "stored take URLs"),
        (422, "voice_not_locked", "voice --pick"),
        (429, "budget_cap_exceeded", "budget cap"),
        (409, "idempotency_conflict", "another body"),
    ],
)
def test_refusals_stop_with_a_hint_and_are_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, code: str, hint: str
) -> None:
    from creation.post.audio_service import AudioServiceError

    body = {"error": {"code": code, "message": "no"}, "request_id": "r"}
    audio, rec, waits = _service(
        tmp_path, monkeypatch, [httpx.Response(status, json=body)]
    )
    with pytest.raises(AudioServiceError, match=hint):
        audio.voice_line(spine_id="sp", cast_id="c", text="Hi.", language="en", key="k")
    assert len(rec.requests) == 1 and waits == []


def test_the_sfx_renderer_sends_the_authored_label_and_refuses_a_bad_shape(
    tmp_path: Path,
) -> None:
    from creation.post import sfx as sfx_mod

    asked: list[dict] = []
    answers = [{"audio_url": "https://a/c.mp3", "shape_problem": None},
               {"audio_url": "https://a/c.mp3", "shape_problem": None},
               {"audio_url": "https://a/c.mp3", "shape_problem": "silent"}]  # fmt: skip

    class Audio:
        def sfx_cue(self, **kwargs: object) -> dict:
            asked.append(kwargs)
            return answers.pop(0)

    original = sfx_mod.download
    sfx_mod.download = lambda url, target: target  # type: ignore[assignment]
    try:
        render = sfx_mod.service_renderer(Audio(), "sp")  # type: ignore[arg-type]
        cue = SfxCue(2, "a door slams", "event", 2.0, 0.2)
        render(cue, tmp_path / "c.mp3")
        render(cue, tmp_path / "c.mp3")
        with pytest.raises(ValueError, match="wrong shape: silent"):
            render(cue, tmp_path / "c.mp3")
    finally:
        sfx_mod.download = original  # type: ignore[assignment]
    assert asked[0] == {
        "spine_id": "sp",
        "sound": "a door slams",
        "seconds": 0.5,
        "key": f"sfx-{cue.cache_key}",
    }
    assert asked[0]["key"] == asked[1]["key"], "a re-run sends the same idempotency key"
