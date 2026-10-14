"""Say a board refusal held for a story object's pending redraw in plain words (fictora-drama #787).

While an object an episode shows has a redraw pending (its new picture not
drawn or not approved yet), the server holds that episode's boards: the boards
step and a single board's redraw answer ``422 cast_not_approved`` with
``details.prop_reasons: {prop_id: "redraw_pending"}``, ``unapproved_prop_ids``
and ``unapproved_objects``. That code also means "the plates lost their
approval", whose fix (approve the same plates again) does not help here, so
this names the object and the two steps that clear it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The server's reason for an object whose new picture is not drawn and approved yet.
REDRAW_PENDING = "redraw_pending"


def _names(details: Mapping[str, Any]) -> dict[str, str]:
    """``prop_id -> name`` from ``unapproved_objects`` (a list of cards or names)."""

    found: dict[str, str] = {}
    for obj in details.get("unapproved_objects") or []:
        if isinstance(obj, Mapping):
            prop_id = str(obj.get("prop_id") or "")
            name = " ".join(str(obj.get("name") or prop_id).split())
            if prop_id:
                found[prop_id] = name
    return found


def pending_redraw_objects(code: Any, details: Any) -> list[str]:
    """The objects a ``cast_not_approved`` refusal holds for a pending redraw (empty for anything else).

    Parameters
    ----------
    code
        The refusal's ``code``.
    details
        Its ``details``.

    Returns
    -------
    list[str]
        Object names (the ``prop_id`` when the server gave no name).
    """

    if str(code or "") != "cast_not_approved" or not isinstance(details, Mapping):
        return []
    reasons = details.get("prop_reasons")
    if not isinstance(reasons, Mapping):
        return []
    names = _names(details)
    plain = [
        str(obj)
        for obj in details.get("unapproved_objects") or []
        if isinstance(obj, str) and obj.strip()
    ]
    pending = [str(pid) for pid, why in reasons.items() if str(why) == REDRAW_PENDING]
    if not pending:
        return []
    if not names and len(plain) == len(pending):
        return plain
    return [names.get(pid, pid) for pid in pending]


def prop_hold_text(code: Any, details: Any, *, desk: str = "D") -> str | None:
    """The plain words for a board refusal held by an object's pending redraw, or ``None``.

    Parameters
    ----------
    code
        The refusal's ``code``.
    details
        Its ``details``.
    desk
        The desk to put in the commands (``D`` when the caller has none).

    Returns
    -------
    str | None
        ``Boards are held: <object> is waiting for its redraw. ...`` with the
        commands, or ``None`` when this is not that refusal.
    """

    objects = pending_redraw_objects(code, details)
    if not objects:
        return None
    who = ", ".join(objects)
    verb = "is" if len(objects) == 1 else "are"
    rows = [
        f"Boards are held: {who} {verb} waiting for its redraw. Draw it (`redraw-prop … --confirm-spend`) "
        "and approve the plates first. Nothing was drawn or charged.",
    ]
    rows += [
        f'  fictora-produce redraw-prop --desk {desk} --prop "{name}" --note "what is wrong"   '
        "(prints the price; after the human's yes, the same command with --confirm-spend)"
        for name in objects
    ]
    rows.append(
        f"  then show the new picture and, after the yes: fictora-produce approve --desk {desk} --gate plates "
        "(--again when the desk is past the plates gate)"
    )
    return "\n".join(rows)


__all__ = ["REDRAW_PENDING", "pending_redraw_objects", "prop_hold_text"]
