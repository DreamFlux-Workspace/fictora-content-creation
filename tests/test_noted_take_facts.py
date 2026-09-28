"""Drop and level sound notes reach ``finish`` through the take facts (fictora-drama #475).

The server applies the story's drop and level notes when take facts are read
with ``spine_id``: a dropped cue leaves ``sfx_cues`` for ``sfx_dropped_cues``
(``dropped_by_note_id``), a levelled cue carries ``gain_offset_db`` and
``note_ids``. The kit mixes that plan as it is (default -8 dB + offset, dropped
cues never laid) and never applies a note itself. Facts from an older server
(no such fields) are laid exactly as before.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from conftest import make_take, needs_ffmpeg

from creation import episode_commands as ec
from creation.post.finish import run_finish
from creation.post.sfx import (
    SFX_GAIN_DB,
    SfxCue,
    apply_adjustments,
    parse_adjustment,
    plan_from_take_facts,
)
from creation.post.take_facts import sfx_plan_changes, sfx_plan_lines
from fake_api import FakeApi

DOOR = {
    "shot_index": 2,
    "sound": "a door slams",
    "kind": "event",
    "start_seconds": 3.0,
    "duration_seconds": 1.0,
    "source": "sound_line",
}
PURR = {
    "shot_index": 2,
    "sound": "a cat purring",
    "kind": "sustained",
    "start_seconds": 2.6,
    "duration_seconds": 2.0,
    "source": "sound_line",
}
QUIET_DOOR = {**DOOR, "gain_offset_db": -6.0, "note_ids": ["sn_2"]}
DROPPED_PURR = {**PURR, "dropped_by_note_id": "sn_1"}


def facts(*cues: dict, dropped: tuple[dict, ...] = ()) -> dict:
    """``take_facts`` as the server answers them (``sfx_dropped_cues`` only when something is dropped)."""

    body = {
        "job_id": "job_take_1",
        "endpoint_id": "fal-ai/minimax/hailuo-03/image-to-video",
        "media_kind": "video",
        "shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5, "speaks": True},
            {"shot_index": 2, "start_seconds": 2.5, "end_seconds": 5.0, "speaks": False},
        ],
        "sfx_cues": list(cues),
    }  # fmt: skip
    if dropped:
        body["sfx_dropped_cues"] = list(dropped)
    return body


# --- the plan finish lays ---------------------------------------------------------------------------


def test_a_levelled_cue_is_mixed_at_the_default_plus_its_offset_with_its_notes() -> (
    None
):
    plan = plan_from_take_facts({"take_facts": facts(QUIET_DOOR)})

    (door,) = plan.cues
    assert door.gain_db == SFX_GAIN_DB - 6.0 == -14.0
    assert door.note_ids == ("sn_2",)


def test_an_offset_is_clamped_to_the_layer_range() -> None:
    loud = plan_from_take_facts(facts({**DOOR, "gain_offset_db": 40.0})).cues[0]
    quiet = plan_from_take_facts(facts({**DOOR, "gain_offset_db": -60.0})).cues[0]
    assert (loud.gain_db, quiet.gain_db) == (0.0, -30.0)


def test_a_dropped_cue_is_never_laid_even_if_it_also_sits_in_sfx_cues() -> None:
    plan = plan_from_take_facts(facts(DOOR, dropped=(DROPPED_PURR,)))
    assert [cue.sound for cue in plan.cues] == ["a door slams"]
    assert plan.dropped == ("a cat purring (note sn_1)",)

    both = plan_from_take_facts(facts(DOOR, PURR, dropped=(DROPPED_PURR,)))
    assert [cue.sound for cue in both.cues] == ["a door slams"]


def test_facts_from_an_older_server_lay_every_cue_at_the_default_level() -> None:
    plan = plan_from_take_facts({"take_facts": facts(DOOR, PURR)})

    assert [(cue.sound, cue.gain_db, cue.note_ids) for cue in plan.cues] == [
        ("a door slams", SFX_GAIN_DB, ()),
        ("a cat purring", SFX_GAIN_DB, ()),
    ]
    assert plan.dropped == ()


def test_sfx_adjust_moves_a_noted_cue_on_top_of_the_note() -> None:
    (door,) = plan_from_take_facts(facts(QUIET_DOOR)).cues
    (moved,) = apply_adjustments((door,), (parse_adjustment("door=+4"),))
    assert moved.gain_db == -10.0
    assert isinstance(moved, SfxCue) and moved.note_ids == ("sn_2",)


# --- take-facts printout --------------------------------------------------------------------------


def test_plan_changes_show_a_level_move_and_a_drop_with_their_note_ids() -> None:
    changes = sfx_plan_changes(
        facts(DOOR, PURR), facts(QUIET_DOOR, dropped=(DROPPED_PURR,))
    )

    assert changes == [
        "~ shot 2 at 3.00s for 1.00s: a door slams (event, sound line): "
        "level -8 dB -> -14 dB (-6 dB by note sn_2)",
        "- shot 2 at 2.60s for 2.00s: a cat purring (sustained, sound line): dropped by note sn_1",
    ]
    assert sfx_plan_changes(facts(QUIET_DOOR), facts(QUIET_DOOR)) == []
    back = sfx_plan_changes(facts(QUIET_DOOR), facts(DOOR))
    assert back == [
        "~ shot 2 at 3.00s for 1.00s: a door slams (event, sound line): "
        "level -14 dB -> -8 dB (default level: no note moves it now)"
    ]


def test_saved_plan_lines_show_levels_and_drops() -> None:
    assert sfx_plan_lines(facts(QUIET_DOOR, dropped=(DROPPED_PURR,))) == [
        "shot 2 at 3.00s for 1.00s: a door slams (event, sound line) at -14 dB (-6 dB by note sn_2)",
        "dropped: shot 2 at 2.60s for 2.00s: a cat purring (sustained, sound line) by note sn_1",
    ]
    assert sfx_plan_lines(facts(DOOR)) == [
        "shot 2 at 3.00s for 1.00s: a door slams (event, sound line)"
    ]


def test_refresh_fetches_with_the_spine_and_prints_the_level_change_and_the_drop(
    desk: Path, api: FakeApi
) -> None:
    api_dir = desk / "ep01" / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "17_raw_scene_clips.json").write_text(
        json.dumps({"clips": [{"episode_id": "episode_01", "set_index": 1,
                               "job_id": "job_take_1", "url": "https://r2.example/t1.mp4"}]})
    )  # fmt: skip
    (api_dir / "take-facts-ep01-t1-v1.json").write_text(json.dumps(facts(DOOR, PURR)))
    api.spine_doc["sound_notes"] = [
        {"note_id": "sn_1", "text": "no purring"},
        {"note_id": "sn_2", "text": "the door slam is too loud"},
    ]
    api.routes[("GET", "/v1/jobs/job_take_1/take-facts")] = {
        "take_facts": facts(QUIET_DOOR, dropped=(DROPPED_PURR,))
    }
    out = io.StringIO()

    ec.run_take_facts(desk, episode=1, take_id="t1", refresh=True, out=out)

    got = [p for m, p, _, _ in api.calls if m == "GET" and "take-facts" in p]
    assert got == ["/v1/jobs/job_take_1/take-facts?spine_id=sp1"]
    text = out.getvalue()
    assert "level -8 dB -> -14 dB (-6 dB by note sn_2)" in text
    assert "a cat purring (sustained, sound line): dropped by note sn_1" in text


# --- finish end to end -----------------------------------------------------------------------------


@needs_ffmpeg
def test_finish_lays_the_noted_level_and_never_renders_a_dropped_cue(
    post_desk: Path,
) -> None:
    from test_post_finish import TWO_LINES, fake_bed, fake_sfx

    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts(QUIET_DOOR, dropped=(DROPPED_PURR,)))
    )
    rendered: list[str] = []
    out = io.StringIO()

    result = run_finish(post_desk, sfx_render=fake_sfx(rendered), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, colour=False, stream=out)  # fmt: skip

    assert rendered == ["a door slams"], "a dropped cue is never rendered or laid"
    sfx = next(s for s in result.steps if s.step == "sfx")
    assert "a door slams @3.00s -14 dB (note sn_2)" in sfx.detail, sfx.detail
    assert "dropped by sound notes: a cat purring (note sn_1)" in sfx.detail
    assert "--sfx-adjust" not in out.getvalue()


@needs_ffmpeg
def test_finish_on_older_facts_lays_every_cue_at_minus_8(post_desk: Path) -> None:
    from test_post_finish import TWO_LINES, fake_bed, fake_sfx

    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts(DOOR))
    )
    rendered: list[str] = []

    result = run_finish(post_desk, sfx_render=fake_sfx(rendered), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, colour=False, stream=io.StringIO())  # fmt: skip

    sfx = next(s for s in result.steps if s.step == "sfx")
    assert rendered == ["a door slams"]
    assert "a door slams @3.00s -8 dB" in sfx.detail and "note" not in sfx.detail
