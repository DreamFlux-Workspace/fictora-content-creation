"""Classify POST /v1/video-generations refusals for operator hints."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: Matches the request field ``episode_ordinal``, not ``episode_ordinals`` in JSON.
_EPISODE_ORDINAL_FIELD = re.compile(r"(?<![\w])episode_ordinal(?![\w])")


def request_films_one_episode(body: Mapping[str, Any] | None) -> int | None:
    """Return the episode ordinal a reuse body targets, if any.

    Parameters
    ----------
    body
        JSON body sent to ``POST /v1/video-generations``.

    Returns
    -------
    int | None
        ``episode_ordinal`` when present and positive; otherwise ``None``.
    """

    if body is None:
        return None
    raw = body.get("episode_ordinal")
    if raw is None:
        return None
    try:
        ordinal = int(raw)
    except (TypeError, ValueError):
        return None
    return ordinal if ordinal >= 1 else None


def error_text_mentions_episode_ordinal_field(text: str) -> bool:
    """Return whether ``text`` names the request field, not ``episode_ordinals``.

    A naive ``"episode_ordinal" in text`` check is wrong: it matches the
    ``episode_ordinals`` detail key in ``boards_not_approved_for_generation``
    responses and sends operators toward the #436 deploy hint incorrectly.

    Parameters
    ----------
    text
        Error message or serialized API response.

    Returns
    -------
    bool
        True when the standalone field name appears.
    """

    return _EPISODE_ORDINAL_FIELD.search(text) is not None


def server_refused_episode_ordinal_field(
    text: str,
    *,
    request_body: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether a 422 means the deploy rejects ``episode_ordinal`` on the body.

    Parameters
    ----------
    text
        Error text from the HTTP client (often ``HTTP 422 …``).
    request_body
        Body that was posted. An older API can only refuse ``episode_ordinal``
        when the body sent it, so a body without the field is never a schema
        refusal. ``None`` (body unknown) is judged on the text alone.

    Returns
    -------
    bool
        True when the message looks like an older API rejecting the field.
    """

    if request_body is not None and request_films_one_episode(request_body) is None:
        return False
    if "HTTP 422" not in text:
        return False
    if "boards_not_approved_for_generation" in text:
        return False
    if "body.episode_ordinal" in text:
        return True
    if error_text_mentions_episode_ordinal_field(text) is False:
        return False
    lower = text.lower()
    return "extra input" in lower or "not permitted" in lower or "validation_error" in lower


__all__ = [
    "error_text_mentions_episode_ordinal_field",
    "request_films_one_episode",
    "server_refused_episode_ordinal_field",
]
