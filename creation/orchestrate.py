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
import re
import shlex
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Sequence

import httpx

from creation.authoring_warnings import authoring_warnings, for_episode, warning_lines
from creation.brief_lines import brief_vs_spine_lines
from creation.new_cast import cast_owing_pictures
from creation.desk_media_urls import (
    board_urls_for_episode,
    cast_plate_urls,
    drawn_cast_rows,
)
from creation.harness import stages_gated as stages
from creation.harness.credentials import load_drama_api_credentials
from creation.harness.raw_video import (
    STEP_RAW_CLIPS,
    episode_clips,
    fetch_take_facts,
    wait_for_raw_scene_clips,
)
from creation.harness.run_context import save_run_meta
from creation.harness.session import DramaApiRunSession
from creation.harness.visual_first_ep1 import (
    approve_episode_boards,
    measure_board_exposure,
)
from creation.look_gate import look_gate_refusal
from creation.narrator_cast import NarratorQuestionOpen, settle_narrators
from creation.media_fetch import download_to_versioned
from creation.ops.floor import (
    approve_board,
    approve_series_gate,
    record_estimate,
    record_filmed,
    record_spend,
    set_take_lines,
    take_job_booked,
)
from creation.ops.floor import approve_script as record_script_gate
from creation.ops.luma import measure_board_luma
from creation.ops.notes import append_run_note
from creation.ops.state import (
    episode_by_ordinal,
    load_series,
    save_series,
    series_path,
    spine_take_seconds,
    sync_take_slots_to_spine,
)
from creation.plan_prompt import (
    _without_kit_directive,
    ensure_plan_prompt,
    narrator_warning,
    server_cast_floor,
)
from creation.post.take_facts import (
    cast_names_from,
    save_take_facts,
    shot_people_lines,
)
from creation.prices import (
    fal_billing_day,
    H3_MAX_R2V_ENDPOINT,
    H3_MAX_TURBO_I2V_ENDPOINT,
    H3_RESOLUTION,
    STILL_USD,
    envelope_usd,
    lane_endpoint,
    lane_label,
    lane_take_usd,
    reference_images_ceiling,
    server_lane,
    take_facts_usd,
    take_usd,
    video_usd_per_second,
)
from creation.production_config import load_production_config, save_production_config
from creation.production_state import (
    ProductionState,
    api_dir_for_episode,
    ensure_production,
    load_production,
    production_path,
    save_production,
)
from creation.touch_and_side import one_sided_side_lines, touch_owner_heads_up
from creation.spine_view import (
    beats_by_take,
    board_assets,
    board_inputs,
    episode_id_for,
    frames_by_set,
    named_cast_stop,
    frames_digest,
    script_lines,
    shot_list_lines,
    spoken_lines,
)
from creation.harness_rules import (
    adult_face_lines,
    film_stop_message,
    hands_on_sound_lines,
    hook_mouth_stop,
    locked_camera_line,
    near_touch_line,
    sparkle_adult_line,
    staging_contradiction_lines,
    thin_take_lines,
    young_creature_lines,
)
from creation.stranded_voice import explain_film_refusal, stranded_preflight
from creation.stylised_only import BriefNoticePause
from creation.voice_gate import film_refusal as voices_film_refusal
from creation.voice_mode import next_take_voices_line
from creation.music_blend import music_blend_line
from creation.rules_epoch import is_legacy
from creation.voice_gate import gate_text as voices_gate_text
from creation.voice_gate import pending_for_film as voices_pending_for_film


#: Phases whose ``step`` pays for drawings (cast plates, boards): held while a drawn look frame awaits its yes.
PAID_DRAWING_PHASES = frozenset({"ready_cast_enrol", "ready_boards_enrol"})


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


PRESETS_PATH = "/v1/art-style-presets"
"""Published Art Style Preset catalog (``{"presets": [{preset_id, version, label, scope, status}]}``)."""


def fetch_preset_rows() -> list[dict[str, Any]]:
    """Read the preset catalog from the API. One authenticated GET; spends nothing.

    Returns
    -------
    list[dict[str, Any]]
        Raw ``presets`` rows: every version, tenant rows still uploading included.
    """

    base, token = load_drama_api_credentials(_repo_root())
    probe = DramaApiRunSession(base_url=base, token=token, out_dir=_repo_root())
    try:
        return list(probe.get(PRESETS_PATH).get("presets") or [])
    finally:
        probe.client.close()


def _version_key(row: dict[str, Any]) -> tuple[int, ...]:
    parts = []
    for piece in str(row.get("version") or "0").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _ready(row: dict[str, Any]) -> bool:
    """Global rows carry no status; a tenant row is usable only once ``published``."""

    return row.get("status") in (None, "", "published")


def preset_catalog(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split catalog rows into usable presets and tenant presets not ready yet, newest version each.

    Parameters
    ----------
    rows
        ``GET /v1/art-style-presets`` ``presets`` rows.

    Returns
    -------
    tuple[list[dict[str, Any]], list[dict[str, Any]]]
        ``(ready, not_ready)``, each one row per ``preset_id`` sorted by id.
    """

    newest: dict[tuple[bool, str], dict[str, Any]] = {}
    for row in rows:
        pid = str(row.get("preset_id") or "")
        if not pid:
            continue
        key = (_ready(row), pid)
        if key not in newest or _version_key(row) > _version_key(newest[key]):
            newest[key] = row
    ready = [newest[k] for k in sorted(newest) if k[0]]
    ready_ids = {str(r["preset_id"]) for r in ready}
    waiting = [newest[k] for k in sorted(newest) if not k[0] and k[1] not in ready_ids]
    return ready, waiting


def published_preset_lines() -> list[str]:
    """Read the catalog and format it for ``fictora-produce presets``."""

    return preset_lines(fetch_preset_rows())


def preset_lines(rows: list[dict[str, Any]]) -> list[str]:
    """Format the catalog for ``fictora-produce presets``: one line per preset.

    Parameters
    ----------
    rows
        ``GET /v1/art-style-presets`` ``presets`` rows.

    Returns
    -------
    list[str]
        ``id  version  label``; tenant presets not ready yet are marked.
    """

    ready, waiting = preset_catalog(rows)
    if not ready and not waiting:
        return ["No presets are published."]
    width = max(len(str(r["preset_id"])) for r in (*ready, *waiting))
    out = [
        f"{str(r['preset_id']):<{width}}  {r.get('version') or '?':<8}  {r.get('label') or ''}".rstrip()
        for r in ready
    ]
    out += [
        f"{str(r['preset_id']):<{width}}  {r.get('version') or '?':<8}  {r.get('label') or ''}"
        f"  (not ready: {r.get('status')})"
        for r in waiting
    ]
    return out


def _pick_preset(rows: list[dict[str, Any]], preset_id: str) -> tuple[str, str]:
    ready, waiting = preset_catalog(rows)
    for row in ready:
        if row["preset_id"] == preset_id:
            return str(row["preset_id"]), str(row["version"])
    valid = ", ".join(str(r["preset_id"]) for r in ready) or "none"
    for row in waiting:
        if row["preset_id"] == preset_id:
            raise RuntimeError(
                f"preset {preset_id!r} is not ready yet (status {row.get('status')}). "
                f"Published presets: {valid}. List them with: uv run fictora-produce presets"
            )
    raise RuntimeError(
        f"preset not published: {preset_id!r}. Published presets: {valid}. "
        "List them with: uv run fictora-produce presets"
    )


def resolve_preset(preset_id: str) -> tuple[str, str]:
    """Check a preset id against the published catalog and pin its newest version.

    Parameters
    ----------
    preset_id
        Operator's ``--preset-id``.

    Returns
    -------
    tuple[str, str]
        ``(preset_id, version)``.

    Raises
    ------
    RuntimeError
        When the id is not published (the message lists the published ids).
    """

    return _pick_preset(fetch_preset_rows(), preset_id)


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
    for note in sync_desk_to_spine_takes(desk, spine):
        print(note)
    return desk / "api" / "spine.json"


def sync_desk_to_spine_takes(desk: Path, spine: dict[str, Any]) -> list[str]:
    """Match the desk's take slots and take length to the story the server films.

    A 4:3 letterbox story films 10-second takes (fictora-drama #604; 7-second at 15 s, #617): three a
    30 s episode, six a 60 s one. The desk opens slots from its band, so the
    spine's ``beats_per_storyboard_set`` sets the slot count
    (:func:`creation.ops.state.sync_take_slots_to_spine`), and on a 4:3 story
    ``clip_duration_seconds`` follows the spine too, so estimates price the
    take the server films. Portrait desks are left exactly as they were.

    Parameters
    ----------
    desk
        Series desk.
    spine
        ``GET /v1/spines/{id}`` JSON.

    Returns
    -------
    list[str]
        Plain lines saying what changed; empty when nothing did.
    """

    if not series_path(desk).is_file():
        return []
    series = load_series(desk)
    notes = sync_take_slots_to_spine(series, spine)
    if notes:
        save_series(desk, series)
    seconds = spine_take_seconds(spine, series.band)
    if seconds is not None:
        cfg = load_production_config(desk)
        if cfg.clip_duration_seconds != seconds:
            notes.append(
                f"Letterbox story: takes film {seconds} s on the server; production config "
                f"clip_duration_seconds {cfg.clip_duration_seconds} → {seconds}."
            )
            save_production_config(desk, replace(cfg, clip_duration_seconds=seconds))
    return notes


def sync_spine_lines(
    desk: Path, spine: dict[str, Any], *, episode: int
) -> dict[str, int]:
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
    for take_id, beats in zip(
        take_ids,
        beats_by_take(spine, episode=episode, take_count=len(take_ids)),
        strict=True,
    ):
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
    text = "\n".join(
        script_lines(
            spine, episode=episode, take_ids=[take.take_id for take in slot.takes]
        )
    )
    camera = locked_camera_line(spine, episode=episode)
    if camera:
        text += "\n" + camera
    notes = [
        *thin_take_lines(spine, episode=episode, take_count=len(slot.takes)),
        *staging_contradiction_lines(spine, episode=episode),
        *adult_face_lines(spine),
        *hands_on_sound_lines(spine, episode=episode),
    ]
    near = near_touch_line(spine, episode=episode)
    if near:
        notes.append(near)
    sparkle = sparkle_adult_line(spine, episode=episode)
    if sparkle:
        notes.append(sparkle)
    if notes:
        text += "\n" + "\n".join(notes)
    return text


def _resume_raw_clips(
    api_dir: Path,
    *,
    run: DramaApiRunSession,
    state: ProductionState,
    deadline: float,
    expected_clips: int | None = None,
) -> dict[str, Any] | None:
    """Pick up the take job this desk already paid for, or ``None`` to enrol a new one.

    Only the job enrolled with the desk's current retry suffix is resumed, so a
    ``retry-video --new-paid-take`` enrols its new take instead of reading the old one.
    """

    enrol_path = api_dir / "16_video_enrol.json"
    if not enrol_path.is_file():
        return None
    enrolled_with = (
        state.video_enrolled_suffix if state.video_enrolled_suffix is not None else ""
    )
    if enrolled_with != state.video_idempotency_suffix:
        return None
    try:
        enrol = json.loads(enrol_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    job_id = enrol.get("job_id") if isinstance(enrol, dict) else None
    if not isinstance(job_id, str) or not job_id.strip():
        return None
    raw_path = api_dir / STEP_RAW_CLIPS
    if raw_path.is_file():
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        # Only a record written once the job was complete counts (an older one could hold half a take).
        if (
            isinstance(raw, dict)
            and raw.get("coordinator_job_id") == job_id
            and raw.get("coordinator_status") == "completed"
            and raw.get("clips")
            and (expected_clips is None or len(raw["clips"]) >= expected_clips)
        ):
            return raw
    return wait_for_raw_scene_clips(
        run, job_id, deadline_seconds=deadline, expected_clips=expected_clips
    )


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


def table_estimate_usd(
    state: ProductionState, cfg: Any, *, cast_count: int, takes: int
) -> float:
    """Price ``takes`` takes from the dated table, on the endpoint the server films on.

    The server's last-named endpoint wins; with none, the lane pin's default
    (H3 Max Turbo for ``minimax-h3``). An endpoint with no verified price
    falls back to ``fallback_estimate_usd`` a take when the desk sets one, else
    to the Turbo rate for the take length.

    Parameters
    ----------
    state
        Desk production state (lane pin and the server's last-named endpoint).
    cfg
        Desk production config (take length, optional fallback).
    cast_count
        Drawn cast cards on the story (R2V reference-image ceiling); voice-only
        cast sends no plate, so it is not counted.
    takes
        Takes to price.

    Returns
    -------
    float
        Dollars for all ``takes``.
    """

    server = state.server_lane()
    lane = lane_endpoint(state.video_lane, server=server)
    references = reference_images_ceiling(cast_count, lane[0] if lane else "")
    seconds = cfg.clip_duration_seconds
    per_take = lane_take_usd(
        state.video_lane,
        seconds,
        on=fal_billing_day(),
        reference_images=references,
        server=server,
    )
    if per_take is None:
        fallback = getattr(cfg, "fallback_estimate_usd", None)
        per_take = (
            float(fallback)
            if fallback is not None
            else take_usd(
                H3_MAX_TURBO_I2V_ENDPOINT, H3_RESOLUTION, seconds, on=fal_billing_day()
            )
        )
    return round(float(per_take or 0.0) * takes, 2)


def lane_rate_words(state: ProductionState, *, on: date) -> str:
    """Name what a take films on and its per-second price (``H3 Max Turbo 768P at $0.02/s``).

    Parameters
    ----------
    state
        Desk production state (lane pin and the server's last-named endpoint).
    on
        Filming day (the Turbo promo ends 2026-09-30).

    Returns
    -------
    str
        Lane, resolution and dollars a second; says so when the lane has no verified rate.
    """

    server = state.server_lane()
    label = lane_label(state.video_lane, server=server)
    endpoint = lane_endpoint(state.video_lane, server=server)
    if endpoint is None:
        return f"{label} (no verified $/s)"
    rate = video_usd_per_second(endpoint[0], endpoint[1], on=on)
    if rate is None:
        return f"{label} {endpoint[1]} (no verified $/s)"
    return f"{label} {endpoint[1]} at ${rate}/s"


def price_estimate(
    state: ProductionState,
    cfg: Any,
    estimate: dict[str, Any],
    *,
    cast_count: int,
    takes: int,
    on: date | None = None,
) -> tuple[float, str, list[str]]:
    """Price an estimate answer, naming the lane and $/s, and warn loudly when the server's dollars are missing or do not add up.

    The server's dated ``cost_estimate.total_usd`` is the estimate: it is
    priced on ``priced_on``, the day fal bills on (fictora-drama #534), and
    the kit names the rate for that same day. Kit #68 re-priced it on the
    desk's own calendar day and showed the higher number; fal's day turns
    over at 07:00Z (12:30 IST), so on the morning of a rate change in India
    that over-quoted a correct promo estimate and warned (L-20261001-7). Now
    a ``!!`` line appears only when the server's own numbers disagree with
    each other (rate x seconds against the video dollars, video + stills
    against the total), and then the higher of the total and the re-added
    sum is shown, so the human never says yes to the lower number. Without
    dollars (the server refused or skipped the estimate, or answered with no
    dollars) the kit's own price table is used on fal's billing day, and a
    ``!!`` line says so. A lane with no verified rate adds a second ``!!``
    line naming the rate that was used instead.

    Parameters
    ----------
    state
        Desk production state; call after ``remember_server_lane`` so the lane is current.
    cfg
        Desk production config (take length, optional fallback).
    estimate
        The ``batches/estimate`` answer (``estimate_skipped`` when the server refused it).
    cast_count
        Cast cards on the story (R2V reference-image ceiling).
    takes
        Takes being priced.
    on
        Billing day for the kit's own table (default: :func:`fal_billing_day` now).

    Returns
    -------
    tuple[float, str, list[str]]
        Dollars, a source phrase (always lane + $/s), and warning lines (empty
        when the server's dollars add up).
    """

    today = on or fal_billing_day()
    seconds = cfg.clip_duration_seconds
    cost = (
        estimate.get("cost_estimate")
        if isinstance(estimate.get("cost_estimate"), dict)
        else None
    )
    if cost is not None and _money(cost.get("total_usd")) is not None:
        usd = _estimate_usd(estimate, fallback_usd=0.0)
        priced_on = _iso_day(cost.get("priced_on")) or today
        rate = lane_rate_words(state, on=priced_on)
        server_rate = _money(cost.get("usd_per_second"))
        if server_rate is not None:
            label = lane_label(state.video_lane, server=state.server_lane())
            rate = f"{label} {cost.get('video_resolution') or H3_RESOLUTION} at ${server_rate:g}/s"
        source = f"server estimate priced {cost.get('priced_on')} ({cost.get('takes')} take(s)); {rate}, {seconds} s a take"
        mismatch = _server_sum_mismatch(cost)
        if mismatch is None:
            return usd, source, []
        added, how = mismatch
        warning = (
            f"!! SERVER ESTIMATE DOES NOT ADD UP: the server says ${usd:.2f} total, but {how}, "
            f"which is ${added:.2f}. Showing the higher, ${max(usd, added):.2f}: check it before the human says yes."
        )
        return max(usd, added), source, [warning]
    rate = lane_rate_words(state, on=today)
    table = table_estimate_usd(state, cfg, cast_count=cast_count, takes=takes)
    if estimate.get("estimate_skipped"):
        why = (
            f"the server refused it: {str(estimate.get('detail') or 'no detail')[:160]}"
        )
    elif cost is not None:
        why = "the server named the lane but gave no dollars"
    else:
        why = "the server answer carried no dollars"
    warnings = [
        f"!! SERVER ESTIMATE FAILED ({why}). This number is the kit's LOCAL price table, not the server's: "
        "check it before the human says yes."
    ]
    server = state.server_lane()
    if lane_take_usd(state.video_lane, seconds, on=today, server=server) is None:
        fallback = getattr(cfg, "fallback_estimate_usd", None)
        instead = (
            f"the desk's fallback ${float(fallback):.2f} a take"
            if fallback is not None
            else f"the H3 Max Turbo rate (${video_usd_per_second(H3_MAX_TURBO_I2V_ENDPOINT, H3_RESOLUTION, on=today)}/s)"
        )
        warnings.append(
            f"!! {lane_label(state.video_lane, server=server)} has no verified price; priced at {instead}."
        )
    source = f"price table ({lane_label(state.video_lane, server=server)}, {seconds} s a take); {rate}"
    return table, source, warnings


def _iso_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _server_sum_mismatch(cost: dict[str, Any]) -> tuple[float, str] | None:
    """Check the server's estimate against its own parts; ``None`` when they add up.

    The server sends ``video_usd = usd_per_second x billed_seconds`` (plus
    reference images past the free ones on R2V), ``stills_usd = still_usd_each
    x (plates + boards)`` and ``total_usd = video_usd + stills_usd``. A part
    the answer leaves out is not checked.

    Returns
    -------
    tuple[float, str] | None
        The total re-added from its parts and a phrase saying which sum is off.
    """

    total = _money(cost.get("total_usd"))
    rate = _money(cost.get("usd_per_second"))
    seconds = _money(cost.get("billed_seconds"))
    video = _money(cost.get("video_usd"))
    stills = _money(cost.get("stills_usd"))
    each = _money(cost.get("still_usd_each"))
    if total is None:
        return None
    problems: list[str] = []
    if rate is not None and seconds is not None:
        by_rate = round(rate * seconds, 2)
        r2v = str(cost.get("video_endpoint_id") or "") == H3_MAX_R2V_ENDPOINT
        # R2V adds reference images on top of the seconds; nothing else does.
        if video is None:
            video = by_rate
        elif video < by_rate - 0.005 or (not r2v and abs(video - by_rate) >= 0.005):
            problems.append(f"{seconds:g} s at ${rate:g}/s is ${by_rate:.2f}")
            video = by_rate
    if each is not None:
        count = int(cost.get("plates") or 0) + int(cost.get("boards") or 0)
        by_count = round(each * count, 2)
        if stills is not None and abs(stills - by_count) >= 0.005:
            problems.append(f"{count} still(s) at ${each:.2f} is ${by_count:.2f}")
        stills = by_count
    if video is None or stills is None:
        return None
    added = round(video + stills, 2)
    if not problems and abs(added - total) < 0.005:
        return None
    problems.append(f"video ${video:.2f} + stills ${stills:.2f}")
    return added, "; ".join(problems)


def _estimate_usd(payload: dict[str, Any], *, fallback_usd: float) -> float:
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
    preset: tuple[str, str] | None = None,
) -> ProductionState:
    """Attach API orchestration to an existing series desk.

    Parameters
    ----------
    desk
        Series desk.
    prompt
        The premise.
    preset_id
        Operator's ``--preset-id``; checked against the published catalog.
    video_lane
        Video lane pinned on the draft.
    episode_ordinal
        Episode the binding is for.
    preset
        ``(preset_id, version)`` already resolved by :func:`resolve_preset`; skips a second catalog read.

    Returns
    -------
    ProductionState
        The saved binding. A desk still at ``new`` whose brief changed prints one
        line to stderr: the next ``step`` drafts the new brief.
    """

    desk = desk.expanduser().resolve()
    series = load_series(desk)
    pid, version = preset or resolve_preset(preset_id)
    before = load_production(desk) if production_path(desk).is_file() else None
    api_dir = api_dir_for_episode(desk, episode_ordinal)
    api_dir.mkdir(parents=True, exist_ok=True)
    state = ensure_production(
        desk,
        prompt=ensure_plan_prompt(prompt),
        preset_id=pid,
        preset_version=version,
        video_lane=video_lane,
        episode_ordinal=episode_ordinal,
        band=series.band,
    )
    save_run_meta(
        api_dir, session_id=state.session_id, preset_id=pid, preset_version=version
    )
    save_production(desk, state)
    if (
        before is not None
        and state.phase == "new"
        and _without_kit_directive(before.prompt.strip())
        != _without_kit_directive(state.prompt.strip())
    ):
        # A re-brief after a pause: say plainly that the next step sends these words (L-20261002).
        print(
            f"The brief changed: the next `fictora-produce step --desk {desk}` drafts the new brief "
            "(a new draft; the earlier one is not reused).",
            file=sys.stderr,
        )
    return state


def unbound_desk_recovery(
    desk: Path, *, prompt_arg: str, preset_id: str, just_made: bool = False
) -> str:
    """Say how to carry on when ``start`` finds its dated desk already on disk (L-20260930-13).

    Parameters
    ----------
    desk
        The desk ``start`` would have made.
    prompt_arg
        The operator's ``--prompt`` exactly as typed (``@file`` stays ``@file``).
    preset_id
        The preset ``start`` was asked for.
    just_made
        True when this very ``start`` made the desk and then failed to bind it.

    Returns
    -------
    str
        The exact ``bind`` command for a desk an earlier ``start`` left unbound, or the
        ``step`` command for a desk that is already bound.
    """

    if production_path(desk).is_file():
        state = load_production(desk)
        return (
            f"series desk already exists and is already bound (session {state.session_id}, "
            f"phase {state.phase}): {desk}\n"
            f"Carry on with: uv run fictora-produce step --desk {shlex.quote(str(desk))}\n"
            "Or give the series another name to start a new desk."
        )
    prompt = (
        shlex.quote(prompt_arg)
        if len(prompt_arg) <= 120
        else "@<the same premise file>"
    )
    command = (
        f"uv run fictora-produce bind --desk {shlex.quote(str(desk))} --prompt {prompt} "
        f"--preset-id {shlex.quote(preset_id)}"
    )
    why = (
        "the desk was made but not bound"
        if just_made
        else "series desk already exists but was never bound (an earlier start stopped "
        "before binding, e.g. on a wrong preset id)"
    )
    return (
        f"{why}: {desk}\n"
        f"Finish it with: {command}\n"
        "(add any production flags you gave start, such as --cut-tempo or --language)\n"
        "Or delete that folder and run start again."
    )


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
    envelope = envelope_usd(
        first_episode=_first_episode_of_series(desk, episode), band=series.band
    )
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
        Production state; its board paths, frame digests and board inputs are updated (the caller saves it).
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
        found = list(
            enumerate(
                board_urls_for_episode(
                    spine, api_dir_for_episode(desk, episode), ordinal=episode
                ),
                1,
            )
        )
    made: list[tuple[int, Path]] = []
    fetch = httpx.Client(timeout=120.0)
    try:
        for index, url in found:
            made.append(
                (
                    index,
                    download_to_versioned(
                        fetch, url, ep_dir / "boards", f"board-ep{episode:02d}-t{index}"
                    ),
                )
            )
    finally:
        fetch.close()
    drawn = frames_by_set(spine, episode=episode)
    take_count = len(episode_by_ordinal(load_series(desk), episode).takes)
    for index, path in made:
        key = f"ep{episode:02d}-t{index}"
        state.board_paths[f"t{index}"] = (
            str(path.relative_to(desk)) if path.is_relative_to(desk) else str(path)
        )
        state.board_digests[key] = frames_digest(drawn.get(index, []))
        state.board_inputs[key] = board_inputs(
            spine, episode=episode, set_index=index, take_count=take_count
        )
        if key in state.boards_stale:
            state.boards_stale.remove(key)
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
        exposure = measure_board_exposure(
            run, spine_id=state.spine_id or "", episode=episode
        )
        brightness = {
            int(board.get("set_index") or 1): float(board["mean_percent"])
            for board in exposure.get("boards") or []
            if isinstance(board, dict) and board.get("mean_percent") is not None
        }
    except SystemExit as exc:
        run.emit("board_exposure_unavailable", detail=str(exc)[:300])
    cast_count = len(drawn_cast_rows(spine))
    lane = lane_endpoint(state.video_lane, server=state.server_lane())
    references = reference_images_ceiling(cast_count, lane[0] if lane else "")
    take_price = lane_take_usd(
        state.video_lane,
        clip_seconds,
        on=fal_billing_day(),
        reference_images=references,
        server=state.server_lane(),
    )
    lines: list[str] = []
    for index, path in made:
        take_id = f"t{index}"
        on_desk = take_id in desk_takes
        record_spend(
            desk,
            episode=episode,
            usd=float(STILL_USD),
            take_id=take_id if on_desk else None,
            unit="board",
        )
        if on_desk and take_price is not None:
            record_estimate(desk, episode=episode, take_id=take_id, usd=take_price)
        luma, source = (
            (brightness[index], "server")
            if index in brightness
            else (
                measure_board_luma(path).mean_percent,
                "measured here",
            )
        )
        lines.append(
            f"{take_id} board {path.name}: brightness {luma:.1f}% ({source}). Information only."
        )
    lines += shot_list_lines(spine, episode=episode, sets=[index for index, _ in made])
    cost = float(STILL_USD) * len(made)
    what = "Redrew" if redraw else "Drew"
    lines.append(f"{what} {len(made)} board(s): ${cost:.2f} booked.")
    if take_price is None:
        label = lane_label(state.video_lane, server=state.server_lane())
        lines.append(
            f"{label} has no verified price in the table; the take is not estimated."
        )
    else:
        label = lane_label(state.video_lane, server=state.server_lane())
        what_goes = (
            f"up to {references} reference images"
            if lane and lane[0] == H3_MAX_R2V_ENDPOINT
            else "it opens on this board; cast plates are not sent"
        )
        rate = lane_rate_words(state, on=fal_billing_day())
        lines.append(
            f"A take will cost about ${take_price:.2f} on {rate} ({clip_seconds} s, {what_goes})."
        )
    return lines


def step_retry_unit(episode: int, phase: str) -> str:
    """Return the ``attempts`` unit that counts ``retry-step`` runs of one stage of one episode.

    Parameters
    ----------
    episode
        Episode ordinal.
    phase
        The stage's ready phase (``ready_boards_enrol`` ...).

    Returns
    -------
    str
        ``step-retry-epNN-<phase>``.
    """

    return f"step-retry-ep{episode:02d}-{phase}"


def step_retry_prefix(state: ProductionState) -> str | None:
    """Return the idempotency prefix for the current stage after a ``retry-step``, else ``None``.

    Built from the desk's stable prefix and the retry count, so a retried stage
    never reuses the failed job's key, and an interrupted retry run again picks
    up its own job instead of paying twice.

    Parameters
    ----------
    state
        Production state.

    Returns
    -------
    str | None
        ``<desk prefix>-step-retry-epNN-<phase>-rN`` or ``None`` when the stage was never retried.
    """

    unit = step_retry_unit(state.episode_ordinal, state.phase)
    count = state.attempts.get(unit, 0)
    if not count:
        return None
    return f"{state.idempotency_prefix}-{unit}-r{count}"


def draft_unit(episode: int) -> str:
    """Return the ``pending`` unit that holds an accepted draft while its plan job runs.

    Parameters
    ----------
    episode
        Episode ordinal.

    Returns
    -------
    str
        ``draft-epNN``.
    """

    return f"draft-ep{episode:02d}"


def draft_key_prefix(state: ProductionState) -> str:
    """Return the idempotency prefix the draft is sent under: fixed by the desk, new per retry.

    A re-run of ``step`` after a dead session sends the same key, so the server
    answers with the draft it already accepted instead of authoring (and
    charging) a second one (L-20260926-4). ``retry-step`` bumps the stage's
    retry count, which gives a deliberate re-draft a new key
    (:func:`step_retry_prefix`). ``start_draft`` adds the brief's hash
    (``-b<hash>``), so an edited brief is a new key too (L-20261002).

    Parameters
    ----------
    state
        Production state (its ``idempotency_prefix`` is stable for the desk).

    Returns
    -------
    str
        ``<desk prefix>-epNN`` or, after ``retry-step``, ``<desk prefix>-step-retry-epNN-new-rN``.
    """

    return step_retry_prefix(state) or (
        f"{state.idempotency_prefix}-ep{state.episode_ordinal:02d}"
    )


def run_step(
    desk: Path,
    *,
    confirm_spend: bool = False,
    accept_notices: Sequence[str] = (),
    narrator_heard_only: Sequence[str] = (),
    narrator_on_screen: Sequence[str] = (),
    ask: Callable[[str], str] | None = None,
) -> StepResult:
    """Run the next automated API step for the current phase.

    Before a paid plate or board drawing it refuses, with nothing sent, when the
    desk drew a look frame the look yes does not cover (:func:`look_gate_refusal`).
    It also stops before one while a character named like a narrator has no
    saved answer to "heard only, never seen?" (:mod:`creation.narrator_cast`):
    ``ask`` asks it, or the ``narrator_*`` names answer it; with neither the
    step stops and names both flags. Right after the draft it asks when it can,
    and otherwise says the next step will.

    The draft pauses, with nothing sent, on a brief that asks for a photoreal look
    or names a real person until the operator passes ``accept_notices``
    (:mod:`creation.stylised_only`).

    Parameters
    ----------
    desk
        Series desk.
    confirm_spend
        After the estimate gate, confirm spend and film the take.
    accept_notices
        Brief notice kinds the creator acknowledged (``style_not_available``,
        ``real_person_not_allowed``); read by the draft step only.
    narrator_heard_only, narrator_on_screen
        Names the operator answered "heard only" / "on screen" for.
    ask
        Prompt function for the narrator question, or ``None`` when nobody
        can answer.

    Returns
    -------
    StepResult
        Phase, message and files.
    """

    desk = desk.expanduser().resolve()
    state = load_production(desk)
    if state.phase in PAID_DRAWING_PHASES:
        refusal = look_gate_refusal(desk)
        if refusal:
            raise RuntimeError(refusal)
    cfg = load_production_config(desk)
    run = _open_run(desk, state)
    retry_prefix = step_retry_prefix(state)
    if retry_prefix:
        run.prefix = retry_prefix
    ep = state.episode_ordinal
    ep_dir = _episode_dir(desk, ep)
    paths: list[str] = []

    rerun = f"fictora-produce step --desk {desk}"
    try:
        if state.spine_id and (
            state.phase in PAID_DRAWING_PHASES
            or narrator_heard_only
            or narrator_on_screen
        ):
            # Before any plate or board is drawn: a narrator-named character
            # is heard only or drawn as the operator said, never as guessed.
            settle_narrators(
                desk, run, run.spine(state.spine_id), heard_only=narrator_heard_only,
                on_screen=narrator_on_screen, ask=ask, rerun=rerun, out=sys.stderr,
            )  # fmt: skip
        if state.phase == "new":
            # A server whose season bible holds one person (fictora-drama #582)
            # is asked for exactly the people the story needs; an older one for two.
            effective_prompt = ensure_plan_prompt(
                state.prompt, cast_floor=server_cast_floor(run)
            )
            if effective_prompt != state.prompt:
                state.prompt = effective_prompt
            narration = narrator_warning(effective_prompt, desk=desk)
            if narration:
                print(narration, file=sys.stderr)
                _note(ep_dir, narration.splitlines()[0].lstrip("! "))
            # Saved before anything is sent, so a desk that never stored its
            # prefix keeps this one and a re-run sends the same draft key.
            save_production(desk, state)
            unit = draft_unit(ep)

            def keep_accepted(record: dict[str, Any]) -> None:
                state.pending[unit] = record
                save_production(desk, state)

            try:
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
                    delivery_format=cfg.delivery_format,
                    deadline_seconds=cfg.poll_plan_deadline_seconds,
                    accept_notices=accept_notices,
                    desk=str(desk),
                    key_prefix=draft_key_prefix(state),
                    resume=state.pending.get(unit),
                    on_accepted=keep_accepted,
                )
            except BriefNoticePause as exc:
                _note(ep_dir, str(exc))
                # Not a failure: the desk stays at `new` until the creator chooses.
                raise RuntimeError(str(exc)) from exc
            except SystemExit as exc:
                pause = locked_lines_pause(str(exc.code), desk=desk)
                if pause is None:
                    raise
                _note(ep_dir, pause)
                # Not a failure: the desk stays at `new` so the edited brief drafts next (under
                # its own key). The record stays, marked, so the same brief re-run shows this
                # pause again from its plan job instead of paying for a second draft.
                record = state.pending.get(unit)
                if record is not None:
                    record["paused"] = LOCKED_LINES_OUT_OF_BOUNDS
                    save_production(desk, state)
                raise RuntimeError(pause) from exc
            state.spine_id = spine_id
            state.phase = "ready_cast_enrol"
            state.last_error = None
            state.pending.pop(unit, None)
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
            _note(
                ep_dir,
                f"Draft complete (episode 1 alone). spine_id={spine_id} plan={plan.get('status')}",
            )
            gate = script_gate_text(desk, spine, episode=ep)
            try:
                settle_narrators(
                    desk, run, spine, heard_only=narrator_heard_only, on_screen=narrator_on_screen,
                    ask=ask, rerun=rerun, out=sys.stderr,
                )  # fmt: skip
            except NarratorQuestionOpen as question:
                # The draft is done; the plates wait for the answer.
                gate += "\n" + str(question).replace(
                    "Stopped, nothing drawn: ", "Before the plates: ", 1
                )
            versus = "\n".join(brief_vs_spine_lines(state.prompt, spine, episode=ep))
            if versus:
                _note(ep_dir, versus)
                gate += "\n" + versus
            # Nudges only (fictora-drama #538): the job's own list, else the spine's for this episode.
            notes = "\n".join(
                warning_lines(
                    for_episode(
                        authoring_warnings(plan) or authoring_warnings(spine),
                        episode_id_for(spine, ep),
                        spine,
                    ),
                    spine,
                )
            )
            if notes:
                _note(ep_dir, notes)
                gate += "\n" + notes
            names = named_cast_stop(spine, episode=ep, desk=str(desk), heads_up=True)
            if names:
                _note(ep_dir, names)
                gate += "\n" + names
            # Free warnings before any picture is paid for (Three Payments Late).
            sided = one_sided_side_lines(spine)
            touches = touch_owner_heads_up(spine, episode=ep, desk=str(desk))
            for warning in [*sided, *([touches] if touches else [])]:
                _note(ep_dir, warning)
                gate += "\n" + warning
            return StepResult(
                state.phase,
                f"Draft done. spine_id={spine_id}. {sum(counts.values())} lines on the desk.\n{gate}\n"
                "Next: run step again for cast plates (the script gate comes after the plates).",
                (),
            )

        if state.phase == "ready_cast_enrol":
            if not state.spine_id:
                raise RuntimeError("spine_id missing")
            # From episode 2 this gate is for the characters the episode
            # brought in: only their pictures are new (the approved ones are
            # reused), and the enrol carries the episode's own tag.
            spine_now = run.spine(state.spine_id)
            young = young_creature_lines(spine_now)
            if young:
                raise RuntimeError(
                    "Stopped before the plates. Nothing was sent.\n" + "\n".join(young)
                )
            for line in [
                *adult_face_lines(spine_now),
                *one_sided_side_lines(spine_now),
            ]:
                _note(ep_dir, line)
            owing: set[str] | None = None
            if ep >= 2:
                owing = {
                    cast_id for cast_id, _ in cast_owing_pictures(spine_now, episode=ep)
                }
            stages.enrol_cast(
                run,
                spine_id=state.spine_id,
                prompt=state.prompt,
                preset_id=state.preset_id,
                preset_version=state.preset_version,
                video_lane=state.video_lane,
                tag=f"ep{ep}" if ep >= 2 else "ep1",
            )
            spine = run.spine(state.spine_id)
            api_dir = api_dir_for_episode(desk, ep)
            (ep_dir / "plates").mkdir(parents=True, exist_ok=True)
            fetch = httpx.Client(timeout=120.0)
            try:
                for index, url in enumerate(
                    cast_plate_urls(spine, api_dir, only=owing), start=1
                ):
                    path = download_to_versioned(
                        fetch, url, ep_dir / "plates", f"plate-ep{ep:02d}-{index}"
                    )
                    paths.append(str(path))
            finally:
                fetch.close()
            if paths:
                record_spend(
                    desk,
                    episode=ep,
                    usd=round(float(STILL_USD) * len(paths), 2),
                    unit=f"plates x{len(paths)}",
                )
            state.phase = "wait_plates"
            save_production(desk, state)
            _note(
                ep_dir,
                f"Cast enrol complete: {len(paths)} plate(s), ${float(STILL_USD) * len(paths):.2f}. Open plates/ and approve.",
            )
            return StepResult(
                state.phase,
                "Cast drawn. Human gate: review plates/, then `fictora-produce approve --gate plates`.",
                tuple(paths),
            )

        if state.phase in {"wait_plates", "wait_script", "wait_board", "wait_spend"}:
            gate = {
                "wait_plates": "plates",
                "wait_script": "script",
                "wait_board": "board",
                "wait_spend": "spend",
            }[state.phase]
            if state.phase == "wait_spend" and confirm_spend:
                # The voices gate: nothing films until every speaking voice has the human's yes.
                refused = voices_film_refusal(
                    desk, run.spine(state.spine_id or ""), episode=ep, run=run
                )
                if refused:
                    raise RuntimeError(refused)
                state.phase = "ready_video"
                save_production(desk, state)
                return run_step(desk, confirm_spend=False)
            voices = ""
            if state.phase == "wait_script" and state.spine_id:
                voices = voices_gate_text(
                    desk, run.spine(state.spine_id), episode=ep, run=run
                )
            return StepResult(
                state.phase,
                f"Waiting on human gate `{gate}` (episode {ep}). Use fictora-produce approve."
                + (f"\n{voices}" if voices else ""),
                (),
            )

        if state.phase == "ready_boards_enrol":
            # Name check and hook-mouth check, before paying for the boards.
            spine_now = run.spine(state.spine_id or "")
            stop = named_cast_stop(spine_now, episode=ep, desk=str(desk))
            if stop:
                _note(ep_dir, stop)
                raise RuntimeError(stop)
            mouth = hook_mouth_stop(spine_now, episode=ep)
            if mouth:
                raise RuntimeError(
                    "Stopped before the boards. " + mouth + " Nothing was sent."
                )
            conflicts = staging_contradiction_lines(spine_now, episode=ep)
            near = near_touch_line(spine_now, episode=ep)
            sparkle = sparkle_adult_line(spine_now, episode=ep)
            if conflicts or near or sparkle:
                raise RuntimeError(
                    "Stopped before the boards. Nothing was sent.\n"
                    + "\n".join(
                        [
                            *conflicts,
                            *([near] if near else []),
                            *([sparkle] if sparkle else []),
                        ]
                    )
                )
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
                raise RuntimeError(
                    f"the boards job completed but the spine has no board for {episode_id_for(spine, ep)}"
                )
            paths.extend(str(path) for _, path in made)
            report = board_report(
                desk,
                run,
                state,
                spine,
                episode=ep,
                made=made,
                redraw=False,
                clip_seconds=cfg.clip_duration_seconds,
            )
            state.exposure_accept_dim = False
            state.phase = "wait_board"
            save_production(desk, state)
            _note(ep_dir, "Boards drawn: " + "; ".join(report[: len(made)]))
            return StepResult(
                state.phase,
                "\n".join(report)
                + "\nNext: look at the board and the shot list; check its rows follow the shot plan. "
                "Say yes (`fictora-produce approve --gate board`) or redraw one (`redraw-board --take tN --note 'what is wrong'`).",
                tuple(paths),
            )

        if state.phase == "ready_estimate":
            estimate = stages.estimate_batch(
                run, spine_id=state.spine_id or "", episode=ep
            )
            state.remember_server_lane(server_lane(estimate))
            spine = run.spine(state.spine_id or "")
            stopped = film_stop_message(spine, episode=ep)
            if stopped:
                return StepResult(state.phase, stopped, ())
            slot = episode_by_ordinal(load_series(desk), ep)
            cast_count = len(drawn_cast_rows(spine))
            state.estimate_usd, source, warnings = price_estimate(
                state, cfg, estimate, cast_count=cast_count, takes=len(slot.takes)
            )
            warnings = [
                *stranded_preflight(spine, unit=f"ep{ep:02d}", desk=desk, episode=ep),
                *warnings,
            ]
            voices_line = next_take_voices_line(desk, run, spine)
            # One line, never a gate: the music families the show's score may play.
            # Frozen for desks created before 2026-10-06 (rules epoch): no line.
            blend_line = None if is_legacy(desk) else music_blend_line(run, spine)
            unvoiced = voices_pending_for_film(desk, spine, episode=ep, run=run)
            if unvoiced:
                who = ", ".join(v.name for v in unvoiced)
                warnings.append(
                    f"!! VOICES NOT APPROVED: {who}. Filming is refused until the human keeps or picks "
                    f"each voice (`fictora-produce voice --desk {desk} --list`)."
                )
            state.phase = "wait_spend"
            save_production(desk, state)
            budget = envelope_line(desk, episode=ep, next_usd=state.estimate_usd)
            _note(
                ep_dir,
                " ".join(
                    [
                        *warnings,
                        f"Estimate ${state.estimate_usd:.2f} before take ({source}). {budget}",
                    ]
                ),
            )
            scope = f"episode {ep} alone, {len(slot.takes)} take(s)" + (
                f"; episodes 1-{ep - 1} are not filmed or booked again"
                if ep > 1
                else ""
            )
            return StepResult(
                state.phase,
                "".join(f"{line}\n" for line in warnings)
                + f"{voices_line}\n"
                + (f"{blend_line}\n" if blend_line else "")
                + f"Estimate ${state.estimate_usd:.2f} for {scope} ({source}).\n{budget}\n"
                "Human yes, then `fictora-produce step --confirm-spend`.",
                (),
            )

        if state.phase == "ready_video":
            return _film(desk, run, state, cfg=cfg, paths=paths)

        if state.phase == "complete":
            return StepResult(
                state.phase,
                f"Episode {ep} already complete. video={state.last_delivery_url}",
                (),
            )

        if state.phase == "failed":
            if CAST_NOT_APPROVED in (state.last_error or ""):
                raise RuntimeError(
                    f"Episode {ep} stopped at `{state.failed_phase or 'an unrecorded stage'}`: {state.last_error}\n"
                    f"Nothing more was sent. {reapprove_plates_hint(desk)}"
                )
            raise RuntimeError(
                f"Episode {ep} stopped at `{state.failed_phase or 'an unrecorded stage'}`: {state.last_error}\n"
                'Nothing more was sent. After fixing the cause: `fictora-produce retry-step --desk D --cause "..."`, '
                "then `fictora-produce step`."
            )
        raise RuntimeError(f"unknown phase: {state.phase}")
    except SystemExit as exc:
        if state.phase != "failed":
            state.failed_phase = state.phase
        state.phase = "failed"
        state.last_error = str(exc)
        save_production(desk, state)
        if CAST_NOT_APPROVED in str(exc.code):
            raise SystemExit(f"{exc.code}\n{reapprove_plates_hint(desk)}") from exc
        plain = explain_film_refusal(str(exc.code))
        if plain is not None:
            raise SystemExit(f"{exc.code}\n{plain}") from exc
        raise
    finally:
        run.client.close()


@dataclass
class CollectedTakes:
    """What :func:`collect_takes` put on the desk."""

    jobs: list[str]
    booked_usd: float
    paths: list[str]
    first_url: str | None
    #: Clips the job returned for another episode (an older server filmed more than asked).
    foreign: list[str]
    #: Who is on screen per shot, from each take's facts (``shots[].people``); empty on an older server.
    on_screen: list[str] = field(default_factory=list)
    #: Clips of this episode the kit could not tie to a take (no take number and no request that names
    #: one): not downloaded, not booked, no facts saved; never filed under a guessed take.
    unplaced: list[str] = field(default_factory=list)


def take_for_clip(
    clip: dict[str, Any],
    position: int,
    *,
    desk_takes: list[str],
    asked: list[str],
    clips: list[dict[str, Any]],
    foreign: bool,
) -> tuple[str | None, str]:
    """The desk take a filmed clip belongs to, by explicit identity only, or ``None`` and why.

    1. The clip's take number from the server (``set_index``, from ``relation.id`` ``..._setNN``).
    2. Without one: the film request, when it asked for exactly one take and one clip came back.
    3. Without one: a whole-episode film whose job returned exactly the episode's takes and
       nothing else, none numbered: the server numbers takes by their place in the job's
       ``depends_on`` (``take_index``), so the place is the server's own identity.

    Anything else is not guessed: a clip with no take number on a film that asked for
    some of the takes would otherwise land on t1 by position (#66 review), and its
    facts and sound effects with it.

    Parameters
    ----------
    clip
        One clip from :func:`creation.harness.raw_video.wait_for_raw_scene_clips`.
    position
        Its 1-based place among this episode's clips.
    desk_takes
        The episode's takes on the desk.
    asked
        The takes the film request asked for.
    clips
        Every clip of this episode the job returned.
    foreign
        Whether the job also returned another episode's clips.

    Returns
    -------
    tuple[str | None, str]
        ``(take_id, "")`` or ``(None, reason)``.
    """

    index = clip.get("set_index")
    if isinstance(index, int):
        take_id = f"t{index}"
        if take_id in desk_takes:
            return take_id, ""
        return (
            None,
            f"the server numbers it take {index}, which is not a take on the desk ({', '.join(desk_takes)})",
        )
    if len(asked) == 1 and len(clips) == 1:
        return asked[0], ""
    numbered = any(isinstance(c.get("set_index"), int) for c in clips)
    if (
        asked == desk_takes
        and len(clips) == len(desk_takes)
        and not numbered
        and not foreign
    ):
        return desk_takes[position - 1], ""
    return None, (
        f"it has no take number (set_index) and this film asked for {', '.join(asked) or 'no take'} "
        f"with {len(clips)} clip(s) back; not filed under a guessed take"
    )


def seed_attempt_for(
    desk: Path, *, episode: int, take_ids: list[str] | None = None
) -> int | None:
    """Return the next compile attempt for a re-film (the most films of the takes in scope, plus one).

    ``None`` when none of them was filmed yet: a first film sends no ``seed_attempt`` (the server's 1).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_ids
        Takes in scope; default every take of the episode.

    Returns
    -------
    int | None
        ``previous + 1`` or ``None``.
    """

    slot = episode_by_ordinal(load_series(desk), episode)
    films = [
        take.filmed_count
        for take in slot.takes
        if take_ids is None or take.take_id in take_ids
    ]
    most = max(films, default=0)
    return most + 1 if most else None


def collect_takes(
    desk: Path,
    run: DramaApiRunSession,
    state: ProductionState,
    raw: dict[str, Any],
    *,
    episode: int,
    clip_seconds: int,
    spine: dict[str, Any],
    asked: list[str] | None = None,
) -> CollectedTakes:
    """Download one episode's filmed takes raw, save their take facts, book spend, mark them filmed.

    Every take of the episode the job filmed is collected and booked (it was paid for),
    so a one-take re-film collects the one take the server filmed.

    Parameters
    ----------
    desk
        Series desk.
    run
        Session (reads take facts).
    state
        Production state (lane, spine).
    raw
        :func:`creation.harness.raw_video.wait_for_raw_scene_clips` result.
    episode
        Episode filmed.
    clip_seconds
        Take length, for pricing a take without facts.
    spine
        Spine JSON (the episode's id).
    asked
        The takes the film request asked for (default: every take of the episode).
        A clip is filed under a take only by explicit identity (:func:`take_for_clip`).

    Returns
    -------
    CollectedTakes
        Job lines, dollars booked, files, the first clip URL, clips of other episodes, and
        clips that could not be tied to a take.
    """

    ep_dir = _episode_dir(desk, episode)
    episode_id = episode_id_for(spine, episode)
    foreign = [
        f"{clip.get('episode_id')} ({clip.get('job_id')})"
        for clip in raw.get("clips") or []
        if clip.get("episode_id") not in ("", episode_id)
    ]
    clips = episode_clips(raw, episode_id=episode_id)
    slot = episode_by_ordinal(load_series(desk), episode)
    take_ids = [take.take_id for take in slot.takes]
    (ep_dir / "takes").mkdir(parents=True, exist_ok=True)
    today = fal_billing_day()
    fetch = httpx.Client(timeout=300.0)
    booked = 0.0
    jobs: list[str] = []
    paths: list[str] = []
    on_screen: list[str] = []
    unplaced: list[str] = []
    cast_names = cast_names_from(spine)
    try:
        for position, clip in enumerate(clips, start=1):
            take_id, why = take_for_clip(
                clip, position, desk_takes=take_ids, asked=list(asked or take_ids), clips=clips,
                foreign=bool(foreign),
            )  # fmt: skip
            if take_id is None:
                run.emit(
                    "take_without_desk_slot",
                    job_id=clip.get("job_id"),
                    index=clip.get("set_index"),
                    reason=why,
                )
                unplaced.append(
                    f"take job `{clip.get('job_id')}` ({clip.get('url')}): {why}"
                )
                continue
            path = download_to_versioned(
                fetch,
                str(clip["url"]),
                ep_dir / "takes",
                f"take-ep{episode:02d}-{take_id}-raw",
                default_suffix=".mp4",
            )
            paths.append(str(path))
            if clip.get("job_id") and take_job_booked(desk, str(clip["job_id"])):
                # A run cut off mid-collect already booked and counted this take.
                jobs.append(
                    f"{take_id}: take job `{clip['job_id']}` (already booked; not booked again)"
                )
                continue
            facts = fetch_take_facts(run, str(clip["job_id"]), spine_id=state.spine_id)
            priced: tuple[float, str] | None = None
            if facts is not None:
                save_take_facts(
                    desk, episode=episode, take_id=take_id, facts=facts, spine=spine
                )
                priced = take_facts_usd(facts, on=today)
                on_screen += on_screen_lines(take_id, facts, cast_names)
                on_screen += take_soundtrack_lines(take_id, facts)
                state.remember_server_lane(
                    server_lane(facts)
                )  # the caller saves the state
            if priced is None:
                estimate = lane_take_usd(
                    state.video_lane, clip_seconds, on=today, server=state.server_lane()
                )
                priced = (
                    (estimate, "estimate from the price table")
                    if estimate is not None
                    else None
                )
            if priced is not None:
                record_spend(
                    desk,
                    episode=episode,
                    usd=priced[0],
                    take_id=take_id,
                    unit=f"take {clip_seconds}s",
                    job_id=str(clip.get("job_id") or "") or None,
                )
                booked += priced[0]
            record_filmed(desk, episode=episode, take_id=take_id)
            jobs.append(
                f"{take_id}: take job `{clip['job_id']}`"
                + (
                    f" ({priced[1]}, ${priced[0]:.2f})"
                    if priced
                    else " (not priced; book by hand)"
                )
            )
    finally:
        fetch.close()
    return CollectedTakes(
        jobs,
        booked,
        paths,
        str(clips[0]["url"]) if clips else None,
        foreign,
        on_screen,
        unplaced,
    )


def on_screen_lines(
    take_id: str, facts: dict[str, Any] | None, cast_names: dict[str, str]
) -> list[str]:
    """``tK on screen, per shot:`` then one line per shot with a head count; empty when the server sent none.

    Parameters
    ----------
    take_id
        ``t1`` ...
    facts
        The take facts (or ``None``).
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[str]
        Printable lines (indented under the header).
    """

    shots = shot_people_lines(facts, cast_names)
    if not shots:
        return []
    return [f"{take_id} on screen, per shot (take facts):"] + [
        f"  {line}" for line in shots
    ]


def take_soundtrack_lines(take_id: str, facts: dict[str, Any] | None) -> list[str]:
    """``tK Soundtrack: …`` when the server said whose voices the take's sound is; empty from an older server.

    Parameters
    ----------
    take_id
        ``t1`` ...
    facts
        The take facts (or ``None``).

    Returns
    -------
    list[str]
        One printable line, or none.
    """

    from creation.post.soundtrack import soundtrack_from

    soundtrack = soundtrack_from(facts)
    if not soundtrack.sent:
        return []
    return [f"{take_id} {soundtrack.one_line()}"]


def unplaced_warning(unplaced: list[str]) -> str:
    """Say loudly which clips were not filed under any take (and why), so a human places them."""

    if not unplaced:
        return ""
    return (
        "\n!! Not filed under any take (not downloaded, no facts saved, not booked, not marked filmed): "
        + "; ".join(unplaced)
        + ". It was paid for: book it by hand, and tell engineering with the take job id. Never file it "
        "under a take by hand without knowing which take it is."
    )


def foreign_warning(foreign: list[str]) -> str:
    """Say loudly that the job filmed another episode too (never booked here)."""

    if not foreign:
        return ""
    return (
        "\n!! The server also filmed another episode: "
        + ", ".join(foreign)
        + ". They were not downloaded or booked "
        "here. Tell engineering with the video job id (the deploy may not film one episode alone)."
    )


def _film(
    desk: Path,
    run: DramaApiRunSession,
    state: ProductionState,
    *,
    cfg: Any,
    paths: list[str],
) -> StepResult:
    """Film the current episode alone (or pick up its job), collect every take raw with its take facts, book spend."""

    ep = state.episode_ordinal
    ep_dir = _episode_dir(desk, ep)
    api_dir = api_dir_for_episode(desk, ep)
    expected = len(episode_by_ordinal(load_series(desk), ep).takes) or None
    raw = _resume_raw_clips(
        api_dir,
        run=run,
        state=state,
        deadline=cfg.poll_video_deadline_seconds,
        expected_clips=expected,
    )
    delivery: dict[str, Any] | None = None
    if raw is None:
        before = run.spine(state.spine_id or "")
        stopped = film_stop_message(before, episode=ep) or voices_film_refusal(
            desk, before, episode=ep, run=run
        )
        if stopped:
            raise RuntimeError(stopped)
        for warning in stranded_preflight(
            before, unit=f"ep{ep:02d}", desk=desk, episode=ep
        ):
            print(warning, file=sys.stderr)
        print(f"[film] {next_take_voices_line(desk, run, before)}", file=sys.stderr)
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
            seed_attempt=seed_attempt_for(desk, episode=ep),
            expected_clips=expected,
        )
        raw = result["raw_scenes"]
        delivery = result.get("delivery")
    elif cfg.api_captions:
        delivery = stages.fetch_delivery_optional(
            run, str(raw.get("coordinator_job_id") or "")
        )
    video_job_id = str(raw.get("coordinator_job_id") or "")
    spine = run.spine(state.spine_id or "")
    save_spine_snapshot(desk, ep, spine)
    got = collect_takes(
        desk,
        run,
        state,
        raw,
        episode=ep,
        clip_seconds=cfg.clip_duration_seconds,
        spine=spine,
    )
    if got.first_url is None:
        raise RuntimeError(
            f"the take job {video_job_id} completed but no take for episode {ep} came back"
        )
    paths.extend(got.paths)
    url = _delivery_video_url(delivery) if delivery else None
    if url:
        fetch = httpx.Client(timeout=300.0)
        try:
            path = download_to_versioned(
                fetch,
                url,
                ep_dir / "takes",
                f"take-ep{ep:02d}-api-captioned",
                default_suffix=".mp4",
            )
        finally:
            fetch.close()
        paths.append(str(path))
    state.last_delivery_url = url or got.first_url
    state.last_video_job_id = video_job_id or None
    state.phase = "complete"
    save_production(desk, state)
    _note(
        ep_dir,
        f"Takes filmed: video job `{video_job_id}`; "
        + "; ".join(got.jobs)
        + f". Booked ${got.booked_usd:.2f}."
        + (f" NOT FILED: {'; '.join(got.unplaced)}." if got.unplaced else ""),
    )
    follow = (
        "arc --list (pick the series arc)" if ep == 1 else f"author --episode {ep + 1}"
    )
    hint = (
        " Not done yet: after the human says Use it, run `fictora-produce finish --desk <desk> --episode "
        f"{ep} --take tK` per take (sound effects, music, mix, captions, mark; hosted post is off)."
    )
    return StepResult(
        state.phase,
        f"Episode {ep} filmed: {len(got.jobs)} take(s), ${got.booked_usd:.2f} booked. Video job {video_job_id}.{hint}\n"
        + "\n".join(f"  {line}" for line in got.jobs + got.on_screen)
        + f"\nNext episode: fictora-produce {follow} --desk <desk>."
        + foreign_warning(got.foreign)
        + unplaced_warning(got.unplaced),
        tuple(paths),
    )


#: The server's answer when a board or take is asked for while the cast plates are not approved on the story.
CAST_NOT_APPROVED = "cast_not_approved"


#: The server's refusal when the brief's locked lines do not fit the episode (fictora-drama).
LOCKED_LINES_OUT_OF_BOUNDS = "locked_lines_out_of_bounds"


def locked_lines_pause(message: str, *, desk: Path | str = "<desk>") -> str | None:
    """The creator-facing pause for a brief whose locked lines do not fit, or None for any other error.

    The server never shortens, splits, merges or drops a locked line: it stops
    the draft before any writer runs and names each line and its overrun. The
    desk stays at ``new``; nothing was drafted.

    Parameters
    ----------
    message
        The draft's failure text (``plan failed authoring_validation_failed: ...``).
    desk
        Series desk, for the commands to paste.

    Returns
    -------
    str | None
        What to tell the creator, or None when the failure is something else.
    """

    if LOCKED_LINES_OUT_OF_BOUNDS not in message:
        return None
    # The server's own words: "locked_lines_out_of_bounds at scene_prompt: <refusal>".
    found = re.search(
        rf"{LOCKED_LINES_OUT_OF_BOUNDS} at scene_prompt: (.+?)(?: \(\+\d+ more in details\.errors\)| \(details |$)",
        message,
        re.S,
    )
    listed = "\n  " + (found.group(1).strip() if found else message.strip()[:1200])
    return (
        "PAUSED for the creator: the brief locks its lines word for word and they do not fit this episode, "
        "so nothing was drafted (the server never shortens, splits, merges or drops a locked line)."
        f"{listed}\n"
        "Ask the creator to edit the brief's Lines (shorten a line, or move it to the next episode), then "
        f"`fictora-produce bind --desk {desk} --prompt <edited brief> ...` and `fictora-produce step` again: "
        "the edited brief is a new draft. `step` with the brief unchanged shows this pause again and drafts nothing."
    )


def reapprove_plates_hint(desk: Path | str = "<desk>") -> str:
    """The fix printed when a step is refused with ``cast_not_approved``.

    Parameters
    ----------
    desk
        Series desk, for a command the operator can paste.

    Returns
    -------
    str
        What to run, in order: re-approve the current plates ($0), put the
        failed step back, run it again.
    """

    return (
        "Fix: the server lost the plates approval (the plates on the desk are unchanged). After the "
        f"human's yes to the same plates: `fictora-produce approve --desk {desk} --gate plates --again` "
        f'($0, draws nothing), then `fictora-produce retry-step --desk {desk} --cause "plates re-approved"`, '
        "then `fictora-produce step`."
    )


def reapprove_plates(desk: Path, *, path: Path | None = None) -> StepResult:
    """Send the plates approval again on the story's current version, outside ``wait_plates``.

    For a story the server says has unapproved plates (``cast_not_approved``)
    though the human approved them and nothing changed. Sends
    ``POST /v1/spines/{id}/cast/approve`` on the current ``spine_version`` with a
    fresh idempotency key, saves the answer and the story (``api/spine.json``),
    and records it on the desk. It draws nothing and costs $0; the phase is
    left as it is.

    Parameters
    ----------
    desk
        Series desk.
    path
        The plate file the human looked at again (recorded on the gate).

    Returns
    -------
    StepResult
        The desk's phase and what to run next (``retry-step`` when a step failed).

    Raises
    ------
    RuntimeError
        When the desk is still at ``wait_plates`` (use the normal approve) or
        the plates were never approved on this desk.
    """

    desk = desk.expanduser().resolve()
    state = load_production(desk)
    if state.phase == "wait_plates":
        raise RuntimeError(
            "The plates are waiting for their first yes: run `fictora-produce approve --gate plates` "
            "without --again."
        )
    if load_series(desk).plates.status != "approved":
        raise RuntimeError(
            "Refused: the plates were never approved on this desk, so there is nothing to approve again. "
            "Show the human the plates first."
        )
    ep = state.episode_ordinal
    ep_dir = _episode_dir(desk, ep)
    tag = f"ep{ep:02d}-reapprove-{uuid.uuid4().hex[:8]}"
    run = _open_run(desk, state)
    try:
        spine = stages.approve_cast(run, spine_id=state.spine_id or "", tag=tag)
    finally:
        run.client.close()
    save_spine_snapshot(desk, ep, spine)
    version = spine.get("spine_version")
    approve_series_gate(
        desk,
        "plates",
        path=str(path) if path else None,
        note=f"approved again ({tag}) on spine_version {version}",
    )
    _note(
        ep_dir,
        f"Plates approved again on the server (`{tag}`, spine_version {version}): same plates, "
        "$0, nothing drawn. Saved api/spine.json.",
    )
    if state.phase == "failed":
        follow = (
            f'Next: `fictora-produce retry-step --desk {desk} --cause "plates re-approved"`, '
            "then `fictora-produce step`."
        )
    else:
        follow = "Next: `fictora-produce step`."
    return StepResult(
        state.phase,
        f"Plates approved again on spine_version {version} ($0, nothing drawn). {follow}",
        (desk / "api" / "spine.json",),
    )


#: Phases before an episode's boards are drawn: nothing to approve again.
_BEFORE_BOARDS = frozenset(
    {"new", "ready_cast_enrol", "wait_plates", "wait_script", "ready_boards_enrol"}
)


def _newest_board(desk: Path, episode: int, take_id: str) -> Path | None:
    found = sorted(
        (desk / f"ep{episode:02d}" / "boards").glob(
            f"board-ep{episode:02d}-{take_id}-v*.png"
        ),
        key=lambda p: int(p.stem.rsplit("-v", 1)[-1] or 0),
    )
    return found[-1] if found else None


def reapprove_boards(
    desk: Path, *, path: Path | None = None, accept_dim: bool = False
) -> StepResult:
    """Send the board approval to the server again: a board redrawn after its yes, on a desk past the gate.

    ``redraw-board`` sends the desk back to the board gate only from ``wait_board``,
    ``ready_estimate`` or ``wait_spend``; on a desk further on (``ready_video``,
    ``complete``) the new board stayed pending on the server, the desk-only yes
    (``fictora-ops approve``) never reached it, and film was refused
    (``boards_not_approved_for_generation``). This measures the episode's boards
    and approves them on the story's current version under a fresh idempotency
    key (the first approval's key would replay its old answer), records the yes
    on the desk for each take's newest board, and leaves the phase as it is.
    $0, draws nothing.

    Parameters
    ----------
    desk
        Series desk.
    path
        The board file the human looked at (one-take episodes; default each take's newest board).
    accept_dim
        Kept for older servers.

    Returns
    -------
    StepResult
        The unchanged phase and what to run next.

    Raises
    ------
    RuntimeError
        When the episode has no board drawn yet.
    """

    desk = desk.expanduser().resolve()
    state = load_production(desk)
    ep = state.episode_ordinal
    if state.phase in _BEFORE_BOARDS:
        raise RuntimeError(
            f"no board drawn for episode {ep} yet (phase {state.phase}): nothing to approve. "
            "Next: fictora-produce step."
        )
    slot = episode_by_ordinal(load_series(desk), ep)
    images: dict[str, Path] = {}
    for take in slot.takes:
        stored = state.board_paths.get(take.take_id)
        image = (
            _newest_board(desk, ep, take.take_id)
            or (desk / stored if stored else None)
            or path
        )
        if image is None:
            raise ValueError(
                f"no board on the desk for {take.take_id}; pass --path to the board file reviewed"
            )
        images[take.take_id] = image
    tag = f"ep{ep:02d}-boards-reapprove-{uuid.uuid4().hex[:8]}"
    run = _open_run(desk, state)
    try:
        spine = run.spine(state.spine_id or "")
        approve_episode_boards(
            run,
            spine_id=state.spine_id or "",
            spine=spine,
            episode=ep,
            accept_dim=accept_dim,
            idempotency_key=f"{run.prefix}-{tag}",
        )
    finally:
        run.client.close()
    for take_id, image in images.items():
        approve_board(desk, episode=ep, take_id=take_id, image=image)
    version = spine.get("spine_version")
    boards = ", ".join(f"{t} `{p.name}`" for t, p in images.items())
    _note(
        _episode_dir(desk, ep),
        f"Board(s) approved again on the server (`{tag}`, spine_version {version}): {boards}. "
        "$0, nothing drawn.",
    )
    follow = (
        "Next: film the take again if the redraw was for it (`fictora-produce film … --cause …`)."
        if state.phase in ("ready_video", "complete")
        else "Next: fictora-produce step."
    )
    return StepResult(
        state.phase,
        f"Board(s) of episode {ep} approved again on the server ({boards}; spine_version {version}); "
        f"phase {state.phase} unchanged. {follow}",
        (),
    )


def approve_gate(
    desk: Path,
    *,
    gate: str,
    path: Path | None = None,
    accept_dim: bool | None = None,
    again: bool = False,
) -> StepResult:
    """Record a human gate and run the matching API approve when needed.

    ``again`` sends the plates approval again outside ``wait_plates``
    (:func:`reapprove_plates`). A board yes outside ``wait_board`` (a board
    redrawn after its yes, on a desk past the board gate, even ``complete``),
    or with ``again``, goes to the server too: :func:`reapprove_boards`.
    """

    if again and gate not in ("plates", "board"):
        raise ValueError("--again is for --gate plates or --gate board")
    if again and gate == "plates":
        return reapprove_plates(desk, path=path)
    desk = desk.expanduser().resolve()
    state = load_production(desk)
    if gate == "board" and (again or state.phase != "wait_board"):
        return reapprove_boards(desk, path=path, accept_dim=bool(accept_dim))
    run = _open_run(desk, state)
    ep = state.episode_ordinal

    try:
        if gate == "plates":
            if state.phase != "wait_plates":
                raise RuntimeError(f"expected wait_plates, got {state.phase}")
            stages.approve_cast(
                run, spine_id=state.spine_id or "", tag=f"ep{ep}" if ep >= 2 else "ep1"
            )
            record = approve_series_gate(
                desk, "plates", path=str(path) if path else None
            )
            # A character a later episode brought in is approved after that
            # episode's script yes: go on to its boards.
            # The voices gate comes after the plates: the human hears each voice before any filming.
            voices = voices_gate_text(
                desk, run.spine(state.spine_id or ""), episode=ep, run=run
            )
            voices = f"\n{voices}" if voices else ""
            if (
                ep >= 2
                and episode_by_ordinal(load_series(desk), ep).script.status
                == "approved"
            ):
                state.phase = "ready_boards_enrol"
                save_production(desk, state)
                return StepResult(
                    state.phase,
                    f"Plates approved ({record.status}), including episode {ep}'s new character(s). "
                    f"Next: fictora-produce step (boards).{voices}",
                    (),
                )
            state.phase = "wait_script"
            save_production(desk, state)
            # A desk that drew no look frame is asked its captions at its first plates yes (free, never blocks).
            from io import StringIO

            from creation.caption_preview import caption_style_at_look

            asked = StringIO()
            caption_style_at_look(desk, out=asked)
            captions = (
                f"\n{asked.getvalue().rstrip()}" if asked.getvalue().strip() else ""
            )
            return StepResult(
                state.phase,
                f"Plates approved ({record.status}). Human: approve script lines.{voices}{captions}",
                (),
            )

        if gate == "script":
            if state.phase != "wait_script":
                raise RuntimeError(f"expected wait_script, got {state.phase}")
            spine = stages.approve_script(
                run, spine_id=state.spine_id or "", episode=ep
            )
            save_spine_snapshot(desk, ep, spine)
            record = record_script_gate(desk, episode=ep)
            owing = cast_owing_pictures(spine, episode=ep) if ep >= 2 else []
            if owing:
                # Boards are drawn from the characters' pictures; the server
                # refuses them (cast_not_approved) until a newcomer's is approved.
                state.phase = "ready_cast_enrol"
                save_production(desk, state)
                who = ", ".join(name for _, name in owing)
                return StepResult(
                    state.phase,
                    f"Episode {ep} script approved on the API. New character(s) {who} need a picture before "
                    f"boards: `fictora-produce step` draws it (~${float(STILL_USD) * len(owing):.2f}), then "
                    "the human approves it with `fictora-produce approve --gate plates`.",
                    (),
                )
            state.phase = "ready_boards_enrol"
            save_production(desk, state)
            warning = (
                f"\n!! {record.note}"
                if record.note and record.note.startswith("WARNING")
                else ""
            )
            names = named_cast_stop(spine, episode=ep, desk=str(desk), heads_up=True)
            if names:
                _note(_episode_dir(desk, ep), names)
                warning += f"\n{names}"
            touches = touch_owner_heads_up(spine, episode=ep, desk=str(desk))
            if touches:
                _note(_episode_dir(desk, ep), touches)
                warning += f"\n{touches}"
            camera = locked_camera_line(spine, episode=ep)
            if camera:
                warning += "\n" + camera
            return StepResult(
                state.phase,
                f"Episode {ep} script approved on the API. Next: fictora-produce step (boards).{warning}",
                (),
            )

        if gate == "board":
            if state.phase != "wait_board":
                raise RuntimeError(f"expected wait_board, got {state.phase}")
            spine = run.spine(state.spine_id or "")
            approve_episode_boards(
                run,
                spine_id=state.spine_id or "",
                spine=spine,
                episode=ep,
                accept_dim=bool(accept_dim),
            )
            slot = episode_by_ordinal(load_series(desk), ep)
            recorded: list[str] = []
            for take in slot.takes:
                stored = state.board_paths.get(take.take_id)
                image = desk / stored if stored else path
                if image is None:
                    raise ValueError(
                        f"no board recorded for {take.take_id}; pass --path to the board file reviewed"
                    )
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
