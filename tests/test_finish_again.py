"""Finish again for $0 an episode whose takes are filmed but whose ending failed (fictora-drama #1341, 5 Nov 2026).

The server says which failed run can be finished from its stored takes; the kit shows it before a re-film
price and asks for it with ``finish-again``. A real re-film still films fresh takes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands
from creation.episode_commands import CommandStopped, run_film
from creation.finish_again import finish_again_offer_lines, run_finish_again
from creation.ops.floor import approve_board, init_series_desk, record_filmed
from creation.production_state import (
    ensure_production,
    load_production,
    save_production,
)
from fake_api import FakeApi, png_bytes, spine_fixture

ESTIMATE = "/v1/spines/sp1/batches/estimate"
VIDEO = "/v1/video-generations"
FINISH = "/v1/video-generations/job_video_7/finish-again"
OFFER = {
    "job_id": "job_video_7",
    "message": "Every take of this episode is already filmed; only the finishing step failed. "
    "Finish it again for free, with no new takes.",
}


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    path = init_series_desk(tmp_path, "Blood Vows", band="30s", episode_count=2)
    state = ensure_production(
        path,
        prompt="A vow in blood.",
        preset_id="modern-romance",
        preset_version="2",
        video_lane="minimax-h3",
    )
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
            record_filmed(path, episode=episode, take_id=take_id)
    return path


@pytest.fixture
def api(desk: Path, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi(desk / "ep02" / "api", spine=spine_fixture())
    monkeypatch.setattr(episode_commands, "_open_run", lambda _desk, _state: fake)
    return fake


def test_the_refilm_price_offers_finishing_again_first(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", ESTIMATE)] = {
        "cost_estimate": {"total_usd": "2.40"},
        "finish_again": OFFER,
    }

    text = run_film(desk, episode=2, cause="the ring drops before the line")

    assert text.index(OFFER["message"]) < text.index("about $")
    assert f"finish-again --desk {desk} --episode 2" in text and "job_video_7" in text
    assert api.posted(VIDEO) == [] and api.posted(FINISH) == []


def test_no_offer_prints_nothing_extra(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "2.40"}}

    text = run_film(desk, episode=2, cause="the ring drops before the line")

    assert "finish-again" not in text
    assert (
        finish_again_offer_lines({"finish_again": {"job_id": ""}}, desk=desk, episode=2)
        == []
    )


def test_finish_again_asks_the_server_for_the_run_it_offered(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", ESTIMATE)] = {
        "cost_estimate": {"total_usd": "2.40"},
        "finish_again": OFFER,
    }
    api.routes[("POST", FINISH)] = {"job_id": "job_video_7", "state": "started"}

    text = run_finish_again(desk, episode=2)

    assert api.posted(FINISH) == [{}]
    assert api.posted(VIDEO) == []  # nothing filmed
    assert "no new takes, $0" in text
    assert load_production(desk).pending == {}  # the kit keeps nothing of its own


def test_finish_again_while_it_runs_starts_nothing_new(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", ESTIMATE)] = {"finish_again": OFFER}
    api.routes[("POST", FINISH)] = {
        "job_id": "job_video_7",
        "state": "already_finishing",
    }

    assert "already being finished again" in run_finish_again(desk, episode=2)


def test_without_an_offer_finish_again_says_to_refilm_and_sends_nothing(
    desk: Path, api: FakeApi
) -> None:
    api.routes[("POST", ESTIMATE)] = {"cost_estimate": {"total_usd": "2.40"}}

    with pytest.raises(CommandStopped, match="nothing to finish"):
        run_finish_again(desk, episode=2)
    assert api.posted(FINISH) == []


def test_a_refusal_is_said_in_the_servers_words(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", ESTIMATE)] = {"finish_again": OFFER}

    def refuse(_method: str, _path: str, _body: Any) -> Any:
        return 409, {
            "error": {
                "code": "newer_run",
                "message": "Run job_video_8 filmed this episode later.",
            }
        }

    api.routes[("POST", FINISH)] = refuse

    with pytest.raises(CommandStopped, match="newer_run"):
        run_finish_again(desk, episode=2)
