"""Episode-1 API stages with human gates — enrol and approve stay separate."""

from __future__ import annotations

import time
from typing import Any, Mapping

from creation.harness.session import DramaApiRunSession
from creation.harness.visual_first_ep1 import (
    approve_ep1_boards,
    measure_ep1_board_exposure,
    reuse_generation_body,
)


def scene_prompt(spine: Mapping[str, Any], fallback: str) -> str:
    """Return normalized scene prompt or the desk fallback."""

    normalized = str(spine.get("scene_prompt_normalized") or "").strip()
    return normalized or fallback


def start_draft(
    run: DramaApiRunSession,
    *,
    prompt: str,
    preset_id: str,
    preset_version: str,
    band: str = "15s",
    video_lane: str = "minimax-h3",
    episode_count: int = 4,
) -> tuple[str, dict[str, Any]]:
    """Create a prompt-video draft and poll the plan job.

    Returns
    -------
    tuple[str, dict[str, Any]]
        ``(spine_id, terminal plan job)``.
    """

    body: dict[str, Any] = {
        "prompt": prompt,
        "episode_count": episode_count,
        "episode_video_mode": "extended",
        "art_style_preset_id": preset_id,
        "art_style_preset_version": preset_version,
        "duration_band": band,
        "locale": "en-US",
    }
    if video_lane:
        body["model_overrides"] = {"video": video_lane}
    run.save("01_draft_request.json", body)

    last_plan: dict[str, Any] = {}
    draft: dict[str, Any] = {}
    for attempt in range(3):
        suffix = f"-a{attempt}" if attempt else ""
        draft = run.post(
            "/v1/prompt-video-authoring-drafts",
            body,
            idempotency_key=f"{run.prefix}-draft{suffix}",
        )
        run.save(f"01_draft_accepted{suffix}.json", draft)
        plan = run.poll_job(draft["plan_job_id"], label="plan", video_route=False, deadline_seconds=1800.0)
        run.save(f"02_plan_terminal{suffix}.json", plan)
        last_plan = plan
        if plan.get("status") == "completed":
            spine_id = str(draft["spine_id"])
            run.save("03_spine.json", run.spine(spine_id))
            return spine_id, plan
        error = plan.get("error") if isinstance(plan.get("error"), dict) else {}
        if (
            _terminal_error_code(plan) == "authoring_stalled"
            and error.get("retryable") is True
            and attempt + 1 < 3
        ):
            run.emit("plan_retry", code="authoring_stalled", attempt=attempt + 1)
            time.sleep(5.0)
            continue
        break
    raise SystemExit(
        f"plan failed: {last_plan.get('status')} code={_terminal_error_code(last_plan)}"
    )


def _episode_ids_from_spine(spine: Mapping[str, Any]) -> list[str]:
    """Return ordered episode ids from a spine snapshot."""

    episode_ids: list[str] = []
    for row in spine.get("episode_summaries") or []:
        if isinstance(row, dict):
            eid = str(row.get("episode_id") or "").strip()
            if eid:
                episode_ids.append(eid)
    return episode_ids


def _pilot_batch_estimate_episode_ids(spine: Mapping[str, Any]) -> list[str]:
    """Episode ids for the first pilot batch estimate call.

    Prod requires at least five planned episodes on the spine (drama cadence) and
    accepts only episode 1 and at most episode 2 in the estimate body.
    """

    episode_ids = _episode_ids_from_spine(spine)
    if len(episode_ids) < 2:
        raise SystemExit(
            "estimate requires at least 2 planned episodes on the spine for the pilot batch; "
            f"got {len(episode_ids)}"
        )
    return episode_ids[:2]


def _terminal_error_code(terminal: dict[str, Any]) -> str | None:
    error = terminal.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        return str(code) if code else None
    return None


def _cast_plates_present(spine: dict[str, Any]) -> bool:
    """Return whether every cast row has a usable portrait asset on the spine."""

    cast_rows = [row for row in (spine.get("cast") or []) if isinstance(row, dict) and row.get("cast_id")]
    if not cast_rows:
        return False
    assets = [row for row in (spine.get("media_assets") or []) if isinstance(row, dict)]
    ready_cast_ids: set[str] = set()
    for asset in assets:
        if asset.get("relation_type") != "cast_card" or asset.get("stale"):
            continue
        if asset.get("url"):
            relation_id = asset.get("relation_id")
            if isinstance(relation_id, str) and relation_id:
                ready_cast_ids.add(relation_id)
    for row in cast_rows:
        cast_id = str(row["cast_id"])
        if cast_id in ready_cast_ids:
            continue
        if any(row.get(key) for key in ("image_url", "portrait_url", "full_body_url", "url")):
            ready_cast_ids.add(cast_id)
    return all(str(row["cast_id"]) in ready_cast_ids for row in cast_rows)


def enrol_cast(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    video_lane: str | None = "minimax-h3",
    tag: str = "ep1",
    max_attempts: int = 5,
) -> dict[str, Any]:
    """Enrol cast plates and poll. Does not approve."""

    last_terminal: dict[str, Any] = {}
    for attempt in range(max_attempts):
        spine = run.spine(spine_id)
        suffix = f"-a{attempt}" if attempt else ""
        job = run.post(
            f"/v1/spines/{spine_id}/cast/enrol",
            reuse_generation_body(
                prompt=scene_prompt(spine, prompt),
                spine=spine,
                preset_id=preset_id,
                preset_version=preset_version,
                video_lane=video_lane,
            ),
            idempotency_key=f"{run.prefix}-{tag}-cast-enrol{suffix}",
        )
        run.save(f"05_{tag}_cast_enrol{suffix}.json", job)
        terminal = run.poll_job(job["job_id"], label="cast", video_route=True, deadline_seconds=3600.0)
        run.save(f"06_{tag}_cast_terminal{suffix}.json", terminal)
        last_terminal = terminal
        if terminal.get("status") == "completed":
            return run.spine(spine_id)
        if _terminal_error_code(terminal) == "plan_media_spine_version_stale":
            recovered = run.spine(spine_id)
            if _cast_plates_present(recovered):
                run.emit("cast_recover", code="plan_media_spine_version_stale", note="plates on spine")
                return recovered
            if attempt + 1 < max_attempts:
                run.emit("cast_retry", code="plan_media_spine_version_stale", attempt=attempt + 1)
                time.sleep(5.0)
                continue
        break
    raise SystemExit(f"cast failed: {last_terminal.get('status')} code={_terminal_error_code(last_terminal)}")


def approve_cast(run: DramaApiRunSession, *, spine_id: str, tag: str = "ep1") -> dict[str, Any]:
    """Human yes on cast plates."""

    spine = run.spine(spine_id)
    approved = run.post(
        f"/v1/spines/{spine_id}/cast/approve",
        {"spine_version": spine["spine_version"]},
        idempotency_key=f"{run.prefix}-{tag}-cast-approve",
    )
    run.save(f"07_{tag}_cast_approved.json", approved)
    return run.spine(spine_id)


def approve_script(run: DramaApiRunSession, *, spine_id: str) -> dict[str, Any]:
    """Human yes on script lines."""

    spine = run.spine(spine_id)
    if spine.get("approval_state") != "approved":
        approved = run.post(
            f"/v1/spines/{spine_id}/approve",
            {"spine_version": spine["spine_version"]},
            idempotency_key=f"{run.prefix}-script-approve",
        )
        run.save("04_spine_approved.json", approved)
    return run.spine(spine_id)


def enrol_boards(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    video_lane: str | None = "minimax-h3",
    tag: str = "ep1",
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Enrol boards and poll. Does not approve."""

    last_terminal: dict[str, Any] = {}
    for attempt in range(max_attempts):
        spine = run.spine(spine_id)
        suffix = f"-a{attempt}" if attempt else ""
        job = run.post(
            f"/v1/spines/{spine_id}/boards/enrol",
            reuse_generation_body(
                prompt=scene_prompt(spine, prompt),
                spine=spine,
                preset_id=preset_id,
                preset_version=preset_version,
                video_lane=video_lane,
            ),
            idempotency_key=f"{run.prefix}-{tag}-boards-enrol{suffix}",
        )
        run.save(f"08_{tag}_boards_enrol{suffix}.json", job)
        terminal = run.poll_job(job["job_id"], label="boards", video_route=True, deadline_seconds=7200.0)
        run.save(f"09_{tag}_boards_terminal{suffix}.json", terminal)
        last_terminal = terminal
        if terminal.get("status") == "completed":
            return run.spine(spine_id)
        if _terminal_error_code(terminal) == "plan_media_spine_version_stale" and attempt + 1 < max_attempts:
            run.emit("boards_retry", code="plan_media_spine_version_stale", attempt=attempt + 1)
            time.sleep(2.0)
            continue
        break
    raise SystemExit(f"boards failed: {last_terminal.get('status')} code={_terminal_error_code(last_terminal)}")


def estimate_batch(run: DramaApiRunSession, *, spine_id: str) -> dict[str, Any]:
    """Resolve the next pilot batch episodes before video enrol (v2 estimate)."""

    spine = run.spine(spine_id)
    if not _episode_ids_from_spine(spine):
        raise SystemExit("spine has no episode_summaries")
    body = {
        "spine_version": spine["spine_version"],
        "episode_ids": _pilot_batch_estimate_episode_ids(spine),
    }
    try:
        estimate = run.post(f"/v1/spines/{spine_id}/batches/estimate", body)
    except SystemExit as exc:
        msg = str(exc)
        if "invalid_episode_selection" in msg or "invalid_pilot_batch" in msg:
            estimate = {
                **body,
                "estimate_skipped": True,
                "detail": msg[:800],
            }
            run.save("12_estimate.json", estimate)
            return estimate
        raise
    run.save("12_estimate.json", estimate)
    return estimate


def finish_video_job(
    run: DramaApiRunSession,
    job_id: str,
    *,
    poll_deadline_seconds: float = 7200.0,
) -> dict[str, Any]:
    """Poll one enrolled video job and fetch delivery JSON."""

    terminal = run.poll_job(
        job_id,
        label="video",
        video_route=True,
        deadline_seconds=poll_deadline_seconds,
    )
    run.save("17_video_terminal.json", terminal)
    if terminal.get("status") != "completed":
        raise SystemExit(f"video failed: {terminal.get('status')}")
    delivery = run.get(f"/v1/video-generations/{job_id}/delivery")
    run.save("18_delivery.json", delivery)
    return delivery


def delivery_for_completed_video_job(run: DramaApiRunSession, job_id: str) -> dict[str, Any]:
    """Fetch delivery for a video job that already reached ``completed``."""

    delivery = run.get(f"/v1/video-generations/{job_id}/delivery")
    run.save("18_delivery.json", delivery)
    return delivery


def enrol_video(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    caption_style: str = "house",
    api_captions: bool = False,
    video_lane: str | None = "minimax-h3",
    clip_duration_seconds: int = 15,
    cut_tempo: str | None = "one_shot",
    video_idempotency_suffix: str = "",
    poll_deadline_seconds: float = 7200.0,
) -> dict[str, Any]:
    """Film one reuse take; delivery JSON or raw scene clips per ``api_captions``."""

    from creation.harness.raw_video import wait_for_raw_scene_clips

    spine = run.spine(spine_id)
    body = reuse_generation_body(
        prompt=scene_prompt(spine, prompt),
        spine=spine,
        preset_id=preset_id,
        preset_version=preset_version,
        clip_duration_seconds=clip_duration_seconds,
        cut_tempo=cut_tempo,
        caption_style=caption_style if api_captions else None,
        api_captions=api_captions,
        video_lane=video_lane,
    )
    run.save("16_video_request.json", body)
    idem_suffix = (video_idempotency_suffix or "").strip()
    idem = f"{run.prefix}-video{idem_suffix}" if idem_suffix else f"{run.prefix}-video"
    job = run.post("/v1/video-generations", body, idempotency_key=idem)
    run.save("16_video_enrol.json", job)
    job_id = str(job["job_id"])
    if api_captions:
        return finish_video_job(run, job_id, poll_deadline_seconds=poll_deadline_seconds)
    raw = wait_for_raw_scene_clips(run, job_id, deadline_seconds=poll_deadline_seconds)
    return {"raw_scenes": raw, "primary_clip_url": raw["clips"][0]["url"] if raw.get("clips") else None}


__all__ = [
    "approve_cast",
    "approve_ep1_boards",
    "approve_script",
    "delivery_for_completed_video_job",
    "enrol_boards",
    "enrol_cast",
    "enrol_video",
    "finish_video_job",
    "estimate_batch",
    "measure_ep1_board_exposure",
    "start_draft",
]
