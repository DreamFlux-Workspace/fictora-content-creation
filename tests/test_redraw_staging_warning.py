"""A redraw that re-authors the take's frames says so, with the rows as they stand, before anything is paid."""

from __future__ import annotations

import io
from pathlib import Path

from creation import episode_commands as ec
from fake_api import FakeApi
from test_redraw_board_note import (
    NOTE,
    REGEN,
    _note_on_redraw_deploy,
    _record_digest,
    _regen_routes,
)


def _said_before_the_draw(api: FakeApi, out: io.StringIO) -> list[str]:
    """Wrap the regenerate route: keep what was printed up to the paid POST."""

    seen: list[str] = []
    inner = api.routes[("POST", REGEN)]

    def regenerate(method: str, path: str, body: dict | None) -> dict:
        seen.append(out.getvalue())
        return inner(method, path, body)

    api.routes[("POST", REGEN)] = regenerate
    return seen


def test_a_note_redraw_warns_about_restaging_and_lists_the_rows_before_paying(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    _note_on_redraw_deploy(api)
    out = io.StringIO()
    seen = _said_before_the_draw(api, out)

    ec.run_redraw_board(desk, episode=1, take_id="t1", note=NOTE, out=out)

    (before,) = seen
    assert "!! STAGING MAY CHANGE" in before
    assert "cast_refs" in before and "shot_scale" in before and "cell_role" in before
    assert "no preview" in before
    assert "t1 rows now (compare after the redraw):" in before
    assert "row 1:" in before
    assert "edit --desk" in before and "--episode 1 --frame N --set" in before


def test_a_redraw_of_edited_frames_alone_draws_them_as_they_stand_without_the_warning(
    desk: Path, api: FakeApi
) -> None:
    _record_digest(desk, api)
    _regen_routes(api)
    for frame in api.spine_doc["frames"]:
        if frame["episode_id"] == "episode_01":
            frame["visual_brief"]["camera_angle"] = "low angle"
    out = io.StringIO()

    ec.run_redraw_board(desk, episode=1, take_id="t1", cause="lower angle", out=out)

    assert "STAGING MAY CHANGE" not in out.getvalue()
