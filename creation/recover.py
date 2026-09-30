"""Recover from stuck or failed video generation jobs."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creation.harness.credentials import load_drama_api_credentials
from creation.harness.session import DramaApiRunSession
from creation.production_state import load_production


#: How long ``cancel-job`` waits before reading a job again when the cancel did not stop it at once.
CANCEL_RECHECK_SECONDS = 15.0

_STOPPED = frozenset({"completed", "failed", "cancelled"})


def _job_status(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    job = payload.get("job")
    holder = job if isinstance(job, Mapping) else payload
    status = holder.get("status")
    return str(status) if status is not None else None


def cancel_and_check(
    run: Any,
    job_id: str,
    *,
    wait_seconds: float = CANCEL_RECHECK_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], list[str]]:
    """POST cancel for one job and say plainly whether it has stopped.

    When the answer says ``terminal: false`` the cancel was only requested: the
    operator is told so (server sub-steps may keep running; a server fix is in
    progress), and the job is read once more after ``wait_seconds``. Nothing is
    retried or re-enrolled.

    Parameters
    ----------
    run
        Session with ``post`` and ``get_optional``.
    job_id
        The job to cancel.
    wait_seconds
        The wait before the one follow-up read.
    sleep
        Sleep function (a fake in tests).

    Returns
    -------
    tuple[dict[str, Any], list[str]]
        The cancel response, and the lines to show the operator.
    """

    payload = run.post(
        f"/v1/jobs/{job_id}/cancel",
        {},
        idempotency_key=f"{run.prefix}-cancel-{job_id}",
    )
    after = [
        "Do not enrol another take.",
        "A new take starts another ffmpeg job on Railway.",
    ]
    if payload.get("terminal") is not False:
        status = _job_status(payload)
        head = f"Cancelled (job status: {status})." if status else "Cancelled."
        return payload, [head, *after]
    status = _job_status(payload) or "unknown"
    lines = [
        f"Cancel requested, but job {job_id} has not stopped yet (status: {status}).",
        "Steps already started on the server may keep running for a while (a server fix is in progress).",
        f"Reading the job again in {wait_seconds:.0f}s ...",
    ]
    sleep(wait_seconds)
    code, body = run.get_optional(f"/v1/jobs/{job_id}")
    now = _job_status(body) if 200 <= code < 300 else None
    if now is None:
        lines.append(
            f"Could not read the job (HTTP {code}). Check it with `fictora-produce status`."
        )
    elif now in _STOPPED:
        lines.append(f"The job has stopped (status: {now}).")
    else:
        lines.append(
            f"The job is still {now}. Check again with `fictora-produce status`; the cancel stands, "
            "do not send it again or enrol another take while it runs."
        )
    return payload, [*lines, *after]


def cancel_video_job(desk: Path, job_id: str) -> tuple[dict[str, Any], list[str]]:
    """POST cancel for one coordinator or video job id (see :func:`cancel_and_check`).

    Parameters
    ----------
    desk
        Series desk with ``production.json``.
    job_id
        Job id from ``GET /v1/video-generations/{id}`` or terminal artefact.

    Returns
    -------
    tuple[dict[str, Any], list[str]]
        Cancel response JSON, and the lines to show the operator.
    """

    state = load_production(desk)
    base, token = load_drama_api_credentials(Path(__file__).resolve().parents[1])
    run = DramaApiRunSession(
        base_url=base,
        token=token,
        out_dir=desk / f"ep{state.episode_ordinal:02d}" / "api",
        session_id=state.session_id,
    )
    try:
        return cancel_and_check(run, job_id)
    finally:
        run.client.close()


def prepare_video_retry(desk: Path, *, job_id: str | None = None) -> str:
    """Mark desk ready for one new video enrol with a fresh idempotency suffix.

    A second call fails. Each enrol starts ffmpeg on Railway. Cancel does not
    call this.

    Parameters
    ----------
    desk
        Series desk path.
    job_id
        Optional stuck job id to record on state.

    Returns
    -------
    str
        New idempotency suffix applied to the next enrol.

    Raises
    ------
    RuntimeError
        When this desk already has a video retry suffix.
    """

    from creation.production_state import load_production, save_production

    state = load_production(desk)
    if "-retry-" in state.video_idempotency_suffix:
        raise RuntimeError(
            "This desk already retried video once. Another enrol starts another "
            "ffmpeg job on Railway. Stop and ask engineering."
        )
    suffix = f"-retry-{uuid.uuid4().hex[:8]}"
    state.video_idempotency_suffix = suffix
    state.phase = "ready_video"
    state.last_error = None
    if job_id:
        state.last_video_job_id = job_id
    save_production(desk, state)
    return suffix


#: Stages ``retry-step`` can put back, with what re-running them costs.
RETRYABLE_STEPS: dict[str, str] = {
    "new": "the draft (writing episode 1): $0, no images",
    "ready_cast_enrol": "cast plates: $0.30 per plate (a character needs two)",
    "ready_boards_enrol": "boards: $0.30 per board",
    "ready_estimate": "the estimate: $0, prices only",
}

#: ``last_error`` fragments that name the stage, for desks that failed before ``failed_phase`` was recorded.
_ERROR_STAGE_MARKS: tuple[tuple[str, str], ...] = (
    ("/boards/enrol", "ready_boards_enrol"),
    ("/cast/enrol", "ready_cast_enrol"),
    ("/prompt-video-authoring-drafts", "new"),
    ("/batches/estimate", "ready_estimate"),
    ("/video-generations", "ready_video"),
    ("episode_summaries", "ready_estimate"),
    ("film one episode alone", "ready_video"),
    ("boards ", "ready_boards_enrol"),
    ("cast ", "ready_cast_enrol"),
    ("plan ", "new"),
    ("video ", "ready_video"),
)


@dataclass(frozen=True)
class StepRetry:
    """What :func:`retry_failed_step` put back."""

    phase: str
    backup: Path
    key_prefix: str
    inferred: bool
    cost: str


def infer_failed_phase(last_error: str | None) -> str | None:
    """Name the stage a legacy failed desk stopped at, from its ``last_error``.

    Parameters
    ----------
    last_error
        The error ``step`` recorded.

    Returns
    -------
    str | None
        The stage's ready phase, or ``None`` when the text names none.
    """

    text = (last_error or "").strip()
    lowered = text.lower()
    for mark, phase in _ERROR_STAGE_MARKS:
        if mark.endswith(" "):
            if lowered.startswith(mark):
                return phase
        elif mark in text:
            return phase
    return None


def retry_failed_step(
    desk: Path,
    *,
    cause: str | None = None,
    phase: str | None = None,
) -> StepRetry:
    """Put a failed desk back on the stage that failed, with a fresh idempotency key. Sends nothing.

    ``production.json`` is backed up to a new ``api/production-backup-vN.json``
    first. The phase goes back to the failed stage's ready phase, ``last_error``
    is cleared, and the stage's retry count goes up so the next ``step`` enrols
    under a new key (:func:`creation.orchestrate.step_retry_prefix`) and never
    gets the failed job back.

    Parameters
    ----------
    desk
        Series desk with ``production.json``.
    cause
        Why the retry should succeed now; written to the run notes.
    phase
        The stage to put back, when the desk did not record one and its error names none.

    Returns
    -------
    StepRetry
        The restored phase, the backup path, the new key prefix and the stage's cost.

    Raises
    ------
    RuntimeError
        When the desk is not failed, the stage is unknown, or it failed at the take.
    """

    from creation.ops.folder import next_versioned_path
    from creation.ops.notes import append_run_note
    from creation.orchestrate import step_retry_prefix, step_retry_unit
    from creation.production_state import production_path, save_production

    desk = desk.expanduser().resolve()
    state = load_production(desk)
    if state.phase != "failed":
        raise RuntimeError(
            f"Refused. retry-step is only for a failed step; this desk is at `{state.phase}`. "
            "Nothing was changed. Run `fictora-produce status` / `step` as usual."
        )
    inferred = False
    target = phase or state.failed_phase
    if not target:
        target = infer_failed_phase(state.last_error)
        inferred = target is not None
    if not target:
        raise RuntimeError(
            "This desk failed without saying which stage (last_error: "
            f"{state.last_error!r}). Nothing was changed. Pass the stage: "
            f"--phase {{{','.join(RETRYABLE_STEPS)}}}."
        )
    if target == "ready_video":
        raise RuntimeError(
            "The take failed. Nothing was changed. A take is re-filmed with "
            "`retry-video --new-paid-take` or `film --cause`, after a human yes (it is paid)."
        )
    if target not in RETRYABLE_STEPS:
        raise RuntimeError(
            f"Unknown stage `{target}`. Nothing was changed. One of: {', '.join(RETRYABLE_STEPS)}."
        )

    source = production_path(desk)
    (desk / "api").mkdir(parents=True, exist_ok=True)
    backup = next_versioned_path(desk / "api", "production-backup", ".json")
    backup.write_bytes(source.read_bytes())

    unit = step_retry_unit(state.episode_ordinal, target)
    state.attempts[unit] = state.attempts.get(unit, 0) + 1
    state.phase = target  # type: ignore[assignment]
    state.failed_phase = None
    old_error = state.last_error
    state.last_error = None
    save_production(desk, state)
    key_prefix = step_retry_prefix(state) or ""
    append_run_note(
        desk / f"ep{state.episode_ordinal:02d}",
        f"retry-step: `{target}` put back after a failure ({(old_error or '')[:300]}). "
        f"Cause: {cause or 'none given'}. New key prefix {key_prefix}. Backup {backup.name}.",
    )
    cost = RETRYABLE_STEPS[target]
    if target == "ready_boards_enrol":
        from creation.ops.state import episode_by_ordinal, load_series
        from creation.prices import STILL_USD

        boards = len(episode_by_ordinal(load_series(desk), state.episode_ordinal).takes)
        cost += f", {boards} board(s) = ${float(STILL_USD) * boards:.2f} (paid)"
    elif target == "ready_cast_enrol":
        cost += " (paid)"
    return StepRetry(
        phase=target,
        backup=backup,
        key_prefix=key_prefix,
        inferred=inferred,
        cost=cost,
    )
