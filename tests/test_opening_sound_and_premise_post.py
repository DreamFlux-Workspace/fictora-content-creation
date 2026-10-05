"""Founder decisions, 5 Oct 2026: a sound hook at 0 s on every episode (#10), and the premise line as the post title (#9).

The server plans each episode's opening hook on take 1 shot 1, its Sound label ending "on the first frame"
(fictora-drama ``opening_sound``). Finish lays it at 0 s at its own known level, never calls it a cue on the
opening line by accident, and says ``opening_sound_flat`` when take 1 has nothing in its first half second.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

from conftest import make_take, make_tone, needs_ffmpeg
from test_post_finish import FACTS, TWO_LINES, fake_bed

from creation.harness_rules import (
    OPENING_WINDOW_SECONDS,
    is_opening_cue,
    opening_sound_flat,
    opening_sound_line,
)
from creation.post.finish import run_finish
from creation.post.reel_plan import post_text
from creation.post.sfx import (
    OPENING_SOUND_GAIN_DB,
    SFX_GAIN_DB,
    SfxCue,
    noted_gain_db,
    plan_from_take_facts,
)

HOOK = "a phone buzzes twice against wood, on the first frame"


def test_the_opening_hook_is_never_called_a_cue_on_the_opening_line() -> None:
    assert is_opening_cue(HOOK)
    assert opening_sound_line(HOOK, 0.0) is None
    stray = opening_sound_line("a noodle slurp", 0.0)
    assert stray is not None and "opening line" in stray


def test_a_take_with_nothing_in_its_first_half_second_is_flat() -> None:
    flat = opening_sound_flat((("a door slams", 3.0),), episode=2)
    assert (
        flat is not None
        and flat.startswith("!! opening_sound_flat")
        and "episode 2" in flat
    )
    assert opening_sound_flat(((HOOK, 0.0),), episode=2) is None
    assert opening_sound_flat((("a cup", OPENING_WINDOW_SECONDS),), episode=2) is None
    assert opening_sound_flat((), episode=1) is not None


def test_the_hook_plays_at_its_own_known_level_and_notes_still_move_it() -> None:
    assert noted_gain_db({"sound": HOOK}) == OPENING_SOUND_GAIN_DB > SFX_GAIN_DB
    assert (
        noted_gain_db({"sound": HOOK, "gain_offset_db": -3.0})
        == OPENING_SOUND_GAIN_DB - 3.0
    )
    assert noted_gain_db({"sound": "a door slams"}) == SFX_GAIN_DB
    facts = copy.deepcopy(FACTS)
    facts["take_facts"]["sfx_cues"].insert(
        0,
        {
            "shot_index": 1,
            "sound": HOOK,
            "kind": "event",
            "start_seconds": 0.0,
            "duration_seconds": 1.0,
        },
    )
    plan = plan_from_take_facts(facts)
    assert (plan.cues[0].start, plan.cues[0].gain_db) == (0.0, OPENING_SOUND_GAIN_DB)


def test_the_premise_line_is_the_post_title_and_an_older_show_posts_as_before() -> None:
    titled = post_text(
        series="Night Shift", episode=3, title="The Jacket", genre="mystery_case",
        premise_line="The night-shift clerk is wearing his jacket.",
    )  # fmt: skip
    lines = titled.splitlines()
    assert lines[0] == "The night-shift clerk is wearing his jacket."
    assert lines[1] == "Night Shift · Episode 3: The Jacket"
    plain = post_text(series="Night Shift", episode=3, title="The Jacket")
    assert plain.startswith("Night Shift · Episode 3: The Jacket")


@needs_ffmpeg
def test_finish_lays_the_hook_at_zero_and_says_flat_when_take_one_has_none(
    post_desk: Path,
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    facts_path = post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json"
    facts_path.write_text(json.dumps(FACTS))
    calls: list[str] = []

    def render(cue: SfxCue, target: Path) -> Path:
        calls.append(cue.sound)
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.8)

    out = io.StringIO()
    run_finish(
        post_desk,
        sfx_render=render,
        bed_maker=fake_bed,
        facts_fetcher=lambda *a: None,
        stream=out,
    )
    assert "!! opening_sound_flat" in out.getvalue()
    assert "opening_sound_flat" in (post_desk / "ep01" / "run-notes.md").read_text()

    hooked = copy.deepcopy(FACTS)
    hooked["take_facts"]["sfx_cues"].insert(
        0,
        {
            "shot_index": 1,
            "sound": HOOK,
            "kind": "event",
            "start_seconds": 0.0,
            "duration_seconds": 1.0,
        },
    )
    facts_path.write_text(json.dumps(hooked))
    out = io.StringIO()
    result = run_finish(
        post_desk,
        sfx_render=render,
        bed_maker=fake_bed,
        facts_fetcher=lambda *a: None,
        stream=out,
    )
    assert "opening_sound_flat" not in out.getvalue()
    assert "on the opening line" not in out.getvalue()
    sfx = next(step for step in result.steps if step.step == "sfx")
    assert f"{HOOK} @0.00s {OPENING_SOUND_GAIN_DB:+.0f} dB" in sfx.detail
