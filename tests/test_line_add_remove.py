"""`line --add / --remove / --new-voice`: the server's add/remove/voice-only fields, its named refusals, the desk."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.harness.http_util import api_error_text
from creation.ops.floor import approve_script
from creation.ops.state import episode_by_ordinal, load_series
from fake_api import FakeApi, spine_fixture


def _with_silent_beat(spine: dict[str, Any]) -> dict[str, Any]:
    """Episode 1 gets a second beat with no line whose shot moves Hana (the SCP-173 silent beat)."""

    spine["beats"].insert(
        1,
        {
            "beat_id": "beat_episode_01_02",
            "episode_id": "episode_01",
            "ordinal": 2,
            "motion_intent": "Hana looks at the door",
            "motion_direction": {
                "camera_move": "locked",
                "intensity": "low",
                "subject_cast_id": "cast_hana",
            },
            "dialogue_lines": [],
        },
    )
    return spine


def _refusal(code: str, message: str, details: dict[str, Any]) -> SystemExit:
    """What the kit's session raises on a named 400 (``creation.harness.session``)."""

    body = {
        "error": {"code": code, "message": message, "details": details},
        "request_id": "req_1",
    }
    return SystemExit(
        f"HTTP 400 PATCH https://drama.example/v1/spines/sp1: {api_error_text(body)}"
    )


def _server(api: FakeApi) -> Any:
    """Apply a story patch as fictora-drama PR #466 does, refusing a beat that already speaks by name."""

    def apply(patch: dict[str, Any]) -> None:
        removed = set(patch.get("remove_dialogue_line_ids") or [])
        beats = {beat["beat_id"]: beat for beat in api.spine_doc["beats"]}
        for add in patch.get("add_dialogue_lines") or []:
            speaking = [
                ln["line_id"]
                for ln in beats[add["beat_id"]]["dialogue_lines"]
                if ln["line_id"] not in removed
            ]
            if speaking:
                raise _refusal(
                    "beat_already_has_line",
                    "A beat carries one spoken line.",
                    {"beat_ids": [add["beat_id"]], "line_ids": speaking},
                )
        for edit in patch.get("beats") or []:
            beats[edit["beat_id"]]["motion_direction"] = edit["motion_direction"]
        for add in patch.get("add_dialogue_lines") or []:
            subject = (beats[add["beat_id"]].get("motion_direction") or {}).get(
                "subject_cast_id"
            )
            if subject and subject != add["cast_id"]:
                raise _refusal(
                    "line_speaker_not_motion_subject",
                    "A speaking beat moves its speaker.",
                    {
                        "beats": [
                            {
                                "beat_id": add["beat_id"],
                                "speaker_cast_id": add["cast_id"],
                                "motion_subject_cast_id": subject,
                            }
                        ]
                    },
                )
        for card in patch.get("add_voice_only_cast") or []:
            api.spine_doc["cast"].append(
                {"cast_id": card["cast_id"], "name": card["name"], "role": card["role"]}
            )
        for beat in api.spine_doc["beats"]:
            beat["dialogue_lines"] = [
                ln for ln in beat["dialogue_lines"] if ln["line_id"] not in removed
            ]
        for add in patch.get("add_dialogue_lines") or []:
            line = {k: v for k, v in add.items() if k != "beat_id"}
            beats[add["beat_id"]]["dialogue_lines"].append(
                {"line_id": f"line_{add['beat_id'][5:]}_1a2b3c4d", **line}
            )

    return apply


def _before_gate(api: FakeApi) -> None:
    api.spine_doc = _with_silent_beat(spine_fixture(approved=False))
    apply = _server(api)

    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        apply((body or {})["patch"])
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch


def _after_gate(api: FakeApi) -> list[dict[str, Any]]:
    api.spine_doc = _with_silent_beat(spine_fixture())
    apply = _server(api)
    edits: list[dict[str, Any]] = []

    def preview(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        edits.append(copy.deepcopy((body or {})["edit"]))
        return {"proposal_id": "prop_1", "items": []}

    def execute(*_: Any) -> dict[str, Any]:
        apply(edits[-1]["patch"])
        return {"stale_storyboard_sets": []}

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = preview
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = execute
    return edits


def _desk_lines(desk: Path) -> list[tuple[str, str]]:
    take = episode_by_ordinal(load_series(desk), 1).takes[0]
    return [(line.speaker, line.original) for line in take.lines]


def _patches(api: FakeApi) -> list[dict[str, Any]]:
    return [
        body["patch"] for method, _, body, _ in api.calls if method == "PATCH" and body
    ]


def test_add_before_the_gate_patches_the_new_line_resaves_the_spine_and_syncs_the_desk(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api)
    out = io.StringIO()

    ec.run_line(
        desk,
        episode=1,
        add=True,
        beat="2",
        speaker="Hana",
        text="Someone is at the door.",
        out=out,
    )

    assert _patches(api) == [
        {
            "add_dialogue_lines": [
                {
                    "beat_id": "beat_episode_01_02",
                    "cast_id": "cast_hana",
                    "text": "Someone is at the door.",
                }
            ]
        }
    ]
    saved = json.loads((desk / "api" / "spine.json").read_text(encoding="utf-8"))
    assert saved["beats"][1]["dialogue_lines"][0]["text"] == "Someone is at the door."
    assert ("Hana", "Someone is at the door.") in _desk_lines(desk)
    text = out.getvalue()
    assert "+ beat 2 (beat_episode_01_02)  Hana: Someone is at the door." in text
    assert "new line: line_episode_01_02_1a2b3c4d" in text
    assert "script approval: not given yet" in text


def test_remove_drops_the_line_on_the_server_and_the_desk(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api)
    out = io.StringIO()

    ec.run_line(desk, episode=1, remove="2", out=out)

    assert _patches(api) == [{"remove_dialogue_line_ids": ["line_episode_01_02"]}]
    assert _desk_lines(desk) == [("Hana", "We're closed.")]
    assert "- line_episode_01_02  Ren: Not for me." in out.getvalue()


def test_a_beat_that_already_speaks_is_refused_by_name_with_the_fix(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api)

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_line(
            desk,
            episode=1,
            add=True,
            beat="1",
            speaker="Ren",
            text="Fine.",
            out=io.StringIO(),
        )

    message = str(stopped.value)
    assert "the server refused the line edit (beat_already_has_line)" in message
    assert (
        "remove the old line in the same command (`--add ... --remove line_episode_01_01, line_episode_01_02`)"
        in message
    )
    assert _desk_lines(desk) == []  # nothing synced after a refusal


def test_replace_sends_remove_and_add_in_one_patch(desk: Path, api: FakeApi) -> None:
    _before_gate(api)
    api.spine_doc["beats"][0][
        "dialogue_lines"
    ].pop()  # one line per beat, as the server holds it
    out = io.StringIO()

    ec.run_line(
        desk,
        episode=1,
        add=True,
        beat="1",
        speaker="Ren",
        text="Fine.",
        remove="1",
        out=out,
    )

    assert _patches(api) == [
        {
            "remove_dialogue_line_ids": ["line_episode_01_01"],
            "add_dialogue_lines": [
                {
                    "beat_id": "beat_episode_01_01",
                    "cast_id": "cast_ren",
                    "text": "Fine.",
                }
            ],
        }
    ]
    assert _desk_lines(desk) == [("Ren", "Fine.")]


def test_a_speaker_who_is_not_the_motion_subject_is_refused_and_speaker_moves_sends_the_subject(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api)

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_line(
            desk,
            episode=1,
            add=True,
            beat="2",
            speaker="Ren",
            text="Open it.",
            out=io.StringIO(),
        )
    message = str(stopped.value)
    assert "(line_speaker_not_motion_subject)" in message
    assert "beat_episode_01_02 moves Hana, not Ren" in message
    assert "add --speaker-moves" in message

    out = io.StringIO()
    ec.run_line(
        desk,
        episode=1,
        add=True,
        beat="2",
        speaker="Ren",
        text="Open it.",
        speaker_moves=True,
        out=out,
    )

    last = _patches(api)[-1]
    assert last["beats"] == [
        {
            "beat_id": "beat_episode_01_02",
            "motion_direction": {
                "camera_move": "locked",
                "intensity": "low",
                "subject_cast_id": "cast_ren",
            },
        }
    ]
    assert "motion subject of beat_episode_01_02: Hana  ->  Ren" in out.getvalue()


def test_a_new_voice_after_the_gate_goes_through_the_cascade_off_screen_and_reopens_the_desk(
    desk: Path, api: FakeApi
) -> None:
    edits = _after_gate(api)
    api.spine_doc["beats"][1]["motion_direction"]["subject_cast_id"] = (
        "cast_speaker-voice"
    )
    ec.sync_spine_lines(desk, api.spine_doc, episode=1)
    approve_script(desk, episode=1)
    out = io.StringIO()

    ec.run_line(
        desk,
        episode=1,
        add=True,
        beat="2",
        text="D-9341. Do not break eye contact.",
        new_voice="Speaker voice",
        role="Facility intercom",
        voice_description="tinny, clipped, calm",
        out=out,
    )

    assert edits == [
        {
            "scope": "section",
            "target_type": "episode",
            "target_id": "episode_01",
            "patch": {
                "add_voice_only_cast": [
                    {
                        "cast_id": "cast_speaker-voice",
                        "name": "Speaker voice",
                        "role": "Facility intercom",
                        "voice_description": "tinny, clipped, calm",
                    }
                ],
                "add_dialogue_lines": [
                    {
                        "beat_id": "beat_episode_01_02",
                        "cast_id": "cast_speaker-voice",
                        "text": "D-9341. Do not break eye contact.",
                        "off_screen": True,
                    }
                ],
            },
        }
    ]
    assert ("Speaker voice", "D-9341. Do not break eye contact.") in _desk_lines(desk)
    text = out.getvalue()
    assert "+ voice Speaker voice (cast_speaker-voice), heard, never drawn" in text
    assert "Speaker voice (off-screen): D-9341." in text
    assert "the server keeps this script approved" in text
    assert "pending again" in text
    assert episode_by_ordinal(load_series(desk), 1).script.status == "pending"


def test_a_new_line_on_a_japanese_show_says_the_server_writes_its_performed_line(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api)
    api.spine_doc["spoken_language"] = "ja-JP"
    out = io.StringIO()

    ec.run_line(
        desk,
        episode=1,
        add=True,
        beat="2",
        speaker="Hana",
        text="Someone is here.",
        out=out,
    )

    assert (
        "The server writes the new line's performed ja-JP line when the script is approved"
        in out.getvalue()
    )


@pytest.mark.parametrize(
    ("kwargs", "stop"),
    [
        (
            {"new_voice": "Speaker voice", "role": "r", "voice_description": "v"},
            r"--new-voice describe a new line: add --add \(a new voice comes with its line\)",
        ),
        ({"remove": "1", "role": "intercom"}, "--role describe a --new-voice"),
        (
            {"add": True, "beat": "2", "text": "x", "new_voice": "Speaker voice"},
            "--new-voice needs --role",
        ),
        (
            {
                "add": True,
                "beat": "2",
                "text": "x",
                "new_voice": "Hana",
                "role": "r",
                "voice_description": "v",
            },
            "already in the cast",
        ),
        (
            {
                "add": True,
                "beat": "2",
                "text": "x",
                "new_voice": "V",
                "role": "r",
                "voice_description": "v",
                "off_screen": False,
            },
            "heard, never seen",
        ),
        ({"add": True, "beat": "2", "text": "x"}, "--add needs --speaker NAME"),
        ({"add": True, "text": "x", "speaker": "Hana"}, "--add needs --beat N"),
        (
            {"add": True, "beat": "2", "text": "x", "speaker": "Hana", "line": "1"},
            "run them as two commands",
        ),
    ],
)
def test_command_line_mistakes_stop_before_any_call(
    desk: Path, api: FakeApi, kwargs: dict[str, Any], stop: str
) -> None:
    _before_gate(api)
    with pytest.raises(ec.CommandStopped, match=stop):
        ec.run_line(desk, episode=1, out=io.StringIO(), **kwargs)
    assert _patches(api) == []


def test_an_unknown_refusal_is_passed_through_unchanged() -> None:
    message = "HTTP 409 PATCH https://x/v1/spines/sp1: spine_version_conflict: stale"
    assert ec.explain_refusal(message, spine_fixture(), episode=1) == message


def test_the_cli_takes_add_new_voice_and_provider_voice(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api)
    api.spine_doc["beats"][1]["motion_direction"]["subject_cast_id"] = "cast_intercom"
    argv = [
        "line", "--desk", str(desk), "--episode", "1", "--add", "--beat", "2", "--text", "Stay still.",
        "--new-voice", "Intercom", "--role", "Facility intercom", "--voice-description", "tinny", "--provider-voice", "Rachel",
    ]  # fmt: skip

    assert produce_main(argv) == 0

    patch = _patches(api)[0]
    assert patch["add_voice_only_cast"][0]["provider_voice"] == "Rachel"
    assert patch["add_dialogue_lines"][0]["off_screen"] is True
