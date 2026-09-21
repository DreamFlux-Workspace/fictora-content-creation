"""Append-only run notes for a content production folder."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


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
