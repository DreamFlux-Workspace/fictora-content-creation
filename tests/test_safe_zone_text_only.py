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


def test_a_placement_warning_is_labelled_text_only_and_says_where_to_look() -> None:
    lines = safe_zone_lines(
        [_frame("bottom edge, left")], cast_names={"cast_hana": "Hana"}
    )

    warning = lines[0]
    assert warning.startswith("  !! text-only check, row 1 cell 1:")
    assert 'Hana\'s face is placed "bottom edge, left"' in warning
    assert "the drawing was not measured" in warning
    assert "look at row 1 cell 1 on the board" in warning
    assert "a false alarm when the drawing keeps it clear" in warning


def test_the_closing_line_says_what_the_check_reads() -> None:
    (only,) = safe_zone_lines([_frame("upper third, centre")])
    assert only.strip().startswith("safe zones (text-only check):")
    assert "not measured on the board image" in only
