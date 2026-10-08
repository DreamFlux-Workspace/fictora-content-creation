"""``--reference-mode``: the operator's reference-lane trial on ``film`` and ``step`` (face drift, L-20260929-15).

Off by default, the film body is exactly as before. With the flag the
reference price is asked for first (``reference_mode: true`` on the estimate),
printed and booked; the film body carries ``reference_mode: true``; the choice
is kept per film key so a resume sends the same body; a server refusal stops
with a plain message, nothing sent or booked, and the next plain film goes out
cleanly. Never together with ``--single-frame-start``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import orchestrate
from creation.cli_produce import main
from creation.episode_commands import CommandStopped, run_film
from creation.harness import stages_gated as stages
from creation.ops.state import episode_by_ordinal, load_series
from creation.prices import H3_MAX_R2V_ENDPOINT, H3_MAX_TURBO_I2V_ENDPOINT
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
NOTE = (
    "reference mode: this take films on the reference lane (board + cast plates) at the "
    "reference price — a trial, never a default"
)
OPERATOR_ONLY = SystemExit(
    "HTTP 403 POST https://drama.example/v1/video-generations: reference_mode_operator_only: "
    "reference_mode is an operator setting and is not accepted on a creator session."
)
WRONG_LANE = SystemExit(
    "HTTP 422 POST https://drama.example/v1/video-generations: video_generation_invalid: "
    "reference_mode films H3 takes; this run films on seedance"
)
OLDER_DEPLOY = SystemExit(
    "HTTP 422 POST https://drama.example/v1/video-generations: "
    '[{"type": "extra_forbidden", "loc": ["body", "reference_mode"], "msg": "Extra inputs are not permitted"}]'
)
ESTIMATE_OLDER_DEPLOY = SystemExit(
    "HTTP 422 POST https://drama.example/v1/spines/sp1/batches/estimate: "
    '[{"type": "extra_forbidden", "loc": ["body", "reference_mode"], "msg": "Extra inputs are not permitted"}]'
)

TURBO = {
    "cost_estimate": {
        "total_usd": "0.60",
        "video_usd": "0.60",
        "stills_usd": "0",
        "usd_per_second": "0.04",
        "billed_seconds": 15,
        "video_endpoint_id": H3_MAX_TURBO_I2V_ENDPOINT,
        "video_resolution": "768P",
        "takes": 1,
        "priced_on": "2026-10-08",
    }
}
#: One 15 s take at $0.08/s plus two reference images past four ($0.02048 each).
REFERENCE = {
    "cost_estimate": {
        "total_usd": "1.24",
        "video_usd": "1.24",
        "stills_usd": "0",
        "usd_per_second": "0.08",
        "billed_seconds": 15,
        "video_endpoint_id": H3_MAX_R2V_ENDPOINT,
        "video_resolution": "768P",
        "takes": 1,
        "priced_on": "2026-10-08",
    }
}


def _estimates(method: str, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
    """The server's two answers: the reference lane only when the body asks for it."""

    return REFERENCE if (body or {}).get("reference_mode") else TURBO


def _no_priced_facts(api: FakeApi, job: str) -> None:
    """Take facts that name no lane: the take is booked at the kit's fallback price."""

    api.routes[("GET", f"/v1/jobs/{job}/take-facts")] = {"take_facts": {}}


def _priced_take_two(desk: Path, api: FakeApi) -> None:
    _filmed_once(desk)
    api.routes[("POST", ESTIMATE)] = _estimates
    run_film(desk, episode=2, take_id="t2", cause=CAUSE)
    _take_two_of_episode_two(api)


def _estimate_bodies(api: FakeApi, path: str = ESTIMATE) -> list[dict[str, Any] | None]:
    return api.posted(path)


def _film_ref(desk: Path, **kwargs: Any) -> str:
    return run_film(
        desk,
        episode=2,
        take_id="t2",
        cause=CAUSE,
        confirm_spend=True,
        reference_mode=True,
        **kwargs,
    )


# --- film -------------------------------------------------------------------------------------------


def test_film_without_the_flag_sends_the_body_as_before(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    before = production_path(desk30).read_text(encoding="utf-8")
    assert "reference_mode" not in before

    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    (body,) = api30.posted(VIDEO)
    assert "reference_mode" not in body
    # The price asked for before the yes is the plain one; no reference estimate is asked for.
    assert all("reference_mode" not in (b or {}) for b in _estimate_bodies(api30))
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
    trial = stages.video_request_body(api30, **built, reference_mode=True)
    assert json.dumps(body, sort_keys=True) == json.dumps(plain, sort_keys=True)
    assert trial.pop("reference_mode") is True
    assert json.dumps(trial, sort_keys=True) == json.dumps(plain, sort_keys=True)
    state = load_production(desk30)
    assert "reference_mode" not in production_path(desk30).read_text(encoding="utf-8")
    assert state.reference_mode_keys == []


def test_the_estimate_body_is_unchanged_without_the_flag(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    api30.routes[("POST", ESTIMATE)] = _estimates

    stages.estimate_batch(api30, spine_id="sp1", episode=2, reroll_take_index=2)
    stages.estimate_batch(
        api30, spine_id="sp1", episode=2, reroll_take_index=2, reference_mode=True
    )

    plain, reference = _estimate_bodies(api30)
    assert reference is not None and reference.pop("reference_mode") is True
    assert json.dumps(plain, sort_keys=True) == json.dumps(reference, sort_keys=True)


def test_film_with_the_flag_prices_the_reference_lane_sends_it_and_books_at_it(
    desk30: Path, api30: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _priced_take_two(desk30, api30)
    _no_priced_facts(api30, "job_take_e2t2")
    spend_before = episode_by_ordinal(load_series(desk30), 2).spend_usd
    lane_before = load_production(desk30).server_lane()
    capsys.readouterr()

    text = _film_ref(desk30)

    (body,) = api30.posted(VIDEO)
    assert body["reference_mode"] is True
    # The server refuses reference_mode with the dialogue track or the single-frame opening.
    assert "h3_target_audio" not in body and "single_frame_start" not in body
    _plain, asked = _estimate_bodies(api30)
    assert asked is not None and asked["reference_mode"] is True
    assert asked["reroll_take_index"] == 2
    out = capsys.readouterr().out
    assert NOTE in out
    assert stages.REFERENCE_VOICE_NOTE in out
    assert "Reference price for ep02 t2: about $1.24" in out
    assert "H3 Max R2V 768P at $0.08/s" in out
    # Booked at the reference estimate, not the Turbo table price ($0.60).
    slot = episode_by_ordinal(load_series(desk30), 2)
    assert slot.spend_usd == pytest.approx(spend_before + 1.24)
    assert slot.takes[1].estimate_usd == pytest.approx(1.24)
    assert "$1.24 booked" in text
    # The reference lane is this film's alone: the story keeps its own lane.
    assert load_production(desk30).server_lane() == lane_before


def test_a_reference_take_with_facts_is_booked_from_them_and_leaves_the_story_lane(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)  # the take facts name R2V, 15 s, no extra images
    state = load_production(desk30)
    state.remember_server_lane((H3_MAX_TURBO_I2V_ENDPOINT, "768P"))
    save_production(desk30, state)
    spend_before = episode_by_ordinal(load_series(desk30), 2).spend_usd

    _film_ref(desk30)

    assert episode_by_ordinal(load_series(desk30), 2).spend_usd == pytest.approx(
        spend_before + 1.20
    )
    assert load_production(desk30).server_lane() == (H3_MAX_TURBO_I2V_ENDPOINT, "768P")


def test_a_trial_film_cut_off_after_sending_keeps_the_choice_and_price_on_its_unit(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    api30.routes[("POST", VIDEO)] = SystemExit("HTTP 502: bad gateway")

    with pytest.raises(SystemExit):
        _film_ref(desk30)

    pending = load_production(desk30).pending[UNIT]
    assert pending["reference_mode"] is True and pending["job_id"] is None
    assert pending["reference_take_usd"] == pytest.approx(1.24)
    _take_two_of_episode_two(api30)
    _no_priced_facts(api30, "job_take_e2t2")
    spend_before = episode_by_ordinal(load_series(desk30), 2).spend_usd
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)
    first, second = api30.posted(VIDEO)
    assert first == second and second["reference_mode"] is True
    one, two = _video_keys(api30)
    assert one == two
    # The resume booked at the price kept on the unit (no second estimate asked for).
    assert len(_estimate_bodies(api30)) == 2
    assert episode_by_ordinal(load_series(desk30), 2).spend_usd == pytest.approx(
        spend_before + 1.24
    )


def test_a_plain_film_cut_off_is_not_resent_with_the_flag(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    state = load_production(desk30)
    state.pending[UNIT] = {"key": "k-1", "job_id": None}
    save_production(desk30, state)

    with pytest.raises(CommandStopped, match="already sent without --reference-mode"):
        _film_ref(desk30)

    assert api30.posted(VIDEO) == []


@pytest.mark.parametrize("refusal", [OPERATOR_ONLY, WRONG_LANE, OLDER_DEPLOY])
def test_a_refused_trial_film_stops_plainly_books_nothing_and_the_next_film_is_plain(
    desk30: Path, api30: FakeApi, refusal: SystemExit
) -> None:
    _priced_take_two(desk30, api30)
    good = api30.routes[("POST", VIDEO)]
    api30.routes[("POST", VIDEO)] = refusal
    spend_before = load_series(desk30).spend_usd

    with pytest.raises(CommandStopped) as caught:
        _film_ref(desk30)

    message = str(caught.value)
    assert "reference lane" in message and "isn't available" in message
    assert "nothing was filmed or charged" in message
    assert "seedance" not in message.lower()
    state = load_production(desk30)
    assert UNIT not in state.pending
    assert load_series(desk30).spend_usd == pytest.approx(spend_before)
    assert episode_by_ordinal(load_series(desk30), 2).takes[1].filmed_count == 1

    api30.routes[("POST", VIDEO)] = good
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    first, second = api30.posted(VIDEO)
    assert first["reference_mode"] is True and "reference_mode" not in second
    one, two = _video_keys(api30)
    assert one != two


@pytest.mark.parametrize(
    "answer",
    [
        ESTIMATE_OLDER_DEPLOY,
        TURBO,
        {"cost_estimate": {"video_endpoint_id": H3_MAX_R2V_ENDPOINT}},
    ],
    ids=["refused", "priced-turbo", "no-dollars"],
)
def test_a_reference_price_the_server_will_not_give_stops_before_anything_is_sent(
    desk30: Path, api30: FakeApi, answer: Any
) -> None:
    _priced_take_two(desk30, api30)
    api30.routes[("POST", ESTIMATE)] = lambda m, p, body: (
        _raise(answer) if (body or {}).get("reference_mode") else TURBO
    )

    with pytest.raises(CommandStopped, match="would not price the reference lane"):
        _film_ref(desk30)

    assert api30.posted(VIDEO) == []
    assert UNIT not in load_production(desk30).pending


def _raise(answer: Any) -> Any:
    if isinstance(answer, SystemExit):
        raise answer
    return answer


def test_the_flag_without_the_spend_yes_sends_nothing(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    with pytest.raises(
        CommandStopped, match="--reference-mode goes with --confirm-spend"
    ):
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, reference_mode=True)
    assert api30.calls == []


def test_film_refuses_both_trials_together_up_front(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    calls = len(api30.calls)
    with pytest.raises(CommandStopped, match="cannot go together"):
        _film_ref(desk30, single_frame_start=True)
    assert api30.calls[calls:] == []


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
        assert seen.pop("reference_mode") is False
        assert main([*argv, "--reference-mode"]) == 0
        assert seen.pop("reference_mode") is True


# --- step -------------------------------------------------------------------------------------------

STEP_ESTIMATE = "/v1/spines/sp1/batches/estimate"


def _ep1_take(api: FakeApi) -> None:
    _video_routes(api, facts_lines=None, children=("job_take_a",))
    api.routes[("GET", "/v1/jobs/job_take_a/take-facts")] = {
        "take_facts": {"endpoint_id": ""}
    }
    api.routes[("POST", STEP_ESTIMATE)] = _estimates


def test_step_without_the_flag_sends_the_body_as_before(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=0.6)
    _ep1_take(api)

    orchestrate.run_step(desk, confirm_spend=True)

    (body,) = api.posted(VIDEO)
    assert "reference_mode" not in body
    assert api.posted(STEP_ESTIMATE) == []
    assert "reference_mode" not in production_path(desk).read_text(encoding="utf-8")


def test_step_with_the_flag_prices_sends_keeps_and_books_the_reference_lane(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=0.6)
    _ep1_take(api)
    spend_before = episode_by_ordinal(load_series(desk), 1).spend_usd

    result = orchestrate.run_step(desk, confirm_spend=True, reference_mode=True)

    (asked,) = api.posted(STEP_ESTIMATE)
    assert asked is not None and asked["reference_mode"] is True
    (body,) = api.posted(VIDEO)
    assert body["reference_mode"] is True
    state = load_production(desk)
    assert state.reference_mode_keys == _video_keys(api)
    assert state.estimate_usd == pytest.approx(1.24)
    assert state.server_lane() != (H3_MAX_R2V_ENDPOINT, "768P")
    err = capsys.readouterr().err
    assert NOTE in err
    assert stages.REFERENCE_VOICE_NOTE in err
    assert "h3_target_audio" not in body
    assert "Reference price $1.24 for episode 1" in err
    assert episode_by_ordinal(load_series(desk), 1).spend_usd == pytest.approx(
        spend_before + 1.24
    )
    assert "$1.24 booked" in result.message


def test_a_resumed_step_sends_the_reference_body_again_without_the_flag(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_video")
    key = f"{load_production(desk).idempotency_prefix}-step-video"
    set_phase(desk, "ready_video", reference_mode_keys=[key], estimate_usd=1.24)
    _ep1_take(api)

    orchestrate.run_step(desk)

    (body,) = api.posted(VIDEO)
    assert body["reference_mode"] is True
    assert _video_keys(api) == [key]


def test_a_plain_step_film_already_sent_is_not_resent_with_the_flag(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_video", video_enrolled_suffix="")
    (desk / "ep01" / "api" / "16_video_request.json").write_text("{}", encoding="utf-8")
    _ep1_take(api)

    with pytest.raises(
        RuntimeError, match="already sent once without --reference-mode"
    ):
        orchestrate.run_step(desk, reference_mode=True)

    assert api.posted(VIDEO) == []
    assert api.posted(STEP_ESTIMATE) == []


def test_the_flag_on_another_step_sends_nothing(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_spend", estimate_usd=0.6)
    with pytest.raises(
        RuntimeError, match="--reference-mode goes with `step --confirm-spend`"
    ):
        orchestrate.run_step(desk, reference_mode=True)
    assert api.posted(VIDEO) == []
    assert load_production(desk).phase == "wait_spend"


def test_step_refuses_both_trials_together_up_front(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_spend", estimate_usd=0.6)
    _ep1_take(api)
    with pytest.raises(RuntimeError, match="cannot go together"):
        orchestrate.run_step(
            desk, confirm_spend=True, reference_mode=True, single_frame_start=True
        )
    assert api.posted(VIDEO) == [] and api.posted(STEP_ESTIMATE) == []
    assert load_production(desk).phase == "wait_spend"


def test_a_step_the_server_will_not_price_stops_at_the_spend_yes(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=0.6)
    _ep1_take(api)
    api.routes[("POST", STEP_ESTIMATE)] = lambda m, p, body: (
        _raise(ESTIMATE_OLDER_DEPLOY) if (body or {}).get("reference_mode") else TURBO
    )

    with pytest.raises(RuntimeError, match="would not price the reference lane"):
        orchestrate.run_step(desk, confirm_spend=True, reference_mode=True)

    assert api.posted(VIDEO) == []
    state = load_production(desk)
    assert state.phase == "wait_spend" and state.reference_mode_keys == []
    assert state.estimate_usd == pytest.approx(0.6)


@pytest.mark.parametrize("refusal", [OPERATOR_ONLY, WRONG_LANE, OLDER_DEPLOY])
def test_a_refused_trial_step_stops_plainly_and_goes_back_to_the_spend_yes(
    desk: Path, api: FakeApi, refusal: SystemExit
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=0.6)
    _ep1_take(api)
    api.routes[("POST", VIDEO)] = refusal
    spend_before = load_series(desk).spend_usd

    with pytest.raises(RuntimeError) as caught:
        orchestrate.run_step(desk, confirm_spend=True, reference_mode=True)

    assert "isn't available for this show or account" in str(caught.value)
    state = load_production(desk)
    assert state.phase == "wait_spend"
    assert state.reference_mode_keys == []
    assert load_series(desk).spend_usd == pytest.approx(spend_before)

    _ep1_take(api)
    orchestrate.run_step(desk, confirm_spend=True)
    first, second = api.posted(VIDEO)
    assert first["reference_mode"] is True and "reference_mode" not in second
