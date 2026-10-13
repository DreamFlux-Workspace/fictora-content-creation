"""A default place to stand for someone added to a shot by ``--set cast_refs`` (#19).

A frame names its people twice, ``cast_refs`` and the brief's
``subject_blocking``. ``edit --frame N --set cast_refs=[...]`` that *adds*
someone was refused by the server (``frame_cast_needs_staging``) until the
operator wrote their whole staging by hand. The kit now writes a default entry
for them and sends it with the whole brief, so the edit goes through:

* position: the side (left/right) their nearest other frame of the episode
  gives them, so a shot's cells agree (what the server's edge-duplicate
  prevention, ``keep_shot_sides``, would otherwise rewrite); else the side
  opposite the lead (the first person the frame already stages), or the free
  side; the centre when nobody else is in the shot;
* pose: still, hands in view;
* gaze and interaction: toward the lead, or the centre of the shot.

The same rule as fictora-drama's ``spine_frame_cast_edits.default_subject_blocking``
(which the app's director uses). The kit keeps its own copy because it never
imports server code (AGENTS.md boundary); keep the two in step.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

#: Pose for someone added with no staging of their own.
ADDED_CAST_POSE = "standing still with both hands visible and relaxed"

_SIDE = re.compile(
    r"\b(left|right|cent(?:er|re)|middle)\b"
    r"(?!\s+(?:of|hand|hands|arm|arms|shoulder|foot|feet|leg|legs|eye|eyes|cheek|ear|wrist|fist|side of))",
    re.IGNORECASE,
)


def frame_side(text: str | None) -> str | None:
    """The side of the frame a ``frame_position`` puts someone on.

    Parameters
    ----------
    text
        A ``frame_position``.

    Returns
    -------
    str | None
        ``"left"`` / ``"right"`` when exactly one of them is named,
        ``"center"`` when only the centre is, else ``None``.
    """

    words = {m.group(1).casefold() for m in _SIDE.finditer(text or "")}
    lateral = words & {"left", "right"}
    if len(lateral) == 1:
        return next(iter(lateral))
    if lateral:
        return None
    return "center" if words else None


def _nearest_own_side(
    spine: Mapping[str, Any], frame: Mapping[str, Any], cast_id: str
) -> str | None:
    ordinal = frame.get("ordinal") or 0
    candidates = sorted(
        (
            abs((other.get("ordinal") or 0) - ordinal),
            other.get("ordinal") or 0,
            str(entry.get("frame_position") or ""),
        )
        for other in spine.get("frames") or []
        if isinstance(other, Mapping)
        and other.get("episode_id") == frame.get("episode_id")
        and other.get("frame_id") != frame.get("frame_id")
        for entry in (other.get("visual_brief") or {}).get("subject_blocking") or []
        if isinstance(entry, Mapping) and entry.get("cast_id") == cast_id
    )
    for _, _, position in candidates:
        side = frame_side(position)
        if side in {"left", "right"}:
            return side
    return None


def default_subject_blocking(
    spine: Mapping[str, Any],
    frame: Mapping[str, Any],
    cast_refs: Sequence[str],
) -> list[dict[str, Any]]:
    """The frame's ``subject_blocking`` for ``cast_refs``: kept entries as they are, a default for anyone added.

    Parameters
    ----------
    spine
        The story (``api/spine.json``).
    frame
        The frame, as saved.
    cast_refs
        Everyone the frame should stage, in order (cast ids).

    Returns
    -------
    list[dict[str, Any]]
        One entry per id in ``cast_refs``, in that order.
    """

    names = {
        str(card.get("cast_id")): str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping)
    }
    stored = {
        str(entry.get("cast_id")): dict(entry)
        for entry in (frame.get("visual_brief") or {}).get("subject_blocking") or []
        if isinstance(entry, Mapping) and entry.get("cast_id")
    }
    kept = [stored[cast_id] for cast_id in cast_refs if cast_id in stored]
    anchor = kept[0] if kept else None
    anchor_side = frame_side(anchor.get("frame_position")) if anchor else None
    taken = {frame_side(entry.get("frame_position")) for entry in kept}
    entries: list[dict[str, Any]] = []
    for cast_id in cast_refs:
        if cast_id in stored:
            entries.append(stored[cast_id])
            continue
        side = _nearest_own_side(spine, frame, cast_id)
        if side is None:
            if not kept and not entries:
                side = "center"
            elif anchor_side == "left":
                side = "right"
            elif anchor_side == "right":
                side = "left"
            else:
                side = next((s for s in ("right", "left") if s not in taken), "right")
        taken.add(side)
        facing = names.get(str(anchor["cast_id"])) if anchor is not None else None
        entries.append(
            {
                "cast_id": cast_id,
                "frame_position": "centre of the frame in the middle plane"
                if side == "center"
                else f"frame {side}, in the middle plane",
                "pose": ADDED_CAST_POSE,
                "gaze": f"looking toward {facing}"
                if facing
                else "looking toward the centre of the shot",
                "interaction": f"shares the moment with {facing}, keeping their own space"
                if facing
                else "holds their position in the scene",
            }
        )
    return entries


__all__ = ["ADDED_CAST_POSE", "default_subject_blocking", "frame_side"]
