"""Tests for spine vs terminal media URL resolution."""

from __future__ import annotations

import json
from pathlib import Path

from creation.desk_media_urls import (
    board_urls_for_episode,
    cast_plate_urls,
    drawn_cast_rows,
)
from creation.prices import H3_MAX_R2V_ENDPOINT, reference_images_ceiling


def test_cast_plate_urls_from_terminal_when_spine_bare(tmp_path: Path) -> None:
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    terminal = {
        "output": {
            "characters": [
                {"cast_id": "cast_a", "image_url": "https://x/a.png"},
                {"cast_id": "cast_b", "image_url": "https://x/b.png"},
            ]
        }
    }
    (api_dir / "06_ep1_cast_terminal.json").write_text(
        json.dumps(terminal), encoding="utf-8"
    )
    spine = {"cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_b"}]}
    assert cast_plate_urls(spine, api_dir) == ["https://x/a.png", "https://x/b.png"]


def test_cast_plate_urls_prefers_media_assets() -> None:
    spine = {
        "cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_b"}],
        "media_assets": [
            {
                "relation_type": "cast_card",
                "relation_id": "cast_a",
                "url": "https://x/a.png",
                "stale": False,
            },
            {
                "relation_type": "cast_card",
                "relation_id": "cast_b",
                "url": "https://x/b.png",
                "stale": False,
            },
        ],
    }
    assert cast_plate_urls(spine, Path("/nonexistent")) == [
        "https://x/a.png",
        "https://x/b.png",
    ]


def test_board_urls_from_terminal(tmp_path: Path) -> None:
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    terminal = {
        "output": {
            "boards": [
                {"episode_id": "episode_01", "image_url": "https://x/b1.png"},
                {"episode_id": "episode_02", "image_url": "https://x/b2.png"},
            ]
        }
    }
    (api_dir / "09_ep1_boards_terminal.json").write_text(
        json.dumps(terminal), encoding="utf-8"
    )
    spine = {"episode_summaries": [{"ordinal": 1, "episode_id": "episode_01"}]}
    assert board_urls_for_episode(spine, api_dir, ordinal=1) == ["https://x/b1.png"]


def test_cast_plate_urls_skip_a_voice_only_leftover_plate() -> None:
    """A voice-only character (server ``voice_only: true``) never reaches ``plates/``,
    even when a plate from before fictora-drama #453 is still on the spine."""

    spine = {
        "cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_voice", "voice_only": True}],
        "media_assets": [
            {
                "relation_type": "cast_card",
                "relation_id": "cast_a",
                "url": "https://x/a.png",
                "stale": False,
            },
            {
                "relation_type": "cast_card",
                "relation_id": "cast_voice",
                "url": "https://x/v.png",
                "stale": False,
            },
        ],
    }
    assert cast_plate_urls(spine, Path("/nonexistent")) == ["https://x/a.png"]


def test_drawn_cast_rows_drive_the_reference_image_estimate() -> None:
    """The R2V estimate counts plates sent; a voice sends none."""

    spine = {
        "cast": [
            {"cast_id": "cast_a"},
            {"cast_id": "cast_b", "voice_only": False},
            {"cast_id": "cast_voice", "voice_only": True},
        ]
    }
    assert [row["cast_id"] for row in drawn_cast_rows(spine)] == ["cast_a", "cast_b"]
    assert (
        reference_images_ceiling(len(drawn_cast_rows(spine)), H3_MAX_R2V_ENDPOINT) == 3
    )
