"""A show's old music lock, and the take's music-free stem (no music in delivered videos since 20 Oct 2026).

Founder decision, 20 Oct 2026: every delivered video carries NO music (voice,
sound effects and room sound only; a trending sound is added inside Instagram
or TikTok at posting). ``finish`` lays no bed on any show, so the music lock
(founder decision, 8 Oct 2026: one source of music per show all season, kept
in ``series.json`` ``music_lock``) no longer decides anything in ``finish``,
and ``music-lock`` only says so. What stays here:

- :func:`remix_onto_stem`: a take the harness's music was baked into (take
  facts ``soundtrack.music.laid``) gets its stored music-free stem (voices,
  room, effects; ``soundtrack.music.stem_url``) in place of its sound, by
  mixing, not filming. ``finish`` does it on every such take.
- :func:`music_by_finish_wanted`: an existing show (created before 6 Oct 2026)
  whose lock was ``finish`` still asks the server to film with no music in the
  take (``music_by_finish``), as before. The server owns the no-music rule for
  every show; this only keeps an older server's answer the same.
- Reading a desk's stored or inferred lock (:func:`desk_music_lock`), for that.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TextIO

import httpx

from creation.ops.state import load_series, save_series

DeskMusicKind = Literal["finish", "in_take"]
DESK_MUSIC_KINDS: tuple[DeskMusicKind, ...] = ("finish", "in_take")
#: The server's route (GET reads, POST changes on purpose).
MUSIC_LOCK_ROUTE = "/v1/spines/{spine_id}/music-lock"
#: Where every lock the desk stored is logged, oldest first.
MUSIC_LOCK_LOG = Path("shared") / "music-lock-log.jsonl"

#: What each kind means, in the words the kit prints.
MUSIC_LOCK_WORDS: Mapping[str, str] = {
    "finish": "no music in the take; finish lays the show's bed",
    "in_take": "the harness's music is in each take; finish lays no bed",
}


@dataclass(frozen=True)
class DeskMusicLock:
    """Where the show's music comes from on this desk (``series.json`` ``music_lock``)."""

    kind: DeskMusicKind
    #: ``first_finish`` (the show's first finish wrote it), ``inferred`` (read from finished episodes)
    #: or ``changed`` (``music-lock --set``).
    origin: str
    #: The episode it was read from or first written on.
    episode: int | None = None
    #: ``finish``: the bed the show's episodes were finished with (relative to the desk).
    bed: str | None = None
    #: ``changed``: why, in the operator's words.
    reason: str | None = None

    def as_json(self) -> dict[str, Any]:
        """The stored form (``None`` fields left out)."""

        raw = {
            "kind": self.kind,
            "origin": self.origin,
            "episode": self.episode,
            "bed": self.bed,
            "reason": self.reason,
        }
        return {key: value for key, value in raw.items() if value is not None}

    def line(self) -> str:
        """``Music for the next take: … (why).``"""

        why = {
            "first_finish": "set by the show's first finish",
            "inferred": f"what ep{self.episode or 0:02d}, the show's first finished episode, used",
            "changed": f"changed on purpose: {self.reason}",
        }.get(self.origin, self.origin)
        bed = f" (`{Path(self.bed).name}`)" if self.bed else ""
        return f"Music for the next take: {MUSIC_LOCK_WORDS[self.kind]}{bed} ({why})."


def _parse(raw: object) -> DeskMusicLock | None:
    if not isinstance(raw, Mapping) or raw.get("kind") not in DESK_MUSIC_KINDS:
        return None
    episode = raw.get("episode")
    return DeskMusicLock(
        kind=raw["kind"],  # type: ignore[arg-type]
        origin=str(raw.get("origin") or "inferred"),
        episode=int(episode)
        if isinstance(episode, int) and not isinstance(episode, bool)
        else None,
        bed=str(raw["bed"]) if raw.get("bed") else None,
        reason=str(raw["reason"]) if raw.get("reason") else None,
    )


def stored_desk_music_lock(desk: Path) -> DeskMusicLock | None:
    """The desk's stored lock (``series.json`` ``music_lock``), or ``None``."""

    try:
        return _parse(load_series(desk).extra.get("music_lock"))
    except (OSError, ValueError, KeyError):
        return None


def _finished_episodes(desk: Path) -> list[int]:
    found: list[int] = []
    for path in desk.glob("ep[0-9][0-9]"):
        if path.is_dir() and path.name[2:].isdigit():
            found.append(int(path.name[2:]))
    return sorted(found)


def infer_desk_music_lock(desk: Path) -> DeskMusicLock | None:
    """Read the show's music from its finished episodes: the first one with a complete take decides.

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    DeskMusicLock | None
        ``in_take`` when most of that episode's complete takes carried the
        harness's music (``music_in_take``), else ``finish`` with the bed they
        were finished with; ``None`` when no take was finished yet.
    """

    from creation.post.finish_record import finish_records

    for episode in _finished_episodes(desk):
        latest: dict[str, Any] = {}
        for record in finish_records(desk, episode):
            if record.complete and not record.edits:
                latest[record.take_id] = record
        if not latest:
            continue
        kinds = Counter(
            "in_take" if record.music_in_take else "finish"
            for record in latest.values()
        )
        kind: DeskMusicKind = (
            "in_take" if kinds["in_take"] > kinds["finish"] else "finish"
        )
        beds = Counter(
            record.bed
            for record in latest.values()
            if record.bed and not record.music_in_take
        )
        bed = (
            min(beds, key=lambda name: (-beds[name], name))
            if kind == "finish" and beds
            else None
        )
        return DeskMusicLock(kind=kind, origin="inferred", episode=episode, bed=bed)
    return None


def store_desk_music_lock(
    desk: Path, lock: DeskMusicLock, *, out: TextIO | None = None
) -> DeskMusicLock:
    """Write ``lock`` to ``series.json`` and log it in ``shared/music-lock-log.jsonl``.

    Parameters
    ----------
    desk
        Series desk.
    lock
        The lock to keep.
    out
        Where the one-line note goes (``None``: stderr).

    Returns
    -------
    DeskMusicLock
        ``lock``.
    """

    try:
        series = load_series(desk)
        series.extra["music_lock"] = lock.as_json()
        save_series(desk, series)
    except (OSError, ValueError, KeyError) as exc:
        print(
            f"!! the show's music lock could not be kept on the desk: {exc}",
            file=out or sys.stderr,
        )
        return lock
    log = desk / MUSIC_LOCK_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **lock.as_json()}
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    print(f"Music lock kept for this show: {lock.line()}", file=out or sys.stderr)
    return lock


#: Why a new desk has no music lock.
NEW_SHOW_MUSIC_IS_FRESH = (
    "This show was created since 6 Oct 2026: the harness scores every episode fresh from the shared "
    "library, following each scene's mood, so it has no music lock."
)


def lock_applies(desk: Path | None) -> bool:
    """Whether the music lock applies to this desk: existing shows only (created before 6 Oct 2026)."""

    from creation.rules_epoch import is_legacy

    return is_legacy(desk)


def desk_music_lock(desk: Path, *, out: TextIO | None = None) -> DeskMusicLock | None:
    """The desk's lock: stored, else read once from its finished episodes and stored.

    Returns
    -------
    DeskMusicLock | None
        ``None`` on a new desk (no lock: scored fresh, :func:`lock_applies`),
        and when the show has no finished take yet (its first finish writes it).
    """

    if not lock_applies(desk):
        return None
    stored = stored_desk_music_lock(desk)
    if stored is not None:
        return stored
    inferred = infer_desk_music_lock(desk)
    return (
        store_desk_music_lock(desk, inferred, out=out) if inferred is not None else None
    )


def remix_onto_stem(
    take: Path,
    stem_url: str,
    out: Path,
    *,
    fetch: Callable[[str, Path], Path] | None = None,
) -> Path:
    """The take's picture over its music-free stem: the baked-in music is gone, nothing is filmed.

    Parameters
    ----------
    take
        The take as filmed (its sound is the dialogue track with the harness's music in it).
    stem_url
        The track as sent minus its music (voices, room and effects), on the same timeline.
    out
        Where the new take goes (``take-epNN-tK-stem-vN.mp4``); the stem is saved beside it.
    fetch
        ``(url, dest) -> dest``; ``None`` downloads it.

    Returns
    -------
    Path
        ``out``: the video stream copied, the stem padded or cut to the picture's length.
    """

    from creation.post.audio_service import download
    from creation.post.media import probe_video, run_ffmpeg

    stem = (fetch or download)(stem_url, out.with_suffix(".stem.wav"))
    seconds = probe_video(take).duration_seconds
    run_ffmpeg(
        ["-i", str(take), "-i", str(stem), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
         "-af", "apad", "-t", f"{seconds:.3f}", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
    )  # fmt: skip
    return out


def music_by_finish_wanted(
    desk: Path | None, run: _Api, spine: Mapping[str, Any]
) -> bool:
    """Whether a film run asks the harness for no music in its takes because finish lays the show's theme.

    Parameters
    ----------
    desk
        Series desk (``None``: no desk, never).
    run
        Open API session (reads the server's lock when the desk has none; free).
    spine
        The show's spine.

    Returns
    -------
    bool
        True only on an existing show (created before 6 Oct 2026) whose music
        is laid in finish: the desk's lock, else the server's. A new show is
        never locked, so its film body is exactly as before.
    """

    if desk is None or not lock_applies(desk):
        return False
    lock = desk_music_lock(desk)
    if lock is not None:
        return lock.kind == "finish"
    server = server_music_lock(run, str(spine.get("spine_id") or ""))
    found = server.get("music_lock") if server is not None else None
    return isinstance(found, Mapping) and found.get("kind") == "finish"


class _Api(Protocol):
    """The slice of :class:`creation.harness.session.DramaApiRunSession` used here."""

    def get_optional(self, path: str) -> tuple[int, Any]: ...

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]: ...

    def spine(self, spine_id: str) -> dict[str, Any]: ...


def server_music_lock(run: _Api, spine_id: str) -> dict[str, Any] | None:
    """``GET …/music-lock`` (free), or ``None`` on an older server or no connection."""

    try:
        status, answer = run.get_optional(MUSIC_LOCK_ROUTE.format(spine_id=spine_id))
    except httpx.TransportError:
        return None
    if 200 <= status < 300 and isinstance(answer, Mapping):
        return dict(answer)
    return None


__all__ = [
    "DESK_MUSIC_KINDS",
    "MUSIC_LOCK_LOG",
    "MUSIC_LOCK_ROUTE",
    "MUSIC_LOCK_WORDS",
    "DeskMusicKind",
    "DeskMusicLock",
    "desk_music_lock",
    "infer_desk_music_lock",
    "server_music_lock",
    "store_desk_music_lock",
    "stored_desk_music_lock",
    "NEW_SHOW_MUSIC_IS_FRESH",
    "lock_applies",
    "music_by_finish_wanted",
    "remix_onto_stem",
]
