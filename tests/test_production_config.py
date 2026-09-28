"""Desk production.config.json loading."""

from __future__ import annotations

import json
from pathlib import Path

from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)


def test_defaults_when_missing(tmp_path: Path) -> None:
    cfg = load_production_config(tmp_path)
    assert (
        cfg.draft_episode_count == 1
    )  # episode 1 is drafted alone; later episodes are authored
    assert cfg.clip_duration_seconds == 15
    assert cfg.cut_tempo is None  # the server default (punchy) unless the desk sets one
    assert cfg.spoken_language is None
    assert (
        cfg.fallback_estimate_usd is None
    )  # an unpriced lane is priced at the Turbo rate, never a stale $1.20


def test_an_old_config_with_four_draft_episodes_still_loads(tmp_path: Path) -> None:
    (tmp_path / "production.config.json").write_text(
        json.dumps({"draft_episode_count": 4, "cut_tempo": "one_shot"}),
        encoding="utf-8",
    )
    cfg = load_production_config(tmp_path)
    assert (cfg.draft_episode_count, cfg.cut_tempo) == (4, "one_shot")


def test_round_trip(tmp_path: Path) -> None:
    cfg = ProductionConfig(draft_episode_count=4, fallback_estimate_usd=1.5)
    save_production_config(tmp_path, cfg)
    loaded = load_production_config(tmp_path)
    assert loaded.fallback_estimate_usd == 1.5
