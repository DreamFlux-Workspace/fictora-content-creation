"""Episode thumbnail draw on the Drama API (operator route, like look-frame)."""

from __future__ import annotations

import re
from typing import Any

from creation.harness.session import DramaApiRunSession

_EPISODE_THUMBNAIL_PATH = re.compile(
    r"/v1/spines/\{spine_id\}/episodes/\{ordinal\}/thumbnail"
)


def openapi_has_episode_thumbnail_route(openapi: Any) -> bool | None:
    """Whether ``POST /v1/spines/{id}/episodes/{ordinal}/thumbnail`` is on this deploy.

    Parameters
    ----------
    openapi
        Parsed ``/openapi.json`` body.

    Returns
    -------
    bool | None
        ``True`` when the route exists, ``False`` when OpenAPI is readable but the route
        is missing, ``None`` when ``openapi`` is not a dict.
    """

    if not isinstance(openapi, dict):
        return None
    paths = openapi.get("paths") or {}
    for path, ops in paths.items():
        if (
            _EPISODE_THUMBNAIL_PATH.search(path)
            and isinstance(ops, dict)
            and "post" in ops
        ):
            return True
    return False


def server_draws_episode_thumbnail(run: DramaApiRunSession) -> bool | None:
    """Ask OpenAPI whether the episode thumbnail route exists (spends nothing).

    Parameters
    ----------
    run
        Session.

    Returns
    -------
    bool | None
        ``True`` supported, ``False`` older deploy, ``None`` when OpenAPI could not be read.
    """

    status, body = run.get_optional("/openapi.json")
    if not 200 <= status < 300:
        run.emit("openapi_unavailable", status=status)
        return None
    return openapi_has_episode_thumbnail_route(body)


def episode_thumbnail_route_missing(message: str) -> bool:
    """True when the server answered 404 because the route is not on this deploy."""

    lower = message.lower()
    return (
        "http 404" in lower and "thumbnail" in lower and "spine_not_found" not in lower
    )


def post_episode_thumbnail(
    run: DramaApiRunSession,
    *,
    spine_id: str,
    episode: int,
    video_url: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Draw the episode card from a tenant-owned take URL.

    Parameters
    ----------
    run
        Session.
    spine_id
        Story spine.
    episode
        Episode ordinal (path segment).
    video_url
        Durable MP4 URL in our storage (the filmed take clip).
    idempotency_key
        Stable idempotency key for this draw.

    Returns
    -------
    dict[str, Any]
        Response body (``image_url``, ``cost_usd``, …).

    Raises
    ------
    SystemExit
        On HTTP errors from the API.
    """

    body = {"video_url": video_url}
    run.save(
        f"ep{episode:02d}_thumbnail_request.json",
        {**body, "idempotency_key": idempotency_key},
    )
    answer = run.post(
        f"/v1/spines/{spine_id}/episodes/{episode}/thumbnail",
        body,
        idempotency_key=idempotency_key,
    )
    run.save(f"ep{episode:02d}_thumbnail_response.json", answer)
    return answer
