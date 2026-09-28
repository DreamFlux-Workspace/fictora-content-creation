"""Desk orchestration over the hosted Drama API.

One phase machine per desk, pointed at one episode at a time
(``production.json`` ``episode_ordinal``). Episode 1 runs the visual-first
order: draft (episode 1 alone) -> cast plates -> script -> boards -> estimate
-> take. Episode 2 on is written with ``author --episode N`` (see
:mod:`creation.episode_commands`), which points the machine at that episode
with its lines waiting at the script gate; the rest of the loop is the same.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from creation.desk_media_urls import board_urls_for_episode, cast_plate_urls
from creation.harness import stages_gated as stages
from creation.harness.credentials import load_drama_api_credentials
from creation.harness.raw_video import episode_clips, fetch_take_facts, wait_for_raw_scene_clips
from creation.harness.run_context import save_run_meta
from creation.harness.session import DramaApiRunSession
from creation.harness.visual_first_ep1 import approve_episode_boards, measure_board_exposure
from creation.media_fetch import download_to_versioned
from creation.ops.floor import (
    approve_board,
    approve_series_gate,
    record_estimate,
    record_filmed,
    record_spend,
    set_take_lines,
)
from creation.ops.floor import approve_script as record_script_gate
from creation.ops.folder import next_versioned_path
from creation.ops.luma import measure_board_luma
from creation.ops.notes import append_run_note
from creation.ops.state import episode_by_ordinal, load_series
from creation.plan_prompt import ensure_plan_prompt
from creation.prices import (
    STILL_USD,
    envelope_usd,
    lane_label,
    lane_take_usd,
    reference_images_ceiling,
    take_facts_usd,
)
from creation.production_config import load_production_config
from creation.production_state import (
    ProductionState,
    api_dir_for_episode,
    ensure_production,
    load_production,
    save_production,
)
from creation.spine_view import (
    board_assets,
    beats_by_take,
    episode_id_for,
    frames_by_set,
    frames_digest,
    script_lines,
    shot_list_lines,
    spoken_lines,
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


def _note(ep_dir: Path, body: str) -> None:
    if (ep_dir / "run-notes.md").is_file():
        append_run_note(ep_dir, body)


def _resolve_preset(run: DramaApiRunSession, preset_id: str) -> tuple[str, str]:
    presets = run.get("/v1/art-style-presets").get("presets") or []
    matches = [row for row in presets if row.get("preset_id") == preset_id]
    if not matches:
        raise RuntimeError(f"preset not published: {preset_id!r}")
    preset = max(matches, key=lambda row: tuple(int(x) for x in str(row.get("version") or "0").split(".")))
    return str(preset["preset_id"]), str(preset["version"])


def save_spine_snapshot(desk: Path, episode: int, spine: dict[str, Any]) -> Path:
    """Save the spine as the desk's current story (``api/spine.json``) and the episode's (``epNN/api/spine.json``).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal whose ``api/`` also gets a copy (local captions read it).
    spine
        ``GET /v1/spines/{id}`` JSON.

    Returns
    -------
    Path
        The series-level snapshot.
    """

    text = json.dumps(spine, ensure_ascii=False, indent=2) + "\n"
    for folder in (desk / "api", api_dir_for_episode(desk, episode)):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "spine.json").write_text(text, encoding="utf-8")
    return desk / "api" / "spine.json"


def sync_spine_lines(desk: Path, spine: dict[str, Any], *, episode: int) -> dict[str, int]:
    """Put the spine's performed lines on the desk take by take. Does not approve them.

    Parameters
    ----------
    desk
        Series desk.
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    dict[str, int]
        Line count per take id.
    """

    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    take_ids = [take.take_id for take in slot.takes]
    counts: dict[str, int] = {}
    for take_id, beats in zip(take_ids, beats_by_take(spine, episode=episode, take_count=len(take_ids)), strict=True):
        lines = [line for beat in beats for line in spoken_lines(spine, beat)]
        set_take_lines(desk, episode=episode, take_id=take_id, lines=lines)
        counts[take_id] = len(lines)
    return counts


def script_gate_text(desk: Path, spine: dict[str, Any], *, episode: int) -> str:
    """Return the script gate as text: each take's shots and lines.

    Parameters
    ----------
    desk
        Series desk.
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    str
        Printable block.
    """

    slot = episode_by_ordinal(load_series(desk), episode)
    return "\n".join(script_lines(spine, episode=episode, take_ids=[take.take_id for take in slot.takes]))


def _resume_raw_clips(api_dir: Path, *, run: DramaApiRunSession, state: ProductionState, deadline: float) -> dict[str, Any] | None:
    """Pick up the take job this desk already paid for, or ``None`` to enrol a new one.

    Only the job enrolled with the desk's current retry suffix is resumed, so a
    ``retry-video --new-paid-take`` enrols its new take instead of reading the old one.
    """

    enrol_path = api_dir / "16_video_enrol.json"
    if not enrol_path.is_file():
        return None
    enrolled_with = state.video_enrolled_suffix if state.video_enrolled_suffix is not None else ""
    if enrolled_with != state.video_idempotency_suffix:
        return None
    try:
        enrol = json.loads(enrol_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    job_id = enrol.get("job_id") if isinstance(enrol, dict) else None
    if not isinstance(job_id, str) or not job_id.strip():
        return None
    raw_path = api_dir / "17_raw_scene_clips.json"
    if raw_path.is_file():
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        if isinstance(raw, dict) and raw.get("coordinator_job_id") == job_id and raw.get("clips"):
            return raw
    return wait_for_raw_scene_clips(run, job_id, deadline_seconds=deadline)


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


def _money(value: Any) -> float | None:
    """Parse a price the API sends as a decimal string (``"1.20"``) or a number."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _estimate_usd(payload: dict[str, Any], *, fallback_usd: float = 1.20) -> float:
    """Return the estimate's dollars: the server's dated ``cost_estimate.total_usd`` first.

    Older answers carried a top-level total; neither -> ``fallback_usd``.
    """

    cost = payload.get("cost_estimate")
    if isinstance(cost, dict):
        total = _money(cost.get("total_usd"))
        if total is not None:
            return total
    for key in ("total_usd", "estimated_usd", "usd", "total_cost_usd"):
        value = _money(payload.get(key))
        if value is not None:
            return value
    return float(fallback_usd)


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
    series = load_series(desk)
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
        prompt=ensure_plan_prompt(prompt),
        preset_id=pid,
        preset_version=version,
        video_lane=video_lane,
        episode_ordinal=episode_ordinal,
        band=series.band,
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
    return (
        f"episode={state.episode_ordinal} phase={state.phase} spine={state.spine_id or '—'} "
        f"session={state.session_id}"
    )


def _first_episode_of_series(desk: Path, episode: int) -> bool:
    return episode == 1 and not load_series(desk).continuing


def envelope_line(desk: Path, *, episode: int, next_usd: float) -> str:
    """Say where the episode's spend stands against its envelope. A warning, never a stop.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    next_usd
        Money the next step would add.

    Returns
    -------
    str
        ``$X of $Y envelope`` with a ``!!`` warning past the envelope or past 2x.
    """

    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    envelope = envelope_usd(first_episode=_first_episode_of_series(desk, episode), band=series.band)
    projected = slot.spend_usd + next_usd
    line = f"${projected:.2f} of a ${envelope:.2f} envelope for ep{episode:02d}"
    if projected > envelope * 2:
        return f"!! {line}: past 2x. Warn the human and escalate before filming (warn only; nothing is blocked)."
    if projected > envelope:
        return f"!! {line}: over the envelope (warn only; say it to the human)."
    return line


def download_boards(
    desk: Path,
    state: ProductionState,
    spine: dict[str, Any],
    *,
    episode: int,
    sets: list[int] | None = None,
) -> list[tuple[int, Path]]:
    """Download an episode's current boards (or only ``sets``) as ``board-epNN-tK-vN.png`` and remember them.

    Parameters
    ----------
    desk
        Series desk.
    state
        Production state; its board paths and frame digests are updated (the caller saves it).
    spine
        Spine JSON after the drawing.
    episode
        Episode ordinal.
    sets
        Only these boards (a redraw); default every current board.

    Returns
    -------
    list[tuple[int, Path]]
        ``(set index, file)`` per board.
    """

    ep_dir = _episode_dir(desk, episode)
    (ep_dir / "boards").mkdir(parents=True, exist_ok=True)
    found = board_assets(spine, episode=episode)
    if sets is not None:
        found = [(index, url) for index, url in found if index in sets]
    if not found and sets is None:
        found = list(enumerate(board_urls_for_episode(spine, api_dir_for_episode(desk, episode), ordinal=episode), 1))
    made: list[tuple[int, Path]] = []
    fetch = httpx.Client(timeout=120.0)
    try:
        for index, url in found:
            made.append((index, download_to_versioned(fetch, url, ep_dir / "boards", f"board-ep{episode:02d}-t{index}")))
    finally:
        fetch.close()
    drawn = frames_by_set(spine, episode=episode)
    for index, path in made:
        state.board_paths[f"t{index}"] = str(path.relative_to(desk)) if path.is_relative_to(desk) else str(path)
        state.board_digests[f"ep{episode:02d}-t{index}"] = frames_digest(drawn.get(index, []))
    return made


def board_report(
    desk: Path,
    run: DramaApiRunSession,
    state: ProductionState,
    spine: dict[str, Any],
    *,
    episode: int,
    made: list[tuple[int, Path]],
    redraw: bool,
    clip_seconds: int,
) -> list[str]:
    """Book the boards, record each take's estimate, and say brightness, shot list and the take price.

    Brightness is information only: a dark board is the human's call.

    Parameters
    ----------
    desk
        Series desk.
    run
        Session (reads the exposure).
    state
        Production state (lane, spine).
    spine
        Spine JSON.
    episode
        Episode ordinal.
    made
        ``(set index, file)`` from :func:`download_boards`.
    redraw
        True for a redraw.
    clip_seconds
        Take length the estimate is priced at.

    Returns
    -------
    list[str]
        Printable lines: brightness, shot list, safe zones, cost, the take price.
    """

    series = load_series(desk)
    slot = episode_by_ordinal(series, episode)
    desk_takes = {take.take_id for take in slot.takes}
    brightness: dict[int, float] = {}
    try:
        exposure = measure_board_exposure(run, spine_id=state.spine_id or "", episode=episode)
        brightness = {
            int(board.get("set_index") or 1): float(board["mean_percent"])
            for board in exposure.get("boards") or []
            if isinstance(board, dict) and board.get("mean_percent") is not None
        }
    except SystemExit as exc:
        run.emit("board_exposure_unavailable", detail=str(exc)[:300])
    cast_count = len([card for card in spine.get("cast") or [] if isinstance(card, dict)])
    references = reference_images_ceiling(cast_count)
    take_price = lane_take_usd(state.video_lane, clip_seconds, on=date.today(), reference_images=references)
    lines: list[str] = []
    for index, path in made:
        take_id = f"t{index}"
        on_desk = take_id in desk_takes
        record_spend(desk, episode=episode, usd=float(STILL_USD), take_id=take_id if on_desk else None)
        if on_desk and take_price is not None:
            record_estimate(desk, episode=episode, take_id=take_id, usd=take_price)
        luma, source = (brightness[index], "server") if index in brightness else (
            measure_board_luma(path).mean_percent,
            "measured here",
        )
        lines.append(f"{take_id} board {path.name}: brightness {luma:.1f}% ({source}). Information only.")
    lines += shot_list_lines(spine, episode=episode, sets=[index for index, _ in made])
    cost = float(STILL_USD) * len(made)
    what = "Redrew" if redraw else "Drew"
    lines.append(f"{what} {len(made)} board(s): ${cost:.2f} booked.")
    if take_price is None:
        lines.append(f"{lane_label(state.video_lane)} has no verified price in the table; the take is not estimated.")
    else:
        lines.append(
            f"A take will cost about ${take_price:.2f} on {lane_label(state.video_lane)} "
            f"({clip_seconds} s, up to {references} reference images)."
        )
    return lines


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
            effective_prompt = ensure_plan_prompt(state.prompt)
            if effective_prompt != state.prompt:
                state.prompt = effective_prompt
                save_production(desk, state)
            spine_id, plan = stages.start_draft(
                run,
                prompt=effective_prompt,
                preset_id=state.preset_id,
                preset_version=state.preset_version,
                band=state.band,
                video_lane=state.video_lane,
                cut_tempo=cfg.cut_tempo,
                spoken_language=cfg.spoken_language,
                locale=cfg.locale,
                deadline_seconds=cfg.poll_plan_deadline_seconds,
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
            spine = run.spine(spine_id)
            save_spine_snapshot(desk, ep, spine)
            counts = sync_spine_lines(desk, spine, episode=ep)
            _note(ep_dir, f"Draft complete (episode 1 alone). spine_id={spine_id} plan={plan.get('status')}")
            gate = script_gate_text(desk, spine, episode=ep)
            return StepResult(
                state.phase,
                f"Draft done. spine_id={spine_id}. {sum(counts.values())} lines on the desk.\n{gate}\n"
                "Next: run step again for cast plates (the script gate comes after the plates).",
                (),
            )

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
            api_dir = api_dir_for_episode(desk, ep)
            (ep_dir / "plates").mkdir(parents=True, exist_ok=True)
            fetch = httpx.Client(timeout=120.0)
            try:
                for index, url in enumerate(cast_plate_urls(spine, api_dir), start=1):
                    path = download_to_versioned(fetch, url, ep_dir / "plates", f"plate-ep{ep:02d}-{index}")
                    paths.append(str(path))
            finally:
                fetch.close()
            if paths:
                record_spend(desk, episode=ep, usd=round(float(STILL_USD) * len(paths), 2))
            state.phase = "wait_plates"
            save_production(desk, state)
            _note(ep_dir, f"Cast enrol complete: {len(paths)} plate(s), ${float(STILL_USD) * len(paths):.2f}. Open plates/ and approve.")
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
            return StepResult(state.phase, f"Waiting on human gate `{gate}` (episode {ep}). Use fictora-produce approve.", ())

        if state.phase == "ready_boards_enrol":
            stages.enrol_boards(
                run,
                spine_id=state.spine_id or "",
                prompt=state.prompt,
                preset_id=state.preset_id,
                preset_version=state.preset_version,
                video_lane=state.video_lane,
                cut_tempo=cfg.cut_tempo,
                tag=f"ep{ep}",
                episode=ep,
                deadline_seconds=cfg.poll_boards_deadline_seconds,
            )
            spine = run.spine(state.spine_id or "")
            save_spine_snapshot(desk, ep, spine)
            state.board_paths = {}
            made = download_boards(desk, state, spine, episode=ep)
            if not made:
                raise RuntimeError(f"the boards job completed but the spine has no board for {episode_id_for(spine, ep)}")
            paths.extend(str(path) for _, path in made)
            report = board_report(
                desk, run, state, spine, episode=ep, made=made, redraw=False, clip_seconds=cfg.clip_duration_seconds
            )
            state.exposure_accept_dim = False
            state.phase = "wait_board"
            save_production(desk, state)
            _note(ep_dir, "Boards drawn: " + "; ".join(report[: len(made)]))
            return StepResult(
                state.phase,
                "\n".join(report)
                + "\nNext: look at the board and the shot list; check its rows follow the shot plan. "
                "Say yes (`fictora-produce approve --gate board`) or redraw one (`redraw-board --take tN --cause ...`).",
                tuple(paths),
            )

        if state.phase == "ready_estimate":
            estimate = stages.estimate_batch(run, spine_id=state.spine_id or "", episode=ep)
            spine = run.spine(state.spine_id or "")
            slot = episode_by_ordinal(load_series(desk), ep)
            cast_count = len([card for card in spine.get("cast") or [] if isinstance(card, dict)])
            per_take = lane_take_usd(
                state.video_lane,
                cfg.clip_duration_seconds,
                on=date.today(),
                reference_images=reference_images_ceiling(cast_count),
            )
            table = round(per_take * len(slot.takes), 2) if per_take is not None else cfg.fallback_estimate_usd
            cost = estimate.get("cost_estimate") if isinstance(estimate.get("cost_estimate"), dict) else None
            if cost is not None and _money(cost.get("total_usd")) is not None:
                state.estimate_usd = _estimate_usd(estimate, fallback_usd=table)
                source = f"server estimate priced {cost.get('priced_on')} ({cost.get('takes')} take(s))"
            else:
                state.estimate_usd = table
                source = f"price table ({lane_label(state.video_lane)}, {cfg.clip_duration_seconds} s a take)"
            state.phase = "wait_spend"
            save_production(desk, state)
            budget = envelope_line(desk, episode=ep, next_usd=state.estimate_usd)
            _note(ep_dir, f"Estimate ${state.estimate_usd:.2f} before take ({source}). {budget}")
            return StepResult(
                state.phase,
                f"Estimate ${state.estimate_usd:.2f} for episode {ep} ({source}).\n{budget}\n"
                "Human yes, then `fictora-produce step --confirm-spend`.",
                (),
            )

        if state.phase == "ready_video":
            return _film(desk, run, state, cfg=cfg, paths=paths)

        if state.phase == "complete":
            return StepResult(state.phase, f"Episode {ep} already complete. video={state.last_delivery_url}", ())

        raise RuntimeError(f"unknown phase: {state.phase}")
    except SystemExit as exc:
        state.phase = "failed"
        state.last_error = str(exc)
        save_production(desk, state)
        raise
    finally:
        run.client.close()


def _film(desk: Path, run: DramaApiRunSession, state: ProductionState, *, cfg: Any, paths: list[str]) -> StepResult:
    """Film the current episode (or pick up its job), collect every take raw with its take facts, book spend."""

    ep = state.episode_ordinal
    ep_dir = _episode_dir(desk, ep)
    api_dir = api_dir_for_episode(desk, ep)
    raw = _resume_raw_clips(api_dir, run=run, state=state, deadline=cfg.poll_video_deadline_seconds)
    delivery: dict[str, Any] | None = None
    if raw is None:
        state.video_enrolled_suffix = state.video_idempotency_suffix
        save_production(desk, state)
        result = stages.enrol_video(
            run,
            spine_id=state.spine_id or "",
            prompt=state.prompt,
            preset_id=state.preset_id,
            preset_version=state.preset_version,
            caption_style=cfg.caption_style,
            api_captions=cfg.api_captions,
            video_lane=state.video_lane,
            clip_duration_seconds=cfg.clip_duration_seconds,
            cut_tempo=cfg.cut_tempo,
            video_idempotency_suffix=state.video_idempotency_suffix,
            poll_deadline_seconds=cfg.poll_video_deadline_seconds,
            episode=ep,
        )
        raw = result["raw_scenes"]
        delivery = result.get("delivery")
    elif cfg.api_captions:
        delivery = stages.fetch_delivery_optional(run, str(raw.get("coordinator_job_id") or ""))
    video_job_id = str(raw.get("coordinator_job_id") or "")
    spine = run.spine(state.spine_id or "")
    save_spine_snapshot(desk, ep, spine)
    clips = episode_clips(raw, episode_id=episode_id_for(spine, ep))
    if not clips:
        raise RuntimeError(f"the take job {video_job_id} completed but no take for episode {ep} came back")
    slot = episode_by_ordinal(load_series(desk), ep)
    take_ids = [take.take_id for take in slot.takes]
    (ep_dir / "takes").mkdir(parents=True, exist_ok=True)
    today = date.today()
    fetch = httpx.Client(timeout=300.0)
    booked = 0.0
    jobs: list[str] = []
    url: str | None = None
    try:
        for position, clip in enumerate(clips, start=1):
            index = clip.get("set_index") or position
            take_id = f"t{index}" if f"t{index}" in take_ids else (take_ids[position - 1] if position <= len(take_ids) else None)
            if take_id is None:
                run.emit("take_without_desk_slot", job_id=clip.get("job_id"), index=index)
                continue
            path = download_to_versioned(
                fetch, str(clip["url"]), ep_dir / "takes", f"take-ep{ep:02d}-{take_id}-raw", default_suffix=".mp4"
            )
            paths.append(str(path))
            facts = fetch_take_facts(run, str(clip["job_id"]), spine_id=state.spine_id)
            priced: tuple[float, str] | None = None
            if facts is not None:
                facts_path = next_versioned_path(api_dir, f"take-facts-ep{ep:02d}-{take_id}", ".json")
                facts_path.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                priced = take_facts_usd(facts, on=today)
            if priced is None:
                estimate = lane_take_usd(state.video_lane, cfg.clip_duration_seconds, on=today)
                priced = (estimate, "estimate from the price table") if estimate is not None else None
            if priced is not None:
                record_spend(desk, episode=ep, usd=priced[0], take_id=take_id)
                booked += priced[0]
            record_filmed(desk, episode=ep, take_id=take_id)
            jobs.append(f"{take_id}: take job `{clip['job_id']}`" + (f" ({priced[1]}, ${priced[0]:.2f})" if priced else " (not priced; book by hand)"))
        url = _delivery_video_url(delivery) if delivery else None
        if url:
            path = download_to_versioned(fetch, url, ep_dir / "takes", f"take-ep{ep:02d}-api-captioned", default_suffix=".mp4")
            paths.append(str(path))
    finally:
        fetch.close()
    state.last_delivery_url = url or str(clips[0]["url"])
    state.last_video_job_id = video_job_id or None
    state.phase = "complete"
    save_production(desk, state)
    _note(ep_dir, f"Takes filmed: video job `{video_job_id}`; " + "; ".join(jobs) + f". Booked ${booked:.2f}.")
    follow = "arc --list (pick the series arc)" if ep == 1 else f"author --episode {ep + 1}"
    hint = (
        " Not done yet: after the human says Use it, run `fictora-produce finish --desk <desk> --episode "
        f"{ep} --take tK` per take (sound effects, music, mix, captions, mark; hosted post is off)."
    )
    return StepResult(
        state.phase,
        f"Episode {ep} filmed: {len(jobs)} take(s), ${booked:.2f} booked. Video job {video_job_id}.{hint}\n"
        + "\n".join(f"  {line}" for line in jobs)
        + f"\nNext episode: fictora-produce {follow} --desk <desk>.",
        tuple(paths),
    )


def approve_gate(desk: Path, *, gate: str, path: Path | None = None, accept_dim: bool | None = None) -> StepResult:
    """Record a human gate and run the matching API approve when needed."""

    desk = desk.expanduser().resolve()
    state = load_production(desk)
    run = _open_run(desk, state)
    ep = state.episode_ordinal

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
            spine = stages.approve_script(run, spine_id=state.spine_id or "", episode=ep)
            save_spine_snapshot(desk, ep, spine)
            record = record_script_gate(desk, episode=ep)
            state.phase = "ready_boards_enrol"
            save_production(desk, state)
            warning = f"\n!! {record.note}" if record.note and record.note.startswith("WARNING") else ""
            return StepResult(
                state.phase,
                f"Episode {ep} script approved on the API. Next: fictora-produce step (boards).{warning}",
                (),
            )

        if gate == "board":
            if state.phase != "wait_board":
                raise RuntimeError(f"expected wait_board, got {state.phase}")
            spine = run.spine(state.spine_id or "")
            approve_episode_boards(run, spine_id=state.spine_id or "", spine=spine, episode=ep, accept_dim=bool(accept_dim))
            slot = episode_by_ordinal(load_series(desk), ep)
            recorded: list[str] = []
            for take in slot.takes:
                stored = state.board_paths.get(take.take_id)
                image = desk / stored if stored else path
                if image is None:
                    raise ValueError(f"no board recorded for {take.take_id}; pass --path to the board file reviewed")
                approve_board(desk, episode=ep, take_id=take.take_id, image=Path(image))
                recorded.append(take.take_id)
            state.phase = "ready_estimate"
            save_production(desk, state)
            return StepResult(
                state.phase,
                f"Board(s) approved for episode {ep} ({', '.join(recorded)}). Next: fictora-produce step (estimate).",
                (),
            )

        raise ValueError(f"unknown gate: {gate}")
    finally:
        run.client.close()
