"""Where Continue opens the story, as the server names it for creators (fictora-drama #1773; 18 Nov 2026).

``GET /v1/spines/{id}/director/next-offers`` carries ``continue``: a run to wait
on, a button to tap, an open ask to answer, or done. ``fictora-ops status``
prints it next to the episode's stage so operators see the same next step as
creators. A server from before #1773, or one that could not read the story's
runs, leaves ``continue`` out, and status prints as it always did.

``continue`` does not name characters; a portrait redraw takes the names from
the spine's stale cast pictures.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

NEXT_OFFERS_ROUTE = "/v1/spines/{spine_id}/director/next-offers"
SPINE_ROUTE = "/v1/spines/{spine_id}"
TIMEOUT_SECONDS = 30.0

_BUTTONS = {
    "pick_look": "Pick the look",
    "draw_portraits": "Draw portraits",
    "redraw_portraits": "Redraw portraits",
    "approve_cast": "Approve the cast",
    "write_episode": "Write episode {n}",
    "approve_script": "Approve the script",
    "draw_boards": "Draw boards",
    "redraw_boards": "Redraw boards",
    "approve_boards": "Approve boards",
    "film": "Film episode {n}",
}


@dataclass(frozen=True)
class StoryContinue:
    """The server's Continue target, said for the operator.

    Attributes
    ----------
    episode_ordinal
        The episode it is about.
    line
        What to do, e.g. ``Redraw Mina (paid)``.
    """

    episode_ordinal: int
    line: str


def stale_portrait_names(spine: Mapping[str, Any]) -> list[str]:
    """Names of the characters whose drawn portrait is out of date, in cast order.

    Parameters
    ----------
    spine
        Spine JSON.

    Returns
    -------
    list[str]
        One name per stale portrait; the cast id when the card has no name.
    """

    stale = {
        str(asset.get("relation_id"))
        for asset in spine.get("media_assets") or []
        if isinstance(asset, Mapping)
        and asset.get("relation_type") == "cast_card"
        and asset.get("kind") == "still"
        and asset.get("url")
        and asset.get("stale") is True
    }
    return [
        str(card.get("name") or card["cast_id"])
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and str(card.get("cast_id")) in stale
    ]


def continue_line(target: Mapping[str, Any], *, stale_names: list[str]) -> str | None:
    """Say a ``continue`` target in one line, or ``None`` when it is not one the kit knows.

    Parameters
    ----------
    target
        The ``continue`` object from next-offers.
    stale_names
        Characters with an out of date portrait (named by a portrait redraw).

    Returns
    -------
    str or None
        e.g. ``Redraw Mina (paid)``, ``wait for the drawing to finish``.
    """

    kind = target.get("kind")
    if kind == "done":
        return "done, every episode is filmed"
    if kind == "wait":
        return f"wait for the {target.get('family') or 'run'} to finish"
    button = target.get("button")
    if kind not in ("do", "answer") or button not in _BUTTONS:
        return None
    action = _BUTTONS[button].format(n=target.get("episode_ordinal"))
    if button == "redraw_portraits" and stale_names:
        action = "Redraw " + ", ".join(stale_names)
    cost = "paid" if target.get("uses_credits") is True else "free"
    if kind == "answer" and target.get("interrupt_id"):
        return f"{action} ({cost}; answers ask {target['interrupt_id']})"
    return f"{action} ({cost})"


def read_continue(
    desk: Path,
    *,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = TIMEOUT_SECONDS,
) -> StoryContinue | None:
    """Read the desk's story's Continue target from the server. Reads only.

    Parameters
    ----------
    desk
        Series desk.
    api
        ``(base_url, headers)``; default the kit's credentials on the desk's session.
    transport
        HTTP transport (tests fake the server with it).
    timeout
        Seconds each read may take.

    Returns
    -------
    StoryContinue or None
        ``None`` when the desk has no story, the kit cannot reach the server, the
        server refused, or it sent no ``continue`` (before drama #1773).
    """

    from creation.post.desk import spine_body
    from creation.post.desk import spine_id as desk_spine_id
    from creation.post.final_approval import NotChecked, kit_api

    try:
        spine_id = desk_spine_id(desk)
        base, headers = api if api is not None else kit_api(desk, env_token_only=False)
    except (ValueError, OSError, NotChecked):
        return None
    root = base.rstrip("/")
    try:
        with httpx.Client(
            transport=transport, timeout=timeout, headers=dict(headers)
        ) as client:
            got = client.get(root + NEXT_OFFERS_ROUTE.format(spine_id=spine_id))
            body = got.json() if got.is_success else None
            target = body.get("continue") if isinstance(body, Mapping) else None
            if not isinstance(target, Mapping) or not isinstance(
                target.get("episode_ordinal"), int
            ):
                return None
            names: list[str] = []
            if target.get("button") == "redraw_portraits":
                spine = client.get(root + SPINE_ROUTE.format(spine_id=spine_id))
                if spine.is_success:
                    names = stale_portrait_names(spine_body(spine.json()))
    except (httpx.HTTPError, ValueError):
        return None
    line = continue_line(target, stale_names=names)
    return StoryContinue(target["episode_ordinal"], line) if line else None
