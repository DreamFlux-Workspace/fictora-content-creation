"""Video models, their names and prices come from the server (Wan integration plan, section 5).

The kit keeps no list of models: ``--video-lane`` is checked against the
server's ``GET /v1/video-lanes``, price lines use the estimate's
``lane_label`` / ``take_line`` when the server sends them, a lane the kit's
table does not know is never priced with a local-table alarm, the thoughts
pre-check follows the server's ``locked_voices_required`` flag, and a
``409 video_lane_locked`` says what to do. Every new field is optional: an
older server gets today's behaviour.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from creation import prices
from creation.harness.http_util import VIDEO_LANE_LOCKED_FIX, api_error_text
from creation.orchestrate import lane_rate_words, price_estimate, video_lane_refusal
from creation.production_state import (
    ProductionState,
    load_production,
    save_production,
)
from creation.voice_mode import show_voices

CFG = SimpleNamespace(clip_duration_seconds=15, fallback_estimate_usd=None)
DAY = date(2026, 11, 4)

LANES = [
    {
        "lane_id": "minimax-h3",
        "label": "H3 Max Turbo",
        "servable": True,
        "default": True,
    },
    {"lane_id": "wan-3.0", "label": "Wan 3.0", "servable": True},
    {"lane_id": "later", "label": "Later", "servable": False, "note": "not ready"},
]


def _state(lane: str = "minimax-h3") -> ProductionState:
    return ProductionState(
        session_id="s",
        prompt="p",
        preset_id="pre",
        preset_version="1",
        video_lane=lane,
    )


def test_any_lane_the_server_lists_as_servable_is_accepted() -> None:
    assert video_lane_refusal("wan-3.0", LANES) is None
    assert video_lane_refusal("minimax-h3", LANES) is None


def test_a_lane_the_server_cannot_film_is_refused_with_the_usable_ones() -> None:
    refusal = video_lane_refusal("later", LANES) or ""
    assert "not available on this server yet (not ready)" in refusal
    assert "minimax-h3 (H3 Max Turbo), wan-3.0 (Wan 3.0)" in refusal
    unknown = video_lane_refusal("nope", LANES) or ""
    assert "'nope' is not one the server offers" in unknown


def test_no_lane_list_refuses_nothing_the_server_still_checks() -> None:
    assert video_lane_refusal("anything", None) is None


def _wan_estimate(**extra: Any) -> dict[str, Any]:
    return {
        "cost_estimate": {
            "priced_on": "2026-11-04",
            "video_endpoint_id": "kie/wan-3.0",
            "video_resolution": "480P",
            "takes": 1,
            "billed_seconds": 15,
            "usd_per_second": "0.04",
            "video_usd": "0.60",
            "total_usd": "0.60",
            **extra,
        }
    }


def test_the_servers_label_and_take_line_are_printed_for_any_model() -> None:
    state = _state("wan-3.0")
    estimate = _wan_estimate(
        lane_label="Wan 3.0", take_line="3 shots of 5 s at $0.04/s"
    )
    state.remember_server_lane(
        prices.server_lane(estimate), label=prices.server_lane_label(estimate)
    )

    usd, source, warnings = price_estimate(
        state, CFG, estimate, cast_count=2, takes=1, on=DAY
    )

    assert usd == 0.60 and warnings == []
    assert "Wan 3.0: 3 shots of 5 s at $0.04/s" in source
    assert "H3" not in source


def test_the_servers_label_names_the_rate_when_no_take_line_is_sent() -> None:
    state = _state("wan-3.0")
    estimate = _wan_estimate(lane_label="Wan 3.0")

    _, source, _ = price_estimate(state, CFG, estimate, cast_count=2, takes=1, on=DAY)

    assert "Wan 3.0 480P at $0.04/s" in source


def test_a_lane_the_table_does_not_know_is_quiet_without_server_dollars() -> None:
    state = _state("wan-3.0")
    state.remember_server_lane(None, label="Wan 3.0")

    usd, source, warnings = price_estimate(
        state,
        CFG,
        {"estimate_skipped": True, "detail": "busy"},
        cast_count=1,
        takes=1,
        on=DAY,
    )

    assert usd > 0
    assert source.startswith("no server price yet (Wan 3.0")
    assert len(warnings) == 1 and not warnings[0].startswith("!!")
    assert "The server's price for Wan 3.0 is unavailable right now" in warnings[0]
    assert lane_rate_words(state, on=DAY) == "Wan 3.0 (priced by the server)"


def test_an_h3_desk_without_server_dollars_still_warns_loudly() -> None:
    usd, _, warnings = price_estimate(
        _state(), CFG, {"estimate_skipped": True}, cast_count=1, takes=1, on=DAY
    )

    assert usd > 0
    assert warnings[0].startswith("!! SERVER ESTIMATE FAILED")


def test_the_server_label_is_saved_only_when_sent(tmp_path: Path) -> None:
    state = _state()
    save_production(tmp_path, state)
    assert "video_lane_label" not in (tmp_path / "production.json").read_text()

    state.remember_server_lane(None, label="Wan 3.0")
    save_production(tmp_path, state)
    assert load_production(tmp_path).video_lane_label == "Wan 3.0"


def test_a_locked_lane_refusal_keeps_the_servers_words_and_says_what_to_do() -> None:
    body = {
        "error": {
            "code": "video_lane_locked",
            "message": "This story was filmed on H3 Max Turbo; it keeps that model.",
        }
    }

    text = api_error_text(body)

    assert "This story was filmed on H3 Max Turbo; it keeps that model." in text
    assert VIDEO_LANE_LOCKED_FIX in text


class _VoiceApi:
    def __init__(self, answer: dict[str, Any]) -> None:
        self.answer = answer

    def get_optional(self, path: str) -> tuple[int, Any]:
        return 200, self.answer


def test_the_thoughts_pre_check_follows_the_server_flag() -> None:
    spine = {"spine_id": "sp1"}

    older = show_voices(_VoiceApi({"voice_mode": "model"}), spine)
    told = show_voices(
        _VoiceApi({"voice_mode": "model", "locked_voices_required": False}), spine
    )

    assert older.thoughts_need_locked is True
    assert told.thoughts_need_locked is False


def test_a_show_the_server_says_needs_no_locked_voices_is_not_stopped_for_thoughts(
    tmp_path: Path, monkeypatch
) -> None:
    from creation import voice_mode
    from creation.voice_gate import voices_first_refusal
    from creation.voice_mode import ShowVoices

    spine = {
        "spine_id": "sp1",
        "cast": [{"cast_id": "c1", "name": "Mira"}],
        "episode_summaries": [{"inner_voice": [{"speaker_cast_id": "c1"}]}],
    }

    def ask(flag: bool):
        monkeypatch.setattr(
            voice_mode,
            "voices_for_film",
            lambda desk, run, spine: ShowVoices(
                "model", "show", thoughts_need_locked=flag
            ),
        )
        return voices_first_refusal(
            tmp_path, spine, episode=1, run=None, stage="plates"
        )  # type: ignore[arg-type]

    assert "Mira" in (ask(True) or "")
    assert ask(False) is None


def test_a_desk_with_no_lane_sends_none_and_the_server_picks() -> None:
    from creation.cli_config import DEFAULT_VIDEO_LANE
    from creation.harness.stages_gated import draft_request_body

    assert DEFAULT_VIDEO_LANE is None
    body = draft_request_body(
        prompt="p", preset_id="x", preset_version="1", video_lane=DEFAULT_VIDEO_LANE
    )
    assert "model_overrides" not in body
    pinned = draft_request_body(
        prompt="p", preset_id="x", preset_version="1", video_lane="wan-3.0"
    )
    assert pinned["model_overrides"] == {"video": "wan-3.0"}


def test_the_desk_records_the_lane_the_server_reports_and_never_overwrites_one() -> (
    None
):
    state = _state()
    state.video_lane = None

    assert state.remember_story_lane({"video_lane": None}) is False
    assert state.video_lane is None
    assert state.remember_story_lane({"video_lane": "wan-3.0"}) is True
    assert state.video_lane == "wan-3.0"
    assert state.remember_story_lane({"video_lane": "minimax-h3"}) is False
    assert state.video_lane == "wan-3.0"


def test_a_no_lane_desk_before_any_server_answer_is_server_priced_and_quiet() -> None:
    state = _state()
    state.video_lane = None

    assert lane_rate_words(state, on=DAY) == (
        "the server's default model (priced by the server)"
    )
    _, _, warnings = price_estimate(state, CFG, {}, cast_count=1, takes=1, on=DAY)
    assert not any(line.startswith("!!") for line in warnings)
