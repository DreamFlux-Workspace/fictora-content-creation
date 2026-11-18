"""``fictora-ops status`` prints the server's Continue target next to the episode's stage (drama #1773)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from creation import story_continue
from creation.cli_ops import main as ops_main

BASE = "https://drama.example"
REAL_READ = story_continue.read_continue

SPINE_WITH_STALE_MINA = {
    "spine_id": "spine_test",
    "cast": [
        {"cast_id": "cast_mina", "name": "Mina"},
        {"cast_id": "cast_jae", "name": "Jae"},
    ],
    "media_assets": [
        {
            "relation_type": "cast_card",
            "relation_id": "cast_mina",
            "kind": "still",
            "url": "https://cdn.example/mina.png",
            "stale": True,
        },
        {
            "relation_type": "cast_card",
            "relation_id": "cast_jae",
            "kind": "still",
            "url": "https://cdn.example/jae.png",
            "stale": False,
        },
    ],
}


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    offers: dict[str, Any],
    spine: dict[str, Any] | None = None,
) -> list[str]:
    paths: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/director/next-offers"):
            return httpx.Response(200, json=offers)
        return httpx.Response(200, json={"spine": spine or {}})

    monkeypatch.setattr(
        story_continue,
        "read_continue",
        lambda desk, **_k: REAL_READ(
            desk,
            api=(BASE, {"Authorization": "Bearer t"}),
            transport=httpx.MockTransport(answer),
        ),
    )
    return paths


def _status(desk: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> str:
    capsys.readouterr()
    assert ops_main(["status", "--desk", str(desk), *extra]) == 0
    return capsys.readouterr().out


def _offers(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": "pictures",
        "episode_ordinal": 1,
        "offers": [],
        "continue": target,
        "usable": [],
    }


def test_a_stale_portrait_names_the_character_and_says_it_is_paid(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(
        monkeypatch,
        _offers(
            {
                "kind": "do",
                "button": "redraw_portraits",
                "episode_ordinal": 1,
                "uses_credits": True,
            }
        ),
        SPINE_WITH_STALE_MINA,
    )

    out = _status(post_desk, capsys)

    row = next(line for line in out.splitlines() if line.startswith("ep01"))
    assert row.endswith("next: Redraw Mina (paid)")


def test_a_redraw_with_no_stale_name_on_the_spine_stays_generic(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(
        monkeypatch,
        _offers(
            {
                "kind": "do",
                "button": "redraw_portraits",
                "episode_ordinal": 1,
                "uses_credits": True,
            }
        ),
        {"cast": [], "media_assets": []},
    )

    assert "next: Redraw portraits (paid)" in _status(post_desk, capsys)


def test_a_free_do_says_free_and_reads_no_spine(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = _serve(
        monkeypatch,
        _offers(
            {
                "kind": "do",
                "button": "write_episode",
                "episode_ordinal": 1,
                "uses_credits": False,
            }
        ),
    )

    assert "next: Write episode 1 (free)" in _status(post_desk, capsys)
    assert paths == ["/v1/spines/spine_test/director/next-offers"]


def test_an_answer_names_the_ask_it_answers(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(
        monkeypatch,
        _offers(
            {
                "kind": "answer",
                "button": "approve_cast",
                "episode_ordinal": 1,
                "uses_credits": False,
                "interrupt_id": "int_42",
            }
        ),
    )

    assert "next: Approve the cast (free; answers ask int_42)" in _status(
        post_desk, capsys
    )


def test_a_wait_names_the_run_in_flight(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(
        monkeypatch,
        _offers(
            {
                "kind": "wait",
                "episode_ordinal": 1,
                "uses_credits": False,
                "family": "drawing",
            }
        ),
    )

    assert "next: wait for the drawing to finish" in _status(post_desk, capsys)


def test_done_says_every_episode_is_filmed(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(
        monkeypatch,
        _offers({"kind": "done", "episode_ordinal": 1, "uses_credits": False}),
    )

    assert "next: done, every episode is filmed" in _status(post_desk, capsys)


def test_json_rows_carry_the_next_step(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(
        monkeypatch,
        _offers(
            {
                "kind": "do",
                "button": "film",
                "episode_ordinal": 1,
                "uses_credits": True,
            }
        ),
    )

    rows = json.loads(_status(post_desk, capsys, "--json"))

    assert rows[0]["next"] == "Film episode 1 (paid)"


def test_a_server_without_continue_prints_status_as_before(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(monkeypatch, {"step": "pictures", "episode_ordinal": 1, "offers": []})

    out = _status(post_desk, capsys)
    rows = json.loads(_status(post_desk, capsys, "--json"))

    assert "next:" not in out
    assert out.startswith("ep01  waiting=")
    assert "next" not in rows[0]


def test_a_refused_read_prints_status_as_before(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        story_continue,
        "read_continue",
        lambda desk, **_k: REAL_READ(
            desk,
            api=(BASE, {}),
            transport=httpx.MockTransport(lambda _r: httpx.Response(404, json={})),
        ),
    )

    assert "next:" not in _status(post_desk, capsys)
