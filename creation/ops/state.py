"""Machine state for a content-ops series desk."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = "fictora.content-ops.series.v1"
SERIES_FILENAME = "series.json"
QUEUE_FILENAME = "QUEUE.md"

Band = Literal["15s", "30s", "60s"]
GateStatus = Literal["pending", "approved", "rejected"]
TakeVerdict = Literal["pending", "use", "change"]

TAKES_FOR_BAND: dict[str, int] = {"15s": 1, "30s": 2, "60s": 4}
MAX_LINES_PER_TAKE = 3
ENVELOPE_STOP_MULTIPLIER = 2.0
ENVELOPE_FIRST_USD = 4.50
ENVELOPE_CONTINUING_USD: dict[str, float] = {"15s": 2.50, "30s": 4.00, "60s": 8.00}

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
    """

    ordinal: int
    slug: str
    script: GateRecord = field(default_factory=GateRecord)
    post: GateRecord = field(default_factory=GateRecord)
    takes: list[TakeState] = field(default_factory=list)
    spend_usd: float = 0.0


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


def take_ids_for_band(band: str) -> list[str]:
    """Return take ids for a duration band.

    Parameters
    ----------
    band
        ``15s``, ``30s``, or ``60s``.

    Returns
    -------
    list[str]
        ``t1`` … in band order.

    Raises
    ------
    ValueError
        When ``band`` is not a known duration band.
    """

    count = TAKES_FOR_BAND.get(band)
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


def new_episode(ordinal: int, band: str) -> EpisodeState:
    """Return a blank episode slot for a band.

    Parameters
    ----------
    ordinal
        1-based episode number.
    band
        Duration band.

    Returns
    -------
    EpisodeState
        Episode with empty take slots.
    """

    return EpisodeState(
        ordinal=ordinal,
        slug=f"ep{ordinal:02d}",
        takes=[TakeState(take_id=take_id) for take_id in take_ids_for_band(band)],
    )


def new_series(
    title: str,
    slug: str,
    band: Band,
    day: str,
    *,
    episode_count: int,
    continuing: bool = False,
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
        episodes=[new_episode(index, band) for index in range(1, episode_count + 1)],
    )


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
    payload = asdict(series)
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
    )


def _episode_from_dict(raw: dict[str, Any]) -> EpisodeState:
    return EpisodeState(
        ordinal=int(raw["ordinal"]),
        slug=str(raw["slug"]),
        script=_gate_from_dict(raw["script"]),
        post=_gate_from_dict(raw["post"]),
        takes=[_take_from_dict(item) for item in raw["takes"]],
        spend_usd=float(raw["spend_usd"]),
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
    )


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
