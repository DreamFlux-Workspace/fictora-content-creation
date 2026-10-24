"""A later episode's newcomer: the server says who owes a picture, so the kit never meets the boards' 422.

Founder, 24 Oct 2026 (follows fictora-drama #1000's cast growth): the kit only
learned a mid-series newcomer had no picture from ``cast_not_approved`` when it
asked for boards. fictora-drama now puts ``cast_pictures_owed`` on every spine
(the boards gate's own check); the kit reads it after `author`, at the script
yes and before boards. An older server without the field gets the old behaviour.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from conftest import set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.new_cast import (
    cast_owing_pictures,
    newcomers_owing_pictures,
    pictures_waiting_for_yes,
)
from creation.production_state import load_production
from fake_api import FakeApi

ENROL = "/v1/spines/sp1/boards/enrol"


def _owed(*items: tuple[str, str, int, str]) -> list[dict[str, Any]]:
    return [
        {
            "cast_id": cast_id,
            "name": name,
            "intro_episode_ordinal": intro,
            "reason": reason,
        }
        for cast_id, name, intro, reason in items
    ]


def _joined_outside_author(spine: dict[str, Any]) -> dict[str, Any]:
    """Kai joined from the app's chat: on the cast before `author`, no picture yet."""

    spine["cast"].append(
        {"cast_id": "cast_kai", "name": "Kai", "intro_episode_ordinal": 2}
    )
    spine["cast_pictures_owed"] = _owed(("cast_kai", "Kai", 2, "plate_missing"))
    return spine


def test_the_servers_list_wins_over_the_kits_own_read() -> None:
    # No intro episode on the card (the kit's read would miss it); the server names it.
    spine = {
        "cast": [{"cast_id": "cast_kai", "name": "Kai"}],
        "media_assets": [],
        "cast_pictures_owed": _owed(
            ("cast_kai", "Kai", 1, "plate_stale"),
            ("cast_mia", "Mia", 2, "never_approved"),
        ),
    }

    assert cast_owing_pictures(spine, episode=2) == [("cast_kai", "Kai")]
    assert pictures_waiting_for_yes(spine) == [("cast_mia", "Mia")]
    spine["cast_pictures_owed"] = []
    assert cast_owing_pictures(spine, episode=2) == []


def test_author_names_a_newcomer_who_joined_outside_author(
    desk: Path, api: FakeApi
) -> None:
    before = _joined_outside_author(api.spine_doc)

    assert newcomers_owing_pictures(before, before, episode=2) == [("cast_kai", "Kai")]

    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    out = io.StringIO()
    ec.run_author(desk, episode=2, out=out)

    assert (
        "new character added: Kai (cast_kai) — approve their picture before boards"
        in out.getvalue()
    )


def test_boards_stop_before_sending_when_the_server_says_a_picture_is_owed(
    desk: Path, api: FakeApi
) -> None:
    _joined_outside_author(api.spine_doc)
    set_phase(desk, "ready_boards_enrol")

    result = orchestrate.run_step(desk)

    assert (
        result.phase == "ready_cast_enrol"
        and load_production(desk).phase == "ready_cast_enrol"
    )
    assert (
        "Stopped before the boards; nothing was sent. The server says Kai need a picture first."
        in (result.message)
    )
    assert "asks before spending" in result.message
    assert api.posted(ENROL) == []


def test_a_drawn_picture_waiting_for_its_yes_asks_for_the_plates_yes(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc["cast_pictures_owed"] = _owed(
        ("cast_kai", "Kai", 2, "never_approved")
    )
    set_phase(desk, "ready_boards_enrol")

    result = orchestrate.run_step(desk)

    assert result.phase == "ready_boards_enrol"
    assert "The picture of Kai is drawn and waits for the human's yes" in result.message
    assert "--gate plates --again" in result.message
    assert api.posted(ENROL) == []


def test_a_story_object_waiting_for_its_yes_stops_the_boards_too(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc["cast_pictures_owed"] = []
    api.spine_doc["object_pictures_owed"] = [
        {"prop_id": "prop_plate", "name": "cracked white plate"}
    ]
    set_phase(desk, "ready_boards_enrol")

    result = orchestrate.run_step(desk)

    assert result.phase == "ready_boards_enrol"
    assert (
        "The picture of cracked white plate is drawn and waits for the human's yes"
        in result.message
    )
    assert "--gate plates --again" in result.message
    assert api.posted(ENROL) == []


def test_an_older_server_without_the_field_keeps_the_kits_own_read(
    desk: Path, api: FakeApi
) -> None:
    assert "cast_pictures_owed" not in api.spine_doc
    set_phase(desk, "ready_boards_enrol")

    result = orchestrate.run_step(desk)

    assert "Stopped before the boards" not in result.message
    assert "Nothing drawn yet" in result.message
