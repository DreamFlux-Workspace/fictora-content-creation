"""Preflight before a take may be enrolled: human gates block, quality checks warn loudly.

Two kinds of check:

- **Gates** (look, plates, script, board approved). A human says yes before
  money moves. These block; nothing overrides them.
- **Warnings** (line count, board luma, estimate, envelope, hand-off,
  re-roll cause). These never hard-block. They print a loud warning that says
  what failed, how many times this unit has been filmed, and the money spent on
  the unit and the episode so far. The operator continues only by an explicit
  confirmation naming the unit (``--proceed-anyway ep01-t1``), and the override
  is written into ``series.json``. An override covers the next film only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from creation.ops.luma import measure_board_luma
from creation.ops.state import (
    ENVELOPE_STOP_MULTIPLIER,
    MAX_LINES_PER_TAKE,
    EpisodeState,
    PreflightOverride,
    SeriesState,
    TakeState,
    envelope_usd,
    take_by_id,
    unit_id,
)

#: Checks that warn loudly instead of blocking. Everything else is a human gate and blocks.
WARNING_CODES = frozenset(
    {"line_count", "board_luma", "estimate", "envelope", "handoff", "reroll"}
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

    @property
    def blocking(self) -> bool:
        """True for a human gate. Warnings never block."""

        return self.code not in WARNING_CODES


@dataclass(frozen=True)
class PreflightReport:
    """Preflight result for one take.

    Parameters
    ----------
    episode
        Episode ordinal.
    take_id
        Take id.
    unit
        Unit id the operator types to confirm (``ep01-t1``).
    checks
        Rule results in evaluation order.
    films
        How many times this take has been filmed already.
    unit_spend_usd
        Money spent on this take so far.
    episode_spend_usd
        Money spent on the episode so far.
    envelope
        The episode envelope.
    override
        The recorded override that covers the open warnings, if any.
    """

    episode: int
    take_id: str
    unit: str
    checks: tuple[PreflightCheck, ...]
    films: int
    unit_spend_usd: float
    episode_spend_usd: float
    envelope: float
    override: PreflightOverride | None = None

    @property
    def passed(self) -> bool:
        """True when every check passed, with no warnings."""

        return all(check.ok for check in self.checks)

    @property
    def blocked(self) -> bool:
        """True when a human gate is still open. Nothing overrides this."""

        return any(check.blocking and not check.ok for check in self.checks)

    @property
    def confirmed(self) -> bool:
        """True when the open warnings are covered by an explicit override on record."""

        return self.override is not None

    @property
    def cleared(self) -> bool:
        """True when the take may be enrolled: no gate open, and warnings absent or confirmed."""

        return not self.blocked and (not self.warnings() or self.confirmed)

    def failed(self) -> list[PreflightCheck]:
        """Return the failing checks.

        Returns
        -------
        list[PreflightCheck]
            Checks where ``ok`` is false.
        """

        return [check for check in self.checks if not check.ok]

    def warnings(self) -> list[PreflightCheck]:
        """Return failing checks that warn instead of block.

        Returns
        -------
        list[PreflightCheck]
            Failing warning checks.
        """

        return [check for check in self.failed() if not check.blocking]

    def status(self) -> str:
        """Return one status word.

        Returns
        -------
        str
            ``PASS``, ``BLOCK``, ``WARN`` or ``OVERRIDDEN``.
        """

        if self.blocked:
            return "BLOCK"
        if self.passed:
            return "PASS"
        return "OVERRIDDEN" if self.confirmed else "WARN"

    def one_line(self) -> str:
        """Return a single status line.

        Returns
        -------
        str
            Status word plus the first open problem.
        """

        head = f"{self.status():10s} ep{self.episode:02d} {self.take_id}"
        if self.passed:
            return head
        first = next((c for c in self.failed() if c.blocking), None) or self.failed()[0]
        return f"{head}  {first.code}: {first.detail}"

    def loud_warning(self) -> str:
        """Return the banner the operator must read before confirming.

        Returns
        -------
        str
            What failed, films so far, money spent on the unit and the episode,
            and the exact confirmation to continue. Empty when nothing warns.
        """

        warnings = self.warnings()
        if not warnings:
            return ""
        bar = "!" * 72
        lines = [
            bar,
            f"!! PREFLIGHT WARNING — {self.unit} — this is not blocked, but read it",
            bar,
        ]
        lines += [f"!! {check.code}: {check.detail}" for check in warnings]
        retakes = max(0, self.films)
        lines += [
            f"!! Films of this unit so far: {self.films} ({retakes} re-take(s) if you film again).",
            f"!! Spent on this unit: ${self.unit_spend_usd:.2f}. "
            f"Spent on the episode: ${self.episode_spend_usd:.2f} of a ${self.envelope:.2f} envelope.",
        ]
        if self.confirmed and self.override is not None:
            lines.append(
                f"!! Overridden by the operator at {self.override.at_utc}. Covers the next film only."
            )
        else:
            lines.append(
                f"!! To film anyway: fictora-ops preflight ... --proceed-anyway {self.unit}  (logged in series.json)"
            )
        lines.append(bar)
        return "\n".join(lines)


def evaluate_preflight(
    series: SeriesState,
    episode: EpisodeState,
    take: TakeState,
    *,
    desk: Path,
) -> PreflightReport:
    """Evaluate the runbook rules before a take is enrolled.

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
        Every check, the unit's film count and spend, and any override on record.
    """

    checks = (
        _approved("look_approved", series.look.status, "Look is not approved."),
        _approved(
            "plates_approved", series.plates.status, "Shared plates are not approved."
        ),
        _approved("script_approved", episode.script.status, "Lines are not approved."),
        _line_count(take),
        _approved(
            "board_approved",
            take.board.status,
            f"{take.take_id} board is not approved.",
        ),
        _board_luma(episode, take, desk),
        _estimate(take),
        _envelope(series, episode, take),
        _handoff(episode, take, desk),
        _reroll(take),
    )
    open_codes = {check.code for check in checks if not check.ok and not check.blocking}
    return PreflightReport(
        episode=episode.ordinal,
        take_id=take.take_id,
        unit=unit_id(episode, take),
        checks=checks,
        films=take.filmed_count,
        unit_spend_usd=take.spend_usd,
        episode_spend_usd=episode.spend_usd,
        envelope=envelope_usd(series, episode),
        override=covering_override(take, open_codes),
    )


def covering_override(
    take: TakeState, open_codes: set[str]
) -> PreflightOverride | None:
    """Return the override that covers these warnings on the next film, if one is recorded.

    Parameters
    ----------
    take
        Take slot.
    open_codes
        Warning codes open now.

    Returns
    -------
    PreflightOverride | None
        The latest override made at the current film count whose codes include every open code.
    """

    if not open_codes:
        return None
    for record in reversed(take.overrides):
        if record.filmed_count == take.filmed_count and open_codes <= set(record.codes):
            return record
    return None


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
        Every check with the unit's film count and spend.
    """

    from creation.ops.state import episode_by_ordinal

    slot = episode_by_ordinal(series, episode)
    return evaluate_preflight(series, slot, take_by_id(slot, take_id), desk=desk)


def _approved(code: str, status: str, detail: str) -> PreflightCheck:
    return PreflightCheck(
        code=code,
        ok=status == "approved",
        detail=detail if status != "approved" else "ok",
    )


def _line_count(take: TakeState) -> PreflightCheck:
    count = len(take.lines)
    if count > MAX_LINES_PER_TAKE:
        return PreflightCheck(
            code="line_count",
            ok=False,
            detail=f"{take.take_id} has {count} lines; maximum is {MAX_LINES_PER_TAKE}. A four-line take drops a line.",
        )
    return PreflightCheck(code="line_count", ok=True, detail=f"{count} lines")


def _board_luma(episode: EpisodeState, take: TakeState, desk: Path) -> PreflightCheck:
    if take.board.status != "approved" or not take.board.path:
        return PreflightCheck(
            code="board_luma",
            ok=False,
            detail="Approved board path is missing; brightness unmeasured.",
        )
    path = _resolve(desk, episode.slug, take.board.path)
    if not path.is_file():
        return PreflightCheck(
            code="board_luma", ok=False, detail=f"Board file missing: {path}"
        )
    # Brightness is reported, never a warning: the human already looked at the board.
    return PreflightCheck(
        code="board_luma", ok=True, detail=measure_board_luma(path).one_line()
    )


def _estimate(take: TakeState) -> PreflightCheck:
    if take.estimate_usd is None:
        return PreflightCheck(
            code="estimate",
            ok=False,
            detail="Cost is not on the table (no batches/estimate).",
        )
    return PreflightCheck(code="estimate", ok=True, detail=f"${take.estimate_usd:.2f}")


def _envelope(
    series: SeriesState, episode: EpisodeState, take: TakeState
) -> PreflightCheck:
    envelope = envelope_usd(series, episode)
    projected = episode.spend_usd + (take.estimate_usd or 0.0)
    ceiling = envelope * ENVELOPE_STOP_MULTIPLIER
    if projected > ceiling:
        return PreflightCheck(
            code="envelope",
            ok=False,
            detail=f"${projected:.2f} of ${envelope:.2f} envelope, past 2x (${ceiling:.2f}). Escalate.",
        )
    return PreflightCheck(
        code="envelope", ok=True, detail=f"${projected:.2f} of ${envelope:.2f} envelope"
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
        return PreflightCheck(
            code="handoff", ok=False, detail=f"Hand-off file missing: {path}"
        )
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
