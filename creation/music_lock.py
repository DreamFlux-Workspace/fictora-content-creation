"""A show's music stays the same across its episodes (founder decision, 2026-10-08).

Music is always the harness's (the 1 Oct rule): the kit never lays a file or a
description the operator chooses. But a show must sound like itself all
season. Two shows changed music between episodes because an update changed
where the music came from:

- L-20261005-2 (Seedlings): its early episodes' takes carried no music and
  ``finish`` laid the show's bed; after the server started baking its scored
  cues into the take (fictora-drama #572/#589), ``finish`` (#110) skipped the
  show's bed on every take whose facts said ``music.laid``: the show's music
  became the harness's track music mid-season.
- L-20261005-22 (Beach Court): episodes 1-3 were finished with the show's
  pinned theme; episode 4 came back with music baked in, so ``finish`` skipped
  the theme.

So the desk records where the show's music comes from, once
(``series.json`` ``music_lock``):

- ``finish``: the takes carry no music and ``finish`` lays the show's bed
  (``bed``, the harness bed pinned on the desk);
- ``in_take``: the harness's music is in each take (baked into its track, or
  the video model's), and ``finish`` lays no bed.

It is read once from the show's finished episodes (the first finished episode
decides, :func:`infer_desk_music_lock`), else written by the first ``finish``
of the show. Later ``finish`` runs honour it: a take never finished before
that comes back with music on a ``finish`` show is NOT DONE with a ``!!`` line
saying why (a take finished before keeps what it had). The server keeps the
same choice per show (``GET/POST /v1/spines/{id}/music-lock``, fictora-drama
show music lock) and, with the kit's ``music_by_finish``, films a ``finish``
show's takes with no music, so ``finish`` lays the bed as on its first
episodes.

``fictora-produce music-lock --desk D [--set finish|in-take --reason "..."]``
reads it, or changes it on purpose: the change is written to the desk (with
its reason, in ``shared/music-lock-log.jsonl`` and the episode's run notes)
and sent to the server.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TextIO

import httpx

from creation.harness.http_util import api_error_text
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
#: The server's kinds, in the kit's words.
SERVER_LOCK_WORDS: Mapping[str, str] = {
    "track": "the harness's scored music, in each take's track",
    "finish": "no music in the take; finish lays the show's theme",
    "model": "the video model's own music",
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


def desk_music_lock(desk: Path, *, out: TextIO | None = None) -> DeskMusicLock | None:
    """The desk's lock: stored, else read once from its finished episodes and stored.

    Returns
    -------
    DeskMusicLock | None
        ``None`` when the show has no finished take yet (its first finish writes it).
    """

    stored = stored_desk_music_lock(desk)
    if stored is not None:
        return stored
    inferred = infer_desk_music_lock(desk)
    return (
        store_desk_music_lock(desk, inferred, out=out) if inferred is not None else None
    )


def take_music_refusal(
    lock: DeskMusicLock | None,
    *,
    music_in_take: bool,
    music_why: str,
    finished_before: bool,
    desk: Path,
) -> str | None:
    """Why ``finish`` cannot honour the show's music on this take, or ``None`` when it can.

    Parameters
    ----------
    lock
        The desk's lock (``None``: nothing finished yet, the take decides).
    music_in_take
        The take carries the harness's music (take facts ``music.laid`` or ``model_music``).
    music_why
        Which fact says so.
    finished_before
        The take has a finish record (it keeps the music it was finished with).
    desk
        Series desk (for the command lines).

    Returns
    -------
    str | None
        The ``!!`` sentence for a take never finished before that came back
        with music on a show whose music is laid in finish; else ``None``.
    """

    if lock is None or lock.kind != "finish" or not music_in_take or finished_before:
        return None
    bed = f" (`{Path(lock.bed).name}`)" if lock.bed else ""
    return (
        f"!! NOT DONE: this show's music is laid in finish{bed}, as its earlier episodes' was, but this take came "
        f"back with music in it ({music_why}). Laying the bed would double the music and skipping it would change "
        "the show's music mid-season (L-20261005-2, L-20261005-22). Re-film the take: the kit sends "
        "music_by_finish, so the server films this show with no music in the take. Or change the show's music "
        f'on purpose: `fictora-produce music-lock --desk {desk} --set in-take --reason "..."`.'
    )


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


def server_kind(kind: DeskMusicKind, *, locked_voices: bool) -> str:
    """The server's lock for a desk kind: ``finish``; or, in the take, the track's (locked voices) or the model's."""

    if kind == "finish":
        return "finish"
    return "track" if locked_voices else "model"


def set_server_music_lock(
    run: _Api, spine: Mapping[str, Any], kind: str, *, reason: str
) -> dict[str, Any]:
    """``POST …/music-lock`` (free); a stale spine version is read again and sent once more.

    Raises
    ------
    RuntimeError
        The server is older than the route, or refused the change.
    """

    from creation.post.desk import spine_body

    body = spine_body(spine)
    sid = str(body.get("spine_id") or "")
    path = MUSIC_LOCK_ROUTE.format(spine_id=sid)
    for attempt in (1, 2):
        status, answer = run.post_optional(
            path,
            {
                "spine_version": body.get("spine_version"),
                "kind": kind,
                "reason": reason,
            },
        )
        if 200 <= status < 300 and isinstance(answer, Mapping):
            return dict(answer)
        error = answer.get("error") if isinstance(answer, Mapping) else None
        code = str(error.get("code") or "") if isinstance(error, Mapping) else ""
        if status == 409 and code == "spine_version_conflict" and attempt == 1:
            body = spine_body(run.spine(sid))
            continue
        if status in (404, 405) and not code:
            raise RuntimeError(
                "this Drama API has no music-lock route yet (an older deploy): only the desk was changed"
            )
        raise RuntimeError(
            f"the server refused the music lock (HTTP {status}): {api_error_text(answer)}"
        )
    raise AssertionError("unreachable")


def run_music_lock(
    desk: Path,
    *,
    set_to: str | None = None,
    reason: str | None = None,
    out: TextIO | None = None,
) -> DeskMusicLock | None:
    """``music-lock --desk D [--set finish|in-take --reason "..."]``: read or change the show's music (free).

    Parameters
    ----------
    desk
        Series desk bound to a story.
    set_to
        ``finish`` or ``in-take`` to change it on purpose; ``None`` to read it.
    reason
        Why (required with ``set_to``): logged on the desk and sent to the server.
    out
        Text stream.

    Returns
    -------
    DeskMusicLock | None
        The desk's lock after the call.

    Raises
    ------
    ValueError
        ``--set`` without ``--reason``, or an unknown kind.
    """

    from creation.ops.notes import append_run_note
    from creation.post.desk import open_api, refresh_spine
    from creation.production_state import load_production
    from creation.voice_mode import show_voices

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    kind: DeskMusicKind | None = None
    if set_to is not None:
        kind = set_to.replace("-", "_")  # type: ignore[assignment]
        if kind not in DESK_MUSIC_KINDS:
            raise ValueError(f"music lock is finish or in-take, not {set_to!r}")
        if not (reason or "").strip():
            raise ValueError(
                'changing a show\'s music needs a reason: --reason "why the music changes"'
            )
    lock = desk_music_lock(desk, out=out)
    episode = load_production(desk).episode_ordinal
    run = open_api(desk, episode)
    try:
        spine = refresh_spine(run, desk, episode)
        if kind is not None:
            text = " ".join(str(reason).split())
            bed = lock.bed if lock is not None and kind == "finish" else None
            lock = store_desk_music_lock(
                desk,
                DeskMusicLock(
                    kind=kind, origin="changed", episode=episode, bed=bed, reason=text
                ),
                out=out,
            )
            if (desk / f"ep{episode:02d}" / "run-notes.md").is_file():
                append_run_note(
                    desk / f"ep{episode:02d}",
                    f"Music lock changed on purpose: {lock.line()}",
                )
            wanted = server_kind(kind, locked_voices=show_voices(run, spine).locked)
            try:
                answer = set_server_music_lock(run, spine, wanted, reason=text)
            except RuntimeError as exc:
                print(f"!! {exc}", file=out)
            else:
                if answer.get("notice"):
                    print(str(answer["notice"]), file=out)
        server = server_music_lock(run, str(spine.get("spine_id") or ""))
    finally:
        run.client.close()
    print(
        lock.line()
        if lock is not None
        else "Music for the next take: not set yet (the show's first finish sets it).",
        file=out,
    )
    if server is not None and isinstance(server.get("music_lock"), Mapping):
        server_lock = server["music_lock"]
        mixed = (
            " Its filmed episodes used more than one kind of music."
            if server.get("mixed")
            else ""
        )
        print(
            f"The harness: {SERVER_LOCK_WORDS.get(str(server_lock.get('kind')), server_lock.get('kind'))} "
            f"({server.get('source')}).{mixed}",
            file=out,
        )
    print(
        f'Change it on purpose: fictora-produce music-lock --desk {desk} --set finish|in-take --reason "..."',
        file=out,
    )
    return lock


__all__ = [
    "DESK_MUSIC_KINDS",
    "MUSIC_LOCK_LOG",
    "MUSIC_LOCK_ROUTE",
    "MUSIC_LOCK_WORDS",
    "DeskMusicKind",
    "DeskMusicLock",
    "desk_music_lock",
    "infer_desk_music_lock",
    "run_music_lock",
    "server_kind",
    "server_music_lock",
    "set_server_music_lock",
    "store_desk_music_lock",
    "stored_desk_music_lock",
    "take_music_refusal",
]
