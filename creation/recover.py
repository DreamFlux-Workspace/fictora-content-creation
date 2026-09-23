"""Recover from stuck or failed video generation jobs."""

from __future__ import annotations

import uuid
from pathlib import Path

from creation.harness.credentials import load_drama_api_credentials
from creation.harness.session import DramaApiRunSession
from creation.production_state import load_production


def cancel_video_job(desk: Path, job_id: str) -> dict:
    """POST cancel for one coordinator or video job id.

    Parameters
    ----------
    desk
        Series desk with ``production.json``.
    job_id
        Job id from ``GET /v1/video-generations/{id}`` or terminal artefact.

    Returns
    -------
    dict
        Cancel response JSON.
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
        return run.post(
            f"/v1/jobs/{job_id}/cancel",
            {},
            idempotency_key=f"{run.prefix}-cancel-{job_id}",
        )
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
