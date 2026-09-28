"""Shared CLI flags for desk production config."""

from __future__ import annotations

import argparse

from creation.production_config import ProductionConfig


def add_production_config_args(parser: argparse.ArgumentParser) -> None:
    """Register flags that map to ``production.config.json``."""

    parser.add_argument(
        "--draft-episodes",
        type=int,
        default=1,
        help="Ignored: episode 1 is drafted alone; write later episodes with `author --episode N`.",
    )
    parser.add_argument("--clip-seconds", type=int, default=15, help="Take length 4–15.")
    parser.add_argument(
        "--cut-tempo",
        default=None,
        choices=("punchy", "slow_burn", "one_shot"),
        help="Shot plan, set on the draft: punchy (coverage) or one_shot/slow_burn. Default: server's (punchy).",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Spoken language when not English: ja or ko (captions stay English).",
    )
    parser.add_argument(
        "--caption-style",
        default="house",
        help="Local caption recipe name (see skill); sent to API only with --api-captions.",
    )
    parser.add_argument(
        "--api-captions",
        action="store_true",
        help="Burn captions on the Drama API (slow post-production). Default: raw clip only.",
    )
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
        cut_tempo=str(args.cut_tempo) if args.cut_tempo else None,
        spoken_language=str(args.language) if getattr(args, "language", None) else None,
        caption_style=str(args.caption_style),
        api_captions=bool(args.api_captions),
        fallback_estimate_usd=float(args.fallback_estimate_usd),
    )
