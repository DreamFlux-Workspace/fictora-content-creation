"""Board prices and board spend come from the server, never the kit's own table (server-heavy rule).

Low Tide (16 Oct 2026): a shot-clips episode draws ONE grid that stands for
both takes, but the kit priced and booked $0.30 per take board file: one grid
showed and booked as $0.60.

* The price shown before a board draw is the server's estimate: its
  ``cost_estimate.boards`` times ``still_usd_each`` (:func:`server_board_price`).
* What a board draw books is what the server says the run paid for:
  ``picture_spend`` on the run's terminal job (fictora-drama
  ``DramaPictureSpend``: boards and plates that reached the provider, the
  still price, the settlement) (:func:`server_picture_spend`).

An older server answers neither; the kit then counts the distinct board
pictures it downloaded (a grid's takes share one picture) at its own still
price (:func:`distinct_board_pictures`), and says so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from creation.spine_view import board_assets, episode_id_for


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def server_board_price(
    run: Any, *, spine_id: str, episode: int
) -> tuple[int, float] | None:
    """The boards the server would draw for an episode and one still's price, from its estimate.

    Parameters
    ----------
    run
        API session (``DramaApiRunSession``).
    spine_id
        Story spine.
    episode
        Episode ordinal.

    Returns
    -------
    tuple[int, float] | None
        ``(boards, still_usd_each)``; ``None`` when the server refused or gave no board count.
    """

    spine = run.spine(spine_id)
    try:
        body = {
            "spine_version": spine["spine_version"],
            "episode_ids": [episode_id_for(spine, episode)],
        }
    except (KeyError, SystemExit, ValueError):
        return None
    status, answer = run.post_optional(f"/v1/spines/{spine_id}/batches/estimate", body)
    if not 200 <= status < 300 or not isinstance(answer, Mapping):
        return None
    cost = answer.get("cost_estimate")
    if not isinstance(cost, Mapping):
        return None
    boards, each = cost.get("boards"), _number(cost.get("still_usd_each"))
    if (
        not isinstance(boards, int)
        or isinstance(boards, bool)
        or boards < 0
        or each is None
    ):
        return None
    return boards, each


def server_picture_spend(terminal: Mapping[str, Any] | None) -> dict[str, float] | None:
    """What a board run paid for, from its terminal job (``result.picture_spend``).

    Parameters
    ----------
    terminal
        The run's terminal job JSON.

    Returns
    -------
    dict[str, float] | None
        ``boards``, ``plates``, ``still_usd_each`` and ``settled_usd``; ``None``
        from a server that does not report it.
    """

    result = (terminal or {}).get("result")
    spend = result.get("picture_spend") if isinstance(result, Mapping) else None
    if not isinstance(spend, Mapping):
        return None
    numbers = {
        key: _number(spend.get(key))
        for key in ("boards", "plates", "still_usd_each", "settled_usd")
    }
    if any(value is None for value in numbers.values()):
        return None
    return {key: float(value) for key, value in numbers.items() if value is not None}


def distinct_board_pictures(
    spine: Mapping[str, Any], *, episode: int, sets: Sequence[int]
) -> int:
    """The fallback count for an older server: distinct board pictures among the takes drawn.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    sets
        Takes whose boards were drawn.

    Returns
    -------
    int
        At least one per distinct picture; a shot-clips grid shared by its takes counts once.
    """

    urls = {
        url for index, url in board_assets(spine, episode=episode) if index in set(sets)
    }
    return len(urls) if urls else len(set(sets))


__all__ = ["distinct_board_pictures", "server_board_price", "server_picture_spend"]
