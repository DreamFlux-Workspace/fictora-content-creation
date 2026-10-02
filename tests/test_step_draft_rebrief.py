"""An edited brief after a creator pause is a new draft; the same brief never pays twice (L-20261002).

Live repro (2 Oct, desk 2026-10-02-bori-got-out): the draft paused with
``locked_lines_out_of_bounds``; the kit said to ``bind`` the edited brief and
``step`` again; that step adopted the old plan job under the fixed key
``<prefix>-ep01-draft`` and replayed the old pause. The new brief was never sent.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import orchestrate
from creation.harness import stages_gated
from creation.production_state import load_production, save_production
from creation.story_setup import run_brief_edit
from fake_api import FakeApi

DRAFTS = "/v1/prompt-video-authoring-drafts"
UNIT = orchestrate.draft_unit(1)

REFUSAL = (
    "locked_lines_out_of_bounds at scene_prompt: Your brief locks lines word for word, and they don't fit this "
    'episode, so nothing was written. Line 2 (Bori: "I got out of the yard and ran all the way down to the '
    'river and back before anyone saw me.") is 22 words; one beat holds about 12 words here.'
)
PAUSED_PLAN = {
    "status": "failed",
    "error": {
        "code": "authoring_validation_failed",
        "message": REFUSAL,
        "retryable": False,
    },
}


@pytest.fixture
def paused_draft(desk: Path, api: FakeApi) -> FakeApi:
    """A desk at ``new`` whose first draft pauses on locked lines; every later draft is a new job."""

    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
    }
    jobs = iter(range(1, 10))

    def draft(_m: str, _p: str, _b: object) -> dict[str, Any]:
        n = next(jobs)
        return {"plan_job_id": f"job_plan_{n}", "spine_id": "sp1"}

    api.routes[("POST", DRAFTS)] = draft
    api.jobs["job_plan_1"] = PAUSED_PLAN
    api.jobs["job_plan_2"] = {"status": "completed"}
    set_phase(desk, "new", spine_id=None)
    return api


def _draft_keys(api: FakeApi) -> list[str | None]:
    return [
        key for method, path, _, key in api.calls if (method, path) == ("POST", DRAFTS)
    ]


def _phases(api: FakeApi) -> list[str]:
    return [phase for phase, _ in api.events]


def _pause(desk: Path) -> str:
    with pytest.raises(RuntimeError) as paused:
        orchestrate.run_step(desk)
    text = str(paused.value)
    assert text.startswith("PAUSED for the creator")
    return text


def test_an_edited_brief_bound_after_a_pause_is_drafted_under_a_new_key(
    desk: Path, paused_draft: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api = paused_draft
    _pause(desk)
    capsys.readouterr()

    orchestrate.bind_desk(
        desk,
        prompt="Bori got out. Lines: Bori: I ran to the river and back.",
        preset_id="modern-romance",
        preset=("modern-romance", "2"),
    )
    assert "drafts the new brief" in capsys.readouterr().err

    orchestrate.run_step(desk)

    first, second = _draft_keys(api)
    assert first != second, "the edited brief is a new key, so the server drafts it"
    assert "job_plan_2" in [job for job, _ in api.polled]
    assert api.posted(DRAFTS)[1]["prompt"].startswith("Bori got out.")
    assert "draft_dropped" in _phases(api) and "draft_adopted" not in _phases(api)
    state = load_production(desk)
    assert state.phase == "ready_cast_enrol" and UNIT not in state.pending


def test_the_same_brief_after_a_pause_shows_the_pause_again_and_sends_nothing(
    desk: Path, paused_draft: FakeApi
) -> None:
    api = paused_draft
    first = _pause(desk)
    assert load_production(desk).pending[UNIT]["paused"] == "locked_lines_out_of_bounds"

    again = _pause(desk)

    assert len(_draft_keys(api)) == 1, "no second paid draft for the same brief"
    assert [job for job, _ in api.polled] == ["job_plan_1", "job_plan_1"]
    assert "draft_paused_again" in _phases(api)
    assert again == first
    assert "the edited brief is a new draft" in again


def test_brief_edit_before_the_draft_is_a_changed_brief_too(
    desk: Path, paused_draft: FakeApi
) -> None:
    api = paused_draft
    _pause(desk)

    assert run_brief_edit(
        desk, edit="Bori got out. Lines: Bori: I ran to the river.", out=io.StringIO()
    )
    orchestrate.run_step(desk)

    first, second = _draft_keys(api)
    assert first != second
    assert load_production(desk).phase == "ready_cast_enrol"


def test_a_saved_draft_from_before_brief_hashes_is_not_adopted(
    desk: Path, paused_draft: FakeApi
) -> None:
    """The live desk's record: the fixed #105 prefix, no brief hash, the old paused job."""

    api = paused_draft
    api.jobs["job_plan_1"] = {"status": "completed"}
    state = load_production(desk)
    legacy = f"{state.idempotency_prefix}-ep01"
    state.pending[UNIT] = {
        "key_prefix": legacy,
        "key": f"{legacy}-draft",
        "attempt": 0,
        "plan_job_id": "job_old",
        "spine_id": "sp_old",
    }
    save_production(desk, state)

    orchestrate.run_step(desk)

    assert len(_draft_keys(api)) == 1
    assert "job_old" not in [job for job, _ in api.polled]
    dropped = [p for phase, p in api.events if phase == "draft_dropped"]
    assert dropped and dropped[0]["job_id"] == "job_old"


def test_a_record_for_another_brief_is_not_adoptable_even_under_the_same_prefix() -> (
    None
):
    record = {
        "key_prefix": "desk-ep01-babc",
        "brief_hash": "0123456789",
        "attempt": 0,
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }

    assert (
        stages_gated.adoptable_draft(
            record, key_prefix="desk-ep01-babc", brief_hash="9876543210"
        )
        is None
    )
    assert stages_gated.adoptable_draft(
        record, key_prefix="desk-ep01-babc", brief_hash="0123456789"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"prompt": "another brief"},
        {"spoken_language": "ja"},
        {"band": "30s"},
        {"preset_version": "3"},
        {"cut_tempo": "slow_burn"},
        {"video_lane": "other-lane"},
    ],
)
def test_every_draft_setting_is_part_of_the_brief_hash(change: dict[str, str]) -> None:
    base = {"prompt": "a brief", "preset_id": "x", "preset_version": "2"}
    same = stages_gated.draft_brief_hash(stages_gated.draft_request_body(**base))

    assert same == stages_gated.draft_brief_hash(
        stages_gated.draft_request_body(**base)
    )
    assert same != stages_gated.draft_brief_hash(
        stages_gated.draft_request_body(**{**base, **change})
    )
