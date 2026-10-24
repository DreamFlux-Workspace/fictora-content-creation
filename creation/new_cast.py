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

from typing import Any, Collection, Mapping

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


#: ``cast_pictures_owed`` reasons a picture step draws (paid); the others only wait for a yes.
_TO_DRAW = frozenset({"plate_missing", "plate_stale"})


def server_pictures_owed(
    spine: Mapping[str, Any],
) -> list[Mapping[str, Any]] | None:
    """The server's own list of characters the boards wait on for a picture, or ``None`` from an older server.

    fictora-drama (24 Oct 2026) puts ``cast_pictures_owed`` on every spine it
    returns: the boards gate's own check (``cast_not_approved``), so the kit
    offers a newcomer's picture before boards instead of meeting the refusal.
    An empty list means nothing is owed; a missing field means a server from
    before that, and the kit falls back to reading the cast itself.

    Parameters
    ----------
    spine
        Spine JSON.

    Returns
    -------
    list[Mapping[str, Any]] | None
        Each ``{cast_id, name, intro_episode_ordinal, reason}``; ``None`` when
        the server does not say.
    """

    owed = spine.get("cast_pictures_owed")
    if not isinstance(owed, list):
        return None
    return [item for item in owed if isinstance(item, Mapping) and item.get("cast_id")]


def pictures_waiting_for_yes(spine: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Characters and story objects the server says are drawn and only wait for the plates yes before boards.

    Objects come from ``object_pictures_owed`` (fictora-drama, 24 Oct 2026):
    the plates yes (``cast/approve``) approves their pictures too.

    Parameters
    ----------
    spine
        Spine JSON.

    Returns
    -------
    list[tuple[str, str]]
        ``(cast_id or prop_id, name)``, characters first; empty from an older
        server or when none waits.
    """

    waiting = [
        (str(item["cast_id"]), str(item.get("name") or item["cast_id"]))
        for item in server_pictures_owed(spine) or []
        if item.get("reason") not in _TO_DRAW
    ]
    objects = spine.get("object_pictures_owed")
    if isinstance(objects, list):
        waiting.extend(
            (str(item["prop_id"]), str(item.get("name") or item["prop_id"]))
            for item in objects
            if isinstance(item, Mapping) and item.get("prop_id")
        )
    return waiting


def cast_owing_pictures(
    spine: Mapping[str, Any], *, episode: int
) -> list[tuple[str, str]]:
    """Characters whose picture must be drawn before this episode's boards.

    The server says who (``cast_pictures_owed``: no picture yet, or the card
    changed after it): a later episode's newcomer, whoever brought them in.
    From an older server, the kit's own read: characters brought in after
    episode 1 (by ``episode``) who are drawn and have no approved picture.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        The episode about to be boarded.

    Returns
    -------
    list[tuple[str, str]]
        ``(cast_id, name)``; empty when no picture is owed.
    """

    owed = server_pictures_owed(spine)
    if owed is not None:
        return [
            (str(item["cast_id"]), str(item.get("name") or item["cast_id"]))
            for item in owed
            if item.get("reason") in _TO_DRAW
        ]
    approved = _approved_plate_ids(spine)
    return [
        (str(card["cast_id"]), str(card.get("name") or card["cast_id"]))
        for card in drawn_cast_rows(dict(spine))
        if 1 < int(card.get("intro_episode_ordinal") or 1) <= episode
        and str(card["cast_id"]) not in approved
    ]


def newcomers_owing_pictures(
    before: Mapping[str, Any], after: Mapping[str, Any], *, episode: int
) -> list[tuple[str, str]]:
    """Who `author` names as new: the cast the episode added, plus anyone the server says owes a picture.

    The server's list (characters with no picture yet) also catches one who
    joined outside `author` (added from the app's chat, say), so the kit hears
    of them right after the episode is written, not from the boards.

    Parameters
    ----------
    before, after
        Spine JSON before and after `author`.
    episode
        The episode just written.

    Returns
    -------
    list[tuple[str, str]]
        ``(cast_id, name)``, the added cast first; no one twice.
    """

    found = newcomers(before, after)
    seen = {cast_id for cast_id, _ in found}
    found.extend(
        (str(item["cast_id"]), str(item.get("name") or item["cast_id"]))
        for item in server_pictures_owed(after) or []
        if item.get("reason") == "plate_missing"
        and int(item.get("intro_episode_ordinal") or 1) <= episode
        and str(item["cast_id"]) not in seen
    )
    return found


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


def cast_without_pictures(
    spine: Mapping[str, Any], cast_ids: Collection[str]
) -> list[tuple[str, str]]:
    """The characters of ``cast_ids`` the plates step came back without a picture for.

    Noodle24 ep 4 (L-20261007-5) and Gallery Heiress ep 2 (L-20261008-17): the
    server answered a new character's plates step with episode 1's finished
    cast run, nothing was drawn, and the kit still printed "Cast drawn".

    Parameters
    ----------
    spine
        Spine JSON after the plates step.
    cast_ids
        The characters the step was meant to draw.

    Returns
    -------
    list[tuple[str, str]]
        ``(cast_id, name)`` in cast order of each character with no current
        picture (any review state); empty when every one has one.
    """

    drawn = {
        str(asset.get("relation_id"))
        for asset in spine.get("media_assets") or []
        if isinstance(asset, Mapping)
        and asset.get("relation_type") == "cast_card"
        and asset.get("url")
        and not asset.get("stale")
    }
    wanted = {str(cast_id) for cast_id in cast_ids}
    return [
        (str(card["cast_id"]), str(card.get("name") or card["cast_id"]))
        for card in _cast(spine)
        if str(card["cast_id"]) in wanted and str(card["cast_id"]) not in drawn
    ]


def no_picture_message(missing: list[tuple[str, str]], *, desk: str) -> str:
    """The stop the plates step prints when a character it owed got no picture.

    Parameters
    ----------
    missing
        From :func:`cast_without_pictures`.
    desk
        The desk path, for the commands it prints.

    Returns
    -------
    str
        Who has no picture and how to draw each one.
    """

    names = ", ".join(name for _, name in missing)
    draws = "\n".join(
        f'  fictora-produce redraw-plate --desk {desk} --cast "{name}"  (one picture, ~$0.30)'
        for _, name in missing
    )
    return (
        f"No picture came back for {names}: the server answered with an earlier cast run "
        "and drew nothing new. The plates step is not done.\n"
        f"Draw each one, then run `step` again:\n{draws}"
    )


__all__ = [
    "cast_owing_pictures",
    "pictures_waiting_for_yes",
    "server_pictures_owed",
    "cast_without_pictures",
    "new_cast_notice",
    "newcomers",
    "newcomers_owing_pictures",
    "no_picture_message",
]
