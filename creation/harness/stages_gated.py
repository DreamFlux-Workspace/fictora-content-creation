"""Episode-1 API stages with human gates — enrol and approve stay separate."""

from __future__ import annotations

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
    episode_count: int = 1,
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
        "clip_duration_seconds": 15,
        "aspect_ratio": "9:16",
        "locale": "en-US",
        "model_overrides": {"video": video_lane},
    }
    run.save("01_draft_request.json", body)
    draft = run.post("/v1/prompt-video-authoring-drafts", body, idempotency_key=f"{run.prefix}-draft")
    run.save("01_draft_accepted.json", draft)
    plan = run.poll_job(draft["plan_job_id"], label="plan", video_route=False, deadline_seconds=1800.0)
    run.save("02_plan_terminal.json", plan)
    if plan.get("status") != "completed":
        raise SystemExit(f"plan failed: {plan.get('status')}")
    spine_id = str(draft["spine_id"])
    run.save("03_spine.json", run.spine(spine_id))
    return spine_id, plan


def enrol_cast(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    video_lane: str | None = "minimax-h3",
    tag: str = "ep1",
) -> dict[str, Any]:
    """Enrol cast plates and poll. Does not approve."""

    spine = run.spine(spine_id)
    job = run.post(
        f"/v1/spines/{spine_id}/cast/enrol",
        reuse_generation_body(
            prompt=scene_prompt(spine, prompt),
            spine=spine,
            preset_id=preset_id,
            preset_version=preset_version,
            video_lane=video_lane,
        ),
        idempotency_key=f"{run.prefix}-{tag}-cast-enrol",
    )
    run.save(f"05_{tag}_cast_enrol.json", job)
    terminal = run.poll_job(job["job_id"], label="cast", video_route=True, deadline_seconds=3600.0)
    run.save(f"06_{tag}_cast_terminal.json", terminal)
    if terminal.get("status") != "completed":
        raise SystemExit(f"cast failed: {terminal.get('status')}")
    return run.spine(spine_id)


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
) -> dict[str, Any]:
    """Enrol boards and poll. Does not approve."""

    spine = run.spine(spine_id)
    job = run.post(
        f"/v1/spines/{spine_id}/boards/enrol",
        reuse_generation_body(
            prompt=scene_prompt(spine, prompt),
            spine=spine,
            preset_id=preset_id,
            preset_version=preset_version,
            video_lane=video_lane,
        ),
        idempotency_key=f"{run.prefix}-{tag}-boards-enrol",
    )
    run.save(f"08_{tag}_boards_enrol.json", job)
    terminal = run.poll_job(job["job_id"], label="boards", video_route=True, deadline_seconds=7200.0)
    run.save(f"09_{tag}_boards_terminal.json", terminal)
    if terminal.get("status") != "completed":
        raise SystemExit(f"boards failed: {terminal.get('status')}")
    return run.spine(spine_id)


def estimate_batch(run: DramaApiRunSession, *, spine_id: str) -> dict[str, Any]:
    """Price the batch before video enrol."""

    spine = run.spine(spine_id)
    summaries = spine.get("episode_summaries") or []
    if not summaries:
        raise SystemExit("spine has no episode_summaries")
    episode_id = str(summaries[0].get("episode_id") or "")
    if not episode_id:
        raise SystemExit("episode_id missing")
    body = {"spine_version": spine["spine_version"], "episode_ids": [episode_id]}
    estimate = run.post(f"/v1/spines/{spine_id}/batches/estimate", body)
    run.save("12_estimate.json", estimate)
    return estimate


def enrol_video(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    caption_style: str = "house",
    video_lane: str | None = "minimax-h3",
    clip_duration_seconds: int = 15,
    cut_tempo: str | None = "one_shot",
) -> dict[str, Any]:
    """Film one reuse take with captions and fetch delivery."""

    spine = run.spine(spine_id)
    body = reuse_generation_body(
        prompt=scene_prompt(spine, prompt),
        spine=spine,
        preset_id=preset_id,
        preset_version=preset_version,
        clip_duration_seconds=clip_duration_seconds,
        cut_tempo=cut_tempo,
        caption_style=caption_style,
        video_lane=video_lane,
    )
    run.save("16_video_request.json", body)
    job = run.post("/v1/video-generations", body, idempotency_key=f"{run.prefix}-video")
    run.save("16_video_enrol.json", job)
    terminal = run.poll_job(job["job_id"], label="video", video_route=True, deadline_seconds=7200.0)
    run.save("17_video_terminal.json", terminal)
    if terminal.get("status") != "completed":
        raise SystemExit(f"video failed: {terminal.get('status')}")
    delivery = run.get(f"/v1/video-generations/{job['job_id']}/delivery")
    run.save("18_delivery.json", delivery)
    return delivery


__all__ = [
    "approve_cast",
    "approve_ep1_boards",
    "approve_script",
    "enrol_boards",
    "enrol_cast",
    "enrol_video",
    "estimate_batch",
    "measure_ep1_board_exposure",
    "start_draft",
]
