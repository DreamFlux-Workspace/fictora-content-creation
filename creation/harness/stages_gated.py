"""Episode-1 API stages with human gates — enrol and approve stay separate."""

from __future__ import annotations

import time
from typing import Any, Mapping

from creation.harness.http_util import HOSTED_POST_OFF_HINT, describe_job_error
from creation.harness.video_enrol_errors import server_refused_episode_ordinal_field
from creation.desk_media_urls import drawn_cast_rows
from creation.harness.session import DramaApiRunSession
from creation.spine_view import episode_id_for
from creation.harness.visual_first_ep1 import (
    approve_ep1_boards,
    measure_ep1_board_exposure,
    reuse_generation_body,
)


def scene_prompt(spine: Mapping[str, Any], fallback: str) -> str:
    """Return normalized scene prompt or the desk fallback."""

    normalized = str(spine.get("scene_prompt_normalized") or "").strip()
    return normalized or fallback


#: The draft ``outline_mode`` that writes episode 1 alone; the series arc is picked at episode 2
#: (``arc --list`` / ``arc --pick``). The server default (``rolling``) would outline five episodes up front.
EP1_ALONE_OUTLINE_MODE = "arc_at_episode_two"

#: Spoken-language tags the draft contract accepts.
SPOKEN_LANGUAGES: tuple[str, ...] = ("en-US", "ja-JP", "ko-KR")


def spoken_language_tag(value: str) -> str:
    """Return the contract tag for a spoken language from the tag or its short code (``ja`` -> ``ja-JP``).

    Parameters
    ----------
    value
        ``ja-JP``, ``ja``, ``KO`` and so on.

    Returns
    -------
    str
        One of :data:`SPOKEN_LANGUAGES`.

    Raises
    ------
    ValueError
        For a language the draft contract does not take (the API would answer 422).
    """

    wanted = value.strip().replace("_", "-").casefold()
    for tag in SPOKEN_LANGUAGES:
        if wanted in (tag.casefold(), tag.split("-")[0].casefold()):
            return tag
    raise ValueError(
        f"spoken language {value!r} is not supported; use one of {', '.join(SPOKEN_LANGUAGES)}"
    )


def draft_request_body(
    *,
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    band: str = "15s",
    video_lane: str | None = "minimax-h3",
    cut_tempo: str | None = None,
    spoken_language: str | None = None,
    locale: str = "en-US",
) -> dict[str, Any]:
    """Build the ``POST /v1/prompt-video-authoring-drafts`` body: episode 1 alone, arc at episode 2.

    Parameters
    ----------
    prompt
        The approved premise.
    preset_id, preset_version
        Art style preset pin.
    band
        Season duration band.
    video_lane
        ``model_overrides.video``; pinned on the draft because row boards are decided at admission.
    cut_tempo
        Shot plan (``punchy``, ``slow_burn``, ``one_shot``), set on the draft so the board and the take agree.
        ``None`` leaves the server default (punchy coverage).
    spoken_language
        Language the cast speaks when not English (``ja``, ``ko`` or the contract tag).
    locale
        Draft locale.

    Returns
    -------
    dict[str, Any]
        One-episode draft body carrying ``outline_mode=arc_at_episode_two``.
    """

    body: dict[str, Any] = {
        "prompt": prompt,
        "episode_count": 1,
        "episode_video_mode": "extended",
        "art_style_preset_id": preset_id,
        "art_style_preset_version": preset_version,
        "duration_band": band,
        "locale": locale,
    }
    if video_lane:
        body["model_overrides"] = {"video": video_lane}
    if cut_tempo:
        body["cut_tempo"] = cut_tempo
    if spoken_language:
        body["spoken_language"] = spoken_language_tag(spoken_language)
    body["outline_mode"] = EP1_ALONE_OUTLINE_MODE
    return body


def start_draft(
    run: DramaApiRunSession,
    *,
    prompt: str,
    preset_id: str,
    preset_version: str,
    band: str = "15s",
    video_lane: str = "minimax-h3",
    cut_tempo: str | None = None,
    spoken_language: str | None = None,
    locale: str = "en-US",
    deadline_seconds: float = 1800.0,
) -> tuple[str, dict[str, Any]]:
    """Draft episode 1 alone (arc picked at episode 2) and poll the plan job.

    A retryable ``authoring_stalled`` is re-drafted (up to three tries). Any other
    failure stops with the server's own code, message and rule details: a
    deterministic authoring failure names the rule that failed.

    Parameters
    ----------
    run
        Session (its session id owns the new spine).
    prompt, preset_id, preset_version, band, video_lane, cut_tempo, spoken_language, locale
        As :func:`draft_request_body`.
    deadline_seconds
        Plan poll cap.

    Returns
    -------
    tuple[str, dict[str, Any]]
        ``(spine_id, terminal plan job)``.
    """

    body = draft_request_body(
        prompt=prompt,
        preset_id=preset_id,
        preset_version=preset_version,
        band=band,
        video_lane=video_lane,
        cut_tempo=cut_tempo,
        spoken_language=spoken_language,
        locale=locale,
    )
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
        plan = run.poll_job(
            draft["plan_job_id"],
            label="plan",
            video_route=False,
            deadline_seconds=deadline_seconds,
        )
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
    raise SystemExit(f"plan {describe_job_error(last_plan)}")


def _episode_ids_from_spine(spine: Mapping[str, Any]) -> list[str]:
    """Return ordered episode ids from a spine snapshot."""

    episode_ids: list[str] = []
    for row in spine.get("episode_summaries") or []:
        if isinstance(row, dict):
            eid = str(row.get("episode_id") or "").strip()
            if eid:
                episode_ids.append(eid)
    return episode_ids


def _terminal_error_code(terminal: dict[str, Any]) -> str | None:
    error = terminal.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        return str(code) if code else None
    return None


def _cast_plates_present(spine: dict[str, Any]) -> bool:
    """Return whether every drawn cast row has a usable portrait asset on the spine.

    Voice-only cast (``voice_only: true`` from the server) gets no plate, so
    it is not waited on; a story whose cast is all voices owes no plate.
    """

    if not any(
        isinstance(row, dict) and row.get("cast_id") for row in spine.get("cast") or []
    ):
        return False
    cast_rows = drawn_cast_rows(spine)
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
        if any(
            row.get(key)
            for key in ("image_url", "portrait_url", "full_body_url", "url")
        ):
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
        terminal = run.poll_job(
            job["job_id"], label="cast", video_route=True, deadline_seconds=3600.0
        )
        run.save(f"06_{tag}_cast_terminal{suffix}.json", terminal)
        last_terminal = terminal
        if terminal.get("status") == "completed":
            return run.spine(spine_id)
        if _terminal_error_code(terminal) == "plan_media_spine_version_stale":
            recovered = run.spine(spine_id)
            if _cast_plates_present(recovered):
                run.emit(
                    "cast_recover",
                    code="plan_media_spine_version_stale",
                    note="plates on spine",
                )
                return recovered
            if attempt + 1 < max_attempts:
                run.emit(
                    "cast_retry",
                    code="plan_media_spine_version_stale",
                    attempt=attempt + 1,
                )
                time.sleep(5.0)
                continue
        break
    raise SystemExit(f"cast {describe_job_error(last_terminal)}")


def approve_cast(
    run: DramaApiRunSession, *, spine_id: str, tag: str = "ep1"
) -> dict[str, Any]:
    """Human yes on cast plates."""

    spine = run.spine(spine_id)
    approved = run.post(
        f"/v1/spines/{spine_id}/cast/approve",
        {"spine_version": spine["spine_version"]},
        idempotency_key=f"{run.prefix}-{tag}-cast-approve",
    )
    run.save(f"07_{tag}_cast_approved.json", approved)
    return run.spine(spine_id)


def approve_script(
    run: DramaApiRunSession, *, spine_id: str, episode: int = 1
) -> dict[str, Any]:
    """Human yes on the lines: the whole spine at episode 1, one episode's own approve from episode 2.

    Episode 2 on is approved with ``POST /v1/spines/{id}/pilot-episodes/{n}/approve``: the
    whole-spine approve already happened at episode 1 and would leave episode N ``drafted``.

    Parameters
    ----------
    run
        Session that owns the spine.
    spine_id
        Story spine.
    episode
        Episode ordinal.

    Returns
    -------
    dict[str, Any]
        The spine after the approval.
    """

    spine = run.spine(spine_id)
    if episode >= 2:
        state = next(
            (
                str(row.get("authoring_state") or "")
                for row in spine.get("episode_summaries") or []
                if isinstance(row, dict)
                and row.get("episode_id") == episode_id_for(spine, episode)
            ),
            "",
        )
        if state != "approved":
            approved = run.post(
                f"/v1/spines/{spine_id}/pilot-episodes/{episode}/approve",
                {"spine_version": spine["spine_version"], "episode_ordinal": episode},
                idempotency_key=f"{run.prefix}-ep{episode:02d}-script-approve",
            )
            run.save(f"04_ep{episode:02d}_script_approved.json", approved)
        return run.spine(spine_id)
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
    cut_tempo: str | None = None,
    tag: str = "ep1",
    episode: int = 1,
    max_attempts: int = 3,
    deadline_seconds: float = 7200.0,
) -> dict[str, Any]:
    """Draw boards for episode ``episode`` and poll. Does not approve.

    Sends ``episode_count: N``. The server draws boards only for the episodes
    1..N that still lack them (fictora-drama #436), so episode N is drawn and
    booked alone once earlier episodes have boards; episodes written past N are
    never drawn. With 1 the server would only look at episode 1, find its boards
    drawn and refuse episode 2's enrol with 409 ``boards_already_generated``.
    """

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
                cut_tempo=cut_tempo,
                extra={"episode_count": episode},
            ),
            idempotency_key=f"{run.prefix}-{tag}-boards-enrol{suffix}",
        )
        run.save(f"08_{tag}_boards_enrol{suffix}.json", job)
        terminal = run.poll_job(
            job["job_id"],
            label="boards",
            video_route=True,
            deadline_seconds=deadline_seconds,
        )
        run.save(f"09_{tag}_boards_terminal{suffix}.json", terminal)
        last_terminal = terminal
        if terminal.get("status") == "completed":
            return run.spine(spine_id)
        if (
            _terminal_error_code(terminal) == "plan_media_spine_version_stale"
            and attempt + 1 < max_attempts
        ):
            run.emit(
                "boards_retry",
                code="plan_media_spine_version_stale",
                attempt=attempt + 1,
            )
            time.sleep(2.0)
            continue
        break
    raise SystemExit(f"boards {describe_job_error(last_terminal)}")


def estimate_batch(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    episode: int = 1,
    reroll_take_index: int | None = None,
) -> dict[str, Any]:
    """Price what will be filmed: ``POST /v1/spines/{id}/batches/estimate`` for one episode, or one take of it.

    An arc story (episode 1 drafted alone) is estimated one episode at a time.
    With ``reroll_take_index`` the server prices that one take (fictora-drama #436).
    The answer's ``cost_estimate`` carries dated dollars (internal provider cost).
    A 400 on the selection, or a server too old to price one take (422 naming
    ``reroll_take_index``), is saved as a skip and the desk prices from its table.

    Parameters
    ----------
    run
        Session that owns the spine.
    spine_id
        Story spine.
    episode
        Episode ordinal (resolved to its API id through ``episode_summaries``).
    reroll_take_index
        One-based take to price alone; ``None`` prices the whole episode.

    Returns
    -------
    dict[str, Any]
        The estimate, or ``{..., "estimate_skipped": True}``.
    """

    spine = run.spine(spine_id)
    if not spine.get("episode_summaries"):
        raise SystemExit("spine has no episode_summaries")
    body: dict[str, Any] = {
        "spine_version": spine["spine_version"],
        "episode_ids": [episode_id_for(spine, episode)],
    }
    name = f"12_ep{episode:02d}_estimate.json"
    if reroll_take_index is not None:
        body["reroll_take_index"] = reroll_take_index
        name = f"12_ep{episode:02d}_t{reroll_take_index}_estimate.json"
    try:
        estimate = run.post(f"/v1/spines/{spine_id}/batches/estimate", body)
    except SystemExit as exc:
        msg = str(exc)
        one_take_unknown = reroll_take_index is not None and "reroll_take_index" in msg
        if (
            one_take_unknown
            or "invalid_episode_selection" in msg
            or "invalid_pilot_batch" in msg
            or "HTTP 400" in msg
        ):
            estimate = {**body, "estimate_skipped": True, "detail": msg[:800]}
            run.save(name, estimate)
            return estimate
        raise
    run.save(name, estimate)
    return estimate


#: Said, and nothing is sent, when the deployed API cannot film one episode alone.
OLD_SERVER_FILM = (
    "The deployed Drama API does not film one episode alone yet (no `episode_ordinal` on "
    "POST /v1/video-generations; fictora-drama #436). Filming episode {episode} on it would film "
    "episodes 1..{episode} again and book them. Nothing was sent and nothing was charged. "
    "Tell engineering, and film this episode once the deploy has #436."
)


def _schema_has(openapi: Any, schema_suffix: str, field: str) -> bool | None:
    """Whether an OpenAPI schema named ``*schema_suffix`` lists ``field`` (``None`` when there is no such schema)."""

    if not isinstance(openapi, dict):
        return None
    schemas = (openapi.get("components") or {}).get("schemas") or {}
    found: bool | None = None
    for name, schema in schemas.items():
        if not str(name).endswith(schema_suffix) or not isinstance(schema, dict):
            continue
        props = schema.get("properties") or {}
        if field in props:
            return True
        found = False
    return found


def server_films_one_episode(run: DramaApiRunSession) -> bool | None:
    """Ask the deployed API's OpenAPI whether ``POST /v1/video-generations`` takes ``episode_ordinal``.

    Spends nothing. ``None`` when the schema cannot be read (the request then goes
    out and an older server's 422 is turned into :data:`OLD_SERVER_FILM`).

    Parameters
    ----------
    run
        Session.

    Returns
    -------
    bool | None
        ``True`` supported, ``False`` an older deploy, ``None`` unknown.
    """

    status, body = run.get_optional("/openapi.json")
    if not 200 <= status < 300:
        run.emit("openapi_unavailable", status=status)
        return None
    return _schema_has(body, "VideoGenerationCreateRequest", "episode_ordinal")


def film_scope(
    run: DramaApiRunSession,
    *,
    episode: int,
    reroll_take_index: int | None = None,
    seed_attempt: int | None = None,
) -> dict[str, Any]:
    """Return the fields that film episode ``episode`` alone (or take K of it) on ``POST /v1/video-generations``.

    ``{"episode_count": N, "episode_ordinal": N}`` films episode N only: episodes
    1..N must be approved, and none of them before N is filmed or booked again.
    ``reroll_take_index`` + ``seed_attempt`` film one take with a fresh seed.

    An older deploy without ``episode_ordinal`` is refused here with
    :data:`OLD_SERVER_FILM` before anything is sent, except episode 1, where
    ``episode_count: 1`` already films episode 1 alone and the field is left out.

    Parameters
    ----------
    run
        Session (reads ``/openapi.json``; spends nothing).
    episode
        Episode being filmed.
    reroll_take_index
        One-based take to film alone.
    seed_attempt
        One-based compile attempt (the previous film's plus one).

    Returns
    -------
    dict[str, Any]
        Extra request fields.

    Raises
    ------
    SystemExit
        On an older deploy, for episode 2 on.
    """

    extra: dict[str, Any] = {"episode_count": episode, "episode_ordinal": episode}
    if server_films_one_episode(run) is False:
        if episode != 1:
            raise SystemExit(OLD_SERVER_FILM.format(episode=episode))
        del extra["episode_ordinal"]
        run.emit(
            "episode_ordinal_unsupported",
            note="episode 1 films alone with episode_count 1",
        )
    if reroll_take_index is not None:
        extra["reroll_take_index"] = reroll_take_index
    if seed_attempt is not None:
        extra["seed_attempt"] = seed_attempt
    return extra


def post_video_generation(
    run: DramaApiRunSession, body: dict[str, Any], *, idempotency_key: str, episode: int
) -> dict[str, Any]:
    """POST the take request; an older server's refusal of ``episode_ordinal`` becomes :data:`OLD_SERVER_FILM`.

    Parameters
    ----------
    run
        Session.
    body
        ``POST /v1/video-generations`` body.
    idempotency_key
        Key for the enrol.
    episode
        Episode being filmed (for the message).

    Returns
    -------
    dict[str, Any]
        The admitted job.
    """

    try:
        return run.post("/v1/video-generations", body, idempotency_key=idempotency_key)
    except SystemExit as exc:
        text = str(exc.code)
        if server_refused_episode_ordinal_field(text, request_body=body):
            raise SystemExit(
                f"{OLD_SERVER_FILM.format(episode=episode)} (server said: {text[:400]})"
            ) from None
        raise


def fetch_delivery_optional(
    run: DramaApiRunSession, job_id: str
) -> dict[str, Any] | None:
    """Try the hosted delivery; never raise when it is not there.

    Hosted post-production is off on the deployed API, so ``/delivery`` answers
    ``409 post_production_not_ready`` for every completed take. The raw takes
    are the product; post runs on the laptop.

    Parameters
    ----------
    run
        Session.
    job_id
        Video coordinator job id.

    Returns
    -------
    dict[str, Any] | None
        Delivery JSON (saved as ``18_delivery.json``), or ``None`` (logged).
    """

    status, body = run.get_optional(f"/v1/video-generations/{job_id}/delivery")
    if 200 <= status < 300 and isinstance(body, dict):
        run.save("18_delivery.json", body)
        return body
    run.emit(
        "delivery_unavailable",
        status=status,
        note="hosted post-production is off; the raw takes are the output",
    )
    return None


def finish_video_job(
    run: DramaApiRunSession,
    job_id: str,
    *,
    poll_deadline_seconds: float = 7200.0,
) -> dict[str, Any]:
    """Poll one enrolled video job to terminal and try the (optional) hosted delivery.

    Returns
    -------
    dict[str, Any]
        The delivery JSON, or ``{}`` when the deployment has no hosted delivery.

    Raises
    ------
    SystemExit
        When the job did not complete; the message carries the server's error code and rule.
    """

    terminal = run.poll_job(
        job_id,
        label="video",
        video_route=True,
        deadline_seconds=poll_deadline_seconds,
    )
    run.save("17_video_terminal.json", terminal)
    if terminal.get("status") != "completed":
        if _terminal_error_code(terminal) == "hosted_post_off":
            raise SystemExit(f"video stopped in hosted post: {HOSTED_POST_OFF_HINT}")
        raise SystemExit(f"video {describe_job_error(terminal)}")
    return fetch_delivery_optional(run, job_id) or {}


def delivery_for_completed_video_job(
    run: DramaApiRunSession, job_id: str
) -> dict[str, Any]:
    """Fetch the optional delivery for a video job that already reached ``completed`` (``{}`` when absent)."""

    return fetch_delivery_optional(run, job_id) or {}


def video_request_body(
    run: DramaApiRunSession,
    *,
    spine: dict[str, Any],
    prompt: str,
    preset_id: str,
    preset_version: str | None,
    caption_style: str = "house",
    api_captions: bool = False,
    video_lane: str | None = "minimax-h3",
    clip_duration_seconds: int = 15,
    cut_tempo: str | None = None,
    episode: int = 1,
    reroll_take_index: int | None = None,
    seed_attempt: int | None = None,
) -> dict[str, Any]:
    """Build the ``POST /v1/video-generations`` reuse body that films episode ``episode`` alone.

    ``episode_count`` and ``episode_ordinal`` are both N (:func:`film_scope`): the
    server checks episodes 1..N are approved, films episode N only (nothing
    earlier is filmed or booked again), and pastes episode N-1's stored last
    frame into episode N's first take when the location matches.

    Parameters
    ----------
    run
        Session (reads ``/openapi.json`` once to refuse an older deploy; spends nothing).
    spine
        Spine JSON.
    prompt, preset_id, preset_version, caption_style, api_captions, video_lane, clip_duration_seconds, cut_tempo
        Reuse-body fields.
    episode
        Episode being filmed.
    reroll_take_index, seed_attempt
        A single-take re-film with a fresh seed.

    Returns
    -------
    dict[str, Any]
        ``DramaVideoGenerationCreateRequest`` JSON.
    """

    extra = film_scope(
        run,
        episode=episode,
        reroll_take_index=reroll_take_index,
        seed_attempt=seed_attempt,
    )
    return reuse_generation_body(
        prompt=scene_prompt(spine, prompt),
        spine=spine,
        preset_id=preset_id,
        preset_version=preset_version,
        clip_duration_seconds=clip_duration_seconds,
        cut_tempo=cut_tempo,
        caption_style=caption_style if api_captions else None,
        api_captions=api_captions,
        video_lane=video_lane,
        extra=extra,
    )


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
    cut_tempo: str | None = None,
    video_idempotency_suffix: str = "",
    poll_deadline_seconds: float = 7200.0,
    episode: int = 1,
    seed_attempt: int | None = None,
) -> dict[str, Any]:
    """Film episode ``episode``'s takes and collect them raw (hosted delivery only when asked and available).

    Parameters
    ----------
    run
        Session that owns the spine.
    spine_id
        Story spine.
    prompt, preset_id, preset_version, caption_style, api_captions, video_lane, clip_duration_seconds, cut_tempo
        As :func:`video_request_body`.
    video_idempotency_suffix
        Set once by ``retry-video --new-paid-take``.
    poll_deadline_seconds
        Poll cap.
    episode
        Episode ordinal.
    seed_attempt
        A whole-episode re-film's compile attempt (the previous plus one); ``None`` on the first film.

    Returns
    -------
    dict[str, Any]
        ``{"raw_scenes": {...}, "primary_clip_url": url, "delivery": {...} | None}``.
    """

    from creation.harness.raw_video import wait_for_raw_scene_clips

    if not 4 <= clip_duration_seconds <= 15:
        raise SystemExit(
            "clip_duration_seconds must be 4-15; the API rejects anything else"
        )
    spine = run.spine(spine_id)
    body = video_request_body(
        run,
        spine=spine,
        prompt=prompt,
        preset_id=preset_id,
        preset_version=preset_version,
        caption_style=caption_style,
        api_captions=api_captions,
        video_lane=video_lane,
        clip_duration_seconds=clip_duration_seconds,
        cut_tempo=cut_tempo,
        episode=episode,
        seed_attempt=seed_attempt,
    )
    run.save("16_video_request.json", body)
    idem_suffix = (video_idempotency_suffix or "").strip()
    base = (
        f"{run.prefix}-video" if episode == 1 else f"{run.prefix}-ep{episode:02d}-video"
    )
    idem = f"{base}{idem_suffix}" if idem_suffix else base
    job = post_video_generation(run, body, idempotency_key=idem, episode=episode)
    run.save("16_video_enrol.json", job)
    job_id = str(job["job_id"])
    raw = wait_for_raw_scene_clips(run, job_id, deadline_seconds=poll_deadline_seconds)
    delivery = (
        finish_video_job(run, job_id, poll_deadline_seconds=poll_deadline_seconds)
        if api_captions
        else None
    )
    first = raw["clips"][0]["url"] if raw.get("clips") else None
    return {"raw_scenes": raw, "primary_clip_url": first, "delivery": delivery or None}


__all__ = [
    "EP1_ALONE_OUTLINE_MODE",
    "approve_cast",
    "approve_ep1_boards",
    "approve_script",
    "delivery_for_completed_video_job",
    "draft_request_body",
    "enrol_boards",
    "enrol_cast",
    "enrol_video",
    "estimate_batch",
    "OLD_SERVER_FILM",
    "fetch_delivery_optional",
    "film_scope",
    "finish_video_job",
    "measure_ep1_board_exposure",
    "post_video_generation",
    "server_films_one_episode",
    "spoken_language_tag",
    "start_draft",
    "video_request_body",
]
