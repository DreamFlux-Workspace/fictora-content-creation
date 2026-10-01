"""Say the server's ``minor_in_intimate_scene`` refusal in the operator's words (fictora-drama #562).

Producer rule (1 Oct 2026): a child can be in a story, but never in a
romantic, sexual or intimate scene, not as a participant and not as a
bystander. The server refuses a beat or frame that puts someone under 18 on
screen in such a scene, at every free step before a paid one: authoring, a
spine edit (``edit`` / ``line`` / a cascade), board admission, the estimate
and batch, and film admission. Its 422 names each scene in
``details.scenes[]`` (``path``, ``kind``, ``episode_ordinal``, ``ordinal``,
``minors``, ``phrase``).

:func:`minor_scene_fix` turns that into one line per scene (which episode,
beat or frame, which child, the words that made it intimate) and the two
fixes: move the child out of that shot, or change the scene so it is not
intimate. Never age the child up to keep the scene.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: The server's refusal code.
MINOR_IN_INTIMATE_SCENE = "minor_in_intimate_scene"


def _who(minors: Any) -> str:
    names = [str(name) for name in minors or [] if str(name).strip()]
    if not names:
        return "a child"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _place_id(path: str) -> str | None:
    """``frame_ep01_04`` from a spine path ``frames.frame_ep01_04`` (a draft path has none)."""

    found = re.match(r"^(?:frames|beats)\.(.+)$", path)
    return found.group(1) if found else None


def scene_lines(details: Mapping[str, Any] | None) -> list[str]:
    """One line per refused scene, with its fix.

    Parameters
    ----------
    details
        The refusal's ``details`` (``{"scenes": [...]}``), or ``None``.

    Returns
    -------
    list[str]
        ``- episode 1 frame 4 (frame_ep01_04): Mina is under 18 and on screen; "they kiss" makes it intimate``
        then its ``fix:`` line, for every scene named.
    """

    rows: list[str] = []
    for scene in (details or {}).get("scenes") or []:
        if not isinstance(scene, Mapping):
            continue
        kind = str(scene.get("kind") or "scene")
        episode = scene.get("episode_ordinal")
        ordinal = scene.get("ordinal")
        path = str(scene.get("path") or "")
        ident = _place_id(path)
        who = _who(scene.get("minors"))
        verb = "is" if len(scene.get("minors") or []) <= 1 else "are"
        phrase = scene.get("phrase")
        where = f"episode {episode} {kind} {ordinal}" + (
            f" ({ident})" if ident else f" ({path})" if path else ""
        )
        said = f'; "{phrase}" makes it intimate' if phrase else ""
        rows.append(f"  - {where}: {who} {verb} under 18 and on screen{said}")
        if kind == "frame":
            target = f"--frame {ident}" if ident else f"--frame {ordinal}"
            move = (
                f"move {who} out of that shot (`edit --episode {episode} {target} --set 'cast_refs=[...]'` "
                "naming everyone else in it; the server keeps their staging)"
            )
            change = "or change the scene so it is not intimate (the frame's moment and blocking, and its beat)"
        else:
            target = f"--beat {ordinal}"
            move = (
                f"move {who} out of that beat (`edit --episode {episode} {target}`: another motion subject, "
                "and out of the frames it anchors)"
            )
            change = f"or change the scene so it is not intimate (`edit --episode {episode} {target} --intent ...`)"
        rows.append(f"    fix: {move}, {change}. Never age {who} up to keep the scene.")
    return rows


def minor_scene_fix(details: Mapping[str, Any] | None) -> str:
    """The operator text for a ``minor_in_intimate_scene`` refusal.

    Parameters
    ----------
    details
        The refusal's ``details``, or ``None`` (a job failure carries only the message).

    Returns
    -------
    str
        The rule, each scene with its child and its fix (or the general fix
        when no scene is named), and that nothing was drawn or paid.
    """

    rows = [
        "  A child is never in a romantic, sexual or intimate scene, not even as a bystander "
        "(producer rule, 1 Oct 2026). Nothing was drawn or paid."
    ]
    named = scene_lines(details)
    rows += named or [
        "  fix: move the child out of that shot (`edit --frame` / `edit --beat`: out of its cast and blocking), "
        "or change the scene so it is not intimate. Never age the child up to keep the scene."
    ]
    return "\n".join(rows)


def says_minor_scene(code: Any, message: Any) -> bool:
    """True when an error is the ``minor_in_intimate_scene`` refusal (its code, or a message that starts with it)."""

    return str(code or "") == MINOR_IN_INTIMATE_SCENE or MINOR_IN_INTIMATE_SCENE in str(
        message or ""
    )


__all__ = [
    "MINOR_IN_INTIMATE_SCENE",
    "minor_scene_fix",
    "says_minor_scene",
    "scene_lines",
]
