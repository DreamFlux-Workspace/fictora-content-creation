"""Normalize creator premises before Drama plan / season-bible authoring.

Also reads a brief for narrator and voice-over lines (the draft warns about
them; ``brief --strip-narration`` takes them out) and asks the server how few
cast members a story may have.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from creation.narrator_cast import named_like_narrator

# Marker so re-bind and idempotent re-step do not stack the directive.
_CAST_FLOOR_MARKER = "[fictora:season-bible-cast-min=2]"
#: The directive for a server whose season bible holds one person (fictora-drama #582).
_SOLO_MARKER = "[fictora:season-bible-cast-min=1]"

#: The directive this kit appended until 2026-10-01. It asked every
#: member for a full visual_brief, so the second member the minimum forced into a
#: one-creature show came out as a narrator with a face (Sighted ep 1,
#: 2026-10-01, L-20261001-2 / -4); his plate was later drawn inside the film job.
#: Kept only so an undrafted desk that still carries it gets the new directive.
_OLD_CAST_FLOOR_SUFFIX = (
    f"\n\n{_CAST_FLOOR_MARKER} "
    "The season bible cast array must include at least two distinct named characters, "
    "each with full visual_brief and voice_brief. "
    "Episode 1 may show only one person on screen; the second may be voice-only, "
    "off-screen, or introduced later."
)

_CAST_FLOOR_SUFFIX = (
    f"\n\n{_CAST_FLOOR_MARKER} "
    "The season bible cast array must include at least two distinct named characters, "
    "and each must be someone the story actually needs. "
    "Episode 1 may show only one person on screen. When the story has only one person to show, "
    "the second is either a voice heard and never seen (a caller, an intercom, a radio, "
    "a voice through a wall), with every line marked off_screen, kept out of every frame, "
    'and a visual_brief whose anchors read "never shown on screen; heard only as a voice"; '
    "or a second drawn character the story actually needs, with a full visual_brief and voice_brief. "
    "Never invent a narrator, storyteller or voice-over character to reach two. "
    "If the premise itself asks for narration, the narrator is the voice heard and never seen above."
)

_SOLO_SUFFIX = (
    f"\n\n{_SOLO_MARKER} "
    "The season bible cast array holds exactly the people the story needs: one person is a whole cast. "
    "Never invent a narrator, storyteller or voice-over character, and never add someone to reach a number. "
    "A voice heard and never seen is a cast member only when it speaks lines: every line marked off_screen, "
    'kept out of every frame, and a visual_brief whose anchors read "never shown on screen; heard only as a voice". '
    "A creature, crowd or passer-by the story only shows is not a cast member."
)

_SCENE_PROMPT_MAX_LEN = 20_000

#: What a server before one-person shows requires (its capabilities name no ``cast_floor``).
OLDER_SERVER_CAST_FLOOR = 2
CAPABILITIES_ROUTE = "/v1/capabilities"


def _without_kit_directive(text: str) -> str:
    """The premise without a cast directive this kit appended at its end (any version)."""

    for suffix in (_OLD_CAST_FLOOR_SUFFIX, _CAST_FLOOR_SUFFIX, _SOLO_SUFFIX):
        if text.endswith(suffix.strip()):
            return text[: -len(suffix.strip())].rstrip()
    return text


def ensure_plan_prompt(
    prompt: str, *, cast_floor: int = OLDER_SERVER_CAST_FLOOR
) -> str:
    """Append the season-bible cast directive the server needs, once.

    A server before one-person shows (fictora-drama
    ``DramaAuthoringSeasonBibleDraft.cast``, ``min_length=2``) rejects a season
    bible with one cast entry, so a solo premise still needs a second roster
    member: a voice heard and never seen, or a second drawn character the story
    needs, never a narrator invented to reach two. A server that reports
    ``cast_floor`` 1 (#582) gets the one-person directive instead: exactly the
    people the story needs. A prompt carrying another directive this kit wrote
    (the older two-member one that asked every member for a full
    ``visual_brief``, or the one for the other floor) gets the right one in its
    place; this runs only before the draft, so no drafted spine's prompt changes.
    A marker the operator wrote mid-text is left alone.

    Parameters
    ----------
    prompt
        Creator premise from ``fictora-produce start`` or ``bind``.
    cast_floor
        The server's ``cast_floor`` (:func:`server_cast_floor`); 2 when unknown.

    Returns
    -------
    str
        Prompt safe to send as ``scene_prompt`` on draft enrol.

    Raises
    ------
    ValueError
        When appending the directive would exceed the API scene_prompt limit.
    """

    text = _without_kit_directive(prompt.strip())
    if _CAST_FLOOR_MARKER in text or _SOLO_MARKER in text:
        return text
    combined = text + (_SOLO_SUFFIX if cast_floor <= 1 else _CAST_FLOOR_SUFFIX)
    if len(combined) > _SCENE_PROMPT_MAX_LEN:
        msg = (
            f"prompt length {len(text)} leaves no room for cast floor directive "
            f"(max {_SCENE_PROMPT_MAX_LEN})"
        )
        raise ValueError(msg)
    return combined


def server_cast_floor(run: Any) -> int:
    """The fewest cast members the server's season bible accepts.

    Parameters
    ----------
    run
        The desk's API session.

    Returns
    -------
    int
        ``cast_floor`` from ``GET /v1/capabilities``; 2 when the server does
        not say (a deploy before one-person shows) or cannot be asked.
    """

    status, body = run.get_optional(CAPABILITIES_ROUTE)
    if status != 200 or not isinstance(body, Mapping):
        return OLDER_SERVER_CAST_FLOOR
    floor = body.get("cast_floor")
    return floor if isinstance(floor, int) and floor >= 1 else OLDER_SERVER_CAST_FLOOR


#: ``Speaker (V.O.): line`` / ``- **Narrator:** line`` / ``> VO: line``.
_LABEL = re.compile(r"^\s*(?:[-*>•]\s*)?\**(?P<label>[^:|\n]{1,60}?)\**\s*:\s*\S")


# --- Narrator lines in a brief -----------------------------------------------------------------


def _row_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return []
    return [cell.strip().strip("*").strip() for cell in stripped.strip("|").split("|")]


def is_narrator_line(line: str) -> bool:
    """Whether one brief line is spoken by a narrator or a voice-over.

    A label before a colon (``Narrator: …``, ``KENJI (V.O.): …``) or a table
    row whose short cell names a narrator. Prose that only mentions narration
    ("no narrator in this one") is not a line.

    Parameters
    ----------
    line
        One line of the brief.

    Returns
    -------
    bool
        True for a narrator or voice-over line.
    """

    cells = _row_cells(line)
    if cells:
        if all(set(cell) <= set("-: ") for cell in cells):
            return False
        return any(len(cell) <= 40 and named_like_narrator(cell) for cell in cells)
    match = _LABEL.match(line)
    return bool(match and named_like_narrator(match.group("label")))


def narrator_lines(text: str) -> list[str]:
    """The brief's narrator and voice-over lines, in order.

    Parameters
    ----------
    text
        The brief.

    Returns
    -------
    list[str]
        Each line, stripped.
    """

    return [line.strip() for line in text.splitlines() if is_narrator_line(line)]


def strip_narration(text: str) -> str:
    """The brief without its narrator and voice-over lines; everything else exactly as written.

    Parameters
    ----------
    text
        The brief.

    Returns
    -------
    str
        The brief with those lines removed (and no run of blank lines left behind).
    """

    kept = [line for line in text.splitlines() if not is_narrator_line(line)]
    joined = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def narrator_warning(text: str, *, desk: Path | str | None = None) -> str | None:
    """The warning printed before the draft when the brief has narrator lines; ``None`` when it has none.

    Parameters
    ----------
    text
        The brief the draft will send.
    desk
        The desk, for the command the warning names.

    Returns
    -------
    str | None
        A multi-line warning, or ``None``.
    """

    found = narrator_lines(text)
    if not found:
        return None
    where = desk if desk is not None else "D"
    shown = "\n".join(f"     {line[:120]}" for line in found[:5])
    more = f"\n     (+{len(found) - 5} more)" if len(found) > 5 else ""
    return (
        f"!! the brief has {len(found)} narrator / voice-over line(s):\n{shown}{more}\n"
        "   Runbook: voice-over is laid dry in post (`finish --voice`), never written into the take. "
        "Drafted, these become a heard-only character's lines (you are asked once: heard only, never seen?). "
        f"To keep them out of the story, run `fictora-produce brief --desk {where} --strip-narration` "
        "before the first `step` (it edits the desk's brief; nothing is sent); after the draft the same "
        "command replaces the server's stored brief."
    )
