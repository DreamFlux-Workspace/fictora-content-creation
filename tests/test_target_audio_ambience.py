"""Option C sound: a locked-voice take gets its location's ambience, not only room tone.

A take filmed with ``soundtrack.mode == "target_audio"`` comes back with exactly
our dialogue track: the model's own location sound is gone. finish makes one
ambience cue per episode from the take's sound plan (the spine's frame
location, the take facts' sustained cues), lays it at about -28 dB between the
lines, ducked under each line, and keeps room tone only as the fallback. Native
takes are untouched.
"""

from __future__ import annotations

import copy
import io
import json
import re
import subprocess
from pathlib import Path

import pytest
from conftest import SPINE, make_take, needs_ffmpeg
from test_post_finish import FACTS, TWO_LINES, fake_bed, fake_sfx
from test_target_audio_soundtrack import NATIVE, TARGET, facts_with

from creation.post.ambience import (
    AMBIENCE_DUCK_DB,
    AMBIENCE_GAP_DB,
    DESCRIPTION_LIMIT,
    DESCRIPTION_TAIL,
    RETRY_HEAD,
    CueUnusable,
    ambience_brief,
    describe,
    room_sound_phrase,
    lay_ambience,
    record_path,
)
from creation.post.finish import AMBIENCE_STEP, ROOM_TONE_STEP, run_finish
from creation.post.media import measure_rms_windows

PLACE = "Hanakaze Sweets' narrow wooden shopfront in the lively covered Kikuzaka Ginza arcade, warm afternoon"


def spine_with_place(place: str | None = PLACE) -> dict:
    """``SPINE`` with one frame for its beat, set in ``place``."""

    spine = copy.deepcopy(SPINE)
    spine["beats"][0]["frame_id"] = "frame_1"
    spine["frames"] = [
        {
            "frame_id": "frame_1",
            "episode_id": "episode_01",
            "visual_brief": {"location": place} if place else {},
        }
    ]
    return spine


def locked_facts(*, sustained: str | None = None) -> dict:
    facts = facts_with(TARGET)
    if sustained:
        facts["take_facts"]["sfx_cues"].append(
            {"shot_index": 2, "sound": sustained, "kind": "sustained",
             "start_seconds": 2.5, "duration_seconds": 2.5, "source": "sound_line"}
        )  # fmt: skip
    return facts


def noise(target: Path, seconds: float, *, ramp: bool = False) -> Path:
    """Pink noise of ``seconds``; with ``ramp`` its level climbs steadily (a seam test can tell where it is)."""

    level = f"0.02+0.4*t/{seconds:.3f}" if ramp else "0.3"
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"anoisesrc=color=pink:amplitude=1:seed=7:sample_rate=48000:duration={seconds}",
         "-af", f"volume='{level}':eval=frame", "-ac", "2", str(target)],
        check=True,
    )  # fmt: skip
    return target


def fake_maker(calls: list[tuple[str, float]], *, ramp: bool = False):
    def make(description: str, seconds: float, target: Path) -> tuple[Path, float]:
        calls.append((description, seconds))
        return noise(target.with_suffix(".wav"), seconds, ramp=ramp), 0.01

    return make


def no_maker(*_: object) -> tuple[Path, float]:
    raise AssertionError("no ambience cue may be made here")


def _desk(post_desk: Path, facts: dict, spine: dict | None, *, takes: int = 1) -> None:
    for number in range(1, takes + 1):
        make_take(
            post_desk / "ep01" / "takes" / f"take-ep01-t{number}-raw-v1.mp4",
            tones=TWO_LINES,
        )
        (post_desk / "ep01" / "api" / f"take-facts-ep01-t{number}-v1.json").write_text(
            json.dumps(facts)
        )
    if spine is not None:
        (post_desk / "ep01" / "api" / "03_spine.json").write_text(json.dumps(spine))


def _finish(
    post_desk: Path, maker, *, take_id: str = "t1", out: io.StringIO | None = None
):
    return run_finish(
        post_desk, take_id=take_id, sfx_render=fake_sfx([]), bed_maker=fake_bed,
        facts_fetcher=lambda *a: None, cut_meter=lambda _take: (3.9,), colour=False,
        ambience_maker=maker, stream=out or io.StringIO(),
    )  # fmt: skip


def _step(result, name: str):
    return next(s for s in result.steps if s.step == name)


def _mean(levels: tuple[float, ...], a: float, b: float, window: float = 0.1) -> float:
    inside = levels[int(a / window) : int(b / window)]
    return sum(inside) / len(inside)


# --- the description ---------------------------------------------------------------------------------------------


def test_the_description_comes_from_the_frame_location_and_the_planned_ambience() -> (
    None
):
    brief = ambience_brief(
        locked_facts(sustained="soft crowd murmur under the arcade roof"),
        spine_with_place(),
        episode=1,
    )
    assert brief is not None and brief.location == PLACE
    assert brief.description.startswith(f"Continuous background ambience of {PLACE[:60]}")
    assert "; soft crowd murmur under the arcade roof; " in brief.description
    assert brief.description.endswith(DESCRIPTION_TAIL)
    assert "a door slams" not in brief.description, (
        "a one-off event is laid by the sfx step, not looped"
    )

    long = ambience_brief(
        locked_facts(), spine_with_place("a very long place " * 40), episode=1
    )
    assert long is not None and len(long.description) <= DESCRIPTION_LIMIT
    assert long.description.endswith(DESCRIPTION_TAIL), "the tail is never cut"

    assert ambience_brief(locked_facts(), spine_with_place(None), episode=1) is None, (
        "no location and no planned ambience: nothing to describe, never a guess"
    )
    only_sound = ambience_brief(
        locked_facts(sustained="rain on a tin roof"), None, episode=1
    )
    assert only_sound is not None and "rain on a tin roof" in only_sound.description


def test_the_description_never_asks_for_silence_and_never_loops_a_one_off() -> None:
    """L-20261006-21 (Noodle24 ep 4, Back from the Sea): "a quiet rolling simmer" came back unusable.

    Asked for "quiet", an effects model renders dead air or a few bubbles and a decay; the server's
    shape check rejects that as a sustained cue that collapses, and room tone went in. The kit now
    words the request the way the server words the app's own beds (show_ambience.ambience_prompt).
    """

    facts = locked_facts(sustained="a quiet rolling simmer from the oden pot; the cook says nothing")
    spine = spine_with_place("A quiet, hushed noodle stall at night")
    spine["beats"][0]["motion_direction"] = {"sound_cue": "a door slams shut"}
    brief = ambience_brief(facts, spine, episode=1)
    assert brief is not None
    text = brief.description.casefold()
    assert "quiet" not in text and "hush" not in text and "says nothing" not in text, brief.description
    assert "rolling simmer from the oden pot" in text
    assert "door slams" not in text, "a beat's one-off sound is the sfx step's, never looped"
    assert brief.description.endswith(DESCRIPTION_TAIL)
    assert "even level" in DESCRIPTION_TAIL and "no single loud events" in DESCRIPTION_TAIL
    assert brief.retry_description.startswith(RETRY_HEAD + "A noodle stall at night"), brief.retry_description
    assert "simmer" not in brief.retry_description, "the re-make names the place alone"

    spine["beats"][0]["motion_direction"] = {"sound_cue": "the oden pot keeps simmering"}
    assert "oden pot keeps simmering" in ambience_brief(locked_facts(), spine, episode=1).description

    assert room_sound_phrase("the room holds; a held breath at most; no line") == ""
    assert room_sound_phrase("steady rain on the tin roof, the listener makes no sound") == (
        "steady rain on the tin roof"
    )
    assert len(describe("x " * 400, ["rain " * 80])) <= DESCRIPTION_LIMIT


@needs_ffmpeg
def test_an_unusable_ambience_is_re_made_once_as_a_steady_room_tone(post_desk: Path) -> None:
    """The first render collapsed: one re-make with the server's retry wording, then it is laid."""

    _desk(post_desk, locked_facts(sustained="a quiet rolling simmer"), spine_with_place())
    calls: list[tuple[str, float]] = []
    good = fake_maker(calls)

    def collapses_first(description: str, seconds: float, target: Path) -> tuple[Path, float]:
        if not description.startswith(RETRY_HEAD):
            calls.append((description, seconds))
            raise CueUnusable("sustained cue collapses after its first half second", 0.01)
        return good(description, seconds, target)

    out = io.StringIO()
    result = _finish(post_desk, collapses_first, out=out)
    assert len(calls) == 2 and calls[1][0].startswith(RETRY_HEAD)
    step = _step(result, AMBIENCE_STEP)
    assert step.status == "ran", step.detail
    assert "re-made once as a steady room tone" in step.detail
    assert step.cost_usd == pytest.approx(0.02), "both renders are booked"
    assert _step(result, ROOM_TONE_STEP).output is None, "the ambience fills the gaps, no room tone"
    assert json.loads(record_path(post_desk, 1).read_text())["description"].startswith(RETRY_HEAD)

    calls.clear()
    second = _finish(post_desk, collapses_first)
    assert calls == [], "a re-finish reuses the re-made cue and pays nothing"
    assert _step(second, AMBIENCE_STEP).cost_usd == 0.0

    def always_collapses(description: str, seconds: float, target: Path) -> tuple[Path, float]:
        calls.append((description, seconds))
        raise CueUnusable("silent", 0.01)

    record_path(post_desk, 1).unlink()
    for cue in (post_desk / "ep01" / "ambience").iterdir():
        cue.unlink()
    failed = _finish(post_desk, always_collapses)
    assert len(calls) == 2, "re-made once, never more"
    assert _step(failed, AMBIENCE_STEP).status == "failed"
    assert "again as a steady room tone" in _step(failed, AMBIENCE_STEP).detail
    assert _step(failed, ROOM_TONE_STEP).output is not None, "room tone carries it"


# --- laying it --------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_the_ambience_sits_at_its_level_between_lines_and_ducks_under_each_line(
    tmp_path: Path,
) -> None:
    silent = make_take(tmp_path / "silent.mp4", tones=((9.0, 9.5, 440),))
    cue = noise(tmp_path / "cue.wav", 5.0)
    laid = lay_ambience(
        silent, cue, tmp_path / "out.mp4", windows=[(1.0, 2.0)], level_db=-28.0
    )
    levels = measure_rms_windows(laid.output, window_seconds=0.1)

    gap = _mean(levels, 2.6, 4.2)
    under = _mean(levels, 1.3, 1.7)
    assert -30.0 < gap < -26.0, gap
    assert gap - under == pytest.approx(AMBIENCE_DUCK_DB, abs=1.5), (gap, under)
    assert levels[0] < gap - 10, "faded in at the episode's head"
    assert levels[-1] < gap - 6, "faded out at the episode's tail"
    with pytest.raises(FileExistsError):
        lay_ambience(silent, cue, laid.output, windows=[], level_db=-28.0)


@needs_ffmpeg
def test_laying_the_ambience_keeps_the_voice_at_its_level(tmp_path: Path) -> None:
    """A stereo cue under a mono take once re-laid the take's own voice about 4 dB quieter (live check)."""

    from creation.post.media import measure_loudness

    voiced = make_take(tmp_path / "voiced.mp4", tones=TWO_LINES)
    cue = noise(tmp_path / "cue.wav", 5.0)
    laid = lay_ambience(
        voiced,
        cue,
        tmp_path / "out.mp4",
        windows=[(1.0, 2.0), (3.2, 4.0)],
        level_db=-60.0,
    )
    assert measure_loudness(laid.output) == pytest.approx(
        measure_loudness(voiced), abs=0.5
    )


# --- finish ------------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_finish_makes_the_location_ambience_and_lays_it_instead_of_room_tone(
    post_desk: Path,
) -> None:
    _desk(post_desk, locked_facts(sustained="soft crowd murmur"), spine_with_place())
    calls: list[tuple[str, float]] = []
    out = io.StringIO()
    result = _finish(post_desk, fake_maker(calls), out=out)
    log = out.getvalue()

    assert result.complete, log
    assert [s.step for s in result.steps][:4] == [
        "deboard",
        "sfx",
        AMBIENCE_STEP,
        ROOM_TONE_STEP,
    ]
    assert len(calls) == 1
    description, seconds = calls[0]
    assert (
        PLACE in description
        and "soft crowd murmur" in description
        and "no music" in description
        and "no speech" in description
    )
    assert seconds == 5.0, "the cue covers the episode as filmed"

    ambience = _step(result, AMBIENCE_STEP)
    assert ambience.status == "ran" and ambience.detail.startswith(
        f'ambience: "{description}", -28 dB'
    )
    # The level between the lines, as laid, is the one the step reports (the mix's take gain then lands it at -28).
    laid_db = float(
        re.search(
            r"\(([+-][\d.]+) dB before the mix's ([+-][\d.]+) dB take gain\)",
            ambience.detail,
        )[1]
    )
    take_gain = float(
        re.search(r"before the mix's ([+-][\d.]+) dB take gain", ambience.detail)[1]
    )
    assert laid_db == pytest.approx(AMBIENCE_GAP_DB - take_gain, abs=1.0)
    from creation.post.media import measure_loudness
    from creation.post.mix import pick_gain

    assert pick_gain(measure_loudness(ambience.output)) == take_gain, (
        "the gain the mix picks on this very file"
    )
    levels = measure_rms_windows(ambience.output, window_seconds=0.1)
    assert _mean(levels, 2.4, 3.0) == pytest.approx(laid_db, abs=1.5), (
        "the gap between the lines"
    )
    assert _mean(levels, 0.4, 0.7) == pytest.approx(laid_db, abs=1.5), (
        "before the first line"
    )

    tone = _step(result, ROOM_TONE_STEP)
    assert tone.output is None and tone.detail.startswith(
        "no room tone: the location ambience fills the gaps"
    )
    assert "2 of 2 line(s) have voice in their window" in tone.detail, (
        "the heard-check still runs"
    )
    assert not list((post_desk / "ep01" / "takes").glob("*-room-tone-*.mp4"))
    assert "Sound: music ✓ · SFX ✓ · mix ✓ · ambience ✓ · captions ✓" in log
    assert f'- ambience: ran — ambience: "{description}", -28 dB' in log, (
        "in the summary"
    )
    notes = (post_desk / "ep01" / "run-notes.md").read_text()
    assert "Finish · ambience ->" in notes and description in notes
    record = json.loads(
        next(
            (post_desk / "ep01" / "takes").glob("take-ep01-t1-finish-v*.json")
        ).read_text()
    )
    assert Path(record["pre_bed"]).name.startswith("take-ep01-t1-ambience-"), (
        "join's bed goes over the ambience"
    )


@needs_ffmpeg
def test_a_refinish_reuses_the_cue_on_the_desk_and_pays_nothing(
    post_desk: Path,
) -> None:
    _desk(post_desk, locked_facts(), spine_with_place())
    calls: list[tuple[str, float]] = []
    first = _finish(post_desk, fake_maker(calls))
    assert (
        _step(first, AMBIENCE_STEP).cost_usd == pytest.approx(0.01) and len(calls) == 1
    )
    assert record_path(post_desk, 1).is_file()

    second = _finish(post_desk, fake_maker(calls))
    assert len(calls) == 1, "the cue on the desk is reused"
    assert second.complete and _step(second, AMBIENCE_STEP).cost_usd == 0.0
    assert "(on the desk, free)" in _step(second, AMBIENCE_STEP).detail


@needs_ffmpeg
def test_no_cue_falls_back_to_room_tone_with_a_clear_note(post_desk: Path) -> None:
    _desk(post_desk, locked_facts(), spine_with_place(None))
    out = io.StringIO()
    result = _finish(post_desk, no_maker, out=out)
    assert result.complete
    ambience = _step(result, AMBIENCE_STEP)
    assert (
        ambience.status == "skipped" and "room tone is laid instead" in ambience.detail
    )
    tone = _step(result, ROOM_TONE_STEP)
    assert tone.output is not None and tone.output.name.startswith(
        "take-ep01-t1-room-tone-"
    )
    assert "fallback: no location ambience" in tone.detail
    assert (
        "!! no location ambience on this take: room tone is laid instead"
        in out.getvalue()
    )
    assert (
        "room tone ✓" in result.sound_line() and "ambience" not in result.sound_line()
    )

    def refused(*_: object) -> tuple[Path, float]:
        raise RuntimeError("HTTP 422 sfx-cues: refused")

    _desk(post_desk, locked_facts(), spine_with_place())
    failed = _finish(post_desk, refused)
    assert failed.complete, "a refused cue never stops the take: room tone carries it"
    assert _step(failed, AMBIENCE_STEP).status == "failed"
    assert "refused" in _step(failed, AMBIENCE_STEP).detail
    assert _step(failed, ROOM_TONE_STEP).output is not None
    assert not record_path(post_desk, 1).exists(), "nothing is cached from a failed cue"


@needs_ffmpeg
def test_two_takes_carry_one_continuous_ambience_across_the_seam(
    post_desk: Path,
) -> None:
    quiet = locked_facts()
    quiet["take_facts"]["sfx_cues"] = []  # nothing over the seam but the ambience
    _desk(post_desk, quiet, spine_with_place(), takes=2)
    calls: list[tuple[str, float]] = []
    maker = fake_maker(calls, ramp=True)
    one = _finish(post_desk, maker, take_id="t1")
    two = _finish(post_desk, maker, take_id="t2")
    assert len(calls) == 1 and calls[0][1] == 10.0, "one cue for the whole episode"
    assert "from 0.00s of the episode's ambience" in _step(one, AMBIENCE_STEP).detail
    assert "from 5.00s of the episode's ambience" in _step(two, AMBIENCE_STEP).detail

    def heard(result, a: float, b: float) -> float:
        """The ambience's level in a window as the mix plays it (with the take gain the mix picks)."""

        step = _step(result, AMBIENCE_STEP)
        gain = float(
            re.search(r"before the mix's ([+-][\d.]+) dB take gain", step.detail)[1]
        )
        return _mean(measure_rms_windows(step.output, window_seconds=0.1), a, b) + gain

    # The cue climbs steadily: t2 picks up where t1 stops, with no fade out of t1 and no fade into t2.
    assert heard(two, 0.0, 0.3) == pytest.approx(heard(one, 4.6, 4.9), abs=2.0)
    assert heard(two, 0.0, 0.3) > heard(one, 0.4, 0.7) + 4, (
        "t2 does not restart the ambience"
    )


@needs_ffmpeg
def test_native_takes_get_no_ambience(post_desk: Path) -> None:
    _desk(post_desk, facts_with(NATIVE), spine_with_place())
    result = _finish(post_desk, no_maker)
    assert result.complete
    assert AMBIENCE_STEP not in [s.step for s in result.steps]
    assert ROOM_TONE_STEP not in [s.step for s in result.steps]
    assert not record_path(post_desk, 1).exists()


def test_facts_fixture_is_unchanged() -> None:
    assert FACTS["take_facts"]["sfx_cues"][0]["kind"] == "event"


# --- laid voices (Hanakaze ep 7) ---------------------------------------------------------------------------------


@needs_ffmpeg
def test_the_ambience_and_the_bed_duck_under_a_laid_voice_line_too(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A narration line laid with ``finish --voice`` sat over full ambience (about -28 dB, 10 dB under it)."""

    from conftest import make_tone

    from creation.post import finish as finish_module
    from creation.post.hand import Placed

    _desk(post_desk, locked_facts(), spine_with_place())
    voice = make_tone(
        post_desk / "ep01" / "voices" / "voice-ep01-aya-v1.wav", seconds=0.5, volume=0.5
    )
    seen: dict[str, list] = {}
    real_lay, real_mix = finish_module.lay_ambience, finish_module.mix_take

    def spy_lay(*args, **kwargs):
        seen["ambience"] = list(kwargs["windows"])
        return real_lay(*args, **kwargs)

    def spy_mix(*args, **kwargs):
        seen["bed"] = list(kwargs["duck_windows"])
        return real_mix(*args, **kwargs)

    monkeypatch.setattr(finish_module, "lay_ambience", spy_lay)
    monkeypatch.setattr(finish_module, "mix_take", spy_mix)
    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
        cut_meter=lambda _take: (3.9,), colour=False, ambience_maker=fake_maker([]),
        voices=(Placed(voice, 2.4),), over_locked_voices=True, stream=io.StringIO(),
    )  # fmt: skip

    laid = (2.4, pytest.approx(2.9, abs=0.05))
    assert any(w == laid for w in seen["ambience"]), seen["ambience"]
    assert any(w == laid for w in seen["bed"]), seen["bed"]
    ambience = _step(result, AMBIENCE_STEP)
    assert "and 1 laid voice window(s)" in ambience.detail
