from __future__ import annotations

import io
from pathlib import Path

from conftest import set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.left_out import KIT_CANNOT_ADD_BACK, left_out_lines
from fake_api import FakeApi
from test_rewrite_command import _rewritten, _serve_notes

BOX = "One status box ('Trust 0/10') came out wrong, so I left it out of this episode."
SORA = (
    "I could only bring in two new characters at once, so Sora isn't in this episode."
)


def _left_out(episode_id: str, line: str, reason: str = "") -> dict[str, str]:
    return {
        "left_out_id": "lo_0123456789",
        "kind": "system_panel",
        "episode_id": episode_id,
        "quote": "Trust 0/10",
        "line": line,
        "reason": reason,
    }


def test_only_this_episodes_lines_print_as_sent_then_the_kit_says_it_cannot_add_back() -> (
    None
):
    spine = {
        "left_out": [
            _left_out(
                "ep_02", BOX, reason="the box named a colour the look has no word for"
            ),
            _left_out("ep_02", SORA),
            _left_out("ep_02", "   "),
            _left_out("episode_01", "Not this one."),
            "not a record",
        ]
    }

    assert left_out_lines(spine, "ep_02") == [BOX, SORA, KIT_CANNOT_ADD_BACK]


LIMIT = "I left 'Level 2' out of this episode because a take can show only two status boxes."


def _note(episode_id: str, line: str | None) -> dict[str, object]:
    note: dict[str, object] = {
        "left_out_id": "lo_9876543210",
        "reason": "take_full",
        "episode_id": episode_id,
        "quote": "Level 2",
        "told": False,
    }
    if line is not None:
        note["line"] = line
    return note


def test_a_left_out_note_prints_its_own_line_with_no_add_back_line() -> None:
    spine = {
        "left_out_notes": [
            _note("ep_02", LIMIT),
            _note("ep_02", None),
            _note("episode_01", "Not this one."),
        ]
    }

    assert left_out_lines(spine, "ep_02") == [LIMIT]


def test_notes_print_before_offered_records_and_their_add_back_line() -> None:
    spine = {
        "left_out": [_left_out("ep_02", BOX)],
        "left_out_notes": [_note("ep_02", LIMIT)],
    }

    assert left_out_lines(spine, "ep_02") == [LIMIT, BOX, KIT_CANNOT_ADD_BACK]


def test_nothing_left_out_or_an_older_server_prints_nothing() -> None:
    assert left_out_lines({}, "ep_02") == []
    assert left_out_lines({"left_out": [_left_out("episode_01", BOX)]}, "ep_02") == []


def test_a_rewrite_prints_what_was_left_out(desk: Path, api: FakeApi) -> None:
    _serve_notes(api)
    _rewritten(api, "job_rw_1")
    api.spine_doc["left_out"] = [_left_out("ep_02", SORA)]
    out = io.StringIO()

    ec.run_rewrite(desk, episode=2, line="Ren swaps the sugar.", out=out)

    assert out.getvalue().count(SORA) == 1


def test_the_draft_step_prints_and_notes_what_was_left_out(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
        "left_out": [_left_out("episode_01", BOX)],
    }
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }
    api.jobs["job_plan"] = {"status": "completed", "result": {}}
    set_phase(desk, "new", spine_id=None)

    result = orchestrate.run_step(desk)

    assert result.phase == "ready_cast_enrol"
    assert result.message.count(BOX) == 1
    assert BOX in (desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")
