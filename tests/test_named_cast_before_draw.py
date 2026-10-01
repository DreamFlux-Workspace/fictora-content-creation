"""The board gate's name check runs before a board is paid for.

Hanakaze: a frame still said "Ren" while its cast list had Genzō. The check
(``names X but lists Y``) printed only after the boards were drawn, so a
leftover detail cost a $0.30 redraw. It now stops the paid draw first.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.production_state import load_production
from creation.spine_view import named_cast_stop
from fake_api import FakeApi, spine_fixture
from test_redraw_board_note import (
    NOTE,
    REGEN,
    _note_on_redraw_deploy,
    _record_digest,
    _regen_routes,
)

ENROL = "/v1/spines/sp1/boards/enrol"


def _name_ren_on_hanas_frame(spine: dict) -> None:
    """Row 1 of episode 1 stages Hana, but its words now describe Ren."""

    frame = next(f for f in spine["frames"] if f["frame_id"] == "frame_episode_01_01")
    frame["visual_brief"]["story_moment"] = "Ren lowers his phone as his smile fades."


def test_the_stop_names_the_take_frame_names_and_the_fix() -> None:
    spine = spine_fixture()
    _name_ren_on_hanas_frame(spine)

    text = named_cast_stop(spine, episode=1, desk="/d")

    assert text is not None
    assert text.startswith(
        "!! Stopped before drawing the boards: nothing was sent or paid."
    )
    assert "ep01 t1 frame_episode_01_01 (row 1) names Ren but lists Hana" in text
    assert (
        "fictora-produce edit --desk /d --episode 1 --frame frame_episode_01_01 "
        "--set subject_blocking.0.cast_id=cast_ren" in text
    )
    assert 'story_moment="…"' in text
    assert named_cast_stop(spine_fixture(), episode=1, desk="/d") is None
    assert named_cast_stop(spine, episode=2, desk="/d") is None, (
        "episode 2's frames are clean"
    )


def test_the_heads_up_says_step_will_stop() -> None:
    spine = spine_fixture()
    _name_ren_on_hanas_frame(spine)

    text = named_cast_stop(spine, episode=1, desk="/d", heads_up=True) or ""

    assert (
        text.startswith("!! Before the boards:")
        and "`step` stops before drawing them" in text
    )


def test_step_stops_before_paying_for_boards_when_a_frame_names_someone_it_does_not_list(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _name_ren_on_hanas_frame(api.spine_doc)
    set_phase(desk, "ready_boards_enrol")

    assert produce_main(["step", "--desk", str(desk)]) == 2

    err = capsys.readouterr().err
    assert "Stopped before drawing the boards: nothing was sent or paid" in err
    assert "frame_episode_01_01 (row 1) names Ren but lists Hana" in err
    assert "subject_blocking.0.cast_id=cast_ren" in err
    assert api.posted(ENROL) == []
    assert load_production(desk).phase == "ready_boards_enrol", (
        "not a failure: fix and step again"
    )


def test_a_redraw_of_frames_that_still_name_the_wrong_person_stops_before_paying(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    _name_ren_on_hanas_frame(api.spine_doc)

    with pytest.raises(ec.CommandStopped) as stopped:
        ec.run_redraw_board(
            desk, episode=1, take_id="t1", cause="ren row", out=io.StringIO()
        )

    text = str(stopped.value)
    assert "Stopped before redrawing t1: nothing was sent or paid" in text
    assert "names Ren but lists Hana" in text and "--frame frame_episode_01_01" in text
    assert api.posted(REGEN) == []


def test_a_redraw_that_re_authors_the_frames_goes_on_and_says_to_check_the_names(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    _note_on_redraw_deploy(api)
    _name_ren_on_hanas_frame(api.spine_doc)
    out = io.StringIO()

    ec.run_redraw_board(desk, episode=1, take_id="t1", note=NOTE, out=out)

    assert api.posted(REGEN)
    assert "check the names on the redrawn board" in out.getvalue()
    assert "names Ren but lists Hana" in out.getvalue()
