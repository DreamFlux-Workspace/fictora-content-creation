"""Visual-first episode-1 API stages for the production create-flow harness."""

from __future__ import annotations

from typing import Any, Mapping

from creation.harness.artifacts import (
    lines_from_spine,
    video_urls_from_payloads,
    write_story_markdown,
)
from creation.harness.http_util import describe_job_error
from creation.harness.session import DramaApiRunSession


def reuse_generation_body(
    *,
    prompt: str,
    spine: Mapping[str, Any],
    preset_id: str,
    preset_version: str | None,
    episode_count: int = 1,
    duration_band: str | None = None,
    clip_duration_seconds: int = 15,
    cut_tempo: str | None = None,
    caption_style: str | None = None,
    api_captions: bool = False,
    video_lane: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a reuse-mode generation body compatible with current OpenAPI.

    Parameters
    ----------
    prompt
        Scene prompt for compile.
    spine
        Current spine JSON (needs ``spine_id``). Its ``locale`` and ``spoken_language`` are sent as-is.
    preset_id
        Art style preset id.
    preset_version
        Optional pinned preset version.
    episode_count
        Pilot episodes to compile.
    duration_band
        Duration band; defaults to spine value or ``15s``.
    clip_duration_seconds
        Per-take length. Default 15. The API accepts 4 to 15.
    cut_tempo
        Optional cut tempo override (``punchy``, ``slow_burn``, ``one_shot``).
    caption_style
        Optional burn-in preset. Sets ``captions_enabled`` when provided.
    video_lane
        Optional ``model_overrides.video`` lane id.
    extra
        Additional JSON fields (e.g. captions). Applied last.

    Returns
    -------
    dict[str, Any]
        Request body without retired tier/resolution fields.
    """
    body: dict[str, Any] = {
        "prompt": prompt,
        "art_style_preset_id": preset_id,
        "authoring_mode": "reuse",
        "reuse_spine_id": spine["spine_id"],
        "episode_count": episode_count,
        "duration_band": duration_band or spine.get("duration_band") or "15s",
        "clip_duration_seconds": clip_duration_seconds,
        "aspect_ratio": "9:16",
        "episode_video_mode": "extended",
        # Copied from the spine: the server carries ``spoken_language`` onto the take's coordinator so
        # post-production picks the right caption treatment (whole English lines on a Japanese or
        # Korean show). ``locale`` is the subtitle language (English unless the spine says otherwise).
        "locale": str(spine.get("locale") or "en-US"),
    }
    if spine.get("spoken_language"):
        body["spoken_language"] = str(spine["spoken_language"])
    if preset_version:
        body["art_style_preset_version"] = preset_version
    if cut_tempo:
        body["cut_tempo"] = cut_tempo
    if api_captions and caption_style:
        body["captions_enabled"] = True
        body["caption_style"] = caption_style
    if video_lane:
        body["model_overrides"] = {"video": video_lane}
    if extra:
        body.update(dict(extra))
    return body


def run_cast_look(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    cut_tempo: str | None = None,
    video_lane: str | None = None,
    tag: str = "ep1",
) -> dict[str, Any]:
    """Enrol cast, poll, and approve portraits (visual-first before script).

    Parameters
    ----------
    run
        Active harness session.
    spine_id
        Target spine id.
    prompt
        Reuse prompt for compile.
    preset_id
        Art style preset id.
    preset_version
        Optional preset version pin.
    cut_tempo
        Optional cut tempo.
    video_lane
        Optional video lane override.
    tag
        Artefact filename tag.

    Returns
    -------
    dict[str, Any]
        Latest spine JSON after cast approval.
    """
    spine = run.spine(spine_id)
    cast_job = run.post(
        f"/v1/spines/{spine_id}/cast/enrol",
        reuse_generation_body(
            prompt=prompt,
            spine=spine,
            preset_id=preset_id,
            preset_version=preset_version,
            cut_tempo=cut_tempo,
            video_lane=video_lane,
        ),
        idempotency_key=f"{run.prefix}-{tag}-cast",
    )
    run.save(f"05_{tag}_cast_enrol.json", cast_job)
    cast = run.poll_job(cast_job["job_id"], label="cast", video_route=True, deadline_seconds=3600.0)
    run.save(f"06_{tag}_cast_terminal.json", cast)
    if cast.get("status") != "completed":
        raise SystemExit(f"cast failed: {cast.get('status')}")
    spine = run.spine(spine_id)
    cast_ok = run.post(
        f"/v1/spines/{spine_id}/cast/approve",
        {"spine_version": spine["spine_version"]},
        idempotency_key=f"{run.prefix}-{tag}-cast-approve",
    )
    run.save(f"07_{tag}_cast_approved.json", cast_ok)
    return run.spine(spine_id)


def approve_ep1_script(run: DramaApiRunSession, *, spine_id: str, spine: Mapping[str, Any]) -> dict[str, Any]:
    """Approve episode-1 script after cast portraits are approved.

    Parameters
    ----------
    run
        Active harness session.
    spine_id
        Target spine id.
    spine
        Spine snapshot with current ``spine_version``.

    Returns
    -------
    dict[str, Any]
        Latest spine JSON after script approval.
    """
    if spine.get("approval_state") == "approved":
        return dict(spine)
    approved = run.post(
        f"/v1/spines/{spine_id}/approve",
        {"spine_version": spine["spine_version"]},
        idempotency_key=f"{run.prefix}-script-approve",
    )
    run.save("04_spine_approved.json", approved)
    return run.spine(spine_id)


def run_boards(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    cut_tempo: str | None = None,
    video_lane: str | None = None,
    tag: str = "ep1",
) -> dict[str, Any]:
    """Enrol boards and poll until terminal.

    Parameters
    ----------
    run
        Active harness session.
    spine_id
        Target spine id.
    prompt
        Reuse prompt.
    preset_id
        Art style preset id.
    preset_version
        Optional preset version.
    cut_tempo
        Optional cut tempo.
    video_lane
        Optional video lane.
    tag
        Artefact tag.

    Returns
    -------
    dict[str, Any]
        Latest spine JSON after boards complete.
    """
    spine = run.spine(spine_id)
    boards_job = run.post(
        f"/v1/spines/{spine_id}/boards/enrol",
        reuse_generation_body(
            prompt=prompt,
            spine=spine,
            preset_id=preset_id,
            preset_version=preset_version,
            cut_tempo=cut_tempo,
            video_lane=video_lane,
        ),
        idempotency_key=f"{run.prefix}-{tag}-boards",
    )
    run.save(f"08_{tag}_boards_enrol.json", boards_job)
    boards = run.poll_job(boards_job["job_id"], label="boards", video_route=True, deadline_seconds=7200.0)
    run.save(f"09_{tag}_boards_terminal.json", boards)
    if boards.get("status") != "completed":
        raise SystemExit(f"boards failed: {boards.get('status')}")
    spine = run.spine(spine_id)
    run.save(f"10_{tag}_spine_after_boards.json", spine)
    return spine


def attach_look_register(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    spine: Mapping[str, Any],
    url: str,
) -> dict[str, Any]:
    """Pin the series look-register crop.

    Parameters
    ----------
    run
        Active harness session.
    spine_id
        Target spine id.
    spine
        Spine with current version.
    url
        Public still URL for the text-free look crop.

    Returns
    -------
    dict[str, Any]
        Updated spine JSON.
    """

    body = {"spine_version": spine["spine_version"], "url": url}
    updated = run.post(f"/v1/spines/{spine_id}/look-register", body)
    run.save("03b_look_register.json", updated)
    return updated


def attach_series_audio_bed(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    spine: Mapping[str, Any],
    url: str,
) -> dict[str, Any]:
    """Pin the series music bed.

    Parameters
    ----------
    run
        Active harness session.
    spine_id
        Target spine id.
    spine
        Spine with current version.
    url
        Public audio URL.

    Returns
    -------
    dict[str, Any]
        Updated spine JSON.
    """

    body = {"spine_version": spine["spine_version"], "url": url}
    updated = run.post(f"/v1/spines/{spine_id}/audio-bed", body)
    run.save("03c_audio_bed.json", updated)
    return updated


def measure_board_exposure(run: DramaApiRunSession, *, spine_id: str, episode: int = 1) -> dict[str, Any]:
    """Read one episode's board brightness. Information only: a dark board is the human's call.

    Parameters
    ----------
    run
        Active harness session.
    spine_id
        Target spine id.
    episode
        Episode ordinal.

    Returns
    -------
    dict[str, Any]
        Exposure report JSON (``boards[].mean_percent``).
    """

    exposure = run.get(f"/v1/spines/{spine_id}/episodes/{episode}/boards/exposure")
    run.save("10b_boards_exposure.json" if episode == 1 else f"10b_ep{episode:02d}_boards_exposure.json", exposure)
    return exposure


def measure_ep1_board_exposure(run: DramaApiRunSession, *, spine_id: str) -> dict[str, Any]:
    """Episode 1's board brightness (see :func:`measure_board_exposure`)."""

    return measure_board_exposure(run, spine_id=spine_id, episode=1)


def approve_episode_boards(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    spine: Mapping[str, Any],
    episode: int = 1,
    accept_dim: bool = False,
) -> dict[str, Any]:
    """Measure an episode's exposure (the server needs it measured), then approve its boards.

    Brightness never blocks: the server ignores ``accept_dim`` since boards
    brightness became information only; it is sent for older deployments.

    Parameters
    ----------
    run
        Active harness session.
    spine_id
        Target spine id.
    spine
        Spine with current version.
    episode
        Episode ordinal.
    accept_dim
        Kept for older servers; ignored by the current API.

    Returns
    -------
    dict[str, Any]
        Approval response JSON.
    """

    measure_board_exposure(run, spine_id=spine_id, episode=episode)
    boards_ok = run.post(
        f"/v1/spines/{spine_id}/episodes/{episode}/boards/approve",
        {
            "spine_version": spine["spine_version"],
            "episode_ordinal": episode,
            "accept_dim": accept_dim,
        },
        idempotency_key=f"{run.prefix}-boards-approve" if episode == 1 else f"{run.prefix}-ep{episode:02d}-boards-approve",
    )
    run.save("11_boards_approved.json" if episode == 1 else f"11_ep{episode:02d}_boards_approved.json", boards_ok)
    return boards_ok


def approve_ep1_boards(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    spine: Mapping[str, Any],
    accept_dim: bool = False,
) -> dict[str, Any]:
    """Approve episode 1's boards (see :func:`approve_episode_boards`)."""

    return approve_episode_boards(run, spine_id=spine_id, spine=spine, episode=1, accept_dim=accept_dim)


def run_video_and_write_story(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    meta_lines: list[str],
    cut_tempo: str | None = None,
    clip_duration_seconds: int = 15,
    caption_style: str | None = None,
    video_lane: str | None = None,
    video_extra: Mapping[str, Any] | None = None,
    footer: str = "",
) -> dict[str, Any]:
    """Submit video generation, poll, fetch delivery, and write STORY.md.

    Parameters
    ----------
    run
        Active harness session.
    spine_id
        Target spine id.
    prompt
        Reuse prompt.
    preset_id
        Art style preset id.
    preset_version
        Optional preset version.
    meta_lines
        STORY.md header lines after the title.
    cut_tempo
        Optional cut tempo.
    clip_duration_seconds
        Per-take length. Default 15. The API accepts 4 to 15.
    caption_style
        Optional burn-in preset (``house`` for the runbook recipe).
    video_lane
        Optional video lane.
    video_extra
        Extra video body fields (captions, etc.).
    footer
        STORY.md footer.

    Returns
    -------
    dict[str, Any]
        Terminal video job JSON.
    """
    spine = run.spine(spine_id)
    video_body = reuse_generation_body(
        prompt=prompt,
        spine=spine,
        preset_id=preset_id,
        preset_version=preset_version,
        clip_duration_seconds=clip_duration_seconds,
        cut_tempo=cut_tempo,
        caption_style=caption_style,
        video_lane=video_lane,
        extra=video_extra,
    )
    run.save("16_video_request.json", video_body)
    video_job = run.post("/v1/video-generations", video_body, idempotency_key=f"{run.prefix}-video")
    run.save("16_video_enrol.json", video_job)
    video = run.poll_job(video_job["job_id"], label="video", video_route=True, deadline_seconds=7200.0)
    run.save("17_video_terminal.json", video)
    if video.get("status") != "completed":
        raise SystemExit(f"video {describe_job_error(video)}")
    # Hosted post is off on the deployed API: /delivery answers 409 post_production_not_ready.
    status, body = run.get_optional(f"/v1/video-generations/{video_job['job_id']}/delivery")
    delivery: dict[str, Any] = body if 200 <= status < 300 and isinstance(body, dict) else {}
    if delivery:
        run.save("18_delivery.json", delivery)
    else:
        run.emit("delivery_unavailable", status=status)
    spine = run.spine(spine_id)
    run.save("19_spine_final.json", spine)
    cast_names = [str(c.get("name") or "") for c in spine.get("cast") or [] if isinstance(c, dict)]
    write_story_markdown(
        run.out / "STORY.md",
        title=str(spine.get("title") or spine_id),
        meta_lines=meta_lines,
        logline=str(spine.get("logline") or prompt[:500]),
        cast_names=[name for name in cast_names if name],
        dialogue_lines=lines_from_spine(spine),
        video_urls=video_urls_from_payloads(video=video, delivery=delivery),
        footer=footer,
    )
    return video
