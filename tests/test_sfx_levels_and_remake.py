"""Kit-laid effects are levelled over the music bed; a silent effect is re-made once (same cue).

Hana L-20261001-5 (seen 10x): planned effects buried 15 dB under the music or
silent, raised by hand with ``--sfx-adjust``; Noodle24's clink 17 dB under.
Gallery L-20261008-20: the only planned effect came back silent and stopped
the finish. Founder decision (9 Oct): levelling and timing reach every show,
continuing ones included, on condition that ONLY the level changes: same cues,
same sounds, no new or swapped sound; a re-make asks for the same cue.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from conftest import make_take, make_tone, needs_ffmpeg
from test_post_finish import FACTS, TWO_LINES, fake_bed

from creation.post.finish import run_finish
from creation.post.sfx import (
    SFX_GAIN_DB,
    BedReference,
    SfxCue,
    SfxPlan,
    bed_levelled_gain,
    lay_sfx,
    plan_from_take_facts,
    service_renderer,
)
from creation.rules_epoch import desk_rules


def _cue(**kw: Any) -> SfxCue:
    base = dict(
        shot_index=1, sound="a cup clinks", kind="event", start=1.0, seconds=1.0
    )
    base.update(kw)
    return SfxCue(**base)  # type: ignore[arg-type]


BED = BedReference(
    levels=(-20.0,) * 20, bed_db=0.0, take_gain_db=0.0, voice_peak_db=-10.0
)


# --- Levelling (L-20261001-5) ---------------------------------------------------------------------


def test_a_buried_effect_is_raised_to_sit_six_db_under_the_bed() -> None:
    # Rendered peak -29 dB at -8 dB gain: heard at -37, 17 dB under a -20 dB bed (the Noodle24 clink).
    gain, why = bed_levelled_gain(_cue(), -29.0, BED)
    assert gain == pytest.approx(SFX_GAIN_DB + 11.0)  # -37 -> -26: 6 dB under the bed
    assert why.startswith(
        "+11 dB: it sat 17 dB under the music bed, now about 6 dB under"
    )


def test_an_effect_already_heard_is_never_lowered() -> None:
    assert bed_levelled_gain(_cue(), -12.0, BED) == (SFX_GAIN_DB, "")


def test_the_raise_stops_at_the_gain_ceiling_and_under_the_voice() -> None:
    gain, why = bed_levelled_gain(_cue(), -49.0, replace(BED, voice_peak_db=None))
    assert gain == 10.0 and why.startswith("+18 dB")  # the raise ceiling
    gain, why = bed_levelled_gain(
        _cue(gain_db=-2.0), -49.0, replace(BED, voice_peak_db=None)
    )
    assert gain == 10.0 and "held at +10 dB gain" in why
    gain, why = bed_levelled_gain(_cue(), -45.0, replace(BED, voice_peak_db=-40.0))
    assert gain == pytest.approx(5.0) and "held under the voice" in why


def test_a_cue_a_note_lowered_stays_that_much_further_under() -> None:
    noted = _cue(gain_db=SFX_GAIN_DB - 6.0, note_ids=("n1",))
    gain, _why = bed_levelled_gain(noted, -29.0, BED)
    # heard at -43, the target is 12 dB under (6 + the note's 6): raised to -32.
    assert gain - noted.gain_db == pytest.approx(11.0)


def test_with_no_bed_or_a_silent_render_nothing_changes() -> None:
    assert bed_levelled_gain(_cue(), -29.0, replace(BED, levels=())) == (
        SFX_GAIN_DB,
        "",
    )
    assert bed_levelled_gain(_cue(), -90.0, BED) == (SFX_GAIN_DB, "")


@needs_ffmpeg
def test_levelling_changes_only_the_level_never_the_cue_or_its_sound(
    tmp_path: Path,
) -> None:
    """Founder condition (9 Oct): a continuing show keeps exactly its cues; only their level moves."""

    take = make_take(tmp_path / "take.mp4", tones=TWO_LINES)
    plan = plan_from_take_facts(FACTS)
    asked: list[tuple[str, float]] = []

    def faint(cue: SfxCue, target: Path) -> Path:
        asked.append((cue.sound, cue.seconds))
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.07)

    results = {}
    for legacy in (True, False):
        # Under a continuing (legacy) desk's rules and under today's: the same cues either way.
        rules = desk_rules(tmp_path / "2026-09-01-old") if legacy else desk_rules(None)
        rules.__enter__()
        out = tmp_path / f"out-{legacy}.mp4"
        cache = tmp_path / f"sfx-{legacy}"
        plain = lay_sfx(take, plan, cache_dir=cache, output=out, render=faint)
        levelled = lay_sfx(
            take, plan, cache_dir=cache, output=tmp_path / f"lev-{legacy}.mp4", render=faint,
            bed=BedReference(levels=(-12.0,) * 40, bed_db=0.0, take_gain_db=6.0, voice_peak_db=-6.0),
        )  # fmt: skip
        rules.__exit__(None, None, None)
        results[legacy] = (plain, levelled)
        same = lambda c: (c.shot_index, c.sound, c.kind, c.start, c.seconds, c.source)  # noqa: E731
        assert [same(c) for c in levelled.mixed] == [same(c) for c in plain.mixed]
        assert [same(c) for c in plain.mixed] == [
            same(replace(c, seconds=c.seconds)) for c in plan.cues
        ]
        assert levelled.levelled and all(
            b.gain_db > a.gain_db for a, b in zip(plain.mixed, levelled.mixed)
        )
    # The levelled run reused the cached renders: nothing new asked for, no new sound.
    assert {sound for sound, _ in asked} == {c.sound for c in plan.cues}


# --- Re-make a silent effect once (L-20261008-20) -------------------------------------------------


@needs_ffmpeg
def test_a_silent_render_is_re_made_once_with_the_same_sound(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", tones=TWO_LINES)
    plan = SfxPlan(cues=(_cue(sound="a lamp creaks", start=2.0),), speech=())
    asked: list[SfxCue] = []

    def silent_then_heard(cue: SfxCue, target: Path) -> Path:
        asked.append(cue)
        return make_tone(
            target,
            seconds=cue.seconds,
            freq=300,
            volume=0.0 if cue.remake == 0 else 0.5,
        )

    result = lay_sfx(
        take,
        plan,
        cache_dir=tmp_path / "sfx",
        output=tmp_path / "o.mp4",
        render=silent_then_heard,
    )

    assert [(c.sound, c.start, c.seconds, c.remake) for c in asked] == [
        ("a lamp creaks", 2.0, 1.0, 0),
        ("a lamp creaks", 2.0, 1.0, 1),
    ]
    assert [c.sound for c in result.mixed] == ["a lamp creaks"] and result.skipped == ()


@needs_ffmpeg
def test_the_servers_silent_answer_is_re_made_too(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", tones=TWO_LINES)
    plan = SfxPlan(cues=(_cue(),), speech=())
    asked: list[int] = []

    def server(cue: SfxCue, target: Path) -> Path:
        asked.append(cue.remake)
        raise ValueError("wrong shape: silent")

    result = lay_sfx(take, plan, cache_dir=tmp_path / "sfx", output=tmp_path / "a.mp4",
                     render=lambda c, t: server(c, t) if c.remake == 0 else make_tone(t, seconds=c.seconds))  # fmt: skip
    assert asked == [0] and len(result.mixed) == 1


@needs_ffmpeg
def test_a_silent_file_in_the_cache_is_never_laid_again(tmp_path: Path) -> None:
    take = make_take(tmp_path / "take.mp4", tones=TWO_LINES)
    cue = _cue()
    cache = tmp_path / "sfx"
    make_tone(cache / f"{cue.cache_key}.mp3", seconds=1.0, volume=0.0)
    asked: list[int] = []

    def render(c: SfxCue, target: Path) -> Path:
        asked.append(c.remake)
        return make_tone(target, seconds=c.seconds, freq=300)

    result = lay_sfx(
        take,
        SfxPlan(cues=(cue,), speech=()),
        cache_dir=cache,
        output=tmp_path / "o.mp4",
        render=render,
    )
    assert asked == [0] and len(result.mixed) == 1


def test_a_re_make_asks_the_server_for_the_same_sound_under_a_new_key() -> None:
    """The server caches a cue by its content, so the re-make must differ in content, never in sound."""

    calls: list[dict[str, Any]] = []

    class Audio:
        def sfx_cue(self, **kw: Any) -> dict[str, Any]:
            calls.append(kw)
            return {"shape_problem": "silent"}

    render = service_renderer(Audio(), "sp1")  # type: ignore[arg-type]
    for remake in (0, 1):
        with pytest.raises(ValueError, match="wrong shape"):
            render(_cue(remake=remake), Path("/nonexistent"))
    first, again = calls
    assert first["sound"] == again["sound"] == "a cup clinks"
    assert first["key"] != again["key"] and first["seconds"] != again["seconds"]
    assert _cue(remake=1).cache_key == _cue().cache_key


@needs_ffmpeg
def test_finish_levels_a_buried_cue_over_the_bed_on_its_own(post_desk: Path) -> None:
    """What operators did by hand (``--sfx-adjust "cup=+10"``) the finish now does."""

    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )

    def quiet(cue: SfxCue, target: Path) -> Path:
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.07)

    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=quiet, bed_maker=fake_bed, facts_fetcher=lambda *a: None,
                        stream=out)  # fmt: skip
    sfx = next(s for s in result.steps if s.step == "sfx")
    assert "levelled over the music bed: a door slams +" in sfx.detail, sfx.detail
    mix = next(s for s in result.steps if s.step == "mix")
    assert "!! cue 'a door slams'" not in mix.detail, mix.detail


# --- A wanted hit then a quiet tail is not a broken sound (Sweet Racket L-20261007-1) -------------


def test_a_hit_then_a_bed_is_checked_as_that_shape() -> None:
    from creation.post.sfx import HIT_THEN_TAIL, checked_shape, shape_problem

    slam = _cue(kind="sustained", sound="a car door slams, then a low engine idle")
    assert checked_shape(slam) == HIT_THEN_TAIL
    hit_then_low = (-10.0, -14.0, -30.0, -31.0, -30.0, -32.0, -31.0, -30.0)
    assert (
        shape_problem("sustained", hit_then_low, legacy=False) is not None
    )  # the old false warning
    assert shape_problem(checked_shape(slam), hit_then_low) is None
    assert (
        shape_problem(HIT_THEN_TAIL, (-10.0, -14.0, -90.0, -90.0, -90.0))
        == "the tail after the hit is silent"
    )
    # A plain bed, and an event, keep their own checks.
    assert (
        checked_shape(_cue(kind="sustained", sound="rain on the window")) == "sustained"
    )
    assert (
        checked_shape(_cue(kind="sustained", sound="an engine idles, then dies"))
        == "sustained"
    )
    assert (
        checked_shape(_cue(kind="event", sound="a door slams then rattles")) == "event"
    )


# --- Hand cues (--cue) are levelled the same way (Stream D open item, 9 Oct) ----------------------


def test_a_buried_hand_cue_is_raised_like_a_planned_effect(tmp_path: Path) -> None:
    from creation.post.hand import HandPlan, Placed, level_hand_cues

    cue = tmp_path / "cue-oden-simmer-v1.mp3"
    cue.write_bytes(b"")  # never read: the meter is passed in
    plan = HandPlan(cues=((Placed(cue, 1.0), 1.0),))
    levelled, notes = level_hand_cues(plan, BED, measure=lambda _p: (-29.0, -30.0))
    ((placed, seconds),) = levelled.cues
    # The same answer the planned effect gets at the same level (the Noodle24 clink case).
    want, _why = bed_levelled_gain(_cue(), -29.0, BED)
    assert placed.gain_db == pytest.approx(want) == pytest.approx(SFX_GAIN_DB + 11.0)
    assert (placed.path, placed.start, seconds) == (cue, 1.0, 1.0), (
        "only the level changes"
    )
    assert len(notes) == 1 and notes[0].startswith(
        "cue-oden-simmer-v1.mp3 @1.00s +11 dB"
    )

    heard, notes = level_hand_cues(plan, BED, measure=lambda _p: (-12.0,))
    assert heard == plan and notes == (), "a cue already heard is never lowered"
    assert level_hand_cues(plan, None) == (plan, ()), "no bed: nothing changes"

    asked = HandPlan(cues=((Placed(cue, 1.0, gain_db=SFX_GAIN_DB - 6.0), 1.0),))
    ((lowered, _s),) = level_hand_cues(asked, BED, measure=lambda _p: (-29.0,))[0].cues
    # The operator's @-14 dB is a deliberate cut: heard at -43, raised to sit 12 dB under (-32), not 6.
    assert lowered.gain_db == pytest.approx(SFX_GAIN_DB - 6.0 + 11.0)


@needs_ffmpeg
def test_finish_levels_a_buried_hand_cue_over_the_bed(post_desk: Path) -> None:
    from creation.post.hand import Placed

    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    quiet = make_tone(
        post_desk / "ep01" / "sfx" / "cue-oden-simmer-v1.mp3",
        seconds=0.8,
        freq=300,
        volume=0.07,
    )
    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=lambda c, t: make_tone(t, seconds=c.seconds, freq=500, volume=0.6),
                        bed_maker=fake_bed, facts_fetcher=lambda *a: None, colour=False,
                        cues=(Placed(quiet, 3.4),), stream=out)  # fmt: skip
    cues = next(s for s in result.steps if s.step == "cues")
    assert cues.status == "ran", cues.detail
    assert (
        "levelled over the music bed: cue-oden-simmer-v1.mp3 @3.40s +" in cues.detail
    ), cues.detail
