"""Series desk: parallel episode slots, review queue, and recorded gates."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

from creation.ops.folder import (
    DEFAULT_RUN_PARENT,
    init_named_run_folder,
    run_folder_name,
    slugify_series,
)
from creation.ops.luma import measure_board_luma
from creation.ops.preflight import PreflightReport, evaluate_preflight
from creation.ops.state import (
    MAX_LINES_PER_TAKE,
    QUEUE_FILENAME,
    Band,
    EpisodeState,
    GateRecord,
    SeriesState,
    SpokenLine,
    TakeState,
    envelope_usd,
    episode_by_ordinal,
    load_series,
    new_episode,
    new_series,
    save_series,
    take_by_id,
    utc_now,
)

SHARED_SUBDIRS: tuple[str, ...] = (
    "shared/look",
    "shared/plates",
    "shared/reference",
    "shared/voices",
    "shared/beds",
)
"""Series-locked assets reused by every episode on the desk."""


def init_series_desk(
    parent: Path,
    title: str,
    *,
    band: Band,
    episode_count: int,
    day: date | None = None,
    continuing: bool = False,
    templates_dir: Path | None = None,
) -> Path:
    """Create a series desk with parallel episode slots.

    Parameters
    ----------
    parent
        Directory that will hold the dated desk.
    title
        Series title.
    band
        Season-locked duration band.
    episode_count
        How many episode folders to open now.
    day
        Desk date. Defaults to today.
    continuing
        True when plates already exist from a prior series desk.
    templates_dir
        Optional template override.

    Returns
    -------
    Path
        Created desk folder.

    Raises
    ------
    FileExistsError
        When the dated desk already exists.
    ValueError
        When ``band`` or ``episode_count`` is invalid.
    """

    stamp = day or date.today()
    desk = parent / run_folder_name(title, stamp)
    if desk.exists():
        raise FileExistsError(f"series desk already exists: {desk}")
    desk.mkdir(parents=True)
    for relative in SHARED_SUBDIRS:
        (desk / relative).mkdir(parents=True)
    series = new_series(
        title.strip(),
        slugify_series(title),
        band,
        stamp.isoformat(),
        episode_count=episode_count,
        continuing=continuing,
    )
    for episode in series.episodes:
        _create_episode_folder(desk, title, episode, stamp=stamp, templates_dir=templates_dir)
    save_series(desk, series)
    write_queue(desk, series)
    return desk


def add_episode(desk: Path, *, templates_dir: Path | None = None) -> EpisodeState:
    """Open one more episode slot on an existing desk.

    Parameters
    ----------
    desk
        Series desk.
    templates_dir
        Optional template override.

    Returns
    -------
    EpisodeState
        New episode slot.
    """

    series = load_series(desk)
    episode = new_episode(len(series.episodes) + 1, series.band)
    series.episodes.append(episode)
    stamp = date.fromisoformat(series.day)
    _create_episode_folder(desk, series.title, episode, stamp=stamp, templates_dir=templates_dir)
    save_series(desk, series)
    write_queue(desk, series)
    return episode


def set_take_lines(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    lines: Iterable[SpokenLine],
) -> TakeState:
    """Replace the lines on one take. Does not approve them.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        Take id.
    lines
        New line list.

    Returns
    -------
    TakeState
        Updated take.
    """

    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    take = take_by_id(slot, take_id)
    take.lines = list(lines)
    slot.script.status = "pending"
    slot.script.at_utc = None
    save_series(desk, series)
    write_queue(desk, series)
    return take


def approve_series_gate(desk: Path, gate: str, *, path: str | None = None, note: str | None = None) -> GateRecord:
    """Record a human yes on a series-level gate.

    Parameters
    ----------
    desk
        Series desk.
    gate
        ``look`` or ``plates``.
    path
        File the human opened.
    note
        Optional note.

    Returns
    -------
    GateRecord
        Approved record.

    Raises
    ------
    ValueError
        When ``gate`` is not a series gate.
    """

    if gate not in {"look", "plates"}:
        raise ValueError(f"series gate must be look or plates; got {gate!r}")
    series = load_series(desk)
    record = GateRecord(status="approved", at_utc=utc_now(), path=path, note=note)
    setattr(series, gate, record)
    save_series(desk, series)
    write_queue(desk, series)
    return record


def approve_script(desk: Path, *, episode: int) -> GateRecord:
    """Record a human yes on the episode lines.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.

    Returns
    -------
    GateRecord
        Approved script gate.

    Raises
    ------
    ValueError
        When any take on the episode has more than three lines.
    """

    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    for take in slot.takes:
        if len(take.lines) > MAX_LINES_PER_TAKE:
            raise ValueError(
                f"{slot.slug} {take.take_id} has {len(take.lines)} lines; maximum is {MAX_LINES_PER_TAKE}."
            )
    slot.script = GateRecord(status="approved", at_utc=utc_now(), note="lines")
    save_series(desk, series)
    write_queue(desk, series)
    return slot.script


def approve_board(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    image: Path,
) -> GateRecord:
    """Record a human yes on a board after measuring luma.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        Take id.
    image
        Board image the human opened.

    Returns
    -------
    GateRecord
        Approved board gate with luma.

    Raises
    ------
    FileNotFoundError
        When ``image`` is missing.
    ValueError
        When the image is unreadable.
    """

    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    take = take_by_id(slot, take_id)
    report = measure_board_luma(image)
    stored = _store_path(desk, image)
    take.board = GateRecord(
        status="approved",
        at_utc=utc_now(),
        path=stored,
        note=report.one_line(),
        luma_percent=round(report.mean_percent, 2),
    )
    save_series(desk, series)
    write_queue(desk, series)
    return take.board


def approve_post(desk: Path, *, episode: int, path: str | None = None) -> GateRecord:
    """Record a human yes on the mixed and captioned episode.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    path
        Final file the human watched.

    Returns
    -------
    GateRecord
        Approved post gate.
    """

    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    slot.post = GateRecord(status="approved", at_utc=utc_now(), path=path)
    save_series(desk, series)
    write_queue(desk, series)
    return slot.post


def record_estimate(desk: Path, *, episode: int, take_id: str, usd: float) -> TakeState:
    """Record the priced batch before take enrol.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        Take id.
    usd
        Estimate in US dollars.

    Returns
    -------
    TakeState
        Updated take.

    Raises
    ------
    ValueError
        When ``usd`` is negative.
    """

    if usd < 0:
        raise ValueError("estimate must be zero or positive")
    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    take = take_by_id(slot, take_id)
    take.estimate_usd = float(usd)
    save_series(desk, series)
    write_queue(desk, series)
    return take


def record_spend(desk: Path, *, episode: int, usd: float, take_id: str | None = None) -> SeriesState:
    """Add a paid unit to the ledger.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    usd
        Amount spent.
    take_id
        Optional take to attribute.

    Returns
    -------
    SeriesState
        Updated desk.

    Raises
    ------
    ValueError
        When ``usd`` is negative.
    """

    if usd < 0:
        raise ValueError("spend must be zero or positive")
    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    slot.spend_usd += usd
    series.spend_usd += usd
    if take_id:
        take_by_id(slot, take_id).spend_usd += usd
    save_series(desk, series)
    write_queue(desk, series)
    return series


def set_handoff(desk: Path, *, episode: int, take_id: str, image: Path) -> TakeState:
    """Pin the previous take's last frame on this take.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        Take that opens on this frame.
    image
        Hand-off image.

    Returns
    -------
    TakeState
        Updated take.

    Raises
    ------
    FileNotFoundError
        When ``image`` is missing.
    """

    if not image.is_file():
        raise FileNotFoundError(f"hand-off frame not found: {image}")
    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    take = take_by_id(slot, take_id)
    take.handoff_path = _store_path(desk, image)
    save_series(desk, series)
    write_queue(desk, series)
    return take


def record_filmed(desk: Path, *, episode: int, take_id: str) -> TakeState:
    """Mark that one take film completed. Does not record a verdict.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        Take id.

    Returns
    -------
    TakeState
        Updated take.
    """

    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    take = take_by_id(slot, take_id)
    take.filmed_count += 1
    take.verdict = "pending"
    save_series(desk, series)
    write_queue(desk, series)
    return take


def record_verdict(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    verdict: str,
    cause: str | None = None,
) -> TakeState:
    """Record Use it or Change this.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        Take id.
    verdict
        ``use`` or ``change``.
    cause
        Required when ``verdict`` is ``change``.

    Returns
    -------
    TakeState
        Updated take.

    Raises
    ------
    ValueError
        When the verdict is invalid or ``change`` has no cause.
    """

    if verdict not in {"use", "change"}:
        raise ValueError("verdict must be use or change")
    if verdict == "change" and not (cause or "").strip():
        raise ValueError("Change this needs a written cause in the direction.")
    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    take = take_by_id(slot, take_id)
    if take.filmed_count < 1:
        raise ValueError("Record the film before the verdict.")
    take.verdict = verdict  # type: ignore[assignment]
    take.change_cause = cause.strip() if verdict == "change" and cause else None
    save_series(desk, series)
    write_queue(desk, series)
    return take


def preflight_take(desk: Path, *, episode: int, take_id: str) -> PreflightReport:
    """Run take preflight for one slot.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        Take id.

    Returns
    -------
    PreflightReport
        Pass or fail.
    """

    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    return evaluate_preflight(series, slot, take_by_id(slot, take_id), desk=desk)


def waiting_on(series: SeriesState, episode: EpisodeState, *, desk: Path) -> str:
    """Return the next human or fix action for one episode.

    Parameters
    ----------
    series
        Series desk.
    episode
        Episode slot.
    desk
        Desk path for preflight.

    Returns
    -------
    str
        Queue token such as ``board:t1`` or ``done``.
    """

    if series.look.status != "approved":
        return "look"
    if series.plates.status != "approved":
        return "plates"
    if episode.script.status != "approved":
        return "script"
    for take in episode.takes:
        if take.verdict == "use":
            continue
        if take.board.status != "approved":
            return f"board:{take.take_id}"
        if take.estimate_usd is None:
            return f"estimate:{take.take_id}"
        report = evaluate_preflight(series, episode, take, desk=desk)
        if not report.passed:
            return f"fix:{report.failed()[0].code}"
        if take.filmed_count == 0 or take.verdict == "change":
            return f"cost-yes:{take.take_id}"
        return f"take-read:{take.take_id}"
    if episode.post.status != "approved":
        return "post"
    return "done"


def status_rows(desk: Path) -> list[dict[str, str]]:
    """Return one status row per episode.

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    list[dict[str, str]]
        Queue rows for humans and agents.
    """

    series = load_series(desk)
    rows: list[dict[str, str]] = []
    for episode in series.episodes:
        waiting = waiting_on(series, episode, desk=desk)
        first_open = next((take.take_id for take in episode.takes if take.verdict != "use"), episode.takes[-1].take_id)
        report = evaluate_preflight(series, episode, take_by_id(episode, first_open), desk=desk)
        rows.append(
            {
                "episode": episode.slug,
                "waiting": waiting,
                "preflight": "PASS" if report.passed else "FAIL",
                "spend": f"${episode.spend_usd:.2f}",
                "envelope": f"${envelope_usd(series, episode):.2f}",
            }
        )
    return rows


def write_queue(desk: Path, series: SeriesState | None = None) -> Path:
    """Write QUEUE.md so a human can review every waiting gate.

    Parameters
    ----------
    desk
        Series desk.
    series
        Optional already-loaded desk.

    Returns
    -------
    Path
        Queue file.
    """

    current = series or load_series(desk)
    rows = status_rows(desk)
    lines = [
        f"# {current.title} — review queue",
        "",
        f"Band: {current.band}. Series spend: ${current.spend_usd:.2f}.",
        "Open the path. Say yes or say what is wrong. Silence is not consent.",
        "",
        "| Episode | Waiting on | Preflight | Spend | Envelope |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['episode']} | {row['waiting']} | {row['preflight']} | {row['spend']} | {row['envelope']} |"
        )
    waiting = [row for row in rows if row["waiting"] != "done"]
    lines.extend(["", f"Open gates: {len(waiting)}.", ""])
    path = desk / QUEUE_FILENAME
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def default_desk_parent() -> Path:
    """Return the default parent for series desks.

    Returns
    -------
    Path
        ``~/Downloads/documents``.
    """

    return DEFAULT_RUN_PARENT


def _create_episode_folder(
    desk: Path,
    title: str,
    episode: EpisodeState,
    *,
    stamp: date,
    templates_dir: Path | None,
) -> Path:
    """Create one episode review folder on the desk.

    Parameters
    ----------
    desk
        Series desk.
    title
        Series title.
    episode
        Episode slot.
    stamp
        Template date.
    templates_dir
        Optional templates.

    Returns
    -------
    Path
        Episode folder.
    """

    return init_named_run_folder(
        desk / episode.slug,
        title,
        day=stamp,
        templates_dir=templates_dir,
        extra_substitutions={"{{episode}}": str(episode.ordinal)},
    )


def _store_path(desk: Path, image: Path | str) -> str:
    """Return a path stored relative to the desk when possible.

    Parameters
    ----------
    desk
        Series desk.
    image
        File on disk.

    Returns
    -------
    str
        Relative path from the desk, or the absolute path.
    """

    resolved = Path(image).expanduser().resolve()
    try:
        return str(resolved.relative_to(desk.resolve()))
    except ValueError:
        return str(resolved)
