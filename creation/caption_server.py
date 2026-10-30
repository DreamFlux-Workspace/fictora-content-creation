"""The server lays out the captions the kit burns, and checks them (24 Oct 2026, fictora-drama #1029).

Server-heavy rule (15 Oct 2026): where a caption sits on the frame is a
server rule. The server's one safe-area definition gives the side margins
(clear of Instagram and TikTok's right-hand like, comment and share rail),
the 55-70% caption band, Bold's bottom at 70% and the wrap-before-shrink
fit. The kit used to keep its own copy (Bold at 76%, 56-60 px margins), which
drifted when #1023 changed the server's.

* :func:`server_caption_ass`: ``finish`` / ``caption`` still time the words on
  the laptop (speech spans, transcripts), then send the timed chunks to
  ``POST /v1/captions/layout`` and burn the caption file that comes back,
  exactly as given.
* :func:`server_safe_area_check`: the take check sends the burned caption
  file to ``POST /v1/captions/safe-area-check`` and prints what the server
  finds (zone, cue, time). Captions are no longer looked for by yellow pixels.

An older server without these routes (404/405), or one that cannot be
reached, is never an error: the caller falls back to the kit's own layout or
reader and prints a ``NOTE``. Tests install answers with :func:`server_override`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from creation.captions import Cue

#: The server's routes (fictora-drama #1029).
CAPTION_LAYOUT_ROUTE = "/v1/captions/layout"
SAFE_AREA_CHECK_ROUTE = "/v1/captions/safe-area-check"
#: The show's caption look (fictora-drama #1191 adds its ``caption_size``, x0.5-x2).
CAPTION_LOOK_ROUTE = "/v1/spines/{spine_id}/caption-look"
#: Most chunks one layout request carries (the server's ``CAPTION_LAYOUT_MAX_CHUNKS``).
MAX_CHUNKS = 2000
#: Most words one chunk carries (the server's ``CAPTION_LAYOUT_MAX_WORDS``).
MAX_WORDS = 60
#: Two cues this close (seconds) are one build-up.
_JOIN_SECONDS = 0.02

#: ``(path, body) -> (status, answer)``: how the server is asked (tests replace it).
Poster = Callable[[str, dict[str, Any]], tuple[int, Any]]

_OVERRIDE: ContextVar[Poster | None] = ContextVar(
    "fictora_caption_server_poster", default=None
)


#: ``() -> (status, answer)``: how the show's caption look is read (tests replace it).
LookGetter = Callable[[], tuple[int, Any]]

_LOOK_OVERRIDE: ContextVar[LookGetter | None] = ContextVar(
    "fictora_caption_look_getter", default=None
)


@contextmanager
def caption_look_override(getter: LookGetter) -> Iterator[None]:
    """Answer the show's caption-look read with ``getter`` inside the block (tests)."""

    token = _LOOK_OVERRIDE.set(getter)
    try:
        yield
    finally:
        _LOOK_OVERRIDE.reset(token)


@contextmanager
def server_override(poster: Poster) -> Iterator[None]:
    """Answer the caption routes with ``poster`` inside the block (tests; validation runs).

    Parameters
    ----------
    poster
        ``(path, body) -> (status, answer)``.

    Yields
    ------
    None
    """

    token = _OVERRIDE.set(poster)
    try:
        yield
    finally:
        _OVERRIDE.reset(token)


def _ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def _word(text: str, start: int, end: int) -> dict[str, Any]:
    return {"text": text, "start_ms": start, "end_ms": max(end, start + 1)}


def _bold_chunk(group: Sequence[Cue]) -> dict[str, Any]:
    """One Bold chunk: each word from the start of the cue that first shows it."""

    words = group[0].chunk.split()
    starts: list[int] = []
    for cue in group:
        while len(starts) < min(cue.shown, len(words)):
            starts.append(_ms(cue.start))
    while len(starts) < len(words):
        starts.append(_ms(group[-1].start))
    end = _ms(group[-1].end)
    timed = [
        _word(word, starts[i], starts[i + 1] if i + 1 < len(words) else end)
        for i, word in enumerate(words)
    ]
    emphasis = group[0].emphasis
    return {
        "words": timed,
        "end_ms": max(end, starts[-1] + 1),
        "italic": group[0].italic,
        "emphasis_index": emphasis if 0 <= emphasis < len(words) else None,
    }


def _flicker_chunk(group: Sequence[Cue]) -> dict[str, Any]:
    """One build-up chunk: each new word timed on the cue that adds it."""

    timed: list[dict[str, Any]] = []
    seen = 0
    for cue in group:
        tokens = cue.text.split()
        for word in tokens[seen:]:
            timed.append(_word(word, _ms(cue.start), _ms(cue.end)))
        seen = len(tokens)
    end = _ms(group[-1].end)
    return {
        "words": timed,
        "end_ms": max(end, timed[-1]["start_ms"] + 1),
        "italic": group[0].italic,
    }


def _whole_chunk(cue: Cue) -> dict[str, Any]:
    """One whole-line chunk: every word over the cue's span."""

    start, end = _ms(cue.start), _ms(cue.end)
    chunk: dict[str, Any] = {
        "words": [_word(word, start, end) for word in cue.text.split()],
        "end_ms": max(end, start + 1),
        "italic": cue.italic,
    }
    if cue.chunk and 0 <= cue.emphasis < len(chunk["words"]):
        chunk["emphasis_index"] = cue.emphasis
    return chunk


def caption_chunks(cues: Sequence[Cue], *, whole_lines: bool) -> list[dict[str, Any]]:
    """The kit's cues as the server's timed chunks (``POST /v1/captions/layout``).

    Bold cues (``chunk`` and ``shown`` set) are grouped by chunk; house flicker
    cues by build-up (each cue shows the one before plus a word, with no gap);
    with ``whole_lines`` every cue is one chunk shown whole.

    Parameters
    ----------
    cues
        The cues the kit timed, in any order.
    whole_lines
        Each cue is a whole line (``plain``, or English over other speech).

    Returns
    -------
    list[dict[str, Any]]
        Chunks in time order; cues without words are left out.
    """

    ordered = sorted((c for c in cues if c.text.split()), key=lambda c: c.start)
    chunks: list[dict[str, Any]] = []
    group: list[Cue] = []

    def flush() -> None:
        if group:
            chunks.append(
                _bold_chunk(group)
                if group[0].chunk and group[0].shown
                else _flicker_chunk(group)
            )
        group.clear()

    for cue in ordered:
        if whole_lines or len(cue.text.split()) > MAX_WORDS:
            flush()
            chunks.append(_whole_chunk(cue))
            continue
        prev = group[-1] if group else None
        if cue.chunk and cue.shown:
            same = (
                prev is not None
                and prev.chunk == cue.chunk
                and cue.shown > prev.shown
                and prev.italic == cue.italic
            )
        else:
            same = (
                prev is not None
                and not prev.chunk
                and prev.italic == cue.italic
                and abs(cue.start - prev.end) <= _JOIN_SECONDS
                and cue.text.startswith(prev.text + " ")
            )
        if not same:
            flush()
        group.append(cue)
    flush()
    return chunks


def _post(
    desk: Path | None, episode: int, path: str, body: dict[str, Any]
) -> tuple[int, Any]:
    override = _OVERRIDE.get()
    if override is not None:
        return override(path, body)
    if desk is None:
        raise OSError("no desk to reach the Drama API from")
    from creation.post.desk import open_api

    run = open_api(desk, episode)
    try:
        status, answer = run.post_optional(path, body)
        return int(status), answer
    finally:
        run.client.close()


def _read_look(desk: Path | None, episode: int) -> tuple[int, Any]:
    getter = _LOOK_OVERRIDE.get()
    if getter is not None:
        return getter()
    if _OVERRIDE.get() is not None or desk is None:
        # A test answering the layout route only, or no desk: no look to read.
        return 404, None
    from creation.post.desk import open_api, spine_id

    try:
        sid = spine_id(desk)
    except ValueError:
        return 404, None
    run = open_api(desk, episode)
    try:
        status, answer = run.get_optional(
            CAPTION_LOOK_ROUTE.format(spine_id=quote(sid, safe=""))
        )
        return int(status), answer
    finally:
        run.client.close()


def _desk_spine_id(desk: Path | None) -> str | None:
    if desk is None:
        return None
    from creation.post.desk import spine_id

    try:
        return spine_id(desk) or None
    except (OSError, ValueError):
        return None


def show_caption_size(desk: Path | None, episode: int) -> float | None:
    """The show's caption size from the server (``caption_size``, fictora-drama #1191), passed through.

    ``None`` when the server sends none (an older server, size unset, no
    connection): the layout request is then exactly as before.
    """

    try:
        status, answer = _read_look(desk, episode)
    except (OSError, httpx.HTTPError, SystemExit):
        return None
    if not 200 <= status < 300 or not isinstance(answer, Mapping):
        return None
    size = answer.get("caption_size")
    if isinstance(size, bool) or not isinstance(size, (int, float)):
        return None
    return float(size)


def _older(status: int) -> bool:
    return status in (404, 405)


def note(text: str) -> None:
    """Print a fallback note (stderr), never an error."""

    print(f"NOTE captions: {text}", file=sys.stderr)


def server_caption_ass(
    cues: Sequence[Cue],
    *,
    width: int,
    height: int,
    style: str,
    whole_lines: bool,
    desk: Path | None,
    episode: int,
    caption_accent: str = "yellow",
) -> tuple[str | None, list[str]]:
    """Ask the server for the caption file of a portrait take.

    Parameters
    ----------
    cues
        The take's timed cues (dialogue and laid lines).
    width, height
        The take's frame.
    style
        ``bold``, ``house`` or ``plain``.
    whole_lines
        Each cue is a whole line (``plain``, or a show not spoken in English).
    desk, episode
        The desk whose API session asks.
    caption_accent
        The caption colour (``yellow`` default).

    Returns
    -------
    tuple[str | None, list[str]]
        The caption file text, or ``None`` to fall back to the kit's own layout
        (an older server, no connection, a refusal; a ``NOTE`` is printed);
        and the server's safe-area finding messages.
    """

    chunks = caption_chunks(cues, whole_lines=whole_lines)
    if not chunks:
        return None, []
    if len(chunks) > MAX_CHUNKS:
        note(
            f"{len(chunks)} caption chunks is more than the server lays out at once; the kit laid them out"
        )
        return None, []
    body = {
        "width": width,
        "height": height,
        "style": style,
        "reveal": "line" if whole_lines else "word",
        "caption_accent": caption_accent,
        "chunks": chunks,
    }
    size = show_caption_size(desk, episode)
    if size is not None:
        body["size"] = size
    # Name the show (fictora-drama #1191): the server then lays the captions out in the
    # show's chat-set style and size, and the file it returns is burned as given.
    show = _desk_spine_id(desk)
    try:
        if show is not None:
            status, answer = _post(
                desk, episode, CAPTION_LAYOUT_ROUTE, {**body, "spine_id": show}
            )
            if _older(status):
                # An unknown show or an older server: ask exactly as before.
                status, answer = _post(desk, episode, CAPTION_LAYOUT_ROUTE, body)
        else:
            status, answer = _post(desk, episode, CAPTION_LAYOUT_ROUTE, body)
    except (OSError, httpx.HTTPError, SystemExit) as exc:
        note(
            f"the server could not be reached for the caption layout ({exc}); the kit laid them out"
        )
        return None, []
    if _older(status):
        note(
            "this Drama API has no caption layout route yet (an older deploy, before fictora-drama #1029); "
            "the kit laid the captions out itself, with its older placement"
        )
        return None, []
    if (
        not 200 <= status < 300
        or not isinstance(answer, Mapping)
        or not answer.get("ass")
    ):
        note(
            f"the server did not lay out the captions (HTTP {status}); the kit laid them out"
        )
        return None, []
    findings = [
        str(f.get("message") or "")
        for f in answer.get("findings") or []
        if isinstance(f, Mapping)
    ]
    return str(answer["ass"]), findings


def server_safe_area_check(
    ass_text: str, *, letterbox: bool, desk: Path | None, episode: int = 1
) -> dict[str, Any] | None:
    """Send a burned caption file to the server's safe-area check.

    Parameters
    ----------
    ass_text
        The caption file's text.
    letterbox
        A letterbox show's file (only the covered zones count).
    desk, episode
        The desk whose API session asks.

    Returns
    -------
    dict[str, Any] | None
        The server's answer (``cues``, ``findings``, ``clear``), or ``None``
        to fall back to the kit's own reader (a ``NOTE`` is printed).
    """

    try:
        status, answer = _post(
            desk,
            episode,
            SAFE_AREA_CHECK_ROUTE,
            {"ass": ass_text, "letterbox": letterbox},
        )
    except (OSError, httpx.HTTPError, SystemExit) as exc:
        note(
            f"the server could not be reached for the caption check ({exc}); the kit read the caption file itself"
        )
        return None
    if _older(status):
        note(
            "this Drama API has no caption safe-area check yet (an older deploy, before fictora-drama #1029); "
            "the kit read the caption file itself"
        )
        return None
    if not 200 <= status < 300 or not isinstance(answer, Mapping):
        note(
            f"the server did not check the caption file (HTTP {status}); the kit read it itself"
        )
        return None
    return dict(answer)


__all__ = [
    "CAPTION_LAYOUT_ROUTE",
    "MAX_CHUNKS",
    "SAFE_AREA_CHECK_ROUTE",
    "CAPTION_LOOK_ROUTE",
    "caption_chunks",
    "caption_look_override",
    "note",
    "server_caption_ass",
    "server_override",
    "server_safe_area_check",
    "show_caption_size",
]
