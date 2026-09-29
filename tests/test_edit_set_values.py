"""`edit --set`: JSON string values, list indices, and the server's bare `invalid_patch` explained (Hanakaze ep 2-3)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.patch_refusal import allowed_values, changed_fields
from fake_api import FakeApi, openapi_doc, spine_fixture

REFUSED = SystemExit("HTTP 400: invalid_patch: The cascade preview edit is invalid.")


def test_a_json_string_value_is_stored_without_its_quotes_and_raw_text_still_works() -> (
    None
):
    assert ec.parse_assignment('shot_scale="extreme close-up"') == (
        "shot_scale",
        "extreme close-up",
    )
    assert ec.parse_assignment("shot_scale=extreme close-up") == (
        "shot_scale",
        "extreme close-up",
    )
    assert ec.parse_assignment('story_objects=["a cup", "a tray"]') == (
        "story_objects",
        ["a cup", "a tray"],
    )
    assert ec.parse_assignment("reaction_kind=null") == ("reaction_kind", None)
    # A number stays text: every brief field is text.
    assert ec.parse_assignment("viewpoint=3") == ("viewpoint", "3")
    with pytest.raises(ec.CommandStopped, match="looks like JSON but does not parse"):
        ec.parse_assignment('shot_scale="close up')


def test_a_numeric_key_part_indexes_a_list_and_the_change_names_that_field() -> None:
    patch, changed = ec.build_patch(
        spine_fixture(),
        episode=1,
        frame="frame_episode_01_01",
        assignments=[
            ec.parse_assignment('subject_blocking.0.frame_position="left third"')
        ],
    )
    brief = patch["frames"][0]["visual_brief"]
    assert brief["subject_blocking"] == [
        {"cast_id": "cast_hana", "frame_position": "left third"}
    ]
    assert changed == [
        "  visual_brief.subject_blocking.0.frame_position: upper third  ->  left third"
    ]
    patch, _ = ec.build_patch(
        spine_fixture(),
        episode=1,
        frame="frame_episode_01_01",
        assignments=[ec.parse_assignment("story_objects.0=a lacquer tray")],
    )
    assert patch["frames"][0]["visual_brief"]["story_objects"] == ["a lacquer tray"]


def test_a_bad_list_index_is_refused_before_anything_is_sent() -> None:
    with pytest.raises(ec.CommandStopped, match=r"has 1 item\(s\); 3 is past the end"):
        ec.build_patch(
            spine_fixture(),
            episode=1,
            frame="1",
            assignments=[("subject_blocking.3.pose", "x")],
        )
    with pytest.raises(
        ec.CommandStopped, match="is a list, so 'pose' must be a number"
    ):
        ec.build_patch(
            spine_fixture(),
            episode=1,
            frame="1",
            assignments=[("subject_blocking.pose", "x")],
        )


def _schema_doc() -> dict[str, Any]:
    doc = openapi_doc()
    doc["components"]["schemas"].update(
        {
            "DramaFrameCellRole": {
                "type": "string",
                "enum": ["anchor", "reaction", "action", "insert", "cover"],
            },
            "DramaFrameVisualBrief": {
                "properties": {
                    "shot_scale": {"type": "string"},
                    "cell_role": {
                        "anyOf": [
                            {"$ref": "#/components/schemas/DramaFrameCellRole"},
                            {"type": "null"},
                        ]
                    },
                }
            },
        }
    )
    return doc


def test_the_schema_names_an_enum_fields_values() -> None:
    assert allowed_values(_schema_doc(), "DramaFrameVisualBrief", "cell_role") == [
        "anchor",
        "reaction",
        "action",
        "insert",
        "cover",
    ]
    assert allowed_values(_schema_doc(), "DramaFrameVisualBrief", "shot_scale") is None
    assert allowed_values(None, "DramaFrameVisualBrief", "cell_role") is None
    assert changed_fields(
        [
            "  visual_brief.cell_role: action  ->  hero",
            "  motion_direction.camera: a  ->  b",
        ]
    ) == ["cell_role", "camera"]


def _refuse_preview(api: FakeApi) -> None:
    def refuse(*_: Any) -> Any:
        raise REFUSED

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = refuse


def test_a_bare_invalid_patch_names_the_changed_field_and_what_the_server_takes(
    desk: Path, api: FakeApi
) -> None:
    _refuse_preview(api)
    api.routes[("GET", "/openapi.json")] = _schema_doc()

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_edit(
            desk,
            episode=1,
            frame="frame_episode_01_02",
            assignments=[ec.parse_assignment("cell_role=hero")],
            out=io.StringIO(),
        )

    text = str(stopped.value)
    assert "invalid_patch: The cascade preview edit is invalid." in text
    assert "visual_brief on frame_episode_01_02" in text
    assert "this edit changed: cell_role" in text
    assert (
        "cell_role = 'hero' is not a value the server takes: anchor, reaction, action, insert, cover"
        in text
    )
    # Not the line-edit fix the same code means on a line edit.
    assert "an id is not on the story" not in text


def test_without_a_readable_schema_the_known_strict_fields_are_named(
    desk: Path, api: FakeApi
) -> None:
    _refuse_preview(api)
    api.routes[("GET", "/openapi.json")] = SystemExit("HTTP 404")
    for frame in api.spine_doc["frames"]:
        frame["visual_brief"]["reaction_kind"] = None  # stored briefs carry the key

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_edit(
            desk,
            episode=1,
            frame="frame_episode_01_02",
            assignments=[("reaction_kind", "surprised"), ("cell_role", "hero")],
            out=io.StringIO(),
        )

    text = str(stopped.value)
    assert "cell_role = 'hero' is not a value the server takes" in text
    assert "reaction_kind: a kind from the deploy's expression library" in text
    assert "use `--expression` instead of --set" in text


def test_a_line_edit_refusal_keeps_its_own_fix(desk: Path, api: FakeApi) -> None:
    _refuse_preview(api)

    with pytest.raises(ec.CommandStopped, match="an id is not on the story"):
        ec.run_edit(
            desk,
            episode=1,
            line_id="line_episode_01_01",
            text="We're shut.",
            out=io.StringIO(),
        )


# --- the server names the rule (fictora-drama #498); who is in a shot ---------------------------------


def _refuse_preview_with(api: FakeApi, text: str) -> None:
    def refuse(*_: Any) -> Any:
        raise SystemExit(text)

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = refuse


def _openapi_reads(api: FakeApi) -> int:
    return sum(1 for m, p, _, _ in api.calls if (m, p) == ("GET", "/openapi.json"))


def test_the_servers_named_rule_is_printed_instead_of_the_kits_guess(
    desk: Path, api: FakeApi
) -> None:
    _refuse_preview_with(
        api,
        "HTTP 400: invalid_patch: The cascade preview edit is invalid: edit.patch.frames.0.visual_brief: "
        "reaction_cast_id 'cast_ren' is not in this cell's subject blocking (details "
        '{"validation_errors": [{"loc": ["body", "edit", "patch", "frames", 0, "visual_brief"], '
        '"msg": "reaction_cast_id \'cast_ren\' is not in this cell\'s subject blocking", "type": "value_error"}]})',
    )

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_edit(
            desk,
            episode=1,
            frame="frame_episode_01_02",
            assignments=[("cell_role", "reaction")],
            out=io.StringIO(),
        )

    text = str(stopped.value)
    assert "the server named what is wrong:" in text
    assert (
        "edit.patch.frames.0.visual_brief: reaction_cast_id 'cast_ren' is not in this cell's subject blocking"
        in text
    )
    assert "likely at fault" not in text, "no guess when the server said"
    assert _openapi_reads(api) == 0


def test_an_older_servers_cast_rule_says_to_edit_the_blocking(
    desk: Path, api: FakeApi
) -> None:
    _refuse_preview_with(
        api,
        "HTTP 400: invalid_patch: The story spine patch violates the spine contract. (details "
        '{"violations": [{"loc": ["frames"], "msg": "Value error, frame f1 visual brief subject blocking '
        'must match cast_refs"}]})',
    )

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_edit(
            desk,
            episode=1,
            frame="frame_episode_01_02",
            assignments=[("cast_refs", ["Ren"])],
            out=io.StringIO(),
        )

    assert (
        "older than fictora-drama #498: change who is in the shot through subject_blocking"
        in str(stopped.value)
    )


def test_a_named_frame_cast_refusal_gets_its_fix(desk: Path, api: FakeApi) -> None:
    _refuse_preview_with(
        api,
        "HTTP 400: frame_cast_needs_staging: A character added to a shot needs staging (details "
        '{"frame_id": "frame_episode_01_02", "cast_ids": ["cast_ren"]})',
    )

    with pytest.raises(ec.CommandStopped, match="needs staging: send subject_blocking"):
        ec.run_edit(
            desk,
            episode=1,
            frame="frame_episode_01_02",
            assignments=[("cast_refs", ["Hana", "Ren"])],
            out=io.StringIO(),
        )


REN = {
    "cast_id": "cast_ren",
    "frame_position": "right third",
    "pose": "arms crossed",
    "gaze": "at Hana",
    "interaction": "waits",
}


def test_restaging_the_shot_sends_the_whole_brief_and_cast_refs_to_match() -> None:
    spine = spine_fixture()
    before = dict(spine["frames"][0]["visual_brief"])
    blocking = [*before["subject_blocking"], REN]

    patch, changed = ec.build_patch(
        spine,
        episode=1,
        frame="frame_episode_01_01",
        assignments=[("subject_blocking", blocking)],
    )

    entry = patch["frames"][0]
    assert entry["cast_refs"] == ["cast_hana", "cast_ren"]
    # The whole current brief goes (the server replaces it), with only the blocking changed.
    assert entry["visual_brief"] == {**before, "subject_blocking": blocking}
    assert "  cast_refs: Hana  ->  Hana, Ren" in changed


def test_a_brief_edit_that_keeps_the_same_people_sends_no_cast_refs() -> None:
    patch, _ = ec.build_patch(
        spine_fixture(),
        episode=1,
        frame="frame_episode_01_01",
        assignments=[("subject_blocking.0.frame_position", "left third")],
    )
    assert "cast_refs" not in patch["frames"][0]


def test_cast_refs_alone_sends_no_brief_and_takes_names() -> None:
    spine = spine_fixture()
    spine["frames"][0]["cast_refs"] = ["cast_hana", "cast_ren"]
    spine["frames"][0]["visual_brief"]["subject_blocking"].append(REN)

    patch, changed = ec.build_patch(
        spine,
        episode=1,
        frame="frame_episode_01_01",
        assignments=[ec.parse_assignment('cast_refs=["Hana"]')],
    )

    assert patch == {
        "frames": [{"frame_id": "frame_episode_01_01", "cast_refs": ["cast_hana"]}]
    }
    assert changed == ["  cast_refs: Hana, Ren  ->  Hana"]


def test_cast_refs_and_blocking_naming_different_people_are_refused_before_sending() -> (
    None
):
    with pytest.raises(ec.CommandStopped, match="name different people"):
        ec.build_patch(
            spine_fixture(),
            episode=1,
            frame="frame_episode_01_01",
            assignments=[
                ("subject_blocking", [REN]),
                ("cast_refs", ["Hana"]),
            ],
        )
    with pytest.raises(ec.CommandStopped, match="JSON list of cast names or ids"):
        ec.build_patch(
            spine_fixture(), episode=1, frame="1", assignments=[("cast_refs", "Hana")]
        )
