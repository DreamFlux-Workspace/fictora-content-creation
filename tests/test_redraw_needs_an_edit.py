"""``redraw-board`` stops before paying when nothing the board is drawn from changed (fake API).

The regenerate route takes no notes, so a redraw with the same frame briefs, beats,
look notes and plates draws the same direction again for $0.30. The kit refuses
that unless ``--reroll``; ``--note`` is kept on the desk and never sent.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.ops.state import episode_by_ordinal, load_series
from creation.production_state import load_production
from creation.spine_view import board_inputs, frames_by_set, frames_digest
from fake_api import FakeApi

REGEN = "/v1/spines/sp1/episodes/1/boards/1/regenerate"
NOTE = "her face is cut off by the caption band in row 2"


def _drawn(desk: Path, api: FakeApi, **extra: object) -> None:
    """Record the board as drawn from the fake spine as it is now; serve the regenerate route."""

    recorded: dict[str, object] = {
        "board_digests": {
            "ep01-t1": frames_digest(frames_by_set(api.spine_doc, episode=1)[1])
        },
        "board_inputs": {
            "ep01-t1": board_inputs(api.spine_doc, episode=1, set_index=1, take_count=1)
        },
    }
    set_phase(desk, "wait_board", **{**recorded, **extra})
    api.routes[("POST", REGEN)] = {"job_id": "job_redraw"}
    api.jobs["job_redraw"] = {"status": "completed"}
    api.routes[("GET", "/v1/spines/sp1/episodes/1/boards/exposure")] = {
        "boards": [{"set_index": 1, "mean_percent": 30.0}]
    }


def _redraw(desk: Path, *tail: str) -> int:
    return produce_main(
        ["redraw-board", "--desk", str(desk), "--episode", "1", "--take", "t1",
         "--cause", "face under the captions", *tail]
    )  # fmt: skip


def _paid(api: FakeApi) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _, _ in api.calls if m != "GET"]


def test_unchanged_briefs_stop_before_any_paid_call(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _drawn(desk, api)

    assert _redraw(desk, "--note", NOTE) == 2

    err = capsys.readouterr().err
    assert "Stopped: t1: nothing this board is drawn from has changed" in err
    assert "Nothing was sent or paid." in err
    assert "--frame N --set" in err and "--beat N --shot" in err and "look-note" in err
    assert "--reroll" in err
    assert _paid(api) == []
    assert episode_by_ordinal(load_series(desk), 1).spend_usd == 0
    assert load_production(desk).phase == "wait_board"


def test_reroll_redraws_the_same_frames(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _drawn(desk, api)

    assert _redraw(desk, "--reroll") == 0

    assert len(api.posted(REGEN)) == 1
    assert "--reroll: t1 is drawn again from the same frames" in capsys.readouterr().out
    redraws = episode_by_ordinal(load_series(desk), 1).takes[0].extra["redraws"]
    assert redraws[0]["reroll"] is True and "note" not in redraws[0]


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ("frame", "the frame briefs"),
        ("beat", "the take's beats"),
        ("look", "the look notes"),
        ("plate", "the cast plates"),
    ],
)
def test_a_changed_input_redraws_and_says_what_changed(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str], change: str, said: str
) -> None:
    _drawn(desk, api)
    if change == "frame":
        api.spine_doc["frames"][1]["visual_brief"]["shot_scale"] = "close-up"
    elif change == "beat":
        api.spine_doc["beats"][0]["shot_plan"] = [
            {"size": "wide", "subject": "the shop"}
        ]
    elif change == "look":
        api.spine_doc["look_notes"] = [{"note_id": "n1", "text": "warmer light"}]
    else:
        api.spine_doc["media_assets"][-1]["url"] = "https://r2.example/ren-v2.png"

    assert _redraw(desk) == 0

    assert len(api.posted(REGEN)) == 1
    assert f"t1 redraws with changed: {said}" in capsys.readouterr().out


def test_a_line_words_edit_alone_is_not_a_change(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _drawn(desk, api)
    api.spine_doc["beats"][0]["dialogue_lines"][0]["text"] = "We're closed, sorry."

    assert _redraw(desk) == 2
    assert _paid(api) == []


def test_a_board_the_server_marked_stale_redraws(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _drawn(desk, api, boards_stale=["ep01-t1"])

    assert _redraw(desk) == 0

    assert len(api.posted(REGEN)) == 1
    assert "marked this board stale" in capsys.readouterr().out
    assert load_production(desk).boards_stale == []


def test_the_cascade_marks_the_board_stale(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = {
        "proposal_id": "prop_1",
        "items": [],
    }
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = {
        "stale_storyboard_sets": [{"episode_ordinal": 1, "set_index": 1}]
    }

    ec.run_edit(
        desk, episode=1, beat="1", intent="Hana locks the door", out=io.StringIO()
    )

    assert load_production(desk).boards_stale == ["ep01-t1"]


def test_a_legacy_desk_compares_the_frame_briefs_alone(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _drawn(desk, api, board_inputs={})

    assert _redraw(desk) == 2

    assert "only the frame briefs were compared" in capsys.readouterr().err
    assert _paid(api) == []


def test_the_note_is_kept_on_the_desk_and_never_sent(desk: Path, api: FakeApi) -> None:
    _drawn(desk, api)
    api.spine_doc["frames"][1]["visual_brief"]["subject_blocking"][0][
        "frame_position"
    ] = "upper third"

    assert _redraw(desk, "--note", NOTE) == 0

    assert all(NOTE not in json.dumps(body) for _, _, body, _ in api.calls)
    redraw = episode_by_ordinal(load_series(desk), 1).takes[0].extra["redraws"][0]
    assert redraw["note"] == NOTE and redraw["cause"] == "face under the captions"
    assert redraw["reroll"] is False and redraw["changed"] == ["the frame briefs"]
    assert f"Note (label only): {NOTE}" in (desk / "ep01" / "run-notes.md").read_text(
        encoding="utf-8"
    )


def test_after_a_redraw_the_next_one_needs_another_edit(
    desk: Path, api: FakeApi
) -> None:
    _drawn(desk, api)
    api.spine_doc["look_notes"] = [{"note_id": "n1", "text": "warmer light"}]
    assert _redraw(desk) == 0
    api.calls.clear()

    assert _redraw(desk) == 2
    assert _paid(api) == []
