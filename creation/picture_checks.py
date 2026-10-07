"""Print the server's automatic picture checks where the operator decides.

The server reads each new story's drawn board, cast plate, look frame and
filmed take once against its spec (fictora-drama ``picture_checks``) and keeps
what it found as warnings: a board row the shot list does not have, a spoken
line drawn on the back of the speaker's head, one person drawn twice, a man
drawn for a woman's card, a forbidden element, a take that opens wide on a
close-up board. Until now nothing compared the picture with its spec before a
human looked, and each miss became a paid redraw or a re-film.

This module only prints them, one ``!!`` line each, plus "look before
approving". They are warnings, never a block: no gate refuses on them. They
are read from

- the spine's board and cast-plate assets (``media_assets[].picture_checks``),
- the take facts (``take_facts.picture_checks``),
- the look-frame answer (``picture_checks``).

``None`` (an older server, or a story created before 6 Oct 2026) prints
nothing; an empty list prints that the check found nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from creation.spine_view import episode_id_for

#: The note the server leaves when its check could not run.
UNAVAILABLE = "picture_check_unavailable"
LOOK_BEFORE_APPROVING = (
    "   look before approving: the picture check is a warning, never a block"
)
_SET_SUFFIX = re.compile(r"_set(\d+)$")


def _findings(raw: Any) -> list[Mapping[str, Any]] | None:
    if not isinstance(raw, list):
        return None
    return [item for item in raw if isinstance(item, Mapping)]


def picture_check_lines(raw: Any, *, what: str) -> list[str]:
    """One ``!! picture check:`` line per finding, then "look before approving".

    Parameters
    ----------
    raw
        The ``picture_checks`` list the server sent (or ``None``).
    what
        What was checked, for the line ("t1 board", "Yeon's plate", "t2").

    Returns
    -------
    list[str]
        ``!! picture check: <what>: <message>`` per finding and the reminder;
        one quiet line when the check found nothing; empty when no check ran.
    """

    findings = _findings(raw)
    if findings is None:
        return []
    if not findings:
        return [f"picture check: {what}: nothing found"]
    lines = [
        f"!! picture check: {what}: {str(item.get('message') or item.get('kind') or 'a warning').strip()}"
        for item in findings
    ]
    return [*lines, LOOK_BEFORE_APPROVING]


def board_picture_checks(
    spine: Mapping[str, Any], *, episode: int, set_index: int
) -> Any:
    """The picture-check findings on one current board of an episode, or ``None``.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    set_index
        The board (take) number.

    Returns
    -------
    Any
        The asset's ``picture_checks`` list, or ``None`` when the board has none.
    """

    wanted = episode_id_for(spine, episode)
    for asset in spine.get("media_assets") or []:
        if not isinstance(asset, Mapping) or asset.get("stale"):
            continue
        if (
            asset.get("relation_type") != "episode"
            or asset.get("relation_id") != wanted
        ):
            continue
        match = _SET_SUFFIX.search(str(asset.get("asset_id") or ""))
        if match and int(match.group(1)) == set_index:
            return asset.get("picture_checks")
    return None


def board_picture_check_lines(
    spine: Mapping[str, Any], *, episode: int, sets: Iterable[int]
) -> list[str]:
    """The picture-check lines for the boards just drawn, after the shot list.

    Parameters
    ----------
    spine
        Spine JSON (after the draw).
    episode
        Episode ordinal.
    sets
        The boards drawn.

    Returns
    -------
    list[str]
        One block per board that was checked; the reminder once at the end.
    """

    lines: list[str] = []
    for set_index in sets:
        found = picture_check_lines(
            board_picture_checks(spine, episode=episode, set_index=set_index),
            what=f"t{set_index} board",
        )
        lines += [line for line in found if line != LOOK_BEFORE_APPROVING]
    return _with_reminder(lines)


def plate_picture_check_lines(
    spine: Mapping[str, Any], *, only: Iterable[str] | None = None
) -> list[str]:
    """The picture-check lines for the current cast plates, at the plates gate.

    Parameters
    ----------
    spine
        Spine JSON (after the draw).
    only
        Cast ids to report (the plates just drawn); ``None`` reports every plate.

    Returns
    -------
    list[str]
        One block per checked plate; the reminder once at the end.
    """

    names = {
        str(card.get("cast_id")): str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    }
    wanted = set(only) if only is not None else None
    lines: list[str] = []
    seen: set[str] = set()
    for asset in spine.get("media_assets") or []:
        if not isinstance(asset, Mapping) or asset.get("stale"):
            continue
        cast_id = str(asset.get("relation_id") or "")
        if asset.get("relation_type") != "cast_card" or cast_id in seen:
            continue
        if wanted is not None and cast_id not in wanted:
            continue
        seen.add(cast_id)
        found = picture_check_lines(
            asset.get("picture_checks"),
            what=f"{names.get(cast_id, cast_id)}'s plate",
        )
        lines += [line for line in found if line != LOOK_BEFORE_APPROVING]
    return _with_reminder(lines)


def take_picture_checks(facts: Mapping[str, Any] | None) -> Any:
    """The picture-check findings in saved take facts, or ``None``.

    Parameters
    ----------
    facts
        Saved take facts (the route's body or its ``take_facts``).

    Returns
    -------
    Any
        ``take_facts.picture_checks``, or ``None`` when absent.
    """

    if not facts:
        return None
    body = facts.get("take_facts", facts)
    return body.get("picture_checks") if isinstance(body, Mapping) else None


def take_picture_check_lines(
    facts: Mapping[str, Any] | None, *, take_id: str
) -> list[str]:
    """The picture-check lines for one filmed take, after ``film``/``step`` collects it.

    Parameters
    ----------
    facts
        The take's facts.
    take_id
        ``t1``, ``t2`` ...

    Returns
    -------
    list[str]
        The lines; empty when no check ran.
    """

    return picture_check_lines(take_picture_checks(facts), what=take_id)


def needs_a_look(lines: Sequence[str]) -> bool:
    """Whether any picture-check line is a warning (``!!``).

    Parameters
    ----------
    lines
        Lines from this module.

    Returns
    -------
    bool
        True when at least one finding was printed.
    """

    return any(line.startswith("!! picture check") for line in lines)


def _with_reminder(lines: list[str]) -> list[str]:
    return [*lines, LOOK_BEFORE_APPROVING] if needs_a_look(lines) else lines


__all__ = [
    "LOOK_BEFORE_APPROVING",
    "UNAVAILABLE",
    "board_picture_check_lines",
    "board_picture_checks",
    "needs_a_look",
    "picture_check_lines",
    "plate_picture_check_lines",
    "take_picture_check_lines",
    "take_picture_checks",
]
