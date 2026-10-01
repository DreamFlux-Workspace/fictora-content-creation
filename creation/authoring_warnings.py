"""The server's authoring warnings, said in plain words as nudges. They never stop anything.

Founder decision, 1 Oct 2026: nudge, never block the creator (fictora-drama
#538). The server measures what it can (a line over 10 words, a take over
about 22 spoken words, a first line over 10 words) and answers
``DramaAuthoringWarning`` dicts::

    {schema_version: "fictora.drama-authoring-warning.v1", path, code, message,
     words?, target?, episode_id?, beat_id?, line_id?, take?}

They ride on every spine response (``authoring_warnings``, built fresh, never
stored), on an authoring job's ``result.authoring_warnings`` (the draft's
``plan_author``, ``author``'s ``pilot_episode_extend``), and on cascade preview /
execute (only what the edit introduces; the messages are also appended to the
old ``warnings`` strings). The field is left out when empty and on an older
server, so nothing is printed then.

The kit prints them, groups them per episode and take, and adds them to the run
notes. It never exits non-zero for one, never refuses, never shortens a line.
A code it does not know is shown by its ``message``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

#: The field name on spine responses, job results and cascade answers.
FIELD = "authoring_warnings"

#: The header every block opens with: what the notes are and that nothing was held.
HEADER = "authoring notes (nudges: nothing was blocked or changed)"

_EPISODE_IN_PATH = re.compile(r"episodes\[([^\]]+)\]")


def authoring_warnings(payload: Any) -> list[dict[str, Any]]:
    """The warnings a server answer carries: its own ``authoring_warnings`` or its job ``result``'s.

    Parameters
    ----------
    payload
        A spine response, a terminal job (``GET /v1/jobs/{id}``), or a cascade answer. Anything else reads as none.

    Returns
    -------
    list[dict[str, Any]]
        The warning dicts with a ``code`` or a ``message``; empty when the field is absent (older server, or none).
    """

    if not isinstance(payload, Mapping):
        return []
    found = payload.get(FIELD)
    if found is None and isinstance(payload.get("result"), Mapping):
        found = payload["result"].get(FIELD)
    if not isinstance(found, list):
        return []
    return [
        dict(item)
        for item in found
        if isinstance(item, Mapping) and (item.get("code") or item.get("message"))
    ]


def _key(warning: Mapping[str, Any]) -> tuple[str, str, Any]:
    return (
        str(warning.get("path") or ""),
        str(warning.get("code") or ""),
        warning.get("words"),
    )


def introduced(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The warnings in ``after`` that ``before`` did not have (path, code and word count compared, as the server does).

    Parameters
    ----------
    before, after
        Warning lists from the spine before and after an edit.

    Returns
    -------
    list[dict[str, Any]]
        The new ones, in ``after``'s order.
    """

    seen = {_key(w) for w in before}
    return [dict(w) for w in after if _key(w) not in seen]


def warning_episode_id(
    warning: Mapping[str, Any], spine: Mapping[str, Any] | None = None
) -> str:
    """The episode a warning is about: its ``episode_id``, its path's ``episodes[...]``, or its beat's episode.

    Parameters
    ----------
    warning
        One warning dict.
    spine
        The spine, to find a beat's episode (optional).

    Returns
    -------
    str
        The episode id, or empty when nothing says.
    """

    if warning.get("episode_id"):
        return str(warning["episode_id"])
    match = _EPISODE_IN_PATH.search(str(warning.get("path") or ""))
    if match:
        return match.group(1)
    beat = _beat(spine, warning.get("beat_id"))
    return str(beat.get("episode_id") or "") if beat else ""


def for_episode(
    warnings: Iterable[Mapping[str, Any]],
    episode_id: str,
    spine: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The warnings about one episode, plus any that name no episode.

    Parameters
    ----------
    warnings
        Warning dicts.
    episode_id
        The episode's API id.
    spine
        The spine, to place beat warnings (optional).

    Returns
    -------
    list[dict[str, Any]]
        The matching warnings, in order.
    """

    return [
        dict(w) for w in warnings if warning_episode_id(w, spine) in ("", episode_id)
    ]


def _beat(spine: Mapping[str, Any] | None, beat_id: Any) -> Mapping[str, Any] | None:
    if not spine or not beat_id:
        return None
    return next(
        (
            beat
            for beat in spine.get("beats") or []
            if isinstance(beat, Mapping) and beat.get("beat_id") == beat_id
        ),
        None,
    )


def _episode_label(episode_id: str, spine: Mapping[str, Any] | None) -> str:
    if not episode_id:
        return "the story"
    for number, summary in enumerate(
        (spine or {}).get("episode_summaries") or [], start=1
    ):
        if isinstance(summary, Mapping) and summary.get("episode_id") == episode_id:
            ordinal = summary.get("ordinal")
            return f"ep{int(ordinal) if isinstance(ordinal, int) else number:02d}"
    return episode_id


def _counted(words: Any, target: Any) -> str:
    if isinstance(words, int) and isinstance(target, int):
        return f"{words} words ({target} recommended)"
    if isinstance(words, int):
        return f"{words} words"
    return ""


def warning_line(
    warning: Mapping[str, Any], spine: Mapping[str, Any] | None = None
) -> str:
    """One warning in plain words, e.g. ``note: beat 3 line runs 14 words (10 recommended) — <message>``.

    Parameters
    ----------
    warning
        One warning dict.
    spine
        The spine, to name a beat by its number (optional; the beat id otherwise).

    Returns
    -------
    str
        The line. An unknown code, or one without a word count, is ``note: <message>``.
    """

    code = str(warning.get("code") or "")
    message = " ".join(str(warning.get("message") or code).split())
    counted = _counted(warning.get("words"), warning.get("target"))
    what = ""
    if counted and code == "line_long":
        beat = _beat(spine, warning.get("beat_id"))
        where = (
            f"beat {beat.get('ordinal')}"
            if beat and beat.get("ordinal") is not None
            else (f"beat {warning['beat_id']}" if warning.get("beat_id") else "a")
        )
        what = f"{where} line runs {counted}"
    elif counted and code == "take_words_over_target":
        take = warning.get("take")
        what = (
            f"take {take} speaks {counted}"
            if take is not None
            else f"a take speaks {counted}"
        )
    elif counted and code == "first_line_long":
        what = f"the first line runs {counted}"
    return f"note: {what} — {message}" if what else f"note: {message}"


def warning_lines(
    warnings: Sequence[Mapping[str, Any]], spine: Mapping[str, Any] | None = None
) -> list[str]:
    """The warnings grouped per episode, then per take, under :data:`HEADER`. Empty when there are none.

    Parameters
    ----------
    warnings
        Warning dicts.
    spine
        The spine, to name episodes and beats by number (optional).

    Returns
    -------
    list[str]
        Printable lines (indented under one header per episode).
    """

    if not warnings:
        return []
    groups: dict[str, list[tuple[int, int, Mapping[str, Any]]]] = {}
    for index, warning in enumerate(warnings):
        take = warning.get("take")
        groups.setdefault(warning_episode_id(warning, spine), []).append(
            (take if isinstance(take, int) else 0, index, warning)
        )
    lines: list[str] = []
    for episode_id, rows in groups.items():
        lines.append(f"{_episode_label(episode_id, spine)} {HEADER}:")
        for take, _, warning in sorted(rows, key=lambda row: (row[0], row[1])):
            named = warning.get("code") == "take_words_over_target"
            prefix = f"t{take} " if take and not named else ""
            lines.append(f"  {prefix}{warning_line(warning, spine)}")
    return lines


def say_warnings(
    warnings: Sequence[Mapping[str, Any]],
    *,
    spine: Mapping[str, Any] | None = None,
    out: Any,
) -> list[str]:
    """Print the warnings (:func:`warning_lines`) and return the lines, for the run notes. Never raises on content.

    Parameters
    ----------
    warnings
        Warning dicts (empty prints nothing).
    spine
        The spine, to name episodes and beats by number (optional).
    out
        Text stream.

    Returns
    -------
    list[str]
        What was printed.
    """

    lines = warning_lines(warnings, spine)
    for line in lines:
        print(line, file=out)
    return lines


__all__ = [
    "FIELD",
    "HEADER",
    "authoring_warnings",
    "for_episode",
    "introduced",
    "say_warnings",
    "warning_episode_id",
    "warning_line",
    "warning_lines",
]
