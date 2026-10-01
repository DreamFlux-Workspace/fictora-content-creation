"""Founder decisions of 1 Oct 2026 on the desk: locked lines, silent episodes, new characters mid-season."""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.brief_lines import brief_vs_spine_lines
from creation.new_cast import cast_owing_pictures, newcomers
from creation.ops.state import load_series
from creation.production_state import load_production
from creation.stranded_voice import voices_left_without_lines
from fake_api import FakeApi

# --- Locked lines: spoken locked, subtitles may adapt, a spoken change is a bug ------------------

FATED = """# Brief — Fated in the Rain

## Lines

Keep these lines exactly.

| Speaker | Original | Translation |
| --- | --- | --- |
| Hana | 비가 와요. | It's raining. |
| Ren | 우산 없어요? | No umbrella? |
"""


def _korean(*, spoken: tuple[str, str], subtitles: tuple[str, str]) -> dict[str, Any]:
    """The shape Fated in the Rain came back in: script kept, performed line and subtitle per line."""

    return {
        "spoken_language": "ko-KR",
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
        "cast": [{"cast_id": "cast_hana", "name": "Hana"}, {"cast_id": "cast_ren", "name": "Ren"}],
        "beats": [
            {"episode_id": "episode_01", "ordinal": 1, "dialogue_lines": [
                {"line_id": "l1", "cast_id": "cast_hana", "text": "비가 와요.", "spoken_text": spoken[0],
                 "subtitle_text": subtitles[0]}]},
            {"episode_id": "episode_01", "ordinal": 2, "dialogue_lines": [
                {"line_id": "l2", "cast_id": "cast_ren", "text": "우산 없어요?", "spoken_text": spoken[1],
                 "subtitle_text": subtitles[1], "brief_subtitle_text": "No umbrella?"}]},
        ],
    }  # fmt: skip


def test_fated_in_the_rain_a_rewritten_performed_line_is_flagged_as_a_bug() -> None:
    """The report said "kept 5, rewritten 0" while every performed line had changed."""

    text = "\n".join(
        brief_vs_spine_lines(
            FATED,
            _korean(
                spoken=("비가 오네요.", "우산 없어요?"),
                subtitles=("It is raining.", "No umbrella?"),
            ),
            episode=1,
        )
    )

    assert "spoken: 1 locked, 1 CHANGED; subtitles: 1 adapted" in text
    assert (
        '!! BUG (the server pins a locked line): the spoken line changed  brief "비가 와요."  performed "비가 오네요."'
        in text
    )
    assert '--line l1 --spoken "비가 와요."' in text
    assert (
        'subtitle adapted  [l1]  brief "It\'s raining."  ->  shown "It is raining."'
        in text
    )


def test_pinned_spoken_lines_read_as_locked_and_only_subtitle_changes_are_listed() -> (
    None
):
    text = "\n".join(
        brief_vs_spine_lines(
            FATED,
            _korean(
                spoken=("비가 와요.", "우산 없어요?"),
                subtitles=("It's raining.", "You have no umbrella?"),
            ),
            episode=1,
        )
    )

    assert "spoken: 2 locked; subtitles: 1 adapted" in text
    assert "BUG" not in text and "CHANGED" not in text
    assert (
        'subtitle adapted  [l2]  brief "No umbrella?"  ->  shown "You have no umbrella?"'
        in text
    )


def test_an_unlocked_brief_reports_spoken_lines_as_written_without_crying_bug() -> None:
    unlocked = FATED.replace("Keep these lines exactly.\n", "")
    text = "\n".join(
        brief_vs_spine_lines(
            unlocked,
            _korean(spoken=("비가 오네요.", "우산 없어요?"), subtitles=("a", "b")),
            episode=1,
        )
    )

    assert "spoken: 1 as written, 1 CHANGED" in text and "BUG" not in text


# --- Locked lines out of bounds: the desk pauses for the creator ---------------------------------

REFUSAL = (
    "locked_lines_out_of_bounds at scene_prompt: Your brief locks lines word for word, and they don't fit this "
    'episode, so nothing was written. We never shorten, split, merge or drop a locked line. Line 3 (Ren: "I '
    'waited at this bus stop every single night for three long years for you.") is 15 words, about 6.0 s to '
    "say; one beat holds about 4.9 s (~12 words) here. Shorten it or move part of it to the next episode."
)


def test_locked_lines_that_do_not_fit_pause_the_desk_for_the_creator(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }
    api.jobs["job_plan"] = {
        "status": "failed",
        "error": {
            "code": "authoring_validation_failed",
            "message": REFUSAL,
            "retryable": False,
        },
    }
    set_phase(desk, "new", spine_id=None)

    with pytest.raises(RuntimeError) as paused:
        orchestrate.run_step(desk)

    text = str(paused.value)
    assert text.startswith("PAUSED for the creator")
    assert "Line 3 (Ren:" in text and "~12 words" in text and "next episode" in text
    assert "fictora-produce bind" in text
    assert load_production(desk).phase == "new", (
        "not a failure: the edited brief drafts next"
    )


# --- Silent episodes: a staged character is never a stranded voice -------------------------------


def _sighted(*, staged: bool) -> dict[str, Any]:
    """Sighted ep 1: an invented off-screen line on beat 1; an older server flags the creature voice-only."""

    return {
        "cast": [
            {"cast_id": "cast_mara", "name": "Mara", "visual_brief": {"x": 1}},
            {"cast_id": "cast_creature", "name": "The creature", "voice_only": True},
        ],
        "frames": [],
        "beats": [
            {"beat_id": "b1", "ordinal": 1, "motion_direction": {"subject_cast_id": "cast_mara"},
             "dialogue_lines": [{"line_id": "line_episode_01_01", "cast_id": "cast_creature",
                                 "text": "A low clicking from the dark.", "off_screen": True}]},
            {"beat_id": "b2", "ordinal": 2,
             "motion_direction": {"subject_cast_id": "cast_creature" if staged else "cast_mara"},
             "dialogue_lines": []},
        ],
    }  # fmt: skip


def test_removing_the_invented_line_of_a_staged_creature_is_not_a_stranded_voice() -> (
    None
):
    assert (
        voices_left_without_lines(_sighted(staged=True), removed=["line_episode_01_01"])
        == []
    )


def test_a_voice_no_beat_stages_is_still_guarded() -> None:
    assert voices_left_without_lines(
        _sighted(staged=False), removed=["line_episode_01_01"]
    ) == [("cast_creature", "The creature")]


# --- New characters mid-season: said after author, plates before boards --------------------------


def _with_newcomer(spine: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
    after = copy.deepcopy(spine)
    after["cast"].append(
        {"cast_id": "cast_kai", "name": "Kai", "intro_episode_ordinal": 2}
    )
    after["beats"][1]["dialogue_lines"][0]["cast_id"] = "cast_kai"
    if approved:
        after["media_assets"].append(
            {"relation_type": "cast_card", "relation_id": "cast_kai", "url": "https://r2.example/kai.png",
             "review_state": "approved"}
        )  # fmt: skip
    return after


def test_author_says_who_is_new_and_that_their_picture_comes_before_boards(
    desk: Path, api: FakeApi
) -> None:
    before = copy.deepcopy(api.spine_doc)

    def author(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        api.spine_doc = _with_newcomer(before)
        return {"extension_job_id": "job_ext_2"}

    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = author
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    out = io.StringIO()

    ec.run_author(desk, episode=2, out=out)

    assert (
        "new character added: Kai (cast_kai) — approve their picture before boards"
        in out.getvalue()
    )
    assert newcomers(before, api.spine_doc) == [("cast_kai", "Kai")]


def test_the_script_yes_routes_a_newcomer_through_plates_then_on_to_boards(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    ec.run_author(desk, episode=2, out=io.StringIO())
    api.spine_doc = _with_newcomer(api.spine_doc)
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/approve")] = {
        "spine_version": "v6"
    }
    assert cast_owing_pictures(api.spine_doc, episode=2) == [("cast_kai", "Kai")]

    scripted = orchestrate.approve_gate(desk, gate="script")

    assert load_production(desk).phase == "ready_cast_enrol"
    assert "New character(s) Kai need a picture before boards" in scripted.message
    assert load_series(desk).episodes[1].script.status == "approved"

    set_phase(desk, "wait_plates")
    api.routes[("POST", "/v1/spines/sp1/cast/approve")] = {"spine_version": "v7"}
    plated = orchestrate.approve_gate(desk, gate="plates")

    assert load_production(desk).phase == "ready_boards_enrol"
    assert "Next: fictora-produce step (boards)" in plated.message
    assert api.posted("/v1/spines/sp1/cast/approve")


def test_an_approved_newcomer_and_episode_one_cast_owe_no_picture(api: FakeApi) -> None:
    assert (
        cast_owing_pictures(_with_newcomer(api.spine_doc, approved=True), episode=2)
        == []
    )
    assert cast_owing_pictures(api.spine_doc, episode=2) == []
