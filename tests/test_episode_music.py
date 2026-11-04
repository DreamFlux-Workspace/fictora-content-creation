"""``music``: Remove, New or Add music on one finished episode, priced first; the server owns all of it.

- With no action it prints what the episode plays and which changes it allows, with prices.
- An action sends only a plan (``dry_run``) without ``--yes``: nothing is applied or spent.
- ``--yes`` applies the plan's ``plan_id``, waits, and downloads the new cut (the next ``_draft_vN_``).
- ``add`` is refused while music plays; a server refusal prints the server's own words.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fake_api import FakeApi
from test_music_rescore import OLD_FILE, _made_episode
from test_music_rescore_new import MUSIC, _change

from creation.cli_produce import main
from creation.post import music_rescore, music_rescore_new
from creation.post.episode_name import episode_files, next_episode_path
from creation.post.server_finish import ServerFinish


@pytest.fixture
def kit(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Fake the server finish (ask, wait, download) and the clock; record what was downloaded."""

    calls: dict[str, list[Any]] = {"finish": []}

    def finish(desk: Path, episode: int, out: Any) -> ServerFinish:
        path = next_episode_path(
            desk / f"ep{episode:02d}" / "takes", desk, episode, "draft"
        )
        path.write_bytes(b"new episode")
        calls["finish"].append((episode, path.name))
        return ServerFinish(episode, video=path)

    ticks = iter(range(0, 10_000, 5))
    monkeypatch.setattr(music_rescore, "_finish", finish)
    monkeypatch.setattr(music_rescore_new, "_sleep", lambda _s: None)
    monkeypatch.setattr(music_rescore_new, "_clock", lambda: float(next(ticks)))
    return calls


def _view(
    delivered: str = "music", *, can_add: bool = True, **extra: Any
) -> dict[str, Any]:
    return {
        "schema_version": "fictora.drama-episode-music.v1", "spine_id": "sp1", "episode_id": "episode_01",
        "delivered": delivered, "can_remove": delivered == "music", "can_add": can_add,
        "unavailable_reason": None if can_add else "Music is off for this account.",
        "new_music_price": {"tokens": 16, "usd": 0.08}, "latest": None, **extra,
    }  # fmt: skip


def _music(desk: Path, *extra: str) -> int:
    return main(["music", "--desk", str(desk), "--episode", "1", *extra])


def _posts(api: FakeApi) -> list[dict[str, Any]]:
    return [
        body or {}
        for method, path, body, _ in api.calls
        if method == "POST" and path == MUSIC
    ]


def _server(api: FakeApi, view: dict[str, Any], change: Any) -> None:
    """GET answers ``view`` first, then the change as the server's ``latest``; POST plans or applies."""

    reads = iter([view])

    def get(*_a: Any) -> dict[str, Any]:
        return next(
            reads,
            {
                **view,
                "latest": _change(
                    "done", action="remove", video_url="https://media.test/x.mp4"
                ),
            },
        )

    api.routes[("GET", MUSIC)] = get
    api.routes[("POST", MUSIC)] = change


def _remove_plan(body: dict[str, Any]) -> dict[str, Any]:
    if body["dry_run"]:
        return _change(
            "planned", action="remove", price={"tokens": 0, "usd": 0.0}, eta_seconds=0,
            what_changes="The music comes out. The voices and sound effects stay just as they are.",
        )  # fmt: skip
    return _change(
        "running", action="remove", price={"tokens": 0, "usd": 0.0}, eta_seconds=0
    )


def test_status_shows_what_plays_and_each_change_with_its_price(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("GET", MUSIC)] = _view("music")

    assert _music(desk) == 0

    text = capsys.readouterr().out
    assert "Episode 1 music: music is playing." in text
    assert "remove: take the music out (free)" in text
    assert "new: replace it with new music (16 tokens ($0.08))" in text
    assert "add:" not in text and _posts(api) == []


def test_a_plan_without_yes_spends_nothing(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _server(
        api,
        _view("no_music"),
        lambda _m, _p, body: _change(
            "planned", warnings=["New music costs 16 tokens."]
        ),
    )

    assert _music(desk, "add", "--feeling", "warm and hopeful") == 0

    assert _posts(api) == [
        {
            "action": "new",
            "feeling": {"kind": "own_words", "text": "warm and hopeful"},
            "dry_run": True,
        }
    ]
    text = capsys.readouterr().out
    assert (
        "Price: 16 tokens ($0.08); takes about 60 s." in text
        and "Note: New music costs 16 tokens." in text
    )
    assert "Not applied: nothing was changed or spent." in text and "--yes" in text
    assert kit == {"finish": []}


def test_yes_applies_the_plan_waits_and_downloads_the_new_cut(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _made_episode(desk, 1)
    _server(api, _view("music"), lambda _m, _p, body: _remove_plan(body))

    assert _music(desk, "remove", "--yes") == 0

    assert _posts(api) == [
        {"action": "remove", "dry_run": True},
        {"action": "remove", "dry_run": False, "plan_id": "p1"},
    ]
    assert [ep for ep, _ in kit["finish"]] == [1]
    names = [p.name for p in episode_files(desk / "ep01" / "takes", 1)]
    assert names[0] == OLD_FILE.format(ep=1) and names[1].startswith(
        "closing-time_ep01_draft_v2_"
    )
    assert "ep01: rebuilt without music" in capsys.readouterr().out


def test_add_is_refused_while_music_plays_and_nothing_is_sent(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("GET", MUSIC)] = _view("music")

    assert _music(desk, "add", "--yes") == 2

    assert _posts(api) == []
    assert (
        "This episode already has music. Use `new` to replace it"
        in capsys.readouterr().out
    )


def test_a_server_refusal_prints_its_plain_reason(
    desk: Path,
    api: FakeApi,
    kit: dict[str, list[Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    reason = (
        "This episode's music is mixed into its voices and there is no copy without it."
    )
    refusal = (409, {"error": {"code": "music_no_clean_source", "message": reason}})
    _server(api, _view("music"), lambda *_a: refusal)

    assert _music(desk, "new", "--yes") == 2

    assert len(_posts(api)) == 1 and kit == {"finish": []}
    assert reason in capsys.readouterr().out
