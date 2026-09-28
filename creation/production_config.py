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

    #: Ignored since episode 1 is drafted alone (``outline_mode=arc_at_episode_two``): the draft
    #: always sends ``episode_count: 1`` and later episodes are written with ``author --episode N``.
    draft_episode_count: int = 1
    clip_duration_seconds: int = 15
    #: Shot plan set on the DRAFT so board and take agree: ``punchy`` (coverage, one shot per row;
    #: romance, horror, comedy, two-handers), ``slow_burn``, or ``one_shot`` (monologue, making, a walk).
    #: ``None`` sends nothing and the server's default (punchy) applies.
    cut_tempo: str | None = None
    caption_style: str = "house"
    #: When false, video enrol omits ``captions_enabled`` (faster API tail; caption locally).
    api_captions: bool = False
    locale: str = "en-US"
    #: Language the cast speaks when not English: ``ja`` / ``ko`` (or ``ja-JP`` / ``ko-KR``). Captions stay English.
    spoken_language: str | None = None
    #: Per-take dollars used only when the endpoint the server films on has no verified price in
    #: ``creation.prices``. ``None`` (default) prices such a take at the H3 Max Turbo dated rate.
    fallback_estimate_usd: float | None = None
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
