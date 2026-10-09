"""``--single-frame-start`` / ``--no-single-frame-start`` on ``film`` and ``step`` (fictora-drama #658).

Since 9 Oct 2026 the single-picture opening is off by default again
(fictora-drama #694), so with no flag the film body is exactly as before and
carries no field, and the server opens on the storyboard.
``--single-frame-start`` sends ``true`` (tests only);
``--no-single-frame-start`` sends ``false`` (the storyboard, said explicitly). Either choice is kept per film key so a resume sends the same
body; a server refusal stops with a plain message and nothing charged.
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
NOTE = stages.SINGLE_FRAME_START_NOTE
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
        assert seen.pop("no_single_frame_start") is False
        assert main([*argv, "--single-frame-start"]) == 0
        assert seen.pop("single_frame_start") is True
        assert seen.pop("no_single_frame_start") is False
        assert main([*argv, "--no-single-frame-start"]) == 0
        assert seen.pop("single_frame_start") is False
        assert seen.pop("no_single_frame_start") is True
        with pytest.raises(SystemExit) as caught:
            main([*argv, "--single-frame-start", "--no-single-frame-start"])
        assert caught.value.code == 2


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


# --- --no-single-frame-start (8 Oct 2026: the single picture is the default) ------------------------

OFF_NOTE = stages.SINGLE_FRAME_START_OFF_NOTE


def test_the_choice_helper_is_tri_state_and_refuses_both() -> None:
    assert stages.single_frame_start_choice() is None
    assert stages.single_frame_start_choice(on=True) is True
    assert stages.single_frame_start_choice(off=True) is False
    with pytest.raises(ValueError, match="cannot go together"):
        stages.single_frame_start_choice(on=True, off=True)


def test_the_body_sends_false_only_when_asked(desk30: Path, api30: FakeApi) -> None:
    built = {
        "spine": api30.spine("sp1"),
        "prompt": "A shop at closing time.",
        "preset_id": "modern-romance",
        "preset_version": "2",
        "episode": 2,
    }
    plain = stages.video_request_body(api30, **built)
    off = stages.video_request_body(api30, **built, single_frame_start=False)
    assert "single_frame_start" not in plain
    assert off.pop("single_frame_start") is False
    assert json.dumps(off, sort_keys=True) == json.dumps(plain, sort_keys=True)


def test_film_with_no_single_frame_start_sends_false_and_keeps_it_on_the_unit(
    desk30: Path, api30: FakeApi, capsys: pytest.CaptureFixture[str]
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
            no_single_frame_start=True,
        )

    assert OFF_NOTE in capsys.readouterr().out
    pending = load_production(desk30).pending[UNIT]
    assert pending["single_frame_start"] is False and pending["job_id"] is None
    _take_two_of_episode_two(api30)
    # Resumed with no flag: the same key goes out with the same body.
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)
    first, second = api30.posted(VIDEO)
    assert first == second and second["single_frame_start"] is False
    one, two = _video_keys(api30)
    assert one == two


def test_film_with_both_flags_sends_nothing(desk30: Path, api30: FakeApi) -> None:
    _priced_take_two(desk30, api30)
    with pytest.raises(CommandStopped, match="cannot go together"):
        run_film(
            desk30,
            episode=2,
            take_id="t2",
            cause=CAUSE,
            confirm_spend=True,
            single_frame_start=True,
            no_single_frame_start=True,
        )
    assert api30.posted(VIDEO) == []


def test_the_off_flag_without_the_spend_yes_sends_nothing(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    with pytest.raises(
        CommandStopped, match="--no-single-frame-start goes with --confirm-spend"
    ):
        run_film(
            desk30, episode=2, take_id="t2", cause=CAUSE, no_single_frame_start=True
        )
    assert api30.calls == []


@pytest.mark.parametrize(
    ("first", "asked", "match"),
    [
        (
            None,
            {"no_single_frame_start": True},
            "already sent without --no-single-frame-start",
        ),
        (
            True,
            {"no_single_frame_start": True},
            "already sent with --single-frame-start",
        ),
        (
            False,
            {"single_frame_start": True},
            "already sent with --no-single-frame-start",
        ),
    ],
)
def test_a_resumed_film_never_changes_its_opening(
    desk30: Path, api30: FakeApi, first: bool | None, asked: dict[str, bool], match: str
) -> None:
    _priced_take_two(desk30, api30)
    state = load_production(desk30)
    unit: dict[str, object] = {"key": "k-1", "job_id": None}
    if first is not None:
        unit["single_frame_start"] = first
    state.pending[UNIT] = unit
    save_production(desk30, state)

    with pytest.raises(CommandStopped, match=match):
        run_film(
            desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True, **asked
        )

    assert api30.posted(VIDEO) == []


def test_an_old_pending_unit_without_the_field_resends_the_plain_body(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    state = load_production(desk30)
    state.pending[UNIT] = {"key": "k-1", "job_id": None}
    save_production(desk30, state)

    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    (body,) = api30.posted(VIDEO)
    assert "single_frame_start" not in body
    assert _video_keys(api30) == ["k-1"]


def test_a_refused_off_film_stops_plainly_on_a_fresh_key(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    good = api30.routes[("POST", VIDEO)]
    api30.routes[("POST", VIDEO)] = WRONG_LANE

    with pytest.raises(CommandStopped, match="nothing was filmed or charged"):
        run_film(
            desk30,
            episode=2,
            take_id="t2",
            cause=CAUSE,
            confirm_spend=True,
            no_single_frame_start=True,
        )

    assert UNIT not in load_production(desk30).pending
    api30.routes[("POST", VIDEO)] = good
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)
    first, second = api30.posted(VIDEO)
    assert first["single_frame_start"] is False and "single_frame_start" not in second
    one, two = _video_keys(api30)
    assert one != two


def test_step_with_no_single_frame_start_sends_false_and_keeps_it_on_the_key(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ep1_take(api)

    orchestrate.run_step(desk, confirm_spend=True, no_single_frame_start=True)

    (body,) = api.posted(VIDEO)
    assert body["single_frame_start"] is False
    state = load_production(desk)
    assert state.single_frame_start_off_keys == _video_keys(api)
    assert state.single_frame_start_keys == []
    assert OFF_NOTE in capsys.readouterr().err


def test_a_resumed_step_sends_the_off_body_again_without_the_flag(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_video")
    key = f"{load_production(desk).idempotency_prefix}-step-video"
    set_phase(desk, "ready_video", single_frame_start_off_keys=[key])
    _ep1_take(api)

    orchestrate.run_step(desk)

    (body,) = api.posted(VIDEO)
    assert body["single_frame_start"] is False
    assert _video_keys(api) == [key]


@pytest.mark.parametrize(
    ("kept", "asked", "match"),
    [
        (
            "single_frame_start_off_keys",
            {"single_frame_start": True},
            "already sent with --no-single-frame-start",
        ),
        (
            "single_frame_start_keys",
            {"no_single_frame_start": True},
            "already sent with --single-frame-start",
        ),
    ],
)
def test_a_resumed_step_never_changes_its_opening(
    desk: Path, api: FakeApi, kept: str, asked: dict[str, bool], match: str
) -> None:
    set_phase(desk, "ready_video")
    key = f"{load_production(desk).idempotency_prefix}-step-video"
    set_phase(desk, "ready_video", **{kept: [key]})
    _ep1_take(api)

    with pytest.raises(RuntimeError, match=match):
        orchestrate.run_step(desk, **asked)

    assert api.posted(VIDEO) == []


def test_a_plain_step_film_already_sent_is_not_resent_with_the_off_flag(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_video", video_enrolled_suffix="")
    (desk / "ep01" / "api" / "16_video_request.json").write_text("{}", encoding="utf-8")
    _ep1_take(api)

    with pytest.raises(
        RuntimeError, match="already sent once without --no-single-frame-start"
    ):
        orchestrate.run_step(desk, no_single_frame_start=True)

    assert api.posted(VIDEO) == []


def test_step_with_both_flags_sends_nothing(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    with pytest.raises(RuntimeError, match="cannot go together"):
        orchestrate.run_step(
            desk,
            confirm_spend=True,
            single_frame_start=True,
            no_single_frame_start=True,
        )
    assert api.posted(VIDEO) == []


def test_the_off_flag_on_another_step_sends_nothing(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    with pytest.raises(
        RuntimeError, match="--no-single-frame-start goes with `step --confirm-spend`"
    ):
        orchestrate.run_step(desk, no_single_frame_start=True)
    assert api.posted(VIDEO) == []


def test_a_refused_off_step_goes_back_to_the_spend_yes(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "wait_spend", estimate_usd=1.2)
    _ep1_take(api)
    api.routes[("POST", VIDEO)] = WRONG_LANE

    with pytest.raises(RuntimeError, match="isn't available for this show or account"):
        orchestrate.run_step(desk, confirm_spend=True, no_single_frame_start=True)

    state = load_production(desk)
    assert state.phase == "wait_spend"
    assert state.single_frame_start_off_keys == []


def test_an_old_production_file_loads_and_keeps_its_bytes(desk: Path) -> None:
    path = production_path(desk)
    save_production(desk, load_production(desk))  # settle what loading fills in
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("single_frame_start_off_keys", None)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    state = load_production(desk)
    assert state.single_frame_start_off_keys == []
    save_production(desk, state)
    assert path.read_text(encoding="utf-8") == before


# --- the words (9 Oct 2026, fictora-drama #694: off by default again) -------------------------------


def _help(capsys: pytest.CaptureFixture[str], command: str) -> str:
    from creation.cli_produce import main as produce_main

    with pytest.raises(SystemExit):
        produce_main([command, "--help"])
    return capsys.readouterr().out


def test_no_help_or_skill_text_calls_the_single_picture_opening_the_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Kit #183's wording said the single picture was the server default; #694 turned it off."""

    root = Path(__file__).resolve().parents[1]
    texts = {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in (
            root / ".cursor/skills/episode-production/reference.md",
            root / ".claude/skills/episode-production/reference.md",
            root / "docs/content-ops/backlog.md",
        )
    }
    texts["film --help"] = _help(capsys, "film")
    texts["step --help"] = _help(capsys, "step")

    stale = (
        "is the default (changed 2026-10-08)",
        "already the server's default",
        "single-picture default",
    )
    for name, text in texts.items():
        flat = " ".join(text.split())
        for phrase in stale:
            assert phrase not in flat, f"{name} still says {phrase!r}"
    for command in ("film --help", "step --help"):
        assert "Tests only" in " ".join(texts[command].split()), texts[command]
    skill = " ".join(texts[".cursor/skills/episode-production/reference.md"].split())
    assert "OFF by default again" in skill and "tests only" in skill
    assert (
        stages.SINGLE_FRAME_START_OFF_NOTE
        == "Opening each take on the storyboard (--no-single-frame-start)"
    )
