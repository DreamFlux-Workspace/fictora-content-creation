"""`edit --beat N --shot-plan/--shot/--clear-shot-plan`: a beat's own shots (fictora-drama #464) on the server and the desk."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation import shot_plan as sp
from creation.cli_produce import main as produce_main
from creation.spine_view import script_lines, shot_list_lines
from fake_api import FakeApi, spine_fixture

PLAN = [
    {
        "size": "extreme close-up",
        "subject": "Hana's hands on the counter",
        "camera": "handheld",
    },
    {"size": "wide", "subject": "the shop", "camera": "locked", "angle": "high angle"},
]


def _server_keeps_plans(api: FakeApi) -> None:
    """A #464 server: the PATCH (and the cascade) store or clear ``beats[].shot_plan``."""

    def apply(patch: dict[str, Any]) -> None:
        beats = {beat["beat_id"]: beat for beat in api.spine_doc["beats"]}
        for edit in patch.get("beats") or []:
            for key, value in edit.items():
                if key == "shot_plan" and value is None:
                    beats[edit["beat_id"]].pop("shot_plan", None)
                elif key != "beat_id":
                    beats[edit["beat_id"]][key] = value

    def on_patch(
        _method: str, _path: str, body: dict[str, Any] | None
    ) -> dict[str, Any]:
        apply((body or {})["patch"])
        return {"spine_version": "v6"}

    def on_execute(
        _method: str, _path: str, _body: dict[str, Any] | None
    ) -> dict[str, Any]:
        apply(previews[-1])
        return {"stale_storyboard_sets": [{"episode_ordinal": 1, "set_index": 1}]}

    previews: list[dict[str, Any]] = []

    def on_preview(
        _method: str, _path: str, body: dict[str, Any] | None
    ) -> dict[str, Any]:
        previews.append((body or {})["edit"]["patch"])
        return {
            "proposal_id": "prop_1",
            "items": [
                {
                    "item_id": "i_frames",
                    "recipe_id": "frames_rewrite",
                    "estimated_tier": "text",
                    "selected": True,
                }
            ],
        }

    api.routes[("PATCH", "/v1/spines/sp1")] = on_patch
    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = on_preview
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = on_execute


def _patches(api: FakeApi) -> list[dict[str, Any]]:
    return [
        body["patch"] for method, _, body, _ in api.calls if method == "PATCH" and body
    ]


def test_shot_flags_before_the_gate_send_only_the_plan_and_save_the_story(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    _server_keeps_plans(api)
    argv = [
        "edit", "--desk", str(desk), "--episode", "1", "--beat", "1",
        "--shot", "extreme close-up|Hana's hands on the counter|handheld",
        "--shot", "wide | the shop | locked | high angle",
    ]  # fmt: skip

    assert produce_main(argv) == 0

    assert _patches(api) == [
        {"beats": [{"beat_id": "beat_episode_01_01", "shot_plan": PLAN}]}
    ]
    for saved in (desk / "api" / "spine.json", desk / "ep01" / "api" / "spine.json"):
        assert (
            json.loads(saved.read_text(encoding="utf-8"))["beats"][0]["shot_plan"]
            == PLAN
        )
    printed = capsys.readouterr().out
    assert "shot plan on the server now:" in printed
    assert "plan shot 2: wide on the shop, locked, high angle" in printed
    assert "!!" not in printed


def test_shot_plan_json_from_a_file_equals_the_flags_and_a_repeat_changes_nothing(
    desk: Path, api: FakeApi, tmp_path: Path
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    _server_keeps_plans(api)
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({"shot_plan": PLAN}), encoding="utf-8")

    ec.run_edit(
        desk,
        episode=1,
        beat="1",
        shot_plan=sp.plan_from_json(f"@{plan_file}"),
        out=io.StringIO(),
    )
    with pytest.raises(
        ec.CommandStopped, match="nothing to change on beat_episode_01_01"
    ):
        ec.run_edit(
            desk,
            episode=1,
            beat="1",
            shot_plan=sp.plan_from_json(json.dumps(PLAN)),
            out=io.StringIO(),
        )

    assert _patches(api) == [
        {"beats": [{"beat_id": "beat_episode_01_01", "shot_plan": PLAN}]}
    ]


def test_clear_sends_null_and_a_plan_travels_with_an_intent_edit(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.spine_doc["beats"][0]["shot_plan"] = PLAN
    _server_keeps_plans(api)

    ec.run_edit(desk, episode=1, beat="1", clear_shot_plan=True, out=io.StringIO())
    ec.run_edit(
        desk,
        episode=1,
        beat="1",
        intent="Hana locks the door",
        shot_plan=PLAN[:1],
        out=io.StringIO(),
    )

    first, second = _patches(api)
    assert first == {"beats": [{"beat_id": "beat_episode_01_01", "shot_plan": None}]}
    assert second["beats"][0]["motion_intent"] == "Hana locks the door"
    assert second["beats"][0]["shot_plan"] == PLAN[:1]
    assert "motion_direction" in second["beats"][0]


def test_after_the_gate_the_plan_goes_through_the_cascade_and_names_the_board_to_redraw(
    desk: Path, api: FakeApi
) -> None:
    _server_keeps_plans(api)
    out = io.StringIO()

    ec.run_edit(desk, episode=1, beat="1", shot_plan=PLAN, out=out)

    preview = api.posted("/v1/spines/sp1/cascade/preview")[0]
    assert preview["edit"]["patch"] == {
        "beats": [{"beat_id": "beat_episode_01_01", "shot_plan": PLAN}]
    }
    assert _patches(api) == []
    assert "redraw-board --episode 1 --take t1" in out.getvalue()
    assert (
        json.loads((desk / "api" / "spine.json").read_text(encoding="utf-8"))["beats"][
            0
        ]["shot_plan"]
        == PLAN
    )


def test_an_older_server_422_is_said_plainly(desk: Path, api: FakeApi) -> None:
    api.spine_doc = spine_fixture(approved=False)
    body = {
        "error": {
            "code": "validation_error",
            "message": "patch.beats.0.shot_plan: Extra inputs are not permitted",
        }
    }
    api.routes[("PATCH", "/v1/spines/sp1")] = SystemExit(
        f"HTTP 422 PATCH https://drama.example/v1/spines/sp1: {ec.api_error_text(body)}"
    )

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_edit(desk, episode=1, beat="1", shot_plan=PLAN, out=io.StringIO())

    message = str(stopped.value)
    assert message.startswith("the server refused the shot plan: HTTP 422")
    assert "Extra inputs are not permitted" in message
    assert "older than beat shot plans" in message


def test_a_server_that_drops_the_field_is_flagged(desk: Path, api: FakeApi) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.routes[("PATCH", "/v1/spines/sp1")] = {"spine_version": "v6"}
    out = io.StringIO()

    ec.run_edit(desk, episode=1, beat="1", shot_plan=PLAN, out=out)

    assert "(no plan: the frames author chooses the shots)" in out.getvalue()
    assert "does not hold the plan that was sent" in out.getvalue()


@pytest.mark.parametrize(
    ("argv_tail", "why"),
    [
        (
            ["--beat", "1", *sum((["--shot", "wide|the shop"] for _ in range(5)), [])],
            "1-4 shots",
        ),
        (["--beat", "1", "--shot", "wide"], "size|subject|camera|angle"),
        (
            [
                "--beat",
                "1",
                "--shot-plan",
                '[{"size": "wide", "subject": "x", "lens": "35mm"}]',
            ],
            "unknown field",
        ),
        (["--beat", "1", "--shot-plan", '[{"size": "wide"}]'], "subject is required"),
        (["--beat", "1", "--shot-plan", "null"], "--clear-shot-plan"),
        (
            [
                "--beat",
                "1",
                "--shot-plan",
                json.dumps([{"size": "w" * 201, "subject": "x"}]),
            ],
            "the most is 200",
        ),
        (["--frame", "1", "--shot", "wide|the shop"], "belong to a beat"),
    ],
)
def test_a_plan_the_server_would_refuse_stops_before_any_call(
    desk: Path,
    api: FakeApi,
    capsys: pytest.CaptureFixture[str],
    argv_tail: list[str],
    why: str,
) -> None:
    api.spine_doc = spine_fixture(approved=False)

    assert (
        produce_main(["edit", "--desk", str(desk), "--episode", "1", *argv_tail]) == 2
    )

    assert why in capsys.readouterr().err
    assert _patches(api) == []


def test_the_script_and_board_printouts_show_the_plan() -> None:
    spine = spine_fixture()
    spine["beats"][0]["shot_plan"] = PLAN

    script = script_lines(spine, episode=1, take_ids=["t1"])
    board = shot_list_lines(spine, episode=1)

    assert (
        "    plan shot 1: extreme close-up on Hana's hands on the counter, handheld"
        in script
    )
    assert "  beat 1 asks for (its first row is shot 1):" in board
    assert "    plan shot 2: wide on the shop, locked, high angle" in board
    assert not any(
        "plan shot" in line
        for line in script_lines(spine_fixture(), episode=1, take_ids=["t1"])
    )


def test_a_server_that_keeps_the_plan_in_its_own_shape_is_not_flagged(
    desk: Path, api: FakeApi
) -> None:
    """Exact equality raised a false 'server dropped plan' when the server added nulls or reordered keys."""

    api.spine_doc = spine_fixture(approved=False)
    held = [
        {"angle": None, "camera": "handheld", "size": "Extreme close-up ", "subject": "Hana's hands on the counter",
         "row": 1},
        {"subject": "the shop", "size": "wide", "angle": "high angle", "camera": "locked", "row": 2},
    ]  # fmt: skip

    def on_patch(_m: str, _p: str, _b: dict[str, Any] | None) -> dict[str, Any]:
        api.spine_doc["beats"][0]["shot_plan"] = held
        return {"spine_version": "v6"}

    api.routes[("PATCH", "/v1/spines/sp1")] = on_patch
    out = io.StringIO()

    ec.run_edit(desk, episode=1, beat="1", shot_plan=PLAN, out=out)

    assert "does not hold the plan that was sent" not in out.getvalue()


def _three_beat_take(api: FakeApi) -> None:
    """One take of three beats. Extra shots are allowed only on the last."""

    doc = spine_fixture(episodes=1, approved=False)
    base = doc["beats"][0]
    beats = []
    for number in range(1, 4):
        beat = json.loads(json.dumps(base))
        beat["beat_id"] = f"beat_episode_01_0{number}"
        beat["ordinal"] = number
        beat["dialogue_lines"][0]["line_id"] = f"line_episode_01_0{number}"
        beats.append(beat)
    doc["beats"] = beats
    doc["beats_per_storyboard_set"] = [3]
    api.spine_doc = doc


def test_two_shots_on_an_earlier_beat_are_refused_before_any_call(
    desk: Path, api: FakeApi
) -> None:
    _three_beat_take(api)

    with pytest.raises(ec.CommandStopped, match="last beat of its take"):
        ec.run_edit(desk, episode=1, beat="1", shot_plan=PLAN, out=io.StringIO())

    assert _patches(api) == []


def test_the_last_beat_of_the_take_can_hold_two_shots(desk: Path, api: FakeApi) -> None:
    _three_beat_take(api)
    _server_keeps_plans(api)

    ec.run_edit(desk, episode=1, beat="3", shot_plan=PLAN, out=io.StringIO())

    assert _patches(api) == [
        {"beats": [{"beat_id": "beat_episode_01_03", "shot_plan": PLAN}]}
    ]


def test_inner_voice_words_in_a_beat_are_refused_before_any_call(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False)

    with pytest.raises(ec.CommandStopped, match="inner voice"):
        ec.run_edit(
            desk,
            episode=1,
            beat="1",
            intent="Hana (inner voice) counts the cabins",
            out=io.StringIO(),
        )

    assert _patches(api) == []


def test_a_speaker_who_is_not_the_motion_subject_is_refused_before_any_call(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.spine_doc["beats"][0]["motion_direction"]["subject_cast_id"] = "cast_hana"

    with pytest.raises(ec.CommandStopped, match="motion subject must match"):
        ec.run_edit(
            desk,
            episode=1,
            line_id="line_episode_01_01",
            speaker="Ren",
            out=io.StringIO(),
        )

    assert _patches(api) == []


def test_same_plan_ignores_shape_but_not_shots() -> None:
    from creation.shot_plan import same_plan

    assert same_plan(None, []) and same_plan([], None)
    assert same_plan(
        [{"size": "wide", "subject": "x", "camera": ""}],
        [{"subject": "x", "size": "WIDE"}],
    )
    assert not same_plan(PLAN[:1], PLAN)
    assert not same_plan(
        [{"size": "wide", "subject": "the shop"}],
        [{"size": "wide", "subject": "the street"}],
    )
