"""A beat's shot plan: the creator's own shots for one beat (``beats[].shot_plan`` on the Drama API).

A plan is 1-4 shots, each ``{size, subject, camera?, angle?, seconds?}``: plain
words ("close-up", "Hana's face", "slow dolly in", "high angle") and, for a
reveal or a punchline that needs its own length, how many seconds it plays
(0.5-15; fictora-drama "shot seconds", which stacks on #765). The take gives
that shot its seconds and shares the rest by tempo. Shot 1 is the beat's first
board row. The server (fictora-drama #464) writes each shot's
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
#: The words of one shot, in the order ``--shot "size|subject|camera|angle|seconds"`` takes them.
SHOT_FIELDS = ("size", "subject", "camera", "angle")
#: How long the shot plays, the fifth ``--shot`` part ("3", "3s", "2.5 s"). Optional.
SECONDS_FIELD = "seconds"
#: Planned seconds one shot may ask for (``MIN_``/``MAX_PLANNED_SHOT_SECONDS`` on the server).
MIN_SECONDS = 0.5
MAX_SECONDS = 15.0
_REQUIRED = ("size", "subject")

#: Said when the server answers 422 to a plan this module already accepted.
OLDER_SERVER_HINT = (
    "HTTP 422 on a plan the kit already checked means this Drama API is older than beat shot plans "
    "(fictora-drama #464) and does not know `shot_plan`, or older than shot seconds and does not know a "
    "shot's `seconds`; nothing was changed. Ask for the deploy, drop the seconds, or until then write the "
    'shots into the beat\'s motion_intent (`edit --beat N --intent "SHOT 1 - close-up, ..."`).'
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


def _seconds(value: Any, *, where: str) -> float:
    if isinstance(value, bool):
        raise ShotPlanError(f"{where}: seconds must be a number, got {value!r}")
    if isinstance(value, str):
        text = value.strip().lower().removesuffix("seconds").removesuffix("sec").removesuffix("s").strip()
        try:
            value = float(text)
        except ValueError:
            raise ShotPlanError(f"{where}: seconds must be a number like 3 or 2.5, got {value!r}") from None
    if not isinstance(value, (int, float)):
        raise ShotPlanError(f"{where}: seconds must be a number, got {type(value).__name__}")
    seconds = float(value)
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise ShotPlanError(
            f"{where}: seconds is {seconds:g}; a shot plays {MIN_SECONDS:g}-{MAX_SECONDS:g} seconds"
        )
    return seconds


def normalize_plan(shots: Any) -> list[dict[str, Any]]:
    """Check a plan and return it in the shape the API takes (optional fields left out when unset).

    Parameters
    ----------
    shots
        A list of shot objects (``{"size", "subject", "camera"?, "angle"?, "seconds"?}``).

    Returns
    -------
    list[dict[str, Any]]
        The shots, trimmed, in order; ``seconds`` a number when given.

    Raises
    ------
    ShotPlanError
        Not a list of 1-4 shots, an unknown field, a missing, empty, multi-line or too long phrase, or
        seconds that are not a number of 0.5-15.
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
    plan: list[dict[str, Any]] = []
    for number, shot in enumerate(shots, start=1):
        where = f"shot {number}"
        if not isinstance(shot, Mapping):
            raise ShotPlanError(
                f"{where} must be an object with size and subject, got {type(shot).__name__}"
            )
        unknown = sorted(set(shot) - {*SHOT_FIELDS, SECONDS_FIELD})
        if unknown:
            raise ShotPlanError(
                f"{where}: unknown field(s) {', '.join(unknown)}; a shot has "
                f"{', '.join((*SHOT_FIELDS, SECONDS_FIELD))}"
            )
        entry: dict[str, Any] = {}
        for key in SHOT_FIELDS:
            value = shot.get(key)
            if value is None:
                if key in _REQUIRED:
                    raise ShotPlanError(f"{where}: {key} is required")
                continue
            entry[key] = _phrase(value, where=where, key=key)
        if shot.get(SECONDS_FIELD) is not None:
            entry[SECONDS_FIELD] = _seconds(shot[SECONDS_FIELD], where=where)
        plan.append(entry)
    return plan


def plan_from_json(value: str) -> list[dict[str, Any]]:
    """Read ``--shot-plan``: JSON inline, ``@file`` or an existing file path.

    The JSON is the list of shots, or an object holding it under ``shot_plan``.

    Parameters
    ----------
    value
        What the operator passed.

    Returns
    -------
    list[dict[str, Any]]
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


def plan_from_shots(values: Sequence[str]) -> list[dict[str, Any]]:
    """Read repeated ``--shot "size|subject|camera|angle|seconds"`` flags into a plan.

    ``camera``, ``angle`` and ``seconds`` may be left out or left empty
    (``"wide|the shop||high angle"``, ``"close-up|her face|slow push in||3"``).

    Parameters
    ----------
    values
        One string per ``--shot``, in order.

    Returns
    -------
    list[dict[str, Any]]
        The checked plan.

    Raises
    ------
    ShotPlanError
        A shot with fewer than two or more than five parts, or a plan that is not valid.
    """

    shots: list[dict[str, Any]] = []
    keys = (*SHOT_FIELDS, SECONDS_FIELD)
    for number, raw in enumerate(values, start=1):
        parts = [part.strip() for part in raw.split("|")]
        if not 2 <= len(parts) <= len(keys):
            raise ShotPlanError(
                f'--shot {number} {raw!r}: write it as "size|subject|camera|angle|seconds" '
                "(camera, angle and seconds optional, separated by |)"
            )
        shots.append(
            {
                key: part
                for key, part in zip(keys, parts, strict=False)
                if part or key in _REQUIRED
            }
        )
    return normalize_plan(shots)


def describe_shot(shot: Mapping[str, Any]) -> str:
    """One shot in words: ``close-up on Hana's face, slow dolly in, high angle, plays 3 s``.

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
    seconds = shot.get(SECONDS_FIELD)
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        extras.append(f"plays {seconds:g} s")
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
        seconds = shot.get(SECONDS_FIELD)
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            fields.append((SECONDS_FIELD, f"{float(seconds):g}"))
        if fields:
            shots.append(tuple(fields))
    return shots


def same_plan(held: Any, sent: Any) -> bool:
    """Whether the server holds the plan that was sent, compared normalised.

    Key order, unset or empty optional fields (``None``, ``""``, missing), extra
    keys the server adds, spacing and letter case do not count; a missing,
    added or reworded shot does, and so do dropped or changed seconds (an older
    server that does not keep them). ``None`` and ``[]`` are both no plan.

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
    "MAX_SECONDS",
    "MAX_SHOTS",
    "MIN_SECONDS",
    "OLDER_SERVER_HINT",
    "SECONDS_FIELD",
    "SHOT_FIELDS",
    "ShotPlanError",
    "describe_shot",
    "normalize_plan",
    "plan_from_json",
    "plan_from_shots",
    "plan_lines",
]
