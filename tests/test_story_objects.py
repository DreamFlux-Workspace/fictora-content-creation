"""Story objects keep one look: the plates gate draws and shows their picture (fictora-drama story_props, 9 Oct 2026).

Hanakaze's carved 営業中 sign came back as a painted pink sakura on a board. The
server gives the objects a story turns on a prop card and ONE picture, drawn
with the plates (one still each, never per take) and approved with them. The
kit says which objects an episode added, routes an episode with an owed object
picture through the plates gate, saves the picture beside the plates, and
books only the pictures drawn.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

import pytest

from conftest import SHOWN_PRICES, set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.ops.state import episode_by_ordinal, load_series
from creation.production_state import load_production
from creation.story_objects import (
    new_objects,
    new_objects_notice,
    object_picture_urls,
    objects_owing_approval,
    objects_to_draw,
)
from fake_api import FakeApi

CAST = "/v1/spines/sp1/cast/enrol"
SIGN = {
    "prop_id": "prop_wooden_sign",
    "name": "wooden 営業中 sign",
    "object_kind": "sign",
    "scale_clause": "a shop sign",
    "intro_episode_ordinal": 2,
}


def _picture(**over: Any) -> dict[str, Any]:
    return {
        "relation_type": "prop",
        "relation_id": "prop_wooden_sign",
        "kind": "still",
        "url": "https://r2.example/sign.png",
        "stale": False,
        "review_state": "pending",
        **over,
    }


def test_an_object_is_drawn_once_and_then_only_waits_for_its_yes() -> None:
    spine: dict[str, Any] = {"props": [SIGN], "media_assets": []}
    assert objects_to_draw(spine, episode=1) == []  # its first episode is 2
    assert objects_to_draw(spine, episode=2) == [
        ("prop_wooden_sign", "wooden 営業中 sign")
    ]
    spine["media_assets"] = [_picture()]
    assert objects_to_draw(spine, episode=2) == []
    assert objects_owing_approval(spine, episode=2) == [
        ("prop_wooden_sign", "wooden 営業中 sign")
    ]
    assert object_picture_urls(spine) == [
        ("object-wooden-sign", "https://r2.example/sign.png")
    ]
    spine["media_assets"] = [_picture(review_state="approved")]
    assert objects_owing_approval(spine, episode=2) == []
    # A redraw asked for: drawn again, not shown as current.
    spine["media_assets"] = [_picture(review_state="approved", stale=True)]
    assert objects_to_draw(spine, episode=2) == [
        ("prop_wooden_sign", "wooden 営業中 sign")
    ]
    assert object_picture_urls(spine) == []


def test_a_server_without_story_objects_changes_nothing() -> None:
    spine = {"cast": [], "media_assets": []}
    assert objects_to_draw(spine, episode=3) == []
    assert objects_owing_approval(spine, episode=3) == []
    assert new_objects(spine, spine) == []


def test_author_says_which_objects_the_episode_added() -> None:
    lines = new_objects_notice(new_objects({"props": []}, {"props": [SIGN]}))
    assert len(lines) == 1
    assert "story object added: wooden 営業中 sign (prop_wooden_sign)" in lines[0]
    assert "~$0.30, once" in lines[0]


def _episode_two_with_sign(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    api.spine_doc = {**copy.deepcopy(api.spine_doc), "props": [SIGN]}
    ec.run_author(desk, episode=2, out=io.StringIO())
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    api.jobs["job_cast"] = {"status": "completed"}


def test_the_plates_step_draws_the_owed_object_saves_it_and_books_one_picture(
    desk: Path, api: FakeApi
) -> None:
    _episode_two_with_sign(desk, api)

    def enrol(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        api.spine_doc["media_assets"].append(_picture())
        return {"job_id": "job_cast"}

    api.routes[("POST", CAST)] = enrol
    before = episode_by_ordinal(load_series(desk), 2).spend_usd

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert load_production(desk).phase == "wait_plates"
    downloads = [payload["url"] for phase, payload in api.events if phase == "download"]
    assert "https://r2.example/sign.png" in downloads
    assert any("object-wooden" in path for path in result.paths)
    assert episode_by_ordinal(load_series(desk), 2).spend_usd - before == pytest.approx(
        0.30
    )


def test_the_script_yes_routes_an_owed_object_picture_through_plates(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    ec.run_author(desk, episode=2, out=io.StringIO())
    api.spine_doc = {**api.spine_doc, "props": [SIGN]}
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/approve")] = {
        "spine_version": "v6"
    }

    scripted = orchestrate.approve_gate(desk, gate="script")

    assert load_production(desk).phase == "ready_cast_enrol"
    assert (
        "Story object(s) wooden 営業中 sign need a picture before boards"
        in scripted.message
    )
    assert "(~$0.30)" in scripted.message


def test_author_prints_the_objects_the_episode_added(desk: Path, api: FakeApi) -> None:
    before = copy.deepcopy(api.spine_doc)

    def author(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        api.spine_doc = {**copy.deepcopy(before), "props": [SIGN]}
        return {"extension_job_id": "job_ext_2"}

    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = author
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    out = io.StringIO()

    ec.run_author(desk, episode=2, out=out)

    assert "story object added: wooden 営業中 sign (prop_wooden_sign)" in out.getvalue()
