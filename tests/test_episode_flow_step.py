"""The phase machine against a fake API: draft alone, per-episode boards/estimate/take/approvals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import set_phase, turbo_take_usd
from creation import orchestrate
from creation.episode_commands import run_author
from creation.harness.stages_gated import draft_request_body, spoken_language_tag
from creation.ops.state import episode_by_ordinal, load_series
from creation.production_config import ProductionConfig, save_production_config
from creation.production_state import load_production
from fake_api import FakeApi


def _add_episode_two(desk: Path, api: FakeApi) -> None:
    """Write episode 2 through the author command so the desk points at it."""

    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/author")] = {
        "extension_job_id": "job_ext_2"
    }
    api.jobs["job_ext_2"] = {"status": "completed", "job_id": "job_ext_2"}
    run_author(desk, episode=2)


# --- Draft ---------------------------------------------------------------------------------------


def test_the_draft_writes_episode_one_alone_with_the_shot_plan_and_language() -> None:
    body = draft_request_body(
        prompt="p",
        preset_id="x",
        preset_version="1",
        cut_tempo="punchy",
        spoken_language="ja",
        band="30s",
    )
    assert body["outline_mode"] == "arc_at_episode_two"
    assert body["episode_count"] == 1
    assert body["cut_tempo"] == "punchy"
    assert body["spoken_language"] == "ja-JP"
    assert body["model_overrides"] == {"video": "minimax-h3"}
    assert body["duration_band"] == "30s"
    assert "cut_tempo" not in draft_request_body(
        prompt="p", preset_id="x", preset_version="1"
    )


def test_an_unsupported_language_is_refused_before_the_request() -> None:
    assert spoken_language_tag("KO") == "ko-KR"
    with pytest.raises(ValueError, match="not supported"):
        spoken_language_tag("fr")


def test_step_new_drafts_alone_syncs_lines_and_prints_the_script_gate(
    desk: Path, api: FakeApi
) -> None:
    save_production_config(desk, ProductionConfig(cut_tempo="punchy"))
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

    result = orchestrate.run_step(desk)

    body = api.posted("/v1/prompt-video-authoring-drafts")[0]
    assert body["outline_mode"] == "arc_at_episode_two" and body["episode_count"] == 1
    assert body["cut_tempo"] == "punchy"
    take = episode_by_ordinal(load_series(desk), 1).takes[0]
    assert [line.original for line in take.lines] == ["We're closed.", "Not for me."]
    assert "shot 1: Hana wipes the counter" in result.message
    assert (desk / "api" / "spine.json").is_file() and (
        desk / "ep01" / "api" / "spine.json"
    ).is_file()
    assert load_production(desk).phase == "ready_cast_enrol"


def test_a_young_creature_without_proportions_stops_before_the_plates(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc["cast"].append(
        {"cast_id": "cast_cinder", "name": "Cinder", "visual_brief": "a newborn dragon"}
    )
    set_phase(desk, "ready_cast_enrol")
    api.routes[("POST", "/v1/spines/sp1/cast/enrol")] = {"job_id": "job_cast"}

    with pytest.raises(RuntimeError, match="oversized head"):
        orchestrate.run_step(desk)

    assert api.posted("/v1/spines/sp1/cast/enrol") == []
    assert load_production(desk).phase == "ready_cast_enrol"


def test_a_hand_over_the_hook_stops_before_the_boards(desk: Path, api: FakeApi) -> None:
    api.spine_doc["beats"][0]["reaction_kind"] = "dramatic_gasp"
    set_phase(desk, "ready_boards_enrol")
    api.routes[("POST", "/v1/spines/sp1/boards/enrol")] = {"job_id": "job_boards"}

    with pytest.raises(RuntimeError, match="hand over the mouth"):
        orchestrate.run_step(desk)

    assert api.posted("/v1/spines/sp1/boards/enrol") == []
    assert load_production(desk).phase == "ready_boards_enrol"


def test_a_failed_draft_says_the_rule_that_failed(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", "/v1/prompt-video-authoring-drafts")] = {
        "plan_job_id": "job_plan",
        "spine_id": "sp1",
    }
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
    assert (
        len(api.posted("/v1/prompt-video-authoring-drafts")) == 1
    )  # a deterministic failure is not retried
    assert load_production(desk).last_error == text


# --- Script, boards, estimate: episode 2 ------------------------------------------------------------


def test_episode_two_script_yes_goes_to_its_own_approve_route(
    desk: Path, api: FakeApi
) -> None:
    _add_episode_two(desk, api)
    api.routes[("POST", "/v1/spines/sp1/pilot-episodes/2/approve")] = {
        "spine_version": "v6"
    }

    result = orchestrate.approve_gate(desk, gate="script")

    assert api.posted("/v1/spines/sp1/pilot-episodes/2/approve") == [
        {"spine_version": "v5", "episode_ordinal": 2}
    ]
    assert api.posted("/v1/spines/sp1/approve") == []
    assert "Episode 2 script approved" in result.message
    assert load_production(desk).phase == "ready_boards_enrol"


def test_episode_two_boards_reach_episode_two_and_print_the_shot_list(
    desk: Path, api: FakeApi
) -> None:
    _add_episode_two(desk, api)
    set_phase(desk, "ready_boards_enrol")
    api.routes[("POST", "/v1/spines/sp1/boards/enrol")] = {"job_id": "job_boards"}
    api.jobs["job_boards"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/2/boards/exposure")] = {
        "boards": [{"set_index": 1, "mean_percent": 14.2}]
    }

    result = orchestrate.run_step(desk)

    body = api.posted("/v1/spines/sp1/boards/enrol")[0]
    assert body["episode_count"] == 2
    downloads = [payload["url"] for phase, payload in api.events if phase == "download"]
    assert downloads == ["https://r2.example/ep_02-board.png"]
    assert "brightness 14.2% (server). Information only." in result.message
    assert "row 1: medium · eye level · front · camera dolly_in" in result.message
    assert "rows 1 and 2 share size and angle" in result.message
    assert 'Hana\'s face is placed "bottom edge, left"' in result.message
    slot = episode_by_ordinal(load_series(desk), 2)
    assert slot.spend_usd == pytest.approx(0.30)
    assert slot.takes[0].estimate_usd == pytest.approx(
        turbo_take_usd(15)
    )  # no server lane yet: the Turbo default
    rate = "0.02" if turbo_take_usd(15) == pytest.approx(0.30) else "0.04"
    assert (
        f"on H3 Max Turbo 768P at ${rate}/s (15 s, it opens on this board; cast plates are not sent)"
        in result.message
    )
    state = load_production(desk)
    assert state.phase == "wait_board" and state.board_paths["t1"].startswith(
        "ep02/boards/board-ep02-t1-v"
    )


def test_boards_quote_r2v_with_its_references_once_the_server_has_named_r2v(
    desk: Path, api: FakeApi
) -> None:
    _add_episode_two(desk, api)
    set_phase(
        desk,
        "ready_boards_enrol",
        video_endpoint_id="minimax/h3-max/reference-to-video",
        video_resolution="768P",
    )
    api.routes[("POST", "/v1/spines/sp1/boards/enrol")] = {"job_id": "job_boards"}
    api.jobs["job_boards"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/2/boards/exposure")] = {"boards": []}

    result = orchestrate.run_step(desk)

    assert (
        "A take will cost about $1.20 on H3 Max R2V 768P at $0.08/s (15 s, up to 3 reference images)."
        in result.message
    )
    assert episode_by_ordinal(load_series(desk), 2).takes[
        0
    ].estimate_usd == pytest.approx(1.20)


def test_a_dim_board_approves_without_accept_dim(desk: Path, api: FakeApi) -> None:
    _add_episode_two(desk, api)
    board = desk / "ep02" / "boards" / "board-ep02-t1-v1.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    from fake_api import png_bytes

    board.write_bytes(png_bytes(gray=20))
    set_phase(
        desk, "wait_board", board_paths={"t1": "ep02/boards/board-ep02-t1-v1.png"}
    )
    api.routes[("GET", "/v1/spines/sp1/episodes/2/boards/exposure")] = {
        "boards": [{"set_index": 1, "mean_percent": 8.0}]
    }
    api.routes[("POST", "/v1/spines/sp1/episodes/2/boards/approve")] = {"ok": True}

    orchestrate.approve_gate(desk, gate="board")

    assert api.posted("/v1/spines/sp1/episodes/2/boards/approve") == [
        {"spine_version": "v5", "episode_ordinal": 2, "accept_dim": False}
    ]
    assert episode_by_ordinal(load_series(desk), 2).takes[0].board.status == "approved"
    assert load_production(desk).phase == "ready_estimate"


def test_the_estimate_prices_episode_two_alone_with_the_servers_dollars(
    desk: Path, api: FakeApi
) -> None:
    _add_episode_two(desk, api)
    set_phase(desk, "ready_estimate")
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "episode_ids": ["ep_02"],
        "cost_estimate": {"total_usd": "1.50", "priced_on": "2026-09-28", "takes": 1},
    }

    result = orchestrate.run_step(desk)

    assert api.posted("/v1/spines/sp1/batches/estimate") == [
        {"spine_version": "v5", "episode_ids": ["ep_02"]}
    ]
    assert load_production(desk).estimate_usd == pytest.approx(1.50)
    assert "server estimate priced 2026-09-28" in result.message
    assert (
        "H3 Max Turbo 768P at $0.0" in result.message
        and "/s, 15 s a take" in result.message
    )  # lane + $/s always
    assert (
        "!!" not in result.message.split("\n")[0]
    )  # the server's own dollars: no fallback warning
    assert (
        "episode 2 alone, 1 take(s); episodes 1-1 are not filmed or booked again"
        in result.message
    )
    assert "$1.50 of a $2.50 envelope for ep02" in result.message


def test_an_estimate_the_server_refuses_falls_back_to_the_price_table(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_estimate")
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = SystemExit(
        "HTTP 400 invalid_episode_selection: nope"
    )

    result = orchestrate.run_step(desk)

    assert load_production(desk).estimate_usd == pytest.approx(turbo_take_usd(15))
    assert "price table (H3 Max Turbo, 15 s a take)" in result.message
    first = result.message.split("\n")[0]
    assert first.startswith(
        "!! SERVER ESTIMATE FAILED (the server refused it: HTTP 400 invalid_episode_selection"
    )
    assert "LOCAL price table" in first


def test_an_estimate_that_names_r2v_without_dollars_is_priced_on_r2v_and_remembered(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_estimate")
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "cost_estimate": {
            "video_endpoint_id": "minimax/h3-max/reference-to-video",
            "video_resolution": "768P",
            "total_usd": None,
            "note": "unpriced",
        }
    }

    result = orchestrate.run_step(desk)

    state = load_production(desk)
    assert state.server_lane() == ("minimax/h3-max/reference-to-video", "768P")
    assert state.estimate_usd == pytest.approx(
        1.20
    )  # $0.08/s x 15 s, three images (no surcharge), never Turbo
    assert (
        "price table (H3 Max R2V, 15 s a take); H3 Max R2V 768P at $0.08/s"
        in result.message
    )
    assert (
        "!! SERVER ESTIMATE FAILED (the server named the lane but gave no dollars)"
        in result.message
    )


def test_an_estimate_that_names_turbo_keeps_the_turbo_price(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_estimate")
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "cost_estimate": {
            "video_endpoint_id": "minimax/h3-max-turbo/image-to-video",
            "video_resolution": "768P",
        }
    }

    result = orchestrate.run_step(desk)

    assert load_production(desk).estimate_usd == pytest.approx(turbo_take_usd(15))
    assert "price table (H3 Max Turbo, 15 s a take)" in result.message


@pytest.mark.parametrize(("fallback", "expected"), [(0.9, 0.9), (None, None)])
def test_an_unpriced_lane_uses_the_desk_fallback_or_the_turbo_rate(
    desk: Path, api: FakeApi, fallback: float | None, expected: float | None
) -> None:
    save_production_config(desk, ProductionConfig(fallback_estimate_usd=fallback))
    set_phase(desk, "ready_estimate")
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "cost_estimate": {
            "video_endpoint_id": "vendor/new-lane",
            "video_resolution": "768P",
            "total_usd": None,
        }
    }

    result = orchestrate.run_step(desk)

    want = expected if expected is not None else turbo_take_usd(15)
    assert load_production(desk).estimate_usd == pytest.approx(want)
    instead = (
        "the desk's fallback $0.90 a take"
        if fallback is not None
        else "the H3 Max Turbo rate ($0.0"
    )
    assert (
        f"!! vendor/new-lane has no verified price; priced at {instead}"
        in result.message
    )
    assert "vendor/new-lane 768P (no verified $/s)" in result.message


def test_over_the_envelope_is_a_warning_not_a_stop(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "ready_estimate")
    from creation.ops.floor import record_spend

    record_spend(desk, episode=1, usd=10.0)
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "cost_estimate": {"total_usd": "1.20"}
    }

    result = orchestrate.run_step(desk)

    assert "past 2x" in result.message and "warn only" in result.message
    assert load_production(desk).phase == "wait_spend"


# --- Take ------------------------------------------------------------------------------------------


def _video_routes(
    api: FakeApi,
    *,
    facts_lines: list[dict] | None,
    children: tuple[str, ...] = ("job_take_a", "job_take_b"),
) -> None:
    api.routes[("POST", "/v1/video-generations")] = {"job_id": "job_video_1"}
    api.routes[("GET", "/v1/jobs/job_video_1")] = {
        "status": "completed",
        "progress": 100,
        "depends_on": list(children),
    }
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


def test_episode_two_films_alone_and_books_the_take_from_its_facts(
    desk: Path, api: FakeApi
) -> None:
    _add_episode_two(desk, api)
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _video_routes(
        api,
        facts_lines=[{"line_id": "line_ep_02_01", "count": 1}],
        children=("job_take_b",),
    )

    result = orchestrate.run_step(desk, confirm_spend=True)

    body = api.posted("/v1/video-generations")[0]
    assert body["episode_count"] == 2
    assert (
        body["episode_ordinal"] == 2
    )  # episode 2 alone: episode 1 is not filmed or booked again
    assert "reroll_take_index" not in body and "seed_attempt" not in body
    downloads = [payload["url"] for phase, payload in api.events if phase == "download"]
    assert downloads == ["https://r2.example/ep2.mp4"]
    assert "also filmed another episode" not in result.message
    facts_call = [
        path
        for method, path, _, _ in api.calls
        if path.startswith("/v1/jobs/job_take_b/take-facts")
    ]
    assert facts_call == ["/v1/jobs/job_take_b/take-facts?spine_id=sp1"]
    facts_files = list((desk / "ep02" / "api").glob("take-facts-ep02-t1-v*.json"))
    assert len(facts_files) == 1
    slot = episode_by_ordinal(load_series(desk), 2)
    assert slot.takes[0].spend_usd == pytest.approx(
        1.24
    )  # $1.20 + two images past four
    assert slot.takes[0].filmed_count == 1
    assert load_production(desk).server_lane() == (
        "minimax/h3-max/reference-to-video",
        "768P",
    )
    notes = (desk / "ep02" / "run-notes.md").read_text(encoding="utf-8")
    assert "job_video_1" in notes and "job_take_b" in notes
    assert "author --episode 3" in result.message
    assert load_production(desk).phase == "complete"


def test_a_take_the_server_refuses_says_why(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_a_new_paid_take_is_enrolled_not_resumed_from_the_old_job(
    desk: Path, api: FakeApi
) -> None:
    api_dir = desk / "ep01" / "api"
    (api_dir / "16_video_enrol.json").write_text(
        json.dumps({"job_id": "job_old"}), encoding="utf-8"
    )
    set_phase(
        desk,
        "ready_video",
        video_idempotency_suffix="-retry-abc",
        video_enrolled_suffix="",
    )
    _video_routes(api, facts_lines=None, children=("job_take_a",))
    api.routes[("GET", "/v1/jobs/job_take_a/take-facts")] = {
        "take_facts": {"endpoint_id": ""}
    }

    orchestrate.run_step(desk)

    assert len(api.posted("/v1/video-generations")) == 1
    assert not any(path == "/v1/jobs/job_old" for _, path, _, _ in api.calls)


# --- Reuse body language ---------------------------------------------------------------------------


def test_the_reuse_body_copies_spoken_language_and_locale_from_the_spine() -> None:
    from creation.harness.visual_first_ep1 import reuse_generation_body

    spine = {"spine_id": "sp1", "spoken_language": "ko-KR", "locale": "en-GB"}
    body = reuse_generation_body(
        prompt="p", spine=spine, preset_id="x", preset_version="1"
    )
    assert body["spoken_language"] == "ko-KR"
    assert body["locale"] == "en-GB"


def test_the_reuse_body_for_an_older_spine_without_language_fields_stays_english() -> (
    None
):
    from creation.harness.visual_first_ep1 import reuse_generation_body

    body = reuse_generation_body(
        prompt="p", spine={"spine_id": "sp1"}, preset_id="x", preset_version="1"
    )
    assert body["locale"] == "en-US"
    assert "spoken_language" not in body, "the server default (en-US) applies"
