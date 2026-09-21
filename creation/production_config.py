"""Desk-level API tuning for fictora-produce (see episode-production skill)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

CONFIG_FILENAME = "production.config.json"


@dataclass
class ProductionConfig:
    """Overrides for Drama API calls on one series desk.

    Stored at ``<desk>/production.config.json``. Missing file uses defaults.
    The episode-production skill documents every field.
    """

    draft_episode_count: int = 4
    clip_duration_seconds: int = 15
    cut_tempo: str = "one_shot"
    caption_style: str = "house"
    locale: str = "en-US"
    fallback_estimate_usd: float = 1.20
    poll_plan_deadline_seconds: float = 1800.0
    poll_cast_deadline_seconds: float = 3600.0
    poll_boards_deadline_seconds: float = 7200.0
    poll_video_deadline_seconds: float = 7200.0


def config_path(desk: Path) -> Path:
    """Return ``production.config.json`` on the desk root."""

    return desk.expanduser().resolve() / CONFIG_FILENAME


def load_production_config(desk: Path) -> ProductionConfig:
    """Load desk config or defaults when the file is absent."""

    path = config_path(desk)
    if not path.is_file():
        return ProductionConfig()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{CONFIG_FILENAME} must be a JSON object")
    allowed = {f.name for f in fields(ProductionConfig)}
    kwargs = {key: raw[key] for key in raw if key in allowed}
    return ProductionConfig(**kwargs)


def save_production_config(desk: Path, config: ProductionConfig) -> Path:
    """Write desk config next to ``production.json``."""

    path = config_path(desk)
    path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
    return path
