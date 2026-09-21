"""Desk store persistence tests."""

from __future__ import annotations

from pathlib import Path

from content_desk.store import DeskStore, GateState


def test_create_and_reload_desk(tmp_path: Path) -> None:
    """A desk round-trips through JSON on disk."""

    store = DeskStore(tmp_path)
    desk = store.create(
        title="Test Series",
        prompt="A locked premise.",
        band="15s",
        session_id="sess-test",
        preset_id="modern-romance-3",
        preset_version="1.0",
        video_lane="minimax-h3",
    )
    assert desk.desk_id.startswith("desk_")
    loaded = store.get(desk.desk_id)
    assert loaded.title == "Test Series"
    assert loaded.gates["plates"] == GateState()


def test_list_desks_sorted_by_update(tmp_path: Path) -> None:
    """list() returns newest desks first."""

    store = DeskStore(tmp_path)
    first = store.create(
        title="First",
        prompt="p",
        band="15s",
        session_id="s1",
        preset_id="x",
        preset_version="1",
        video_lane="minimax-h3",
    )
    second = store.create(
        title="Second",
        prompt="p",
        band="15s",
        session_id="s2",
        preset_id="x",
        preset_version="1",
        video_lane="minimax-h3",
    )
    first.gates["plates"] = GateState(status="approved")
    store.write(first)
    rows = store.list()
    assert {row.desk_id for row in rows} == {first.desk_id, second.desk_id}
    assert rows[0].updated_at_utc >= rows[1].updated_at_utc
