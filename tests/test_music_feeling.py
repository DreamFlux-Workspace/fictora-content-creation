"""``music-feeling`` shows the server's four music options in its order and sends the picked one back."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fake_api import FakeApi

from creation.cli_produce import main

ROUTE = "/v1/spines/sp1/episodes/episode_01/music-feeling"


def _view(version: str = "v1", answer: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "spine_id": "sp1", "spine_version": version, "episode_id": "episode_01",
        "line": "What should the music feel like?",
        "options": [
            {"kind": "suggested", "index": 0, "label": "Cold and tense", "description": "Low strings under the stand-off."},
            {"kind": "suggested", "index": 1, "label": "Quietly sad", "description": "A lone piano as she leaves."},
            {"kind": "no_music", "index": None, "label": "No music", "description": None},
            {"kind": "own_words", "index": None, "label": "Something else…", "description": None},
        ],
        "answer": answer, "answerable": True,
    }  # fmt: skip


def _posts(api: FakeApi) -> list[dict[str, Any]]:
    return [body or {} for m, p, body, _ in api.calls if m == "POST" and p == ROUTE]


def _run(desk: Path, *extra: str) -> int:
    return main(["music-feeling", "--desk", str(desk), "--episode", "1", *extra])


def test_no_answer_prints_the_four_options_in_the_servers_order_and_sends_nothing(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("GET", ROUTE)] = _view()

    assert _run(desk) == 0

    assert _posts(api) == []
    lines = capsys.readouterr().out.splitlines()
    assert lines[:6] == [
        "Music for episode 1: What should the music feel like?",
        "  1. Cold and tense: Low strings under the stand-off.",
        "  2. Quietly sad: A lone piano as she leaves.",
        "  3. No music",
        "  4. Something else…",
        "Answer: not answered yet, so the server picks the music it uses for this show.",
    ]
    assert lines[6].endswith('4 "your own words".')


@pytest.mark.parametrize(
    ("args", "answer"),
    [
        (("1",), {"kind": "suggested", "index": 0}),
        (("2",), {"kind": "suggested", "index": 1}),
        (("3",), {"kind": "no_music"}),
        (
            ("4", "  warm and hopeful "),
            {"kind": "own_words", "text": "warm and hopeful"},
        ),
    ],
)
def test_each_answer_sends_its_kind_against_the_version_it_read(
    desk: Path, api: FakeApi, args: tuple[str, ...], answer: dict[str, Any]
) -> None:
    api.routes[("GET", ROUTE)] = _view()
    api.routes[("POST", ROUTE)] = (200, _view(answer=answer))

    assert _run(desk, *args) == 0

    assert _posts(api) == [{"spine_version": "v1", "answer": answer}]


def test_the_saved_answer_is_shown_in_the_servers_words(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("GET", ROUTE)] = _view(answer={"kind": "own_words", "text": "warm"})

    assert _run(desk) == 0

    assert 'Answer: Something else… "warm".' in capsys.readouterr().out


def test_new_music_with_no_feeling_reads_as_the_servers_pick(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("GET", ROUTE)] = _view(answer={"kind": "server_pick"})

    assert _run(desk) == 0

    assert "Answer: music, in a feeling the server picks" in capsys.readouterr().out


def test_own_words_without_words_sends_nothing(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("GET", ROUTE)] = _view()

    assert _run(desk, "4") == 2

    assert _posts(api) == []
    assert "4 needs the words too" in capsys.readouterr().out


def test_a_stale_version_reads_the_question_again_and_retries_once(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    reads = iter([_view("v1"), _view("v2")])
    api.routes[("GET", ROUTE)] = lambda *_: next(reads)
    answers = iter(
        [
            (409, {"error": {"code": "spine_version_conflict"}}),
            (200, _view("v3", answer={"kind": "no_music"})),
        ]
    )
    api.routes[("POST", ROUTE)] = lambda *_: next(answers)

    assert _run(desk, "3") == 0

    assert [body["spine_version"] for body in _posts(api)] == ["v1", "v2"]
    assert "Answer: No music." in capsys.readouterr().out


def test_a_second_conflict_is_not_retried_again(desk: Path, api: FakeApi) -> None:
    api.routes[("GET", ROUTE)] = _view()
    api.routes[("POST", ROUTE)] = (409, {"error": {"code": "spine_version_conflict"}})

    assert _run(desk, "1") == 2

    assert len(_posts(api)) == 2


def test_a_finished_episode_says_its_music_cannot_change(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("GET", ROUTE)] = _view()
    api.routes[("POST", ROUTE)] = (
        409,
        {"error": {"code": "music_feeling_episode_finished"}},
    )

    assert _run(desk, "2") == 2

    assert "is finished, so its music can't change now" in capsys.readouterr().out


def test_an_older_server_is_skipped_quietly(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(desk, "1") == 0

    assert _posts(api) == []
    assert "doesn't ask about music yet" in capsys.readouterr().out
