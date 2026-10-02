"""A desk whose story another desk also points at refuses story-changing and paid commands (copy-desk report)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from creation.cli_produce import main as produce_main
from creation.shared_spine import desks_sharing_spine, shared_spine_refusal
from fake_api import FakeApi


def _copy(desk: Path, name: str = "2026-10-01-closing-time-copy") -> Path:
    copy = desk.parent / name
    shutil.copytree(desk, copy)
    return copy


def _edit(desk: Path, *extra: str) -> int:
    return produce_main(
        [
            "edit",
            "--desk",
            str(desk),
            "--episode",
            "1",
            "--beat",
            "1",
            "--intent",
            "She looks up.",
            *extra,
        ]
    )


def test_a_copy_desk_is_found_by_its_spine_id(desk: Path) -> None:
    copy = _copy(desk)

    assert desks_sharing_spine(desk) == [copy]
    assert desks_sharing_spine(copy) == [desk]


def test_a_desk_with_its_own_story_shares_nothing(desk: Path) -> None:
    copy = _copy(desk)
    state = json.loads((copy / "production.json").read_text())
    state["spine_id"] = "sp_other"
    (copy / "production.json").write_text(json.dumps(state))

    assert desks_sharing_spine(desk) == []


def test_an_edit_on_a_shared_story_is_refused_and_names_the_other_desk(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    copy = _copy(desk)

    assert _edit(desk) == 2

    err = capsys.readouterr().err
    assert "sp1" in err and str(copy) in err
    assert "--shared-spine-ok" in err and "Nothing was sent" in err
    assert [c for c in api.calls if c[0] in ("POST", "PATCH", "PUT", "DELETE")] == []


def test_shared_spine_ok_lets_it_through(desk: Path, api: FakeApi) -> None:
    _copy(desk)
    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = SystemExit(
        "HTTP 409: test_stop: stop here"
    )

    _edit(desk, "--shared-spine-ok")

    assert any(c[0] in ("POST", "PATCH") for c in api.calls), "the edit was sent"


def test_a_read_only_command_is_never_refused(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _copy(desk)

    assert produce_main(["status", "--desk", str(desk)]) == 0
    assert "shares" not in capsys.readouterr().err


@pytest.mark.parametrize("command", ["reel", "set-bed", "join", "review"])
def test_local_post_commands_are_never_refused_on_a_shared_story(
    desk: Path, command: str
) -> None:
    """``reel`` and ``set-bed`` write only on this desk (``reels/``, ``series.json``), never the server story."""

    _copy(desk)

    assert shared_spine_refusal(command, desk) is None


@pytest.mark.parametrize("command", ["edit", "film", "line", "finish"])
def test_commands_that_write_or_spend_are_still_refused_on_a_shared_story(
    desk: Path, command: str
) -> None:
    copy = _copy(desk)

    refused = shared_spine_refusal(command, desk)
    assert refused is not None and str(copy) in refused


def test_reel_on_a_shared_story_runs_without_the_flag(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _copy(desk)

    # The tiny desk has no finished take, so the reel stops later; what matters is that it is not refused.
    produce_main(["reel", "--desk", str(desk), "--episode", "1"])

    err = capsys.readouterr().err
    assert "Stopped: this desk's story" not in err
    assert "--shared-spine-ok" not in err


def test_music_note_yes_on_a_shared_story_is_refused_but_its_plan_is_not(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    """A plan (dry run) changes nothing; ``music-note --yes`` changes the one server story's music."""

    copy = _copy(desk)

    assert shared_spine_refusal("music-note", desk) is None
    assert produce_main(["music-note", "--desk", str(desk), "calmer", "--yes"]) == 2
    err = capsys.readouterr().err
    assert "Stopped: this desk's story" in err and str(copy) in err
    assert not [call for call in api.calls if call[0] == "POST"], "nothing was sent"
