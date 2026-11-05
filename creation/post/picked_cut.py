"""``fictora-produce cut``: the server's punchy series cut from episodes the human picked.

The picked episodes can come from any of the creator's shows (an operator who made one show per
episode still gets one cut). The kit sends the pick and prints what the server made. Nothing is
cut on this laptop, no state is kept, and the files stay on the server (links are printed, as the
show's own series cut does).

New music (founder, 4 Nov 2026): the edit adds no music by default. ``--music-price`` prints the
server's question ("New music for this edit costs $0.08, paid by Sokii. Make it? (yes/no)": the kit
asks first, Sokii pays) and makes nothing. It sends the picks exactly as the cut will (the human's
own files are uploaded first, as for the cut). ``--new-music`` passes the human's yes. Sokii pays for
every edit's music (5 Nov 2026: nothing is charged to anyone), so no price goes back with the yes.
The server decides what it plays under, writes and lays it; the kit only shows and asks.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from creation.post.episode_cover import (
    VIDEO_UPLOAD_MAX_BYTES,
    CoverStepError,
    Putter,
    file_sha256,
    upload_video,
)
from creation.harness.http_util import api_error_text, api_headers
from creation.post.server_finish import (
    BUSY_STATUSES,
    DEFAULT_WAIT_SECONDS,
    PROGRESS_EVERY_SECONDS,
    SERVER_FINISH_NOT_DONE,
    _body,
    _duration,
    _route_missing,
    series_cut_file_lines,
)

PICKABLE_ROUTE = "/v1/series-cuts/pickable"
CUTS_ROUTE = "/v1/series-cuts"
CUT_ROUTE = "/v1/series-cuts/{series_cut_id}"
MUSIC_OFFER_ROUTE = "/v1/series-cuts/music-offer"
#: The server needs a session header; a pick spans desks, so the kit uses one fixed id.
SESSION_ID = "kit-picked-series-cut"
POLL_SECONDS = 5.0
MIN_PICKED = 2
#: The upload helper labels every file video/mp4, so only containers that really are MP4 go up.
VIDEO_SUFFIXES = frozenset({".mp4", ".m4v"})
#: Waits before asking again when a poll hits a busy answer or a dropped connection.
POLL_RETRY_WAITS = (2.0, 4.0, 8.0, 15.0)
NEWEST_VERSION_NOTICE = (
    "Heads up: I'll make this edit from the episodes saved on our server. If you made your own final versions "
    "on your computer, with your own captions or sound, those won't be used unless you send them to me. "
    "To use them, just tell me which file goes with which episode."
)
NO_MUSIC_ROUTE = "NOT DONE: the server can't price new music for an edit yet. Tell the core team; nothing was made."
OWN_FILES_PRICE_NOTE = (
    "An episode played from your own file keeps its own sound under the new music."
)
OLD_SERVER = (
    "NOT DONE: the server can't make a cut from picked episodes yet. "
    "Tell the core team; nothing was made."
)


@dataclass(frozen=True)
class Pick:
    """One picked episode, and optionally the file the creator chose to stand in for it."""

    spine_id: str
    episode: int
    file: Path | None = None


class CutStop(Exception):
    """A run that ends with one plain line and an exit code (NOT DONE is 5, a bad ask is 2)."""

    def __init__(self, line: str, code: int = SERVER_FINISH_NOT_DONE) -> None:
        super().__init__(line)
        self.line = line
        self.code = code


def _split_ref(raw: str) -> tuple[str, str]:
    """Split ``REF=FILE`` at the last ``=`` that leaves a valid ``<where>:<number>`` ref, so ``=`` in a desk path works."""

    cuts = [len(raw), *(i for i in range(len(raw) - 1, -1, -1) if raw[i] == "=")]
    for end in cuts:
        left, _, tail = raw[:end].rpartition(":")
        if left and tail.isdecimal():
            return raw[:end], raw[end + 1 :]
    return raw, ""


def parse_ref(raw: str) -> Pick:
    """``<spine_id>:<n>`` or ``<desk path>:<n>``, optionally ``=FILE`` to use that file for the episode.

    A desk's spine id is read from its own ``production.json``; nothing new is saved.
    """

    ref, file_part = _split_ref(raw)
    left, _, tail = ref.rpartition(":")
    if not left or not tail.isdecimal() or int(tail) < 1:
        raise CutStop(
            f'"{raw}" is not an episode pick. Use <show id>:<episode number> or <desk folder>:<episode number>, '
            "with =FILE to use your own file for it (run cut --list for the ids). Nothing was made.",
            2,
        )
    chosen = check_video(file_part) if file_part else None
    desk = Path(left).expanduser()
    if desk.is_dir():
        from creation.post.desk import spine_id

        try:
            return Pick(spine_id(desk.resolve()), int(tail), chosen)
        except (ValueError, FileNotFoundError) as exc:
            raise CutStop(f"Stopped: {desk}: {exc}. Nothing was made.", 2) from exc
    if "/" in left or left.startswith(("~", ".")):
        raise CutStop(f"Stopped: no desk folder at {left}. Nothing was made.", 2)
    return Pick(left, int(tail), chosen)


def check_video(raw: str) -> Path:
    """The chosen file, or a plain stop (before anything is uploaded) when it is missing, not a video or too big."""

    path = Path(raw).expanduser()
    if not path.is_file():
        raise CutStop(f"Stopped: no file at {path}. Nothing was made or uploaded.", 2)
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise CutStop(
            f"Stopped: {path.name} is not an .mp4 or .m4v video. Nothing was made or uploaded.",
            2,
        )
    if path.stat().st_size > VIDEO_UPLOAD_MAX_BYTES:
        raise CutStop(
            f"Stopped: {path.name} is over the server's 300 MB upload limit. Nothing was made or uploaded.",
            2,
        )
    return path.resolve()


def apply_file_swaps(picks: Sequence[Pick], swaps: Sequence[str]) -> list[Pick]:
    """``--file N=PATH``: use PATH for the Nth picked episode (1 is the first to play)."""

    out = list(picks)
    for raw in swaps:
        number, sep, path = raw.partition("=")
        if not sep or not number.isdecimal() or not 1 <= int(number) <= len(out):
            raise CutStop(
                f'Stopped: "{raw}" is not N=PATH with N from 1 to {len(out)} (the order the episodes play). '
                "Nothing was made or uploaded.",
                2,
            )
        i = int(number) - 1
        out[i] = Pick(out[i].spine_id, out[i].episode, check_video(path))
    return out


def select_show(
    episodes: Sequence[Mapping[str, Any]], title: str
) -> list[Mapping[str, Any]]:
    """Every delivered episode of every show titled ``title`` (any case): shows by first delivery, episodes in order."""

    wanted = title.strip().casefold()
    chosen = [
        e
        for e in episodes
        if str(e.get("show_title") or "").strip().casefold() == wanted
    ]
    first: dict[str, str] = {}
    for e in chosen:
        key, at = str(e.get("spine_id")), str(e.get("delivered_at") or "")
        first[key] = min(first.get(key, at), at)
    return sorted(
        chosen,
        key=lambda e: (
            first[str(e.get("spine_id"))],
            str(e.get("spine_id")),
            int(e.get("episode") or 0),
        ),
    )


def pickable_lines(episodes: Sequence[Mapping[str, Any]]) -> list[str]:
    """The pickable episodes grouped by show, each with the handle to pick it by."""

    if not episodes:
        return ["No delivered episodes to pick from yet."]
    lines: list[str] = []
    last: tuple[str, str] | None = None
    for e in sorted(
        episodes,
        key=lambda e: (
            str(e.get("show_title") or "").casefold(),
            str(e.get("spine_id")),
            int(e.get("episode") or 0),
        ),
    ):
        group = (str(e.get("show_title") or "Untitled"), str(e.get("spine_id")))
        if group != last:
            lines.append(f"{group[0]}  (show id {group[1]})")
            last = group
        name = f' "{e["episode_title"]}"' if e.get("episode_title") else ""
        seconds = f", {round(float(e['seconds']))} s" if e.get("seconds") else ""
        lines.append(
            f"  Episode {e.get('episode')}{name}{seconds}   pick: {e.get('spine_id')}:{e.get('episode')}"
        )
    lines.append(
        'Cut some with: cut --episodes <pick> <pick> ...   or   cut --show "TITLE"'
    )
    return lines


def _client(
    transport: httpx.BaseTransport | None,
    api: tuple[str, Mapping[str, str]] | None,
    session_id: str = SESSION_ID,
) -> tuple[httpx.Client, str, dict[str, str]]:
    if api is None:
        from creation.harness.credentials import load_drama_api_credentials

        try:
            base, token = load_drama_api_credentials(
                Path(__file__).resolve().parents[2]
            )
        except SystemExit as exc:
            raise CutStop(f"NOT DONE: {exc.code}") from exc
        api = (base, api_headers(token, session_id))
    return (
        httpx.Client(timeout=60.0, transport=transport),
        api[0].rstrip("/"),
        dict(api[1]),
    )


def _error(body: Any) -> Mapping[str, Any]:
    """The server's error (``{"error": {code, message, details}}``), or the body itself for a flat one."""

    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return body if isinstance(body, dict) else {}


def _check(response: httpx.Response, *, what: str) -> Any:
    """The JSON body of an answer, or the plain stop for a server that cannot do this."""

    body = _body(response)
    if response.status_code == 404 and _route_missing(body):
        raise CutStop(OLD_SERVER)
    if response.status_code in BUSY_STATUSES:
        raise CutStop(
            f"NOT DONE: the server is busy or restarting (HTTP {response.status_code}). Run the same command again "
            "in a few minutes; nothing is made twice."
        )
    if (
        response.status_code == 409
        and isinstance(body, dict)
        and body.get("code") == "episode_not_delivered"
    ):
        message = str(body.get("message") or "").strip()
        raise CutStop(
            f"NOT DONE: episode {body.get('episode')} of {body.get('spine_id')} is not delivered yet"
            + (f" ({message})" if message else "")
            + ". Finish it first, or leave it out of the pick. Nothing was made."
        )
    if (
        response.status_code == 422
        and isinstance(body, dict)
        and body.get("code") == "video_url_not_ours"
    ):
        raise CutStop(
            f"NOT DONE: the server won't use that file. {body.get('message') or ''} Nothing was made.".replace(
                "  ", " "
            )
        )
    error = _error(body)
    if (
        response.status_code == 409
        and error.get("code") == "series_cut_music_unavailable"
    ):
        raise CutStop(
            f"NOT DONE: new music is not offered for this edit. {error.get('message') or ''} Nothing was made. "
            "Run it again without --new-music for the edit with each episode's own sound.".replace(
                "  ", " "
            )
        )
    if response.status_code == 404:
        raise CutStop(
            f"NOT DONE: the server does not know one of the shows picked ({api_error_text(body)[:200]})."
        )
    if not response.is_success or not isinstance(body, dict):
        raise CutStop(
            f"NOT DONE: the server did not {what} (HTTP {response.status_code}: {api_error_text(body)[:200]})."
        )
    return body


def _get(
    client: httpx.Client, url: str, headers: Mapping[str, str], *, what: str
) -> Any:
    try:
        return _check(client.get(url, headers=dict(headers)), what=what)
    except httpx.HTTPError as exc:
        raise CutStop(
            f"NOT DONE: could not reach the server ({type(exc).__name__}). Check the internet connection, then run it again."
        ) from exc


def list_pickable(
    *,
    show: str | None = None,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[Mapping[str, Any]]:
    """The episodes the server will cut from, optionally only the shows titled ``show``."""

    client, base, headers = _client(transport, api)
    with client:
        body = _get(client, base + PICKABLE_ROUTE, headers, what="list the episodes")
    episodes = [e for e in body.get("episodes") or [] if isinstance(e, dict)]
    return select_show(episodes, show) if show else episodes


def run_cut(
    picks: Sequence[Pick],
    *,
    title: str | None = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    progress: Callable[[str], None] = lambda line: print(
        line, file=sys.stderr, flush=True
    ),
    putter: Putter | None = None,
    new_music: bool = False,
) -> list[str]:
    """Ask the server for one cut from the picked episodes and return the lines to print.

    ``new_music`` passes the human's yes to the question ``--music-price`` showed; the server makes
    another cut of the same picks with new music laid under it.

    Raises
    ------
    CutStop
        With the plain NOT DONE line when the server did not give the cut.
    """

    _check_picks(picks)
    client, base, headers = _client(transport, api)
    with client:
        entries = _entries(client, base, headers, picks, putter)
        body: dict[str, Any] = {"episodes": entries, "title": title}
        if new_music:
            body["new_music"] = True
        try:
            made = _check(
                client.post(base + CUTS_ROUTE, headers=headers, json=body),
                what="start the cut",
            )
        except httpx.HTTPError as exc:
            raise CutStop(
                f"NOT DONE: could not reach the server ({type(exc).__name__}). Run it again."
            ) from exc
        cut_id = str(made.get("series_cut_id") or "")
        if not cut_id:
            raise CutStop(
                "NOT DONE: the server did not name the cut. Tell the core team; nothing was posted."
            )
        if any(p.file is None for p in picks):
            progress(NEWEST_VERSION_NOTICE)
        return wait_for_cut(
            client,
            base,
            headers,
            cut_id,
            picked=len(picks),
            wait_seconds=wait_seconds,
            sleep=sleep,
            clock=clock,
            progress=progress,
        )


def retry_line(cut_id: str) -> str:
    """How to run this same edit again by its id (never a second ask with the picks)."""

    return f"To try again, run: fictora-produce cut-retry {cut_id}"


BEING_REMADE = (
    "Being made again: the earlier edit keeps playing until the new one is ready."
)


def wait_for_cut(
    client: httpx.Client,
    base: str,
    headers: Mapping[str, str],
    cut_id: str,
    *,
    picked: int | None,
    wait_seconds: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    progress: Callable[[str], None],
) -> list[str]:
    """Ask the server about one edit until it is ready or failed, and return the lines to print.

    Raises
    ------
    CutStop
        With the plain NOT DONE line when the edit failed or is still being made at the deadline.
    """

    url = base + CUT_ROUTE.format(series_cut_id=cut_id)
    deadline, said = clock() + wait_seconds, clock()
    told_remade = False
    while True:
        body = _poll(client, url, headers, deadline=deadline, clock=clock, sleep=sleep)
        status = str(body.get("status") or "")
        # A cut that is queued or making yet still carries its series_cut is a ready edit being made again.
        if (
            not told_remade
            and status in ("queued", "making")
            and isinstance(body.get("series_cut"), dict)
        ):
            progress(BEING_REMADE)
            told_remade = True
        if status == "ready":
            count = picked or len(body.get("episodes") or [])
            return _ready_lines(body, count, cut_id)
        if status == "failed":
            reason = str(body.get("reason") or "no reason given")
            raise CutStop(
                f"NOT DONE: the server could not make the cut ({reason}). Nothing was posted. {retry_line(cut_id)}"
            )
        if clock() >= deadline:
            raise CutStop(
                f"NOT DONE: the cut is still being made after {_duration(wait_seconds)}. Later, run "
                f"fictora-produce cut-retry {cut_id}: it waits for this same edit, starts it again only if it "
                "stalled, and never makes a second one."
            )
        if clock() - said >= PROGRESS_EVERY_SECONDS:
            progress(
                f"Still making the cut ({status or 'working'}); waiting up to {_duration(wait_seconds)}."
            )
            said = clock()
        sleep(POLL_SECONDS)


def _check_picks(picks: Sequence[Pick]) -> None:
    if len(picks) < MIN_PICKED:
        raise CutStop(
            f"Stopped: pick at least {MIN_PICKED} episodes (got {len(picks)}). Nothing was made.",
            2,
        )
    if len({(p.spine_id, p.episode) for p in picks}) != len(picks):
        raise CutStop("Stopped: an episode is picked twice. Nothing was made.", 2)
    for p in picks:
        if p.file is not None:
            check_video(str(p.file))


def _entries(
    client: httpx.Client,
    base: str,
    headers: Mapping[str, str],
    picks: Sequence[Pick],
    putter: Putter | None,
) -> list[dict[str, Any]]:
    """The picks as the server takes them, each chosen file uploaded once (the cut and its price send the same)."""

    if any(p.file for p in picks):
        _get(client, base + PICKABLE_ROUTE, headers, what="check the pick")
    urls = _upload_once(client, base, headers, picks, putter)
    return [
        {
            "spine_id": p.spine_id,
            "episode": p.episode,
            **({"video_url": urls[p.file]} if p.file else {}),
        }
        for p in picks
    ]


def music_price(
    picks: Sequence[Pick],
    *,
    title: str | None = None,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    putter: Putter | None = None,
) -> list[str]:
    """The server's question for new music on this edit; nothing is made or charged.

    The picks go exactly as the cut sends them: a pick the human gave a file for is uploaded first (once
    per file, as the cut does), so the price is the price of the edit that will be made.
    """

    _check_picks(picks)
    client, base, headers = _client(transport, api)
    with client:
        entries = _entries(client, base, headers, picks, putter)
        try:
            response = client.post(
                base + MUSIC_OFFER_ROUTE,
                headers=headers,
                json={"episodes": entries, "title": title},
            )
        except httpx.HTTPError as exc:
            raise CutStop(
                f"NOT DONE: could not reach the server ({type(exc).__name__}). Run it again."
            ) from exc
        if response.status_code in (404, 405) and _route_missing(_body(response)):
            raise CutStop(NO_MUSIC_ROUTE)
        offer = _check(response, what="price new music")
    lines = music_offer_lines(offer)
    if offer.get("offered") and any(p.file for p in picks):
        lines.insert(-1, OWN_FILES_PRICE_NOTE)
    return lines


def music_offer_lines(offer: Mapping[str, Any]) -> list[str]:
    """What the server offers, said so the human can answer yes or no."""

    if not offer.get("offered"):
        reason = str(offer.get("unavailable_reason") or "the server did not say why")
        return [f"No new music for this edit: {reason}", "Nothing was made or charged."]
    lines = [str(offer.get("question") or "Add new music written for this edit?")]
    if offer.get("what_changes"):
        lines.append(str(offer["what_changes"]))
    under = [str(n) for n in offer.get("episodes_with_music") or []]
    if under:
        lines.append(
            f"It plays under picked episode(s) {', '.join(under)}; the others keep their own sound."
        )
    lines.append(
        "Nothing was made or charged. Ask the human this question; only if they say yes, "
        "run the same cut with --new-music."
    )
    return lines


def _poll(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    *,
    deadline: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    check: Callable[[httpx.Response], Any] | None = None,
) -> Any:
    """One status answer, asking again after a busy answer or dropped connection until ``deadline``.

    ``check`` reads the answer (default: the cut's own refusals).
    """

    attempt = 0
    while True:
        try:
            response = client.get(url, headers=dict(headers))
        except httpx.TransportError as exc:
            if clock() >= deadline:
                raise CutStop(
                    f"NOT DONE: could not reach the server ({type(exc).__name__}). Check the internet connection, "
                    "then run the same command again; the server keeps the same cut."
                ) from exc
        except httpx.HTTPError as exc:
            raise CutStop(
                f"NOT DONE: could not reach the server ({type(exc).__name__}). Run it again."
            ) from exc
        else:
            if response.status_code not in BUSY_STATUSES or clock() >= deadline:
                if check is not None:
                    return check(response)
                return _check(response, what="give the cut")
        sleep(POLL_RETRY_WAITS[min(attempt, len(POLL_RETRY_WAITS) - 1)])
        attempt += 1


class _Run:
    """The two-call session shape ``upload_video`` wants, over the cut's own client."""

    def __init__(
        self, client: httpx.Client, base: str, headers: Mapping[str, str]
    ) -> None:
        self._client, self._base, self._headers = client, base, dict(headers)

    def post_optional(self, route: str, body: Mapping[str, Any]) -> tuple[int, Any]:
        response = self._client.post(
            self._base + route, headers=self._headers, json=dict(body)
        )
        return response.status_code, _body(response)


def _upload_once(
    client: httpx.Client,
    base: str,
    headers: Mapping[str, str],
    picks: Sequence[Pick],
    putter: Putter | None,
) -> dict[Path, str]:
    """Upload each chosen file once, even when several picks (or a copy of it) use it."""

    run, by_content, urls = _Run(client, base, headers), {}, {}
    for path in dict.fromkeys(p.file for p in picks if p.file):
        sha = file_sha256(path)
        if sha not in by_content:
            try:
                by_content[sha] = upload_video(run, path, putter=putter)
            except (CoverStepError, httpx.HTTPError) as exc:
                raise CutStop(
                    f"NOT DONE: could not upload {path.name} ({exc}). Nothing was made."
                ) from exc
        urls[path] = by_content[sha]
    return urls


def desk_cut_lines(
    desk: Path,
    episodes: Sequence[int],
    *,
    wait_seconds: float | None = None,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    **run_kwargs: Any,
) -> tuple[bool, list[str]]:
    """``join --desk D --episodes …``: a picked cut of the desk's named episodes, in that order.

    No files are named here, so the server uses the newest delivered version of each episode and the
    cut goes straight ahead: nothing is asked and no ``video_url`` is sent. An older server without
    the picked-cut route stops with the plain NOT DONE line; nothing is made.
    """

    from creation.post.desk import spine_id

    try:
        spine = spine_id(desk.expanduser().resolve())
        picks = [Pick(spine, int(n)) for n in episodes]
        wait = wait_seconds if wait_seconds is not None else DEFAULT_WAIT_SECONDS
        return True, run_cut(
            picks, wait_seconds=wait, api=api, transport=transport, **run_kwargs
        )
    except CutStop as stop:
        return False, [stop.line]
    except (ValueError, FileNotFoundError) as exc:
        return False, [f"NOT DONE: no series cut. {exc}"]


def _ready_lines(body: Mapping[str, Any], picked: int, cut_id: str) -> list[str]:
    cut = body.get("series_cut")
    if not isinstance(cut, dict):
        raise CutStop(
            "NOT DONE: the server said the cut is ready but sent no files. Tell the core team."
        )
    reason = str(body.get("reason") or "").strip()
    lines = [
        f"The server cut these {picked} picked episodes by itself; nothing was cut on this laptop.",
        *([f"Server note: {reason}", retry_line(cut_id)] if reason else []),
        *_music_lines(body.get("music"), cut_id),
        *series_cut_file_lines(cut),
        f'Edit id: {cut_id}. Rename it with: fictora-produce cut-rename {cut_id} "NAME". Save it with the Sokii '
        f"mark for Instagram and TikTok with: fictora-produce cut-save {cut_id} --part N (no --part: the whole edit).",
    ]
    # A failed remake and failed music both offer the same retry; say it once.
    return list(dict.fromkeys(lines))


def _music_lines(music: Any, cut_id: str) -> list[str]:
    """The edit's new music as the server reports it (nothing when none was asked for)."""

    if not isinstance(music, dict):
        return []
    failed = music.get("state") == "failed"
    message = str(music.get("message") or "")
    if not message and failed:
        message = "it could not be made; the edit has no added music."
    lines = [f"New music: {message}"] if message else []
    return [*lines, retry_line(cut_id)] if failed else lines


def cut_command(args: Any) -> int:
    """``cut --list | --episodes REF… | --show TITLE``: returns the exit code (0 done, 5 NOT DONE, 2 bad ask)."""

    try:
        if args.list and (
            args.file
            or args.title
            or args.wait is not None
            or args.music_price
            or args.new_music
        ):
            raise CutStop(
                "Usage: cut --list takes only --show (and --json); drop --file, --title, --wait, --music-price "
                "and --new-music. Nothing was made.",
                2,
            )
        if args.list:
            if args.episodes:
                raise CutStop(
                    "Stopped: --list only shows; do not combine it with --episodes. Nothing was made.",
                    2,
                )
            lines = pickable_lines(list_pickable(show=args.show))
            return _emit(args, True, lines)
        if args.file and not args.show:
            raise CutStop(
                "Stopped: --file N=PATH goes with --show; with --episodes write the file as REF=FILE. Nothing was made.",
                2,
            )
        if args.episodes and args.show:
            raise CutStop(
                "Stopped: use --episodes or --show, not both. Nothing was made.", 2
            )
        if args.episodes:
            picks = [parse_ref(r) for r in args.episodes]
        elif args.show:
            picked = list_pickable(show=args.show)
            if not picked:
                raise CutStop(
                    f'NOT DONE: no delivered episodes of a show titled "{args.show}". Run cut --list. Nothing was made.'
                )
            picks = [Pick(str(e["spine_id"]), int(e["episode"])) for e in picked]
        else:
            raise CutStop(
                "Stopped: give --list, --episodes REF REF ..., or --show TITLE. Nothing was made.",
                2,
            )
        picks = apply_file_swaps(picks, args.file or [])
        if args.music_price:
            if args.new_music:
                raise CutStop(
                    "Stopped: --music-price only shows the price; ask the human, then run with --new-music. "
                    "Nothing was made.",
                    2,
                )
            return _emit(args, True, music_price(picks, title=args.title))
        wait = args.wait if args.wait is not None else DEFAULT_WAIT_SECONDS
        lines = run_cut(
            picks,
            title=args.title,
            wait_seconds=wait,
            new_music=args.new_music,
        )
        return _emit(args, True, lines)
    except CutStop as stop:
        return _emit(args, False, [stop.line], stop.code)


def _emit(args: Any, ok: bool, lines: list[str], code: int = 0) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"ready": ok, "lines": lines}, indent=2))
    else:
        print("\n".join(lines), file=sys.stdout if ok else sys.stderr)
    return code
