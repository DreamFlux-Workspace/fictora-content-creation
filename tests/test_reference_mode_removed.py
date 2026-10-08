"""``--reference-mode`` is removed (founder, 8 Oct 2026: double the take cost was rejected).

It was an operator trial on ``film`` and ``step`` (kit #171, fictora-drama
#664): each take filmed on H3 Max reference-to-video. Now the flag is gone,
no body carries ``reference_mode``, and the server refuses the field (422).

A desk that holds a film first sent with the trial must not send it again: its
body can no longer go out, and the same key with another body is a new server
command (the key digests the body), so it could film twice. Such a film stops
with a plain message and nothing sent. A film the server already admitted is
picked up and collected as usual (no body is sent on a pick-up).
"""

from __future__ import annotations

import inspect
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
)

desk30 = film_tests.desk30  # the 30s desk fixture, pointed at episode 2
api30 = film_tests.api30

UNIT = "film-ep02-t2-s2"
REMOVED = "first sent with --reference-mode, which was removed on 8 Oct 2026"


def _priced_take_two(desk: Path, api: FakeApi) -> None:
    _filmed_once(desk)
    api.routes[("POST", ESTIMATE)] = {
        "cost_estimate": {"total_usd": "0.60", "takes": 1}
    }
    run_film(desk, episode=2, take_id="t2", cause=CAUSE)
    _take_two_of_episode_two(api)


# --- the flag is gone ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["film", "step"])
def test_the_flag_is_no_longer_accepted(desk30: Path, command: str) -> None:
    argv = [command, "--desk", str(desk30), "--confirm-spend", "--reference-mode"]
    if command == "film":
        argv[3:3] = ["--episode", "2"]
    with pytest.raises(SystemExit) as refused:
        main(argv)
    assert refused.value.code == 2  # argparse: unrecognized arguments


def test_no_entry_point_takes_the_option() -> None:
    for fn in (
        run_film,
        orchestrate.run_step,
        stages.video_request_body,
        stages.estimate_batch,
    ):
        assert "reference_mode" not in inspect.signature(fn).parameters, fn.__name__
    assert not hasattr(stages, "ReferenceModeRefused")


def test_a_film_body_never_carries_the_field(desk30: Path, api30: FakeApi) -> None:
    _priced_take_two(desk30, api30)
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)
    (body,) = api30.posted(VIDEO)
    assert "reference_mode" not in body
    assert all("reference_mode" not in (b or {}) for b in api30.posted(ESTIMATE))
    assert "reference_mode" not in production_path(desk30).read_text(encoding="utf-8")


# --- a desk that used the trial -----------------------------------------------------------------


def test_film_stops_plainly_on_an_unsent_reference_film(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    state = load_production(desk30)
    legacy = {
        "key": "k-ref-1",
        "job_id": None,
        "reference_mode": True,
        "reference_take_usd": 1.24,
    }
    state.pending[UNIT] = dict(legacy)
    save_production(desk30, state)

    with pytest.raises(CommandStopped, match=REMOVED) as stopped:
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    assert "Nothing was sent or charged" in str(stopped.value)
    assert api30.posted(VIDEO) == []
    # The desk is left as it was, for engineering to check the first send.
    assert load_production(desk30).pending[UNIT] == legacy


def test_film_picks_up_an_admitted_reference_film_without_posting(
    desk30: Path, api30: FakeApi
) -> None:
    _priced_take_two(desk30, api30)
    state = load_production(desk30)
    state.pending[UNIT] = {
        "key": "k-ref-1",
        "job_id": "job_video_9",
        "reference_mode": True,
    }
    save_production(desk30, state)

    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    assert api30.posted(VIDEO) == []
    assert episode_by_ordinal(load_series(desk30), 2).takes[1].filmed_count == 2


def _ep1_take(api: FakeApi) -> None:
    _video_routes(api, facts_lines=None, children=("job_take_a",))
    api.routes[("GET", "/v1/jobs/job_take_a/take-facts")] = {
        "take_facts": {"endpoint_id": ""}
    }


def test_step_stops_plainly_on_an_unsent_reference_key(
    desk: Path, api: FakeApi
) -> None:
    set_phase(desk, "ready_video")
    key = f"{load_production(desk).idempotency_prefix}-step-video"
    set_phase(desk, "ready_video", reference_mode_keys=[key], estimate_usd=1.24)
    _ep1_take(api)

    with pytest.raises(RuntimeError, match=REMOVED):
        orchestrate.run_step(desk)

    assert api.posted(VIDEO) == []
    # Still recorded (the file keeps the legacy key), so a later step stops the same way.
    assert load_production(desk).reference_mode_keys == [key]
    assert json.loads(production_path(desk).read_text(encoding="utf-8"))[
        "reference_mode_keys"
    ] == [key]


def test_a_plain_step_sends_the_body_as_before(desk: Path, api: FakeApi) -> None:
    set_phase(desk, "wait_spend", estimate_usd=0.6)
    _ep1_take(api)

    orchestrate.run_step(desk, confirm_spend=True)

    (body,) = api.posted(VIDEO)
    assert "reference_mode" not in body
    assert "reference_mode" not in production_path(desk).read_text(encoding="utf-8")
