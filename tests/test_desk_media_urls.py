"""Tests for spine vs terminal media URL resolution."""

from __future__ import annotations

import json
from pathlib import Path

from creation.desk_media_urls import board_urls_for_episode, cast_plate_urls


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
    (api_dir / "06_ep1_cast_terminal.json").write_text(json.dumps(terminal), encoding="utf-8")
    spine = {"cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_b"}]}
    assert cast_plate_urls(spine, api_dir) == ["https://x/a.png", "https://x/b.png"]


def test_cast_plate_urls_prefers_media_assets() -> None:
    spine = {
        "cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_b"}],
        "media_assets": [
            {"relation_type": "cast_card", "relation_id": "cast_a", "url": "https://x/a.png", "stale": False},
            {"relation_type": "cast_card", "relation_id": "cast_b", "url": "https://x/b.png", "stale": False},
        ],
    }
    assert cast_plate_urls(spine, Path("/nonexistent")) == ["https://x/a.png", "https://x/b.png"]


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
    (api_dir / "09_ep1_boards_terminal.json").write_text(json.dumps(terminal), encoding="utf-8")
    spine = {"episode_summaries": [{"ordinal": 1, "episode_id": "episode_01"}]}
    assert board_urls_for_episode(spine, api_dir, ordinal=1) == ["https://x/b1.png"]
