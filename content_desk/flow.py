"""Visual-first episode 1 flow over the hosted Drama API."""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from content_desk.drama_client import DramaClient
from content_desk.store import DeskRecord, DeskStore, GateState


def reuse_generation_body(
    *,
    prompt: str,
    spine: Mapping[str, Any],
    preset_id: str,
    preset_version: str,
    episode_count: int = 1,
    clip_duration_seconds: int = 15,
    cut_tempo: str | None = "one_shot",
    caption_style: str | None = None,
    video_lane: str | None = None,
) -> dict[str, Any]:
    """Build a reuse-mode video/cast/board enrol body."""

    body: dict[str, Any] = {
        "prompt": prompt,
        "art_style_preset_id": preset_id,
        "authoring_mode": "reuse",
        "reuse_spine_id": spine["spine_id"],
        "episode_count": episode_count,
        "duration_band": spine.get("duration_band") or "15s",
        "clip_duration_seconds": clip_duration_seconds,
        "aspect_ratio": "9:16",
        "episode_video_mode": "extended",
        "locale": "en-US",
        "art_style_preset_version": preset_version,
    }
    if cut_tempo:
        body["cut_tempo"] = cut_tempo
    if caption_style:
        body["captions_enabled"] = True
        body["caption_style"] = caption_style
    if video_lane:
        body["model_overrides"] = {"video": video_lane}
    return body


def resolve_preset(client: DramaClient, preset_id: str | None) -> tuple[str, str]:
    """Pick the latest published art-style preset version."""

    presets = client.get("/v1/art-style-presets").get("presets") or []
    wanted = (preset_id or "modern-romance-3").strip()
    matches = [row for row in presets if row.get("preset_id") == wanted]
    if not matches:
        matches = [row for row in presets if row.get("preset_id") == "modern-romance-3"]
    if not matches:
        raise RuntimeError(f"art style preset {wanted!r} is not published")
    preset = max(matches, key=lambda row: tuple(int(x) for x in str(row.get("version") or "0").split(".")))
    return str(preset["preset_id"]), str(preset["version"])


def start_draft(
    client: DramaClient,
    store: DeskStore,
    desk: DeskRecord,
    *,
    idempotency_prefix: str,
) -> DeskRecord:
    """Create a prompt-video authoring draft and poll the plan job."""

    body: dict[str, Any] = {
        "prompt": desk.prompt,
        "episode_count": 1,
        "episode_video_mode": "extended",
        "art_style_preset_id": desk.preset_id,
        "art_style_preset_version": desk.preset_version,
        "duration_band": desk.band,
        "clip_duration_seconds": 15,
        "aspect_ratio": "9:16",
        "locale": "en-US",
        "model_overrides": {"video": desk.video_lane},
    }
    store.save_artifact(desk.desk_id, "01_draft_request.json", body)
    draft = client.post("/v1/prompt-video-authoring-drafts", body, idempotency_key=f"{idempotency_prefix}-draft")
    store.save_artifact(desk.desk_id, "01_draft_accepted.json", draft)
    plan = client.poll_job(draft["plan_job_id"], video_route=False, deadline_seconds=1800.0)
    store.save_artifact(desk.desk_id, "02_plan_terminal.json", plan)
    if plan.get("status") != "completed":
        raise RuntimeError(f"plan job failed: {plan.get('status')}")
    desk.spine_id = draft["spine_id"]
    spine = client.get(f"/v1/spines/{desk.spine_id}")
    store.save_artifact(desk.desk_id, "03_spine.json", spine)
    desk.spine_version = spine.get("spine_version")
    desk.waiting_gate = "plates"
    store.write(desk)
    return desk


def _scene_prompt(spine: Mapping[str, Any], fallback: str) -> str:
    normalized = str(spine.get("scene_prompt_normalized") or "").strip()
    return normalized or fallback


def _episode_id(spine: Mapping[str, Any]) -> str:
    summaries = spine.get("episode_summaries") or []
    if not summaries:
        raise RuntimeError("spine has no episode_summaries")
    episode_id = str(summaries[0].get("episode_id") or "")
    if not episode_id:
        raise RuntimeError("episode_id missing on episode_summaries[0]")
    return episode_id


def _cast_media(spine: Mapping[str, Any]) -> dict[str, Any]:
    cast = spine.get("cast") or []
    urls: list[str] = []
    if isinstance(cast, list):
        for row in cast:
            if isinstance(row, dict):
                for key in ("portrait_url", "full_body_url", "reference_url", "url"):
                    value = row.get(key)
                    if value:
                        urls.append(str(value))
    return {"cast_urls": urls}


def _board_media(spine: Mapping[str, Any]) -> dict[str, Any]:
    boards: list[str] = []
    for summary in spine.get("episode_summaries") or []:
        if not isinstance(summary, dict):
            continue
        for frame in summary.get("frames") or []:
            if isinstance(frame, dict) and frame.get("image_url"):
                boards.append(str(frame["image_url"]))
    return {"board_urls": boards}


def enrol_cast(client: DramaClient, store: DeskStore, desk: DeskRecord, *, prefix: str) -> DeskRecord:
    """Enrol cast plates and poll until terminal. Does not approve."""

    if not desk.spine_id:
        raise RuntimeError("draft has not started")
    spine = client.get(f"/v1/spines/{desk.spine_id}")
    prompt = _scene_prompt(spine, desk.prompt)
    job = client.post(
        f"/v1/spines/{desk.spine_id}/cast/enrol",
        reuse_generation_body(
            prompt=prompt,
            spine=spine,
            preset_id=desk.preset_id,
            preset_version=desk.preset_version,
            video_lane=desk.video_lane,
        ),
        idempotency_key=f"{prefix}-cast-enrol",
    )
    store.save_artifact(desk.desk_id, "05_cast_enrol.json", job)
    terminal = client.poll_job(job["job_id"], video_route=True, deadline_seconds=3600.0)
    store.save_artifact(desk.desk_id, "06_cast_terminal.json", terminal)
    if terminal.get("status") != "completed":
        raise RuntimeError(f"cast enrol failed: {terminal.get('status')}")
    spine = client.get(f"/v1/spines/{desk.spine_id}")
    desk.spine_version = spine.get("spine_version")
    desk.media = {**desk.media, **_cast_media(spine)}
    desk.last_job = terminal
    desk.waiting_gate = "plates"
    store.write(desk)
    return desk


def approve_plates(client: DramaClient, store: DeskStore, desk: DeskRecord, *, prefix: str) -> DeskRecord:
    """Human yes on cast plates."""

    spine = client.get(f"/v1/spines/{desk.spine_id}")
    approved = client.post(
        f"/v1/spines/{desk.spine_id}/cast/approve",
        {"spine_version": spine["spine_version"]},
        idempotency_key=f"{prefix}-cast-approve",
    )
    store.save_artifact(desk.desk_id, "07_cast_approved.json", approved)
    desk.gates["plates"] = GateState(status="approved", at_utc=desk.updated_at_utc)
    desk.spine_version = approved.get("spine_version") or spine.get("spine_version")
    desk.waiting_gate = "script"
    store.write(desk)
    return desk


def approve_script(client: DramaClient, store: DeskStore, desk: DeskRecord, *, prefix: str) -> DeskRecord:
    """Human yes on episode 1 lines."""

    spine = client.get(f"/v1/spines/{desk.spine_id}")
    if spine.get("approval_state") != "approved":
        approved = client.post(
            f"/v1/spines/{desk.spine_id}/approve",
            {"spine_version": spine["spine_version"]},
            idempotency_key=f"{prefix}-script-approve",
        )
        store.save_artifact(desk.desk_id, "04_script_approved.json", approved)
        desk.spine_version = approved.get("spine_version")
    desk.gates["script"] = GateState(status="approved")
    desk.waiting_gate = "board"
    store.write(desk)
    return desk


def enrol_boards(client: DramaClient, store: DeskStore, desk: DeskRecord, *, prefix: str) -> DeskRecord:
    """Enrol boards and poll until terminal."""

    spine = client.get(f"/v1/spines/{desk.spine_id}")
    prompt = _scene_prompt(spine, desk.prompt)
    job = client.post(
        f"/v1/spines/{desk.spine_id}/boards/enrol",
        reuse_generation_body(
            prompt=prompt,
            spine=spine,
            preset_id=desk.preset_id,
            preset_version=desk.preset_version,
            video_lane=desk.video_lane,
        ),
        idempotency_key=f"{prefix}-boards-enrol",
    )
    store.save_artifact(desk.desk_id, "08_boards_enrol.json", job)
    terminal = client.poll_job(job["job_id"], video_route=True, deadline_seconds=7200.0)
    store.save_artifact(desk.desk_id, "09_boards_terminal.json", terminal)
    if terminal.get("status") != "completed":
        raise RuntimeError(f"boards enrol failed: {terminal.get('status')}")
    spine = client.get(f"/v1/spines/{desk.spine_id}")
    desk.spine_version = spine.get("spine_version")
    desk.media = {**desk.media, **_board_media(spine)}
    desk.last_job = terminal
    store.write(desk)
    return desk


def measure_board_exposure(client: DramaClient, store: DeskStore, desk: DeskRecord) -> DeskRecord:
    """Product path: GET boards exposure for episode 1."""

    exposure = client.get(f"/v1/spines/{desk.spine_id}/episodes/1/boards/exposure")
    store.save_artifact(desk.desk_id, "10b_boards_exposure.json", exposure)
    desk.exposure = exposure
    store.write(desk)
    return desk


def approve_board(
    client: DramaClient,
    store: DeskStore,
    desk: DeskRecord,
    *,
    prefix: str,
    accept_dim: bool,
) -> DeskRecord:
    """Approve boards after exposure check."""

    if desk.exposure is None:
        desk = measure_board_exposure(client, store, desk)
    spine = client.get(f"/v1/spines/{desk.spine_id}")
    body = {
        "spine_version": spine["spine_version"],
        "episode_ordinal": 1,
        "accept_dim": accept_dim,
    }
    approved = client.post(
        f"/v1/spines/{desk.spine_id}/episodes/1/boards/approve",
        body,
        idempotency_key=f"{prefix}-boards-approve",
    )
    store.save_artifact(desk.desk_id, "11_boards_approved.json", approved)
    desk.gates["board"] = GateState(status="approved", note=f"accept_dim={accept_dim}")
    desk.spine_version = approved.get("spine_version") or spine.get("spine_version")
    desk.waiting_gate = "video"
    store.write(desk)
    return desk


def estimate_batch(client: DramaClient, store: DeskStore, desk: DeskRecord) -> DeskRecord:
    """POST batches/estimate before video enrol."""

    spine = client.get(f"/v1/spines/{desk.spine_id}")
    episode_id = _episode_id(spine)
    desk.episode_id = episode_id
    body = {"spine_version": spine["spine_version"], "episode_ids": [episode_id]}
    estimate = client.post(f"/v1/spines/{desk.spine_id}/batches/estimate", body)
    store.save_artifact(desk.desk_id, "12_estimate.json", estimate)
    desk.estimate = estimate
    desk.spine_version = spine.get("spine_version")
    store.write(desk)
    return desk


def enrol_video(
    client: DramaClient,
    store: DeskStore,
    desk: DeskRecord,
    *,
    prefix: str,
    caption_style: str = "house",
) -> DeskRecord:
    """Enrol one reuse video generation with house captions."""

    if desk.gates.get("board") and desk.gates["board"].status != "approved":
        raise RuntimeError("board gate must be approved before video")
    if desk.estimate is None:
        desk = estimate_batch(client, store, desk)
    spine = client.get(f"/v1/spines/{desk.spine_id}")
    prompt = _scene_prompt(spine, desk.prompt)
    body = reuse_generation_body(
        prompt=prompt,
        spine=spine,
        preset_id=desk.preset_id,
        preset_version=desk.preset_version,
        caption_style=caption_style,
        video_lane=desk.video_lane,
    )
    store.save_artifact(desk.desk_id, "16_video_request.json", body)
    job = client.post("/v1/video-generations", body, idempotency_key=f"{prefix}-video")
    store.save_artifact(desk.desk_id, "16_video_enrol.json", job)
    terminal = client.poll_job(job["job_id"], video_route=True, deadline_seconds=7200.0)
    store.save_artifact(desk.desk_id, "17_video_terminal.json", terminal)
    if terminal.get("status") != "completed":
        raise RuntimeError(f"video failed: {terminal.get('status')}")
    delivery = client.get(f"/v1/video-generations/{job['job_id']}/delivery")
    store.save_artifact(desk.desk_id, "18_delivery.json", delivery)
    desk.delivery = delivery
    desk.gates["video"] = GateState(status="approved")
    desk.last_job = terminal
    desk.waiting_gate = "video"
    store.write(desk)
    return desk


def new_idempotency_prefix(desk: DeskRecord) -> str:
    """Return a fresh idempotency prefix for one desk action."""

    return f"{desk.desk_id}-{uuid.uuid4().hex[:8]}"
