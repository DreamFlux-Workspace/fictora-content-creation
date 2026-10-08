"""`cast-exit`: mark a character as gone after an episode, or bring them back (Noodle24, 8 Oct 2026).

A guest-per-arc show hit the old cap of four characters a story. A series now holds up to 50, and a
guest who has left is marked on their card (``exit_episode_ordinal``) so the next episode's writers see
one line for them. Spends nothing: before the script gate a ``PATCH`` with ``cast[]``, after it the
``cast_card`` cascade with every paid item off.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from cast_story import _after_gate, _before_gate, _patches, _story, _writes
from creation import cast_commands as cc
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from fake_api import FakeApi


def _with_tanaka(
    spine: dict[str, Any], *, exit_after: int | None = None
) -> dict[str, Any]:
    """Mr. Tanaka, a guest who first appears in episode 2."""

    card: dict[str, Any] = {
        "cast_id": "cast_tanaka",
        "name": "Mr. Tanaka",
        "intro_episode_ordinal": 2,
    }
    if exit_after is not None:
        card["exit_episode_ordinal"] = exit_after
    spine["cast"].append(card)
    return spine


def _saved(desk: Path) -> dict[str, Any]:
    return json.loads((desk / "api" / "spine.json").read_text(encoding="utf-8"))


def _card(spine: dict[str, Any], cast_id: str) -> dict[str, Any]:
    return next(c for c in spine["cast"] if c["cast_id"] == cast_id)


def test_before_the_gate_it_patches_the_card_and_says_what_writers_will_see(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _with_tanaka(_story(approved=False)))
    out = io.StringIO()

    cc.run_cast_exit(desk, cast="Mr. Tanaka", after_episode=5, out=out)

    assert _patches(api) == [
        {"cast": [{"cast_id": "cast_tanaka", "exit_episode_ordinal": 5}]}
    ]
    assert (
        "Mr. Tanaka is marked as leaving after episode 5. Writers will see one line for them from "
        "episode 6; run with --clear to bring them back."
    ) in out.getvalue()
    assert _card(_saved(desk), "cast_tanaka")["exit_episode_ordinal"] == 5


def test_after_the_gate_it_goes_through_the_cast_cascade_with_paid_items_off(
    desk: Path, api: FakeApi
) -> None:
    edits = _after_gate(api, _with_tanaka(_story(approved=True)))
    executed: list[dict[str, Any]] = []
    preview = api.routes[("POST", "/v1/spines/sp1/cascade/preview")]
    execute = api.routes[("POST", "/v1/spines/sp1/cascade/execute")]

    def paid_preview(m: str, p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        answer = preview(m, p, body)
        answer["items"] = [
            {
                "item_id": "it_1",
                "recipe_id": "plate",
                "estimated_tier": ec.PAID_TIER,
                "selected": True,
            }
        ]
        return answer

    def recording_execute(m: str, p: str, body: dict[str, Any] | None) -> Any:
        executed.append(body or {})
        return execute(m, p, body)

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = paid_preview
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = recording_execute

    cc.run_cast_exit(desk, cast="cast_tanaka", after_episode=5, out=io.StringIO())

    assert edits == [
        {
            "scope": "field",
            "target_type": "cast_card",
            "target_id": "cast_tanaka",
            "patch": {"cast_id": "cast_tanaka", "exit_episode_ordinal": 5},
        }
    ]
    assert not _patches(api)
    assert executed[0]["items"] == [
        {"item_id": "it_1", "selected": False}
    ]  # spends nothing
    assert _card(_saved(desk), "cast_tanaka")["exit_episode_ordinal"] == 5


def test_clear_sends_null_and_brings_them_back(desk: Path, api: FakeApi) -> None:
    _before_gate(api, _with_tanaka(_story(approved=False), exit_after=5))
    out = io.StringIO()

    cc.run_cast_exit(desk, cast="mr. tanaka", clear=True, out=out)

    assert _patches(api) == [
        {"cast": [{"cast_id": "cast_tanaka", "exit_episode_ordinal": None}]}
    ]
    assert (
        "Mr. Tanaka is back in the story (was leaving after episode 5)"
        in out.getvalue()
    )
    assert _card(_saved(desk), "cast_tanaka").get("exit_episode_ordinal") is None


def test_nothing_is_sent_when_the_mark_is_already_there(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _with_tanaka(_story(approved=False), exit_after=5))
    out = io.StringIO()

    assert cc.run_cast_exit(desk, cast="Mr. Tanaka", after_episode=5, out=out) is None
    assert cc.run_cast_exit(desk, cast="Ren", clear=True, out=out) is None

    assert _writes(api) == []
    assert "already marked as leaving after episode 5" in out.getvalue()
    assert "Ren is not marked as leaving" in out.getvalue()


def test_an_unknown_name_is_refused_and_lists_the_cast(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _with_tanaka(_story(approved=False)))

    with pytest.raises(ec.CommandStopped) as stopped:
        cc.run_cast_exit(desk, cast="Mrs. Tanaka", after_episode=5, out=io.StringIO())

    assert "no character 'Mrs. Tanaka'" in str(stopped.value)
    assert "Mr. Tanaka (cast_tanaka)" in str(stopped.value)
    assert _writes(api) == []


def test_a_shared_name_is_refused_until_the_cast_id_is_given(
    desk: Path, api: FakeApi
) -> None:
    spine = _with_tanaka(_story(approved=False))
    spine["cast"].append(
        {"cast_id": "cast_tanaka_2", "name": "Mr. Tanaka", "intro_episode_ordinal": 4}
    )
    _before_gate(api, spine)

    with pytest.raises(ec.CommandStopped) as stopped:
        cc.run_cast_exit(desk, cast="Mr. Tanaka", after_episode=5, out=io.StringIO())

    assert "2 characters are called 'Mr. Tanaka' (cast_tanaka, cast_tanaka_2)" in str(
        stopped.value
    )
    assert _writes(api) == []

    cc.run_cast_exit(desk, cast="cast_tanaka_2", after_episode=5, out=io.StringIO())
    assert _patches(api) == [
        {"cast": [{"cast_id": "cast_tanaka_2", "exit_episode_ordinal": 5}]}
    ]


def test_leaving_before_the_first_episode_they_are_in_is_refused(
    desk: Path, api: FakeApi
) -> None:
    # The server's rule (DramaCastCard: exit_episode_ordinal is never before intro_episode_ordinal).
    _before_gate(api, _with_tanaka(_story(approved=False)))

    with pytest.raises(ec.CommandStopped) as stopped:
        cc.run_cast_exit(desk, cast="Mr. Tanaka", after_episode=1, out=io.StringIO())

    assert "first appears in episode 2" in str(stopped.value)
    assert "--after-episode 2" in str(stopped.value)
    assert _writes(api) == []

    cc.run_cast_exit(
        desk, cast="Mr. Tanaka", after_episode=2, out=io.StringIO()
    )  # the same episode is fine
    assert _patches(api) == [
        {"cast": [{"cast_id": "cast_tanaka", "exit_episode_ordinal": 2}]}
    ]


def test_a_character_who_still_speaks_later_gets_a_note(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, _story(approved=False))  # Ren speaks in episodes 1 and 2
    out = io.StringIO()

    cc.run_cast_exit(desk, cast="Ren", after_episode=1, out=out)

    assert "note: Ren still has lines in episode 2" in out.getvalue()


def test_from_the_command_line(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _before_gate(api, _with_tanaka(_story(approved=False)))

    assert (
        produce_main(
            [
                "cast-exit",
                "--desk",
                str(desk),
                "--cast",
                "Mr. Tanaka",
                "--after-episode",
                "5",
            ]
        )
        == 0
    )
    assert "marked as leaving after episode 5" in capsys.readouterr().out
    assert (
        produce_main(
            ["cast-exit", "--desk", str(desk), "--cast", "Mr. Tanaka", "--clear"]
        )
        == 0
    )
    assert "back in the story" in capsys.readouterr().out
    assert (
        produce_main(["cast-exit", "--desk", str(desk), "--cast", "Nobody", "--clear"])
        == 2
    )

    with pytest.raises(SystemExit):  # one of --after-episode or --clear, never both
        produce_main(
            [
                "cast-exit",
                "--desk",
                str(desk),
                "--cast",
                "Ren",
                "--after-episode",
                "1",
                "--clear",
            ]
        )
