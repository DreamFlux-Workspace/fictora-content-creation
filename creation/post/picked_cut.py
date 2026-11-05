"""``fictora-produce cut``: the server's punchy series cut from episodes the human picked.

The picked episodes can come from any of the creator's shows (an operator who made one show per
episode still gets one cut). The kit sends the pick and prints what the server made. Nothing is
cut on this laptop, no state is kept, and the files stay on the server (links are printed).

Parts only (founder, 5 Nov 2026): a punchy edit gives the creator Instagram and TikTok parts, never
one whole video. Before anything is made the kit asks the server for the plan (how many parts, the
number the first part carries, each part's opening and ending text), prints every part, and makes
nothing until the human says go ahead. The human can change the count, the first number or any
part's text; the server plans again and the kit prints it again.

Only what the server holds (5 Nov 2026): the edit is cut from each episode as the server keeps it.
A video file on this laptop is never uploaded for an episode; a better take has to become that
episode's take on the server first.

New music (founder, 4 Nov 2026): the edit adds no music by default. ``--music-price`` prints the
server's question ("New music for this edit costs $0.08, paid by Sokii. Make it? (yes/no)": the kit
asks first, Sokii pays) and makes nothing. ``--new-music`` passes the human's yes. The server decides
what it plays under, writes and lays it; the kit only shows and asks.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

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
PLAN_ROUTE = "/v1/series-cuts/plan"
#: The server needs a session header; a pick spans desks, so the kit uses one fixed id.
SESSION_ID = "kit-picked-series-cut"
POLL_SECONDS = 5.0
MIN_PICKED = 2
#: Waits before asking again when a poll hits a busy answer or a dropped connection.
POLL_RETRY_WAITS = (2.0, 4.0, 8.0, 15.0)
#: ``--part-text N:<side>=TEXT`` side -> the field the server takes.
TEXT_SIDES = {"opening": "opening_text", "ending": "ending_text"}
GO_AHEAD = "Go ahead? [y/N/e=edit] "
NEWEST_VERSION_NOTICE = (
    "Heads up: I'll make this edit from the episodes saved on our server. A version made on your "
    "computer, with your own captions or sound, is not used. To use a better take, make it that "
    "episode's take first, then make the edit again."
)
LOCAL_FILES_GONE = (
    "Stopped: a punchy edit is made only from the episodes saved on the server, so it can't use a video "
    "file from this computer. If an episode has a better take, make it that episode's take on the server "
    "first, then cut again. Nothing was made or uploaded."
)
NOT_CONFIRMED = "Stopped: nobody said go ahead, so nothing was made."
NO_MUSIC_ROUTE = "NOT DONE: the server can't price new music for an edit yet. Tell the core team; nothing was made."
NO_PLAN_ROUTE = "NOT DONE: the server can't plan the Instagram and TikTok parts yet. Tell the core team; nothing was made."
OLD_SERVER = (
    "NOT DONE: the server can't make a cut from picked episodes yet. "
    "Tell the core team; nothing was made."
)


@dataclass(frozen=True)
class Pick:
    """One picked episode, as the server holds it."""

    spine_id: str
    episode: int


@dataclass(frozen=True)
class PartChoice:
    """What the human set for the parts; anything left ``None`` or out is the server's suggestion.

    ``texts`` maps a part's number as printed (``Part 4``) to the fields it overrides,
    ``opening_text`` and/or ``ending_text``.
    """

    part_count: int | None = None
    start_number: int | None = None
    texts: Mapping[int, Mapping[str, str]] = field(default_factory=dict)

    def body(self) -> dict[str, Any]:
        """The optional plan fields, as the plan and the cut both take them."""

        out: dict[str, Any] = {}
        if self.part_count is not None:
            out["part_count"] = self.part_count
        if self.start_number is not None:
            out["start_number"] = self.start_number
        if self.texts:
            out["texts"] = [
                {"number": n, **dict(self.texts[n])} for n in sorted(self.texts)
            ]
        return out


class CutStop(Exception):
    """A run that ends with one plain line and an exit code (NOT DONE is 5, a bad ask is 2)."""

    def __init__(self, line: str, code: int = SERVER_FINISH_NOT_DONE) -> None:
        super().__init__(line)
        self.line = line
        self.code = code


class PartTextTooLong(CutStop):
    """The server's 422: a part's opening or ending text won't fit. ``message`` is its plain words."""

    def __init__(self, message: str, number: Any) -> None:
        self.message = message or f"The text for part {number} is too long to fit."
        self.number = number
        super().__init__(f"Stopped: {self.message} Nothing was made.", 2)


def parse_ref(raw: str) -> Pick:
    """``<spine_id>:<n>`` or ``<desk path>:<n>``.

    A desk's spine id is read from its own ``production.json``; nothing new is saved. The old
    ``REF=FILE`` form is refused: the edit uses only what the server holds for the episode.
    """

    left, _, tail = raw.rpartition(":")
    number, sep, _file = tail.partition("=")
    if left and sep and number.isdecimal():
        raise CutStop(LOCAL_FILES_GONE, 2)
    if not left or not tail.isdecimal() or int(tail) < 1:
        raise CutStop(
            f'"{raw}" is not an episode pick. Use <show id>:<episode number> or <desk folder>:<episode number> '
            "(run cut --list for the ids). Nothing was made.",
            2,
        )
    desk = Path(left).expanduser()
    if desk.is_dir():
        from creation.post.desk import spine_id

        try:
            return Pick(spine_id(desk.resolve()), int(tail))
        except (ValueError, FileNotFoundError) as exc:
            raise CutStop(f"Stopped: {desk}: {exc}. Nothing was made.", 2) from exc
    if "/" in left or left.startswith(("~", ".")):
        raise CutStop(f"Stopped: no desk folder at {left}. Nothing was made.", 2)
    return Pick(left, int(tail))


def parse_part_text(raw: str) -> tuple[int, str, str]:
    """``N:opening=TEXT`` or ``N:ending=TEXT`` -> (part number, server field, text)."""

    head, sep, text = raw.partition("=")
    number, _, side = head.partition(":")
    if (
        not sep
        or not number.strip().isdecimal()
        or side.strip() not in TEXT_SIDES
        or not text.strip()
    ):
        raise CutStop(
            f'Stopped: "{raw}" is not N:opening=TEXT or N:ending=TEXT (N is the part number). Nothing was made.',
            2,
        )
    return int(number), TEXT_SIDES[side.strip()], text.strip()


def choice_from_args(args: Any) -> PartChoice:
    """The human's part settings from ``--parts``, ``--start-number`` and ``--part-text``."""

    for name, value in (
        ("--parts", args.parts),
        ("--start-number", args.start_number),
    ):
        if value is not None and value < 1:
            raise CutStop(f"Stopped: {name} must be 1 or more. Nothing was made.", 2)
    texts: dict[int, dict[str, str]] = {}
    for raw in args.part_text or []:
        number, key, text = parse_part_text(raw)
        texts.setdefault(number, {})[key] = text
    return PartChoice(args.parts, args.start_number, texts)


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
    transport: httpx.BaseTransport | None, api: tuple[str, Mapping[str, str]] | None
) -> tuple[httpx.Client, str, dict[str, str]]:
    if api is None:
        from creation.harness.credentials import load_drama_api_credentials

        try:
            base, token = load_drama_api_credentials(
                Path(__file__).resolve().parents[2]
            )
        except SystemExit as exc:
            raise CutStop(f"NOT DONE: {exc.code}") from exc
        api = (base, api_headers(token, SESSION_ID))
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
    error = _error(body)
    if response.status_code == 422 and error.get("code") == "part_text_too_long":
        details = error.get("details")
        details = details if isinstance(details, dict) else {}
        raise PartTextTooLong(
            str(error.get("message") or "").strip(),
            error.get("number", details.get("number")),
        )
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


def _post(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
) -> httpx.Response:
    try:
        return client.post(url, headers=dict(headers), json=dict(body))
    except httpx.HTTPError as exc:
        raise CutStop(
            f"NOT DONE: could not reach the server ({type(exc).__name__}). Run it again."
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


def _entries(picks: Sequence[Pick]) -> list[dict[str, Any]]:
    return [{"spine_id": p.spine_id, "episode": p.episode} for p in picks]


def plan_parts(
    picks: Sequence[Pick],
    choice: PartChoice,
    *,
    title: str | None = None,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Mapping[str, Any]:
    """The server's plan for the Instagram and TikTok parts of this edit; nothing is made.

    Raises
    ------
    PartTextTooLong
        When a text the human set won't fit in its part.
    CutStop
        With the plain NOT DONE line when the server cannot plan.
    """

    _check_picks(picks)
    client, base, headers = _client(transport, api)
    with client:
        body = {"episodes": _entries(picks), "title": title, **choice.body()}
        response = _post(client, base + PLAN_ROUTE, headers, body)
        if response.status_code in (404, 405) and _route_missing(_body(response)):
            raise CutStop(NO_PLAN_ROUTE)
        plan = _check(response, what="plan the parts")
    if not [p for p in plan.get("parts") or [] if isinstance(p, dict)]:
        raise CutStop(
            "NOT DONE: the server planned no parts. Tell the core team; nothing was made."
        )
    return plan


def _clock(seconds: Any) -> str:
    total = round(float(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"


def plan_lines(plan: Mapping[str, Any], choice: PartChoice) -> list[str]:
    """Every planned part on its own line, then which values are the server's suggestions."""

    count, start = plan.get("part_count"), plan.get("start_number")
    lines = [
        str(plan.get("summary") or "").strip()
        or f"The server plans {count} Instagram and TikTok part(s):"
    ]
    for p in plan.get("parts") or []:
        if isinstance(p, dict):
            lines.append(
                f"Part {p.get('number')} · about {_clock(p.get('approx_seconds'))} · "
                f'starts with "{p.get("opening_text") or ""}" · ends with "{p.get("ending_text") or ""}"'
            )
    suggested_count = plan.get("suggested_part_count")
    suggested_start = plan.get("suggested_start_number")
    lines.append(
        f"{count} part(s) is the server's suggestion."
        if count == suggested_count
        else f"{count} part(s) is your choice; the server suggested {suggested_count}."
    )
    lines.append(
        f"Starting at Part {start} is the server's suggestion (it carries on from this creator's earlier "
        "parts of the show)."
        if start == suggested_start
        else f"Starting at Part {start} is your choice; the server suggested Part {suggested_start}."
    )
    changed = [
        f"Part {n} {side}"
        for n in sorted(choice.texts)
        for side, key in TEXT_SIDES.items()
        if key in choice.texts[n]
    ]
    lines.append(
        "Each opening and ending is the server's suggestion"
        + (f", except the ones you changed: {', '.join(changed)}." if changed else ".")
    )
    return lines


def _ask_number(
    ask: Callable[[str], str], say: Callable[[str], None], prompt: str, now: int
) -> int:
    while True:
        raw = ask(f"{prompt} [{now}] ").strip()
        if not raw:
            return now
        if raw.isdecimal() and int(raw) >= 1:
            return int(raw)
        say("Type a whole number, 1 or more, or press Enter to keep it.")


def edit_choice(
    plan: Mapping[str, Any],
    choice: PartChoice,
    *,
    ask: Callable[[str], str],
    say: Callable[[str], None],
) -> PartChoice:
    """Ask for a new part count, first number and any part's texts (Enter keeps each).

    Texts already set move with the parts when the first number changes, so they stay on the
    same part.
    """

    count = _ask_number(ask, say, "How many parts?", int(plan["part_count"]))
    old_start = int(plan["start_number"])
    start = _ask_number(ask, say, "Number of the first part?", old_start)
    shift = start - old_start
    texts = {n + shift: dict(t) for n, t in choice.texts.items()}
    shown = {
        int(p["number"]) + shift: p
        for p in plan.get("parts") or []
        if isinstance(p, dict) and str(p.get("number", "")).isdecimal()
    }
    while True:
        raw = ask(
            "Change a part's opening or ending text? Type the part number, or press Enter to finish: "
        ).strip()
        if not raw:
            return PartChoice(count, start, texts)
        if not raw.isdecimal() or not start <= int(raw) < start + count:
            say(f"Type a part number from {start} to {start + count - 1}.")
            continue
        n = int(raw)
        for side, key in TEXT_SIDES.items():
            now = texts.get(n, {}).get(key) or str(shown.get(n, {}).get(key) or "")
            new = ask(f'Part {n} {side} (Enter keeps "{now}"): ').strip()
            if new:
                texts.setdefault(n, {})[key] = new


def confirm_parts(
    picks: Sequence[Pick],
    choice: PartChoice,
    *,
    title: str | None = None,
    yes: bool = False,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> PartChoice:
    """Plan, print and ask until the human says go ahead; returns the plan the cut must make.

    ``yes`` (``--yes``) takes the printed plan without asking. The returned choice pins the
    printed count and first number, so the cut makes exactly what was shown.

    Raises
    ------
    CutStop
        When nobody said go ahead, a text won't fit under ``yes``, or the server cannot plan.
    """

    last: Mapping[str, Any] | None = None
    while True:
        try:
            plan = plan_parts(picks, choice, title=title, api=api, transport=transport)
        except PartTextTooLong as too_long:
            if yes:
                raise
            say(too_long.message)
            if last is None:
                last = plan_parts(
                    picks,
                    PartChoice(choice.part_count, choice.start_number),
                    title=title,
                    api=api,
                    transport=transport,
                )
            choice = edit_choice(last, choice, ask=ask, say=say)
            continue
        last = plan
        for line in plan_lines(plan, choice):
            say(line)
        confirmed = PartChoice(
            int(plan["part_count"]), int(plan["start_number"]), choice.texts
        )
        if yes:
            return confirmed
        answer = ask(GO_AHEAD).strip().lower()
        if answer in ("y", "yes"):
            return confirmed
        if answer not in ("e", "edit"):
            raise CutStop(NOT_CONFIRMED)
        choice = edit_choice(plan, confirmed, ask=ask, say=say)


def run_cut(
    picks: Sequence[Pick],
    *,
    title: str | None = None,
    parts: PartChoice | None = None,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    progress: Callable[[str], None] = lambda line: print(
        line, file=sys.stderr, flush=True
    ),
    new_music: bool = False,
) -> list[str]:
    """Ask the server for one cut from the picked episodes and return the lines to print.

    ``parts`` is the plan the human confirmed; the server makes exactly those parts.
    ``new_music`` passes the human's yes to the question ``--music-price`` showed.

    Raises
    ------
    CutStop
        With the plain NOT DONE line when the server did not give the cut.
    """

    _check_picks(picks)
    client, base, headers = _client(transport, api)
    with client:
        body: dict[str, Any] = {"episodes": _entries(picks), "title": title}
        if parts is not None:
            body.update(parts.body())
        if new_music:
            body["new_music"] = True
        made = _check(
            _post(client, base + CUTS_ROUTE, headers, body), what="start the cut"
        )
        cut_id = str(made.get("series_cut_id") or "")
        if not cut_id:
            raise CutStop(
                "NOT DONE: the server did not name the cut. Tell the core team; nothing was posted."
            )
        progress(NEWEST_VERSION_NOTICE)
        url = base + CUT_ROUTE.format(series_cut_id=cut_id)
        deadline, said = clock() + wait_seconds, clock()
        while True:
            body = _poll(
                client, url, headers, deadline=deadline, clock=clock, sleep=sleep
            )
            status = str(body.get("status") or "")
            if status == "ready":
                return _ready_lines(body, len(picks))
            if status == "failed":
                reason = str(body.get("reason") or "no reason given")
                raise CutStop(
                    f"NOT DONE: the server could not make the cut ({reason}). Nothing was posted."
                )
            if clock() >= deadline:
                raise CutStop(
                    f"NOT DONE: the cut is still being made after {_duration(wait_seconds)}. Run the same command "
                    "again later; the server keeps the same cut and nothing is made twice."
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


def music_price(
    picks: Sequence[Pick],
    *,
    title: str | None = None,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """The server's question for new music on this edit; nothing is made or charged."""

    _check_picks(picks)
    client, base, headers = _client(transport, api)
    with client:
        response = _post(
            client,
            base + MUSIC_OFFER_ROUTE,
            headers,
            {"episodes": _entries(picks), "title": title},
        )
        if response.status_code in (404, 405) and _route_missing(_body(response)):
            raise CutStop(NO_MUSIC_ROUTE)
        offer = _check(response, what="price new music")
    return music_offer_lines(offer)


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
) -> Any:
    """One status answer, asking again after a busy answer or dropped connection until ``deadline``."""

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
                return _check(response, what="give the cut")
        sleep(POLL_RETRY_WAITS[min(attempt, len(POLL_RETRY_WAITS) - 1)])
        attempt += 1


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

    The server uses each episode as it holds it and its own suggested parts; nothing is asked. An
    older server without the picked-cut route stops with the plain NOT DONE line; nothing is made.
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


def _ready_lines(body: Mapping[str, Any], picked: int) -> list[str]:
    cut = body.get("series_cut")
    if not isinstance(cut, dict):
        raise CutStop(
            "NOT DONE: the server said the cut is ready but sent no files. Tell the core team."
        )
    return [
        f"The server cut these {picked} picked episodes by itself; nothing was cut on this laptop.",
        *_music_lines(body.get("music")),
        *series_cut_file_lines(cut),
    ]


def _music_lines(music: Any) -> list[str]:
    """The edit's new music as the server reports it (nothing when none was asked for)."""

    if not isinstance(music, dict):
        return []
    message = str(music.get("message") or "")
    if not message and music.get("state") == "failed":
        message = "it could not be made; the edit has no added music."
    return [f"New music: {message}"] if message else []


def _asker(out: Any) -> Callable[[str], str]:
    """``input`` with its question on ``out``; no answer (no keyboard) counts as no."""

    def ask(prompt: str) -> str:
        print(prompt, end="", file=out, flush=True)
        try:
            return input()
        except EOFError:
            print(file=out)
            return ""

    return ask


def _part_flags_given(args: Any) -> bool:
    return bool(
        args.parts is not None
        or args.start_number is not None
        or args.part_text
        or args.yes
    )


def cut_command(args: Any) -> int:
    """``cut --list | --episodes REF… | --show TITLE``: returns the exit code (0 done, 5 NOT DONE, 2 bad ask)."""

    try:
        if args.file:
            raise CutStop(LOCAL_FILES_GONE, 2)
        if args.list and (
            args.title
            or args.wait is not None
            or args.music_price
            or args.new_music
            or _part_flags_given(args)
        ):
            raise CutStop(
                "Usage: cut --list takes only --show (and --json); drop --title, --wait, --music-price, "
                "--new-music and the part settings. Nothing was made.",
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
        if args.episodes and args.show:
            raise CutStop(
                "Stopped: use --episodes or --show, not both. Nothing was made.", 2
            )
        choice = choice_from_args(args)
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
        if args.music_price:
            if args.new_music:
                raise CutStop(
                    "Stopped: --music-price only shows the price; ask the human, then run with --new-music. "
                    "Nothing was made.",
                    2,
                )
            return _emit(args, True, music_price(picks, title=args.title))
        out = sys.stderr if getattr(args, "json", False) else sys.stdout
        confirmed = confirm_parts(
            picks,
            choice,
            title=args.title,
            yes=args.yes,
            ask=_asker(out),
            say=lambda line: print(line, file=out, flush=True),
        )
        wait = args.wait if args.wait is not None else DEFAULT_WAIT_SECONDS
        lines = run_cut(
            picks,
            title=args.title,
            parts=confirmed,
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
