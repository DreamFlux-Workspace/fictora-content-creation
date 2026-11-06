"""Each episode's copy to post on Instagram and TikTok, as the server's show standing names it.

The newest copy saved from the video the app plays is the episode's current copy. The server
owns it and the kit only reads it from ``GET /v1/spines/{spine_id}/standing``; the desk keeps
nothing about it.

* ``current_copy`` names the copy; ``current_copy_unchecked`` says the server could not read the
  video the app plays, so the copy is only the newest one saved: watch it before posting.
* A server that sends neither is read by :func:`_legacy_copy`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum, auto
from pathlib import Path
from typing import Any, Final

import httpx

STANDING_ROUTE = "/v1/spines/{spine_id}/standing"
TIMEOUT_SECONDS = 30.0
NEXT_STEP = "run finish for this episode"
#: Older saved copies the server keeps besides the current one; the laptop is nudged to match.
KEPT_PREVIOUS = 2


class CopySource(StrEnum):
    """Where the kit learned which copy to post."""

    SERVER = "server"
    SERVER_UNCHECKED = "server-unchecked"
    DESK_MATCHED = "desk-matched"


@dataclass(frozen=True)
class CurrentCopy:
    """One episode's copy to post.

    Attributes
    ----------
    source
        Where it came from; :attr:`unchecked` when it is only the newest copy saved.
    content_sha256
        The copy's digest; empty for a file on this laptop matched to the playing cut.
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

        return self.source is CopySource.SERVER_UNCHECKED

    @property
    def label(self) -> str:
        """The copy as the operator sees it: version, short digest, and the file on this laptop."""

        parts = [f"v{self.version}"] if self.version is not None else []
        if self.content_sha256:
            parts.append(f"file {self.content_sha256[:12]}")
        parts.append(
            self.desk_file.name
            if self.desk_file
            else f"not on this laptop yet: {NEXT_STEP}"
        )
        return ", ".join(parts)


@dataclass(frozen=True)
class ShowCopies:
    """Every episode's copy to post from one read of the server's show standing.

    Attributes
    ----------
    by_episode
        The copies by episode ordinal; empty when not checked.
    unconfirmed
        Episodes with a file on this laptop that cannot be shown to come from the cut the app plays.
    reason
        Why it could not be read (``the kit's key was refused`` ...); empty when checked.
    old_files
        Downloaded copies on this laptop older than the copy to post and the two before it. The
        kit never deletes them; :func:`copy_warnings` suggests the operator does.
    """

    by_episode: dict[int, CurrentCopy] = field(default_factory=dict)
    unconfirmed: frozenset[int] = frozenset()
    reason: str = ""
    old_files: tuple[Path, ...] = ()

    @property
    def checked(self) -> bool:
        """Whether the server was read (no ``reason``)."""

        return not self.reason


def not_checked(reason: str) -> ShowCopies:
    """A read of the copies that did not happen, and why."""

    return ShowCopies(reason=reason)


@dataclass(frozen=True)
class QueueStatus:
    """What an episode waits on, as the operator reads it.

    Attributes
    ----------
    text
        The status as shown in the queue.
    counts_done
        Whether it closes the episode's gates (its copy to post is known).
    """

    text: str
    counts_done: bool = False

    def __str__(self) -> str:
        return self.text


class CopyStatus:
    """Where an episode stands once only its copy to post is left (when the server was read)."""

    DONE: Final = QueueStatus("done", counts_done=True)
    UNCHECKED: Final = QueueStatus(
        "done (watch the copy before posting)", counts_done=True
    )
    WAITING: Final = QueueStatus("finish")
    UNCONFIRMED: Final = QueueStatus(
        "can't confirm the copy: don't post yet, ask Tejas to check it with the server"
    )


def copy_status(copies: ShowCopies, episode: int) -> QueueStatus:
    """The queue status of an episode whose takes are all kept.

    Parameters
    ----------
    copies
        The show's copies from the server.
    episode
        Episode ordinal.

    Returns
    -------
    QueueStatus
        Not checked (with the cause) when the server could not be read, else a :class:`CopyStatus`.
    """

    if not copies.checked:
        return QueueStatus(f"not checked: {copies.reason}")
    if episode in copies.unconfirmed:
        return CopyStatus.UNCONFIRMED
    copy = copies.by_episode.get(episode)
    if copy is None:
        return CopyStatus.WAITING
    return CopyStatus.UNCHECKED if copy.unchecked else CopyStatus.DONE


def unchecked_line(episode: int, copy: CurrentCopy) -> str:
    """The plain warning for a copy that is only the newest one saved."""

    return (
        f"Warning: episode {episode}: the server could not check which video the app plays, so the copy "
        f"to post ({copy.label}) is only the newest one saved. Watch it before posting."
    )


def unconfirmed_line(episode: int) -> str:
    """The plain warning for a file on this laptop that cannot be shown to be the copy to post."""

    return (
        f"Warning: episode {episode}: can't confirm the file on this laptop comes from the video the app "
        f"plays now, so it is not the copy to post. Don't post it yet; ask Tejas to check it with the server."
    )


def copy_warnings(copies: ShowCopies) -> list[str]:
    """The operator's warnings: the server not read, or a copy that is unchecked or unconfirmed."""

    if not copies.checked:
        return [
            f"Not checked: could not read the copies to post from the server ({copies.reason}). "
            "Run status again."
        ]
    lines = [
        (episode, unchecked_line(episode, copy))
        for episode, copy in copies.by_episode.items()
        if copy.unchecked
    ]
    lines += [(episode, unconfirmed_line(episode)) for episode in copies.unconfirmed]
    nudge = old_copies_line(copies.old_files)
    return [line for _, line in sorted(lines)] + ([nudge] if nudge else [])


def old_copies_line(files: tuple[Path, ...]) -> str:
    """A plain nudge to delete old downloaded copies by hand; empty when there are none."""

    if not files:
        return ""
    size = 0
    for path in files:
        try:
            size += path.stat().st_size
        except OSError:
            continue
    names = ", ".join(path.name for path in files)
    return (
        f"Tip: this laptop has {len(files)} old saved {'copy' if len(files) == 1 else 'copies'} "
        f"({size / 1_000_000_000:.1f} GB) older than the copy to post and the {KEPT_PREVIOUS} before it. "
        "The server keeps those three, so you can delete the old ones yourself: open each episode's "
        f"takes folder in Finder and move these files (and the .json file beside each) to the Bin: {names}. "
        "The kit never deletes them for you."
    )


def _old_local_copies(folder: Path, episode: int, copy: CurrentCopy) -> list[Path]:
    from creation.post.episode_name import finished_files

    others = [
        path
        for path, _meta in finished_files(folder, episode)
        if path != copy.desk_file
    ]
    return others[KEPT_PREVIOUS:]


def _version(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _from_server(
    record: Mapping[str, Any], source: CopySource, folder: Path, episode: int
) -> CurrentCopy:
    from creation.post.episode_name import saved_video

    url = str(record.get("video_url") or "")
    return CurrentCopy(
        source=source,
        content_sha256=str(
            record.get("content_sha256") or record.get("export_sha256") or ""
        ),
        version=_version(record.get("version")),
        desk_file=saved_video(folder, episode, url) if url else None,
    )


def _when(raw: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _from_playing_cut(meta: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    job = row.get("video_job_id")
    done = _when(meta.get("done_at"))
    changed = _when(row.get("played_cut_changed_at"))
    return (
        bool(job)
        and meta.get("generation_job_id") == job
        and done is not None
        and changed is not None
        and done >= changed
    )


class _NoCopy(Enum):
    NOT_SAVED = auto()
    UNCONFIRMED = auto()


def _final_copy(
    final: Mapping[str, Any], folder: Path, episode: int
) -> CurrentCopy | _NoCopy:
    if final.get("current") is not True:
        return _NoCopy.NOT_SAVED
    return _from_server(final, CopySource.SERVER, folder, episode)


def _desk_copy(
    row: Mapping[str, Any], folder: Path, episode: int
) -> CurrentCopy | _NoCopy:
    from creation.post.episode_name import finished_files

    files = finished_files(folder, episode)
    matched = next((path for path, meta in files if _from_playing_cut(meta, row)), None)
    if matched is not None:
        return CurrentCopy(CopySource.DESK_MATCHED, desk_file=matched)
    return _NoCopy.UNCONFIRMED if files else _NoCopy.NOT_SAVED


def _legacy_copy(
    row: Mapping[str, Any], folder: Path, episode: int
) -> CurrentCopy | _NoCopy:
    if row.get("video") != "finished":
        return _NoCopy.NOT_SAVED
    final = row.get("final")
    if isinstance(final, dict):
        return _final_copy(final, folder, episode)
    return _desk_copy(row, folder, episode)


def _episode_copy(
    row: Mapping[str, Any], desk: Path, episode: int
) -> CurrentCopy | _NoCopy:
    folder = desk / f"ep{episode:02d}" / "takes"
    if "current_copy_unchecked" not in row:
        return _legacy_copy(row, folder, episode)
    current = row.get("current_copy")
    if not isinstance(current, dict):
        return _NoCopy.NOT_SAVED
    unchecked = row["current_copy_unchecked"] is True
    return _from_server(
        current,
        CopySource.SERVER_UNCHECKED if unchecked else CopySource.SERVER,
        folder,
        episode,
    )


def _copies(standing: Any, desk: Path) -> ShowCopies:
    rows = standing.get("episodes") if isinstance(standing, dict) else None
    found = {
        row["episode_ordinal"]: _episode_copy(row, desk, row["episode_ordinal"])
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and isinstance(row.get("episode_ordinal"), int)
    }
    by_episode = {
        episode: copy
        for episode, copy in found.items()
        if isinstance(copy, CurrentCopy)
    }
    return ShowCopies(
        by_episode=by_episode,
        old_files=tuple(
            path
            for episode, copy in sorted(by_episode.items())
            for path in _old_local_copies(
                desk / f"ep{episode:02d}" / "takes", episode, copy
            )
        ),
        unconfirmed=frozenset(
            episode for episode, copy in found.items() if copy is _NoCopy.UNCONFIRMED
        ),
    )


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
        raise _NotChecked("the kit has no key") from None
    if not token:
        raise _NotChecked("the kit has no key")
    return base, api_headers(token, session_id)


def _refusal(status_code: int) -> str:
    if status_code in (401, 403):
        return "the kit's key was refused"
    if status_code == 404:
        return "the server doesn't know this story"
    return "the server had a problem"


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
        return not_checked("the server didn't answer")
    if not got.is_success:
        return not_checked(_refusal(got.status_code))
    try:
        standing = got.json()
    except ValueError:
        return not_checked("the server's answer could not be read")
    return _copies(standing, desk)


__all__ = [
    "STANDING_ROUTE",
    "CopySource",
    "CopyStatus",
    "CurrentCopy",
    "ShowCopies",
    "copy_status",
    "copy_warnings",
    "old_copies_line",
    "KEPT_PREVIOUS",
    "NEXT_STEP",
    "QueueStatus",
    "not_checked",
    "show_copies",
    "unchecked_line",
    "unconfirmed_line",
]
