""" "This episode is final", kept on the server (fictora-drama #1358; 5 Nov 2026).

Server-heavy rule (founder, 15 Oct 2026): the yes lives on the server, not on the laptop.
``fictora-ops approve --gate post --episode N [--path FILE]``:

1. reads the SHA-256 of the file the human watched (``--path``, default the episode's newest
   finished file on the desk, which ``finish`` downloaded from the server);
2. sends it to ``PUT /v1/spines/{spine_id}/episodes/{episode_id}/final``. The server checks the
   file is one of that episode's saved copies and records which file, who and when; the same
   file again changes nothing;
3. reads the episode back from show standing (``GET /v1/spines/{spine_id}/standing``) and
   prints what the server holds.

Only after the server said yes does the kit write its local copy (the ``post`` gate in
``series.json`` and the ``…_final_vN_…`` file). Those are a convenience for the desk and are not
the record: when they disagree with the server, the server is right.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from creation.harness.http_util import api_error_text

#: The server's routes (fictora-drama #1358).
FINAL_ROUTE = "/v1/spines/{spine_id}/episodes/{episode_id}/final"
STANDING_ROUTE = "/v1/spines/{spine_id}/standing"
FINAL_SCHEMA = "fictora.drama-episode-final-approval.v1"
#: Seconds each call may take.
TIMEOUT_SECONDS = 30.0


class FinalNotRecorded(RuntimeError):
    """The server did not record the yes; the message is one plain line with what to do."""


@dataclass(frozen=True)
class FinalOnServer:
    """The final approval as the server holds it after the call.

    Attributes
    ----------
    recorded
        False when this file was already the episode's final (nothing changed on the server).
    final
        The episode's ``final`` from show standing (which file, who, when, ``current``).
    replaced
        The earlier final this one replaced, if any.
    """

    recorded: bool
    final: Mapping[str, Any]
    replaced: Mapping[str, Any] | None = None


def file_sha256(path: Path) -> str:
    """The SHA-256 of a file on the desk, read in chunks."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _code(body: Any) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def refusal_line(status: int, body: Any, *, episode: int) -> str:
    """One plain line for a refused final approval, saying what to do next."""

    code = _code(body)
    if status == 404 and isinstance(body, dict) and body.get("detail") == "Not Found":
        return (
            "This server cannot keep the final yet (it needs the server update for final approvals). "
            "Nothing was recorded; tell the core team."
        )
    if code == "export_not_on_episode":
        return (
            f"That file is not one of the server's saved copies of episode {episode}'s current video "
            "(a file finished on this laptop, or an older cut). Run `fictora-produce finish` for the episode, "
            "watch the file it downloads, then approve that file. Nothing was recorded."
        )
    if code == "episode_video_not_delivered" or (status == 404 and not code):
        return f"The server has no finished episode {episode} yet. Film and finish it first. Nothing was recorded."
    if status in (401, 403):
        return f"The server refused the kit's key (HTTP {status}). Check the kit's drama API token. Nothing was recorded."
    if status in (502, 503, 504):
        return f"The server is busy or restarting (HTTP {status}). Approve again in a few minutes. Nothing was recorded."
    return f"The server refused (HTTP {status}): {api_error_text(body)[:300]}. Nothing was recorded."


def approve_final(
    *,
    base_url: str,
    headers: Mapping[str, str],
    spine_id: str,
    episode_id: str,
    episode: int,
    path: Path,
    transport: httpx.BaseTransport | None = None,
) -> FinalOnServer:
    """Record on the server that ``path`` is the episode's final, then read it back.

    Parameters
    ----------
    base_url, headers
        The Drama API and the kit's headers on the desk's session.
    spine_id
        The show.
    episode_id
        The episode as the desk's story names it.
    episode
        Episode ordinal (for messages and the standing row).
    path
        The file the human watched.
    transport
        HTTP transport (tests fake the server with it).

    Returns
    -------
    FinalOnServer
        What the server holds now.

    Raises
    ------
    FinalNotRecorded
        With one plain line when the server refused, could not be reached, or did not show the yes back.
    """

    digest = file_sha256(path)
    base = base_url.rstrip("/")
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            put = client.put(
                base + FINAL_ROUTE.format(spine_id=spine_id, episode_id=episode_id),
                headers=dict(headers),
                json={"schema_version": FINAL_SCHEMA, "export_sha256": digest},
            )
            answer = _body(put)
            if not put.is_success:
                raise FinalNotRecorded(
                    refusal_line(put.status_code, answer, episode=episode)
                )
            got = client.get(
                base + STANDING_ROUTE.format(spine_id=spine_id), headers=dict(headers)
            )
    except httpx.HTTPError as exc:
        raise FinalNotRecorded(
            f"Could not reach the server ({type(exc).__name__}). Check the internet connection and approve again. "
            "Nothing was recorded on this laptop."
        ) from None
    standing = _body(got)
    rows = (
        standing.get("episodes")
        if got.is_success and isinstance(standing, dict)
        else None
    )
    row = next(
        (
            r
            for r in rows or []
            if isinstance(r, dict) and r.get("episode_ordinal") == episode
        ),
        None,
    )
    final = row.get("final") if row else None
    if not isinstance(final, dict) or final.get("export_sha256") != digest:
        raise FinalNotRecorded(
            f"The server took the yes but its standing for episode {episode} does not show this file as final. "
            "Approve again in a minute; if it still does not show, tell the core team."
        )
    replaced = answer.get("replaced") if isinstance(answer, dict) else None
    return FinalOnServer(
        recorded=bool(answer.get("recorded")) if isinstance(answer, dict) else False,
        final=final,
        replaced=replaced if isinstance(replaced, dict) else None,
    )


def final_line(episode: int, path: Path, held: FinalOnServer) -> str:
    """What the kit prints once the server holds the yes."""

    final = held.final
    who = "an app creator" if final.get("approved_by_kind") == "creator" else "the kit"
    head = (
        "Recorded on the server"
        if held.recorded
        else "Already the final on the server (nothing changed)"
    )
    line = (
        f"{head}: episode {episode} is final = {path.name} "
        f"(file {str(final.get('export_sha256', ''))[:12]}, said by {who} at {final.get('approved_at')})."
    )
    if held.replaced:
        line += f" It replaces the earlier final (file {str(held.replaced.get('export_sha256', ''))[:12]})."
    return line


def approve_desk_final(
    desk: Path,
    episode: int,
    path: Path | None = None,
    *,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[Path, FinalOnServer]:
    """The desk's ``approve --gate post``: find the watched file and record it as final on the server.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    path
        The file the human watched; default the episode's newest finished file on the desk.
    api
        ``(base_url, headers)``; default the kit's credentials on the desk's session.
    transport
        HTTP transport (tests fake the server with it).

    Returns
    -------
    tuple[Path, FinalOnServer]
        The file approved, and what the server holds.

    Raises
    ------
    FinalNotRecorded
        When there is no file to approve, the desk has no story yet, or the server did not record it.
    """

    from creation.post.desk import spine_id as desk_spine_id
    from creation.post.episode_name import newest_episode_file
    from creation.post.server_finish import _api, _desk_episode_id

    source = (
        path
        if path is not None
        else newest_episode_file(desk / f"ep{episode:02d}" / "takes", episode)
    )
    if source is None or not source.is_file():
        raise FinalNotRecorded(
            f"No finished file for episode {episode} on the desk"
            + (f" ({path} is not a file)" if path is not None else "")
            + ". Run `fictora-produce finish` for the episode, watch it, then approve. Nothing was recorded."
        )
    try:
        spine = desk_spine_id(desk)
    except ValueError as exc:
        raise FinalNotRecorded(f"{exc}. Nothing was recorded.") from None
    base, headers = api if api is not None else _api(desk)
    held = approve_final(
        base_url=base,
        headers=headers,
        spine_id=spine,
        episode_id=_desk_episode_id(desk, episode),
        episode=episode,
        path=source,
        transport=transport,
    )
    return source, held


__all__ = [
    "FINAL_ROUTE",
    "STANDING_ROUTE",
    "FinalNotRecorded",
    "FinalOnServer",
    "approve_desk_final",
    "approve_final",
    "file_sha256",
    "final_line",
    "refusal_line",
]
