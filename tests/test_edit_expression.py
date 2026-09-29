"""`edit --beat N --expression KIND|none` and `expressions`: a beat's requested expression (fictora-drama #482)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.expression import (
    ExpressionError,
    expression_options,
    resolve_expression,
    server_takes_expression,
)
from creation.spine_view import script_lines, shot_list_lines
from fake_api import FakeApi, openapi_doc, spine_fixture

CAPABILITIES = {
    "schema_version": "fictora.drama-capabilities.v1",
    "reaction_kinds": [
        {"kind": "slow_surprise", "label": "slow surprise", "comedy": False},
        {"kind": "freeze", "label": "freeze", "comedy": False},
        {"kind": "comic_anger", "label": "comic anger", "comedy": True},
    ],
}


def _openapi(*, reaction_kind: bool) -> dict[str, Any]:
    doc = openapi_doc()
    beat = {"beat_id": {}, "motion_intent": {}, "shot_plan": {}}
    if reaction_kind:
        beat["reaction_kind"] = {}
    doc["components"]["schemas"]["DramaBeatPatch"] = {"properties": beat}
    return doc


def _server_keeps_expressions(api: FakeApi, *, keeps: bool = True) -> None:
    """A #482 server: the capabilities route, and a PATCH / cascade that stores or clears ``reaction_kind``."""

    api.routes[("GET", "/openapi.json")] = _openapi(reaction_kind=True)
    api.routes[("GET", "/v1/capabilities")] = CAPABILITIES

    def apply(patch: dict[str, Any]) -> None:
        if not keeps:
            return
        beats = {beat["beat_id"]: beat for beat in api.spine_doc["beats"]}
        for edit in patch.get("beats") or []:
            for key, value in edit.items():
                if value is None:
                    beats[edit["beat_id"]].pop(key, None)
                elif key != "beat_id":
                    beats[edit["beat_id"]][key] = value

    previews: list[dict[str, Any]] = []

    def on_patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        apply((body or {})["patch"])
        return {"spine_version": "v6"}

    def on_preview(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
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

    def on_execute(_m: str, _p: str, _b: dict[str, Any] | None) -> dict[str, Any]:
        apply(previews[-1])
        return {"stale_storyboard_sets": [{"episode_ordinal": 1, "set_index": 1}]}

    api.routes[("PATCH", "/v1/spines/sp1")] = on_patch
    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = on_preview
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = on_execute


def _patches(api: FakeApi) -> list[dict[str, Any]]:
    return [
        body["patch"] for method, _, body, _ in api.calls if method == "PATCH" and body
    ]


def _sent(api: FakeApi) -> list[str]:
    return [
        f"{method} {path}"
        for method, path, _, _ in api.calls
        if method in {"PATCH", "POST"}
    ]


def test_before_the_gate_a_label_is_checked_and_only_the_kind_is_sent(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    _server_keeps_expressions(api)
    argv = [
        "edit",
        "--desk",
        str(desk),
        "--episode",
        "1",
        "--beat",
        "1",
        "--expression",
        "Slow Surprise",
    ]

    assert produce_main(argv) == 0

    assert _patches(api) == [
        {"beats": [{"beat_id": "beat_episode_01_01", "reaction_kind": "slow_surprise"}]}
    ]
    printed = capsys.readouterr().out
    assert "expression: none (the frames author chooses)  ->  slow_surprise" in printed
    assert "beat_episode_01_01 expression on the server now: slow_surprise" in printed
    assert "!!" not in printed


def test_none_clears_with_null_and_a_repeat_changes_nothing(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    api.spine_doc["beats"][0]["reaction_kind"] = "freeze"
    _server_keeps_expressions(api)

    ec.run_edit(desk, episode=1, beat="1", expression="none", out=io.StringIO())
    with pytest.raises(ec.CommandStopped, match="nothing to change"):
        ec.run_edit(desk, episode=1, beat="1", expression="none", out=io.StringIO())

    assert _patches(api) == [
        {"beats": [{"beat_id": "beat_episode_01_01", "reaction_kind": None}]}
    ]


def test_a_kind_the_deploy_does_not_offer_is_refused_before_anything_is_sent(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    _server_keeps_expressions(api)
    argv = [
        "edit",
        "--desk",
        str(desk),
        "--episode",
        "1",
        "--beat",
        "1",
        "--expression",
        "smirk",
    ]

    assert produce_main(argv) == 2

    assert _sent(api) == []
    err = capsys.readouterr().err
    assert "no expression 'smirk' on this deploy" in err
    assert "slow_surprise, freeze, comic_anger" in err


def test_an_older_deploy_without_the_field_is_refused_before_the_capabilities_read(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    _server_keeps_expressions(api)
    api.routes[("GET", "/openapi.json")] = _openapi(reaction_kind=False)

    with pytest.raises(ec.CommandStopped, match="older than beat expressions"):
        ec.run_edit(desk, episode=1, beat="1", expression="freeze", out=io.StringIO())

    assert _sent(api) == []
    assert not any(path == "/v1/capabilities" for _, path, _, _ in api.calls)


def test_a_deploy_without_the_capabilities_route_is_refused(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    _server_keeps_expressions(api)
    api.routes[("GET", "/openapi.json")] = openapi_doc()  # schema silent: cannot say
    real = api.get_optional

    def get_optional(path: str) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 404, {"detail": "Not Found"}
        return real(path)

    monkeypatch.setattr(api, "get_optional", get_optional)

    with pytest.raises(ec.CommandStopped, match="it has no /v1/capabilities"):
        ec.run_edit(desk, episode=1, beat="1", expression="freeze", out=io.StringIO())
    assert _sent(api) == []


def test_after_the_gate_it_is_a_cascade_and_the_board_is_marked_for_a_redraw(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _server_keeps_expressions(api)

    ec.run_edit(desk, episode=1, beat="1", expression="freeze")

    assert _patches(api) == []
    previews = api.posted("/v1/spines/sp1/cascade/preview")
    assert previews[0]["edit"]["patch"] == {
        "beats": [{"beat_id": "beat_episode_01_01", "reaction_kind": "freeze"}]
    }
    printed = capsys.readouterr().out
    assert "board t1 of ep01 no longer matches the story: `redraw-board" in printed
    assert "expression on the server now: freeze" in printed


def test_it_travels_with_an_intent_edit(desk: Path, api: FakeApi) -> None:
    api.spine_doc = spine_fixture(approved=False)
    _server_keeps_expressions(api)

    ec.run_edit(
        desk,
        episode=1,
        beat="1",
        intent="Hana stops mid-wipe",
        expression="freeze",
        out=io.StringIO(),
    )

    (entry,) = _patches(api)[0]["beats"]
    assert entry["motion_intent"] == "Hana stops mid-wipe"
    assert entry["reaction_kind"] == "freeze"


def test_a_server_that_drops_the_field_is_said_plainly(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = spine_fixture(approved=False)
    _server_keeps_expressions(api, keeps=False)
    out = io.StringIO()

    ec.run_edit(desk, episode=1, beat="1", expression="freeze", out=out)

    assert "does not hold the expression that was sent" in out.getvalue()


def test_expression_belongs_to_a_beat(desk: Path, api: FakeApi) -> None:
    _server_keeps_expressions(api)
    with pytest.raises(ec.CommandStopped, match="belongs to a beat"):
        ec.run_edit(desk, episode=1, frame="1", expression="freeze", out=io.StringIO())
    with pytest.raises(ec.CommandStopped, match="belongs to a beat"):
        ec.build_patch(
            api.spine_doc,
            episode=1,
            frame="1",
            set_reaction_kind=True,
            reaction_kind="freeze",
        )
    assert _sent(api) == []


def test_expressions_lists_the_library_and_each_beats_request(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _server_keeps_expressions(api)
    api.spine_doc["beats"][0]["reaction_kind"] = "comic_anger"

    assert produce_main(["expressions", "--desk", str(desk), "--episode", "1"]) == 0

    printed = capsys.readouterr().out
    assert "slow_surprise  slow surprise" in printed
    assert "comic_anger" in printed and "[comedy" in printed
    assert "beat 1 (beat_episode_01_01): comic_anger" in printed
    assert _sent(api) == []


def test_the_script_and_board_gates_show_the_request_and_flag_a_board_without_it() -> (
    None
):
    spine = spine_fixture()
    spine["beats"][0]["reaction_kind"] = "freeze"

    script = script_lines(spine, episode=1, take_ids=["t1"])
    assert "    expression: freeze" in script

    board = shot_list_lines(spine, episode=1)
    assert "  beat 1 asks for expression freeze (its anchor row wears it)" in board
    assert any(
        line.startswith("  !! beat 1 asks for expression freeze and no row")
        for line in board
    )

    spine["frames"][0]["visual_brief"]["reaction_kind"] = "freeze"
    board = shot_list_lines(spine, episode=1)
    assert not any("no row on this board wears it" in line for line in board)
    assert any("reaction freeze" in line for line in board)


def test_the_library_reader_and_resolver() -> None:
    options = expression_options(CAPABILITIES)
    assert [option["kind"] for option in options] == [
        "slow_surprise",
        "freeze",
        "comic_anger",
    ]
    assert resolve_expression("comic-anger", options) == "comic_anger"
    assert resolve_expression("NONE", options) is None
    with pytest.raises(ExpressionError, match="offers: slow_surprise"):
        resolve_expression("slow", options)
    with pytest.raises(ExpressionError, match="schema"):
        expression_options({"schema_version": "v0", "reaction_kinds": []})
    with pytest.raises(ExpressionError, match="lists no expressions"):
        expression_options({"schema_version": "fictora.drama-capabilities.v1"})
    assert server_takes_expression(_openapi(reaction_kind=True)) is True
    assert server_takes_expression(_openapi(reaction_kind=False)) is False
    assert server_takes_expression(openapi_doc()) is None
