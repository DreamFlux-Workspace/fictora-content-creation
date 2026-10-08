"""``--single-frame-start``: the operator's trial opening on ``film`` and ``step`` (fictora-drama #658).

Off by default, the film body is exactly as before. With the flag the body
carries ``single_frame_start: true``; the choice is kept per film key so a
resume sends the same body; a server refusal stops with a plain message and
nothing charged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import set_phase
from creation import orchestrate
from creation.cli_produce import main
from creation.episode_commands import CommandStopped, run_film
from creation.harness import stages_gated as stages
from creation.ops.state import episode_by_ordinal, load_series
from creation.production_state import load_production, production_path, save_production
from fake_api import FakeApi
from test_episode_flow_step import _video_routes
import test_film_episode as film_tests
from test_film_episode import (
    CAUSE,
    ESTIMATE,
    VIDEO,
    _filmed_once,
    _take_two_of_episode_two,
    _video_keys,
)

desk30 = film_tests.desk30  # the 30s desk fixture, pointed at episode 2
api30 = film_tests.api30

UNIT = "film-ep02-t2-s2"
NOTE = "Opening each take on a single full picture instead of the storyboard (trial)"
OPERATOR_ONLY = SystemExit(
    "HTTP 403 POST https://drama.example/v1/video-generations: single_frame_start_operator_only: "
    "single_frame_start is an operator setting and is not accepted on a creator session."
)
WRONG_LANE = SystemExit(
    "HTTP 422 POST https://drama.example/v1/video-generations: video_generation_invalid: "
    "single_frame_start is an H3 setting; this run films on seedance"
)


def _priced_take_two(desk: Path, api: FakeApi) -> None:
    _filmed_once(desk)
    api.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20"}}
    run_film(desk, episode=2, take_id="t2", cause=CAUSE)
    _take_two_of_episode_two(api)


# --- film -------------------------------------------------------------------------------------------


def test_film_without_the_flag_sends_the_body_as_before(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    before = production_path(desk30).read_text(encoding="utf-8")
    assert "single_frame_start" not in before

    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    (body,) = api30.posted(VIDEO)
    assert "single_frame_start" not in body
    # The one field is all the flag adds: built without it, the body is the same bytes.
    built = {
        "spine": api30.spine("sp1"),
        "prompt": "A shop at closing time.",
        "preset_id": "modern-romance",
        "preset_version": "2",
        "episode": 2,
        "reroll_take_index": 2,
        "seed_attempt": 2,
    }
    plain = stages.video_request_body(api30, **built)
    trial = stages.video_request_body(api30, **built, single_frame_start=True)
    assert json.dumps(body, sort_keys=True) == json.dumps(plain, sort_keys=True)
    assert trial.pop("single_frame_start") is True
    assert json.dumps(trial, sort_keys=True) == json.dumps(plain, sort_keys=True)
    assert "single_frame_start" not in production_path(desk30).read_text(
        encoding="utf-8"
    )


def test_film_with_the_flag_sends_it_and_says_so(
    desk30: Path, api30: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _priced_take_two(desk30, api30)

    run_film(
        desk30,
        episode=2,
        take_id="t2",
        cause=CAUSE,
        confirm_spend=True,
        single_frame_start=True,
    )

    (body,) = api30.posted(VIDEO)
    assert body["single_frame_start"] is True
    assert NOTE in capsys.readouterr().out


def test_a_trial_film_cut_off_after_sending_keeps_the_choice_on_its_unit(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    api30.routes[("POST", VIDEO)] = SystemExit("HTTP 502: bad gateway")

    with pytest.raises(SystemExit):
        run_film(
            desk30,
            episode=2,
            take_id="t2",
            cause=CAUSE,
            confirm_spend=True,
            single_frame_start=True,
        )

    pending = load_production(desk30).pending[UNIT]
    assert pending["single_frame_start"] is True and pending["job_id"] is None
    _take_two_of_episode_two(api30)
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)
    first, second = api30.posted(VIDEO)
    assert first == second and second["single_frame_start"] is True
    one, two = _video_keys(api30)
    assert one == two


def test_an_interrupted_trial_film_resends_the_same_body_without_the_flag(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    state = load_production(desk30)
    state.pending[UNIT] = {"key": "k-1", "job_id": None, "single_frame_start": True}
    save_production(desk30, state)

    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    (body,) = api30.posted(VIDEO)
    assert body["single_frame_start"] is True
    assert _video_keys(api30) == ["k-1"]


def test_a_plain_film_cut_off_is_not_resent_with_the_flag(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    state = load_production(desk30)
    state.pending[UNIT] = {"key": "k-1", "job_id": None}
    save_production(desk30, state)

    with pytest.raises(
        CommandStopped, match="already sent without --single-frame-start"
    ):
        run_film(
            desk30,
            episode=2,
            take_id="t2",
            cause=CAUSE,
            confirm_spend=True,
            single_frame_start=True,
        )

    assert api30.posted(VIDEO) == []


@pytest.mark.parametrize("refusal", [OPERATOR_ONLY, WRONG_LANE])
def test_a_refused_trial_film_stops_plainly_and_the_next_film_is_plain_on_a_fresh_key(
    desk30: Path, api30: FakeApi, refusal: SystemExit
) -> None:
    _priced_take_two(desk30, api30)
    good = api30.routes[("POST", VIDEO)]
    api30.routes[("POST", VIDEO)] = refusal

    with pytest.raises(CommandStopped) as caught:
        run_film(
            desk30,
            episode=2,
            take_id="t2",
            cause=CAUSE,
            confirm_spend=True,
            single_frame_start=True,
        )

    message = str(caught.value)
    assert "isn't available for this show or account" in message
    assert "nothing was filmed or charged" in message
    assert "seedance" not in message.lower()
    state = load_production(desk30)
    assert UNIT not in state.pending
    assert episode_by_ordinal(load_series(desk30), 2).takes[1].filmed_count == 1

    api30.routes[("POST", VIDEO)] = good
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    first, second = api30.posted(VIDEO)
    assert first["single_frame_start"] is True and "single_frame_start" not in second
    one, two = _video_keys(api30)
    assert one != two


def test_the_flag_without_the_spend_yes_sends_nothing(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    with pytest.raises(CommandStopped, match="goes with --confirm-spend"):
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, single_frame_start=True)
    assert api30.calls == []


def test_film_and_step_take_the_flag_off_by_default(
    desk30: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation import cli_produce, episode_commands

    seen: dict[str, object] = {}

    def remember(*_args: object, **kwargs: object) -> orchestrate.StepResult:
        seen.update(kwargs)
        return orchestrate.StepResult("complete", "", ())

    monkeypatch.setattr(episode_commands, "run_film", remember)
    monkeypatch.setattr(cli_produce, "run_step", remember)
    film = ["film", "--desk", str(desk30), "--episode", "2", "--confirm-spend"]
    step = ["step", "--desk", str(desk30), "--confirm-spend"]
    for argv in (film, step):
        assert main(argv) == 0
        assert seen.pop("single_frame_start") is False
        assert main([*argv, "--single-frame-start"]) == 0
        assert seen.pop("single_frame_start") is True


# --- step -------------------------------------------------------------------------------------------


def _ep1_take(api: FakeApi) -> None:
    _video_routes(api, facts_lines=None, children=("job_take_a",))
    api.routes[("GET", "/v1/jobs/job_take_a/take-facts")] = {
        "take_facts": {"endpoint_id": ""}
    }


def test_step_without_the_flag_sends_the_body_as_before(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ep1_take(api)

    orchestrate.run_step(desk, confirm_spend=True)

    (body,) = api.posted(VIDEO)
    assert "single_frame_start" not in body
    assert "single_frame_start" not in production_path(desk).read_text(encoding="utf-8")


def test_step_with_the_flag_sends_it_and_keeps_it_on_the_key(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ep1_take(api)

    orchestrate.run_step(desk, confirm_spend=True, single_frame_start=True)

    (body,) = api.posted(VIDEO)
    assert body["single_frame_start"] is True
    assert load_production(desk).single_frame_start_keys == _video_keys(api)
    assert NOTE in capsys.readouterr().err


def test_after_a_retried_film_stage_the_spend_yes_still_sends_the_trial(
    desk: Path, api: FakeApi
) -> None:
    """The film key's prefix follows the phase after a retry-step: the choice is kept on the key sent."""

    set_phase(
        desk,
        "wait_spend",
        estimate_usd=1.2,
        attempts={orchestrate.step_retry_unit(1, "ready_video"): 1},
    )
    _ep1_take(api)

    orchestrate.run_step(desk, confirm_spend=True, single_frame_start=True)

    (body,) = api.posted(VIDEO)
    assert body["single_frame_start"] is True
    assert load_production(desk).single_frame_start_keys == _video_keys(api)
    assert "-r1-" in _video_keys(api)[0]


def test_a_resumed_step_sends_the_trial_body_again_without_the_flag(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_video")
    key = f"{load_production(desk).idempotency_prefix}-step-video"
    set_phase(desk, "ready_video", single_frame_start_keys=[key])
    _ep1_take(api)

    orchestrate.run_step(desk)

    (body,) = api.posted(VIDEO)
    assert body["single_frame_start"] is True
    assert _video_keys(api) == [key]


def test_a_plain_step_film_already_sent_is_not_resent_with_the_flag(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_video", video_enrolled_suffix="")
    (desk / "ep01" / "api" / "16_video_request.json").write_text("{}", encoding="utf-8")
    _ep1_take(api)

    with pytest.raises(
        RuntimeError, match="already sent once without --single-frame-start"
    ):
        orchestrate.run_step(desk, single_frame_start=True)

    assert api.posted(VIDEO) == []


def test_the_flag_on_another_step_sends_nothing(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    with pytest.raises(RuntimeError, match="goes with `step --confirm-spend`"):
        orchestrate.run_step(desk, single_frame_start=True)
    assert api.posted(VIDEO) == []
    assert load_production(desk).phase == "wait_spend"


@pytest.mark.parametrize("refusal", [OPERATOR_ONLY, WRONG_LANE])
def test_a_refused_trial_step_stops_plainly_and_goes_back_to_the_spend_yes(
    desk: Path, api: FakeApi, refusal: SystemExit
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ep1_take(api)
    api.routes[("POST", VIDEO)] = refusal

    with pytest.raises(RuntimeError) as caught:
        orchestrate.run_step(desk, confirm_spend=True, single_frame_start=True)

    assert "isn't available for this show or account" in str(caught.value)
    state = load_production(desk)
    assert state.phase == "wait_spend"
    assert state.single_frame_start_keys == []

    _ep1_take(api)
    orchestrate.run_step(desk, confirm_spend=True)
    first, second = api.posted(VIDEO)
    assert first["single_frame_start"] is True and "single_frame_start" not in second
