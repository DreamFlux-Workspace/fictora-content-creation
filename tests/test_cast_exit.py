"""`cast-exit`: mark a character as gone after an episode, or bring them back. One server call.

Noodle24 (8 Oct 2026) hit the old cap of four characters a story; a series now holds 50. Long
Shadow (15 Oct 2026) had no way to say its episode 1 giant was dead. Since then the server owns
the whole rule: ``POST /v1/spines/{id}/cast/{cast}/retire`` finds the character (id or name),
checks the episode, writes ``exit_episode_ordinal`` at any stage (nothing redrawn, re-voiced or
re-approved) and names written episodes that still show them. The kit sends the episode and
prints what comes back; it decides nothing and keeps nothing.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import pytest

from cast_story import _story
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


def _retire_server(
    api: FakeApi, spine: dict[str, Any], *, still: list[int] | None = None
) -> list[dict[str, Any]]:
    """Answer the retire route the way the server does; return every body it was sent."""

    api.spine_doc = spine
    sent: list[dict[str, Any]] = []

    def retire(_m: str, path: str, body: dict[str, Any] | None) -> Any:
        who = unquote(path.rsplit("/cast/", 1)[1].removesuffix("/retire"))
        sent.append({"cast": who, **(body or {})})
        cards = [
            c
            for c in api.spine_doc["cast"]
            if c["cast_id"] == who or c["name"].casefold() == who.casefold()
        ]
        if len(cards) != 1:
            return 404, {
                "error": {
                    "code": "cast_not_found",
                    "message": f"This story has no character {who!r}.",
                }
            }
        card = cards[0]
        after = (body or {}).get("after_episode_ordinal")
        before = card.get("exit_episode_ordinal")
        changed = before != after
        if changed:
            card["exit_episode_ordinal"] = after
            api.spine_doc["spine_version"] = "v_" + "f" * 16
        return {
            **copy.deepcopy(api.spine_doc),
            "request_id": "req_1",
            "retired_cast_id": card["cast_id"],
            "after_episode_ordinal": after,
            "previous_after_episode_ordinal": before,
            "changed": changed,
            "still_in_episodes": still or [],
        }

    api.routes[("POST", "/v1/spines/sp1/cast/Nobody/retire")] = retire
    for card in spine["cast"]:
        for key in {card["cast_id"], card["name"]}:
            api.routes[
                ("POST", f"/v1/spines/sp1/cast/{quote(key, safe='')}/retire")
            ] = retire
    return sent


def _saved(desk: Path) -> dict[str, Any]:
    return json.loads((desk / "api" / "spine.json").read_text(encoding="utf-8"))


def _card(spine: dict[str, Any], cast_id: str) -> dict[str, Any]:
    return next(c for c in spine["cast"] if c["cast_id"] == cast_id)


@pytest.mark.parametrize("approved", [False, True])
def test_one_call_before_or_after_the_gate_and_it_says_what_writers_will_see(
    desk: Path, api: FakeApi, approved: bool
) -> None:
    sent = _retire_server(api, _with_tanaka(_story(approved=approved)))
    out = io.StringIO()

    cc.run_cast_exit(desk, cast="Mr. Tanaka", after_episode=5, out=out)

    assert [(body["cast"], body["after_episode_ordinal"]) for body in sent] == [
        ("Mr. Tanaka", 5)
    ]
    # No PATCH and no cascade: the server takes it at any stage.
    assert [method for method, *_ in api.calls if method in {"PATCH", "PUT"}] == []
    assert not any("/cascade/" in path for _, path, *_ in api.calls)
    assert (
        "Mr. Tanaka is marked as leaving after episode 5. Writers will see one line for them from "
        "episode 6; run with --clear to bring them back."
    ) in out.getvalue()
    saved = _saved(desk)
    assert _card(saved, "cast_tanaka")["exit_episode_ordinal"] == 5
    assert "retired_cast_id" not in saved and "still_in_episodes" not in saved


def test_clear_sends_null_and_brings_them_back(desk: Path, api: FakeApi) -> None:
    sent = _retire_server(api, _with_tanaka(_story(approved=False), exit_after=5))
    out = io.StringIO()

    cc.run_cast_exit(desk, cast="cast_tanaka", clear=True, out=out)

    assert sent[0]["after_episode_ordinal"] is None
    assert (
        "Mr. Tanaka is back in the story (was leaving after episode 5)"
        in out.getvalue()
    )
    assert _card(_saved(desk), "cast_tanaka").get("exit_episode_ordinal") is None


def test_a_repeat_changes_nothing_and_saves_nothing(desk: Path, api: FakeApi) -> None:
    _retire_server(api, _with_tanaka(_story(approved=False), exit_after=5))
    out = io.StringIO()

    assert cc.run_cast_exit(desk, cast="Mr. Tanaka", after_episode=5, out=out) is None
    assert "already marked as leaving after episode 5" in out.getvalue()
    assert not (desk / "api" / "spine.json").exists()


def test_the_servers_refusal_is_printed_as_it_is(desk: Path, api: FakeApi) -> None:
    spine = _with_tanaka(_story(approved=False))
    _retire_server(api, spine)
    api.routes[("POST", "/v1/spines/sp1/cast/Mr.%20Tanaka/retire")] = lambda *_: (
        422,
        {
            "error": {
                "code": "cast_retire_before_intro",
                "message": "Mr. Tanaka first appears in episode 2.",
            }
        },
    )

    with pytest.raises(ec.CommandStopped) as stopped:
        cc.run_cast_exit(desk, cast="Mr. Tanaka", after_episode=1, out=io.StringIO())

    assert str(stopped.value) == "Refused: Mr. Tanaka first appears in episode 2."


def test_an_older_server_without_the_route_is_named(desk: Path, api: FakeApi) -> None:
    api.spine_doc = _story(approved=False)
    api.routes[("POST", "/v1/spines/sp1/cast/Ren/retire")] = lambda *_: (
        404,
        {"detail": "Not Found"},
    )

    with pytest.raises(ec.CommandStopped) as stopped:
        cc.run_cast_exit(desk, cast="Ren", after_episode=1, out=io.StringIO())

    assert "no cast retire route yet" in str(stopped.value)


def test_written_episodes_that_still_show_them_are_named_from_the_server(
    desk: Path, api: FakeApi
) -> None:
    _retire_server(api, _story(approved=False), still=[2])
    out = io.StringIO()

    cc.run_cast_exit(desk, cast="Ren", after_episode=1, out=out)

    assert "note: Ren is still in written episode 2" in out.getvalue()


def test_from_the_command_line(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _retire_server(api, _with_tanaka(_story(approved=False)))

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
