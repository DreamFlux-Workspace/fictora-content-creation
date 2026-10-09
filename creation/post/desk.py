"""Desk lookups shared by local post: the spine, an episode's lines, the take, its board, its job."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from creation.harness.credentials import load_drama_api_credentials
from creation.harness.raw_video import episode_clips, raw_clips_records
from creation.harness.session import DramaApiRunSession
from creation.ops.state import episode_by_ordinal, load_series, take_by_id
from creation.production_state import load_production
from creation.spine_view import episode_id_for


def open_api(desk: Path, episode: int) -> DramaApiRunSession:
    """An API session on the desk's ``session_id``, saving JSON under ``epNN/api``.

    Parameters
    ----------
    desk
        Series desk with ``production.json``.
    episode
        Episode ordinal whose ``api/`` folder receives snapshots.

    Returns
    -------
    DramaApiRunSession
        Open session (close ``.client`` when done).
    """

    state = load_production(desk)
    base, token = load_drama_api_credentials(Path(__file__).resolve().parents[2])
    api_dir = desk / f"ep{episode:02d}" / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    return DramaApiRunSession(
        base_url=base, token=token, out_dir=api_dir, session_id=state.session_id
    )


def spine_id(desk: Path) -> str:
    """The desk's spine id from ``production.json``.

    Raises
    ------
    ValueError
        When the desk has no spine yet.
    """

    found = load_production(desk).spine_id
    if not found:
        raise ValueError(
            "this desk has no spine_id yet; run `fictora-produce step` first"
        )
    return found


def spine_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Unwrap ``{"spine": {...}}`` responses to the spine itself."""

    inner = payload.get("spine")
    return dict(inner) if isinstance(inner, Mapping) else dict(payload)


def saved_spine(desk: Path, episode: int) -> tuple[dict[str, Any], Path] | None:
    """Newest spine snapshot in ``epNN/api/`` that carries beats (approve receipts are skipped).

    Returns
    -------
    tuple[dict[str, Any], Path] | None
        The spine and its file, or ``None``.
    """

    api = desk / f"ep{episode:02d}" / "api"
    for path in sorted(api.glob("*spine*.json"), key=lambda p: p.name, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and spine_body(payload).get("beats"):
            return spine_body(payload), path
    return None


def current_cast_cards(
    desk: Path, spine: Mapping[str, Any] | None, snapshot: Path | None
) -> dict[str, dict[str, Any]]:
    """The episode spine's cast cards, each with the voice the desk holds now (``cast_id`` -> card).

    ``voice --pick`` (and every other ``refresh_spine``) saves the spine as the
    desk's current story (``api/spine.json``) and as episode 1's snapshot only;
    episode 2's snapshot still names the voice it had when it was saved. A
    card's ``voice_brief`` therefore comes from ``api/spine.json`` when that
    file is at least as new as the episode's ``snapshot``, so the next finish
    speaks in the voice the operator picked (L-20261006-29). Every other field
    stays the episode's own.
    """

    cards = {
        str(card.get("cast_id")): dict(card)
        for card in (spine or {}).get("cast") or []
        if isinstance(card, dict) and card.get("cast_id")
    }
    current = desk / "api" / "spine.json"
    try:
        if snapshot is not None and current.stat().st_mtime < snapshot.stat().st_mtime:
            return cards
        body = spine_body(json.loads(current.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, AttributeError):
        return cards
    for card in body.get("cast") or []:
        if not isinstance(card, dict) or str(card.get("cast_id")) not in cards:
            continue
        brief = card.get("voice_brief")
        if isinstance(brief, Mapping):
            cards[str(card["cast_id"])]["voice_brief"] = dict(brief)
    return cards


def refresh_spine(run: DramaApiRunSession, desk: Path, episode: int) -> dict[str, Any]:
    """GET the spine and save it where the episode flow keeps it (``api/spine.json``, ``epNN/api/spine.json``).

    Returns
    -------
    dict[str, Any]
        The spine.
    """

    from creation.orchestrate import save_spine_snapshot

    body = spine_body(run.spine(spine_id(desk)))
    save_spine_snapshot(desk, episode, body)
    return body


def show_language(spine: Mapping[str, Any]) -> str:
    """The show's spoken language as a Whisper / voice code: ``ja-JP`` -> ``ja``; ``en`` when unset."""

    code = str(spine.get("spoken_language") or "en").strip()
    return (code.split("-", 1)[0] or "en").lower()


def episode_dialogue(spine: Mapping[str, Any], ordinal: int) -> list[dict[str, str]]:
    """Every dialogue line of one episode, in beat order.

    Each is ``{line_id, cast_id, text, spoken_text, subtitle, performed}``: ``performed``
    is what is heard (``spoken_text`` when the line has one, else ``text``);
    ``subtitle`` is the caption (``subtitle_text`` when present, else ``text``).
    """

    wanted = {episode_id_for(spine, ordinal), f"episode_{ordinal:02d}"}
    beats = [
        b
        for b in spine.get("beats") or []
        if isinstance(b, Mapping) and b.get("episode_id") in wanted
    ]
    lines: list[dict[str, str]] = []
    for beat in beats:
        for line in beat.get("dialogue_lines") or []:
            text = (
                str(line.get("text") or "").strip() if isinstance(line, Mapping) else ""
            )
            if text:
                spoken = str(line.get("spoken_text") or "").strip()
                lines.append(
                    {
                        "line_id": str(line.get("line_id") or ""),
                        "cast_id": str(line.get("cast_id") or ""),
                        "text": text,
                        "spoken_text": spoken,
                        "subtitle": str(line.get("subtitle_text") or "").strip()
                        or text,
                        "performed": spoken or text,
                    }
                )
    return lines


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cast_slug(cast_id: str) -> str:
    """``cast_kenji-mori`` -> ``kenji-mori``."""

    return _slug(cast_id.removeprefix("cast_")) or "cast"


def _name_keys(name: str) -> set[str]:
    """Slugs and exact parts of a cast name, including a reading in parentheses.

    ``Koharu (小春)`` matches ``Koharu`` and ``小春``.
    """

    keys = {name.casefold()}
    slug = _slug(name)
    if slug:
        keys.add(slug)
    for part in re.split(r"[()（）]", name):
        part = part.strip()
        if not part:
            continue
        keys.add(part.casefold())
        part_slug = _slug(part)
        if part_slug:
            keys.add(part_slug)
    return keys


def name_matches(card_id: str, card_name: str, wanted: str) -> bool:
    """True when ``wanted`` is this card's id, name, reading, or slug.

    A reading in parentheses counts: ``Koharu (小春)`` matches ``Koharu`` and ``小春``.

    Parameters
    ----------
    card_id
        ``cast_id``.
    card_name
        Display name.
    wanted
        What the operator typed.

    Returns
    -------
    bool
        Whether the card is the one they named.
    """

    key = _slug(wanted.removeprefix("cast_"))
    names = _name_keys(card_name)
    return (
        wanted in {card_id, card_name}
        or wanted.casefold() in {card_id.casefold(), *names}
        or key in {cast_slug(card_id), _slug(card_name)}
        or bool(key)
        and key in names
    )


def find_cast(spine: Mapping[str, Any], wanted: str) -> dict[str, Any]:
    """One cast card by ``cast_id``, name, or slug of either.

    A reading in parentheses is part of the name: ``Koharu (小春)`` matches
    ``Koharu`` and ``小春``.

    Raises
    ------
    ValueError
        When nobody matches; the message lists the cast.
    """

    cards = [card for card in spine.get("cast") or [] if isinstance(card, Mapping)]
    for card in cards:
        cid, name = str(card.get("cast_id") or ""), str(card.get("name") or "")
        if name_matches(cid, name, wanted):
            return dict(card)
    names = (
        ", ".join(f"{c.get('cast_id')} ({c.get('name')})" for c in cards) or "nobody"
    )
    raise ValueError(f"no cast member {wanted!r} on the spine; the cast is: {names}")


def _version(path: Path) -> int:
    match = re.search(r"-v(\d+)$", path.stem)
    return int(match.group(1)) if match else 0


def latest_raw_take(desk: Path, episode: int, take_id: str) -> Path:
    """Newest ``takes/take-epNN-tK-raw-vN.mp4``.

    Raises
    ------
    FileNotFoundError
        When the desk has no raw take.
    """

    takes = desk / f"ep{episode:02d}" / "takes"
    found = [
        p
        for p in takes.glob(f"take-ep{episode:02d}-{take_id}-raw-v*.mp4")
        if p.is_file()
    ]
    if not found:
        raise FileNotFoundError(
            f"no raw take take-ep{episode:02d}-{take_id}-raw-vN.mp4 in {takes}; "
            "run `fictora-produce step --confirm-spend` first"
        )
    return max(found, key=_version)


def approved_board(desk: Path, episode: int, take_id: str) -> Path | None:
    """The board the human approved for this take (desk record), else the newest board for it."""

    try:
        take = take_by_id(episode_by_ordinal(load_series(desk), episode), take_id)
        if take.board.status == "approved" and take.board.path:
            stored = Path(take.board.path)
            path = stored if stored.is_absolute() else desk / stored
            if path.is_file():
                return path
    except (FileNotFoundError, ValueError, KeyError):
        pass
    boards = desk / f"ep{episode:02d}" / "boards"
    found = [
        p for p in boards.glob(f"board-ep{episode:02d}-{take_id}*.png") if p.is_file()
    ]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def board_versions(desk: Path, episode: int, take_id: str) -> list[Path]:
    """Every drawing of this take's board on the desk: the approved one first, then the rest, newest first.

    A take compiled before its board was redrawn can open on the earlier
    drawing (Sweet Racket ep 4 t2, L-20261009-1), so ``deboard`` measures the
    take against each of them, not only the approved one.

    Parameters
    ----------
    desk
        The desk.
    episode
        Episode ordinal.
    take_id
        ``t1``, ``t2`` ...

    Returns
    -------
    list[Path]
        Board images, :func:`approved_board` first; empty when the desk has none.
    """

    first = approved_board(desk, episode, take_id)
    boards = desk / f"ep{episode:02d}" / "boards"
    own = re.compile(rf"^board-ep{episode:02d}-{re.escape(take_id)}(?:-.*)?$")
    found = sorted(
        (
            p
            for p in boards.glob(f"board-ep{episode:02d}-{take_id}*.png")
            if p.is_file() and own.match(p.stem)
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    ordered = [first] if first is not None else []
    ordered += [p for p in found if first is None or p.resolve() != first.resolve()]
    return ordered


def take_clip(desk: Path, episode: int, take_id: str) -> dict[str, Any] | None:
    """The take's newest clip (``job_id`` and the stored ``url``) in the episode's clip records.

    The records are the step's ``epNN/api/17_raw_scene_clips.json`` and each
    ``film`` unit's ``epNN/api/film-*-raw-scene-clips.json``
    (:func:`creation.harness.raw_video.raw_clips_records`), newest first, so a
    take filmed again by ``film`` is read from that film, not from the step.
    In each record the episode's clips are found by its API id (by ordinal via
    ``episode_summaries``); take ``tN`` is the clip with board index ``N``,
    else the ``N``-th clip (the way ``film`` collected it).
    """

    found = saved_spine(desk, episode)
    wanted = episode_id_for(found[0], episode) if found else f"episode_{episode:02d}"
    index = int(take_id.lstrip("t") or 1)
    for path in raw_clips_records(desk / f"ep{episode:02d}" / "api"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        clips = episode_clips(raw, episode_id=wanted)
        for clip in clips:
            if clip.get("set_index") == index:
                return dict(clip)
        if 0 < index <= len(clips) and clips[index - 1].get("set_index") is None:
            return dict(clips[index - 1])
    return None


def take_job_id(desk: Path, episode: int, take_id: str) -> str | None:
    """The take's scene job id (see :func:`take_clip`)."""

    clip = take_clip(desk, episode, take_id)
    return str(clip["job_id"]) if clip and clip.get("job_id") else None


def take_stored_url(desk: Path, episode: int, take_id: str) -> str | None:
    """The take's durable URL in our storage (what ``/v1/transcripts`` may read)."""

    clip = take_clip(desk, episode, take_id)
    return str(clip["url"]) if clip and clip.get("url") else None
