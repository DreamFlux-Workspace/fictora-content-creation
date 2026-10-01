"""`line --add --new-character NAME --look ... --staging ...` (L-20261001-19)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from cast_story import (
    SAM_BRIEF,
    STAGING,
    _after_gate,
    _before_gate,
    _look_file,
    _patches,
    _refusal,
    _server,
    _story,
    _with_sam,
    _writes,
)
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from fake_api import FakeApi


def test_new_character_is_added_given_a_look_put_on_screen_and_staged_in_order(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))
    out = io.StringIO()

    ec.run_line(
        desk,
        episode=1,
        add=True,
        beat="2",
        text="Last train's gone.",
        new_character="Sam",
        role="station guard",
        voice_description="gravelly older man",
        provider_voice="Bill",
        look=_look_file(tmp_path),
        staging=STAGING,
        out=out,
    )

    voice, look, on_screen, staged = _patches(api)
    assert voice["add_voice_only_cast"][0]["cast_id"] == "cast_sam"
    assert voice["add_dialogue_lines"][0]["off_screen"] is True
    assert look["cast"][0]["visual_brief"] == SAM_BRIEF
    assert on_screen["dialogue_lines"] == [
        {"line_id": "line_episode_01_02_new", "off_screen": False}
    ]
    assert on_screen["beats"][0]["motion_direction"]["subject_cast_id"] == "cast_sam"
    frame = staged["frames"][0]
    assert frame["frame_id"] == "frame_episode_01_02"
    assert frame["cast_refs"] == ["cast_hana", "cast_sam"]
    assert frame["visual_brief"]["subject_blocking"][1] == {
        "cast_id": "cast_sam",
        "frame_position": "right third",
        "pose": "in the doorway",
        "gaze": "at Hana",
        "interaction": "holds the door",
    }
    sam_line = api.spine_doc["beats"][1]["dialogue_lines"][0]
    assert sam_line["cast_id"] == "cast_sam" and sam_line["off_screen"] is False
    text = out.getvalue()
    assert "Sam is on screen" in text
    assert f'redraw-plate --desk {desk.resolve()} --cast "Sam"' in text


def test_new_character_after_the_gate_runs_each_step_through_its_cascade(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    edits = _after_gate(api, _story(approved=True))

    ec.run_line(
        desk, episode=1, add=True, beat="2", text="Last train's gone.", new_character="Sam",
        role="station guard", voice_description="gravelly older man", provider_voice="Bill",
        look=_look_file(tmp_path), staging=STAGING, out=io.StringIO(),
    )  # fmt: skip

    assert [e["target_type"] for e in edits] == [
        "episode",
        "cast_card",
        "episode",
        "episode",
    ]
    assert edits[1]["target_id"] == "cast_sam"
    assert edits[3]["patch"]["frames"][0]["cast_refs"] == ["cast_hana", "cast_sam"]


def test_new_character_on_a_drawn_beat_needs_staging_before_anything_is_sent(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_line(
            desk, episode=1, add=True, beat="2", text="Last train's gone.", new_character="Sam",
            role="station guard", voice_description="gravelly", look=_look_file(tmp_path), out=io.StringIO(),
        )  # fmt: skip

    assert "--staging" in str(stopped.value)
    assert _writes(api) == []


def test_new_character_needs_a_look_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _story(approved=False))

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_line(
            desk, episode=1, add=True, beat="2", text="Hi.", new_character="Sam", role="guard",
            voice_description="gravelly", staging=STAGING, out=io.StringIO(),
        )  # fmt: skip

    assert "--look" in str(stopped.value)
    assert _writes(api) == []


def test_a_step_refused_midway_says_what_was_done_and_how_to_finish_or_undo(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))
    apply = _server(api)

    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        sent = (body or {})["patch"]
        if "dialogue_lines" in sent:
            raise _refusal("invalid_patch", "The story spine patch is invalid.", {})
        apply(sent)
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_line(
            desk, episode=1, add=True, beat="2", text="Last train's gone.", new_character="Sam",
            role="station guard", voice_description="gravelly", provider_voice="Bill",
            look=_look_file(tmp_path), staging=STAGING, out=io.StringIO(),
        )  # fmt: skip

    message = str(stopped.value)
    assert "done: 1. added Sam" in message and "2. gave Sam a look" in message
    assert "not done: 3." in message
    assert "line --desk" in message and "--remove line_episode_01_02_new" in message
    assert not any(
        "frames" in p for p in _patches(api)
    )  # nothing sent after the refused edit


def test_the_cli_takes_new_character_look_and_staging(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))
    argv = [
        "line", "--desk", str(desk), "--episode", "1", "--add", "--beat", "2", "--text", "Last train's gone.",
        "--new-character", "Sam", "--role", "station guard", "--voice-description", "gravelly",
        "--provider-voice", "Bill", "--look", _look_file(tmp_path), "--staging", STAGING,
    ]  # fmt: skip

    assert produce_main(argv) == 0
    assert len(_patches(api)) == 4


def test_the_cli_takes_cast_look(desk: Path, api: FakeApi, tmp_path: Path) -> None:
    _before_gate(api, _with_sam(_story(approved=False)))

    assert (
        produce_main(
            [
                "cast",
                "--desk",
                str(desk),
                "--name",
                "Sam",
                "--look",
                _look_file(tmp_path),
            ]
        )
        == 0
    )
    assert _patches(api)[0]["cast"][0]["cast_id"] == "cast_sam"


def test_running_the_same_command_again_finishes_a_stopped_run_without_repeating_edits(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    _before_gate(api, _story(approved=False))
    apply = _server(api)
    refuse = {"on": True}

    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        sent = (body or {})["patch"]
        if "dialogue_lines" in sent and refuse["on"]:
            raise _refusal("invalid_patch", "The story spine patch is invalid.", {})
        apply(sent)
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch
    command = dict(
        episode=1, add=True, beat="2", text="Last train's gone.", new_character="Sam", role="station guard",
        voice_description="gravelly", provider_voice="Bill", look=_look_file(tmp_path), staging=STAGING,
    )  # fmt: skip
    with pytest.raises(ec.CommandStopped):
        ec.run_line(desk, out=io.StringIO(), **command)
    refuse["on"] = False
    sent_before = len(_patches(api))

    ec.run_line(desk, out=io.StringIO(), **command)

    resumed = _patches(api)[sent_before:]
    assert [sorted(p) for p in resumed] == [["beats", "dialogue_lines"], ["frames"]]
    assert sum("add_voice_only_cast" in p for p in _patches(api)) == 1


def test_new_character_ends_on_applied_and_a_stop_on_refused_for_the_edits_not_made(
    desk: Path, api: FakeApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _before_gate(api, _story(approved=False))
    argv = [
        "line", "--desk", str(desk), "--episode", "1", "--add", "--beat", "2", "--text", "Last train's gone.",
        "--new-character", "Sam", "--role", "station guard", "--voice-description", "gravelly",
        "--provider-voice", "Bill", "--look", _look_file(tmp_path), "--staging", STAGING,
    ]  # fmt: skip

    assert produce_main([*argv, "--preview"]) == 0
    assert capsys.readouterr().out.rstrip().splitlines()[-1].startswith("Not applied")

    apply = _server(api)

    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        sent = (body or {})["patch"]
        if "dialogue_lines" in sent:
            raise _refusal("invalid_patch", "The story spine patch is invalid.", {})
        apply(sent)
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch
    assert produce_main(argv) == 2
    err = capsys.readouterr().err.rstrip().splitlines()
    assert err[-1].startswith("Refused: ") and "(2 changes, none made)" in err[-1]
    assert "  Refused: line on screen" in err

    _before_gate(api, api.spine_doc)
    assert produce_main(argv) == 0
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == "Applied: all 4 changes"
