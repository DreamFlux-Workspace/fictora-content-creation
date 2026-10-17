"""Shot clips (a test opt-in): ``shot-clips --on`` and ``film --shot-clips`` (fictora-drama feat/shot-clips).

A show that opted in films each take one clip per board row; the server cuts
and joins the clips into the take, so the desk gets one take per board as
before. The kit prices every clip from the server's estimate before spend,
never films an opted-in show without the flag (nor the flag on a show that did
not opt in), and books a filmed take as the sum of its clips.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest


from creation.cli_produce import main
from creation.episode_commands import CommandStopped, run_film
from creation.harness import stages_gated as stages
from creation.prices import take_facts_usd
from creation.shot_clips import (
    NOT_OPTED_IN,
    NOT_PRICED,
    films_shot_clips,
    set_shot_clips,
    shot_clips_film_refusal,
    shot_clips_priced,
)
from fake_api import FakeApi
import test_film_episode as film_tests
from test_film_episode import (
    CAUSE,
    ESTIMATE,
    VIDEO,
    _filmed_once,
    _take_two_of_episode_two,
)

desk30 = film_tests.desk30
api30 = film_tests.api30

TURBO = "minimax/h3-max-turbo/image-to-video"
PRICED = {
    "cost_estimate": {
        "total_usd": "1.80",
        "priced_on": "2026-10-16",
        "takes": 2,
        "billed_seconds": 45,
        "usd_per_second": "0.04",
        "video_usd": "1.80",
        "shot_clips": 9,
        "shot_clips_quote": "sc1:abc123",
        "boards": 1,
    }
}
ROUTE = "/v1/spines/sp1/shot-clips"


def _opted_in(api: FakeApi) -> None:
    api.spine_doc["shot_clips"] = True


# --- the price of a filmed take ------------------------------------------------------------------


def test_a_shot_clips_take_is_booked_at_the_dollars_the_server_priced_each_clip() -> (
    None
):
    clip = {"row": 1, "planned_seconds": 3.0, "billed_seconds": 5, "billed_usd": "0.20"}
    facts = {
        "endpoint_id": TURBO,
        "resolution": "768P",
        "duration_seconds": 15.0,
        "reference_image_count": 0,
        "shot_clips": [dict(clip, job_id=f"c{n}") for n in range(1, 6)],
    }
    day = date(2026, 10, 16)

    booked = take_facts_usd(facts, on=day)

    assert booked is not None and booked[0] == pytest.approx(1.00)
    assert (
        "5 shot clip(s) of 5 + 5 + 5 + 5 + 5 s, as the server priced them" in booked[1]
    )
    # A clip the server did not price is never guessed by the kit.
    unpriced = dict(facts, shot_clips=[{"row": 1, "billed_seconds": 5}])
    assert take_facts_usd(unpriced, on=day) is None


# --- the refusals, before anything is priced or sent ------------------------------------------------


def test_the_flag_and_the_show_must_agree() -> None:
    plain = {"spine_id": "sp1"}
    opted = {"spine_id": "sp1", "shot_clips": True}

    assert shot_clips_film_refusal(plain, flag=False, episode=2) is None
    assert shot_clips_film_refusal(opted, flag=True, episode=2) is None
    assert "--shot-clips" in (
        shot_clips_film_refusal(opted, flag=False, episode=2) or ""
    )
    assert shot_clips_film_refusal(plain, flag=True, episode=2) == NOT_OPTED_IN
    assert (
        films_shot_clips({"spine": opted})
        and not films_shot_clips(plain)
        and not films_shot_clips(None)
    )


def test_an_opted_in_show_is_never_priced_or_filmed_without_the_flag(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    _opted_in(api30)
    api30.routes[("POST", ESTIMATE)] = PRICED

    with pytest.raises(CommandStopped, match="--shot-clips"):
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE)

    # Only the free estimate is asked (it says this episode films as clips); nothing is filmed.
    assert api30.posted(VIDEO) == []


def test_the_flag_on_a_show_that_did_not_opt_in_sends_nothing(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    # A show that never opted in: the server prices whole takes.
    api30.routes[("POST", ESTIMATE)] = {
        "cost_estimate": {"total_usd": "1.20", "takes": 2}
    }

    with pytest.raises(CommandStopped, match="has not"):
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, shot_clips=True)

    assert api30.posted(VIDEO) == []


def test_a_server_that_does_not_price_clips_is_refused_before_spend(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    _opted_in(api30)
    api30.routes[("POST", ESTIMATE)] = {
        "cost_estimate": {"total_usd": "1.20", "takes": 2}
    }

    with pytest.raises(CommandStopped) as caught:
        run_film(desk30, episode=2, cause=CAUSE, shot_clips=True)

    assert str(caught.value) == NOT_PRICED
    assert api30.posted(VIDEO) == []
    assert shot_clips_priced({"estimate_skipped": True}) is None


# --- price, yes, film --------------------------------------------------------------------------------


def test_film_prices_every_clip_then_films_with_the_same_body(
    desk30: Path, api30: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _filmed_once(desk30)
    _opted_in(api30)
    api30.routes[("POST", ESTIMATE)] = PRICED

    text = run_film(desk30, episode=2, cause=CAUSE, shot_clips=True)

    assert "Shot clips: 9 short clips, one per shot" in text
    assert "45 seconds in all) and 1 board(s) to draw" in text
    assert "15 s a take" not in text
    assert "--episode 2 --shot-clips --cause" in text
    assert api30.posted(VIDEO) == []
    capsys.readouterr()

    with pytest.raises(CommandStopped, match="--shot-clips"):
        run_film(desk30, episode=2, cause=CAUSE, confirm_spend=True)
    assert api30.posted(VIDEO) == []


def test_one_take_films_after_its_clips_were_priced_and_books_them(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    _opted_in(api30)
    api30.routes[("POST", ESTIMATE)] = {
        "cost_estimate": {**PRICED["cost_estimate"], "shot_clips": 4}
    }
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, shot_clips=True)
    _take_two_of_episode_two(api30)
    api30.routes[("GET", "/v1/jobs/job_take_e2t2/take-facts")] = {
        "take_facts": {
            "endpoint_id": TURBO,
            "resolution": "768P",
            "duration_seconds": 15.0,
            "shot_clips": [
                {
                    "job_id": f"c{n}",
                    "row": n,
                    "start_s": 3.75 * (n - 1),
                    "planned_seconds": 3.75,
                    "billed_seconds": 5,
                    "billed_usd": "0.20",
                }
                for n in range(1, 5)
            ],
        }
    }

    text = run_film(
        desk30,
        episode=2,
        take_id="t2",
        cause=CAUSE,
        confirm_spend=True,
        shot_clips=True,
    )

    (body,) = api30.posted(VIDEO)
    # The kit passes the server's token for the number the human said yes to, nothing of its own.
    assert body["shot_clips_quote"] == "sc1:abc123"
    assert {key for key in body if key.startswith("shot_clips")} == {"shot_clips_quote"}
    # Four clips at the server's $0.20 each: $0.80, not the joined take's 15 s ($0.60).
    assert "$0.80 booked" in text


def test_the_film_body_is_the_plain_body(api30: FakeApi) -> None:
    built = {
        "spine": api30.spine("sp1"),
        "prompt": "A shop at closing time.",
        "preset_id": "modern-romance",
        "preset_version": "2",
        "episode": 2,
        "api_captions": True,
    }
    plain = stages.video_request_body(api30, **built)
    opted = dict(built, spine={**built["spine"], "shot_clips": True})

    with pytest.raises(SystemExit, match="--shot-clips"):
        # `step` builds the same body: an opted-in show never films without the flag.
        stages.video_request_body(api30, **opted)
    clips = stages.video_request_body(api30, **opted, shot_clips=True)
    assert json.dumps(clips, sort_keys=True) == json.dumps(plain, sort_keys=True)
    quoted = stages.video_request_body(
        api30, **opted, shot_clips=True, shot_clips_quote="sc1:abc"
    )
    assert quoted.pop("shot_clips_quote") == "sc1:abc"
    assert json.dumps(quoted, sort_keys=True) == json.dumps(plain, sort_keys=True)


# --- the free switch -----------------------------------------------------------------------------------


def test_the_switch_posts_the_route_and_reads_a_stale_version_again(
    api30: FakeApi,
) -> None:
    answers = iter(
        [
            (409, {"error": {"code": "spine_version_conflict"}}),
            {
                "shot_clips": True,
                "changed": True,
                "applied": True,
                "notice": "From the next film on ...",
            },
        ]
    )
    api30.routes[("POST", ROUTE)] = lambda *_args: next(answers)

    answer = set_shot_clips(api30, api30.spine("sp1"), True)

    assert answer["applied"] is True
    bodies = api30.posted(ROUTE)
    assert len(bodies) == 2 and all(body["on"] is True for body in bodies)


def test_an_older_server_without_the_route_changes_nothing(api30: FakeApi) -> None:
    api30.routes[("POST", ROUTE)] = (404, {"detail": "Not Found"})

    with pytest.raises(RuntimeError, match="no shot-clips route"):
        set_shot_clips(api30, api30.spine("sp1"), True)


def test_the_commands_take_the_flags(
    desk30: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation import episode_commands
    from creation import shot_clips as module

    seen: dict[str, object] = {}
    monkeypatch.setattr(episode_commands, "run_film", lambda *_a, **kw: seen.update(kw))
    monkeypatch.setattr(
        module,
        "run_shot_clips",
        lambda _desk, *, set_to=None: seen.update(set_to=set_to),
    )

    assert main(["film", "--desk", str(desk30), "--episode", "2", "--shot-clips"]) == 0
    assert seen.pop("shot_clips") is True
    assert main(["film", "--desk", str(desk30), "--episode", "2"]) == 0
    assert seen.pop("shot_clips") is False
    for argv, wanted in ((["--on"], True), (["--off"], False), ([], None)):
        seen.clear()
        assert main(["shot-clips", "--desk", str(desk30), *argv]) == 0
        assert seen["set_to"] is wanted


# --- film ONE clip of a take again (fictora-drama: per-clip re-film, Low Tide 17 Oct 2026) -----------------------

CLIP_ROUTE = "/v1/video-generations/job_video_A/takes/2/clips/1/refilm"
CLIP_PRICE = {
    "source_job_id": "job_video_A",
    "episode_id": "ep_02",
    "take_index": 2,
    "row": 1,
    "clips_refilmed": 1,
    "clips_reused": 3,
    "stills": 1,
    "still_usd_each": "0.30",
    "clip_billed_seconds": 5,
    "clip_usd": "0.20",
    "total_usd": "0.50",
    "shot_clips_quote": "sc1r:feedface",
    "admitted": False,
    "notice": "Re-films row 1 of take 2 alone.",
}


def _clip_take(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> None:
    """Take 2 of episode 2 was filmed by job_video_A; the server prices and admits its one-clip re-film."""

    from creation.post import take_handles

    monkeypatch.setattr(
        take_handles,
        "take_coordinate",
        lambda desk, episode, take_id: take_handles.TakeCoordinate(
            "job_video_A", 2, "job_take_A2"
        ),
    )

    def route(_method: str, _path: str, body: dict | None) -> dict:
        if body and body.get("preview") is False:
            assert body["shot_clips_quote"] == "sc1r:feedface"
            return {**CLIP_PRICE, "admitted": True, "job_id": "job_video_9"}
        return CLIP_PRICE

    api.routes[("POST", CLIP_ROUTE)] = route


def test_film_one_clip_is_priced_by_the_server_then_films_with_its_token_and_books_only_what_it_filmed(
    desk30: Path, api30: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _filmed_once(desk30)
    _opted_in(api30)
    _clip_take(monkeypatch, api30)

    text = run_film(
        desk30, episode=2, take_id="t2", cause=CAUSE, shot_clips=True, clip=1
    )

    assert api30.posted(CLIP_ROUTE) == [{"preview": True}]
    assert api30.posted(ESTIMATE) == [] and api30.posted(VIDEO) == []
    assert "Re-film shot 1 of take 2 alone: one new opening picture ($0.30)" in text
    assert "one 5 second clip ($0.20)" in text and "About $0.50 in all" in text
    assert "--take t2 --shot-clips --clip 1" in text

    _take_two_of_episode_two(api30)
    api30.routes[("GET", "/v1/jobs/job_take_e2t2/take-facts")] = {
        "take_facts": {
            "endpoint_id": TURBO,
            "resolution": "768P",
            "duration_seconds": 14.0,
            # The server lists the reused clips at $0 and the new clip with its picture.
            "shot_clips": [
                {
                    "job_id": "c1",
                    "row": 1,
                    "start_s": 0.0,
                    "planned_seconds": 1.64,
                    "billed_seconds": 5,
                    "billed_usd": "0.20",
                    "opening_still_usd": "0.30",
                },
                *(
                    {
                        "job_id": f"c{n}",
                        "row": n,
                        "start_s": 3.0 * n,
                        "planned_seconds": 3.0,
                        "billed_seconds": 5,
                        "billed_usd": "0",
                    }
                    for n in (2, 3, 4)
                ),
            ],
        }
    }

    filmed = run_film(
        desk30,
        episode=2,
        take_id="t2",
        cause=CAUSE,
        confirm_spend=True,
        shot_clips=True,
        clip=1,
    )

    posted = api30.posted(CLIP_ROUTE)
    assert posted[-1] == {"preview": False, "shot_clips_quote": "sc1r:feedface"}
    # Nothing of the kit's own goes out: no film body, no seed.
    assert api30.posted(VIDEO) == []
    assert "$0.50 booked" in filmed


def test_film_one_clip_needs_its_take_and_shot_clips_and_an_older_server_sends_nothing(
    desk30: Path, api30: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation.shot_clips import CLIP_REFILM_MISSING

    _filmed_once(desk30)
    _opted_in(api30)
    with pytest.raises(CommandStopped, match="--clip goes with --take tK"):
        run_film(desk30, episode=2, cause=CAUSE, shot_clips=True, clip=1)
    with pytest.raises(CommandStopped, match="add --shot-clips"):
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, clip=1)
    _clip_take(monkeypatch, api30)
    api30.routes[("POST", CLIP_ROUTE)] = (404, {"detail": "Not Found"})
    with pytest.raises(CommandStopped) as caught:
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, shot_clips=True, clip=1)
    assert str(caught.value) == CLIP_REFILM_MISSING.format(episode=2, take="t2")
    assert api30.posted(VIDEO) == [] and api30.posted(ESTIMATE) == []
    # Without a price shown, a confirm sends nothing.
    with pytest.raises(CommandStopped, match="no price shown"):
        run_film(
            desk30,
            episode=2,
            take_id="t2",
            cause=CAUSE,
            confirm_spend=True,
            shot_clips=True,
            clip=1,
        )


def test_the_film_command_takes_the_clip_flag(
    desk30: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation import episode_commands

    seen: dict[str, object] = {}
    monkeypatch.setattr(episode_commands, "run_film", lambda *_a, **kw: seen.update(kw))
    argv = ["film", "--desk", str(desk30), "--episode", "2", "--take", "t2"]
    assert main([*argv, "--shot-clips", "--clip", "1"]) == 0
    assert seen["clip"] == 1 and seen["shot_clips"] is True
    assert main(argv) == 0
    assert seen["clip"] is None


# --- Shot clips turned off (founder, 17 Oct 2026): the server decides per episode -----------------------------

WHOLE_TAKES = {"cost_estimate": {"total_usd": "1.20", "takes": 2}}


def _turned_off(api: FakeApi, *, clip_episodes: list[str]) -> None:
    """The server's story after off: the switch's flag stays, only the filmed clip episodes keep clips."""

    api.spine_doc["shot_clips"] = True
    api.spine_doc["shot_clips_episode_ids"] = clip_episodes


def test_a_whole_take_episode_of_a_turned_off_show_films_without_the_flag(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    _turned_off(api30, clip_episodes=["ep_01"])
    api30.routes[("POST", ESTIMATE)] = WHOLE_TAKES

    text = run_film(desk30, episode=2, cause=CAUSE)

    assert "Shot clips" not in text and "--shot-clips" not in text
    with pytest.raises(CommandStopped, match="films whole takes"):
        run_film(desk30, episode=2, cause=CAUSE, shot_clips=True)
    try:
        # The yes: decided from the estimate the human saw, never from the story's flag.
        run_film(desk30, episode=2, cause=CAUSE, confirm_spend=True)
    except CommandStopped as stopped:
        assert "--shot-clips" not in str(stopped)
    except AssertionError as unrouted:
        assert "unexpected" in str(unrouted)


def test_an_episode_filmed_as_clips_on_a_turned_off_show_still_needs_the_flag(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    _turned_off(api30, clip_episodes=["ep_02"])
    api30.routes[("POST", ESTIMATE)] = PRICED

    with pytest.raises(CommandStopped, match="film it with"):
        run_film(desk30, episode=2, cause=CAUSE)
    assert "Shot clips: 9 short clips" in run_film(
        desk30, episode=2, cause=CAUSE, shot_clips=True
    )


def test_the_episode_decision_reads_only_the_servers_answers() -> None:
    from creation.shot_clips import NOT_PRICED, WHOLE_TAKES_EPISODE, episode_films_clips

    assert episode_films_clips(PRICED) is True
    assert episode_films_clips(WHOLE_TAKES) is False
    assert episode_films_clips({"estimate_skipped": True}) is None
    # An answer without dollars does not say (a refused or partial estimate).
    assert episode_films_clips({"cost_estimate": {"takes": 2}}) is None
    off = {"shot_clips": True, "shot_clips_episode_ids": ["ep_01"]}
    on = {"shot_clips": True}
    # No estimate: the show's switch decides, as before.
    assert shot_clips_film_refusal(on, flag=False, episode=2) is not None
    assert shot_clips_film_refusal({}, flag=False, episode=2) is None
    # The switch on but no clips priced: an older server. Turned off: a whole-take episode.
    assert (
        shot_clips_film_refusal(on, flag=True, episode=2, estimate=WHOLE_TAKES)
        == NOT_PRICED
    )
    assert shot_clips_film_refusal(
        off, flag=True, episode=2, estimate=WHOLE_TAKES
    ) == WHOLE_TAKES_EPISODE.format(episode=2)
    assert (
        shot_clips_film_refusal(off, flag=False, episode=2, estimate=WHOLE_TAKES)
        is None
    )


def test_the_status_says_off_and_names_the_episodes_that_keep_their_clips(
    desk30: Path, api30: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    from types import SimpleNamespace

    import creation.shot_clips as module
    from creation.shot_clips import run_shot_clips

    _turned_off(api30, clip_episodes=["ep_02"])
    session = SimpleNamespace(client=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module, "open_api", lambda *_args: session)
    monkeypatch.setattr(module, "refresh_spine", lambda *_args: api30.spine_doc)
    out = io.StringIO()

    assert run_shot_clips(desk30, out=out) is False
    assert "off - whole takes. ep_02 keep their shot clips" in out.getvalue()


def test_step_films_a_whole_take_episode_of_a_turned_off_show(
    desk: Path, api: FakeApi
) -> None:
    from conftest import set_phase

    from creation import orchestrate

    set_phase(desk, "ready_video")
    api.spine_doc["shot_clips"] = True
    api.spine_doc["shot_clips_episode_ids"] = ["ep_99"]
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = WHOLE_TAKES
    api.routes[("POST", "/v1/video-generations")] = SystemExit("reached the film")

    # The server's estimate says whole takes: step films it (it reaches the film request).
    with pytest.raises(SystemExit, match="reached the film"):
        orchestrate.run_step(desk)

    # Still on (no restriction) and the server prices clips: step never films shot clips.
    set_phase(desk, "ready_video")
    del api.spine_doc["shot_clips_episode_ids"]
    api.routes[("POST", "/v1/spines/sp1/batches/estimate")] = PRICED
    with pytest.raises((SystemExit, RuntimeError), match="--shot-clips"):
        orchestrate.run_step(desk)
