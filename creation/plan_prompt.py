"""Normalize creator premises before Drama plan / season-bible authoring."""

from __future__ import annotations

# Marker so re-bind and idempotent re-step do not stack the directive.
_CAST_FLOOR_MARKER = "[fictora:season-bible-cast-min=2]"

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

_SCENE_PROMPT_MAX_LEN = 20_000


def ensure_plan_prompt(prompt: str) -> str:
    """Append the season-bible cast floor when the creator premise omits it.

    The hosted Drama API rejects season bibles with fewer than two cast entries
    (fictora-drama ``DramaAuthoringSeasonBibleDraft.cast``, ``min_length=2``).
    Solo-on-screen premises still need a second roster member in the bible. That
    member is a voice heard and never seen, or a second drawn character the
    story needs; the directive never asks for a drawn narrator invented to
    reach two. A prompt that still carries the older directive (which asked
    every member for a full ``visual_brief``) gets the new one in its place; this
    runs only before the draft, so no drafted spine's prompt changes.

    Parameters
    ----------
    prompt
        Creator premise from ``fictora-produce start`` or ``bind``.

    Returns
    -------
    str
        Prompt safe to send as ``scene_prompt`` on draft enrol.

    Raises
    ------
    ValueError
        When appending the directive would exceed the API scene_prompt limit.
    """

    text = prompt.strip()
    if text.endswith(_OLD_CAST_FLOOR_SUFFIX.strip()):
        text = text[: -len(_OLD_CAST_FLOOR_SUFFIX.strip())].rstrip()
    elif _CAST_FLOOR_MARKER in text:
        return text
    combined = text + _CAST_FLOOR_SUFFIX
    if len(combined) > _SCENE_PROMPT_MAX_LEN:
        msg = (
            f"prompt length {len(text)} leaves no room for cast floor directive "
            f"(max {_SCENE_PROMPT_MAX_LEN})"
        )
        raise ValueError(msg)
    return combined
