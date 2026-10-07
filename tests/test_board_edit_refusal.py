"""The server's out-of-date-board refusals come with the commands that clear them (fictora-drama #641).

A wording-only edit (a line's words changed, staging unchanged) is refused at
film and estimate with ``board_behind_wording_edit`` until the board is
approved again (recommended, free) or redrawn. A staging edit is refused with
``board_behind_script_edit`` until the take is redrawn.
"""

from __future__ import annotations

from creation.harness.board_edit_refusal import board_edit_choice
from creation.harness.http_util import api_error_text

WORDING_MESSAGE = (
    'The line "Let go." changed to "Let go of me." after take 1\'s board was drawn. The board doesn\'t show the '
    "words, so no redraw is needed: approve the board again to film it as it is (free). Redraw it instead ($0.30) "
    "only if the new line changes what the take should show. Nothing was started and you were not charged."
)

WORDING = {
    "error": {
        "code": "board_behind_wording_edit",
        "message": WORDING_MESSAGE,
        "details": {
            "takes": [
                {"episode_ordinal": 2, "set_index": 1, "edited_beat_ids": ["beat_ep02_01"],
                 "changed_lines": [{"line_id": "line_ep02_01", "before": "Let go.", "after": "Let go of me."}]},
            ]
        },
    },
    "request_id": "req-1",
}  # fmt: skip


def test_a_wording_refusal_prints_the_message_then_approve_first_and_redraw_second() -> (
    None
):
    text = api_error_text(WORDING)

    assert text.startswith(f"board_behind_wording_edit: {WORDING_MESSAGE}")
    approve = text.index("`fictora-produce approve --desk D --gate board`")
    redraw = text.index("`fictora-produce redraw-board --desk D --episode 2 --take t1`")
    assert approve < redraw
    assert "Recommended (free): keep the board" in text


def test_a_staging_refusal_prints_only_the_redraw() -> None:
    staging = {
        "error": {
            "code": "board_behind_script_edit",
            "message": "The script of take 2 of episode 1 changed after its board was drawn ...",
            "details": {
                "takes": [
                    {
                        "episode_ordinal": 1,
                        "set_index": 2,
                        "edited_beat_ids": ["beat_ep01_03"],
                    }
                ]
            },
        }
    }

    text = api_error_text(staging)

    assert "`fictora-produce redraw-board --desk D --episode 1 --take t2`" in text
    assert "approve --desk D --gate board`, then film" not in text


def test_the_desk_is_filled_in_when_known_and_other_errors_get_nothing() -> None:
    choice = board_edit_choice(
        "board_behind_wording_edit", WORDING["error"]["details"], desk="~/desks/rain"
    )

    assert choice is not None
    assert "approve --desk ~/desks/rain --gate board" in choice
    assert "redraw-board --desk ~/desks/rain --episode 2 --take t1" in choice
    assert board_edit_choice("boards_stale", {}) is None
    assert "fictora-produce" not in api_error_text(
        {"error": {"code": "boards_stale", "message": "stale"}}
    )
