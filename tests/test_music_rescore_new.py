"""``rescore-music`` while music is on: the server's re-score is new music written from each episode's scenes.

- The plan shows each episode's price in tokens and dollars; without ``--yes`` nothing is applied.
- ``--yes`` applies, waits for the episode's new music on the server, then downloads the new cut
  (the next ``_draft_vN_`` file) the usual way. The old file is kept.
- A change that fails keeps the old file and says so; an episode with no music-free copy is listed skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fake_api import FakeApi
from test_music_rescore import OLD_FILE, ROUTE, _made_episode, _posts, _rescore

from creation.ops.state import load_series
from creation.post import music_rescore, music_rescore_new
from creation.post.music_rescore import REBUILD_NOT_DONE
from creation.post.episode_name import episode_files, next_episode_path
from creation.post.server_finish import ServerFinish

MUSIC = "/v1/spines/sp1/episodes/episode_01/music"


def _change(state: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": "fictora.drama-episode-music-change.v1", "spine_id": "sp1", "episode_id": "episode_01",
        "action": "new", "state": state, "plan_id": "p1", "price": {"tokens": 16, "usd": 0.08}, "eta_seconds": 60,
        "what_changes": "New music is written for this episode.", "warnings": [], "reason": None,
        "job_id": None if state == "planned" else "job_episode_music_1", "video_url": None,
        "updated_at": "2026-11-04T14:00:00Z", **extra,
    }  # fmt: skip


def _answer(body: dict[str, Any], *, skipped: str | None = None) -> dict[str, Any]:
    applied = bool(body.get("confirm"))
    row: dict[str, Any] = {"episode_id": "episode_01", "episode_ordinal": 1}
    if skipped:
        row["skipped"] = skipped
    else:
        row["change"] = _change("running" if applied else "planned")
    return {
        "spine_id": "sp1", "spine_version": "1.0.0", "kind": "new_music",
        "status": "applied" if applied else "planned", "message": "new music", "rescore_id": None,
        "episode_ordinal": 1, "takes": [], "new_music": [row], "episodes": [],
        "cost": {"new_cue_count": 0, "new_cues_usd": 0.08, "spend_allowed": True,
                 "spent_usd": 0.08 if applied else 0.0},
        "needs_confirmation": not applied and not skipped, "job_id": None,
    }  # fmt: skip


@pytest.fixture
def kit(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Fake the server finish (ask, wait, download); record what ran."""

    calls: dict[str, list[Any]] = {"finish": []}

    def finish(desk: Path, episode: int, out: Any) -> ServerFinish:
        path = next_episode_path(
            desk / f"ep{episode:02d}" / "takes", desk, episode, "draft"
        )
        path.write_bytes(b"new episode")
        calls["finish"].append((episode, path.name))
        return ServerFinish(episode, video=path)

    monkeypatch.setattr(music_rescore, "_finish", finish)
    return calls


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter(range(0, 10_000, 5))
    monkeypatch.setattr(music_rescore_new, "_sleep", lambda _s: None)
    monkeypatch.setattr(music_rescore_new, "_clock", lambda: float(next(ticks)))


def test_the_plan_shows_new_music_at_its_price_and_applies_nothing(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1)
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: _answer(body)

    assert _rescore(desk, "--episode", "1") == 0

    assert [body for body, _ in _posts(api)] == [
        {"dry_run": True, "episode_ordinal": 1}
    ]
    text = capsys.readouterr().out
    assert (
        "ep01: new music written from this episode's own scenes" in text
        and "16 tokens ($0.08)" in text
    )
    assert "Applying costs 16 tokens ($0.08)" in text and "Remove music (free)" in text
    assert kit == {"finish": []} and load_series(desk).spend_usd == 0


def test_yes_waits_for_the_new_music_then_downloads_the_new_cut(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1)
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: _answer(body)
    reads = iter(
        [_change("running"), _change("done", video_url="https://media.test/scored.mp4")]
    )
    api.routes[("GET", MUSIC)] = lambda *_a: {"latest": next(reads)}

    assert _rescore(desk, "--episode", "1", "--yes") == 0

    assert [body for body, _ in _posts(api)][1] == {
        "confirm": True,
        "episode_ordinal": 1,
    }
    assert [ep for ep, _ in kit["finish"]] == [1]
    names = [p.name for p in episode_files(desk / "ep01" / "takes", 1)]
    assert names[0] == OLD_FILE.format(ep=1) and names[1].startswith(
        "closing-time_ep01_draft_v2_"
    )
    text = capsys.readouterr().out
    assert "ep01: rebuilt with new music" in text and "--revert" not in text
    assert load_series(desk).spend_usd == pytest.approx(0.08)


def test_a_failed_change_keeps_the_old_file_and_says_why(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1)
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: _answer(body)
    api.routes[("GET", MUSIC)] = {
        "latest": _change("failed", reason="The new music could not be made.")
    }

    assert _rescore(desk, "--episode", "1", "--yes") == REBUILD_NOT_DONE

    assert kit == {"finish": []}
    assert (
        "NOT rebuilt, the old file stays: The new music could not be made."
        in capsys.readouterr().out
    )


def test_an_episode_that_cannot_take_music_is_listed_skipped_and_nothing_is_sent(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1)
    reason = (
        "This episode's music is mixed into its voices and we have no copy without it."
    )
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: _answer(body, skipped=reason)

    assert _rescore(desk, "--episode", "1", "--yes") == 0

    assert len(_posts(api)) == 1
    assert f"ep01: skipped: {reason}" in capsys.readouterr().out
