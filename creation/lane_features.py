"""What a story's video model cannot do, as the server states it (``spine.video_lane_unsupported``).

The server owns the rule (fictora-drama ``wan_sound_notes``): a story whose
model makes its own sound effects (Wan) takes no sound notes and no per-take
sound re-mix. The kit only reads the server's list off the spine it already
fetched; it never decides by lane id. A spine from a server without the field
reads as supporting everything, exactly as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: The server's name for sound notes in ``video_lane_unsupported``.
SOUND_NOTES = "sound_notes"

#: The plain stop for ``sound-note`` on a story that takes none.
SOUND_NOTES_OFF = (
    "this story's video model makes its own sound effects, so sound notes do not apply to it. "
    "Nothing was saved or charged"
)

#: What ``--sfx-adjust`` says on such a story (instead of pointing at ``sound-note``).
SFX_ADJUST_OFF_HOME = "this story's video model makes its own sound effects, so a sound's level cannot be changed here"


def sound_notes_supported(spine: Mapping[str, Any] | None) -> bool:
    """Whether the story takes sound notes (True when the server does not say otherwise).

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` JSON (or ``None``).

    Returns
    -------
    bool
        False only when the server lists ``sound_notes`` in ``video_lane_unsupported``.
    """

    if not spine:
        return True
    unsupported = spine.get("video_lane_unsupported") or []
    return SOUND_NOTES not in unsupported


def desk_sound_notes_supported(desk: Path, episode: int | None = None) -> bool:
    """:func:`sound_notes_supported` for the newest spine saved on a desk (True when none is saved).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal; the desk's current episode when ``None``.

    Returns
    -------
    bool
        Whether the desk's story takes sound notes.
    """

    from creation.post.desk import saved_spine
    from creation.production_state import load_production

    try:
        number = (
            episode if episode is not None else load_production(desk).episode_ordinal
        )
        found = saved_spine(desk, number)
    except (OSError, ValueError):
        return True
    return sound_notes_supported(found[0] if found else None)


__all__ = [
    "SFX_ADJUST_OFF_HOME",
    "SOUND_NOTES",
    "SOUND_NOTES_OFF",
    "desk_sound_notes_supported",
    "sound_notes_supported",
]
