"""Finish an episode on the server and download it (generation speed plan item 01, phase 4; 30 Oct 2026).

The server finishes every episode the kit films, the same way it finishes an
app creator's episode: one mix at -14 loudness, house captions, the hook title,
the Sokii mark and the cover. The laptop no longer re-finishes takes one by
one (that took 8.5-13 minutes per episode). Server-heavy rule (founder,
15 Oct 2026): the finished episode, its cover and "done" live on the server;
the kit only asks, waits and downloads.

``fictora-produce finish --desk D --episode N`` (and ``join``, the same call):

1. asks ``POST /v1/spines/{spine_id}/episodes/{episode_id}/posting-copy``
   (fictora-drama #1166). The server makes the copy to post on Instagram and
   TikTok exactly as the app's Save makes it, saves it before it answers, and
   records the episode as done on the server. A repeat is a lookup: the stored
   copy comes back at once and nothing is made again;
2. prints a progress line while it waits, up to ``--wait`` seconds;
3. downloads the video under today's readable name
   (``epNN/takes/<slug>_epNN_draft_vN_<date>.mp4``, :mod:`creation.post.episode_name`)
   and the cover as ``epNN/takes/cover-epNN-vN.jpg``. A file the desk already
   has for the same server copy is reused, never downloaded twice.

There is no laptop fallback: when the server cannot finish the episode the
command stops with one plain line the operator can act on, and nothing is
made on the laptop. ``fictora-ops approve --gate post`` still makes the
``…_final_vN_…`` copy of the downloaded file.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

import httpx

from creation.harness.http_util import api_error_text

#: The server's route (fictora-drama #1166).
POSTING_COPY_ROUTE = "/v1/spines/{spine_id}/episodes/{episode_id}/posting-copy"
#: The answer's schema the kit reads.
POSTING_COPY_SCHEMA = "fictora.drama-episode-posting-copy.v1"
#: How long the kit waits for the server's finished copy by default (seconds).
DEFAULT_WAIT_SECONDS = 15 * 60.0
#: Seconds between "still finishing" lines.
PROGRESS_EVERY_SECONDS = 15.0
#: Seconds one download (video or cover) may take.
DOWNLOAD_TIMEOUT_SECONDS = 300.0
#: Exit code when the episode was not finished (the same NOT DONE code ``finish`` and ``join`` used).
SERVER_FINISH_NOT_DONE = 5
#: Browser user agent: the media host refuses some default client agents.
_MEDIA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    )
}

Say = Callable[[str], None]


class ServerFinishError(RuntimeError):
    """The server did not give the finished episode; the message is one plain line with what to do."""


#: The server's stop when a take of the episode said a line wrong and the human has not chosen yet.
LINE_FINDINGS_OPEN = "take_line_findings_open"
#: The existing take decision route: Keep it is ``use`` with ``accept_line_findings``.
TAKE_DECISION_ROUTE = "/v1/video-generations/{job_id}/takes/{take_index}/decision"


class LineFindingsOpen(ServerFinishError):
    """The server will not hand over the episode while a take's line findings wait on the human.

    ``takes`` is the server's list: each take's run, index, number, listen link and findings.
    """

    def __init__(self, takes: list[dict[str, Any]], *, episode: int) -> None:
        self.takes = takes
        self.episode = episode
        super().__init__(line_findings_text(takes, episode=episode))


def _take_label(take: Mapping[str, Any]) -> str:
    number = take.get("take_number") or take.get("take_index")
    return f"t{number}" if number else "a take"


def line_findings_text(takes: list[dict[str, Any]], *, episode: int) -> str:
    """The plain stop for open line findings: each line that came out wrong, and the two ways on."""

    rows: list[str] = [
        f"Episode {episode} has lines that came out wrong. Listen, then keep the take or film it again; "
        "nothing is posted until the human chooses."
    ]
    for take in takes:
        label = _take_label(take)
        if take.get("listen_url"):
            rows.append(f"  {label}: listen: {take['listen_url']}")
        for finding in take.get("findings") or []:
            if not isinstance(finding, Mapping):
                continue
            words = str(finding.get("message") or "").strip() or (
                f'{finding.get("kind", "line")}: script "{finding.get("line", "")}", heard "{finding.get("heard", "")}"'
            )
            window = ""
            if finding.get("listen_start_seconds") is not None:
                window = f" (at {float(finding['listen_start_seconds']):.1f} s)"
            rows.append(f"  {label}: {words}{window}")
    labels = sorted({_take_label(take) for take in takes})
    rows.append(
        f"To keep it as it is: run finish again with --keep-line-findings {labels[0] if len(labels) == 1 else 'all'}. "
        f'To film it again: `fictora-produce film --desk D --episode {episode} --take {labels[0]} --cause "…"` '
        "(priced first)."
    )
    return "\n".join(rows)


def _open_takes(body: Any) -> list[dict[str, Any]] | None:
    """The takes in a ``take_line_findings_open`` refusal, else ``None``."""

    if _error_code(body) != LINE_FINDINGS_OPEN:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    takes = details.get("takes") if isinstance(details, dict) else None
    return (
        [dict(t) for t in takes if isinstance(t, Mapping)]
        if isinstance(takes, list)
        else []
    )


@dataclass(frozen=True)
class ServerFinish:
    """What one server finish did.

    ``video`` is the downloaded file to post (``None`` when the server did not
    give one), ``cover`` the downloaded cover, ``error`` the plain stop line.
    """

    episode: int
    video: Path | None = None
    cover: Path | None = None
    error: str | None = None
    cached: bool = False
    reused: bool = False
    done_at: str | None = None
    generation_job_id: str | None = None
    seconds: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """True when the finished video is on the laptop."""

        return self.error is None and self.video is not None

    def summary_lines(self) -> list[str]:
        """The lines printed for the operator."""

        if not self.complete:
            return [f"NOT DONE: episode {self.episode} was not finished. {self.error}"]
        how = (
            "already on this laptop"
            if self.reused
            else "the server's stored copy (nothing made again)"
            if self.cached
            else f"finished on the server in {_duration(self.seconds)}"
        )
        lines = [
            f"Episode {self.episode} is ready to post on Instagram and TikTok ({how}).",
            f"  Video: {self.video}",
            f"  Cover: {self.cover}" if self.cover else "  Cover: none this time",
        ]
        if self.done_at:
            lines.append(f"  Recorded as done on the server at {self.done_at}.")
        lines += [f"  {note}" for note in self.notes]
        return lines

    def as_json(self) -> dict[str, Any]:
        """The result as JSON (``--json``)."""

        return {
            "episode": self.episode,
            "complete": self.complete,
            "video": str(self.video) if self.video else None,
            "cover": str(self.cover) if self.cover else None,
            "error": self.error,
            "cached": self.cached,
            "reused": self.reused,
            "done_at": self.done_at,
            "generation_job_id": self.generation_job_id,
            "seconds": round(self.seconds, 1),
            "notes": list(self.notes),
        }


def _duration(seconds: float) -> str:
    whole = max(0, round(seconds))
    return f"{whole // 60} min {whole % 60:02d} s" if whole >= 60 else f"{whole} s"


def _stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def posting_copy_path(spine_id: str, episode_id: str) -> str:
    """The Drama API path for one episode's posting copy."""

    return POSTING_COPY_ROUTE.format(spine_id=spine_id, episode_id=episode_id)


def _route_missing(body: Any) -> bool:
    """A 404 from a server without the route (FastAPI's bare ``Not Found``)."""

    return isinstance(body, dict) and body.get("detail") == "Not Found"


def _error_code(body: Any) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def refusal_line(status: int, body: Any, *, episode: int) -> str:
    """One plain line for a server answer that is not the finished episode.

    Parameters
    ----------
    status
        HTTP status.
    body
        Parsed answer (or text).
    episode
        Episode ordinal.

    Returns
    -------
    str
        What happened and what to do next.
    """

    code = _error_code(body)
    if status == 404 and _route_missing(body):
        return (
            "This server cannot finish kit episodes yet (it needs the server update for kit finishing). "
            "Tell the core team; nothing was made or charged."
        )
    if code == "episode_video_not_delivered" or (status == 404 and not code):
        return (
            f"The server has no finished episode {episode} yet. Film it first, or wait until filming "
            "is complete, then run finish again. Nothing was charged."
        )
    if code == "episode_export_failed":
        return (
            "The server could not make the copy to post. Nothing was saved and nothing was charged. "
            "Run finish again in a minute; if it fails again, send the core team this line: "
            f"{api_error_text(body)[:300]}"
        )
    if status in (401, 403):
        return (
            f"The server refused the kit's key (HTTP {status}). Check the kit's drama API token, "
            "then run finish again."
        )
    if status in (502, 503, 504):
        return (
            f"The server is busy or restarting (HTTP {status}). Nothing was lost: run finish again "
            "in a few minutes; a copy it already made comes back at once."
        )
    return f"The server refused (HTTP {status}): {api_error_text(body)[:300]}"


def _with_progress(
    call: Callable[[], httpx.Response],
    *,
    say: Say,
    every: float,
    wait_seconds: float,
    clock: Callable[[], float],
) -> httpx.Response:
    """Run ``call`` while a line every ``every`` seconds says the server is still finishing."""

    done = threading.Event()
    start = clock()

    def ticker() -> None:
        while not done.wait(every):
            say(
                f"  Still finishing on the server ({_duration(clock() - start)} so far, "
                f"waiting up to {_duration(wait_seconds)})."
            )

    thread = threading.Thread(target=ticker, daemon=True)
    thread.start()
    try:
        return call()
    finally:
        done.set()
        thread.join(timeout=1.0)


#: Answers from a busy or restarting server (the gateway's, with no error code from us): asked again.
BUSY_STATUSES = frozenset({502, 503, 504})
#: Waits before asking again after a busy answer or a dropped connection.
BUSY_WAITS = (10.0, 20.0)


def _send_until(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    *,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    say: Say,
) -> httpx.Response:
    """POST once, and again after a busy answer or a dropped connection, never past ``deadline``.

    The call is safe to repeat: the server keeps the copy it made and the first "done" time. A
    refusal carrying the server's own error code (``episode_export_failed``) is never asked again.

    Raises
    ------
    httpx.TimeoutException
        When the wait ran out.
    httpx.TransportError
        When the connection kept dropping.
    """

    attempt = 0
    while True:
        left = deadline - clock()
        if left <= 0:
            raise httpx.ReadTimeout("the wait ran out")
        last: httpx.Response | None = None
        try:
            last = client.post(
                url,
                headers=headers,
                json={},
                timeout=httpx.Timeout(left, connect=min(30.0, left)),
            )
        except httpx.TimeoutException:
            raise
        except httpx.TransportError as exc:
            if attempt >= len(BUSY_WAITS):
                raise
            why = f"Connection dropped ({type(exc).__name__})"
        else:
            if (
                last.status_code not in BUSY_STATUSES
                or _error_code(_body(last))
                or attempt >= len(BUSY_WAITS)
            ):
                return last
            why = f"Server busy (HTTP {last.status_code})"
        wait = BUSY_WAITS[attempt]
        attempt += 1
        if wait >= deadline - clock():
            if last is not None:
                return last
            raise httpx.ReadTimeout("the wait ran out")
        say(f"  {why}; asking again in {wait:.0f} s.")
        sleep(wait)


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def ask_posting_copy(
    *,
    base_url: str,
    headers: Mapping[str, str],
    spine_id: str,
    episode_id: str,
    episode: int,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    transport: httpx.BaseTransport | None = None,
    say: Say = _stderr,
    progress_every: float = PROGRESS_EVERY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Ask the server for the episode's posting copy and wait for it (with a timeout).

    The call is safe to repeat (the server returns its stored copy and keeps the
    first "done" time), so a busy answer (502/503/504) is asked again within the wait.

    Returns
    -------
    dict[str, Any]
        The server's answer (:data:`POSTING_COPY_SCHEMA`).

    Raises
    ------
    ServerFinishError
        With one plain line when the server refused, timed out or answered something else.
    """

    url = base_url.rstrip("/") + posting_copy_path(spine_id, episode_id)
    deadline = clock() + wait_seconds
    with httpx.Client(transport=transport) as client:
        try:
            response = _with_progress(
                lambda: _send_until(
                    client,
                    url,
                    dict(headers),
                    deadline=deadline,
                    clock=clock,
                    sleep=sleep,
                    say=say,
                ),
                say=say,
                every=progress_every,
                wait_seconds=wait_seconds,
                clock=clock,
            )
        except httpx.TimeoutException:
            raise ServerFinishError(
                f"The server did not answer within {_duration(wait_seconds)}. It may still be finishing: "
                "run finish again in a few minutes (a copy it already made comes back at once), or give "
                "finish a longer wait."
            ) from None
        except httpx.TransportError as exc:
            raise ServerFinishError(
                f"Could not reach the server ({type(exc).__name__}). Check the internet connection, "
                "then run finish again. Nothing was charged."
            ) from None
    body = _body(response)
    if not response.is_success:
        takes = _open_takes(body)
        if takes is not None:
            raise LineFindingsOpen(takes, episode=episode)
        raise ServerFinishError(
            refusal_line(response.status_code, body, episode=episode)
        )
    if not isinstance(body, dict) or not body.get("video_url"):
        raise ServerFinishError(
            "The server answered without a video to post. Tell the core team; nothing was made on the laptop."
        )
    return body


def _https_only(url: str) -> None:
    if urlparse(url).scheme != "https":
        raise ServerFinishError(
            f"The server gave a link that is not https ({url[:80]}); not downloaded."
        )


#: ``os.link`` errors that mean "this folder cannot hard-link" (exFAT, network or synced drives).
_NO_HARD_LINKS = frozenset(
    code
    for code in (
        errno.EXDEV,
        errno.EPERM,
        errno.EMLINK,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if code is not None
)


def _place(part: Path, dest: Path) -> None:
    """Move the finished download into place, never over an existing file.

    A hard link is atomic and refuses an existing name; a folder that cannot hard-link
    (exFAT, network or synced drives) gets a checked rename instead.
    """

    try:
        os.link(part, dest)
        return
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in _NO_HARD_LINKS:
            raise
    if dest.exists():
        raise FileExistsError(errno.EEXIST, "file exists", str(dest))
    os.replace(part, dest)


def download_file(
    url: str,
    dest: Path,
    *,
    transport: httpx.BaseTransport | None = None,
    kind: str,
    meta: Mapping[str, Any] | None = None,
) -> Path:
    """Download one file the server saved into ``dest``: whole or not at all, never over an existing file.

    One try: the server saved the file before it answered, so a failure is
    reported plainly instead of being asked again.

    ``meta`` is written beside the file (``<name>.json``) before the file is put in
    place, so a file on the desk always carries the server link it came from.

    Raises
    ------
    ServerFinishError
        When the download failed or the file could not be saved on this laptop.
    """

    _https_only(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f".{dest.name}.{uuid.uuid4().hex}.part")
    try:
        with httpx.Client(
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=_MEDIA_HEADERS,
            transport=transport,
        ) as client:
            with client.stream("GET", url) as response:
                if response.url.scheme != "https":
                    raise ServerFinishError(
                        f"The {kind} link left https; not downloaded."
                    )
                if not response.is_success:
                    raise ServerFinishError(
                        f"The {kind} could not be downloaded (HTTP {response.status_code}). The server keeps it: "
                        "run finish again to download it."
                    )
                with part.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        if part.stat().st_size == 0:
            raise ServerFinishError(
                f"The {kind} came back empty. Run finish again to download it."
            )
        if meta is not None:
            _write_sidecar(dest, meta)
        _place(part, dest)
    except httpx.HTTPError as exc:
        raise ServerFinishError(
            f"The {kind} download stopped ({type(exc).__name__}). The server keeps it: run finish again."
        ) from None
    except FileExistsError:
        raise ServerFinishError(
            f"Another file named {dest.name} appeared while the {kind} downloaded (two finishes at once?). "
            "Nothing was overwritten: run finish again."
        ) from None
    except OSError as exc:
        raise ServerFinishError(
            f"The {kind} could not be saved on this laptop ({exc.strerror or type(exc).__name__}: {dest.parent}). "
            "Check the disk has space and the folder can be written, then run finish again. The server keeps it."
        ) from None
    finally:
        part.unlink(missing_ok=True)
    return dest


def _sidecar(path: Path) -> Path:
    return path.with_suffix(".json")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _saved_video(folder: Path, episode: int, video_url: str) -> Path | None:
    """The episode file already downloaded for this exact server copy, if any."""

    from creation.post.episode_name import episode_files

    for path in reversed(episode_files(folder, episode)):
        meta = _read_json(_sidecar(path))
        if meta and meta.get("video_url") == video_url and path.is_file():
            return path
    return None


def _write_sidecar(path: Path, body: Mapping[str, Any]) -> None:
    _sidecar(path).write_text(json.dumps(dict(body), indent=2) + "\n", encoding="utf-8")


def _desk_episode_id(desk: Path, episode: int) -> str:
    """The episode's id from the desk's saved story (``episode_NN`` when it has none)."""

    from creation.post.desk import saved_spine
    from creation.spine_view import episode_id_for

    saved = saved_spine(desk, episode)
    return episode_id_for(saved[0] if saved else {}, episode)


def _api(desk: Path) -> tuple[str, dict[str, str]]:
    """The server address and the kit's headers on the desk's session."""

    from creation.harness.credentials import load_drama_api_credentials
    from creation.harness.http_util import api_headers
    from creation.production_state import load_production

    state = load_production(desk)
    base, token = load_drama_api_credentials(Path(__file__).resolve().parents[2])
    return base, api_headers(token, state.session_id)


def _kept_all(takes: list[dict[str, Any]], keep: tuple[str, ...]) -> bool:
    """Whether the human's ``--keep-line-findings`` covers every take the server stopped on."""

    wanted = {k.strip().lower() for k in keep}
    if not wanted or not takes:
        return False
    return "all" in wanted or all(_take_label(t) in wanted for t in takes)


def keep_take(
    base_url: str,
    headers: Mapping[str, str],
    take: Mapping[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """Record Keep it on one take whose lines came out wrong (the existing take decision; free).

    Raises
    ------
    ServerFinishError
        When the server refused the decision.
    """

    job = str(take.get("generation_job_id") or "")
    index = take.get("take_index")
    if not job or not index:
        raise ServerFinishError(
            "The server did not say which take to keep. Tell the core team; nothing was posted."
        )
    url = base_url.rstrip("/") + TAKE_DECISION_ROUTE.format(
        job_id=job, take_index=int(index)
    )
    try:
        with httpx.Client(timeout=60.0, transport=transport) as client:
            response = client.post(
                url,
                headers=dict(headers),
                json={"action": "use", "accept_line_findings": True},
            )
    except httpx.HTTPError as exc:
        raise ServerFinishError(
            f"Could not record the keep ({type(exc).__name__}). Run finish again."
        ) from None
    if not response.is_success:
        raise ServerFinishError(
            f"The server would not record the keep for {_take_label(take)} "
            f"(HTTP {response.status_code}: {api_error_text(_body(response))[:200]})."
        )


def run_server_finish(
    desk: Path,
    *,
    episode: int,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    stream: TextIO | None = None,
    progress_every: float = PROGRESS_EVERY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    keep_line_findings: tuple[str, ...] = (),
) -> ServerFinish:
    """Ask the server for the finished episode and download it and its cover under today's names.

    Never finishes anything on the laptop. Never raises for a server or
    download failure: the result carries one plain ``error`` line.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    wait_seconds
        Longest wait for the server's answer.
    api
        ``(base_url, headers)``; default the kit's credentials on the desk's session.
    transport
        HTTP transport for the call and the downloads (tests fake the server with it).
    stream
        Where the progress lines go (default stderr).
    progress_every
        Seconds between progress lines.
    sleep, clock
        Injected for tests.
    keep_line_findings
        Takes (``tK`` or ``all``) the human chose to keep although a line came out wrong: when the
        server stops on them, Keep it is recorded on the server (the existing take decision) and
        the episode is asked for again.

    Returns
    -------
    ServerFinish
        The downloaded video and cover, or the stop line.
    """

    from creation.ops.notes import append_run_note
    from creation.post.desk import spine_id as desk_spine_id
    from creation.post.episode_cover import saved_episode_cover
    from creation.post.episode_name import next_episode_path
    from creation.ops.folder import next_versioned_path

    desk = desk.expanduser().resolve()

    def say(line: str) -> None:
        print(line, file=stream or sys.stderr, flush=True)

    run_dir = desk / f"ep{episode:02d}"
    folder = run_dir / "takes"
    started = clock()
    try:
        spine = desk_spine_id(desk)
        episode_id = _desk_episode_id(desk, episode)
        base, headers = api if api is not None else _api(desk)
        say(
            f"Asking the server for episode {episode}, finished and ready to post "
            "(captions, sound, hook title, Sokii mark, cover)…"
        )

        def ask() -> dict[str, Any]:
            return ask_posting_copy(
                base_url=base,
                headers=headers,
                spine_id=spine,
                episode_id=episode_id,
                episode=episode,
                wait_seconds=wait_seconds,
                transport=transport,
                say=say,
                progress_every=progress_every,
                sleep=sleep,
                clock=clock,
            )

        try:
            body = ask()
        except LineFindingsOpen as stop:
            if not _kept_all(stop.takes, keep_line_findings):
                raise
            for take in stop.takes:
                keep_take(base, headers, take, transport=transport)
                say(f"Kept {_take_label(take)} as it is (recorded on the server).")
            body = ask()
    except ServerFinishError as exc:
        return ServerFinish(episode, error=str(exc), seconds=clock() - started)
    except (ValueError, FileNotFoundError) as exc:
        return ServerFinish(episode, error=f"{exc}", seconds=clock() - started)
    except SystemExit as exc:  # no kit token
        return ServerFinish(episode, error=str(exc.code), seconds=clock() - started)
    seconds = clock() - started
    video_url = str(body["video_url"])
    cached = bool(body.get("cached"))
    notes: list[str] = []
    try:
        video = _saved_video(folder, episode, video_url)
        reused = video is not None
        if video is None:
            say("Downloading the finished episode…")
            video = download_file(
                video_url,
                next_episode_path(folder, desk, episode, "draft"),
                transport=transport,
                kind="video",
                meta={
                    "episode": episode,
                    "video_url": video_url,
                    "server_file_name": body.get("file_name"),
                    "source_sha256": body.get("source_sha256"),
                    "generation_job_id": body.get("generation_job_id"),
                    "done_at": body.get("done_at"),
                },
            )
    except ServerFinishError as exc:
        return ServerFinish(episode, error=str(exc), cached=cached, seconds=seconds)
    cover: Path | None = None
    cover_url = str(body.get("cover_url") or "")
    if cover_url:
        cover = saved_episode_cover(folder, episode, cover_url)
        if cover is None:
            try:
                cover = download_file(
                    cover_url,
                    next_versioned_path(folder, f"cover-ep{episode:02d}", ".jpg"),
                    transport=transport,
                    kind="cover",
                    meta={
                        "cover_url": cover_url,
                        "episode": episode,
                        "video": video.name,
                        "video_url": video_url,
                        "cover_hook": body.get("cover_hook"),
                    },
                )
            except ServerFinishError as exc:
                notes.append(f"No cover this time: {exc}")
                cover = None
    else:
        notes.append(
            "No cover this time: the server could not draw one (the video is ready to post)."
        )
    result = ServerFinish(
        episode,
        video=video,
        cover=cover,
        cached=cached,
        reused=reused,
        done_at=str(body.get("done_at") or "") or None,
        generation_job_id=str(body.get("generation_job_id") or "") or None,
        seconds=seconds,
        notes=tuple(notes),
    )
    if (run_dir / "run-notes.md").is_file():
        try:
            append_run_note(
                run_dir,
                "Finished on the server, $0\n"
                + f"Video -> {video.name}"
                + (f"; cover -> {cover.name}" if cover else "; no cover")
                + (" (already on this laptop)" if reused else "")
                + (
                    f". Server run `{result.generation_job_id}`."
                    if result.generation_job_id
                    else "."
                ),
            )
        except OSError:
            pass  # the run note is a convenience; the files are saved
    return result
