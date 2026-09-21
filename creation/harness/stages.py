"""Stage vocabulary for creator-flow harness runs."""

from __future__ import annotations

EP1_API_STAGES_VISUAL_FIRST: tuple[str, ...] = (
    "draft",
    "cast_enrol",
    "cast_terminal",
    "cast_approve",
    "script_approve",
    "boards_enrol",
    "boards_terminal",
    "boards_exposure",
    "boards_approve",
    "video_enrol",
    "video_terminal",
    "delivery",
)

LATER_EP_API_STAGES_SCRIPT_FIRST: tuple[str, ...] = (
    "pilot_author",
    "pilot_approve",
    "boards_enrol",
    "boards_terminal",
    "boards_approve",
)

BFF_STUB_STAGES_VISUAL_FIRST: tuple[str, ...] = (
    "Idea",
    "Cast/world",
    "Style picker",
    "Cast draw",
    "Portrait approval",
    "Script",
    "Storyboard",
    "Video",
)
