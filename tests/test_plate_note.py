"""``plate-note``: list one character's notes or remove one, $0 (Don't Look, Hana ep 12, 27 Oct 2026).

A redraw that failed left its note on Emily's card, so her good portrait read as
out of date. Removing that note puts the card back to what the portrait was
drawn from, and the server calls it current again.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.ops.state import load_series
from fake_api import FakeApi

NOTE = "/v1/spines/sp1/cast/cast_ren/notes/n1"


def _ren(api: FakeApi) -> dict[str, Any]:
    return next(c for c in api.spine_doc["cast"] if c["cast_id"] == "cast_ren")


def _portrait(api: FakeApi) -> dict[str, Any]:
    return next(
        a for a in api.spine_doc["media_assets"] if a.get("relation_id") == "cast_ren"
    )


def _left_behind(api: FakeApi) -> None:
    """Ren's card holds the note of a redraw that failed; his portrait reads stale."""

    _ren(api)["creator_notes"] = [{"note_id": "n1", "text": "shorter hair"}]
    _portrait(api)["stale"] = True

    def remove(_method: str, _path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        assert (body or {}).get("spine_version")
        _ren(api)["creator_notes"] = []
        _portrait(api)["stale"] = False
        return {"spine": api.spine_doc}

    api.routes[("DELETE", NOTE)] = remove


def test_remove_by_number_clears_the_note_and_says_the_portrait_is_current(
    desk: Path, api: FakeApi
) -> None:
    _left_behind(api)
    out = io.StringIO()

    left = ec.run_plate_note(desk, cast="Ren", remove="1", out=out)

    assert left == []
    assert [c[:2] for c in api.calls if c[0] == "DELETE"] == [("DELETE", NOTE)]
    text = out.getvalue()
    assert "Ren has no notes." in text and "Ren's portrait is current." in text
    assert load_series(desk).spend_log == [], "nothing is drawn or charged"


def test_list_only_sends_nothing_and_says_the_portrait_is_out_of_date(
    desk: Path, api: FakeApi
) -> None:
    _left_behind(api)
    out = io.StringIO()

    left = ec.run_plate_note(desk, cast="Ren", out=out)

    assert left == ["shorter hair"]
    assert not [c for c in api.calls if c[0] != "GET"]
    text = out.getvalue()
    assert "1. n1  shorter hair" in text and "out of date" in text


def test_unknown_note_is_refused_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    _left_behind(api)

    with pytest.raises(
        ec.CommandStopped, match="no note '7' on Ren; the notes are: n1"
    ):
        ec.run_plate_note(desk, cast="Ren", remove="7", out=io.StringIO())

    assert not [c for c in api.calls if c[0] == "DELETE"]


def test_cli_runs_plate_note(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _left_behind(api)

    assert (
        produce_main(
            ["plate-note", "--desk", str(desk), "--cast", "Ren", "--remove", "n1"]
        )
        == 0
    )

    assert "Ren's portrait is current." in capsys.readouterr().out
