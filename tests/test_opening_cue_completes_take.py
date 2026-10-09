"""A hand cue at the opening is the opening sound, and a take whose effects are on it is complete.

Live 15 s letterbox test (Three Payments Late, 6 Oct 2026): t1 is a locked-voice
take. The server laid its wok crash in the dialogue track; the one planned effect
left for the kit ("Noor's chrome fingers snap open") rendered silent twice, so the
sfx step failed and the take was ``SFX ✗`` / NOT DONE. The operator laid a hand
cue at 0 s (``--cue FILE@0@-4``) as the opening-sound advice says: the cue went
on, yet the take stayed NOT DONE, a "sits at 0.00s, on the opening line" warning
named the very cue the advice asked for, and ``join`` refused the take saying
"no music, SFX or mix" when only SFX was missing.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest
from conftest import make_take, make_tone, needs_ffmpeg
from test_post_finish import FACTS, TWO_LINES, fake_bed

from creation.post.finish import run_finish
from creation.post.finish_record import write_finish_record
from creation.post.hand import Placed
from creation.post.join import episode_parts, file_parts
from creation.post.sfx import SfxCue

LINES = [
    {"line_id": "l1", "cast_id": "cast_kenji", "start_s": 1.0, "end_s": 2.0},
    {"line_id": "l2", "cast_id": "cast_aya", "start_s": 3.2, "end_s": 4.0},
]


def _facts() -> dict:
    """Two planned effects: the server laid the door slam in the track; the glass is the kit's to lay."""

    facts = copy.deepcopy(FACTS)
    facts["take_facts"]["sfx_cues"].append(
        {"shot_index": 2, "sound": "a glass breaks", "kind": "event",
         "start_seconds": 4.2, "duration_seconds": 0.6}
    )  # fmt: skip
    facts["take_facts"]["soundtrack"] = {
        "mode": "target_audio", "reason": None, "track_url": "https://x/track.wav",
        "lines": LINES, "native_foley": False,
        "ambience": {"laid": True}, "music": {"laid": True},
        "sfx": {"cues": [{"shot_index": 2, "sound": "a door slams", "start_s": 3.0, "laid": True}]},
    }  # fmt: skip
    return facts


def silent_sfx(calls: list[str]):
    """Every effect renders silent (the server's "wrong shape: silent")."""

    def render(cue: SfxCue, target: Path) -> Path:
        calls.append(cue.sound)
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.0)

    return render


def _desk(post_desk: Path) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(_facts())
    )


@needs_ffmpeg
def test_a_hand_cue_at_the_opening_completes_a_take_whose_other_effects_are_in_the_track(
    post_desk: Path,
) -> None:
    _desk(post_desk)
    cue = make_tone(
        post_desk / "ep01" / "sfx" / "cue-a-sharp-hot-wok-v1.mp3", seconds=1.5, freq=500
    )
    (cue.with_suffix(".json")).write_text(
        json.dumps({"description": "a sharp hot wok sizzle"})
    )
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=silent_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
        cut_meter=lambda _take: (2.5,), cues=(Placed(cue, 0.0),), thumbnail=False, stream=out,
    )  # fmt: skip

    log = out.getvalue()
    assert result.complete, log
    sfx = next(s for s in result.steps if s.step == "sfx")
    assert sfx.status == "ran", sfx.detail
    assert "!! NOT LAID" in sfx.detail and "a glass breaks" in sfx.detail
    assert "already in the track" in sfx.detail
    assert "SFX ✓ (!! 1 planned cue(s) not laid)" in log
    assert "opening_sound_flat" not in log, "the hand cue at 0 s is the opening sound"
    assert "on the opening line" not in log, (
        "the opening sound is not named as a stray cue"
    )
    record = json.loads(
        next(
            (post_desk / "ep01" / "takes").glob("take-ep01-t1-finish-v*.json")
        ).read_text()
    )
    assert record["complete"] is True and "missing" not in record


@needs_ffmpeg
def test_an_effect_silent_after_its_re_make_is_left_out_with_a_warning_and_the_take_is_done(
    post_desk: Path,
) -> None:
    """Gallery L-20261008-20: the only planned effect came back silent and stopped the finish.

    It is now re-made once (the same sound) and, still silent, left out with a
    warning: the take finishes. (Before: ``SFX ✗`` / NOT DONE.)
    """

    _desk(post_desk)
    facts = _facts()
    facts["take_facts"]["soundtrack"]["sfx"] = {
        "cues": []
    }  # nothing laid by the server
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )
    out = io.StringIO()
    calls: list[str] = []

    result = run_finish(
        post_desk, sfx_render=silent_sfx(calls), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
        cut_meter=lambda _take: (2.5,), thumbnail=False, stream=out,
    )  # fmt: skip

    log = out.getvalue()
    assert result.complete, log
    assert result.sound_missing == ()
    # Each planned cue asked for twice (the render and one re-make of the SAME sound), never more.
    assert sorted(calls) == sorted(
        ["a door slams", "a door slams", "a glass breaks", "a glass breaks"]
    )
    sfx = next(s for s in result.steps if s.step == "sfx")
    assert sfx.status == "ran" and "!! NOT LAID 2 of 2 planned" in sfx.detail, (
        sfx.detail
    )
    assert "silent twice: re-made once, then left out" in sfx.detail
    assert "[sfx] !!" in log
    assert "opening_sound_flat" in log
    record = json.loads(
        next(
            (post_desk / "ep01" / "takes").glob("take-ep01-t1-finish-v*.json")
        ).read_text()
    )
    assert record["complete"] is True


@needs_ffmpeg
def test_a_render_the_server_could_not_do_still_stops_the_take(post_desk: Path) -> None:
    """Only an effect left out for its SOUND is dropped; a server that did not answer is retried by re-running."""

    _desk(post_desk)
    facts = _facts()
    facts["take_facts"]["soundtrack"]["sfx"] = {"cues": []}
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )

    def down(cue: SfxCue, target: Path) -> Path:
        raise RuntimeError("HTTP 503")

    result = run_finish(
        post_desk, sfx_render=down, bed_maker=fake_bed, facts_fetcher=lambda *a: None,
        cut_meter=lambda _take: (2.5,), thumbnail=False, stream=io.StringIO(),
    )  # fmt: skip

    assert not result.complete
    assert result.sound_missing == ("SFX",)


def test_join_names_what_an_unfinished_take_is_missing(post_desk: Path) -> None:
    takes = post_desk / "ep01" / "takes"
    final = takes / "take-ep01-t1-sokii-v1.mp4"
    final.write_bytes(b"x")
    write_finish_record(post_desk, episode=1, take_id="t1", complete=False, pre_bed=final, master=final,
                        final=final, bed=None, bed_db=-16.5, duck_db=None, missing=("SFX",))  # fmt: skip
    with pytest.raises(ValueError, match=r"t1 .*not done: no SFX"):
        episode_parts(post_desk, 1)
    with pytest.raises(ValueError, match=r"NOT DONE \(no SFX\)"):
        file_parts(post_desk, (final,))
    # An older record says nothing about what was missing: join says where to look.
    write_finish_record(post_desk, episode=1, take_id="t1", complete=False, pre_bed=final, master=final,
                        final=final, bed=None, bed_db=-16.5, duck_db=None)  # fmt: skip
    with pytest.raises(ValueError, match="Sound:"):
        file_parts(post_desk, (final,))
