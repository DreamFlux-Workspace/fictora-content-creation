"""The board's safe-zone warning says it read the written placement, not the drawing (backlog: safe zones)."""

from __future__ import annotations

from creation.spine_view import safe_zone_lines


def _frame(position: str) -> dict:
    return {
        "frame_id": "f1",
        "board_row": 1,
        "visual_brief": {
            "subject_blocking": [{"cast_id": "cast_hana", "frame_position": position}]
        },
    }


def test_a_placement_is_listed_as_a_cell_to_look_at_not_flagged() -> None:
    lines = safe_zone_lines(
        [_frame("bottom edge, left")], cast_names={"cast_hana": "Hana"}
    )

    warning = lines[0]
    assert warning.startswith("  look at row 1 cell 1:")
    assert 'Hana\'s face is written "bottom edge, left"' in warning
    assert "Written words only, not a finding" in warning


def test_the_closing_line_says_what_the_check_reads() -> None:
    (only,) = safe_zone_lines([_frame("upper third, centre")])
    assert only.strip().startswith("safe zones:")
    assert "Not measured on the board image" in only and "never flagged" in only
