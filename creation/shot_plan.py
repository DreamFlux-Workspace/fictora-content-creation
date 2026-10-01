"""A beat's shot plan: the creator's own shots for one beat (``beats[].shot_plan`` on the Drama API).

A plan is 1-4 shots, each ``{size, subject, camera?, angle?}`` in plain words
("close-up", "Hana's face", "slow dolly in", "high angle"). Shot 1 is the
beat's first board row. The server (fictora-drama #464) writes each shot's
``size`` and ``angle`` onto its row and hands ``subject`` and ``camera`` to the
frames author; a plan longer than the beat's rows is clamped and logged there,
never refused.

The kit checks the same shape the server does before it sends anything, so a
422 on a plan that passed here almost always means the server is older than
the field.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from creation.cli_text import TextArgError, json_or_file

#: Shots one beat's plan may name (``MAX_BEAT_SHOT_PLAN_SHOTS`` on the server).
MAX_SHOTS = 4
#: One shot phrase becomes a single-line board field of at most this many characters.
MAX_PHRASE_CHARS = 200
#: The fields of one shot, in the order ``--shot "size|subject|camera|angle"`` takes them.
SHOT_FIELDS = ("size", "subject", "camera", "angle")
_REQUIRED = ("size", "subject")

#: Said when the server answers 422 to a plan this module already accepted.
OLDER_SERVER_HINT = (
    "HTTP 422 on a plan the kit already checked means this Drama API is older than beat shot plans "
    "(fictora-drama #464) and does not know `shot_plan`; nothing was changed. Ask for the deploy, or until "
    'then write the shots into the beat\'s motion_intent (`edit --beat N --intent "SHOT 1 - close-up, ..."`).'
)


class ShotPlanError(ValueError):
    """A shot plan the server would refuse; the message says which shot and why."""


def _phrase(value: Any, *, where: str, key: str) -> str:
    if not isinstance(value, str):
        raise ShotPlanError(f"{where}: {key} must be words, got {type(value).__name__}")
    text = value.strip()
    if not text:
        raise ShotPlanError(f"{where}: {key} is empty")
    if "\n" in text or "\r" in text:
        raise ShotPlanError(f"{where}: {key} must be one line")
    if len(text) > MAX_PHRASE_CHARS:
        raise ShotPlanError(
            f"{where}: {key} is {len(text)} characters; the most is {MAX_PHRASE_CHARS}"
        )
    return text


def normalize_plan(shots: Any) -> list[dict[str, str]]:
    """Check a plan and return it in the shape the API takes (optional fields left out when unset).

    Parameters
    ----------
    shots
        A list of shot objects (``{"size", "subject", "camera"?, "angle"?}``).

    Returns
    -------
    list[dict[str, str]]
        The shots, trimmed, in order.

    Raises
    ------
    ShotPlanError
        Not a list of 1-4 shots, an unknown field, or a missing, empty, multi-line or too long phrase.
    """

    if shots is None:
        raise ShotPlanError(
            "a shot plan of null clears nothing here: to remove the plan use --clear-shot-plan"
        )
    if not isinstance(shots, list):
        raise ShotPlanError(
            f"a shot plan is a list of shots, got {type(shots).__name__}"
        )
    if not 1 <= len(shots) <= MAX_SHOTS:
        raise ShotPlanError(
            f"a shot plan holds 1-{MAX_SHOTS} shots (a row board draws at most {MAX_SHOTS} rows), got {len(shots)}; "
            "to remove the plan use --clear-shot-plan"
        )
    plan: list[dict[str, str]] = []
    for number, shot in enumerate(shots, start=1):
        where = f"shot {number}"
        if not isinstance(shot, Mapping):
            raise ShotPlanError(
                f"{where} must be an object with size and subject, got {type(shot).__name__}"
            )
        unknown = sorted(set(shot) - set(SHOT_FIELDS))
        if unknown:
            raise ShotPlanError(
                f"{where}: unknown field(s) {', '.join(unknown)}; a shot has {', '.join(SHOT_FIELDS)}"
            )
        entry: dict[str, str] = {}
        for key in SHOT_FIELDS:
            value = shot.get(key)
            if value is None:
                if key in _REQUIRED:
                    raise ShotPlanError(f"{where}: {key} is required")
                continue
            entry[key] = _phrase(value, where=where, key=key)
        plan.append(entry)
    return plan


def plan_from_json(value: str) -> list[dict[str, str]]:
    """Read ``--shot-plan``: JSON inline, ``@file`` or an existing file path.

    The JSON is the list of shots, or an object holding it under ``shot_plan``.

    Parameters
    ----------
    value
        What the operator passed.

    Returns
    -------
    list[dict[str, str]]
        The checked plan.

    Raises
    ------
    ShotPlanError
        The file cannot be read, the text is not JSON, or the plan is not valid.
    """

    try:
        data = json_or_file(value, flag="--shot-plan")
    except TextArgError as exc:
        raise ShotPlanError(str(exc)) from None
    if isinstance(data, Mapping) and "shot_plan" in data:
        data = data["shot_plan"]
    return normalize_plan(data)


def plan_from_shots(values: Sequence[str]) -> list[dict[str, str]]:
    """Read repeated ``--shot "size|subject|camera|angle"`` flags into a plan.

    ``camera`` and ``angle`` may be left out or left empty (``"wide|the shop||high angle"``).

    Parameters
    ----------
    values
        One string per ``--shot``, in order.

    Returns
    -------
    list[dict[str, str]]
        The checked plan.

    Raises
    ------
    ShotPlanError
        A shot with fewer than two or more than four parts, or a plan that is not valid.
    """

    shots: list[dict[str, str]] = []
    for number, raw in enumerate(values, start=1):
        parts = [part.strip() for part in raw.split("|")]
        if not 2 <= len(parts) <= len(SHOT_FIELDS):
            raise ShotPlanError(
                f'--shot {number} {raw!r}: write it as "size|subject|camera|angle" '
                "(camera and angle optional, separated by |)"
            )
        shots.append(
            {
                key: part
                for key, part in zip(SHOT_FIELDS, parts, strict=False)
                if part or key in _REQUIRED
            }
        )
    return normalize_plan(shots)


def describe_shot(shot: Mapping[str, Any]) -> str:
    """One shot in words: ``close-up on Hana's face, slow dolly in, high angle``.

    Parameters
    ----------
    shot
        One shot of a plan.

    Returns
    -------
    str
        Printable phrase.
    """

    text = f"{shot.get('size')} on {shot.get('subject')}"
    extras = [str(shot[key]) for key in ("camera", "angle") if shot.get(key)]
    return ", ".join([text, *extras])


def _canonical(plan: Any) -> list[tuple[tuple[str, str], ...]]:
    shots: list[tuple[tuple[str, str], ...]] = []
    for shot in (
        plan if isinstance(plan, Sequence) and not isinstance(plan, str) else ()
    ):
        if not isinstance(shot, Mapping):
            continue
        fields = []
        for key in SHOT_FIELDS:
            value = shot.get(key)
            if isinstance(value, str) and value.strip():
                fields.append((key, " ".join(value.split()).casefold()))
        if fields:
            shots.append(tuple(fields))
    return shots


def same_plan(held: Any, sent: Any) -> bool:
    """Whether the server holds the plan that was sent, compared normalised.

    Key order, unset or empty optional fields (``None``, ``""``, missing), extra
    keys the server adds, spacing and letter case do not count; a missing,
    added or reworded shot does. ``None`` and ``[]`` are both no plan.

    Parameters
    ----------
    held
        ``beats[].shot_plan`` as the server returned it.
    sent
        The plan the kit sent (``None`` when clearing it).

    Returns
    -------
    bool
        ``True`` when they name the same shots.
    """

    return _canonical(held) == _canonical(sent)


def plan_lines(plan: Any, *, indent: str = "    ") -> list[str]:
    """A beat's plan as printable lines, one per shot; nothing when the beat has no plan.

    Parameters
    ----------
    plan
        ``beat["shot_plan"]`` (``None`` or a list of shots).
    indent
        Leading spaces.

    Returns
    -------
    list[str]
        ``plan shot 1: close-up on Hana's face, handheld`` lines.
    """

    if not isinstance(plan, list):
        return []
    return [
        f"{indent}plan shot {number}: {describe_shot(shot)}"
        for number, shot in enumerate(plan, start=1)
        if isinstance(shot, Mapping)
    ]


__all__ = [
    "MAX_SHOTS",
    "OLDER_SERVER_HINT",
    "SHOT_FIELDS",
    "ShotPlanError",
    "describe_shot",
    "normalize_plan",
    "plan_from_json",
    "plan_from_shots",
    "plan_lines",
]
