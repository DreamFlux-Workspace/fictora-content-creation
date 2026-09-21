"""Normalize creator premises before Drama plan / season-bible authoring."""

from __future__ import annotations

# Marker so re-bind and idempotent re-step do not stack the directive.
_CAST_FLOOR_MARKER = "[fictora:season-bible-cast-min=2]"

_CAST_FLOOR_SUFFIX = (
    f"\n\n{_CAST_FLOOR_MARKER} "
    "The season bible cast array must include at least two distinct named characters, "
    "each with full visual_brief and voice_brief. "
    "Episode 1 may show only one person on screen; the second may be voice-only, "
    "off-screen, or introduced later."
)

_SCENE_PROMPT_MAX_LEN = 20_000


def ensure_plan_prompt(prompt: str) -> str:
    """Append the season-bible cast floor when the creator premise omits it.

    The hosted Drama API rejects season bibles with fewer than two cast entries.
    Solo-on-screen premises still need a second roster member in the bible.

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
    if _CAST_FLOOR_MARKER in text:
        return text
    combined = text + _CAST_FLOOR_SUFFIX
    if len(combined) > _SCENE_PROMPT_MAX_LEN:
        msg = (
            f"prompt length {len(text)} leaves no room for cast floor directive "
            f"(max {_SCENE_PROMPT_MAX_LEN})"
        )
        raise ValueError(msg)
    return combined
