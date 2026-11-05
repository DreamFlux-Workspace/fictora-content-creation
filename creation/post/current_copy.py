"""Each episode's copy to post, as the server's show standing names it (fictora-drama #1407; 6 Nov 2026).

There is no "this episode is final" step any more (founder, 6 Nov 2026). The newest copy saved
from the video the app plays is the episode's current copy; the server owns it and the kit only
reads it from ``GET /v1/spines/{spine_id}/standing``. Server-heavy rule (founder, 15 Oct 2026):
the desk keeps nothing about it.

* ``current_copy`` names the copy; ``current_copy_unchecked`` says the server could not read the
  video the app plays, so the copy is only the newest one saved: watch it before posting.
* A server without #1407 has neither. Its ``final`` field still names the copy when ``current``;
  otherwise the copy to post is the newest file ``finish`` downloaded to the desk, flagged the same way.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

STANDING_ROUTE = "/v1/spines/{spine_id}/standing"
#: Seconds the read may take.
TIMEOUT_SECONDS = 30.0


class CopySource(StrEnum):
    """Where the kit learned which copy to post."""

    SERVER = "server"
    SERVER_UNCHECKED = "server-unchecked"
    OLD_SERVER_FINAL = "old-server-final"
    DESK_NEWEST = "desk-newest"


@dataclass(frozen=True)
class CurrentCopy:
    """One episode's copy to post.

    Attributes
    ----------
    source
        Where it came from; :attr:`unchecked` for the two that are only the newest copy.
    content_sha256
        The copy's digest; empty for the desk's newest file.
    version
        The server's version number, when it said one.
    desk_file
        The file on this laptop for that copy, if ``finish`` downloaded it.
    """

    source: CopySource
    content_sha256: str = ""
    version: int | None = None
    desk_file: Path | None = None

    @property
    def unchecked(self) -> bool:
        """Whether it is only the newest copy, not one the server checked against the playing video."""

        return self.source in (CopySource.SERVER_UNCHECKED, CopySource.DESK_NEWEST)

    @property
    def label(self) -> str:
        """The copy as the operator sees it: version, short digest, and the file on this laptop."""

        parts = [f"v{self.version}"] if self.version is not None else []
        if self.content_sha256:
            parts.append(f"file {self.content_sha256[:12]}")
        parts.append(
            self.desk_file.name if self.desk_file else "not on this laptop yet"
        )
        return ", ".join(parts)


@dataclass(frozen=True)
class ShowCopies:
    """Every episode's copy to post from one read of the server's show standing.

    Attributes
    ----------
    by_episode
        The copies by episode ordinal; empty when not checked.
    reason
        Why it could not be read (``server said 401``, ``offline`` ...); empty when checked.
    """

    by_episode: dict[int, CurrentCopy] = field(default_factory=dict)
    reason: str = ""

    @property
    def checked(self) -> bool:
        """Whether the server was read (no ``reason``)."""

        return not self.reason


def not_checked(reason: str) -> ShowCopies:
    """A read of the copies that did not happen, and why."""

    return ShowCopies(reason=reason)


class CopyStatus(StrEnum):
    """Where an episode stands once only its copy to post is left (when the server was read)."""

    DONE = "done"
    UNCHECKED = "done (watch the copy before posting)"
    WAITING = "finish"


def copy_status(copies: ShowCopies, episode: int) -> str:
    """The queue status of an episode whose takes are all kept.

    Parameters
    ----------
    copies
        The show's copies from the server.
    episode
        Episode ordinal.

    Returns
    -------
    str
        Not checked (with the cause) when the server could not be read, else a :class:`CopyStatus`.
    """

    if not copies.checked:
        return f"finish (copy to post not checked: {copies.reason})"
    copy = copies.by_episode.get(episode)
    if copy is None:
        return CopyStatus.WAITING
    return CopyStatus.UNCHECKED if copy.unchecked else CopyStatus.DONE


def is_done(status: str) -> bool:
    """Whether a queue status is done (an unchecked copy too: it carries a warning instead)."""

    return status in (CopyStatus.DONE, CopyStatus.UNCHECKED)


def unchecked_line(episode: int, copy: CurrentCopy) -> str:
    """The plain warning for a copy that is only the newest one saved."""

    if copy.source is CopySource.DESK_NEWEST:
        return (
            f"Warning: episode {episode}: this server cannot say which copy is the one to post yet, "
            f"so it is the newest file finished on this laptop ({copy.label}). Watch it before posting."
        )
    return (
        f"Warning: episode {episode}: the server could not check which video the app plays, so the copy "
        f"to post ({copy.label}) is only the newest one saved. Watch it before posting."
    )


def copy_warnings(copies: ShowCopies) -> list[str]:
    """The operator's warnings: the server not read, or a copy that is only the newest one saved."""

    if not copies.checked:
        return [
            f"Could not read the copies to post from the server ({copies.reason}). Run status again."
        ]
    return [
        unchecked_line(episode, copy)
        for episode, copy in sorted(copies.by_episode.items())
        if copy.unchecked
    ]


def _version(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _from_server(
    record: Mapping[str, Any], source: CopySource, folder: Path, episode: int
) -> CurrentCopy:
    from creation.post.server_finish import _saved_video

    url = str(record.get("video_url") or "")
    return CurrentCopy(
        source=source,
        content_sha256=str(
            record.get("content_sha256") or record.get("export_sha256") or ""
        ),
        version=_version(record.get("version")),
        desk_file=_saved_video(folder, episode, url) if url else None,
    )


def _episode_copy(
    row: Mapping[str, Any], desk: Path, episode: int
) -> CurrentCopy | None:
    from creation.post.episode_name import newest_episode_file

    folder = desk / f"ep{episode:02d}" / "takes"
    current = row.get("current_copy")
    if isinstance(current, dict):
        unchecked = row.get("current_copy_unchecked") is True
        return _from_server(
            current,
            CopySource.SERVER_UNCHECKED if unchecked else CopySource.SERVER,
            folder,
            episode,
        )
    if "current_copy_unchecked" in row or row.get("video") != "finished":
        return None
    final = row.get("final")
    if isinstance(final, dict) and final.get("current") is True:
        return _from_server(final, CopySource.OLD_SERVER_FINAL, folder, episode)
    newest = newest_episode_file(folder, episode) if folder.is_dir() else None
    return CurrentCopy(CopySource.DESK_NEWEST, desk_file=newest) if newest else None


def _copies(standing: Any, desk: Path) -> dict[int, CurrentCopy]:
    rows = standing.get("episodes") if isinstance(standing, dict) else None
    found = {
        row["episode_ordinal"]: _episode_copy(row, desk, row["episode_ordinal"])
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and isinstance(row.get("episode_ordinal"), int)
    }
    return {episode: copy for episode, copy in found.items() if copy is not None}


class _NotChecked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _kit_api(desk: Path, *, env_token_only: bool) -> tuple[str, dict[str, str]]:
    from creation.harness.credentials import (
        env_drama_api_credentials,
        load_drama_api_credentials,
    )
    from creation.harness.http_util import api_headers
    from creation.production_state import load_production

    try:
        session_id = load_production(desk).session_id
    except (ValueError, OSError):
        raise _NotChecked("no kit session on the desk") from None
    root = Path(__file__).resolve().parents[2]
    try:
        base, token = (
            env_drama_api_credentials(root / ".env")
            if env_token_only
            else load_drama_api_credentials(root)
        )
    except (SystemExit, FileNotFoundError, ValueError):
        raise _NotChecked("no kit token") from None
    if not token:
        raise _NotChecked("no kit token")
    return base, api_headers(token, session_id)


def show_copies(
    desk: Path,
    *,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = TIMEOUT_SECONDS,
    env_token_only: bool = False,
) -> ShowCopies:
    """Every episode's copy to post, from one read of the server's show standing.

    Parameters
    ----------
    desk
        Series desk.
    api
        ``(base_url, headers)``; default the kit's credentials on the desk's session.
    transport
        HTTP transport (tests fake the server with it).
    timeout
        Seconds the read may take.
    env_token_only
        Take the kit's token only from its ``.env``/environment, never from the Railway CLI.

    Returns
    -------
    ShowCopies
        Not checked, with the cause, when the desk has no story yet, the kit has no token, the
        server is unreachable or it refused.
    """

    from creation.post.desk import spine_id as desk_spine_id

    try:
        spine = desk_spine_id(desk)
    except (ValueError, OSError):
        return not_checked("no story on the desk yet")
    try:
        base, headers = (
            api if api is not None else _kit_api(desk, env_token_only=env_token_only)
        )
    except _NotChecked as exc:
        return not_checked(exc.reason)
    try:
        with httpx.Client(transport=transport, timeout=timeout) as client:
            got = client.get(
                base.rstrip("/") + STANDING_ROUTE.format(spine_id=spine),
                headers=dict(headers),
            )
    except httpx.HTTPError:
        return not_checked("offline")
    if not got.is_success:
        return not_checked(f"server said {got.status_code}")
    try:
        standing = got.json()
    except ValueError:
        return not_checked("the server's answer could not be read")
    return ShowCopies(by_episode=_copies(standing, desk))


__all__ = [
    "STANDING_ROUTE",
    "CopySource",
    "CopyStatus",
    "CurrentCopy",
    "ShowCopies",
    "copy_status",
    "copy_warnings",
    "is_done",
    "not_checked",
    "show_copies",
    "unchecked_line",
]
