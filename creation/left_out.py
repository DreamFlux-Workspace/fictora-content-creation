"""What the server left out of an episode (``spine.left_out``), said in the server's own words."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

KIT_CANNOT_ADD_BACK = "The kit can't add these back yet."


def _episode_lines(records: Any, episode_id: str) -> list[str]:
    lines: list[str] = []
    for record in records or []:
        if not isinstance(record, Mapping) or record.get("episode_id") != episode_id:
            continue
        line = record.get("line")
        if isinstance(line, str) and line.strip():
            lines.append(line)
    return lines


def left_out_lines(spine: Mapping[str, Any], episode_id: str) -> list[str]:
    """The server's sentence for each thing it left out of one episode, said as sent.

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` JSON.
    episode_id
        The episode's API id.

    Returns
    -------
    list[str]
        Each ``left_out_notes`` record's ``line`` (told once, never offered back), then each
        ``left_out`` record's ``line`` followed by :data:`KIT_CANNOT_ADD_BACK`, since those offer it.
        A record without a ``line`` (an older server) is not printed.
    """

    told = _episode_lines(spine.get("left_out_notes"), episode_id)
    offered = _episode_lines(spine.get("left_out"), episode_id)
    return [*told, *offered, KIT_CANNOT_ADD_BACK] if offered else told
