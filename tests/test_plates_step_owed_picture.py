"""The plates step never says "Cast drawn" when a character it owed came back without a picture.

Noodle24 ep 4 (L-20261007-5) and Gallery Heiress ep 2 (L-20261008-17): the
server answered a new character's plates step with episode 1's finished cast
run, nothing was drawn for them, and the kit printed "Cast drawn" anyway.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

from conftest import SHOWN_PRICES, set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.new_cast import cast_without_pictures
from creation.production_state import load_production
from fake_api import FakeApi

CAST = "/v1/spines/sp1/cast/enrol"


def _with_newcomer(spine: dict[str, Any]) -> dict[str, Any]:
    after = copy.deepcopy(spine)
    after["cast"].append(
        {"cast_id": "cast_kai", "name": "Kai", "intro_episode_ordinal": 2}
    )
    after["beats"][1]["dialogue_lines"][0]["cast_id"] = "cast_kai"
    return after


def _episode_two_with_kai(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    ec.run_author(desk, episode=2, out=io.StringIO())
    api.spine_doc = _with_newcomer(api.spine_doc)
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    api.jobs["job_cast"] = {"status": "completed"}


def test_a_newcomer_left_without_a_picture_stops_the_step_and_says_how_to_draw_them(
    desk: Path, api: FakeApi
) -> None:
    _episode_two_with_kai(desk, api)
    # The old server: episode 1's finished cast run comes back, nothing new is drawn.
    api.routes[("POST", CAST)] = {"job_id": "job_cast"}

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert api.posted(CAST)
    assert "Cast drawn" not in result.message
    assert "No picture came back for Kai" in result.message
    assert "redraw-plate --desk" in result.message and '--cast "Kai"' in result.message
    assert load_production(desk).phase == "ready_cast_enrol"


def test_a_newcomer_drawn_by_the_step_is_cast_drawn(desk: Path, api: FakeApi) -> None:
    _episode_two_with_kai(desk, api)

    def enrol(_m: str, _p: str, _body: dict[str, Any] | None) -> dict[str, Any]:
        api.spine_doc["media_assets"].append(
            {
                "relation_type": "cast_card",
                "relation_id": "cast_kai",
                "url": "https://r2.example/kai.png",
            }
        )
        return {"job_id": "job_cast"}

    api.routes[("POST", CAST)] = enrol

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert "Cast drawn" in result.message
    assert load_production(desk).phase == "wait_plates"


def test_a_stale_or_missing_picture_counts_as_none() -> None:
    spine = {
        "cast": [
            {"cast_id": "a", "name": "Ann"},
            {"cast_id": "b", "name": "Bo"},
            {"cast_id": "c", "name": "Cy"},
        ],
        "media_assets": [
            {
                "relation_type": "cast_card",
                "relation_id": "a",
                "url": "https://r2.example/a.png",
            },
            {
                "relation_type": "cast_card",
                "relation_id": "b",
                "url": "https://r2.example/b.png",
                "stale": True,
            },
        ],
    }

    assert cast_without_pictures(spine, ["a", "b", "c"]) == [("b", "Bo"), ("c", "Cy")]
    assert cast_without_pictures(spine, ["a"]) == []
