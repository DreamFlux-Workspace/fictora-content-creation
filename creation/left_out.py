"""What the server left out of an episode (``spine.left_out``), said in the server's own words."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

KIT_CANNOT_ADD_BACK = "The kit can't add these back yet."


def left_out_lines(spine: Mapping[str, Any], episode_id: str) -> list[str]:
    """The server's sentence for each thing it left out of one episode, then that the kit can't add it back.

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` JSON.
    episode_id
        The episode's API id.

    Returns
    -------
    list[str]
        Each record's ``line`` as sent, then :data:`KIT_CANNOT_ADD_BACK`; empty when nothing was left out
        or on an older server.
    """

    lines: list[str] = []
    for record in spine.get("left_out") or []:
        if not isinstance(record, Mapping) or record.get("episode_id") != episode_id:
            continue
        line = record.get("line")
        if isinstance(line, str) and line.strip():
            lines.append(line)
    return [*lines, KIT_CANNOT_ADD_BACK] if lines else []
