"""Carry a finish record onto its files after a cut: the trim handles' finish record."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from creation.ops.folder import next_versioned_path
from creation.post.finish_record import (
    RECORD_FILES,
    FinishRecord,
    carry_finish_record,
)

#: Name part of each edited record file: ``take-epNN-tK-<step>-<part>-vN.mp4``.
COMPANION = {"pre_bed": "prebed", "master": "master", "final": "final"}

Apply = Callable[[Path, Path], object]


class RecordCarry:
    """The finish record of the file being edited, and the edited copy of each of its files.

    Parameters
    ----------
    desk
        Series desk.
    record
        The record naming the edited file.
    source
        The file the record names as its final.
    step
        The edit's name part in the new file names.
    """

    def __init__(
        self, desk: Path, record: FinishRecord, source: Path, step: str
    ) -> None:
        self.desk = desk
        self.record = record
        self.source = source.resolve()
        self.step = step
        self.files: dict[str, Path | None] = {}
        #: What happened to the captions on the last :meth:`write` (``!!`` when they could not be carried).
        self.caption_line: str | None = None
        self.problem = self._check()

    def _inputs(self) -> dict[str, Path | None]:
        return {role: self.record.resolve(self.desk, role) for role in RECORD_FILES}

    def _check(self) -> str | None:
        for role, path in self._inputs().items():
            if path is None and role == "pre_bed":
                continue
            if path is None or not path.is_file():
                return f"the {COMPANION[role]} file its finish record names is gone ({path})"
        return None

    def edit_companions(self, apply: Apply) -> None:
        """Apply the edit to every record file except the source (the caller edits that one)."""

        if self.problem is not None:
            return
        takes = self.desk / f"ep{self.record.episode:02d}" / "takes"
        base = f"take-ep{self.record.episode:02d}-{self.record.take_id}"
        done: dict[Path, Path] = {}
        for role, path in self._inputs().items():
            if path is None:
                self.files[role] = None
                continue
            resolved = path.resolve()
            if resolved == self.source:
                continue
            if resolved not in done:
                out = next_versioned_path(
                    takes, f"{base}-{self.step}-{COMPANION[role]}", ".mp4"
                )
                apply(resolved, out)
                done[resolved] = out
            self.files[role] = done[resolved]

    def write(self, output: Path, edit: dict[str, Any]) -> Path | None:
        """Write the carried record once the source is edited into ``output``."""

        if self.problem is not None:
            return None
        for role, path in self._inputs().items():
            if path is not None and path.resolve() == self.source:
                self.files[role] = output
        written = carry_finish_record(
            self.desk, self.record, files=self.files, edit=edit
        )
        self.caption_line = self._carry_captions(edit)
        return written

    def _carry_captions(self, edit: dict[str, Any]) -> str | None:
        """Carry the master's ``.ass`` onto the edited master, re-timed as the picture was (canary 7 Oct 2026).

        Later steps read a take's captions beside the record's master;
        without this an edit after finish left them uncaptioned.
        """

        from creation.post.edit_captions import captions_for_record, carry_ass

        before = self.record.resolve(self.desk, "master")
        after = self.files.get("master")
        if before is None or after is None or after.resolve() == before.resolve():
            return None
        source, notes = captions_for_record(self.desk, self.record)
        if source is None:
            if any(note.startswith("!!") for note in notes):
                return "\n".join(notes)
            return (
                f"- Captions: none beside the master `{before.name}`, so none on `{after.name}` "
                "(the take was finished without burned captions)"
            )
        carried = carry_ass(source, after.with_suffix(".ass"), [edit])
        return "\n".join([*notes, carried.line])
