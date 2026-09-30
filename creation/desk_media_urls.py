"""Resolve cast plate and board URLs from spine GET or enrol terminal artefacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from creation.stranded_voice import unlooked_unused_cast_ids


def _latest_terminal_path(api_dir: Path, glob_pattern: str) -> Path | None:
    """Return the newest matching terminal JSON under ``api_dir``."""

    matches = sorted(
        api_dir.glob(glob_pattern), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return matches[0] if matches else None


def _terminal_output(api_dir: Path, glob_pattern: str) -> dict[str, Any] | None:
    path = _latest_terminal_path(api_dir, glob_pattern)
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    output = payload.get("output")
    return output if isinstance(output, dict) else None


def drawn_cast_rows(spine: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the cast rows that get an identity plate, in spine order.

    Mirrors the server's ``drawn_cast`` (fictora-drama #517): every cast row
    except

    - a character it only ever hears (an intercom, a phone caller, a narrator:
      every line off screen, in no frame), flagged ``voice_only: true`` on the
      card (fictora-drama #453 / #469); the kit reads that flag and never
      re-derives it, so a row without it (an older server) counts as drawn;
    - an unused character with no visual brief (no line, vocalization,
      inner-voice cue or frame, on a spine that has frames:
      :func:`creation.stranded_voice.unlooked_unused_cast_ids`), such as a voice
      whose only line was removed. The server keeps the card but draws no plate.

    An unused character who has a visual brief (written for a later episode) is
    still drawn and still counted.

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` body.

    Returns
    -------
    list[dict[str, Any]]
        Cast rows with a ``cast_id`` the server draws a plate for.
    """

    skipped = unlooked_unused_cast_ids(spine)
    return [
        row
        for row in (spine.get("cast") or [])
        if isinstance(row, dict)
        and row.get("cast_id")
        and row.get("voice_only") is not True
        and str(row["cast_id"]) not in skipped
    ]


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
        One URL per drawn cast member (voice-only cast is skipped, so a
        leftover plate for them never reaches ``plates/``), spine-first then
        terminal fallback.
    """

    urls: list[str] = []
    cast_rows = drawn_cast_rows(spine)
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
        for key in (
            "portrait_url",
            "full_body_url",
            "reference_url",
            "image_url",
            "url",
        ):
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


def board_urls_for_episode(
    spine: dict[str, Any], api_dir: Path, *, ordinal: int
) -> list[str]:
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
        if (
            int(summary.get("episode_ordinal") or summary.get("ordinal") or 1)
            == ordinal
        ):
            episode_id = str(summary.get("episode_id") or "")
            break
    for summary in spine.get("episode_summaries") or []:
        if not isinstance(summary, dict):
            continue
        if (
            int(summary.get("episode_ordinal") or summary.get("ordinal") or 1)
            != ordinal
        ):
            continue
        for frame in summary.get("frames") or []:
            if isinstance(frame, dict) and frame.get("image_url"):
                urls.append(str(frame["image_url"]))
    for board in spine.get("boards") or []:
        if not isinstance(board, dict):
            continue
        if (
            episode_id
            and board.get("episode_id") == episode_id
            and board.get("image_url")
        ):
            urls.append(str(board["image_url"]))
    if urls:
        return urls
    output = _terminal_output(api_dir, "09_*boards_terminal*.json")
    if not output:
        return []
    target = episode_id or f"episode_{ordinal:02d}"
    for board in output.get("boards") or []:
        if (
            isinstance(board, dict)
            and board.get("episode_id") == target
            and board.get("image_url")
        ):
            urls.append(str(board["image_url"]))
    return urls
