"""Desk orchestration over the hosted Drama API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from creation.harness.credentials import load_drama_api_credentials
from creation.harness.run_context import save_run_meta
from creation.harness.session import DramaApiRunSession
from creation.harness import stages_gated as stages
from creation.harness.visual_first_ep1 import approve_ep1_boards, measure_ep1_board_exposure
from creation.media_fetch import download_to_versioned
from creation.ops.floor import approve_board, approve_script as record_script_gate, approve_series_gate
from creation.ops.notes import append_run_note
from creation.ops.state import load_series
from creation.production_config import load_production_config
from creation.production_state import (
    ProductionState,
    api_dir_for_episode,
    ensure_production,
    load_production,
    save_production,
)


@dataclass(frozen=True)
class StepResult:
    """Outcome of one orchestrator step."""

    phase: str
    message: str
    paths: tuple[str, ...] = ()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _episode_dir(desk: Path, ordinal: int) -> Path:
    return desk / f"ep{ordinal:02d}"


def _resolve_preset(run: DramaApiRunSession, preset_id: str) -> tuple[str, str]:
    presets = run.get("/v1/art-style-presets").get("presets") or []
    matches = [row for row in presets if row.get("preset_id") == preset_id]
    if not matches:
        raise RuntimeError(f"preset not published: {preset_id!r}")
    preset = max(matches, key=lambda row: tuple(int(x) for x in str(row.get("version") or "0").split(".")))
    return str(preset["preset_id"]), str(preset["version"])


def _cast_urls(spine: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for row in spine.get("cast") or []:
        if not isinstance(row, dict):
            continue
        for key in ("portrait_url", "full_body_url", "reference_url", "image_url", "url"):
            value = row.get(key)
            if value:
                urls.append(str(value))
    return urls


def _board_urls(spine: dict[str, Any], ordinal: int) -> list[str]:
    urls: list[str] = []
    episode_id = None
    for summary in spine.get("episode_summaries") or []:
        if not isinstance(summary, dict):
            continue
        if int(summary.get("episode_ordinal") or summary.get("ordinal") or 1) == ordinal:
            episode_id = str(summary.get("episode_id") or "")
            break
    for summary in spine.get("episode_summaries") or []:
        if not isinstance(summary, dict):
            continue
        if int(summary.get("episode_ordinal") or summary.get("ordinal") or 1) != ordinal:
            continue
        for frame in summary.get("frames") or []:
            if isinstance(frame, dict) and frame.get("image_url"):
                urls.append(str(frame["image_url"]))
    for board in spine.get("boards") or []:
        if not isinstance(board, dict):
            continue
        if episode_id and board.get("episode_id") == episode_id and board.get("image_url"):
            urls.append(str(board["image_url"]))
    return urls


def _resume_video_delivery(
    run: DramaApiRunSession,
    api_dir: Path,
    *,
    poll_deadline_seconds: float,
) -> dict[str, Any] | None:
    """Poll or fetch delivery for an in-flight job saved in ``16_video_enrol.json``."""

    enrol_path = api_dir / "16_video_enrol.json"
    if not enrol_path.is_file():
        return None
    try:
        enrol = json.loads(enrol_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    job_id = enrol.get("job_id") if isinstance(enrol, dict) else None
    if not isinstance(job_id, str) or not job_id.strip():
        return None
    snapshot = run.get(f"/v1/video-generations/{job_id}")
    status = snapshot.get("status")
    if status in {"queued", "running"}:
        return stages.finish_video_job(run, job_id, poll_deadline_seconds=poll_deadline_seconds)
    if status == "completed":
        return stages.delivery_for_completed_video_job(run, job_id)
    return None


def _delivery_video_url(delivery: dict[str, Any]) -> str | None:
    for key in ("video_url", "url"):
        if delivery.get(key):
            return str(delivery[key])
    for row in delivery.get("deliveries") or []:
        if isinstance(row, dict):
            for key in ("video_url", "final_video_url", "url"):
                if row.get(key):
                    return str(row[key])
    episodes = delivery.get("episodes") or []
    if episodes and isinstance(episodes[0], dict):
        ep = episodes[0]
        for key in ("video_url", "final_video_url", "url"):
            if ep.get(key):
                return str(ep[key])
    return None


def _estimate_usd(payload: dict[str, Any], *, fallback_usd: float = 1.20) -> float:
    for key in ("total_usd", "estimated_usd", "usd", "total_cost_usd"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    batches = payload.get("batches") or payload.get("estimates") or []
    total = 0.0
    if isinstance(batches, list):
        for row in batches:
            if isinstance(row, dict):
                for key in ("total_usd", "usd", "cost_usd"):
                    if isinstance(row.get(key), (int, float)):
                        total += float(row[key])
                        break
    return total if total > 0 else float(fallback_usd)


def bind_desk(
    desk: Path,
    *,
    prompt: str,
    preset_id: str,
    video_lane: str = "minimax-h3",
    episode_ordinal: int = 1,
) -> ProductionState:
    """Attach API orchestration to an existing series desk."""

    desk = desk.expanduser().resolve()
    load_series(desk)
    base, token = load_drama_api_credentials(_repo_root())
    api_dir = api_dir_for_episode(desk, episode_ordinal)
    api_dir.mkdir(parents=True, exist_ok=True)
    probe = DramaApiRunSession(base_url=base, token=token, out_dir=api_dir)
    try:
        pid, version = _resolve_preset(probe, preset_id)
    finally:
        probe.client.close()
    state = ensure_production(
        desk,
        prompt=prompt,
        preset_id=pid,
        preset_version=version,
        video_lane=video_lane,
        episode_ordinal=episode_ordinal,
    )
    save_run_meta(api_dir, session_id=state.session_id, preset_id=pid, preset_version=version)
    save_production(desk, state)
    return state


def _open_run(desk: Path, state: ProductionState) -> DramaApiRunSession:
    base, token = load_drama_api_credentials(_repo_root())
    api_dir = api_dir_for_episode(desk, state.episode_ordinal)
    api_dir.mkdir(parents=True, exist_ok=True)
    return DramaApiRunSession(
        base_url=base,
        token=token,
        out_dir=api_dir,
        session_id=state.session_id,
    )


def status_message(desk: Path) -> str:
    """Return a one-line operator status for the desk."""

    state = load_production(desk)
    return f"phase={state.phase} spine={state.spine_id or '—'} session={state.session_id}"


def run_step(desk: Path, *, confirm_spend: bool = False) -> StepResult:
    """Run the next automated API step for the current phase."""

    desk = desk.expanduser().resolve()
    state = load_production(desk)
    cfg = load_production_config(desk)
    run = _open_run(desk, state)
    ep = state.episode_ordinal
    ep_dir = _episode_dir(desk, ep)
    paths: list[str] = []

    try:
        if state.phase == "new":
            spine_id, plan = stages.start_draft(
                run,
                prompt=state.prompt,
                preset_id=state.preset_id,
                preset_version=state.preset_version,
                band=state.band,
                video_lane=state.video_lane,
                episode_count=cfg.draft_episode_count,
            )
            state.spine_id = spine_id
            state.phase = "ready_cast_enrol"
            state.last_error = None
            save_run_meta(
                api_dir_for_episode(desk, ep),
                session_id=state.session_id,
                spine_id=spine_id,
                preset_id=state.preset_id,
                preset_version=state.preset_version,
            )
            save_production(desk, state)
            append_run_note(ep_dir, f"Draft complete. spine_id={spine_id} plan={plan.get('status')}")
            return StepResult(state.phase, f"Draft done. spine_id={spine_id}. Next: run step again for cast enrol.", ())

        if state.phase == "ready_cast_enrol":
            if not state.spine_id:
                raise RuntimeError("spine_id missing")
            stages.enrol_cast(
                run,
                spine_id=state.spine_id,
                prompt=state.prompt,
                preset_id=state.preset_id,
                preset_version=state.preset_version,
                video_lane=state.video_lane,
            )
            spine = run.spine(state.spine_id)
            fetch = httpx.Client(timeout=120.0)
            try:
                for index, url in enumerate(_cast_urls(spine), start=1):
                    path = download_to_versioned(fetch, url, ep_dir / "plates", f"plate-ep{ep:02d}-{index}")
                    paths.append(str(path))
            finally:
                fetch.close()
            state.phase = "wait_plates"
            save_production(desk, state)
            append_run_note(ep_dir, "Cast enrol complete. Open plates/ and approve.")
            return StepResult(
                state.phase,
                "Cast drawn. Human gate: review plates/, then `fictora-produce approve --gate plates`.",
                tuple(paths),
            )

        if state.phase in {"wait_plates", "wait_script", "wait_board", "wait_spend"}:
            gate = {"wait_plates": "plates", "wait_script": "script", "wait_board": "board", "wait_spend": "spend"}[
                state.phase
            ]
            if state.phase == "wait_spend" and confirm_spend:
                state.phase = "ready_video"
                save_production(desk, state)
                return run_step(desk, confirm_spend=False)
            return StepResult(state.phase, f"Waiting on human gate `{gate}`. Use fictora-produce approve.", ())

        if state.phase == "ready_boards_enrol":
            stages.enrol_boards(
                run,
                spine_id=state.spine_id or "",
                prompt=state.prompt,
                preset_id=state.preset_id,
                preset_version=state.preset_version,
                video_lane=state.video_lane,
            )
            spine = run.spine(state.spine_id or "")
            fetch = httpx.Client(timeout=120.0)
            try:
                for index, url in enumerate(_board_urls(spine, ep), start=1):
                    path = download_to_versioned(fetch, url, ep_dir / "boards", f"board-ep{ep:02d}-t1-{index}")
                    paths.append(str(path))
            finally:
                fetch.close()
            exposure = measure_ep1_board_exposure(run, spine_id=state.spine_id or "")
            boards = exposure.get("boards") if isinstance(exposure.get("boards"), list) else []
            state.exposure_accept_dim = any(isinstance(b, dict) and b.get("below_dim_floor") for b in boards)
            state.phase = "wait_board"
            save_production(desk, state)
            append_run_note(ep_dir, f"Board exposure: {json.dumps(exposure, default=str)[:500]}")
            return StepResult(
                state.phase,
                "Boards drawn. Review boards/ and exposure in api/. Then approve board gate.",
                tuple(paths),
            )

        if state.phase == "ready_estimate":
            estimate = stages.estimate_batch(run, spine_id=state.spine_id or "")
            state.estimate_usd = _estimate_usd(estimate, fallback_usd=cfg.fallback_estimate_usd)
            state.phase = "wait_spend"
            save_production(desk, state)
            append_run_note(ep_dir, f"Estimate ${state.estimate_usd:.2f} before take.")
            return StepResult(
                state.phase,
                f"Estimate ${state.estimate_usd:.2f}. Human yes, then `fictora-produce step --confirm-spend`.",
                (),
            )

        if state.phase == "ready_video":
            delivery = _resume_video_delivery(
                run,
                api_dir_for_episode(desk, ep),
                poll_deadline_seconds=cfg.poll_video_deadline_seconds,
            )
            if delivery is None:
                delivery = stages.enrol_video(
                    run,
                    spine_id=state.spine_id or "",
                    prompt=state.prompt,
                    preset_id=state.preset_id,
                    preset_version=state.preset_version,
                    caption_style=cfg.caption_style,
                    video_lane=state.video_lane,
                    clip_duration_seconds=cfg.clip_duration_seconds,
                    cut_tempo=cfg.cut_tempo,
                    video_idempotency_suffix=state.video_idempotency_suffix,
                    poll_deadline_seconds=cfg.poll_video_deadline_seconds,
                )
            url = _delivery_video_url(delivery)
            if url:
                fetch = httpx.Client(timeout=300.0)
                try:
                    path = download_to_versioned(fetch, url, ep_dir / "takes", f"take-ep{ep:02d}-t1-raw", default_suffix=".mp4")
                    paths.append(str(path))
                finally:
                    fetch.close()
            job_id = None
            enrol_path = api_dir_for_episode(desk, ep) / "16_video_enrol.json"
            if enrol_path.is_file():
                try:
                    enrol = json.loads(enrol_path.read_text(encoding="utf-8"))
                    if isinstance(enrol, dict) and isinstance(enrol.get("job_id"), str):
                        job_id = enrol["job_id"]
                except json.JSONDecodeError:
                    pass
            state.last_delivery_url = url
            state.last_video_job_id = job_id
            state.phase = "complete"
            save_production(desk, state)
            append_run_note(ep_dir, f"Take delivery: {url}")
            return StepResult(state.phase, f"Complete. video_url={url}", tuple(paths))

        if state.phase == "complete":
            return StepResult(state.phase, f"Already complete. video={state.last_delivery_url}", ())

        raise RuntimeError(f"unknown phase: {state.phase}")
    except SystemExit as exc:
        state.phase = "failed"
        state.last_error = str(exc)
        save_production(desk, state)
        raise
    finally:
        run.client.close()


def approve_gate(desk: Path, *, gate: str, path: Path | None = None, accept_dim: bool | None = None) -> StepResult:
    """Record a human gate and run the matching API approve when needed."""

    desk = desk.expanduser().resolve()
    state = load_production(desk)
    run = _open_run(desk, state)
    ep = state.episode_ordinal
    ep_dir = _episode_dir(desk, ep)
    paths: list[str] = []

    try:
        if gate == "plates":
            if state.phase != "wait_plates":
                raise RuntimeError(f"expected wait_plates, got {state.phase}")
            stages.approve_cast(run, spine_id=state.spine_id or "")
            record = approve_series_gate(desk, "plates", path=str(path) if path else None)
            state.phase = "wait_script"
            save_production(desk, state)
            return StepResult(state.phase, f"Plates approved ({record.status}). Human: approve script lines.", ())

        if gate == "script":
            if state.phase != "wait_script":
                raise RuntimeError(f"expected wait_script, got {state.phase}")
            stages.approve_script(run, spine_id=state.spine_id or "")
            record_script_gate(desk, episode=ep)
            state.phase = "ready_boards_enrol"
            save_production(desk, state)
            return StepResult(state.phase, "Script approved on API. Next: fictora-produce step (boards enrol).", ())

        if gate == "board":
            if state.phase != "wait_board":
                raise RuntimeError(f"expected wait_board, got {state.phase}")
            use_dim = state.exposure_accept_dim if accept_dim is None else accept_dim
            spine = run.spine(state.spine_id or "")
            approve_ep1_boards(run, spine_id=state.spine_id or "", spine=spine, accept_dim=use_dim)
            if path is None:
                raise ValueError("board approve requires --path to the board file reviewed")
            record = approve_board(desk, episode=ep, take_id="t1", image=str(path))
            state.phase = "ready_estimate"
            save_production(desk, state)
            return StepResult(state.phase, f"Board approved ({record.status}). Next: fictora-produce step (estimate).", ())

        raise ValueError(f"unknown gate: {gate}")
    finally:
        run.client.close()
