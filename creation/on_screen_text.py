"""Words the kit burns on the picture are drawn by the server (decision D7, 21 Oct 2026).

The kit used to keep its own copy of fictora-drama's ``caption_dashes`` (no em
or en dash on a burned caption, title or hook line). A copy drifts, and the
server-heavy rule says the server owns the rules, so every caption cue and
title the kit draws now goes through
``POST /v1/spines/{spine_id}/episodes/{episode_id}/on-screen-text`` and the kit
burns exactly what comes back.

* The command's desk and episode are the ones its rules run under
  (:func:`creation.rules_epoch.desk_rules`, set for every ``fictora-produce``
  command), so the deep caption code asks without being handed a session.
* Texts go in batches (one request per caption build, not per cue), and each
  answer is remembered for the rest of the command.
* No local fallback: a server without the route (an older deploy) or one that
  cannot be reached stops the command with :class:`OnScreenTextUnavailable`,
  which says what to do. Tests install a drawer with :func:`drawer_override`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

import httpx

TextKind = Literal["caption", "title"]
#: The server's route (fictora-drama, 21 Oct 2026).
ON_SCREEN_TEXT_ROUTE = "/v1/spines/{spine_id}/episodes/{episode_id}/on-screen-text"
#: Most texts one request may carry (the server's ``ON_SCREEN_TEXT_MAX_ITEMS``).
MAX_ITEMS = 500
#: What an older server says, in plain words.
OLDER_SERVER = (
    "this Drama API cannot draw on-screen words yet (an older deploy, before the on-screen text route). "
    "The kit no longer cleans captions and titles itself: update the server, then run this again"
)

Drawer = Callable[[Sequence[str], TextKind], list[str]]

_OVERRIDE: ContextVar[Drawer | None] = ContextVar(
    "fictora_on_screen_text_drawer", default=None
)
_CACHE: dict[tuple[str, str, str, str], str] = {}


class OnScreenTextUnavailable(RuntimeError):
    """The server could not draw the words; nothing is burned with the kit's own guess."""


@contextmanager
def drawer_override(drawer: Drawer) -> Iterator[None]:
    """Draw texts with ``drawer`` inside the block (tests; never a production fallback).

    Parameters
    ----------
    drawer
        ``(texts, kind) -> drawn texts``, one per text, in order.

    Yields
    ------
    None
    """

    token = _OVERRIDE.set(drawer)
    try:
        yield
    finally:
        _OVERRIDE.reset(token)


def _active_desk_episode() -> tuple[Path, int]:
    from creation.rules_epoch import _DESK, _EPISODE

    desk = _DESK.get()
    if desk is None:
        raise OnScreenTextUnavailable(
            "on-screen words are drawn by the server for a desk's episode; this ran with no desk"
        )
    return desk.expanduser().resolve(), int(_EPISODE.get() or 1)


def _episode_id(desk: Path, episode: int, run: Any) -> tuple[str, str]:
    from creation.post.desk import saved_spine, spine_body, spine_id
    from creation.spine_view import episode_id_for

    sid = spine_id(desk)
    found = saved_spine(desk, episode)
    body: Mapping[str, Any] = (
        found[0] if found is not None else spine_body(run.spine(sid))
    )
    return sid, episode_id_for(body, episode)


def ask_server(
    run: Any, spine_id: str, episode_id: str, texts: Sequence[str], kind: TextKind
) -> list[str]:
    """Send texts to the server's on-screen text route and return them as drawn.

    Parameters
    ----------
    run
        Open API session (``post_optional``).
    spine_id, episode_id
        The episode the words are burned on.
    texts
        Non-empty texts.
    kind
        ``caption`` or ``title``.

    Returns
    -------
    list[str]
        One drawn text per text, in order.

    Raises
    ------
    OnScreenTextUnavailable
        An older server (no route) or any refusal.
    """

    from creation.harness.http_util import api_error_text

    path = ON_SCREEN_TEXT_ROUTE.format(spine_id=spine_id, episode_id=episode_id)
    drawn: list[str] = []
    for start in range(0, len(texts), MAX_ITEMS):
        chunk = texts[start : start + MAX_ITEMS]
        status, answer = run.post_optional(
            path, {"items": [{"kind": kind, "text": text} for text in chunk]}
        )
        if status in (404, 405) and not (
            isinstance(answer, Mapping)
            and isinstance(answer.get("error"), Mapping)
            and answer["error"].get("code") in ("spine_not_found", "episode_not_found")
        ):
            raise OnScreenTextUnavailable(OLDER_SERVER)
        if not 200 <= status < 300 or not isinstance(answer, Mapping):
            raise OnScreenTextUnavailable(
                f"the server did not draw the on-screen words (HTTP {status}): {api_error_text(answer)}"
            )
        items = answer.get("items") or []
        if len(items) != len(chunk):
            raise OnScreenTextUnavailable(
                "the server answered a different number of on-screen words than were sent"
            )
        drawn.extend(str(item.get("text") or "") for item in items)
    return drawn


def drawn_texts(texts: Sequence[str], *, kind: TextKind = "caption") -> list[str]:
    """Return each text as the server says the picture draws it (one request for the batch).

    Parameters
    ----------
    texts
        The texts, in order; empty ones come back empty without a call.
    kind
        ``caption`` (a cue; its line breaks are kept) or ``title``.

    Returns
    -------
    list[str]
        One drawn text per text.

    Raises
    ------
    OnScreenTextUnavailable
        No desk, an older server, or the server could not be asked.
    """

    override = _OVERRIDE.get()
    if override is not None:
        return list(override(list(texts), kind))
    wanted = [text for text in dict.fromkeys(texts) if text.strip()]
    if not wanted:
        return list(texts)
    desk, episode = _active_desk_episode()
    from creation.post.desk import open_api

    prefix = (str(desk), str(episode))
    missing = [t for t in wanted if (*prefix, kind, t) not in _CACHE]
    if missing:
        run = open_api(desk, episode)
        try:
            sid, eid = _episode_id(desk, episode, run)
            for text, drawn in zip(
                missing, ask_server(run, sid, eid, missing, kind), strict=True
            ):
                _CACHE[(*prefix, kind, text)] = drawn
        except (OSError, httpx.TransportError) as exc:
            raise OnScreenTextUnavailable(
                f"the server could not be reached to draw the on-screen words ({exc}); run this again"
            ) from exc
        finally:
            run.client.close()
    return [_CACHE[(*prefix, kind, t)] if t.strip() else t for t in texts]


def server_owns_text(run: Any) -> bool:
    """Whether the server draws on-screen words, keeps caption labels and checks voice lines (capabilities).

    Parameters
    ----------
    run
        Open API session (``get_optional``).

    Returns
    -------
    bool
        ``on_screen_text`` from ``GET /v1/capabilities``; false for a server
        that does not say (an older deploy) or cannot be asked.
    """

    status, body = run.get_optional("/v1/capabilities")
    return (
        status == 200
        and isinstance(body, Mapping)
        and body.get("on_screen_text") is True
    )


def drawn_text(text: str, *, kind: TextKind = "caption") -> str:
    """Return one text as the server says the picture draws it.

    Parameters
    ----------
    text
        The text.
    kind
        ``caption`` or ``title``.

    Returns
    -------
    str
        The drawn text.
    """

    return drawn_texts([text], kind=kind)[0]


__all__ = [
    "MAX_ITEMS",
    "OLDER_SERVER",
    "ON_SCREEN_TEXT_ROUTE",
    "OnScreenTextUnavailable",
    "ask_server",
    "server_owns_text",
    "drawer_override",
    "drawn_text",
    "drawn_texts",
]
