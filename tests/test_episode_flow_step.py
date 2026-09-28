"""The phase machine against a fake API: draft alone, per-episode boards/estimate/take/approvals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import set_phase
from creation import orchestrate
from creation.episode_commands import run_author
from creation.harness.stages_gated import draft_request_body, spoken_language_tag
from creation.ops.state import episode_by_ordinal, load_series
from creation.production_config import ProductionConfig, save_production_config
from creation.production_state import load_production
from fake_api import FakeApi


def _add_episode_two(desk: Path, api: FakeApi) -> None:
    """Write episode 2 through the author command so the desk points at it."""

    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {"extension_job_id": "job_ext_2"}
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    run_author(desk, episode=2)


# --- Draft ---------------------------------------------------------------------------------------


def test_the_draft_writes_episode_one_alone_with_the_shot_plan_and_language() -> None:
    body = draft_request_body(
        prompt="p", preset_id="x", preset_version="1", cut_tempo="punchy", spoken_language="ja", band="30s"
    )
    assert body["outline_mode"] == "arc_at_episode_two"
    assert body["episode_count"] == 1
    assert body["cut_tempo"] == "punchy"
    assert body["spoken_language"] == "ja-JP"
    assert body["model_overrides"] == {"video": "minimax-h3"}
    assert body["duration_band"] == "30s"
    assert "cut_tempo" not in draft_request_body(prompt="p", preset_id="x", preset_version="1")


def test_an_unsupported_language_is_refused_before_the_request() -> None:
    assert spoken_language_tag("KO") == "ko-KR"
    with pytest.raises(ValueError, match="not supported"):
        spoken_language_tag("fr")


def test_step_new_drafts_alone_syncs_lines_and_prints_the_script_gate(desk: Path, api: FakeApi) -> None:
    save_production_config(desk, ProductionConfig(cut_tempo="punchy"))
    api.spine_doc = {**api.spine_doc, "episode_summaries": api.spine_doc["episode_summaries"][:1]}
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {"plan_job_id": "job_plan", "spine_id": "sp1"}
    api.jobs["job_plan"] = {"status": "completed"}
    set_phase(desk, "new", spine_id=None)

    result = orchestrate.run_step(desk)

    body = api.posted("/v1/prompt-video-authoring-drafts")[0]
    assert body["outline_mode"] == "arc_at_episode_two" and body["episode_count"] == 1
    assert body["cut_tempo"] == "punchy"
    take = episode_by_ordinal(load_series(desk), 1).takes[0]
    assert [line.original for line in take.lines] == ["We're closed.", "Not for me."]
    assert "shot 1: Hana wipes the counter" in result.message
    assert (desk / "api" / "spine.json").is_file() and (desk / "ep01" / "api" / "spine.json").is_file()
    assert load_production(desk).phase == "ready_cast_enrol"


def test_a_failed_draft_says_the_rule_that_failed(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {"plan_job_id": "job_plan", "spine_id": "sp1"}
    api.jobs["job_plan"] = {
        "status": "failed",
        "error": {
            "code": "authoring_validation_failed",
            "message": "frame_ordinal at frames[3]: must form complete storyboard sets",
            "retryable": False,
            "details": {"errors": [{"loc": ["frames", 3]}]},
        },
    }
    set_phase(desk, "new", spine_id=None)

    with pytest.raises(SystemExit) as caught:
        orchestrate.run_step(desk)

    text = str(caught.value)
    assert "authoring_validation_failed" in text and "complete storyboard sets" in text
    assert len(api.posted("/v1/prompt-video-authoring-drafts")) == 1  # a deterministic failure is not retried
    assert load_production(desk).last_error == text


# --- Script, boards, estimate: episode 2 ------------------------------------------------------------


def test_episode_two_script_yes_goes_to_its_own_approve_route(desk: Path, api: FakeApi) -> None:
    _add_episode_two(desk, api)
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/approve")] = {"spine_version": "v6"}

    result = orchestrate.approve_gate(desk, gate="script")

    assert api.posted("/v1/spines/sp1/pilot-episodes/2/approve") == [{"spine_version": "v5", "episode_ordinal": 2}]
    assert api.posted("/v1/spines/sp1/approve") == []
    assert "Episode 2 script approved" in result.message
    assert load_production(desk).phase == "ready_boards_enrol"


def test_episode_two_boards_reach_episode_two_and_print_the_shot_list(desk: Path, api: FakeApi) -> None:
    _add_episode_two(desk, api)
    set_phase(desk, "ready_boards_enrol")
    api.routes[("POST", "/v1/spines/sp1/boards/enrol")] = {"job_id": "job_boards"}
    api.jobs["job_boards"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/2/boards/exposure")] = {"boards": [{"set_index": 1, "mean_percent": 14.2}]}

    result = orchestrate.run_step(desk)

    body = api.posted("/v1/spines/sp1/boards/enrol")[0]
    assert body["episode_count"] == 2
    downloads = [payload["url"] for phase, payload in api.events if phase == "download"]
    assert downloads == ["https://r2.example/ep_02-board.png"]
    assert "brightness 14.2% (server). Information only." in result.message
    assert "row 1: medium · eye level · front · camera dolly_in" in result.message
    assert "rows 1 and 2 share size and angle" in result.message
    assert "Hana's face is placed \"bottom edge, left\"" in result.message
    slot = episode_by_ordinal(load_series(desk), 2)
    assert slot.spend_usd == pytest.approx(0.30)
    assert slot.takes[0].estimate_usd == pytest.approx(1.20)
    state = load_production(desk)
    assert state.phase == "wait_board" and state.board_paths["t1"].startswith("ep02/boards/board-ep02-t1-v")


def test_a_dim_board_approves_without_accept_dim(desk: Path, api: FakeApi) -> None:
    _add_episode_two(desk, api)
    board = desk / "ep02" / "boards" / "board-ep02-t1-v1.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    from fake_api import png_bytes

    board.write_bytes(png_bytes(gray=20))
    set_phase(desk, "wait_board", board_paths={"t1": "ep02/boards/board-ep02-t1-v1.png"})
    api.routes[("GET", "/v1/spines/sp1/episodes/2/boards/exposure")] = {"boards": [{"set_index": 1, "mean_percent": 8.0}]}
    api.routes[("POST", "/v1/spines/sp1/episodes/2/boards/approve")] = {"ok": True}

    orchestrate.approve_gate(desk, gate="board")

    assert api.posted("/v1/spines/sp1/episodes/2/boards/approve") == [
        {"spine_version": "v5", "episode_ordinal": 2, "accept_dim": False}
    ]
    assert episode_by_ordinal(load_series(desk), 2).takes[0].board.status == "approved"
    assert load_production(desk).phase == "ready_estimate"


def test_the_estimate_prices_episode_two_alone_with_the_servers_dollars(desk: Path, api: FakeApi) -> None:
    _add_episode_two(desk, api)
    set_phase(desk, "ready_estimate")
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "episode_ids": ["ep_02"],
        "cost_estimate": {"total_usd": "1.50", "priced_on": "2026-09-28", "takes": 1},
    }

    result = orchestrate.run_step(desk)

    assert api.posted("/v1/spines/sp1/batches/estimate") == [{"spine_version": "v5", "episode_ids": ["ep_02"]}]
    assert load_production(desk).estimate_usd == pytest.approx(1.50)
    assert "server estimate priced 2026-09-28" in result.message
    assert "$1.50 of a $2.50 envelope for ep02" in result.message


def test_an_estimate_the_server_refuses_falls_back_to_the_price_table(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "ready_estimate")
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = SystemExit("HTTP 400 invalid_episode_selection: nope")

    result = orchestrate.run_step(desk)

    assert load_production(desk).estimate_usd == pytest.approx(1.20)
    assert "price table (H3 Max R2V, 15 s a take)" in result.message


def test_over_the_envelope_is_a_warning_not_a_stop(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "ready_estimate")
    from creation.ops.floor import record_spend

    record_spend(desk, episode=1, usd=10.0)
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {"cost_estimate": {"total_usd": "1.20"}}

    result = orchestrate.run_step(desk)

    assert "past 2x" in result.message and "warn only" in result.message
    assert load_production(desk).phase == "wait_spend"


# --- Take ------------------------------------------------------------------------------------------


def _video_routes(api: FakeApi, *, facts_lines: list[dict] | None) -> None:
    api.routes[("POST", "/v1/video-generations")] = {"job_id": "job_video_1"}
    api.routes[("GET", "/v1/jobs/job_video_1")] = {"status": "running", "depends_on": ["job_take_a", "job_take_b"]}
    api.routes[("GET", "/v1/jobs/job_take_a")] = {
        "status": "completed",
        "episode_ids": ["episode_01"],
        "relation": {"id": "scene_episode_01_set01"},
        "result": {"video": {"url": "https://r2.example/ep1.mp4"}},
    }
    api.routes[("GET", "/v1/jobs/job_take_b")] = {
        "status": "completed",
        "episode_ids": ["ep_02"],
        "relation": {"id": "scene_ep_02_set01"},
        "result": {"video": {"url": "https://r2.example/ep2.mp4"}},
    }
    api.routes[("GET", "/v1/jobs/job_take_b/take-facts")] = {
        "take_facts": {
            "job_id": "job_take_b",
            "endpoint_id": "minimax/h3-max/reference-to-video",
            "resolution": "768P",
            "duration_seconds": 15.0,
            "reference_image_count": 6,
            "lines": facts_lines,
            "spoken_line_count": 2,
        }
    }


def test_episode_two_films_with_episode_count_two_and_books_the_take_from_its_facts(desk: Path, api: FakeApi) -> None:
    _add_episode_two(desk, api)
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _video_routes(api, facts_lines=[{"line_id": "line_ep_02_01", "count": 1}])

    result = orchestrate.run_step(desk, confirm_spend=True)

    body = api.posted("/v1/video-generations")[0]
    assert body["episode_count"] == 2
    downloads = [payload["url"] for phase, payload in api.events if phase == "download"]
    assert downloads == ["https://r2.example/ep2.mp4"]  # episode 1's reused take is not collected again
    facts_call = [path for method, path, _, _ in api.calls if path.startswith("/v1/jobs/job_take_b/take-facts")]
    assert facts_call == ["/v1/jobs/job_take_b/take-facts?spine_id=sp1"]
    facts_files = list((desk / "ep02" / "api").glob("take-facts-ep02-t1-v*.json"))
    assert len(facts_files) == 1
    slot = episode_by_ordinal(load_series(desk), 2)
    assert slot.takes[0].spend_usd == pytest.approx(1.24)  # $1.20 + two images past four
    assert slot.takes[0].filmed_count == 1
    notes = (desk / "ep02" / "run-notes.md").read_text(encoding="utf-8")
    assert "job_video_1" in notes and "job_take_b" in notes
    assert "author --episode 3" in result.message
    assert load_production(desk).phase == "complete"


def test_a_take_the_server_refuses_says_why(desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    from creation.harness import raw_video

    monkeypatch.setattr(raw_video.time, "sleep", lambda _s: None)
    save_production_config(desk, ProductionConfig(poll_video_deadline_seconds=0.2))
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    api.routes[("POST", "/v1/video-generations")] = {"job_id": "job_video_1"}
    api.routes[("GET", "/v1/jobs/job_video_1")] = {
        "status": "failed",
        "error": {
            "code": "take_missing_approved_line",
            "message": "take 1 carries 2 of 3 approved lines",
            "details": {"missing_line_ids": ["line_episode_01_03"]},
        },
    }

    with pytest.raises(SystemExit) as caught:
        orchestrate.run_step(desk, confirm_spend=True)

    assert "take_missing_approved_line" in str(caught.value)
    assert "line_episode_01_03" in str(caught.value)
    assert load_production(desk).phase == "failed"


def test_a_new_paid_take_is_enrolled_not_resumed_from_the_old_job(desk: Path, api: FakeApi) -> None:
    api_dir = desk / "ep01" / "api"
    (api_dir / "16_video_enrol.json").write_text(json.dumps({"job_id": "job_old"}), encoding="utf-8")
    set_phase(desk, "ready_video", video_idempotency_suffix="-retry-abc", video_enrolled_suffix="")
    _video_routes(api, facts_lines=None)
    api.routes[("GET", "/v1/jobs/job_take_a/take-facts")] = {"take_facts": {"endpoint_id": ""}}

    orchestrate.run_step(desk)

    assert len(api.posted("/v1/video-generations")) == 1
    assert not any(path == "/v1/jobs/job_old" for _, path, _, _ in api.calls)
