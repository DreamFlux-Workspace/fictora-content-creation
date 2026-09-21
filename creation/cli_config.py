"""Shared CLI flags for desk production config."""

from __future__ import annotations

import argparse

from creation.production_config import ProductionConfig


def add_production_config_args(parser: argparse.ArgumentParser) -> None:
    """Register flags that map to ``production.config.json``."""

    parser.add_argument(
        "--draft-episodes",
        type=int,
        default=4,
        help="Planned episodes in draft (prod cadence: 4, not 5).",
    )
    parser.add_argument("--clip-seconds", type=int, default=15, help="Take length 4–15.")
    parser.add_argument("--cut-tempo", default="one_shot", help="e.g. one_shot, punchy, slow_burn.")
    parser.add_argument("--caption-style", default="house", help="Burn-in preset when captions enabled.")
    parser.add_argument(
        "--fallback-estimate-usd",
        type=float,
        default=1.20,
        help="Desk spend line when batch estimate API skips or returns no USD.",
    )


def config_from_args(args: argparse.Namespace) -> ProductionConfig:
    """Build ``ProductionConfig`` from parsed CLI namespace."""

    return ProductionConfig(
        draft_episode_count=int(args.draft_episodes),
        clip_duration_seconds=int(args.clip_seconds),
        cut_tempo=str(args.cut_tempo),
        caption_style=str(args.caption_style),
        fallback_estimate_usd=float(args.fallback_estimate_usd),
    )
