"""The human's opening action: how an episode's first shot moves.

Approved item #6 (Motion & Physics Review, 13 Oct 2026): cell 1 often drew a
still pose and the real action arrived about 2.5 s in (L-20261005-17, Sighted
ep 4, NOCLIP ep 3). The server takes an optional ``opening_action`` ("she kicks
the door open") on the draft (episode 1) and on ``pilot-episodes/{n}/author``
(episode 2 on); it pins beat 1's motion and the board's first cell to it.
Empty sends nothing, and the server opens mid-motion on its own as before.

The kit sends it only to a server that takes it: a deploy whose
``/openapi.json`` does not list the field is told plainly, before anything is
sent (an older server answers an unknown field with HTTP 422).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Most characters the server takes (``OPENING_ACTION_MAX_CHARS`` in fictora-drama).
OPENING_ACTION_LIMIT = 200
#: Request schemas that carry it: the draft (episode 1) and the next-episode author request.
DRAFT_SCHEMA = "DramaPromptVideoAuthoringDraftCreateRequest"
AUTHOR_SCHEMA = "DramaPilotEpisodeAuthorRequest"


class OpeningActionRefused(ValueError):
    """An opening action the kit will not send (too long, or the server does not take one)."""


def clean_opening_action(text: str | None) -> str | None:
    """Return the opening action as the server reads it: one line, blank is none.

    Parameters
    ----------
    text
        The human's words, from ``--opening-action``.

    Returns
    -------
    str | None
        Whitespace collapsed, or ``None`` when blank.

    Raises
    ------
    OpeningActionRefused
        It is longer than :data:`OPENING_ACTION_LIMIT` characters.
    """

    cleaned = " ".join((text or "").split()) or None
    if cleaned is not None and len(cleaned) > OPENING_ACTION_LIMIT:
        raise OpeningActionRefused(
            f"--opening-action is {len(cleaned)} characters; the server takes at most {OPENING_ACTION_LIMIT}. "
            'Nothing was sent. Keep only how the first shot moves ("she kicks the door open").'
        )
    return cleaned


def schema_takes_opening_action(openapi: Any, schema: str) -> bool | None:
    """Whether the deploy's ``/openapi.json`` lists ``opening_action`` on ``schema``.

    Parameters
    ----------
    openapi
        ``/openapi.json`` as read (anything else reads as unknown).
    schema
        Schema name suffix (:data:`DRAFT_SCHEMA` or :data:`AUTHOR_SCHEMA`).

    Returns
    -------
    bool | None
        ``True`` / ``False`` when the schema is there, ``None`` when it is not
        (the document could not be read or names no such request).
    """

    if not isinstance(openapi, Mapping):
        return None
    schemas = (openapi.get("components") or {}).get("schemas") or {}
    if not isinstance(schemas, Mapping):
        return None
    for name, body in schemas.items():
        if str(name).endswith(schema) and isinstance(body, Mapping):
            return "opening_action" in (body.get("properties") or {})
    return None


def require_opening_action_support(run: Any, schema: str, *, label: str) -> None:
    """Stop before sending an opening action to a deploy that does not take one.

    Parameters
    ----------
    run
        Session with ``get_optional(path) -> (status, body)``.
    schema
        The request schema the action rides on.
    label
        The command saying it (``draft``, ``author``).

    Raises
    ------
    OpeningActionRefused
        ``/openapi.json`` names the request and it has no ``opening_action``.
        An unreadable document lets the request go: the server's own 422 is
        then the answer.
    """

    status, doc = run.get_optional("/openapi.json")
    takes = schema_takes_opening_action(doc, schema) if 200 <= status < 300 else None
    if takes is False:
        raise OpeningActionRefused(
            f"[{label}] this server does not take an opening action yet (fictora-drama: opening action on the "
            "brief). Nothing was sent. Run again without --opening-action, or after the server update."
        )


__all__ = [
    "AUTHOR_SCHEMA",
    "DRAFT_SCHEMA",
    "OPENING_ACTION_LIMIT",
    "OpeningActionRefused",
    "clean_opening_action",
    "require_opening_action_support",
    "schema_takes_opening_action",
]
