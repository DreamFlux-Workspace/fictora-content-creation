"""Shared CLI flags for desk production config."""

from __future__ import annotations

import argparse
from dataclasses import asdict

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
        default=None,
        help="Caption style for the show, read by local finish, caption and reel: bold (one short white line "
        "at a time, one yellow word; a new show's default), subtle (yellow Arial Bold word flicker; house is "
        "its older name), plain (white whole lines) or none (no captions). Unset: the look approval shows "
        "both on a still and saves bold unless the human picks subtle. finish/caption/reel --caption-style "
        "overrides it per run. Sent to the API as its caption_style only with --api-captions.",
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

    style = str(args.caption_style) if args.caption_style else None
    if args.api_captions and style is None:
        style = "house"  # the server's own house captions, as before
    if args.api_captions and style in ("bold", "subtle", "plain", "none"):
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


def merge_config_from_args(
    saved: ProductionConfig, args: argparse.Namespace
) -> ProductionConfig:
    """Return the desk's saved config with only the flags the operator passed laid over it.

    ``bind`` used to rebuild ``production.config.json`` from its own flags, so a
    re-bind with just ``--prompt`` silently dropped the desk's language, voice
    mode, caption style, tempo and format (Back from the Sea L-20261008-2: the
    show was drafted in English). A flag left unset now keeps the saved value;
    a flag given replaces it. Rules epoch, Group B marker, posting fields and
    poll deadlines are never touched by flags, so they always survive.
    """

    fresh = config_from_args(args)  # validates --caption-style / --api-captions
    merged = ProductionConfig(**asdict(saved))
    if getattr(args, "cut_tempo", None):
        merged.cut_tempo = fresh.cut_tempo
    if getattr(args, "language", None):
        merged.spoken_language = fresh.spoken_language
    if getattr(args, "voice_mode", None):
        merged.voice_mode = fresh.voice_mode
    format_changed = bool(getattr(args, "delivery_format", None)) and (
        fresh.delivery_format != saved.delivery_format
    )
    if getattr(args, "delivery_format", None):
        merged.delivery_format = fresh.delivery_format
    if getattr(args, "clip_seconds", None) is not None or format_changed:
        merged.clip_duration_seconds = fresh.clip_duration_seconds
    if getattr(args, "caption_style", None) or getattr(args, "api_captions", False):
        merged.caption_style = fresh.caption_style
    if getattr(args, "api_captions", False):
        merged.api_captions = True
    if getattr(args, "fallback_estimate_usd", None) is not None:
        merged.fallback_estimate_usd = fresh.fallback_estimate_usd
    return merged


#: Art-style preset and video lane a desk that was never bound gets from ``bind``.
DEFAULT_PRESET_ID = "modern-dark-fantasy"
DEFAULT_VIDEO_LANE = "minimax-h3"


def bound_preset_and_lane(
    desk, *, preset_id: str | None, video_lane: str | None
) -> tuple[str, str]:
    """Return the preset and lane ``bind`` should use: the flag when given, else the desk's saved ones.

    ``bind`` defaulted ``--preset-id`` to modern-dark-fantasy and ``--video-lane``
    to minimax-h3, so re-binding a desk to change only its brief silently
    switched its art style and lane, the same family as L-20261008-2.
    """

    from creation.production_state import load_production, production_path

    saved = (
        load_production(desk)
        if production_path(desk.expanduser().resolve()).is_file()
        else None
    )
    return (
        preset_id
        or (saved.preset_id if saved and saved.preset_id else DEFAULT_PRESET_ID),
        video_lane
        or (saved.video_lane if saved and saved.video_lane else DEFAULT_VIDEO_LANE),
    )
