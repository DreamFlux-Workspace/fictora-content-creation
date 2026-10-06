"""Append-only run notes for a content production folder."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def ensure_run_notes(run_dir: Path, title: str | None = None) -> tuple[Path, bool]:
    """Make ``run-notes.md`` in ``run_dir`` when it is missing; an existing file is never touched.

    Parameters
    ----------
    run_dir
        An existing folder (a desk, or one episode's folder).
    title
        The heading's name; default the folder's name.

    Returns
    -------
    tuple[Path, bool]
        The notes file, and True when it was created now.

    Raises
    ------
    FileNotFoundError
        When ``run_dir`` is not a folder (nothing is created).
    """

    if not run_dir.is_dir():
        raise FileNotFoundError(
            f"no folder {run_dir}: run-notes.md goes in a desk or episode folder"
        )
    notes = run_dir / "run-notes.md"
    if notes.is_file():
        return notes, False
    name = (title or run_dir.name).strip() or run_dir.name
    notes.write_text(
        f"# {name} — run notes\n\n"
        "What happened on this desk, newest last: gates, spend, faults accepted, re-film causes. "
        "Each episode also keeps its own `epNN/run-notes.md`.\n",
        encoding="utf-8",
    )
    return notes, True


def append_run_note(run_dir: Path, body: str) -> Path:
    """Append one timestamped note to ``run-notes.md``. Never overwrite.

    Parameters
    ----------
    run_dir
        Production folder that already contains ``run-notes.md``.
    body
        Markdown to append after a timestamp heading.

    Returns
    -------
    Path
        The notes file that was updated.

    Raises
    ------
    FileNotFoundError
        When ``run-notes.md`` is missing.
    ValueError
        When ``body`` is empty.
    """

    notes = run_dir / "run-notes.md"
    if not notes.is_file():
        raise FileNotFoundError(f"run-notes.md missing in {run_dir}")
    text = body.strip()
    if not text:
        raise ValueError("note body must be non-empty")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    block = f"\n\n## {stamp}\n\n{text}\n"
    with notes.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return notes
