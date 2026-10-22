"""The creator's premise as the draft sends it, and the brief's narrator lines.

Until 21 Oct 2026 the kit appended its own cast directive to every premise
(at least two cast members, or one; never an invented narrator; a voice heard
and never seen). Decision D6 of the kit/server reconcile: the server owns the
writer's wording and its own cast floor (fictora-drama #582 and the season
bible prompt), so the premise now goes exactly as the creator wrote it; a
directive an older run stored on the desk is taken off before it is sent
(:func:`plan_prompt`).

Also reads a brief for narrator and voice-over lines (the draft warns about
them; ``brief --strip-narration`` takes them out).
"""

from __future__ import annotations

import re
from pathlib import Path

from creation.narrator_cast import named_like_narrator

# The directives this kit appended until 21 Oct 2026, kept only to take them off a stored brief.
_CAST_FLOOR_MARKER = "[fictora:season-bible-cast-min=2]"
_SOLO_MARKER = "[fictora:season-bible-cast-min=1]"
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


def _without_kit_directive(text: str) -> str:
    """The premise without a cast directive this kit appended at its end (any version)."""

    for suffix in (_OLD_CAST_FLOOR_SUFFIX, _CAST_FLOOR_SUFFIX, _SOLO_SUFFIX):
        if text.endswith(suffix.strip()):
            return text[: -len(suffix.strip())].rstrip()
    return text


def plan_prompt(prompt: str) -> str:
    """The premise the draft sends: the creator's words, with any directive this kit once appended taken off.

    Parameters
    ----------
    prompt
        Creator premise from ``fictora-produce start`` or ``bind`` (or as a desk stored it).

    Returns
    -------
    str
        The premise, stripped. The server writes the cast to its own floor and rules.
    """

    return _without_kit_directive(prompt.strip())


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
