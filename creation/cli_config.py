"""Shared CLI flags for desk production config."""

from __future__ import annotations

import argparse

from creation.ops.state import LETTERBOX_TAKE_SECONDS_FOR_BAND
from creation.production_config import ProductionConfig


def add_production_config_args(parser: argparse.ArgumentParser) -> None:
    """Register flags that map to ``production.config.json``."""

    parser.add_argument(
        "--draft-episodes",
        type=int,
        default=1,
        help="Ignored: episode 1 is drafted alone; write later episodes with `author --episode N`.",
    )
    parser.add_argument(
        "--clip-seconds",
        type=int,
        default=None,
        help="Take length 4–15. Default 15, or with --delivery-format letterbox the server's 4:3 "
        "take length: 10 s at 30s/60s, 7 s at 15s (two takes, a 14 s episode).",
    )
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
        "--delivery-format",
        default=None,
        choices=("portrait", "letterbox"),
        help="Set on the draft: letterbox films a 4:3 picture with a title band above and a caption "
        "band below (needs a server with fictora-drama #595). Default: portrait.",
    )
    parser.add_argument(
        "--voice-mode",
        default=None,
        choices=("locked", "model"),
        help="Whose voices the show's takes speak in: locked (each character's locked voice, filmed as a "
        "dialogue track; the Voices gate applies) or model (the video model's own voices from the cast "
        "descriptions; no Voices gate). Sent to the server once the story exists. Default: the server's "
        "(locked for a new show; a show filmed with model voices keeps them).",
    )
    parser.add_argument(
        "--caption-style",
        default="house",
        help="Caption style for the desk, read by local finish and caption: house (yellow Arial Bold word "
        "flicker, default), plain (white whole lines) or none (no captions). finish/caption "
        "--caption-style overrides it per run. Sent to the API as its caption_style only with --api-captions.",
    )
    parser.add_argument(
        "--api-captions",
        action="store_true",
        help="Burn captions on the Drama API (slow post-production). Default: raw clip only.",
    )
    parser.add_argument(
        "--fallback-estimate-usd",
        type=float,
        default=None,
        help=(
            "Per-take USD used only when the server's lane has no verified price in the table. "
            "Default: the H3 Max Turbo dated rate."
        ),
    )


#: Take length when ``--clip-seconds`` is not given.
DEFAULT_CLIP_SECONDS = 15
#: Take length of a letterbox desk when ``--clip-seconds`` is not given: the
#: server films 10-second 4:3 takes at 30 s and 60 s (fictora-drama #604).
LETTERBOX_CLIP_SECONDS = 10


def default_clip_seconds(args: argparse.Namespace) -> int:
    """Return the take length: ``--clip-seconds``, else the server's 4:3 take for letterbox, else 15.

    A letterbox take is 10 s at 30 s and 60 s and 7 s at 15 s (two takes, a
    14 s episode; fictora-drama #617).
    """

    if getattr(args, "clip_seconds", None) is not None:
        return int(args.clip_seconds)
    if getattr(args, "delivery_format", None) == "letterbox":
        band = getattr(args, "band", None)
        return LETTERBOX_TAKE_SECONDS_FOR_BAND.get(str(band), LETTERBOX_CLIP_SECONDS)
    return DEFAULT_CLIP_SECONDS


def config_from_args(args: argparse.Namespace) -> ProductionConfig:
    """Build ``ProductionConfig`` from parsed CLI namespace."""

    style = str(args.caption_style)
    if args.api_captions and style in ("plain", "none"):
        raise ValueError(
            f"--caption-style {style} is a local caption style: the server does not burn it. "
            "Drop --api-captions (captions are burned locally by finish), or name a server caption style"
        )
    return ProductionConfig(
        draft_episode_count=int(args.draft_episodes),
        clip_duration_seconds=default_clip_seconds(args),
        cut_tempo=str(args.cut_tempo) if args.cut_tempo else None,
        spoken_language=str(args.language) if getattr(args, "language", None) else None,
        delivery_format=(
            str(args.delivery_format)
            if getattr(args, "delivery_format", None)
            else None
        ),
        voice_mode=(
            str(args.voice_mode) if getattr(args, "voice_mode", None) else None
        ),
        caption_style=style,
        api_captions=bool(args.api_captions),
        fallback_estimate_usd=(
            float(args.fallback_estimate_usd)
            if args.fallback_estimate_usd is not None
            else None
        ),
    )
