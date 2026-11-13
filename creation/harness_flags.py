"""Per-story harness flags: the drama server's named switches for one harness change, sent on the draft.

The server ships a harness change (prompt text, a craft rule, a pipeline step)
behind a named flag (fictora-drama ``docs/runbooks/harness-flags.md``). The
operator authors the same story with the flag off and on, films both, and
compares them blind. The server resolves a story's flags once, when the story
is drafted, and stores them on the spine (``GET /v1/spines/{id}`` returns
``harness_flags``), so the kit sends them only on episode 1's draft.

``start --harness-flag NAME=on|off`` keeps the choice in the desk's
``production.config.json``. The draft body carries it, so its brief hash and
idempotency key do too: a resumed ``step`` resends the same request, and the
two arms of a pair are two different drafts. A desk without a flag sends no
``harness_flags`` key, so its body and key are what they were before.

Only an operator session can send flags (``403 harness_flags_operator_only``),
and an unregistered name is refused with 422. Either stops the draft with
nothing admitted or charged. A deploy whose ``/openapi.json`` lists no
``harness_flags`` on the draft request is told plainly before anything is sent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from creation.opening_action import DRAFT_SCHEMA, schema_takes_field

#: A flag name as the server registers it (snake_case).
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STATES = {"on": True, "off": False}

HarnessFlags = dict[str, bool]


class HarnessFlagsRefused(ValueError):
    """Harness flags the kit will not send, or the server would not take; nothing was drafted or charged."""


def parse_harness_flags(values: Sequence[str] | None) -> HarnessFlags | None:
    """Return ``--harness-flag NAME=on|off`` values as ``{name: bool}``, sorted by name.

    Parameters
    ----------
    values
        Each ``--harness-flag`` as typed.

    Returns
    -------
    HarnessFlags | None
        ``None`` when no flag was given.

    Raises
    ------
    HarnessFlagsRefused
        A value is not ``NAME=on`` or ``NAME=off``, or names one flag both ways.
    """

    flags: HarnessFlags = {}
    for raw in values or ():
        name, sep, state = raw.strip().partition("=")
        name, state = name.strip(), state.strip().lower()
        if not sep or not _NAME.match(name) or state not in _STATES:
            raise HarnessFlagsRefused(
                f"--harness-flag {raw!r}: use NAME=on or NAME=off with a snake_case flag name "
                "(list them in fictora-drama: uv run python scripts/harness_flags.py)"
            )
        if flags.get(name, _STATES[state]) != _STATES[state]:
            raise HarnessFlagsRefused(
                f"--harness-flag {name} is given both on and off; give it once"
            )
        flags[name] = _STATES[state]
    return dict(sorted(flags.items())) or None


def harness_flags_text(flags: Mapping[str, bool]) -> str:
    """Return ``a=on, b=off`` for the flags, sorted by name."""

    return ", ".join(
        f"{name}={'on' if on else 'off'}" for name, on in sorted(flags.items())
    )


def require_harness_flags_support(run: Any) -> None:
    """Stop before sending harness flags to a deploy whose draft request does not take them.

    Parameters
    ----------
    run
        Session with ``get_optional(path) -> (status, body)``.

    Raises
    ------
    HarnessFlagsRefused
        ``/openapi.json`` names the draft request and it has no ``harness_flags``.
        An unreadable document lets the draft go: the server's own 422 is then the answer.
    """

    status, doc = run.get_optional("/openapi.json")
    takes = (
        schema_takes_field(doc, DRAFT_SCHEMA, "harness_flags")
        if 200 <= status < 300
        else None
    )
    if takes is False:
        raise HarnessFlagsRefused(
            "[draft] this server does not take harness flags on the draft "
            f"({DRAFT_SCHEMA} has no harness_flags). Nothing was sent or charged. "
            "Run step again after the server update, or bind the desk without --harness-flag."
        )


def harness_flags_refusal(
    status: int, answer: Any, flags: Mapping[str, bool], server_text: str
) -> str | None:
    """Return the stop line for a draft the server refused because of its harness flags, else ``None``.

    Parameters
    ----------
    status
        The draft's HTTP status.
    answer
        The server's JSON answer.
    flags
        The flags the draft carried.
    server_text
        The server's own error text, quoted in the line.

    Returns
    -------
    str | None
        A line for a 403 or a 422 that names ``harness_flags``; ``None`` for any other answer.
    """

    said = repr(answer)
    if status not in (403, 422) or not (
        "harness_flags" in said or "harness flags" in said
    ):
        return None
    why = (
        "only an operator desk session may send harness flags"
        if status == 403
        else "the server does not know one of the names (list them in fictora-drama: "
        "uv run python scripts/harness_flags.py)"
    )
    return (
        f"Stopped: the server refused the harness flags ({harness_flags_text(flags)}): {why}. "
        f"Nothing was drafted or charged. Server: HTTP {status} {server_text}"
    )


def pair_arms(names: Sequence[str]) -> tuple[HarnessFlags, HarnessFlags]:
    """Return the base arm (every named flag off) and the head arm (every named flag on).

    Raises
    ------
    HarnessFlagsRefused
        No name, or a name that is not snake_case.
    """

    unique = sorted(set(names))
    if not unique:
        raise HarnessFlagsRefused("harness-pair needs at least one --flag NAME")
    for name in unique:
        if not _NAME.match(name):
            raise HarnessFlagsRefused(f"--flag {name!r} is not a snake_case flag name")
    return {name: False for name in unique}, {name: True for name in unique}


__all__ = [
    "HarnessFlags",
    "HarnessFlagsRefused",
    "harness_flags_refusal",
    "harness_flags_text",
    "pair_arms",
    "parse_harness_flags",
    "require_harness_flags_support",
]
