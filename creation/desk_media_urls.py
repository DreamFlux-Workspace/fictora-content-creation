"""Resolve cast plate and board URLs from spine GET or enrol terminal artefacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _latest_terminal_path(api_dir: Path, glob_pattern: str) -> Path | None:
    """Return the newest matching terminal JSON under ``api_dir``."""

    matches = sorted(api_dir.glob(glob_pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _terminal_output(api_dir: Path, glob_pattern: str) -> dict[str, Any] | None:
    path = _latest_terminal_path(api_dir, glob_pattern)
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    output = payload.get("output")
    return output if isinstance(output, dict) else None


def cast_plate_urls(spine: dict[str, Any], api_dir: Path) -> list[str]:
    """Return ordered cast still URLs for plate download.

    Parameters
    ----------
    spine
        Latest ``GET /v1/spines/{id}`` body after cast enrol.
    api_dir
        Episode ``api/`` folder with ``06_*cast_terminal*.json``.

    Returns
    -------
    list[str]
        One URL per cast member, spine-first then terminal fallback.
    """

    urls: list[str] = []
    cast_rows = [row for row in (spine.get("cast") or []) if isinstance(row, dict) and row.get("cast_id")]
    assets = [row for row in (spine.get("media_assets") or []) if isinstance(row, dict)]
    by_cast: dict[str, str] = {}
    for asset in assets:
        if asset.get("relation_type") != "cast_card" or asset.get("stale"):
            continue
        url = asset.get("url")
        relation_id = asset.get("relation_id")
        if url and isinstance(relation_id, str) and relation_id not in by_cast:
            by_cast[relation_id] = str(url)
    for row in cast_rows:
        cast_id = str(row["cast_id"])
        if cast_id in by_cast:
            urls.append(by_cast[cast_id])
            continue
        for key in ("portrait_url", "full_body_url", "reference_url", "image_url", "url"):
            value = row.get(key)
            if value:
                urls.append(str(value))
                break
    if urls:
        return urls
    output = _terminal_output(api_dir, "06_*cast_terminal*.json")
    if not output:
        return []
    for row in output.get("characters") or []:
        if isinstance(row, dict) and row.get("image_url"):
            urls.append(str(row["image_url"]))
    return urls


def board_urls_for_episode(spine: dict[str, Any], api_dir: Path, *, ordinal: int) -> list[str]:
    """Return storyboard still URLs for one episode ordinal.

    Parameters
    ----------
    spine
        Latest spine body after boards enrol.
    api_dir
        Episode ``api/`` with ``09_*boards_terminal*.json``.
    ordinal
        1-based episode ordinal.

    Returns
    -------
    list[str]
        Board image URLs for the episode.
    """

    urls: list[str] = []
    episode_id = None
    for summary in spine.get("episode_summaries") or []:
        if not isinstance(summary, dict):
            continue
        if int(summary.get("episode_ordinal") or summary.get("ordinal") or 1) == ordinal:
            episode_id = str(summary.get("episode_id") or "")
            break
    for summary in spine.get("episode_summaries") or []:
        if not isinstance(summary, dict):
            continue
        if int(summary.get("episode_ordinal") or summary.get("ordinal") or 1) != ordinal:
            continue
        for frame in summary.get("frames") or []:
            if isinstance(frame, dict) and frame.get("image_url"):
                urls.append(str(frame["image_url"]))
    for board in spine.get("boards") or []:
        if not isinstance(board, dict):
            continue
        if episode_id and board.get("episode_id") == episode_id and board.get("image_url"):
            urls.append(str(board["image_url"]))
    if urls:
        return urls
    output = _terminal_output(api_dir, "09_*boards_terminal*.json")
    if not output:
        return []
    target = episode_id or f"episode_{ordinal:02d}"
    for board in output.get("boards") or []:
        if isinstance(board, dict) and board.get("episode_id") == target and board.get("image_url"):
            urls.append(str(board["image_url"]))
    return urls
