"""Lines: `line` edits the server and the desk together, the board says who speaks on each row, the draft
compares the brief's lines with the script."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.brief_lines import (
    brief_vs_spine_lines,
    compare_lines,
    parse_brief_lines,
    spine_script_lines,
)
from creation.cli_produce import main as produce_main
from creation.ops.floor import approve_script
from creation.ops.state import episode_by_ordinal, load_series
from creation.spine_view import shot_list_lines
from fake_api import FakeApi, spine_fixture

# --- line -----------------------------------------------------------------------------------------


def _apply_line_patch(api: FakeApi) -> Any:
    """A PATCH / cascade that really changes the fake server's story, as the deployed API does."""

    def apply(patch: dict[str, Any]) -> None:
        for edit in patch.get("dialogue_lines") or []:
            for beat in api.spine_doc["beats"]:
                for line in beat["dialogue_lines"]:
                    if line["line_id"] == edit["line_id"]:
                        line.update({k: v for k, v in edit.items() if k != "line_id"})

    return apply


def _desk_lines(desk: Path) -> list[tuple[str, str]]:
    take = episode_by_ordinal(load_series(desk), 1).takes[0]
    return [(line.speaker, line.original) for line in take.lines]


def test_line_before_the_gate_patches_the_server_resaves_the_spine_and_syncs_the_desk(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    apply = _apply_line_patch(api)

    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        apply((body or {})["patch"])
        return {"spine_version": "v6"}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch
    out = io.StringIO()

    ec.run_line(desk, episode=1, line="2", text="Not for you.", speaker="hana", out=out)

    bodies = [body for method, _, body, _ in api.calls if method == "PATCH"]
    assert bodies == [
        {
            "spine_version": "v5",
            "patch": {
                "dialogue_lines": [
                    {
                        "line_id": "line_episode_01_02",
                        "text": "Not for you.",
                        "cast_id": "cast_hana",
                    }
                ]
            },
        }
    ]
    assert _desk_lines(desk) == [("Hana", "We're closed."), ("Hana", "Not for you.")]
    saved = json.loads((desk / "api" / "spine.json").read_text(encoding="utf-8"))
    assert saved["beats"][0]["dialogue_lines"][1]["text"] == "Not for you."
    text = out.getvalue()
    assert "speaker: Ren  ->  Hana" in text
    assert "script approval: not given yet" in text
    assert "pending again" not in text


def test_line_after_the_gate_goes_through_the_cascade_and_reopens_the_desks_script_yes(
    desk: Path, api: FakeApi
) -> None:
    apply = _apply_line_patch(api)
    edits: list[dict[str, Any]] = []

    def preview(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        edits.append(copy.deepcopy((body or {})["edit"]["patch"]))
        return {"proposal_id": "prop_1", "items": []}

    def execute(*_: Any) -> dict[str, Any]:
        apply(edits[-1])
        return {"stale_storyboard_sets": []}

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = preview
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = execute
    ec.sync_spine_lines(desk, api.spine_doc, episode=1)
    approve_script(desk, episode=1)
    out = io.StringIO()

    ec.run_line(
        desk,
        episode=1,
        line="line_episode_01_01",
        text="We're closed. Go home.",
        out=out,
    )

    assert edits == [
        {
            "dialogue_lines": [
                {"line_id": "line_episode_01_01", "text": "We're closed. Go home."}
            ]
        }
    ]
    assert _desk_lines(desk) == [
        ("Hana", "We're closed. Go home."),
        ("Ren", "Not for me."),
    ]
    text = out.getvalue()
    assert "the server keeps this script approved" in text
    assert "pending again" in text and "--gate script --episode 1" in text
    assert episode_by_ordinal(load_series(desk), 1).script.status == "pending"


def test_line_speaker_outside_the_cast_points_to_new_voice(
    desk: Path, api: FakeApi
) -> None:
    with pytest.raises(
        ec.CommandStopped, match="no cast member 'Speaker voice'.*--new-voice NAME"
    ):
        ec.run_line(
            desk, episode=1, line="1", speaker="Speaker voice", out=io.StringIO()
        )
    with pytest.raises(
        ec.CommandStopped,
        match=r"no line '9' in episode 1; its lines are:\n  1\. line_episode_01_01",
    ):
        ec.run_line(desk, episode=1, line="9", text="x", out=io.StringIO())
    assert [call for call in api.calls if call[0] != "GET"] == []


def test_line_marks_a_speaker_heard_not_seen_and_warns_when_the_speaker_is_not_drawn(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.spine_doc["beats"][0]["frame_id"] = "frame_episode_01_01"
    apply = _apply_line_patch(api)

    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        apply((body or {})["patch"])
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch
    out = io.StringIO()
    ec.run_line(desk, episode=1, line="2", text="Not for me, then.", out=out)
    assert "!! Ren speaks this line on frame_episode_01_01" in out.getvalue()

    out = io.StringIO()
    ec.run_line(desk, episode=1, line="2", off_screen=True, out=out)
    body = [b for m, _, b, _ in api.calls if m == "PATCH"][-1]
    assert body["patch"] == {
        "dialogue_lines": [{"line_id": "line_episode_01_02", "off_screen": True}]
    }
    assert "does not draw" not in out.getvalue()


def test_a_new_english_line_on_a_japanese_show_says_the_performed_line_is_rewritten(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False, spoken_language="ja-JP")
    api.routes[("PATCH", "/v1/spines/sp1")] = {}
    out = io.StringIO()

    ec.run_line(desk, episode=1, line="1", text="We are closed.", out=out)

    assert "The performed ja-JP line was dropped" in out.getvalue()
    assert "when the script is approved" in out.getvalue()


def test_line_with_no_change_lists_the_lines_through_the_cli(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert produce_main(["line", "--desk", str(desk), "--episode", "1"]) == 0
    text = capsys.readouterr().out
    assert "1. line_episode_01_01  beat 1  Hana: We're closed." in text
    assert "2. line_episode_01_02  beat 1  Ren: Not for me." in text


# --- board: who speaks on each row ---------------------------------------------------------------


def _board_spine() -> dict[str, Any]:
    spine = spine_fixture()
    beat = spine["beats"][0]
    beat["frame_id"] = "frame_episode_01_02"
    return spine


def test_the_shot_list_says_who_speaks_on_each_row_and_warns_when_they_are_not_drawn() -> (
    None
):
    lines = "\n".join(shot_list_lines(_board_spine(), episode=1))

    assert 'row 2 cell 1 says: Hana: "We\'re closed."' in lines
    assert 'row 2 cell 1 says: Ren: "Not for me."' in lines
    assert (
        '!! row 2: Ren speaks "Not for me." but is not drawn (the row shows Hana)'
        in lines
    )
    assert "!! row 2: Hana" not in lines
    assert "row 1 cell" not in lines


def test_an_off_screen_speaker_needs_no_place_on_the_row() -> None:
    spine = _board_spine()
    spine["beats"][0]["dialogue_lines"][1]["off_screen"] = True

    lines = "\n".join(shot_list_lines(spine, episode=1))

    assert 'Ren (off-screen): "Not for me."' in lines
    assert "!! row 2: Ren" not in lines


def _heard_only_ren_on_the_board() -> dict[str, Any]:
    """Hanakaze ep 3 shape: Ren's only line is off-screen, yet row 1 lists him."""

    spine = _board_spine()
    spine["beats"][0]["dialogue_lines"][1]["off_screen"] = True
    first = spine["frames"][0]
    first["cast_refs"] = ["cast_ren"]
    first["visual_brief"]["subject_blocking"][0]["cast_id"] = "cast_ren"
    return spine


def test_an_off_screen_speaker_listed_on_the_board_is_warned() -> None:
    lines = "\n".join(shot_list_lines(_heard_only_ren_on_the_board(), episode=1))

    assert (
        "!! Ren is off-screen in this take (heard, not seen) but is listed on frame_episode_01_01 (row 1)"
        in lines
    )
    assert "Hana is off-screen" not in lines


def test_an_off_screen_speaker_seen_elsewhere_in_the_take_is_not_warned() -> None:
    spine = _heard_only_ren_on_the_board()
    spine["beats"][0]["dialogue_lines"].append(
        {
            "line_id": "line_extra",
            "cast_id": "cast_ren",
            "text": "Fine.",
            "off_screen": None,
        }
    )

    lines = "\n".join(shot_list_lines(spine, episode=1))

    assert "Ren is off-screen" not in lines


def test_an_off_screen_line_on_a_faceless_row_is_warned() -> None:
    spine = _board_spine()
    spine["beats"][0]["dialogue_lines"][1]["off_screen"] = True
    brief = spine["frames"][1]["visual_brief"]
    brief["cell_role"] = "insert"
    brief["shot_scale"] = "insert shot"

    lines = "\n".join(shot_list_lines(spine, episode=1))

    assert "!! row 2 carries Ren's off-screen line but shows no face" in lines


def test_a_speaker_placed_off_frame_is_warned() -> None:
    spine = _board_spine()
    spine["frames"][1]["visual_brief"]["subject_blocking"].append(
        {"cast_id": "cast_ren", "frame_position": "off-frame right"}
    )

    lines = "\n".join(shot_list_lines(spine, episode=1))

    assert '!! row 2: Ren speaks "Not for me." but is placed off-frame' in lines


# --- the brief's lines against the drafted script ------------------------------------------------

BRIEF = """# Brief — SCP-173 Blink

## Cast

| Name | Role | Speaks? | Existing plates? |
| --- | --- | --- | --- |
| D-9341 | Prisoner | yes | no |

## Lines (three maximum per take)

### Take 1

| # | Beat | Speaker | Original | Translation |
| --- | --- | --- | --- | --- |
| 1 | 1 | Speaker voice (off-screen, flat, calm) | D-9341. Do not break eye contact. | — |
| 2 | 2 | D-9341 (begging) | My eyes are burning, please open the door! | — |
| 3 | 3 | D-9341 (desperate) | No no no— I'm not blinking, I'm NOT— | — |

Approved: no
"""


def _drafted() -> dict[str, Any]:
    return {
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
        "cast": [{"cast_id": "cast_voice", "name": "Speaker voice"}, {"cast_id": "cast_d", "name": "D-9341"}],
        "beats": [
            {"episode_id": "episode_01", "ordinal": 1, "dialogue_lines": [
                {"line_id": "l1", "cast_id": "cast_voice", "text": "D-9341. Do not break eye contact."}]},
            {"episode_id": "episode_01", "ordinal": 2, "dialogue_lines": []},
            {"episode_id": "episode_01", "ordinal": 3, "dialogue_lines": [
                {"line_id": "l3", "cast_id": "cast_d", "text": "No no no, I'm not blinking. Someone take over."}]},
        ],
    }  # fmt: skip


def test_the_brief_lines_table_is_read_with_speakers_stripped_of_directions() -> None:
    lines = parse_brief_lines(BRIEF)
    assert [(line.speaker, line.text) for line in lines] == [
        ("Speaker voice", "D-9341. Do not break eye contact."),
        ("D-9341", "My eyes are burning, please open the door!"),
        ("D-9341", "No no no— I'm not blinking, I'm NOT—"),
    ]
    assert (
        parse_brief_lines(
            "# Brief\n\n## Lines\n\n### Take 1\n\n| # | Speaker | Original | Translation |\n| --- |"
        )
        == []
    )


def test_brief_lines_are_matched_by_speaker_and_similarity() -> None:
    matches = compare_lines(
        parse_brief_lines(BRIEF), spine_script_lines(_drafted(), episode=1)
    )
    assert [(m.verdict, m.spine.line_id if m.spine else None) for m in matches] == [
        ("kept", "l1"),
        ("cut", None),
        ("rewritten", "l3"),
    ]


def test_the_comparison_prints_counts_each_change_and_what_to_do() -> None:
    text = "\n".join(brief_vs_spine_lines(BRIEF, _drafted(), episode=1))
    assert "brief 3 -> script 2 (kept 1, rewritten 1, cut 1, added 0)" in text
    assert 'cut        D-9341: "My eyes are burning, please open the door!"' in text
    assert (
        'script D-9341: "No no no, I\'m not blinking. Someone take over."  [l3]' in text
    )
    assert "fictora-produce line" in text
    assert brief_vs_spine_lines("A shop at closing time.", _drafted(), episode=1) == []


def test_step_prints_the_brief_against_the_draft(desk: Path, api: FakeApi) -> None:
    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
    }
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }
    api.jobs["job_plan"] = {"status": "completed"}
    brief = (
        "## Lines\n\n| # | Speaker | Original | Translation |\n| --- | --- | --- | --- |\n"
        "| 1 | Hana | We're closed. | — |\n| 2 | Ren | Not for me, sorry. | — |\n"
    )
    set_phase(desk, "new", spine_id=None, prompt=brief)

    result = orchestrate.run_step(desk)

    assert "brief 2 -> script 2 (kept 1, rewritten 1, cut 0, added 0)" in result.message


def test_the_shot_list_says_rows_written_before_a_script_edit_are_rewritten_before_the_first_draw() -> (
    None
):
    """Gallery Heiress L-20261008-19: 12 hand frame edits because the rows looked current."""

    spine = _board_spine()
    spine["media_assets"] = [
        asset for asset in spine.get("media_assets") or [] if asset.get("relation_type") != "episode"
    ]
    for frame in spine["frames"]:
        frame["edited_beat_ids"] = [spine["beats"][0]["beat_id"]]

    lines = "\n".join(shot_list_lines(spine, episode=1))

    assert "rewritten from the script as it is now before this board is first drawn" in lines
    assert "don't hand-edit them" in lines


def test_a_current_take_says_nothing_about_script_edits() -> None:
    lines = "\n".join(shot_list_lines(_board_spine(), episode=1))

    assert "script changed" not in lines
