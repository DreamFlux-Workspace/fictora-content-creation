"""Dated prices, the HTTP session's refusals and error text, and reading the spine by ordinal."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from creation import prices
from creation.harness.http_util import api_error_text, describe_job_error
from creation.harness.raw_video import episode_clips
from creation.harness.session import DramaApiRunSession
from creation.media_fetch import BROWSER_USER_AGENT, download_to_versioned
from creation.orchestrate import _estimate_usd
from creation.spine_view import (
    beats_by_take,
    board_assets,
    covered_placement,
    episode_id_for,
    storyboard_set_for_beat,
)
from fake_api import spine_fixture

DAY = date(2026, 9, 28)


R2V = (prices.H3_MAX_R2V_ENDPOINT, "768P")


def test_the_minimax_pin_prices_turbo_until_the_server_names_another_lane() -> None:
    assert (
        prices.LANE_DEFAULT_ENDPOINTS["minimax-h3"][0]
        == prices.H3_MAX_TURBO_I2V_ENDPOINT
    )
    assert prices.lane_take_usd(
        "minimax-h3", 15, on=date(2026, 9, 30)
    ) == pytest.approx(0.30)
    assert prices.lane_take_usd(
        "minimax-h3", 15, on=date(2026, 10, 1)
    ) == pytest.approx(0.60)
    # Turbo charges nothing for images, and a take under five seconds films (and bills) five.
    assert prices.lane_take_usd(
        "minimax-h3", 15, on=DAY, reference_images=9
    ) == pytest.approx(0.30)
    assert prices.lane_take_usd("minimax-h3", 4, on=date(2026, 10, 1)) == pytest.approx(
        0.20
    )
    assert prices.lane_label("minimax-h3") == "H3 Max Turbo"
    assert prices.lane_take_usd("some-other-lane", 15, on=DAY) is None


def test_an_r2v_take_is_priced_only_when_the_server_names_r2v() -> None:
    assert prices.lane_take_usd("minimax-h3", 15, on=DAY, server=R2V) == pytest.approx(
        1.20
    )
    assert prices.lane_take_usd(
        "minimax-h3", 15, on=DAY, reference_images=6, server=R2V
    ) == pytest.approx(1.24)
    assert prices.lane_label("minimax-h3", server=R2V) == "H3 Max R2V"
    assert prices.reference_images_ceiling(12, prices.H3_MAX_R2V_ENDPOINT) == 9
    assert prices.reference_images_ceiling(12, prices.H3_MAX_TURBO_I2V_ENDPOINT) == 1


def test_the_servers_lane_is_read_from_an_estimate_or_take_facts() -> None:
    estimate = {
        "cost_estimate": {
            "video_endpoint_id": prices.H3_MAX_R2V_ENDPOINT,
            "video_resolution": "768P",
        }
    }
    assert prices.server_lane(estimate) == R2V
    facts = {"endpoint_id": prices.H3_MAX_TURBO_I2V_ENDPOINT, "resolution": "768P"}
    assert prices.server_lane(facts) == (prices.H3_MAX_TURBO_I2V_ENDPOINT, "768P")
    assert prices.server_lane({"estimate_skipped": True}) is None
    assert prices.server_lane(None) is None


def test_turbo_facts_are_priced_at_their_dated_rate() -> None:
    turbo = {
        "endpoint_id": prices.H3_MAX_TURBO_I2V_ENDPOINT,
        "resolution": "768P",
        "duration_seconds": 15,
    }
    assert prices.take_facts_usd(turbo, on=date(2026, 9, 30))[0] == pytest.approx(0.30)
    assert prices.take_facts_usd(turbo, on=date(2026, 10, 1))[0] == pytest.approx(0.60)
    assert (
        prices.take_facts_usd(
            {"endpoint_id": "x/y", "resolution": "768P", "duration_seconds": 5}, on=DAY
        )
        is None
    )


def test_envelopes_stay_warn_only_figures() -> None:
    assert prices.envelope_usd(first_episode=True, band="15s") == 5.50
    assert prices.envelope_usd(first_episode=False, band="15s") == 2.50
    assert prices.envelope_usd(first_episode=False, band="30s") == 5.00


def test_the_estimate_reads_the_servers_decimal_string_dollars() -> None:
    assert _estimate_usd(
        {"cost_estimate": {"total_usd": "3.84"}}, fallback_usd=0.3
    ) == pytest.approx(3.84)
    assert _estimate_usd(
        {"cost_estimate": {"total_usd": None}}, fallback_usd=1.2
    ) == pytest.approx(1.2)


def test_the_session_refuses_provider_spec(tmp_path: Path) -> None:
    run = DramaApiRunSession(
        base_url="https://api.example", token="t", out_dir=tmp_path
    )
    try:
        with pytest.raises(PermissionError, match="take-facts"):
            run.get("/v1/jobs/job_take_1/provider-spec")
        assert run.url("/v1/jobs/job_take_1/take-facts?spine_id=sp1").endswith(
            "take-facts?spine_id=sp1"
        )
    finally:
        run.client.close()


def test_an_enrol_409_says_the_servers_code_message_and_details(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "boards_already_generated",
                    "message": "episode 1 boards exist",
                    "details": {"set": 1},
                },
                "request_id": "req_9",
            },
        )

    run = DramaApiRunSession(
        base_url="https://api.example", token="t", out_dir=tmp_path
    )
    run.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit) as caught:
        run.post("/v1/spines/sp1/boards/enrol", {})
    text = str(caught.value)
    assert "boards_already_generated: episode 1 boards exist" in text
    assert '"set": 1' in text and "req_9" in text


def test_job_errors_are_described_with_their_rule() -> None:
    job = {
        "status": "failed",
        "error": {
            "code": "authoring_validation_failed",
            "message": "rule X",
            "details": {"n": 2},
        },
    }
    assert describe_job_error(job).startswith(
        "failed authoring_validation_failed: rule X (details"
    )
    assert describe_job_error({"status": "cancelled"}) == "cancelled (no error detail)"
    assert api_error_text("plain") == "plain"


def test_downloads_send_a_browser_user_agent(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "python" in request.headers.get("user-agent", "").lower():
            return httpx.Response(403)
        return httpx.Response(200, content=b"img")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    path = download_to_versioned(
        client, "https://r2.example/a.png", tmp_path, "board-ep01-t1"
    )
    assert path.read_bytes() == b"img"
    assert "Mozilla" in BROWSER_USER_AGENT


def test_episodes_are_found_by_ordinal_not_built() -> None:
    spine = spine_fixture()
    assert episode_id_for(spine, 2) == "ep_02"
    assert episode_id_for(spine, 7) == "episode_07"
    assert board_assets(spine, episode=2) == [(1, "https://r2.example/ep_02-board.png")]
    assert [b["beat_id"] for b in beats_by_take(spine, episode=2, take_count=1)[0]] == [
        "beat_ep_02_01"
    ]


def test_beats_follow_the_servers_beat_to_set_map() -> None:
    assert [storyboard_set_for_beat(n, (2, 3)) for n in (1, 2, 3, 5)] == [1, 1, 2, 2]
    with pytest.raises(ValueError):
        storyboard_set_for_beat(6, (2, 3))


def test_safe_zone_reads_placements_but_not_keep_clear_notes() -> None:
    assert covered_placement("bottom edge, left") == "bottom band"
    assert covered_placement("far right of the counter") == "right rail"
    assert covered_placement("bottom fifth clear of faces", frame_anchored=True) is None
    assert covered_placement("the bottom of the bowl", frame_anchored=True) is None
    assert covered_placement("upper third, centre") is None


def test_episode_clips_keep_only_the_episode_in_board_order() -> None:
    raw = {
        "clips": [
            {"job_id": "b", "episode_id": "ep_02", "set_index": 2},
            {"job_id": "x", "episode_id": "episode_01", "set_index": 1},
            {"job_id": "a", "episode_id": "ep_02", "set_index": 1},
        ]
    }
    assert [clip["job_id"] for clip in episode_clips(raw, episode_id="ep_02")] == [
        "a",
        "b",
    ]
