"""Story objects: the things a story turns on, each with one picture every board and take keeps to.

fictora-drama ``story_props`` (founder-approved 9 Oct 2026): Hanakaze's carved
営業中 sign came back as a painted pink sakura on a board, because nothing held
its look. The server now gives the objects a story turns on (at most three an
episode) a prop card when the episode is written, draws ONE picture per object
in the same step as the cast plates (one still each, never per take), and,
once approved, every board and take from that episode on keeps to it. The
plates gate's approval (``cast/approve``) approves the object pictures too.

The kit says which objects an episode added right after ``author``, draws an
owed object picture at the plates gate (from episode 2 the episode routes
through it again, as for a new character), and saves the pictures next to the
plates so the human sees them before ``approve --gate plates``. A server
without the feature sends no ``props``: nothing changes.
"""

from __future__ import annotations

import re
from typing import Any, Collection, Mapping


def _props(spine: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        card
        for card in spine.get("props") or []
        if isinstance(card, Mapping) and card.get("prop_id")
    ]


def _latest_pictures(spine: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for asset in spine.get("media_assets") or []:
        if (
            isinstance(asset, Mapping)
            and asset.get("relation_type") == "prop"
            and asset.get("kind", "still") == "still"
        ):
            latest[str(asset.get("relation_id"))] = asset
    return latest


def _shown_by(card: Mapping[str, Any], episode: int) -> bool:
    start = card.get("intro_episode_ordinal")
    return start is None or int(start) <= episode


def objects_to_draw(spine: Mapping[str, Any], *, episode: int) -> list[tuple[str, str]]:
    """Story objects used by ``episode`` with no current picture: the plates step draws them.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        The episode about to be drawn.

    Returns
    -------
    list[tuple[str, str]]
        ``(prop_id, name)`` of each object never drawn, or whose redraw was
        asked for (stale), in card order.
    """

    pictures = _latest_pictures(spine)
    found: list[tuple[str, str]] = []
    for card in _props(spine):
        if not _shown_by(card, episode):
            continue
        picture = pictures.get(str(card["prop_id"]))
        if picture is None or not picture.get("url") or picture.get("stale"):
            found.append(
                (str(card["prop_id"]), str(card.get("name") or card["prop_id"]))
            )
    return found


def objects_owing_approval(
    spine: Mapping[str, Any], *, episode: int
) -> list[tuple[str, str]]:
    """Story objects used by ``episode`` without an approved, current picture (to draw or to approve).

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        The episode about to be boarded.

    Returns
    -------
    list[tuple[str, str]]
        ``(prop_id, name)`` in card order; empty when every one is approved.
    """

    pictures = _latest_pictures(spine)
    return [
        (str(card["prop_id"]), str(card.get("name") or card["prop_id"]))
        for card in _props(spine)
        if _shown_by(card, episode)
        and not (
            (picture := pictures.get(str(card["prop_id"]))) is not None
            and picture.get("url")
            and not picture.get("stale")
            and picture.get("review_state") == "approved"
        )
    ]


def object_picture_urls(
    spine: Mapping[str, Any], *, only: Collection[str] | None = None
) -> list[tuple[str, str]]:
    """Current object pictures to save next to the plates.

    Parameters
    ----------
    spine
        Spine JSON after the plates step.
    only
        Prop ids to return; None returns every object with a current picture.

    Returns
    -------
    list[tuple[str, str]]
        ``(file stem, url)`` per picture, in card order.
    """

    pictures = _latest_pictures(spine)
    rows: list[tuple[str, str]] = []
    for card in _props(spine):
        prop_id = str(card["prop_id"])
        if only is not None and prop_id not in only:
            continue
        picture = pictures.get(prop_id)
        if picture is None or not picture.get("url") or picture.get("stale"):
            continue
        slug = re.sub(
            r"[^a-z0-9]+", "-", str(card.get("name") or prop_id).casefold()
        ).strip("-")
        rows.append((f"object-{slug or prop_id}", str(picture["url"])))
    return rows


def new_objects(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[tuple[str, str]]:
    """The story objects ``after`` has that ``before`` did not, in card order."""

    known = {str(card["prop_id"]) for card in _props(before)}
    return [
        (str(card["prop_id"]), str(card.get("name") or card["prop_id"]))
        for card in _props(after)
        if str(card["prop_id"]) not in known
    ]


def new_objects_notice(found: list[tuple[str, str]]) -> list[str]:
    """The lines ``author`` prints for each story object the episode added."""

    return [
        f"story object added: {name} ({prop_id}) — one picture is drawn at the plates gate (~$0.30, once) "
        "and every board and take from this episode on keeps to it; approve it with `approve --gate plates`"
        for prop_id, name in found
    ]


__all__ = [
    "new_objects",
    "new_objects_notice",
    "object_picture_urls",
    "objects_owing_approval",
    "objects_to_draw",
]
