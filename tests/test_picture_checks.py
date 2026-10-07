"""The server's picture checks are printed at each gate, as warnings only (fictora-drama picture_checks)."""

from __future__ import annotations

import io
from pathlib import Path

from conftest import SHOWN_PRICES, set_phase
from creation import episode_commands as ec
from creation import orchestrate
from creation.picture_checks import (
    LOOK_BEFORE_APPROVING,
    picture_check_lines,
    plate_picture_check_lines,
)
from creation.post.review import picture_checks_section
from creation.production_state import load_production
from fake_api import FakeApi

BACK_OF_HEAD = {
    "kind": "speaker_face_hidden",
    "severity": "warn",
    "where": {"row": 3, "cast_id": "cast_ren"},
    "message": "row 3: Ren's line is drawn on the back of his head",
}
UNAVAILABLE = {
    "kind": "picture_check_unavailable",
    "severity": "warn",
    "where": {},
    "message": "picture check could not run (it took too long); look before approving",
}


def test_lines_are_one_warning_each_then_look_before_approving() -> None:
    assert picture_check_lines(None, what="t1 board") == []
    assert picture_check_lines([], what="t1 board") == [
        "picture check: t1 board: nothing found"
    ]
    assert picture_check_lines([BACK_OF_HEAD, UNAVAILABLE], what="t1 board") == [
        "!! picture check: t1 board: row 3: Ren's line is drawn on the back of his head",
        "!! picture check: t1 board: picture check could not run (it took too long); look before approving",
        LOOK_BEFORE_APPROVING,
    ]


def test_boards_print_the_check_after_the_shot_list_and_still_wait_for_the_yes(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc["media_assets"][0]["picture_checks"] = [BACK_OF_HEAD]
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", "/v1/spines/sp1/boards/enrol")] = {"job_id": "job_boards"}
    api.jobs["job_boards"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {
        "boards": [{"set_index": 1, "mean_percent": 14.2}]
    }

    result = orchestrate.run_step(desk, confirm_spend=True)

    message = result.message
    assert (
        "!! picture check: t1 board: row 3: Ren's line is drawn on the back of his head"
        in message
    )
    assert LOOK_BEFORE_APPROVING in message
    assert message.index("t1 board, row by row:") < message.index("!! picture check")
    assert load_production(desk).phase == "wait_board"


def test_an_older_server_prints_no_picture_check(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "ready_boards_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", "/v1/spines/sp1/boards/enrol")] = {"job_id": "job_boards"}
    api.jobs["job_boards"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {
        "boards": [{"set_index": 1, "mean_percent": 14.2}]
    }

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert "picture check" not in result.message


def test_the_plates_gate_prints_each_plates_check(desk: Path, api: FakeApi) -> None:
    for asset in api.spine_doc["media_assets"]:
        if asset.get("relation_id") == "cast_hana":
            asset["picture_checks"] = [
                {
                    "kind": "cold_skin",
                    "severity": "warn",
                    "where": {"cast_id": "cast_hana"},
                    "message": "Hana's skin reads ghost-cold (grey, blue or corpse-white); the card is a living person",
                }
            ]
        elif asset.get("relation_id") == "cast_ren":
            asset["picture_checks"] = []
    set_phase(desk, "ready_cast_enrol", drawing_estimates=SHOWN_PRICES)
    api.routes[("POST", "/v1/spines/sp1/cast/enrol")] = {"job_id": "job_cast"}
    api.jobs["job_cast"] = {"status": "completed"}

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert result.phase == "wait_plates"
    assert (
        "!! picture check: Hana's plate: Hana's skin reads ghost-cold" in result.message
    )
    assert "picture check: Ren's plate: nothing found" in result.message
    assert LOOK_BEFORE_APPROVING in result.message
    notes = (desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")
    assert "!! picture check: Hana's plate" in notes


def test_plate_lines_report_only_the_plates_asked_for(api: FakeApi) -> None:
    api.spine_doc["media_assets"][-1]["picture_checks"] = [UNAVAILABLE]

    assert plate_picture_check_lines(api.spine_doc, only=["cast_hana"]) == []
    assert plate_picture_check_lines(api.spine_doc, only=["cast_ren"])[0].startswith(
        "!! picture check: Ren's plate: picture check could not run"
    )


def _film_routes(api: FakeApi, facts_extra: dict) -> None:
    api.routes[("POST", "/v1/video-generations")] = {"job_id": "job_video_1"}
    api.routes[("GET", "/v1/jobs/job_video_1")] = {
        "status": "completed",
        "progress": 100,
        "depends_on": ["job_take_a"],
    }
    api.routes[("GET", "/v1/jobs/job_take_a")] = {
        "status": "completed",
        "episode_ids": ["episode_01"],
        "relation": {"id": "scene_episode_01_set01"},
        "result": {"video": {"url": "https://r2.example/ep1.mp4"}},
    }
    api.routes[("GET", "/v1/jobs/job_take_a/take-facts")] = {
        "take_facts": {
            "job_id": "job_take_a",
            "endpoint_id": "minimax/h3-max/reference-to-video",
            "resolution": "768P",
            "duration_seconds": 15.0,
            "reference_image_count": 3,
            "spoken_line_count": 0,
            **facts_extra,
        }
    }


def test_step_prints_each_takes_check_when_it_collects_the_take(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _film_routes(
        api,
        {
            "picture_checks": [
                {
                    "kind": "first_shot_framing",
                    "severity": "warn",
                    "where": {"shot": 1},
                    "message": "shot 1: opens wide; board row 1 is a close-up",
                }
            ]
        },
    )

    result = orchestrate.run_step(desk, confirm_spend=True)

    assert (
        "!! picture check: t1: shot 1: opens wide; board row 1 is a close-up"
        in result.message
    )
    assert LOOK_BEFORE_APPROVING.strip() in result.message


def test_review_carries_the_takes_picture_check_section() -> None:
    facts = {
        "take_facts": {
            "picture_checks": [
                {
                    "kind": "duplicate_person",
                    "message": "shot 2: the same person is shown twice",
                }
            ]
        }
    }

    section = picture_checks_section(facts)

    assert section is not None and section.status == "⚠"
    assert (
        section.details[0]
        == "picture check: take: shot 2: the same person is shown twice"
    )
    assert "look before approving" in section.details[-1]
    assert picture_checks_section({"take_facts": {}}) is None
    clean = picture_checks_section({"take_facts": {"picture_checks": []}})
    assert clean is not None and clean.status == "✓"


def test_look_frame_prints_its_check(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", "/v1/spines/sp1/look-frame")] = {
        "image_url": "https://cdn.example/look.png",
        "width": 1088,
        "height": 1936,
        "cached": False,
        "cost_usd": 0.3,
        "picture_checks": [
            {
                "kind": "person_mismatch",
                "message": "the look frame draws 1 woman; the description asks for 2 men",
            }
        ],
    }
    out = io.StringIO()

    ec.run_look_frame(desk, description="Two adult men at a bar, teal night.", out=out)

    printed = out.getvalue()
    assert (
        "!! picture check: look frame: the look frame draws 1 woman; the description asks for 2 men"
        in printed
    )
    assert LOOK_BEFORE_APPROVING in printed
