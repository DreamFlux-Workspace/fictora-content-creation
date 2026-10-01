"""The kit reads which lines are heard off screen from the server, not from the flag alone.

fictora-drama (founder decision 4): a line is heard off screen when it is
marked ``off_screen`` or when the frame or beat that carries it keeps its
speaker out of view (Hanakaze ep 1: Genzō's "Smile!" had ``off_screen: null``
while his frame said "NOT VISIBLE" and the beat forbade "visible Genzō"). The
server derives it on every read and lists the lines in
``heard_off_screen_line_ids``; the kit's line list, its board warnings and its
"speaks on a frame that does not draw them" check use that list. Against a
server without the field, the flag alone is read, as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from creation import episode_commands as ec
from creation.spine_view import heard_line_ids, heard_not_seen
from fake_api import spine_fixture

_REN_LINE = "line_episode_01_02"


def _derived(spine: dict[str, Any]) -> dict[str, Any]:
    return {**spine, "heard_off_screen_line_ids": [_REN_LINE]}


def test_the_server_list_and_the_flag_both_count() -> None:
    spine = spine_fixture()
    assert heard_line_ids(spine) == set()
    assert heard_line_ids(_derived(spine)) == {_REN_LINE}
    spine["beats"][1]["dialogue_lines"][1]["off_screen"] = True
    assert heard_line_ids(spine) == {"line_ep_02_02"}


def test_the_line_list_marks_a_derived_line_heard() -> None:
    rows = ec.line_listing(_derived(spine_fixture()), episode=1)

    ren = next(row for row in rows if _REN_LINE in row)
    assert "(off-screen)" in ren
    assert "(off-screen)" not in next(
        row for row in rows if "line_episode_01_01" in row
    )


def test_the_board_warning_counts_a_derived_line_as_heard() -> None:
    spine = _derived(spine_fixture())
    beats = [beat for beat in spine["beats"] if beat["episode_id"] == "episode_01"]
    for beat in beats:
        beat["dialogue_lines"] = [
            ln for ln in beat["dialogue_lines"] if ln["cast_id"] == "cast_ren"
        ]

    assert heard_not_seen(beats) == set()
    assert heard_not_seen(beats, heard_line_ids=heard_line_ids(spine)) == {"cast_ren"}


def test_no_out_of_frame_warning_for_a_line_the_server_already_hears(
    tmp_path: Path,
) -> None:
    spine = spine_fixture(approved=False)
    spine["beats"][0]["frame_id"] = "frame_episode_01_01"

    def consequences(story: dict[str, Any]) -> list[str]:
        return ec.line_edit_consequences(
            story, episode=1, line_id=_REN_LINE, after_gate=False, desk_was_approved=False,
            relocalized=False, desk=tmp_path,
        )  # fmt: skip

    # Ren speaks on a frame that stages only Hana.
    assert any("does not draw Ren" in row for row in consequences(spine))
    assert not any("does not draw Ren" in row for row in consequences(_derived(spine)))
