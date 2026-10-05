"""Letterbox is asked for on the draft; portrait drafts are sent exactly as before."""

from __future__ import annotations

import argparse
from pathlib import Path

from conftest import set_phase
from creation import orchestrate
from creation.cli_config import add_production_config_args, config_from_args
from creation.harness.stages_gated import draft_brief_hash, draft_request_body
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)
from fake_api import FakeApi


def test_a_letterbox_draft_carries_the_delivery_format() -> None:
    body = draft_request_body(
        prompt="p", preset_id="x", preset_version="1", delivery_format="letterbox"
    )
    assert body["delivery_format"] == "letterbox"


def test_a_portrait_draft_is_byte_for_byte_the_old_body() -> None:
    old = draft_request_body(prompt="p", preset_id="x", preset_version="1")
    for portrait in (None, "portrait"):
        body = draft_request_body(
            prompt="p", preset_id="x", preset_version="1", delivery_format=portrait
        )
        assert "delivery_format" not in body
        assert body == old
        assert draft_brief_hash(body) == draft_brief_hash(old), (
            "a changed hash would re-draft every existing desk"
        )


def test_start_flag_is_kept_in_the_desk_config(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    add_production_config_args(parser)
    config = config_from_args(parser.parse_args(["--delivery-format", "letterbox"]))
    assert config.delivery_format == "letterbox"
    save_production_config(tmp_path, config)
    assert load_production_config(tmp_path).delivery_format == "letterbox"
    assert config_from_args(parser.parse_args([])).delivery_format is None


def test_step_new_sends_letterbox_on_the_draft(desk: Path, api: FakeApi) -> None:
    save_production_config(desk, ProductionConfig(delivery_format="letterbox"))
    api.spine_doc = {
        **api.spine_doc,
        "episode_summaries": api.spine_doc["episode_summaries"][:1],
    }
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }
    api.jobs["job_plan"] = {"status": "completed"}
    set_phase(desk, "new", spine_id=None)

    orchestrate.run_step(desk)

    posted = api.posted("/v1/prompt-video-authoring-drafts")
    assert posted and posted[0]["delivery_format"] == "letterbox"
