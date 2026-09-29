"""A beat's expression: the anime reaction a creator asks one beat to play (``beats[].reaction_kind``).

The server (fictora-drama #482) keeps an expression library and lists it on
``GET /v1/capabilities`` (``reaction_kinds: [{kind, label, comedy}]``, in the
library's order). A beat asks for one with ``PATCH /v1/spines/{id}``
``beats[].reaction_kind`` (``null`` clears it and gives the choice back to the
frames author). The server lays the kind on the beat's anchor frame (both
frames of its row on a row board), so the board prompt draws its marks and the
take prompt plays it.

The kit checks the kind against the deploy's own list before it sends
anything, and refuses on a deploy older than the field (its
``/openapi.json`` beat patch has no ``reaction_kind``, or it has no
``/v1/capabilities``) instead of sending a field the server would drop.

Two things stay open on the server: the comedy kinds are not refused on a
serious scene (the register is not enforced), and the request does not say
whose face wears the kind when a row stages two people.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from creation.harness.stages_gated import _schema_has

#: The route that lists the library.
CAPABILITIES_PATH = "/v1/capabilities"
#: The ``schema_version`` the kit reads.
CAPABILITIES_SCHEMA = "fictora.drama-capabilities.v1"
#: The OpenAPI schema (suffix) for one beat of a spine patch.
BEAT_PATCH_SCHEMA = "DramaBeatPatch"
#: What ``--expression`` takes to clear a beat's request.
CLEAR_WORDS = frozenset({"none", "null", "clear"})

#: Said when the deploy is older than beat expressions.
OLDER_SERVER = (
    "this Drama API is older than beat expressions (fictora-drama #482): {why}. Nothing was sent. "
    "Ask for the deploy, or until then write the expression into the beat's motion_intent "
    '(`edit --beat N --intent "... she freezes, eyes wide ..."`).'
)


class ExpressionError(ValueError):
    """An expression the deploy does not offer, or a deploy that has none; the message says which."""


def server_takes_expression(openapi: Any) -> bool | None:
    """Whether the deploy's spine patch takes ``beats[].reaction_kind``.

    Parameters
    ----------
    openapi
        ``/openapi.json`` as read from the deploy.

    Returns
    -------
    bool | None
        ``True`` it does, ``False`` an older deploy, ``None`` the schema is not there to say.
    """

    return _schema_has(openapi, BEAT_PATCH_SCHEMA, "reaction_kind")


def expression_options(payload: Any) -> list[dict[str, Any]]:
    """The library from a ``GET /v1/capabilities`` answer, in the server's order.

    Parameters
    ----------
    payload
        The answer's JSON.

    Returns
    -------
    list[dict[str, Any]]
        ``{kind, label, comedy}`` per expression.

    Raises
    ------
    ExpressionError
        The answer is not a capabilities document the kit reads, or lists no expressions.
    """

    if not isinstance(payload, Mapping):
        raise ExpressionError(f"{CAPABILITIES_PATH} did not answer with a JSON object")
    version = payload.get("schema_version")
    if version != CAPABILITIES_SCHEMA:
        raise ExpressionError(
            f"{CAPABILITIES_PATH} answered schema {version!r}; the kit reads {CAPABILITIES_SCHEMA!r}"
        )
    options = [
        {
            "kind": str(item["kind"]),
            "label": str(item.get("label") or item["kind"]),
            "comedy": item.get("comedy") is True,
        }
        for item in payload.get("reaction_kinds") or []
        if isinstance(item, Mapping) and item.get("kind")
    ]
    if not options:
        raise ExpressionError(f"{CAPABILITIES_PATH} lists no expressions")
    return options


def _key(text: str) -> str:
    return "_".join(text.strip().lower().replace("-", " ").split())


def resolve_expression(raw: str, options: Sequence[Mapping[str, Any]]) -> str | None:
    """Turn ``--expression`` into the kind the API takes, or ``None`` to clear.

    Parameters
    ----------
    raw
        A kind (``slow_surprise``), its label (``slow surprise``), or ``none`` to clear.
    options
        The deploy's library (:func:`expression_options`).

    Returns
    -------
    str | None
        The kind, or ``None`` for a clear.

    Raises
    ------
    ExpressionError
        The deploy does not offer it; the message lists what it does.
    """

    wanted = _key(raw)
    if wanted in CLEAR_WORDS:
        return None
    for option in options:
        if wanted in {_key(str(option["kind"])), _key(str(option["label"]))}:
            return str(option["kind"])
    offered = ", ".join(str(option["kind"]) for option in options)
    raise ExpressionError(
        f"no expression {raw!r} on this deploy; it offers: {offered} (or `none` to clear)"
    )


def vocabulary_lines(options: Sequence[Mapping[str, Any]]) -> list[str]:
    """One printable line per expression, comedy ones marked.

    Parameters
    ----------
    options
        The deploy's library.

    Returns
    -------
    list[str]
        ``  kind  (label)  [comedy]``.
    """

    width = max((len(str(option["kind"])) for option in options), default=0)
    return [
        f"  {str(option['kind']).ljust(width)}  {option['label']}"
        + (
            "  [comedy: symbolic marks, light scenes only]"
            if option.get("comedy")
            else ""
        )
        for option in options
    ]


def beat_expression(beat: Mapping[str, Any]) -> str | None:
    """The expression a beat asks for, or ``None`` when the frames author chooses.

    Parameters
    ----------
    beat
        One ``beats[]`` entry of the spine.

    Returns
    -------
    str | None
        Its ``reaction_kind``.
    """

    value = beat.get("reaction_kind")
    return str(value) if value else None


__all__ = [
    "BEAT_PATCH_SCHEMA",
    "CAPABILITIES_PATH",
    "CAPABILITIES_SCHEMA",
    "CLEAR_WORDS",
    "OLDER_SERVER",
    "ExpressionError",
    "beat_expression",
    "expression_options",
    "resolve_expression",
    "server_takes_expression",
    "vocabulary_lines",
]
