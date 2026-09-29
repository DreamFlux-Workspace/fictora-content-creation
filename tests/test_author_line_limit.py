"""`author --line`: counted against the server's limit before sending (a 401+ character line got a 422)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.ops.state import load_series
from fake_api import FakeApi, openapi_doc

AUTHOR = ("POST", "/v1/spines/sp1/pilot-episodes/2/author")


def _limit_doc(limit: int) -> dict[str, Any]:
    doc = openapi_doc()
    doc["components"]["schemas"]["DramaPilotEpisodeDirection"] = {
        "properties": {"line": {"type": "string", "minLength": 1, "maxLength": limit}}
    }
    return doc


def _ready(api: FakeApi) -> None:
    api.routes[AUTHOR] = {"extension_job_id": "job_ext_2", "status": "queued"}
    api.jobs["job_ext_2"] = {"status": "completed"}


def _openapi_reads(api: FakeApi) -> int:
    return sum(
        1
        for method, path, _, _ in api.calls
        if (method, path) == ("GET", "/openapi.json")
    )


def test_a_line_over_the_limit_is_refused_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    _ready(api)
    api.routes[("GET", "/openapi.json")] = _limit_doc(400)
    line = "x" * 401
    slots = len(load_series(desk).episodes)

    with pytest.raises(
        ec.CommandStopped,
        match=r"--line is 401 characters; the server takes at most 400 \(this deploy's /openapi.json\)",
    ):
        ec.run_author(desk, episode=2, direction={"line": line}, out=io.StringIO())

    assert api.posted(AUTHOR[1]) == []
    assert len(load_series(desk).episodes) == slots, "no desk slot is opened"


def test_a_deploy_that_raised_the_limit_takes_the_longer_line(
    desk: Path, api: FakeApi
) -> None:
    _ready(api)
    api.routes[("GET", "/openapi.json")] = _limit_doc(800)
    line = "y" * 650

    ec.run_author(desk, episode=2, direction={"line": line}, out=io.StringIO())

    assert api.posted(AUTHOR[1]) == [
        {"spine_version": "v5", "direction": {"line": line}}
    ]


def test_the_known_limit_holds_when_the_schema_does_not_say(
    desk: Path, api: FakeApi
) -> None:
    _ready(api)
    api.routes[("GET", "/openapi.json")] = SystemExit("HTTP 404")

    with pytest.raises(ec.CommandStopped, match=r"at most 400 \(the known limit"):
        ec.run_author(desk, episode=2, direction={"line": "z" * 420}, out=io.StringIO())
    assert api.posted(AUTHOR[1]) == []


def test_a_short_line_is_sent_without_reading_the_schema(
    desk: Path, api: FakeApi
) -> None:
    _ready(api)

    ec.run_author(
        desk, episode=2, direction={"line": "Ren apologises."}, out=io.StringIO()
    )

    assert _openapi_reads(api) == 0
    assert api.posted(AUTHOR[1])[0]["direction"] == {"line": "Ren apologises."}


def test_direction_line_limit_reads_max_length() -> None:
    assert ec.direction_line_limit(_limit_doc(800)) == 800
    assert ec.direction_line_limit(openapi_doc()) is None
    assert ec.direction_line_limit(None) is None
