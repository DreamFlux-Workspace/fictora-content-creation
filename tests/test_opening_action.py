"""`--opening-action`: how an episode's first shot moves, sent on the draft (ep 1) and on `author` (ep 2 on).

Approved item #6: cell 1 often drew a still pose and the action came ~2.5 s in
(L-20261005-17). The server pins beat 1 and the board's first cell to the
human's opening action; the kit sends it only to a deploy that takes it.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.cli_config import (
    add_production_config_args,
    config_from_args,
    merge_config_from_args,
)
from creation.harness.stages_gated import draft_brief_hash, draft_request_body
from creation.opening_action import (
    AUTHOR_SCHEMA,
    DRAFT_SCHEMA,
    OPENING_ACTION_LIMIT,
    OpeningActionRefused,
    clean_opening_action,
    schema_takes_opening_action,
)
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)
from fake_api import FakeApi, openapi_doc

AUTHOR = ("POST", "/v1/spines/sp1/pilot-episodes/2/author")
DRAFTS = "/v1/prompt-video-authoring-drafts"
KICK = "Mina kicks the kitchen door open"


def _doc(schema: str, *, takes: bool) -> dict[str, Any]:
    doc = openapi_doc()
    doc["components"]["schemas"][schema] = {
        "properties": {"spine_version": {}, **({"opening_action": {}} if takes else {})}
    }
    return doc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    add_production_config_args(parser)
    return parser


# --- the words ------------------------------------------------------------------------------


def test_the_action_is_one_line_blank_is_none_and_it_is_bounded() -> None:
    assert clean_opening_action("  Mina   kicks\nthe door ") == "Mina kicks the door"
    assert clean_opening_action("   ") is None
    assert clean_opening_action(None) is None
    assert clean_opening_action("a" * OPENING_ACTION_LIMIT)
    with pytest.raises(OpeningActionRefused, match="at most 200"):
        clean_opening_action("a" * (OPENING_ACTION_LIMIT + 1))


def test_the_schema_check_reads_the_deploy_s_own_request() -> None:
    assert (
        schema_takes_opening_action(_doc(AUTHOR_SCHEMA, takes=True), AUTHOR_SCHEMA)
        is True
    )
    assert (
        schema_takes_opening_action(_doc(AUTHOR_SCHEMA, takes=False), AUTHOR_SCHEMA)
        is False
    )
    assert schema_takes_opening_action(openapi_doc(), DRAFT_SCHEMA) is None
    assert schema_takes_opening_action("not a document", DRAFT_SCHEMA) is None


# --- episode 1: on the draft ------------------------------------------------------------------


def test_the_flag_is_kept_in_the_desk_config_and_a_rebind_without_it_keeps_it(
    tmp_path: Path,
) -> None:
    config = config_from_args(_parser().parse_args(["--opening-action", f"  {KICK} "]))
    assert config.opening_action == KICK
    save_production_config(tmp_path, config)
    assert load_production_config(tmp_path).opening_action == KICK
    assert config_from_args(_parser().parse_args([])).opening_action is None
    kept = merge_config_from_args(
        config, _parser().parse_args(["--cut-tempo", "punchy"])
    )
    assert kept.opening_action == KICK
    replaced = merge_config_from_args(
        config, _parser().parse_args(["--opening-action", "He runs"])
    )
    assert replaced.opening_action == "He runs"


def test_a_draft_without_one_is_byte_for_byte_the_old_body() -> None:
    old = draft_request_body(prompt="p", preset_id="x", preset_version="1")
    body = draft_request_body(
        prompt="p", preset_id="x", preset_version="1", opening_action=None
    )
    assert "opening_action" not in body
    assert draft_brief_hash(body) == draft_brief_hash(old), (
        "a changed hash would re-draft every existing desk"
    )
    sent = draft_request_body(
        prompt="p", preset_id="x", preset_version="1", opening_action=KICK
    )
    assert sent["opening_action"] == KICK


def _new_desk(desk: Path, api: FakeApi, action: str) -> None:
    save_production_config(desk, ProductionConfig(opening_action=action))
    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
    }
    api.routes[("POST", DRAFTS)] = {"plan_job_id": "job_plan", "spine_id": "sp1"}
    api.jobs["job_plan"] = {"status": "completed"}
    set_phase(desk, "new", spine_id=None)


def test_step_new_sends_the_opening_action_on_the_draft(
    desk: Path, api: FakeApi
) -> None:
    _new_desk(desk, api, KICK)
    api.routes[("GET", "/openapi.json")] = _doc(DRAFT_SCHEMA, takes=True)

    orchestrate.run_step(desk)

    posted = api.posted(DRAFTS)
    assert posted and posted[0]["opening_action"] == KICK


def test_a_server_without_the_field_stops_the_draft_before_sending(
    desk: Path, api: FakeApi
) -> None:
    _new_desk(desk, api, KICK)
    api.routes[("GET", "/openapi.json")] = _doc(DRAFT_SCHEMA, takes=False)

    with pytest.raises(
        (SystemExit, RuntimeError), match="does not take an opening action"
    ):
        orchestrate.run_step(desk)

    assert api.posted(DRAFTS) == []


# --- episode 2 on: on author --------------------------------------------------------------------


def _ready(api: FakeApi) -> None:
    api.routes[AUTHOR] = {"extension_job_id": "job_ext_2", "status": "queued"}
    api.jobs["job_ext_2"] = {"status": "completed"}


def test_author_sends_the_opening_action(desk: Path, api: FakeApi) -> None:
    _ready(api)
    api.routes[("GET", "/openapi.json")] = _doc(AUTHOR_SCHEMA, takes=True)

    ec.run_author(
        desk, episode=2, opening_action=" He slams  the phone down ", out=io.StringIO()
    )

    assert api.posted(AUTHOR[1]) == [
        {"spine_version": "v5", "opening_action": "He slams the phone down"}
    ]


def test_author_without_one_sends_the_old_body_and_reads_no_schema(
    desk: Path, api: FakeApi
) -> None:
    _ready(api)

    ec.run_author(desk, episode=2, out=io.StringIO())

    assert api.posted(AUTHOR[1]) == [{"spine_version": "v5"}]
    assert not [call for call in api.calls if call[:2] == ("GET", "/openapi.json")]


def test_author_stops_before_sending_to_a_server_without_the_field(
    desk: Path, api: FakeApi
) -> None:
    _ready(api)
    api.routes[("GET", "/openapi.json")] = _doc(AUTHOR_SCHEMA, takes=False)

    with pytest.raises(ec.CommandStopped, match="does not take an opening action"):
        ec.run_author(desk, episode=2, opening_action=KICK, out=io.StringIO())

    assert api.posted(AUTHOR[1]) == []


def test_author_refuses_an_overlong_action_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    _ready(api)

    with pytest.raises(ec.CommandStopped, match="at most 200"):
        ec.run_author(desk, episode=2, opening_action="x" * 201, out=io.StringIO())

    assert api.posted(AUTHOR[1]) == []
