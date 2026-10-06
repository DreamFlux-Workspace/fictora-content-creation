"""Machine state for a content-ops series desk."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from creation import prices as _prices

SCHEMA_VERSION = "fictora.content-ops.series.v1"
SERIES_FILENAME = "series.json"
QUEUE_FILENAME = "QUEUE.md"

Band = Literal["15s", "30s", "60s"]
GateStatus = Literal["pending", "approved", "rejected"]
TakeVerdict = Literal["pending", "use", "change"]

TAKES_FOR_BAND: dict[str, int] = {"15s": 1, "30s": 2, "60s": 4}
#: Takes per band on a letterbox (4:3) story: one per 10 s, rounded up
#: (fictora-drama #604, #617). 30 s is three 10-second takes, 60 s six, and
#: 15 s two 7-second takes (a 14 s episode).
LETTERBOX_TAKES_FOR_BAND: dict[str, int] = {"15s": 2, "30s": 3, "60s": 6}
#: Seconds one letterbox (4:3) take films on the server, per band: episode
#: length over take count, rounded down (``storyboard_take_seconds``).
LETTERBOX_TAKE_SECONDS_FOR_BAND: dict[str, int] = {"15s": 7, "30s": 10, "60s": 10}
#: Seconds one letterbox (4:3) take films at 30 s and 60 s.
LETTERBOX_TAKE_SECONDS = 10
MAX_LINES_PER_TAKE = 3
#: Preflight warns (never blocks) past this multiple of the envelope.
ENVELOPE_STOP_MULTIPLIER = 2.0
#: Warn-only spend envelopes. One source: ``creation.prices`` (sized for R2V; Turbo takes sit inside them).
ENVELOPE_FIRST_USD = _prices.ENVELOPE_FIRST_USD
ENVELOPE_CONTINUING_USD: dict[str, float] = dict(_prices.ENVELOPE_CONTINUING_USD)

SERIES_GATES = frozenset({"look", "plates"})
EPISODE_GATES = frozenset({"script", "board", "post"})


@dataclass
class GateRecord:
    """One human gate. Silence is not approval.

    Parameters
    ----------
    status
        ``pending``, ``approved``, or ``rejected``.
    at_utc
        When the human answered, if they have.
    path
        Review file they looked at.
    note
        Optional reason or luma line.
    luma_percent
        Board brightness when the gate is a board.
    """

    status: GateStatus = "pending"
    at_utc: str | None = None
    path: str | None = None
    note: str | None = None
    luma_percent: float | None = None


@dataclass
class SpokenLine:
    """One spoken or inner-voice line on a take.

    Parameters
    ----------
    speaker
        Character name.
    original
        Line in the language that will be heard.
    translation
        Translation shown to the operator.
    """

    speaker: str
    original: str
    translation: str = ""


@dataclass
class PreflightOverride:
    """An operator's explicit decision to film past preflight warnings (covers the next film only).

    Parameters
    ----------
    at_utc
        When the operator confirmed.
    unit_id
        The unit id they typed (``ep01-t1``).
    codes
        Warning codes that were open.
    details
        The warning text they were shown.
    filmed_count
        Films of the unit when confirmed; the override covers the next film only.
    unit_spend_usd, episode_spend_usd
        Spend on the unit and the episode at that moment.
    """

    at_utc: str
    unit_id: str
    codes: list[str]
    details: list[str]
    filmed_count: int
    unit_spend_usd: float
    episode_spend_usd: float


@dataclass
class SpendEntry:
    """One line in the desk's spend ledger: what the money bought.

    The same keys the retired internal kit wrote to ``spend_log``, so an adopted
    desk's ledger reads as it is.

    Parameters
    ----------
    at_utc
        When it was booked.
    episode
        Episode ordinal it was charged to.
    usd
        Amount.
    unit
        What was bought (``board``, ``look-frame``, ``voice-line``, ``cue:gaan-sting``);
        ``unlabelled`` when the booking did not say.
    take_id
        The take it was charged to, if any.
    """

    at_utc: str
    episode: int
    usd: float
    unit: str
    take_id: str | None = None


@dataclass
class TakeState:
    """One 15-second take slot on an episode.

    Parameters
    ----------
    take_id
        ``t1`` … ``t4``.
    lines
        Approved or draft lines. More than three fails preflight.
    board
        Board gate for this take.
    estimate_usd
        Priced batch for this take, if recorded.
    filmed_count
        How many times this take was filmed. A second film needs a cause.
    verdict
        Operator verdict after the last film.
    change_cause
        Direction cause required for ``change``.
    handoff_path
        Previous take's last frame, when this take has a predecessor.
    spend_usd
        Spend charged to this take.
    overrides
        Preflight overrides the operator confirmed.
    extra
        Fields another tool wrote on this take that this kit does not model;
        written back unchanged on save.
    """

    take_id: str
    lines: list[SpokenLine] = field(default_factory=list)
    board: GateRecord = field(default_factory=GateRecord)
    estimate_usd: float | None = None
    filmed_count: int = 0
    verdict: TakeVerdict = "pending"
    change_cause: str | None = None
    handoff_path: str | None = None
    spend_usd: float = 0.0
    overrides: list[PreflightOverride] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EpisodeState:
    """One episode slot on the series desk.

    Parameters
    ----------
    ordinal
        1-based episode number.
    slug
        Folder name, ``ep01``.
    script
        Line gate.
    post
        Mix and captions gate.
    takes
        Take slots for the series band.
    spend_usd
        Episode spend including this episode's units.
    extra
        Fields another tool wrote on this episode that this kit does not
        model; written back unchanged on save.
    """

    ordinal: int
    slug: str
    script: GateRecord = field(default_factory=GateRecord)
    post: GateRecord = field(default_factory=GateRecord)
    takes: list[TakeState] = field(default_factory=list)
    spend_usd: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SeriesState:
    """One series desk that can hold many episode slots.

    Parameters
    ----------
    schema_version
        ``fictora.content-ops.series.v1``.
    title
        Human series title.
    slug
        Filesystem slug.
    band
        Season-locked duration band.
    day
        Desk date ``YYYY-MM-DD``.
    continuing
        True when plates already exist from a prior desk.
    look
        Series look gate.
    plates
        Shared plate gate.
    spend_usd
        Series spend.
    episodes
        Episode slots.
    bed_path
        Chosen series music bed, if any.
    spend_log
        Every booked amount with its unit label, oldest first. ``spend_usd``
        stays the running total; desks from before the ledger have none.
    extra
        Top-level fields another tool wrote (the retired internal kit's
        ``bed_db``, ``api``, ``series_arc`` ...) that this kit
        does not model; written back unchanged on save, so a desk never loses
        them on the first write.
    """

    schema_version: str
    title: str
    slug: str
    band: Band
    day: str
    continuing: bool
    look: GateRecord
    plates: GateRecord
    spend_usd: float
    episodes: list[EpisodeState]
    bed_path: str | None = None
    spend_log: list[SpendEntry] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def parse_spoken_lines(payload: list[Any]) -> list[SpokenLine]:
    """Build spoken lines from a JSON list.

    Parameters
    ----------
    payload
        List of objects with ``speaker`` and ``original``.

    Returns
    -------
    list[SpokenLine]
        Parsed lines.

    Raises
    ------
    ValueError
        When an item is missing speaker or original text.
    """

    lines: list[SpokenLine] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each line must be an object")
        speaker = str(item.get("speaker") or "").strip()
        original = str(item.get("original") or item.get("text") or "").strip()
        if not speaker or not original:
            raise ValueError("each line needs speaker and original")
        lines.append(
            SpokenLine(
                speaker=speaker,
                original=original,
                translation=str(item.get("translation") or ""),
            )
        )
    return lines


def utc_now() -> str:
    """Return the current UTC timestamp.

    Returns
    -------
    str
        ISO-8601 UTC timestamp.
    """

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def take_ids_for_band(band: str, *, letterbox: bool = False) -> list[str]:
    """Return take ids for a duration band.

    Parameters
    ----------
    band
        ``15s``, ``30s``, or ``60s``.
    letterbox
        True for a desk started ``--delivery-format letterbox``: 10-second
        takes (``LETTERBOX_TAKES_FOR_BAND``). The spine has the last word
        (:func:`sync_take_slots_to_spine`): a server with real 4:3 takes off
        boards a letterbox show portrait.

    Returns
    -------
    list[str]
        ``t1`` … in band order.

    Raises
    ------
    ValueError
        When ``band`` is not a known duration band.
    """

    count = (LETTERBOX_TAKES_FOR_BAND if letterbox else TAKES_FOR_BAND).get(band)
    if count is None:
        raise ValueError(f"band must be 15s, 30s, or 60s; got {band!r}")
    return [f"t{index}" for index in range(1, count + 1)]


def envelope_usd(series: SeriesState, episode: EpisodeState) -> float:
    """Return the spend envelope for one episode.

    Parameters
    ----------
    series
        Series desk.
    episode
        Episode slot.

    Returns
    -------
    float
        First-episode envelope or the continuing envelope for the band.
    """

    if episode.ordinal == 1 and not series.continuing:
        return ENVELOPE_FIRST_USD
    return ENVELOPE_CONTINUING_USD[series.band]


def empty_gate() -> GateRecord:
    """Return a pending gate.

    Returns
    -------
    GateRecord
        Pending record with no path.
    """

    return GateRecord()


def new_episode(ordinal: int, band: str, *, letterbox: bool = False) -> EpisodeState:
    """Return a blank episode slot for a band.

    Parameters
    ----------
    ordinal
        1-based episode number.
    band
        Duration band.
    letterbox
        True for a letterbox desk (10-second takes, 7-second at 15 s; see :func:`take_ids_for_band`).

    Returns
    -------
    EpisodeState
        Episode with empty take slots.
    """

    return EpisodeState(
        ordinal=ordinal,
        slug=f"ep{ordinal:02d}",
        takes=[
            TakeState(take_id=take_id)
            for take_id in take_ids_for_band(band, letterbox=letterbox)
        ],
    )


def new_series(
    title: str,
    slug: str,
    band: Band,
    day: str,
    *,
    episode_count: int,
    continuing: bool = False,
    letterbox: bool = False,
) -> SeriesState:
    """Return a new series desk with ``episode_count`` slots.

    Parameters
    ----------
    title
        Human series title.
    slug
        Filesystem slug.
    band
        Season-locked band.
    day
        Desk date.
    episode_count
        How many episode slots to open.
    continuing
        True when this desk reuses an existing cast.
    letterbox
        True for a letterbox desk: 10-second take slots.

    Returns
    -------
    SeriesState
        Desk with pending series gates.

    Raises
    ------
    ValueError
        When ``episode_count`` is less than 1.
    """

    if episode_count < 1:
        raise ValueError("episode_count must be at least 1")
    return SeriesState(
        schema_version=SCHEMA_VERSION,
        title=title,
        slug=slug,
        band=band,
        day=day,
        continuing=continuing,
        look=empty_gate(),
        plates=empty_gate(),
        spend_usd=0.0,
        episodes=[
            new_episode(index, band, letterbox=letterbox)
            for index in range(1, episode_count + 1)
        ],
    )


def _untouched(take: TakeState) -> bool:
    """Whether a take slot holds nothing yet: no lines, board, estimate, film or spend."""

    return (
        not take.lines
        and take.board.status == "pending"
        and take.board.path is None
        and take.estimate_usd is None
        and take.filmed_count == 0
        and take.spend_usd == 0.0
        and not take.overrides
    )


def spine_take_count(spine: dict[str, Any]) -> int | None:
    """Return how many takes the server's story films per episode.

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` JSON (bare, or ``{"spine": ...}``).

    Returns
    -------
    int | None
        ``len(beats_per_storyboard_set)``; ``None`` when the spine has no pattern.
    """

    body = spine.get("spine", spine) if isinstance(spine, dict) else {}
    pattern = body.get("beats_per_storyboard_set") if isinstance(body, dict) else None
    if not isinstance(pattern, list) or not pattern:
        return None
    return len(pattern)


def spine_take_seconds(spine: dict[str, Any], band: str) -> int | None:
    """Return the seconds one take films on a 4:3 (letterbox) story, else ``None``.

    Parameters
    ----------
    spine
        Spine JSON.
    band
        The desk's band (``30s`` ...).

    Returns
    -------
    int | None
        The band's seconds over the spine's take count on a ``board_aspect``
        ``4:3`` story (10 for every new letterbox story); ``None`` on portrait,
        where the desk's own ``--clip-seconds`` stands.
    """

    body = spine.get("spine", spine) if isinstance(spine, dict) else {}
    count = spine_take_count(spine)
    if not isinstance(body, dict) or body.get("board_aspect") != "4:3" or not count:
        return None
    try:
        seconds = int(str(band).rstrip("s"))
    except ValueError:
        return None
    return seconds // count


def sync_take_slots_to_spine(series: SeriesState, spine: dict[str, Any]) -> list[str]:
    """Give every episode as many take slots as the server's story films.

    The desk opens its slots from the band (two at 30 s); a 4:3 letterbox story
    films 10-second takes, so the server's spine holds three (or six at 60 s; two 7-second takes at 15 s).
    The spine wins: missing slots are added, and trailing slots the story does
    not film are dropped while they hold nothing. A slot that already holds
    work is never dropped; it is named in the returned notes instead.

    Parameters
    ----------
    series
        Desk state, changed in place.
    spine
        Spine JSON.

    Returns
    -------
    list[str]
        One plain line per change or kept slot; empty when nothing changed.
    """

    count = spine_take_count(spine)
    if count is None:
        return []
    notes: list[str] = []
    for episode in series.episodes:
        have = len(episode.takes)
        if have < count:
            for index in range(have + 1, count + 1):
                episode.takes.append(TakeState(take_id=f"t{index}"))
            notes.append(
                f"{episode.slug}: the story films {count} takes; added t{have + 1}–t{count}."
            )
        elif have > count:
            extra = episode.takes[count:]
            if all(_untouched(take) for take in extra):
                episode.takes = episode.takes[:count]
                notes.append(
                    f"{episode.slug}: the story films {count} takes; dropped empty t{count + 1}–t{have}."
                )
            else:
                notes.append(
                    f"!! {episode.slug}: the story films {count} takes but the desk holds work on "
                    f"{', '.join(take.take_id for take in extra if not _untouched(take))}; left as is."
                )
    return notes


def series_path(desk: Path) -> Path:
    """Return the series.json path for a desk.

    Parameters
    ----------
    desk
        Series desk folder.

    Returns
    -------
    Path
        ``desk / series.json``.
    """

    return desk / SERIES_FILENAME


def load_series(desk: Path) -> SeriesState:
    """Load series state from a desk folder.

    Parameters
    ----------
    desk
        Series desk folder.

    Returns
    -------
    SeriesState
        Parsed desk.

    Raises
    ------
    FileNotFoundError
        When ``series.json`` is missing.
    ValueError
        When the file is not a valid v1 desk.
    """

    path = series_path(desk)
    if not path.is_file():
        raise FileNotFoundError(f"series desk not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported series desk schema in {path}")
    return _series_from_dict(raw)


def save_series(desk: Path, series: SeriesState) -> Path:
    """Write series.json. Overwrites the machine file only.

    Parameters
    ----------
    desk
        Series desk folder.
    series
        Current desk.

    Returns
    -------
    Path
        Path written.
    """

    path = series_path(desk)
    payload = series_to_dict(series)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def episode_by_ordinal(series: SeriesState, ordinal: int) -> EpisodeState:
    """Return one episode slot.

    Parameters
    ----------
    series
        Series desk.
    ordinal
        1-based episode number.

    Returns
    -------
    EpisodeState
        Matching slot.

    Raises
    ------
    ValueError
        When the episode does not exist.
    """

    for episode in series.episodes:
        if episode.ordinal == ordinal:
            return episode
    raise ValueError(f"episode {ordinal} is not on this desk")


def take_by_id(episode: EpisodeState, take_id: str) -> TakeState:
    """Return one take slot.

    Parameters
    ----------
    episode
        Episode slot.
    take_id
        ``t1`` … ``t4``.

    Returns
    -------
    TakeState
        Matching take.

    Raises
    ------
    ValueError
        When the take is not on this episode.
    """

    for take in episode.takes:
        if take.take_id == take_id:
            return take
    raise ValueError(f"take {take_id} is not on {episode.slug}")


def series_to_dict(series: SeriesState) -> dict[str, Any]:
    """Return the series.json payload, with every unmodelled field put back.

    Modelled fields win over an ``extra`` key of the same name, so a stale
    copy can never shadow what this kit just recorded.

    Parameters
    ----------
    series
        Current desk.

    Returns
    -------
    dict[str, Any]
        JSON-ready object: modelled fields first, then the carried ones.
    """

    payload = _fold_extra(asdict(series))
    payload["episodes"] = [_fold_extra(episode) for episode in payload["episodes"]]
    for episode in payload["episodes"]:
        episode["takes"] = [_fold_extra(take) for take in episode["takes"]]
    return payload


def _fold_extra(record: dict[str, Any]) -> dict[str, Any]:
    extra = record.pop("extra", None) or {}
    return {
        **record,
        **{key: value for key, value in extra.items() if key not in record},
    }


def _unmodelled(raw: dict[str, Any], cls: type) -> dict[str, Any]:
    known = {item.name for item in fields(cls)} - {"extra"}
    return {key: value for key, value in raw.items() if key not in known}


def _series_from_dict(raw: dict[str, Any]) -> SeriesState:
    """Build a SeriesState from JSON.

    Parameters
    ----------
    raw
        Decoded series.json object.

    Returns
    -------
    SeriesState
        Typed desk.
    """

    return SeriesState(
        schema_version=str(raw["schema_version"]),
        title=str(raw["title"]),
        slug=str(raw["slug"]),
        band=raw["band"],
        day=str(raw["day"]),
        continuing=bool(raw["continuing"]),
        look=_gate_from_dict(raw["look"]),
        plates=_gate_from_dict(raw["plates"]),
        spend_usd=float(raw["spend_usd"]),
        episodes=[_episode_from_dict(item) for item in raw["episodes"]],
        bed_path=raw.get("bed_path"),
        spend_log=[
            _spend_from_dict(item)
            for item in raw.get("spend_log") or []
            if isinstance(item, dict)
        ],
        extra=_unmodelled(raw, SeriesState),
    )


def _spend_from_dict(raw: dict[str, Any]) -> SpendEntry:
    return SpendEntry(
        at_utc=str(raw.get("at_utc") or ""),
        episode=int(raw.get("episode") or 0),
        usd=float(raw.get("usd") or 0.0),
        unit=str(raw.get("unit") or "unlabelled"),
        take_id=raw.get("take_id"),
    )


def _episode_from_dict(raw: dict[str, Any]) -> EpisodeState:
    return EpisodeState(
        ordinal=int(raw["ordinal"]),
        slug=str(raw["slug"]),
        script=_gate_from_dict(raw["script"]),
        post=_gate_from_dict(raw["post"]),
        takes=[_take_from_dict(item) for item in raw["takes"]],
        spend_usd=float(raw["spend_usd"]),
        extra=_unmodelled(raw, EpisodeState),
    )


def _take_from_dict(raw: dict[str, Any]) -> TakeState:
    return TakeState(
        take_id=str(raw["take_id"]),
        lines=[
            SpokenLine(
                speaker=str(line["speaker"]),
                original=str(line["original"]),
                translation=str(line.get("translation") or ""),
            )
            for line in raw.get("lines") or []
        ],
        board=_gate_from_dict(raw["board"]),
        estimate_usd=_optional_float(raw.get("estimate_usd")),
        filmed_count=int(raw.get("filmed_count") or 0),
        verdict=raw.get("verdict") or "pending",
        change_cause=raw.get("change_cause"),
        handoff_path=raw.get("handoff_path"),
        spend_usd=float(raw.get("spend_usd") or 0.0),
        overrides=[
            PreflightOverride(**item)
            for item in raw.get("overrides") or []
            if isinstance(item, dict)
        ],
        extra=_unmodelled(raw, TakeState),
    )


def unit_id(episode: EpisodeState, take: TakeState) -> str:
    """Return the id an operator types to confirm a unit: ``ep01-t1``.

    Parameters
    ----------
    episode
        Episode slot.
    take
        Take slot.

    Returns
    -------
    str
        ``<episode slug>-<take id>``.
    """

    return f"{episode.slug}-{take.take_id}"


def _gate_from_dict(raw: dict[str, Any] | None) -> GateRecord:
    if not raw:
        return empty_gate()
    return GateRecord(
        status=raw.get("status") or "pending",
        at_utc=raw.get("at_utc"),
        path=raw.get("path"),
        note=raw.get("note"),
        luma_percent=_optional_float(raw.get("luma_percent")),
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
