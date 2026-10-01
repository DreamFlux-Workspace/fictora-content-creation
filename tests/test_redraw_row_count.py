"""A redraw that loses or adds a board row says so loudly (L-20260930-7)."""

from __future__ import annotations

from typing import Any

from creation.board_note import row_change_lines


def _frames(*rows: tuple[int, str]) -> list[dict[str, Any]]:
    return [
        {
            "board_row": row,
            "ordinal": row,
            "visual_brief": {"shot_scale": scale, "camera_angle": "eye level"},
        }
        for row, scale in rows
    ]


def test_a_lost_row_is_named_even_when_every_kept_row_reads_the_same() -> None:
    before = _frames((1, "wide"), (2, "medium"), (3, "close-up"))
    after = _frames((1, "wide"), (2, "medium"))

    lines = row_change_lines(before, after)

    text = "\n".join(lines)
    assert lines, "a lost row is never 'rows read the same'"
    assert (
        "ROW COUNT CHANGED: the board had 3 row(s) before the redraw and has 2 now"
        in text
    )
    assert "ROW LOST: row 3: close-up" in text


def test_an_added_row_is_named() -> None:
    lines = row_change_lines(_frames((1, "wide")), _frames((1, "wide"), (2, "medium")))

    text = "\n".join(lines)
    assert "ROW COUNT CHANGED" in text and "ROW ADDED: row 2: medium" in text


def test_same_rows_same_shots_report_nothing_and_changed_rows_read_was_now() -> None:
    same = _frames((1, "wide"), (2, "medium"))
    assert row_change_lines(same, same) == []
    lines = row_change_lines(same, _frames((1, "wide"), (2, "close-up")))
    assert lines == [
        "  was " + lines[0][len("  was ") :],
        "  now " + lines[1][len("  now ") :],
    ]
    assert "medium" in lines[0] and "close-up" in lines[1]
    assert not any("ROW" in line for line in lines)
