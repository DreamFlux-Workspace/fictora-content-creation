"""Episode-scoped filming: film episode N alone, or only take K of it, against a fake API (request bodies asserted)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase, turbo_take_usd
from creation import episode_commands, orchestrate
from creation.episode_commands import CommandStopped, run_film
from creation.harness import stages_gated as stages
from creation.ops.floor import approve_board, init_series_desk, record_filmed
from creation.ops.state import episode_by_ordinal, load_series
from creation.ops.folder import next_versioned_path
from creation.production_state import ensure_production, load_production, save_production
from fake_api import FakeApi, openapi_doc, png_bytes, spine_fixture

VIDEO = "/v1/video-generations"
ESTIMATE = "/v1/spines/sp1/batches/estimate"


@pytest.fixture
def desk30(tmp_path: Path) -> Path:
    """A 30s desk (two takes an episode), episodes 1 and 2 on the desk, pointed at episode 2."""

    path = init_series_desk(tmp_path, "Closing Time", band="30s", episode_count=2)
    state = ensure_production(path, prompt="A shop at closing time.", preset_id="modern-romance", preset_version="2")
    state.spine_id = "sp1"
    state.episode_ordinal = 2
    state.phase = "complete"
    save_production(path, state)
    for episode in (1, 2):
        board = path / f"ep{episode:02d}" / "boards" / "board.png"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_bytes(png_bytes())
        for take_id in ("t1", "t2"):
            approve_board(path, episode=episode, take_id=take_id, image=board)
    return path


@pytest.fixture
def api30(desk30: Path, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """The fake API for ``desk30``; downloads write a tiny file."""

    fake = FakeApi(desk30 / "ep02" / "api", spine=spine_fixture())
    monkeypatch.setattr(orchestrate, "_open_run", lambda _desk, _state: fake)
    monkeypatch.setattr(episode_commands, "_open_run", lambda _desk, _state: fake)

    def download(client: Any, url: str, directory: Path, stem: str, *, default_suffix: str = ".png") -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        target = next_versioned_path(directory, stem, default_suffix)
        target.write_bytes(b"mp4")
        fake.events.append(("download", {"url": url, "path": str(target)}))
        return target

    monkeypatch.setattr(orchestrate, "download_to_versioned", download)
    return fake


def _take_two_of_episode_two(api: FakeApi) -> None:
    """The server films take 2 of episode 2 alone: one child."""

    api.routes[("POST", VIDEO)] = {"job_id": "job_video_9"}
    api.routes[("GET", "/v1/jobs/job_video_9")] = {"status": "running", "depends_on": ["job_take_e2t2"]}
    api.routes[("GET", "/v1/jobs/job_take_e2t2")] = {
        "status": "completed",
        "episode_ids": ["ep_02"],
        "relation": {"id": "scene_ep_02_set02"},
        "result": {"video": {"url": "https://r2.example/ep2-t2.mp4"}},
    }
    api.routes[("GET", "/v1/jobs/job_take_e2t2/take-facts")] = {
        "take_facts": {"endpoint_id": "minimax/h3-max/reference-to-video", "resolution": "768P", "duration_seconds": 15.0}
    }


def _filmed_once(desk: Path, episode: int = 2) -> None:
    for take_id in ("t1", "t2"):
        record_filmed(desk, episode=episode, take_id=take_id)


CAUSE = "the ring leaves her hand before the line: move the drop after 'Not for me'"


# --- Price first ------------------------------------------------------------------------------------


def test_film_take_prices_that_one_take_and_films_nothing(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20", "priced_on": "2026-09-28", "takes": 1}}

    text = run_film(desk30, episode=2, take_id="t2", cause=CAUSE)

    assert api30.posted(ESTIMATE) == [{"spine_version": "v5", "episode_ids": ["ep_02"], "reroll_take_index": 2}]
    assert api30.posted(VIDEO) == []
    assert "only t2 of episode 2: about $1.20" in text
    assert "; H3 Max Turbo 768P at $0.0" in text and "!! SERVER ESTIMATE FAILED" not in text
    assert "episodes 1-1 are not filmed or booked again" in text
    assert load_production(desk30).film_estimates == {"ep02-t2": 1.20}
    assert "--take t2" in text and "--confirm-spend" in text


def test_a_server_that_cannot_price_one_take_is_priced_from_the_table_for_one_take(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)
    api30.routes[("POST", ESTIMATE)] = SystemExit(
        "HTTP 422: validation_error: reroll_take_index: Extra inputs are not permitted"
    )

    text = run_film(desk30, episode=2, take_id="t2", cause=CAUSE)

    assert load_production(desk30).film_estimates["ep02-t2"] == pytest.approx(turbo_take_usd(15))  # one take, not two
    assert "cannot price one take yet" in text
    assert "!! SERVER ESTIMATE FAILED (the server refused it: HTTP 422" in text


def test_a_film_estimate_that_names_r2v_without_dollars_is_priced_on_r2v(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)
    api30.routes[("POST", ESTIMATE)] = {
        "cost_estimate": {"video_endpoint_id": "minimax/h3-max/reference-to-video", "video_resolution": "768P"}
    }

    text = run_film(desk30, episode=2, take_id="t2", cause=CAUSE)

    state = load_production(desk30)
    assert state.film_estimates["ep02-t2"] == pytest.approx(1.20)
    assert state.server_lane() == ("minimax/h3-max/reference-to-video", "768P")
    assert "price table (H3 Max R2V, 15 s a take); H3 Max R2V 768P at $0.08/s" in text
    assert text.startswith("!! SERVER ESTIMATE FAILED")


def test_confirming_without_a_shown_price_sends_nothing(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)
    _take_two_of_episode_two(api30)

    with pytest.raises(CommandStopped, match="no price shown"):
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    assert api30.posted(VIDEO) == []


# --- The written cause -----------------------------------------------------------------------------


def test_a_second_render_needs_a_cause_and_try_again_is_not_one(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)

    with pytest.raises(CommandStopped, match="needs a written cause"):
        run_film(desk30, episode=2, take_id="t2")
    with pytest.raises(CommandStopped, match="does not name"):
        run_film(desk30, episode=2, take_id="t2", cause="Try again.")

    assert api30.posted(ESTIMATE) == [] and api30.posted(VIDEO) == []


def test_a_take_is_filmed_only_from_an_approved_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = init_series_desk(tmp_path, "Closing Time", band="30s", episode_count=2)
    state = ensure_production(path, prompt="p", preset_id="x", preset_version="1")
    state.spine_id = "sp1"
    save_production(path, state)
    fake = FakeApi(path / "ep02" / "api", spine=spine_fixture())
    monkeypatch.setattr(episode_commands, "_open_run", lambda _desk, _state: fake)

    with pytest.raises(CommandStopped, match="no human yes on the board"):
        run_film(path, episode=2, take_id="t1")

    assert fake.posted(VIDEO) == [] and fake.posted(ESTIMATE) == []


# --- Film take K of episode N -----------------------------------------------------------------------


def test_film_take_k_sends_the_episode_the_take_and_a_fresh_seed_and_books_only_it(
    desk30: Path, api30: FakeApi
) -> None:
    _filmed_once(desk30)
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20", "takes": 1}}
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE)
    _take_two_of_episode_two(api30)
    spend_before = episode_by_ordinal(load_series(desk30), 2).spend_usd

    text = run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    body = api30.posted(VIDEO)[0]
    assert body["episode_count"] == 2
    assert body["episode_ordinal"] == 2
    assert body["reroll_take_index"] == 2
    assert body["seed_attempt"] == 2
    assert body["authoring_mode"] == "reuse" and body["reuse_spine_id"] == "sp1"
    downloads = [payload["url"] for phase, payload in api30.events if phase == "download"]
    assert downloads == ["https://r2.example/ep2-t2.mp4"]
    slot = episode_by_ordinal(load_series(desk30), 2)
    t1, t2 = slot.takes
    assert (t1.filmed_count, t2.filmed_count) == (1, 2)
    assert t2.change_cause == CAUSE and t1.verdict == "pending"
    assert slot.spend_usd == pytest.approx(spend_before + 1.20)
    state = load_production(desk30)
    assert state.film_estimates == {} and state.pending == {}
    assert state.server_lane() == ("minimax/h3-max/reference-to-video", "768P")  # the lane the facts named
    assert "Filmed ep02 t2: 1 take(s), $1.20 booked" in text
    api_dir = desk30 / "ep02" / "api"
    assert (api_dir / "film-ep02-t2-s2-raw-scene-clips.json").is_file()
    assert not (api_dir / "17_raw_scene_clips.json").exists()  # the step's own record is never overwritten
    assert CAUSE in (desk30 / "ep02" / "run-notes.md").read_text(encoding="utf-8")


def test_a_second_refilm_of_the_same_take_moves_the_seed_again(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)
    record_filmed(desk30, episode=2, take_id="t2")  # t2 filmed twice already
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20"}}
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE)
    _take_two_of_episode_two(api30)

    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    assert api30.posted(VIDEO)[0]["seed_attempt"] == 3


def test_an_interrupted_film_picks_up_its_job_without_posting_again(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20"}}
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE)
    _take_two_of_episode_two(api30)
    state = load_production(desk30)
    state.pending["film-ep02-t2-s2"] = {"key": "k-1", "job_id": "job_video_9"}
    save_production(desk30, state)

    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    assert api30.posted(VIDEO) == []
    assert episode_by_ordinal(load_series(desk30), 2).takes[1].filmed_count == 2


# --- Film episode N alone ---------------------------------------------------------------------------


def test_film_episode_n_sends_episode_ordinal_and_no_take(desk30: Path, api30: FakeApi) -> None:
    set_phase(desk30, "wait_spend")
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "2.40", "takes": 2}}
    text = run_film(desk30, episode=2)
    assert api30.posted(ESTIMATE) == [{"spine_version": "v5", "episode_ids": ["ep_02"]}]
    assert "episode 2 alone (2 take(s))" in text
    api30.routes[("POST", VIDEO)] = {"job_id": "job_video_7"}
    api30.routes[("GET", "/v1/jobs/job_video_7")] = {"status": "running", "depends_on": ["job_a", "job_b"]}
    for job, index in (("job_a", 1), ("job_b", 2)):
        api30.routes[("GET", f"/v1/jobs/{job}")] = {
            "status": "completed",
            "episode_ids": ["ep_02"],
            "relation": {"id": f"scene_ep_02_set0{index}"},
            "result": {"video": {"url": f"https://r2.example/ep2-t{index}.mp4"}},
        }
        api30.routes[("GET", f"/v1/jobs/{job}/take-facts")] = {"take_facts": {}}

    run_film(desk30, episode=2, confirm_spend=True)

    body = api30.posted(VIDEO)[0]
    assert (body["episode_count"], body["episode_ordinal"]) == (2, 2)
    assert "reroll_take_index" not in body and "seed_attempt" not in body
    assert load_production(desk30).phase == "complete"


# --- An older deploy --------------------------------------------------------------------------------


def test_an_older_deploy_is_refused_before_anything_is_sent(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)
    api30.routes[("GET", "/openapi.json")] = openapi_doc(episode_ordinal=False)
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20"}}
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE)
    _take_two_of_episode_two(api30)

    with pytest.raises(SystemExit) as caught:
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    assert "does not film one episode alone yet" in str(caught.value.code)
    assert "episodes 1..2 again" in str(caught.value.code)
    assert api30.posted(VIDEO) == []


def test_an_unreadable_schema_and_an_older_servers_422_say_the_same_thing(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)
    api30.routes[("GET", "/openapi.json")] = SystemExit("HTTP 404")
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20"}}
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE)
    api30.routes[("POST", VIDEO)] = SystemExit(
        "HTTP 422: validation_error: body.episode_ordinal: Extra inputs are not permitted"
    )

    with pytest.raises(SystemExit) as caught:
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    assert "does not film one episode alone yet" in str(caught.value.code)
    assert len(api30.posted(VIDEO)) == 1  # sent once, refused at admission, nothing filmed


def test_boards_not_approved_is_not_mistaken_for_an_old_deploy(desk30: Path, api30: FakeApi) -> None:
    _filmed_once(desk30)
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20"}}
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE)
    _take_two_of_episode_two(api30)
    api30.routes[("POST", VIDEO)] = SystemExit(
        "HTTP 422 POST https://drama.example/v1/video-generations: "
        "boards_not_approved_for_generation: pilot episode storyboards not approved for generation: 1 "
        '(details {"episode_ordinals": [1]}) [request req_e40fabe0cb454196bee8606841d81ac9]'
    )

    with pytest.raises(SystemExit) as caught:
        run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    text = str(caught.value.code)
    assert "boards_not_approved_for_generation" in text
    assert "does not film one episode alone yet" not in text


def test_episode_one_on_an_older_deploy_films_alone_without_the_field(desk30: Path, api30: FakeApi) -> None:
    api30.routes[("GET", "/openapi.json")] = openapi_doc(episode_ordinal=False)
    spine = api30.spine_doc

    extra = stages.film_scope(api30, episode=1)
    body = stages.video_request_body(api30, spine=spine, prompt="p", preset_id="x", preset_version="1", episode=1)

    assert extra == {"episode_count": 1}
    assert body["episode_count"] == 1 and "episode_ordinal" not in body


def test_clips_of_another_episode_are_not_booked_and_are_said_out_loud(desk30: Path, api30: FakeApi) -> None:
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20"}}
    run_film(desk30, episode=2, take_id="t2")
    _take_two_of_episode_two(api30)
    api30.routes[("GET", "/v1/jobs/job_video_9")] = {"status": "running", "depends_on": ["job_ep1", "job_take_e2t2"]}
    api30.routes[("GET", "/v1/jobs/job_ep1")] = {
        "status": "completed",
        "episode_ids": ["episode_01"],
        "relation": {"id": "scene_episode_01_set01"},
        "result": {"video": {"url": "https://r2.example/ep1.mp4"}},
    }

    text = run_film(desk30, episode=2, take_id="t2", confirm_spend=True)

    assert "also filmed another episode: episode_01 (job_ep1)" in text
    assert episode_by_ordinal(load_series(desk30), 1).takes[0].filmed_count == 0


# --- The phase machine -----------------------------------------------------------------------------


def test_a_whole_episode_retry_films_with_a_fresh_seed(desk: Path, api: FakeApi) -> None:
    record_filmed(desk, episode=1, take_id="t1")
    set_phase(desk, "ready_video", video_idempotency_suffix="-retry-abc", video_enrolled_suffix="")
    api.routes[("POST", VIDEO)] = {"job_id": "job_video_1"}
    api.routes[("GET", "/v1/jobs/job_video_1")] = {"status": "running", "depends_on": ["job_take_a"]}
    api.routes[("GET", "/v1/jobs/job_take_a")] = {
        "status": "completed",
        "episode_ids": ["episode_01"],
        "relation": {"id": "scene_episode_01_set01"},
        "result": {"video": {"url": "https://r2.example/ep1.mp4"}},
    }
    api.routes[("GET", "/v1/jobs/job_take_a/take-facts")] = {"take_facts": {}}

    orchestrate.run_step(desk)

    body = api.posted(VIDEO)[0]
    assert (body["episode_count"], body["episode_ordinal"], body["seed_attempt"]) == (1, 1, 2)
    assert "reroll_take_index" not in body


def test_a_japanese_show_films_with_its_spoken_language_and_locale_from_the_spine(
    desk30: Path, api30: FakeApi
) -> None:
    api30.spine_doc["spoken_language"] = "ja-JP"
    api30.spine_doc["locale"] = "en-US"
    _filmed_once(desk30)
    api30.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "1.20", "takes": 1}}
    run_film(desk30, episode=2, take_id="t2", cause=CAUSE)
    _take_two_of_episode_two(api30)

    run_film(desk30, episode=2, take_id="t2", cause=CAUSE, confirm_spend=True)

    body = api30.posted(VIDEO)[0]
    assert body["spoken_language"] == "ja-JP", "the take's coordinator must know the show is Japanese"
    assert body["locale"] == "en-US", "subtitles stay English"
