"""The server refuses a take whose board is still a shot-clips grid (fictora-drama #914); the kit says what to do."""

from __future__ import annotations

from creation.harness.http_util import SHOT_CLIPS_BOARD_FIX, api_error_text


def test_the_refusal_says_redraw_the_board_and_that_nothing_was_charged() -> None:
    body = {
        "error": {
            "code": "shot_clips_board",
            "message": "This take's board was drawn for shot clips; redraw the board first. Nothing was filmed or charged.",
            "details": {
                "nothing_sent": True,
                "board_asset_ids": ["asset_plan_board_x_episode_02_set01"],
            },
        },
        "request_id": "req-1",
    }

    text = api_error_text(body)

    assert text.startswith(
        "shot_clips_board: This take's board was drawn for shot clips"
    )
    assert text.endswith(SHOT_CLIPS_BOARD_FIX)
    assert "redraw-board" in text and "Nothing was filmed or charged." in text


def test_every_other_refusal_reads_as_before() -> None:
    text = api_error_text({"error": {"code": "other", "message": "no"}})

    assert text == "other: no"
