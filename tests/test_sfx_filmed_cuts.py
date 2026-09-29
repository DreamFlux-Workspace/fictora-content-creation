"""The kit's SFX step follows the FILMED cuts, as the server's mix does since fictora-drama #487."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import make_take, make_tone, needs_ffmpeg

from creation.cli_produce import main as produce_main
from creation.post.finish import run_finish
from creation.post.sfx import (
    SfxCue,
    SfxPlan,
    filmed_shot_windows,
    follow_filmed_cuts,
    plan_from_take_facts,
    planned_shots,
)
from test_post_finish import FACTS, TWO_LINES, fake_bed

#: Hanakaze Sweets ep 2 t1: planned shot changes 3.80 / 7.50 / 11.20 s; the take cuts at 3.875 / 7.333 / 10.958 s
#: (and a 0.083 s board-frame flash at the head).
HANAKAZE = {
    "shots": [
        {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 3.8, "speaks": True},
        {"shot_index": 2, "start_seconds": 3.8, "end_seconds": 7.5, "speaks": False},
        {"shot_index": 3, "start_seconds": 7.5, "end_seconds": 11.2, "speaks": False},
        {"shot_index": 4, "start_seconds": 11.2, "end_seconds": 15.0, "speaks": True},
    ],
    "sfx_cues": [
        {"shot_index": 2, "sound": "wooden trays clattering", "kind": "event",
         "start_seconds": 3.8, "duration_seconds": 3.0},
        {"shot_index": 3, "sound": "one soft bite through sticky rice", "kind": "event",
         "start_seconds": 9.72, "duration_seconds": 1.0},
        {"shot_index": 3, "sound": "arcade murmur", "kind": "sustained",
         "start_seconds": 7.5, "duration_seconds": 3.7},
    ],
}  # fmt: skip
CUTS = (0.083, 3.875, 7.333, 10.958)


def test_each_planned_change_moves_to_the_nearest_cut_and_the_head_flash_is_ignored() -> (
    None
):
    filmed = filmed_shot_windows(planned_shots(HANAKAZE), CUTS, duration=15.1)

    assert filmed.moved == ((3.8, 3.875), (7.5, 7.333), (11.2, 10.958))
    assert filmed.windows == {
        1: (0.0, 3.875),
        2: (3.875, 7.333),
        3: (7.333, 10.958),
        4: (10.958, 15.0),
    }
    assert filmed.kept == ()


def test_cues_keep_their_fraction_of_the_shot_and_a_sustained_sound_ends_with_it() -> (
    None
):
    plan = plan_from_take_facts(HANAKAZE)
    filmed = filmed_shot_windows(planned_shots(HANAKAZE), CUTS, duration=15.1)

    moved = follow_filmed_cuts(plan, filmed)

    clatter, bite, murmur = moved.cues
    assert (clatter.start, clatter.seconds) == (3.875, 3.0)  # an event keeps its length
    # 9.72 s is 60% into 7.50-11.20; 60% into 7.333-10.958 is 9.508 s (the server's 9.51 s).
    assert bite.start == pytest.approx(9.508, abs=0.002) and bite.seconds == 1.0
    assert (murmur.start, murmur.seconds) == (7.333, pytest.approx(3.625, abs=0.002))
    assert moved.speech == ((0.0, 3.875), (10.958, 15.0))


def test_a_change_with_no_cut_near_it_stays_where_it_was_planned() -> None:
    shots = planned_shots(HANAKAZE)
    filmed = filmed_shot_windows(shots, (3.9,), duration=15.1)

    assert filmed.moved == ((3.8, 3.9),)
    assert filmed.kept == (7.5, 11.2)
    assert "no cut within 1 s, kept as planned: 7.50s, 11.20s" in filmed.one_line()
    # A cut is used once, and a far one is never taken.
    assert filmed_shot_windows(shots, (5.2,), duration=15.1).moved == ()


def test_a_cue_on_a_shot_the_facts_do_not_list_is_left_alone() -> None:
    plan = SfxPlan((SfxCue(9, "door", "event", 2.0, 1.0),), ())
    filmed = filmed_shot_windows(planned_shots(HANAKAZE), CUTS, duration=15.1)
    assert follow_filmed_cuts(plan, filmed).cues == plan.cues


def _desk_with_facts(post_desk: Path) -> Path:
    raw = make_take(
        post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES
    )
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    return raw


@needs_ffmpeg
def test_finish_lays_the_cue_on_the_filmed_shot(post_desk: Path) -> None:
    raw = _desk_with_facts(post_desk)
    measured: list[str] = []

    def cuts(take: Path) -> tuple[float, ...]:
        measured.append(take.name)
        return (2.9,)  # planned change at 2.5 s; the take cuts at 2.9 s

    result = run_finish(post_desk, sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
                        bed_maker=fake_bed, facts_fetcher=lambda *a: None, cut_meter=cuts,
                        stream=io.StringIO())  # fmt: skip

    sfx = next(s for s in result.steps if s.step == "sfx")
    # 3.0 s is 20% into 2.5-5.0; 20% into 2.9-5.0 is 3.32 s.
    assert "a door slams @3.32s" in sfx.detail, sfx.detail
    assert "shot changes on the filmed cuts: 2.50->2.90s" in sfx.detail
    assert measured == [raw.name]
    notes = (post_desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")
    assert "cues follow the filmed cuts measured on `take-ep01-t1-raw-v1.mp4`" in notes


@needs_ffmpeg
def test_a_freeze_output_is_measured_on_its_raw_take(post_desk: Path) -> None:
    raw = _desk_with_facts(post_desk)
    assert (
        produce_main(
            ["freeze", "--desk", str(post_desk), "--at", "1.0", "--hold", "0.4"]
        )
        == 0
    )
    measured: list[str] = []

    def cuts(take: Path) -> tuple[float, ...]:
        measured.append(take.name)
        return ()

    result = run_finish(post_desk, take_file=post_desk / "ep01" / "takes" / "take-ep01-t1-freeze-v1.mp4",
                        sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
                        bed_maker=fake_bed, facts_fetcher=lambda *a: None, cut_meter=cuts,
                        stream=io.StringIO())  # fmt: skip

    assert measured == [raw.name], (
        "a freeze hold ends in a jump that would read as a cut"
    )
    sfx = next(s for s in result.steps if s.step == "sfx")
    assert (
        "a door slams @3.00s" in sfx.detail and "kept as planned: 2.50s" in sfx.detail
    )


@needs_ffmpeg
def test_when_the_cuts_cannot_be_measured_the_plan_is_kept(post_desk: Path) -> None:
    _desk_with_facts(post_desk)

    def broken(take: Path) -> tuple[float, ...]:
        raise RuntimeError("ffmpeg cut trace failed")

    result = run_finish(post_desk, sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
                        bed_maker=fake_bed, facts_fetcher=lambda *a: None, cut_meter=broken,
                        stream=io.StringIO())  # fmt: skip

    sfx = next(s for s in result.steps if s.step == "sfx")
    assert sfx.status == "ran" and "a door slams @3.00s" in sfx.detail
    assert (
        "cues on the planned shots (cuts not measured: ffmpeg cut trace failed)"
        in sfx.detail
    )


def test_a_cut_at_either_end_of_the_take_is_not_a_shot_change() -> None:
    shots = planned_shots(
        {"shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 0.8},
            {"shot_index": 2, "start_seconds": 0.8, "end_seconds": 14.6},
            {"shot_index": 3, "start_seconds": 14.6, "end_seconds": 15.0},
        ]}
    )  # fmt: skip

    filmed = filmed_shot_windows(shots, (0.083, 14.95), duration=15.1)

    assert filmed.moved == () and filmed.kept == (0.8, 14.6)
