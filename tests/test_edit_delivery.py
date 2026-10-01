"""A beat's ``delivery`` is checked before it is sent, and its values are shown up front (L-20260923-3, L-20261001)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.patch_refusal import LINE_DELIVERIES, delivery_values
from fake_api import FakeApi, openapi_doc

ALL = (
    "laughing",
    "excited",
    "whispered",
    "shouted",
    "through_tears",
    "deadpan",
    "trailing_off",
    "breathless",
    "cold",
)


def _doc_with(values: list[str]) -> dict[str, Any]:
    doc = openapi_doc()
    doc["components"]["schemas"].update(
        {
            "DramaLineDelivery": {"type": "string", "enum": values},
            "DramaMotionDirection": {
                "properties": {
                    "delivery": {
                        "anyOf": [
                            {"$ref": "#/components/schemas/DramaLineDelivery"},
                            {"type": "null"},
                        ]
                    }
                }
            },
        }
    )
    return doc


def _with_delivery_field(api: FakeApi) -> None:
    """The server dumps ``delivery: null`` on every beat's motion direction."""

    for beat in api.spine_doc["beats"]:
        beat["motion_direction"].setdefault("delivery", None)


def _stop_at_preview(api: FakeApi) -> None:
    """Answer the cascade preview with a stop, so the test reads what was sent."""

    def stop(*_: Any) -> Any:
        raise SystemExit("HTTP 409: test_stop: stop here")

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = stop


def _sent_text(api: FakeApi) -> str:
    return json.dumps([body for m, _p, body, _k in api.calls if m in ("PATCH", "POST")])


def _sent(api: FakeApi) -> list[str]:
    return [f"{m} {p}" for m, p, *_ in api.calls if m in ("PATCH", "POST")]


def test_edit_help_lists_the_delivery_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        produce_main(["edit", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "delivery" in out
    for value in ALL:
        assert value in out


def test_the_fallback_list_is_the_servers_enum() -> None:
    assert tuple(LINE_DELIVERIES) == ALL
    assert delivery_values(None) == (list(ALL), "the kit's list")
    assert delivery_values(_doc_with(["cold", "sighing"])) == (
        ["cold", "sighing"],
        "this deploy's /openapi.json",
    )


def test_a_wrong_delivery_is_stopped_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("GET", "/openapi.json")] = _doc_with(list(ALL))

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_edit(
            desk,
            episode=1,
            beat="1",
            assignments=[("delivery", "whisper")],
            out=io.StringIO(),
        )

    text = str(stopped.value)
    assert "delivery 'whisper' is not one the server takes" in text
    assert "whispered" in text and "through_tears" in text
    assert "did you mean 'whispered'" in text
    assert _sent(api) == [], "nothing is sent"


def test_a_value_only_a_newer_deploy_lists_is_read_from_its_openapi(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("GET", "/openapi.json")] = _doc_with([*ALL, "sighing"])
    _with_delivery_field(api)
    _stop_at_preview(api)

    with pytest.raises(ec.CommandStopped, match="test_stop"):
        ec.run_edit(
            desk,
            episode=1,
            beat="1",
            assignments=[("delivery", "sighing")],
            out=io.StringIO(),
        )

    assert '"delivery": "sighing"' in _sent_text(api)


def test_without_the_schema_the_kits_list_is_used(desk: Path, api: FakeApi) -> None:
    api.routes[("GET", "/openapi.json")] = SystemExit("HTTP 404")

    with pytest.raises(ec.CommandStopped, match="the kit's list"):
        ec.run_edit(
            desk,
            episode=1,
            beat="1",
            assignments=[("delivery", "sobbing")],
            out=io.StringIO(),
        )
    assert _sent(api) == []


def test_null_clears_the_delivery_without_a_schema_read(
    desk: Path, api: FakeApi
) -> None:
    for beat in api.spine_doc["beats"]:
        beat["motion_direction"]["delivery"] = "cold"
    _stop_at_preview(api)

    with pytest.raises(ec.CommandStopped, match="test_stop"):
        ec.run_edit(
            desk,
            episode=1,
            beat="1",
            assignments=[("delivery", None)],
            out=io.StringIO(),
        )

    assert ("GET", "/openapi.json") not in [(m, p) for m, p, *_ in api.calls]


def test_delivery_on_a_frame_is_pointed_at_the_beat(desk: Path, api: FakeApi) -> None:
    with pytest.raises(ec.CommandStopped, match="delivery is a beat field"):
        ec.run_edit(
            desk,
            episode=1,
            frame="1",
            assignments=[("delivery", "cold")],
            out=io.StringIO(),
        )
    assert _sent(api) == []
