"""Boards that show an older picture of a story object (fictora-drama #787).

When an object's picture is redrawn and the new one is approved, the boards
already drawn with the old picture keep it. The server lists them on every
story answer as a top-level ``outdated_boards`` (left out when empty)::

    [{"asset_id", "episode_id", "episode_ordinal", "set_index",
      "outdated_objects": [{"prop_id", "name"}]}]

A board is listed only after the object's new picture is approved. Redrawing
one of them through the usual board redraw binds the new picture and takes it
off the list.

The kit never redraws them by itself (founder, 13 Oct 2026: never block, the
human decides). It says which boards still show the old picture, that each
redraw costs money, and prints the redraw command for each; the operator
relays it and runs a redraw only on the human's yes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from creation.prices import STILL_USD


@dataclass(frozen=True)
class OutdatedBoard:
    """One drawn board that still shows an older picture of one or more objects."""

    episode: int
    set_index: int
    objects: tuple[str, ...]

    @property
    def take_id(self) -> str:
        """The desk's take name for this board (``t1``, ``t2`` ...)."""

        return f"t{self.set_index}"


def outdated_boards(spine: Mapping[str, Any] | None) -> list[OutdatedBoard]:
    """The boards the story lists as showing an older object picture, in episode and board order.

    Parameters
    ----------
    spine
        Spine JSON (``GET /v1/spines/{id}``), or ``None``.

    Returns
    -------
    list[OutdatedBoard]
        Empty when the story lists none (or the server is older than #787).
    """

    rows: list[OutdatedBoard] = []
    for entry in (spine or {}).get("outdated_boards") or []:
        if not isinstance(entry, Mapping):
            continue
        try:
            episode = int(entry.get("episode_ordinal") or 0)
            set_index = int(entry.get("set_index") or 0)
        except (TypeError, ValueError):
            continue
        if episode < 1 or set_index < 1:
            continue
        names: list[str] = []
        for obj in entry.get("outdated_objects") or []:
            if isinstance(obj, Mapping):
                name = " ".join(
                    str(obj.get("name") or obj.get("prop_id") or "").split()
                )
                if name and name not in names:
                    names.append(name)
        rows.append(OutdatedBoard(episode, set_index, tuple(names)))
    return sorted(rows, key=lambda row: (row.episode, row.set_index))


def outdated_objects_on_board(
    spine: Mapping[str, Any] | None, *, episode: int, set_index: int
) -> list[str]:
    """The objects whose older picture one board still shows (empty when it is not listed).

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    set_index
        The board (take) number, 1-based.

    Returns
    -------
    list[str]
        Object names; ``["an object"]`` when the board is listed without names.
    """

    for row in outdated_boards(spine):
        if row.episode == episode and row.set_index == set_index:
            return list(row.objects) or ["an object"]
    return []


def saved_spine(desk: Path) -> dict[str, Any] | None:
    """The story as the desk last saved it (``api/spine.json``), or ``None``. Reads only the desk."""

    path = desk / "api" / "spine.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def outdated_boards_notice(
    spine: Mapping[str, Any] | None, *, desk: Path | str = "D"
) -> str | None:
    """What to tell the human about boards that show an older object picture, or ``None`` when none do.

    One line naming each board and its object(s), the price of a redraw and that
    the choice is the human's, then the existing redraw command per board. It
    never redraws and never blocks.

    Parameters
    ----------
    spine
        Spine JSON.
    desk
        Series desk, for the commands to paste.

    Returns
    -------
    str | None
        The notice, or ``None``.
    """

    rows = outdated_boards(spine)
    if not rows:
        return None
    named = ", ".join(
        f"ep{row.episode:02d} board {row.set_index}"
        + (f" ({', '.join(row.objects)})" if row.objects else "")
        for row in rows
    )
    lines = [
        f"Boards showing an older picture of an object: {named}. They keep the old look unless "
        f"redrawn; each board redraw is ${float(STILL_USD):.2f}. Human's call.",
        "  Only after the human's yes, per board:",
    ]
    lines += [
        f"    fictora-produce redraw-board --desk {desk} --episode {row.episode} --take {row.take_id}"
        for row in rows
    ]
    return "\n".join(lines)


__all__ = [
    "OutdatedBoard",
    "outdated_boards",
    "outdated_boards_notice",
    "outdated_objects_on_board",
    "saved_spine",
]
