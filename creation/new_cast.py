"""A character a later episode brings in: say so after `author`, and draw and approve their picture before boards.

Founder decision, 1 Oct 2026 (fictora-drama ``new_cast_members``). When a later
episode's direction names a new person who speaks or is staged, the server adds a
cast card for them (``intro_episode_ordinal`` is that episode) with no picture
yet, and refuses that story's boards (``cast_not_approved``) until the picture is
drawn and approved, as for episode 1's cast. On End of the Line ep 2 the new
ghost's lines went to the episode-1 ghost; on Seedlings ep 2 "the boy" got no
plate. The kit says who is new right after `author`, routes the episode through
the plates gate again after its script yes, draws only the newcomer's picture
(~$0.30 each; the approved plates are reused), and goes on to boards after
`approve --gate plates`.
"""

from __future__ import annotations

from typing import Any, Mapping

from creation.desk_media_urls import drawn_cast_rows


def _cast(spine: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        card
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    ]


def newcomers(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[tuple[str, str]]:
    """The cast cards ``after`` has that ``before`` did not, in cast order.

    Parameters
    ----------
    before, after
        Spine JSON before and after `author`.

    Returns
    -------
    list[tuple[str, str]]
        ``(cast_id, name)`` of each new character.
    """

    known = {str(card["cast_id"]) for card in _cast(before)}
    return [
        (str(card["cast_id"]), str(card.get("name") or card["cast_id"]))
        for card in _cast(after)
        if str(card["cast_id"]) not in known
    ]


def _approved_plate_ids(spine: Mapping[str, Any]) -> set[str]:
    return {
        str(asset.get("relation_id"))
        for asset in spine.get("media_assets") or []
        if isinstance(asset, Mapping)
        and asset.get("relation_type") == "cast_card"
        and asset.get("url")
        and not asset.get("stale")
        and asset.get("review_state") == "approved"
    }


def cast_owing_pictures(
    spine: Mapping[str, Any], *, episode: int
) -> list[tuple[str, str]]:
    """Characters brought in after episode 1 (by ``episode``) who are drawn and have no approved picture.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        The episode about to be boarded.

    Returns
    -------
    list[tuple[str, str]]
        ``(cast_id, name)``; empty when every newcomer's picture is approved
        (or none was brought in).
    """

    approved = _approved_plate_ids(spine)
    return [
        (str(card["cast_id"]), str(card.get("name") or card["cast_id"]))
        for card in drawn_cast_rows(dict(spine))
        if 1 < int(card.get("intro_episode_ordinal") or 1) <= episode
        and str(card["cast_id"]) not in approved
    ]


def new_cast_notice(found: list[tuple[str, str]]) -> list[str]:
    """The lines `author` prints for each character the episode brought in.

    Parameters
    ----------
    found
        From :func:`newcomers`.

    Returns
    -------
    list[str]
        One line per new character; empty when there is none.
    """

    return [
        f"new character added: {name} ({cast_id}) — approve their picture before boards "
        "(after the script yes, `step` draws it, ~$0.30; then `approve --gate plates`)"
        for cast_id, name in found
    ]


__all__ = ["cast_owing_pictures", "new_cast_notice", "newcomers"]
