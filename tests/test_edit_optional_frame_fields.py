"""``edit --frame --set`` may set optional visual_brief fields a saved frame leaves out."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.episode_commands import CommandStopped, _apply
from fake_api import FakeApi, openapi_doc, spine_fixture


def test_story_signs_can_be_set_on_a_frame_that_has_none() -> None:
    brief = {"story_moment": "Ren hangs the sign back", "location": "doorway"}
    signs = [{"text": "営業中", "where": "the wooden shop sign on the beam"}]

    _apply(brief, "story_signs", signs, label="visual_brief")

    assert brief["story_signs"] == signs


def test_an_unknown_field_is_still_refused() -> None:
    brief = {"story_moment": "x"}

    with pytest.raises(CommandStopped, match="has no field 'story_sign'"):
        _apply(brief, "story_sign", [], label="visual_brief")


def test_optional_fields_are_only_for_the_visual_brief_top_level() -> None:
    blocking = {"pose": "standing"}

    with pytest.raises(CommandStopped, match="has no field 'story_signs'"):
        _apply(blocking, "story_signs", [], label="visual_brief.subject_blocking.0")


# --- a field the saved frame lacks, from the deploy's schema; every key checked first (L-20261006-36) ---


def _brief_schema(*fields: str) -> dict[str, Any]:
    doc = openapi_doc()
    doc["components"]["schemas"]["DramaFrameVisualBrief"] = {
        "properties": {name: {"type": "string"} for name in fields}
    }
    return doc


def _frame(api: FakeApi, frame_id: str = "frame_episode_01_02") -> dict[str, Any]:
    if api.spine_doc.get("approval_state") == "approved":
        api.spine_doc = spine_fixture(
            approved=False
        )  # before the script gate: a plain PATCH
        api.routes[("PATCH", "/v1/spines/sp1")] = {"spine_version": "v6"}
    return next(f for f in api.spine_doc["frames"] if f["frame_id"] == frame_id)


def _sent(api: FakeApi) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _, _ in api.calls if m in {"PATCH", "POST"}]


def test_expression_cause_can_be_set_on_a_frame_that_lacks_it(
    desk: Path, api: FakeApi
) -> None:
    """The mood-jump advice says `--set expression_cause="..."`; the saved frame leaves it out."""

    brief = _frame(api)["visual_brief"]
    brief.pop("expression_cause", None)
    api.routes[("GET", "/openapi.json")] = _brief_schema(
        *brief, "expression_cause", "story_signs"
    )
    out = io.StringIO()

    ec.run_edit(
        desk,
        episode=1,
        frame="frame_episode_01_02",
        assignments=[
            ec.parse_assignment("expression_cause=the letter falls from her hand"),
            ec.parse_assignment("shot_scale=close up"),
        ],
        out=out,
    )

    (patch,) = [body["patch"] for m, _, body, _ in api.calls if m == "PATCH" and body]
    sent = patch["frames"][0]["visual_brief"]
    assert sent["expression_cause"] == "the letter falls from her hand"
    assert sent["shot_scale"] == "close up"


def test_a_schema_field_the_kit_does_not_know_is_still_settable(
    desk: Path, api: FakeApi
) -> None:
    brief = _frame(api)["visual_brief"]
    api.routes[("GET", "/openapi.json")] = _brief_schema(*brief, "weather_note")

    ec.run_edit(
        desk,
        episode=1,
        frame="frame_episode_01_02",
        assignments=[ec.parse_assignment("weather_note=light rain")],
        out=io.StringIO(),
    )

    (patch,) = [body["patch"] for m, _, body, _ in api.calls if m == "PATCH" and body]
    assert patch["frames"][0]["visual_brief"]["weather_note"] == "light rain"


def test_an_unknown_key_refuses_the_whole_command_and_sends_nothing(
    desk: Path, api: FakeApi
) -> None:
    brief = _frame(api)["visual_brief"]
    api.routes[("GET", "/openapi.json")] = _brief_schema(*brief, "expression_cause")

    with pytest.raises(CommandStopped) as stopped:
        ec.run_edit(
            desk,
            episode=1,
            frame="frame_episode_01_02",
            assignments=[
                ec.parse_assignment("shot_scale=close up"),
                ec.parse_assignment("expresion_cause=typo"),
                ec.parse_assignment("mood=sad"),
            ],
            out=io.StringIO(),
        )

    text = str(stopped.value)
    assert "no field 'expresion_cause', 'mood'" in text, text
    assert "Nothing was sent" in text
    assert _sent(api) == [], "all or nothing: not even shot_scale goes"


def test_without_a_readable_schema_the_kits_list_still_takes_expression_cause(
    desk: Path, api: FakeApi
) -> None:
    _frame(api)["visual_brief"].pop("expression_cause", None)
    api.routes[("GET", "/openapi.json")] = SystemExit("HTTP 404")

    ec.run_edit(
        desk,
        episode=1,
        frame="frame_episode_01_02",
        assignments=[ec.parse_assignment("expression_cause=the door opens")],
        out=io.StringIO(),
    )

    (patch,) = [body["patch"] for m, _, body, _ in api.calls if m == "PATCH" and body]
    assert patch["frames"][0]["visual_brief"]["expression_cause"] == "the door opens"
