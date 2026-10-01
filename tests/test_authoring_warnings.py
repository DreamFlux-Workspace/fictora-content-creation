"""Server authoring warnings (fictora-drama #538) are printed as nudges and never stop a command."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from conftest import set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.authoring_warnings import (
    HEADER,
    authoring_warnings,
    introduced,
    warning_line,
    warning_lines,
)
from creation.cli_produce import main as produce_main
from creation.production_state import load_production
from fake_api import FakeApi, spine_fixture

SCHEMA = "fictora.drama-authoring-warning.v1"


def _line_long(episode_id: str = "ep_02", words: int = 14) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "path": f"beats[beat_{episode_id}_01].dialogue_lines[line_{episode_id}_01].text",
        "code": "line_long",
        "message": f"This line runs {words} words; 10 or fewer reads best.",
        "words": words,
        "target": 10,
        "episode_id": episode_id,
        "beat_id": f"beat_{episode_id}_01",
        "line_id": f"line_{episode_id}_01",
    }


def _take_over(episode_id: str = "ep_02", take: int = 1) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "path": f"episodes[{episode_id}].takes[{take}]",
        "code": "take_words_over_target",
        "message": "This take speaks 26 words; about 22 fits the take.",
        "words": 26,
        "target": 22,
        "episode_id": episode_id,
        "take": take,
    }


def _notes(desk: Path, episode: int) -> str:
    return (desk / f"ep{episode:02d}" / "run-notes.md").read_text(encoding="utf-8")


# --- Plain words ------------------------------------------------------------------------------


def test_each_code_reads_in_plain_words_and_an_unknown_code_shows_its_message() -> None:
    spine = spine_fixture()
    assert warning_line(_line_long(), spine) == (
        "note: beat 1 line runs 14 words (10 recommended) — This line runs 14 words; 10 or fewer reads best."
    )
    assert warning_line(_take_over(take=2)).startswith(
        "note: take 2 speaks 26 words (22 recommended) — "
    )
    first = {
        "code": "first_line_long",
        "message": "Long opener.",
        "words": 12,
        "target": 10,
    }
    assert (
        warning_line(first)
        == "note: the first line runs 12 words (10 recommended) — Long opener."
    )
    streak = {
        "code": "story_machine_streak",
        "message": "Three beats in a row only explain the plot.",
    }
    assert warning_line(streak) == "note: Three beats in a row only explain the plot."


def test_warnings_are_grouped_per_episode_and_take_under_one_header() -> None:
    spine = spine_fixture(episodes=3)
    lines = warning_lines(
        [_take_over("ep_03", 2), _line_long("ep_02"), _take_over("ep_02", 1)], spine
    )
    assert lines[0] == f"ep03 {HEADER}:"
    assert lines[2] == f"ep02 {HEADER}:"
    assert lines[3].startswith("  note: beat 1 line runs 14 words")
    assert lines[4].startswith("  note: take 1 speaks 26 words")


def test_an_absent_or_empty_field_reads_as_no_warnings() -> None:
    assert authoring_warnings({"status": "completed"}) == []
    assert authoring_warnings({"status": "completed", "result": {}}) == []
    assert authoring_warnings(None) == []
    assert warning_lines([]) == []


def test_only_warnings_the_edit_introduced_count_compared_by_path_code_and_words() -> (
    None
):
    old = _line_long(words=14)
    assert introduced([old], [old]) == []
    assert introduced([old], [_line_long(words=12)]) == [_line_long(words=12)]


# --- Job result: author ----------------------------------------------------------------------


def _author(desk: Path, api: FakeApi, terminal: dict[str, Any]) -> str:
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2",
        "status": "queued",
    }
    api.jobs["job_ext_2"] = terminal
    set_phase(desk, "complete", estimate_usd=1.2, last_delivery_url="https://x")
    out = io.StringIO()
    ec.run_author(desk, episode=2, out=out)
    return out.getvalue()


def test_author_prints_the_jobs_warnings_as_nudges_and_notes_them(
    desk: Path, api: FakeApi
) -> None:
    text = _author(
        desk,
        api,
        {
            "status": "completed",
            "result": {"authoring_warnings": [_line_long(), _take_over()]},
        },
    )

    assert f"ep02 {HEADER}:" in text
    assert "note: beat 1 line runs 14 words (10 recommended)" in text
    assert "note: take 1 speaks 26 words (22 recommended)" in text
    assert "beat 1 line runs 14 words" in _notes(desk, 2)
    assert load_production(desk).phase == "wait_script", (
        "a warning never holds the desk"
    )


def test_author_on_an_older_server_prints_no_notes(desk: Path, api: FakeApi) -> None:
    assert HEADER not in _author(desk, api, {"status": "completed"})


# --- Job result: the draft -------------------------------------------------------------------


def test_the_draft_step_says_the_plan_jobs_warnings(desk: Path, api: FakeApi) -> None:
    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
    }
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }
    first = {
        "schema_version": SCHEMA,
        "path": "episodes[episode_01].first_line",
        "code": "first_line_long",
        "message": "The first line runs 12 words.",
        "words": 12,
        "target": 10,
        "episode_id": "episode_01",
    }
    api.jobs["job_plan"] = {
        "status": "completed",
        "result": {"authoring_warnings": [first]},
    }
    set_phase(desk, "new", spine_id=None)

    result = orchestrate.run_step(desk)

    assert result.phase == "ready_cast_enrol"
    assert "note: the first line runs 12 words (10 recommended)" in result.message
    assert "the first line runs 12 words" in _notes(desk, 1)


# --- PATCH response ----------------------------------------------------------------------------


def test_a_patch_prints_only_the_warnings_it_introduced(
    desk: Path, api: FakeApi
) -> None:
    # Episode 2's long take was there before the edit and is still there after it.
    standing = _take_over("ep_02")
    api.spine_doc = {
        **spine_fixture(approved=False),
        "authoring_warnings": [_line_long("episode_01", words=13), standing],
    }
    fresh = _line_long("episode_01", words=15)
    api.routes[("PATCH", "/v1/spines/sp1")] = {
        **spine_fixture(approved=False),
        "spine_version": "v6",
        "authoring_warnings": [fresh, standing],
    }
    out = io.StringIO()

    ec.run_line(
        desk,
        episode=1,
        line="1",
        text="We are closed tonight and every night after this one, sorry, Ren, go.",
        out=out,
    )

    text = out.getvalue()
    assert f"ep01 {HEADER}:" in text
    assert "beat 1 line runs 15 words (10 recommended)" in text
    assert "26 words" not in text, "a warning the story already had is not said again"
    assert HEADER.join(["ep02 ", ":"]) not in text
    assert "beat 1 line runs 15 words" in _notes(desk, 1)


def test_a_patch_without_the_field_prints_nothing_and_the_cli_exits_zero(
    desk: Path, api: FakeApi, capsys: Any
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.routes[("PATCH", "/v1/spines/sp1")] = {"spine_version": "v6"}

    code = produce_main(
        [
            "line",
            "--desk",
            str(desk),
            "--episode",
            "1",
            "--line",
            "1",
            "--text",
            "Closed.",
        ]
    )

    assert code == 0
    assert HEADER not in capsys.readouterr().out


def test_the_cli_exits_zero_with_warnings(
    desk: Path, api: FakeApi, capsys: Any
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.routes[("PATCH", "/v1/spines/sp1")] = {
        "spine_version": "v6",
        "authoring_warnings": [_line_long("episode_01", words=16)],
    }

    code = produce_main(
        [
            "line",
            "--desk",
            str(desk),
            "--episode",
            "1",
            "--line",
            "1",
            "--text",
            "x " * 16,
        ]
    )

    assert code == 0
    assert "runs 16 words (10 recommended)" in capsys.readouterr().out


# --- Cascade preview ---------------------------------------------------------------------------


def _preview(**extra: Any) -> dict[str, Any]:
    return {
        "proposal_id": "prop_1",
        "items": [
            {
                "item_id": "i_frames",
                "recipe_id": "frames_rewrite",
                "estimated_tier": "text",
            }
        ],
        **extra,
    }


def test_cascade_preview_prints_the_structured_list_once_and_other_strings_as_before(
    desk: Path, api: FakeApi
) -> None:
    warning = _line_long("episode_01", words=14)
    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = _preview(
        warnings=["Board t1 will be marked stale.", warning["message"]],
        authoring_warnings=[warning],
    )
    out = io.StringIO()

    ec.run_line(
        desk,
        episode=1,
        line="1",
        text="Closed, closed, closed.",
        preview_only=True,
        out=out,
    )

    text = out.getvalue()
    assert "warning: Board t1 will be marked stale." in text
    assert text.count(warning["message"]) == 1, (
        "the structured list wins over the copied string"
    )
    assert "note: beat 1 line runs 14 words (10 recommended)" in text
    assert "(preview only: nothing was changed)" in text
    assert api.posted("/v1/spines/sp1/cascade/execute") == []


def test_cascade_without_the_field_prints_its_strings_and_no_notes(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = _preview(
        warnings=["Line 1 runs long."]
    )
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = {}
    out = io.StringIO()

    ec.run_line(desk, episode=1, line="1", text="Closed.", out=out)

    text = out.getvalue()
    assert "warning: Line 1 runs long." in text
    assert HEADER not in text


def test_cascade_execute_warnings_are_said_when_the_preview_had_none(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = _preview()
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = {
        "authoring_warnings": [_take_over("episode_01", 1)]
    }
    out = io.StringIO()

    ec.run_line(desk, episode=1, line="1", text="Closed.", out=out)

    assert "note: take 1 speaks 26 words (22 recommended)" in out.getvalue()
    assert "take 1 speaks 26 words" in _notes(desk, 1)
