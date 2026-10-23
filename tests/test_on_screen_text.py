"""The words the kit burns are the server's (decisions D5 and D7, 21 Oct 2026): no local copy, no fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from creation import on_screen_text
from creation.on_screen_text import (
    OLDER_SERVER,
    OnScreenTextUnavailable,
    ask_server,
    drawn_texts,
    server_owns_text,
)
from creation.rules_epoch import desk_rules

pytestmark = pytest.mark.real_on_screen_text


class FakeRun:
    """The server's on-screen text and post-text routes, answered as scripted."""

    def __init__(self, answer: tuple[int, Any] | None = None) -> None:
        self.posted: list[tuple[str, dict[str, Any]]] = []
        self.answer = answer
        self.client = self

    def close(self) -> None:
        pass

    def post_optional(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        self.posted.append((path, body))
        if self.answer is not None:
            return self.answer
        return 200, {
            "items": [
                {"kind": item["kind"], "text": item["text"].replace("—", "…")}
                for item in body["items"]
            ]
        }

    def get_optional(self, path: str) -> tuple[int, Any]:
        if path == "/v1/capabilities":
            return 200, {"on_screen_text": True}
        return 200, {"text": "Shared Heat · Part 2\nFollow for part 3.\n"}

    def spine(self, spine_id: str) -> dict[str, Any]:
        return {
            "spine_id": spine_id,
            "episode_summaries": [{"ordinal": 1, "episode_id": "episode_01"}],
        }


def test_the_texts_go_to_the_server_and_come_back_as_it_draws_them() -> None:
    run = FakeRun()

    drawn = ask_server(run, "sp1", "episode_01", ["Please—", "Hi"], "caption")

    assert drawn == ["Please…", "Hi"]
    ((path, body),) = run.posted
    assert path == "/v1/spines/sp1/episodes/episode_01/on-screen-text"
    assert body == {
        "items": [
            {"kind": "caption", "text": "Please—"},
            {"kind": "caption", "text": "Hi"},
        ]
    }


@pytest.mark.parametrize("status", [404, 405])
def test_an_older_server_stops_with_a_plain_message(status: int) -> None:
    with pytest.raises(OnScreenTextUnavailable) as caught:
        ask_server(
            FakeRun((status, {"detail": "Not Found"})),
            "sp1",
            "episode_01",
            ["x"],
            "title",
        )
    assert str(caught.value) == OLDER_SERVER


def test_a_refusal_is_said_and_nothing_is_guessed() -> None:
    with pytest.raises(OnScreenTextUnavailable, match="HTTP 422"):
        ask_server(
            FakeRun((422, {"error": {"code": "invalid", "message": "bad"}})),
            "sp1",
            "e",
            ["x"],
            "caption",
        )


def test_a_desk_command_asks_once_and_remembers_the_answers(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs: list[FakeRun] = []

    def opened(desk: Path, episode: int) -> FakeRun:
        runs.append(FakeRun())
        return runs[-1]

    monkeypatch.setattr("creation.post.desk.open_api", opened)
    monkeypatch.setattr(on_screen_text, "_CACHE", {})
    with desk_rules(post_desk, 1):
        first = drawn_texts(["Wait—", "", "Wait—"])
        again = drawn_texts(["Wait—"])

    assert first == ["Wait…", "", "Wait…"] and again == ["Wait…"]
    assert len(runs) == 1 and len(runs[0].posted) == 1
    assert runs[0].posted[0][1]["items"] == [{"kind": "caption", "text": "Wait—"}]


def test_with_no_desk_nothing_is_drawn_by_the_kit() -> None:
    with pytest.raises(OnScreenTextUnavailable, match="no desk"):
        drawn_texts(["Hi—"])


def test_capabilities_say_whether_the_server_owns_the_text() -> None:
    assert server_owns_text(FakeRun()) is True

    class Older(FakeRun):
        def get_optional(self, path: str) -> tuple[int, Any]:
            return 404, {"detail": "Not Found"}

    assert server_owns_text(Older()) is False
