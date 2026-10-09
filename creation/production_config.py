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
    #: When true (the default), video enrol sets ``captions_enabled`` and the server burns its own
    #: captions on the episode it finishes (:func:`server_caption_style`). The raw take still comes
    #: back, and ``finish`` captions it locally in the desk's style. False: raw clip only.
    api_captions: bool = True
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
    #: On a legacy desk only: the first episode that gets the "Group B" fixes (founder decision,
    #: 7 Oct 2026; :data:`creation.rules_epoch.CONTINUING_FIXES_FROM_EPISODE`): per-word caption
    #: timing, captions without dashes and the pitch card. ``None``: Group B off. Set with
    #: ``continuing-fixes --desk D --apply`` (in step with the server spine's
    #: ``continuing_fixes_from_episode``), never by hand-editing ``rules_epoch``.
    continuing_fixes_from_episode: int | None = None
    #: The series episode number of this desk's episode 1, for "PART N" on the reel cover, the post
    #: text and ``reels/metrics.csv`` (NOCLIP, L-20261008-9: one desk per episode made every cover
    #: "PART 1"). Set by ``reel --part N``; ``None``: read from a one-episode desk's name
    #: (``…-ep03``), else the desk's own episode ordinal (:func:`creation.post.reel_cover.series_part`).
    first_part: int | None = None
    poll_plan_deadline_seconds: float = 1800.0
    poll_cast_deadline_seconds: float = 3600.0
    poll_boards_deadline_seconds: float = 7200.0
    poll_video_deadline_seconds: float = 7200.0


#: Caption styles only ``finish``, ``caption`` and ``reel`` burn; the server does not know them.
LOCAL_CAPTION_STYLES = frozenset({"bold", "subtle", "plain", "none"})
#: The server's caption recipe a desk with a local (or unchosen) style is captioned in.
SERVER_HOUSE_CAPTION_STYLE = "house"


def server_caption_style(config: ProductionConfig) -> str | None:
    """Return the caption style the server burns on the episode, or ``None`` for no server captions.

    Parameters
    ----------
    config
        The desk's config.

    Returns
    -------
    str | None
        ``None`` when ``api_captions`` is off or the desk asks for no captions; the desk's style when
        it is a server recipe; otherwise the server's ``house`` recipe.
    """

    if not config.api_captions or config.caption_style == "none":
        return None
    if config.caption_style is None or config.caption_style in LOCAL_CAPTION_STYLES:
        return SERVER_HOUSE_CAPTION_STYLE
    return config.caption_style


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
