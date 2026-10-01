"""Refuse story-changing and paid commands on a desk whose server story another desk also points at.

A desk copied on disk (a "copy" desk to try something) keeps the original's
``production.json``, so both desks name one ``spine_id``: one story on the
server. An edit, a redraw or a film on the copy changes, and spends on, the
producer's real story. Before such a command the kit looks for another desk
with the same ``spine_id`` beside this one (and under the default desks
folder) and stops, naming it, unless the operator passes ``--shared-spine-ok``.

Commands that only read the server or work on local files are never stopped.
"""

from __future__ import annotations

import json
from pathlib import Path

from creation.ops.folder import DEFAULT_RUN_PARENT
from creation.production_state import PRODUCTION_FILENAME

#: Commands that read the server or work on local files only: never stopped.
READ_OR_LOCAL_COMMANDS = frozenset(
    {
        # fictora-produce
        "start", "bind", "presets", "config", "status", "setup-check", "caption",
        # episode flow: reads
        "expressions", "spine", "take-facts", "check-lines", "plates",
        # local post (ffmpeg on this laptop)
        "join", "trim", "tempo", "freeze", "soften", "blur", "deboard", "voice-fx", "review",
        # local post that writes only on the desk: the reel (under reels/), the pinned bed (series.json)
        "reel", "set-bed",
    }
)  # fmt: skip
#: Flag that lets a command through on a shared story.
FLAG = "--shared-spine-ok"


def _spine_id(desk: Path) -> str | None:
    try:
        data = json.loads((desk / PRODUCTION_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("spine_id") if isinstance(data, dict) else None
    return str(value) if value else None


def desks_sharing_spine(
    desk: Path, *, roots: tuple[Path, ...] | None = None
) -> list[Path]:
    """Other desks whose ``production.json`` names this desk's ``spine_id``.

    Parameters
    ----------
    desk
        Series desk.
    roots
        Folders whose desks are compared (default: the desk's own folder and the default desks folder).

    Returns
    -------
    list[Path]
        The other desks, sorted; empty when the desk has no story yet or none shares it.
    """

    desk = desk.expanduser().resolve()
    spine = _spine_id(desk)
    if not spine:
        return []
    folders = roots if roots is not None else (desk.parent, DEFAULT_RUN_PARENT)
    found: set[Path] = set()
    for folder in folders:
        try:
            children = list(folder.expanduser().iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and child.resolve() != desk and _spine_id(child) == spine:
                found.add(child.resolve())
    return sorted(found)


def shared_spine_refusal(
    command: str, desk: Path, *, transcribe: bool = False
) -> str | None:
    """Why ``command`` must not run on ``desk``, or ``None`` when it may.

    Parameters
    ----------
    command
        The subcommand.
    desk
        Its ``--desk``.
    transcribe
        ``review --transcribe`` (a paid read).

    Returns
    -------
    str | None
        The refusal naming the other desks, or ``None``.
    """

    if command in READ_OR_LOCAL_COMMANDS and not transcribe:
        return None
    others = desks_sharing_spine(desk)
    if not others:
        return None
    spine = _spine_id(desk.expanduser().resolve())
    listed = "\n".join(f"    {other}" for other in others)
    return (
        f"Stopped: this desk's story (spine {spine}) is also the story of:\n{listed}\n"
        f"`{command}` changes or spends on that one server story, so it would change the other desk's "
        "story too. Run it on the desk that owns the story, or give this desk its own story "
        f"(`fictora-produce start`), or pass {FLAG} if both desks are meant to share it. Nothing was sent."
    )
