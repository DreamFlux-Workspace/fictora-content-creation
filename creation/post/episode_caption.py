"""``caption``: the server burns an episode's captions; nothing is burned on this laptop."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

import httpx

from creation.harness.http_util import api_error_text

ENABLE_ROUTE = "/v1/video-generations/{job_id}/episodes/{episode_id}/captions/enable"
RECAPTION_ROUTE = "/v1/video-generations/{job_id}/episodes/{episode_id}/recaption"
SERVER_STYLE = {"bold": "bold", "house": "house", "plain": "house"}
CAPTION_TIMEOUT_SECONDS = 600.0
EPISODE_UNCHANGED = "The delivered episode was not changed."


def server_style(local_style: str) -> str | None:
    """The server's caption recipe for the show's style, or ``None`` for a show with no captions.

    Parameters
    ----------
    local_style
        ``bold``, ``house`` (subtle), ``plain`` or ``none``, as :func:`creation.captions.resolve_caption_style`
        answers it.

    Returns
    -------
    str | None
        ``bold`` for bold, ``house`` for every other style, ``None`` for ``none``.
    """

    if local_style == "none":
        return None
    return SERVER_STYLE.get(local_style, "house")


def _refusal(status: int, body: Any) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    message = str(error.get("message") or "").strip() if isinstance(error, dict) else ""
    if status == 404 and isinstance(body, dict) and body.get("detail") == "Not Found":
        return f"This server cannot caption an episode yet. Tell the core team. {EPISODE_UNCHANGED}"
    if status in (401, 403):
        return f"The server refused (HTTP {status}): {message or api_error_text(body)[:300]} {EPISODE_UNCHANGED}"
    if status >= 500:
        return f"The server is busy or restarting (HTTP {status}). Run the same command again in a few minutes."
    return f"The server would not caption it (HTTP {status}): {message or api_error_text(body)[:300]} {EPISODE_UNCHANGED}"


def send_caption(
    *,
    base_url: str,
    headers: Mapping[str, str],
    job_id: str,
    episode_id: str,
    style: str,
    retime: bool,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bool, str]:
    """Ask the server to caption (or re-time the captions of) one delivered episode.

    Parameters
    ----------
    base_url, headers
        The server and the kit's headers on the desk's session.
    job_id, episode_id
        The episode's video generation and its id there.
    style
        The server's caption recipe (``captions/enable`` only).
    retime
        Re-time the burned captions instead (``recaption``).
    transport
        HTTP transport (tests fake the server with it).

    Returns
    -------
    tuple[bool, str]
        Whether the server took it, and the line to print.
    """

    route = RECAPTION_ROUTE if retime else ENABLE_ROUTE
    url = base_url.rstrip("/") + route.format(job_id=job_id, episode_id=episode_id)
    sent = {
        **headers,
        "Idempotency-Key": f"kit-caption-{episode_id}-{uuid.uuid4().hex[:12]}",
    }
    try:
        with httpx.Client(
            transport=transport, timeout=CAPTION_TIMEOUT_SECONDS
        ) as client:
            if retime:
                response = client.post(url, headers=sent)
            else:
                response = client.put(url, headers=sent, json={"caption_style": style})
    except httpx.TimeoutException:
        return (
            False,
            "The server did not answer in time. It may still land: run a plain finish in a few minutes.",
        )
    except httpx.TransportError as exc:
        return (
            False,
            f"Could not reach the server ({type(exc).__name__}). {EPISODE_UNCHANGED}",
        )
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if not 200 <= response.status_code < 300 or not isinstance(body, dict):
        return False, _refusal(response.status_code, body)
    episode = body.get("episode") if isinstance(body.get("episode"), dict) else {}
    video = body.get("video_url") or episode.get("video_url") or ""
    done = "re-timed its captions" if retime else f"burned {style} captions"
    return True, (
        f"The server {done} and delivered the new cut in place of the old one: {video}. Nothing was burned on "
        "this laptop; run a plain finish to download it."
    )


def run_caption(
    desk: Path,
    *,
    episode: int,
    style: str,
    retime: bool,
    out: TextIO,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """``caption``: find the desk's episode on the server and have it captioned there.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    style
        The show's caption style (:func:`server_style` maps it).
    retime
        Re-time the burned captions instead.
    out
        Where the answer is printed.
    api, transport
        Injected for tests.

    Returns
    -------
    int
        ``0`` when the server captioned it; ``2`` otherwise.
    """

    from creation.post.server_finish import _api, _desk_episode_id
    from creation.post.take_handles import take_coordinate

    desk = desk.expanduser().resolve()
    recipe = server_style(style)
    if recipe is None and not retime:
        print(
            "The show's caption style is none: nothing to caption. Set one with caption-style --set.",
            file=out,
        )
        return 0
    where = take_coordinate(desk, episode, "t1")
    if where is None:
        print(
            f"Refused: the desk has no filmed episode {episode}, so the server cannot find it to caption.",
            file=sys.stderr,
        )
        return 2
    try:
        base, headers = api if api is not None else _api(desk)
    except SystemExit as exc:
        print(f"Refused: {exc.code}", file=sys.stderr)
        return 2
    ok, line = send_caption(
        base_url=base,
        headers=headers,
        job_id=where.video_job_id,
        episode_id=_desk_episode_id(desk, episode),
        style=recipe or "house",
        retime=retime,
        transport=transport,
    )
    if not ok:
        print(f"Refused: {line}", file=sys.stderr)
        return 2
    print(line, file=out)
    return 0


__all__ = ["run_caption", "send_caption", "server_style"]
