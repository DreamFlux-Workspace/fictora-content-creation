"""``run-notes.md`` exists from ``start``, and ``fictora-ops note`` makes it when it is missing.

Three Payments Late (5 Oct 2026): the desk root had no run-notes.md after ``start``, so the
first ``fictora-ops note --run-dir DESK`` failed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from creation.cli_ops import main as ops_main
from creation.ops.floor import init_series_desk
from creation.ops.notes import append_run_note, ensure_run_notes


def test_a_new_desk_has_run_notes_at_its_root(tmp_path: Path) -> None:
    desk = init_series_desk(
        tmp_path, "Three Payments Late", band="30s", episode_count=1
    )
    notes = desk / "run-notes.md"
    assert notes.is_file()
    assert notes.read_text(encoding="utf-8").startswith(
        "# Three Payments Late — run notes"
    )
    assert (desk / "ep01" / "run-notes.md").is_file(), (
        "each episode still keeps its own"
    )
    append_run_note(desk, "Desk started.")
    assert "Desk started." in notes.read_text(encoding="utf-8")


def test_note_makes_run_notes_when_it_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "old-desk"
    folder.mkdir()

    assert ops_main(["note", "--run-dir", str(folder), "--body", "first note"]) == 0

    text = (folder / "run-notes.md").read_text(encoding="utf-8")
    assert text.startswith("# old-desk — run notes") and "first note" in text
    assert "created" in capsys.readouterr().err


def test_ensure_run_notes_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "run-notes.md").write_text("# kept\n", encoding="utf-8")
    ensure_run_notes(tmp_path, "Other")
    assert (tmp_path / "run-notes.md").read_text(encoding="utf-8") == "# kept\n"


def test_note_on_a_folder_that_does_not_exist_still_fails(tmp_path: Path) -> None:
    assert ops_main(["note", "--run-dir", str(tmp_path / "nope"), "--body", "x"]) != 0
    assert not (tmp_path / "nope").exists()
