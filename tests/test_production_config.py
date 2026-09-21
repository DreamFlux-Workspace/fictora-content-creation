"""Desk production.config.json loading."""

from __future__ import annotations

import json
from pathlib import Path

from creation.production_config import ProductionConfig, load_production_config, save_production_config


def test_defaults_when_missing(tmp_path: Path) -> None:
    cfg = load_production_config(tmp_path)
    assert cfg.draft_episode_count == 4
    assert cfg.clip_duration_seconds == 15


def test_round_trip(tmp_path: Path) -> None:
    cfg = ProductionConfig(draft_episode_count=4, fallback_estimate_usd=1.5)
    save_production_config(tmp_path, cfg)
    loaded = load_production_config(tmp_path)
    assert loaded.fallback_estimate_usd == 1.5
