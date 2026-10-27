"""`ending`: ask the server whether a series can end at an episode, then send the human's pick.

Founder, 27 Oct 2026: ending early is a conversation before anything is written. The server
reads the story's open threads, says whether it can close there and suggests a natural last
episode; the human decides. The kit prints the server's words and sends the pick; it decides
nothing and keeps nothing. ``brief`` prints the planned finale's line only when the server
sends one (fictora-drama #1105), never a count of its own.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from cast_story import _story
from creation import episode_commands as ec
from creation import season_ending_commands as se
from creation.cli_produce import main as produce_main
from fake_api import FakeApi

LINE = "Two things are still open: who sent the logbook, and what is in drawer seven. They land by episode 6."
OPTIONS = [
    {"label": "End in episode 6"},
    {"label": "End it here anyway"},
    {"label": "Keep going"},
]


def test_the_check_prints_the_servers_line_and_options_word_for_word(
    desk: Path, api: FakeApi
) -> None:
    api.spine_doc = _story(approved=True)
    api.routes[("GET", "/v1/spines/sp1/season/ending-check")] = lambda *_: {
        "can_end_here": False,
        "line": LINE,
        "suggested_last_episode": 6,
        "options": OPTIONS,
    }
    out = io.StringIO()

    se.run_ending(desk, episode=3, out=out)

    text = out.getvalue()
    assert f"sokii: {LINE}" in text
    assert "1. End in episode 6\n2. End it here anyway\n3. Keep going" in text
    assert "planned for" not in text
    assert ("GET", "/v1/spines/sp1/season/ending-check?end_at=3") in [
        (m, p) for m, p, *_ in api.calls
    ]


def test_the_pick_is_sent_with_the_story_version(desk: Path, api: FakeApi) -> None:
    api.spine_doc = _story(approved=True)
    sent: list[dict[str, Any]] = []

    def end(_m: str, _path: str, body: dict[str, Any] | None) -> Any:
        sent.append(dict(body or {}))
        return {"last_episode": 6}

    api.routes[("POST", "/v1/spines/sp1/season/end")] = end
    out = io.StringIO()

    se.run_ending(desk, last_episode=6, out=out)

    assert sent == [
        {"last_episode": 6, "expected_spine_version": api.spine_doc["spine_version"]}
    ]
    assert "episode 6 is the series' last episode" in out.getvalue()


def test_the_servers_refusal_is_printed_as_it_is(desk: Path, api: FakeApi) -> None:
    api.spine_doc = _story(approved=True)
    api.routes[("POST", "/v1/spines/sp1/season/end")] = lambda *_: (
        409,
        {
            "error": {
                "code": "episode_already_written",
                "message": "Episode 2 is already written.",
            }
        },
    )

    with pytest.raises(ec.CommandStopped) as stopped:
        se.run_ending(desk, last_episode=2, out=io.StringIO())

    assert str(stopped.value) == "Refused: Episode 2 is already written."


def test_an_older_server_without_the_route_is_named(desk: Path, api: FakeApi) -> None:
    api.spine_doc = _story(approved=True)
    api.routes[("POST", "/v1/spines/sp1/season/end")] = lambda *_: (
        404,
        {"detail": "Not Found"},
    )

    with pytest.raises(ec.CommandStopped) as stopped:
        se.run_ending(desk, last_episode=6, out=io.StringIO())

    assert "no season ending route yet" in str(stopped.value)


def test_exactly_one_of_episode_or_last_episode(desk: Path, api: FakeApi) -> None:
    api.spine_doc = _story(approved=True)
    with pytest.raises(ec.CommandStopped):
        se.run_ending(desk, out=io.StringIO())
    with pytest.raises(ec.CommandStopped):
        se.run_ending(desk, episode=3, last_episode=6, out=io.StringIO())


def test_from_the_command_line(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.spine_doc = _story(approved=True)
    api.routes[("GET", "/v1/spines/sp1/season/ending-check")] = lambda *_: {
        "can_end_here": True,
        "line": "Everything set up so far can be resolved in this episode.",
        "options": [{"label": "End it here"}, {"label": "Keep going"}],
    }

    assert produce_main(["ending", "--desk", str(desk), "--episode", "3"]) == 0
    assert "sokii: Everything set up so far" in capsys.readouterr().out


FINALE = "This episode could close the series. Wrap it up here, or keep going after it's approved."


@pytest.mark.parametrize("line", [FINALE, None])
def test_brief_prints_the_servers_finale_line_only_when_sent(
    desk: Path, api: FakeApi, line: str | None
) -> None:
    api.spine_doc = _story(approved=True)
    facts: dict[str, Any] = {
        "next_episode_ordinal": 30,
        "season_finale": line is not None,
    }
    if line:
        facts["season_finale_line"] = line
    api.routes[("POST", "/v1/spines/sp1/director/brief")] = lambda *_: {
        "next_episode_ordinal": 30,
        "recap": "Gina and Andre follow the drips.",
        "directions": [
            {"direction_id": "d1", "title": "The ward", "line": "They open it."}
        ],
        "facts": facts,
    }
    out = io.StringIO()

    ec.run_brief(desk, episode=30, out=out)

    assert (f"sokii: {FINALE}" in out.getvalue()) is (line is not None)
    assert "planned for" not in out.getvalue()
