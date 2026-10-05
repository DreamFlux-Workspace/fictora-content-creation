"""The show's music blend: one line on the estimate, a free command to read or change it (2026-10-05).

The score follows each beat's music intent and plays the blend's moods on
ambiguous beats. The blend never gates anything: ``step`` and the estimate
print it on one line, ``music-blend`` reads it, and ``--set`` / ``--default``
change it only when the human asks.
"""

from __future__ import annotations

import copy
import io
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import music_blend as music_blend_mod
from creation import orchestrate
from creation.cli_produce import main as produce_main
from creation.music_blend import (
    MUSIC_BLEND_FAMILIES,
    music_blend_line,
    parse_blend,
    run_music_blend,
    set_music_blend,
    show_music_blend,
)
from fake_api import FakeApi

ROUTE = "/v1/spines/sp1/music-blend"
NOTICE = "Only takes filmed from now on play the romance + suspense blend; takes already filmed keep their music."


def _server(api: FakeApi) -> FakeApi:
    """The music-blend routes as fictora-drama answers them: the show's own blend, else the genre's."""

    def read(_m: str, _p: str, _body: Any) -> dict[str, Any]:
        stored = api.spine_doc.get("music_blend")
        return {
            "spine_id": "sp1",
            "spine_version": api.spine_doc["spine_version"],
            "music_blend": stored or ["romance"],
            "source": "show" if stored else "genre",
            "default_music_blend": ["romance"],
            "families": list(MUSIC_BLEND_FAMILIES),
        }

    def write(_m: str, _p: str, body: dict[str, Any] | None) -> Any:
        assert body is not None
        if body["spine_version"] != api.spine_doc["spine_version"]:
            return 409, {"error": {"code": "spine_version_conflict"}}
        before = read("GET", ROUTE, None)
        api.spine_doc["music_blend"] = body["music_blend"]
        api.spine_doc["music_blend_from_creator"] = bool(body["music_blend"])
        api.spine_doc["spine_version"] += "+"
        after = read("GET", ROUTE, None)
        changed = after["music_blend"] != before["music_blend"]
        return 200, {
            "spine_id": "sp1",
            "spine_version": api.spine_doc["spine_version"],
            "previous_music_blend": before["music_blend"],
            "previous_source": before["source"],
            "music_blend": after["music_blend"],
            "source": after["source"],
            "changed": changed,
            "applied": changed,
            "notice": NOTICE if changed and body["music_blend"] else None,
        }

    api.routes[("GET", ROUTE)] = read
    api.routes[("POST", ROUTE)] = write
    return api


@pytest.fixture
def show(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    monkeypatch.setattr(music_blend_mod, "open_api", lambda _desk, _episode: api)
    return _server(api)


def test_a_blend_is_read_from_known_families_only() -> None:
    assert parse_blend("romance,suspense") == ("romance", "suspense")
    assert parse_blend("Romance + Slice of life") == ("romance", "slice_of_life")
    with pytest.raises(ValueError, match="unknown music family"):
        parse_blend("romance,polka")
    with pytest.raises(ValueError, match="at most 3"):
        parse_blend("romance,suspense,comedy,action")
    with pytest.raises(ValueError, match="name one"):
        parse_blend(" , ")


def test_the_server_answer_and_why(show: FakeApi) -> None:
    blend = show_music_blend(show, show.spine_doc)

    assert (blend.families, blend.source) == (("romance",), "genre")
    assert blend.line() == "Music blend: romance (the show's genre)."


def test_an_older_server_reads_the_saved_spine_and_says_so(api: FakeApi) -> None:
    blend = show_music_blend(api, api.spine_doc)
    assert blend.source == "older-server" and "older deploy" in blend.line()

    api.spine_doc["music_blend"] = ["romance", "suspense"]
    assert show_music_blend(api, api.spine_doc).families == ("romance", "suspense")

    api.routes[("POST", ROUTE)] = lambda *_: (404, {"detail": "Not Found"})
    with pytest.raises(RuntimeError, match="no music-blend route yet"):
        set_music_blend(api, api.spine_doc, ["romance"])


def test_a_stale_version_is_read_again_once(show: FakeApi) -> None:
    stale = copy.deepcopy(show.spine_doc)
    show.spine_doc["spine_version"] += "+newer"

    answer = set_music_blend(show, stale, ["romance", "suspense"])

    assert answer["applied"] is True
    assert [b["spine_version"] for b in show.posted(ROUTE)] == [
        stale["spine_version"],
        stale["spine_version"] + "+newer",
    ]


def test_music_blend_sets_and_prints_the_servers_notice(
    desk: Path, show: FakeApi
) -> None:
    out = io.StringIO()

    blend = run_music_blend(desk, set_to="romance,suspense", out=out)

    assert [body["music_blend"] for body in show.posted(ROUTE)] == [
        ["romance", "suspense"]
    ]
    assert (blend.families, blend.source) == (("romance", "suspense"), "show")
    text = out.getvalue()
    assert "Music blend changed for this show." in text and NOTICE in text
    assert "Music blend: romance + suspense (set for this show)." in text

    # --default sends null: back to the genre's.
    back = run_music_blend(desk, default=True, out=io.StringIO())
    assert show.posted(ROUTE)[-1]["music_blend"] is None and back.source == "genre"


def test_reading_the_blend_sends_nothing(desk: Path, show: FakeApi) -> None:
    out = io.StringIO()

    run_music_blend(desk, out=out)

    assert show.posted(ROUTE) == []
    assert "Change it: fictora-produce music-blend" in out.getvalue()


def test_the_cli_refuses_an_unknown_family_before_sending(
    desk: Path, show: FakeApi
) -> None:
    with pytest.raises(ValueError, match="unknown music family"):
        run_music_blend(desk, set_to="polka")
    assert show.posted(ROUTE) == []
    with pytest.raises(SystemExit):
        produce_main(
            ["music-blend", "--desk", str(desk), "--set", "romance", "--default"]
        )


def test_the_estimate_prints_the_blend_on_one_line_and_holds_nothing(
    desk: Path, show: FakeApi
) -> None:
    set_phase(desk, "ready_estimate")
    show.routes[("POST", "/v1/spines/sp1/batches/estimate")] = {
        "cost_estimate": {"total_usd": "1.20", "priced_on": "2026-10-05", "takes": 1}
    }

    result = orchestrate.run_step(desk)

    assert result.message.count("Music blend:") == 1
    assert "Music blend: romance (the show's genre)." in result.message
    assert result.phase == "wait_spend"
    assert show.posted(ROUTE) == []


def test_a_failing_read_never_stops_the_estimate(show: FakeApi) -> None:
    show.routes[("GET", ROUTE)] = lambda *_: SystemExit("boom")

    assert music_blend_line(show, show.spine_doc).startswith("Music blend: not known")
