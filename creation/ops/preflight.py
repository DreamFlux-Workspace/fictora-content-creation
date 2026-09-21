"""Hard preflight before a take may be enrolled."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from creation.ops.luma import INTERIOR_DIM_BELOW_PERCENT, measure_board_luma
from creation.ops.state import (
    ENVELOPE_STOP_MULTIPLIER,
    MAX_LINES_PER_TAKE,
    EpisodeState,
    SeriesState,
    TakeState,
    envelope_usd,
    take_by_id,
)


@dataclass(frozen=True)
class PreflightCheck:
    """One preflight rule.

    Parameters
    ----------
    code
        Stable id for the rule.
    ok
        True when the rule passed.
    detail
        Operator-facing reason.
    """

    code: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class PreflightReport:
    """Preflight result for one take.

    Parameters
    ----------
    episode
        Episode ordinal.
    take_id
        Take id.
    passed
        True when every check passed.
    checks
        Rule results in evaluation order.
    """

    episode: int
    take_id: str
    passed: bool
    checks: tuple[PreflightCheck, ...]

    def failed(self) -> list[PreflightCheck]:
        """Return the failing checks.

        Returns
        -------
        list[PreflightCheck]
            Checks where ``ok`` is false.
        """

        return [check for check in self.checks if not check.ok]

    def one_line(self) -> str:
        """Return a single status line.

        Returns
        -------
        str
            ``PASS`` or ``FAIL`` plus the first failure.
        """

        if self.passed:
            return f"PASS  ep{self.episode:02d} {self.take_id}"
        first = self.failed()[0]
        return f"FAIL  ep{self.episode:02d} {self.take_id}  {first.code}: {first.detail}"


def evaluate_preflight(
    series: SeriesState,
    episode: EpisodeState,
    take: TakeState,
    *,
    desk: Path,
) -> PreflightReport:
    """Evaluate runbook rules that must pass before a take is enrolled.

    Parameters
    ----------
    series
        Series desk.
    episode
        Episode slot.
    take
        Take to film.
    desk
        Desk path, used to resolve board and hand-off files.

    Returns
    -------
    PreflightReport
        Pass or fail with every check recorded.
    """

    checks = [
        _approved("look_approved", series.look.status, "Look is not approved."),
        _approved("plates_approved", series.plates.status, "Shared plates are not approved."),
        _approved("script_approved", episode.script.status, "Lines are not approved."),
        _line_count(take),
        _approved("board_approved", take.board.status, f"{take.take_id} board is not approved."),
        _board_luma(episode, take, desk),
        _estimate(take),
        _envelope(series, episode, take),
        _handoff(episode, take, desk),
        _reroll(take),
    ]
    return PreflightReport(
        episode=episode.ordinal,
        take_id=take.take_id,
        passed=all(check.ok for check in checks),
        checks=tuple(checks),
    )


def evaluate_preflight_ids(
    series: SeriesState,
    *,
    desk: Path,
    episode: int,
    take_id: str,
) -> PreflightReport:
    """Evaluate preflight by episode ordinal and take id.

    Parameters
    ----------
    series
        Series desk.
    desk
        Desk path.
    episode
        Episode ordinal.
    take_id
        Take id.

    Returns
    -------
    PreflightReport
        Pass or fail with every check recorded.
    """

    from creation.ops.state import episode_by_ordinal

    slot = episode_by_ordinal(series, episode)
    return evaluate_preflight(series, slot, take_by_id(slot, take_id), desk=desk)


def _approved(code: str, status: str, detail: str) -> PreflightCheck:
    return PreflightCheck(code=code, ok=status == "approved", detail=detail if status != "approved" else "ok")


def _line_count(take: TakeState) -> PreflightCheck:
    count = len(take.lines)
    if count > MAX_LINES_PER_TAKE:
        return PreflightCheck(
            code="line_count",
            ok=False,
            detail=f"{take.take_id} has {count} lines; maximum is {MAX_LINES_PER_TAKE}.",
        )
    return PreflightCheck(code="line_count", ok=True, detail=f"{count} lines")


def _board_luma(episode: EpisodeState, take: TakeState, desk: Path) -> PreflightCheck:
    if take.board.status != "approved" or not take.board.path:
        return PreflightCheck(code="board_luma", ok=False, detail="Approved board path is missing.")
    path = _resolve(desk, episode.slug, take.board.path)
    if not path.is_file():
        return PreflightCheck(code="board_luma", ok=False, detail=f"Board file missing: {path}")
    report = measure_board_luma(path)
    if report.below_dim_floor:
        return PreflightCheck(
            code="board_luma",
            ok=False,
            detail=(f"{report.one_line()}. Interiors at or below {INTERIOR_DIM_BELOW_PERCENT:.0f}% render dim."),
        )
    return PreflightCheck(code="board_luma", ok=True, detail=report.one_line())


def _estimate(take: TakeState) -> PreflightCheck:
    if take.estimate_usd is None:
        return PreflightCheck(code="estimate", ok=False, detail="Cost is not on the table.")
    return PreflightCheck(code="estimate", ok=True, detail=f"${take.estimate_usd:.2f}")


def _envelope(series: SeriesState, episode: EpisodeState, take: TakeState) -> PreflightCheck:
    envelope = envelope_usd(series, episode)
    projected = episode.spend_usd + (take.estimate_usd or 0.0)
    ceiling = envelope * ENVELOPE_STOP_MULTIPLIER
    if projected > ceiling:
        return PreflightCheck(
            code="envelope",
            ok=False,
            detail=f"${projected:.2f} of ${envelope:.2f} envelope (stop at {ceiling:.2f}). Escalate.",
        )
    return PreflightCheck(
        code="envelope",
        ok=True,
        detail=f"${projected:.2f} of ${envelope:.2f} envelope",
    )


def _handoff(episode: EpisodeState, take: TakeState, desk: Path) -> PreflightCheck:
    needs = take.take_id != "t1" or episode.ordinal > 1
    if not needs:
        return PreflightCheck(code="handoff", ok=True, detail="first take of episode 1")
    if not take.handoff_path:
        return PreflightCheck(
            code="handoff",
            ok=False,
            detail="Hand-off frame is not set. Paste the previous last frame.",
        )
    path = _resolve(desk, episode.slug, take.handoff_path)
    if not path.is_file():
        return PreflightCheck(code="handoff", ok=False, detail=f"Hand-off file missing: {path}")
    return PreflightCheck(code="handoff", ok=True, detail=str(path))


def _reroll(take: TakeState) -> PreflightCheck:
    if take.filmed_count == 0:
        return PreflightCheck(code="reroll", ok=True, detail="not yet filmed")
    if take.verdict != "change":
        return PreflightCheck(
            code="reroll",
            ok=False,
            detail="This take is already filmed. Record a Change-this cause first.",
        )
    if not (take.change_cause or "").strip():
        return PreflightCheck(
            code="reroll",
            ok=False,
            detail="A second render needs a written cause in the direction.",
        )
    return PreflightCheck(code="reroll", ok=True, detail=take.change_cause or "ok")


def _resolve(desk: Path, episode_slug: str, stored: str) -> Path:
    path = Path(stored)
    if path.is_absolute():
        return path
    episode_relative = desk / episode_slug / path
    if episode_relative.exists():
        return episode_relative
    return desk / path
