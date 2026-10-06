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
    #: Local caption style ``finish``, ``caption`` and ``reel`` burn: ``bold`` (one short white line at
    #: a time, one yellow word), ``subtle`` (yellow Arial Bold word flicker; ``house`` is its older name),
    #: ``plain`` (white whole lines) or ``none``; ``--caption-style`` on each command overrides it.
    #: ``None``: not chosen yet; a new show is captioned ``bold`` and a show with finished episodes
    #: ``subtle`` (:func:`creation.captions.show_caption_style`); the look approval saves the choice
    #: (:mod:`creation.caption_preview`). Sent to the API as its ``caption_style`` only with ``api_captions``.
    caption_style: str | None = None
    #: When false, video enrol omits ``captions_enabled`` (faster API tail; caption locally).
    api_captions: bool = False
    locale: str = "en-US"
    #: Language the cast speaks when not English: ``ja`` / ``ko`` (or ``ja-JP`` / ``ko-KR``). Captions stay English.
    spoken_language: str | None = None
    #: ``letterbox`` asks the server, at draft time, for a 4:3 picture in a 9:16 frame with a title
    #: band above and a caption band below (fictora-drama #595). The server decides the board aspect
    #: only at admission, so it must be on the draft. ``None`` (or ``portrait``) sends nothing: portrait.
    delivery_format: str | None = None
    #: Whose voices the show's takes speak in, chosen at ``start --voice-mode``: ``locked`` (each
    #: character's locked voice, filmed as a dialogue track) or ``model`` (the video model's own voices
    #: from the cast descriptions). Sent to the server once the story exists, and only while the server
    #: stores no choice for the show (:mod:`creation.voice_mode`); the server's is the truth after that.
    #: ``None`` sends nothing: a new show gets the server's default (locked on production).
    voice_mode: str | None = None
    #: A letterbox show's caption colour in its 9:16 file: ``yellow`` (house yellow) or ``white``.
    #: ``finish --caption-colour`` overrides it; ``None`` reads the spine's ``letterbox_caption_colour``
    #: (the creator's pick in the app), else yellow. Portrait shows never read it.
    letterbox_caption_colour: str | None = None
    #: Per-take dollars used only when the endpoint the server films on has no verified price in
    #: ``creation.prices``. ``None`` (default) prices such a take at the H3 Max Turbo dated rate.
    fallback_estimate_usd: float | None = None
    #: Posting, for the operator only (never sent to the server, never in the caption): the
    #: show's lane (one lane per account, e.g. ``mystery``), the Instagram ``account`` it posts
    #: on (``@handle``) and its ``posting_slot`` (``"18:30 IST"``; at least 2 h from the other
    #: accounts' slots, docs/content-ops/posting.md). ``reel`` prints them with the post text
    #: and writes them in ``reels/metrics.csv``.
    lane: str | None = None
    account: str | None = None
    posting_slot: str | None = None
    #: The desk's rules epoch (:mod:`creation.rules_epoch`): ``start`` stamps ``"2026-10-06"`` on a
    #: new desk; ``"legacy"`` keeps a desk's original behaviour (created before 6 Oct 2026). ``None``:
    #: classified by the desk's creation date. Set with ``rules-epoch --desk D --set ...``.
    rules_epoch: str | None = None
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
