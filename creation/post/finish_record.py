"""The finish record: which file each step of ``finish`` left, so ``join`` can rebuild the sound.

``finish`` writes ``epNN/takes/take-epNN-tK-finish-vN.json`` after every run.
``join`` needs two files per take that the finished (marked) file no longer
separates: the take before the bed went on (``pre_bed``, what the mix read:
voice, effects, hand cues, the look) and the un-marked captioned picture
(``master``, what the mark went on). With them the join lays ONE bed across
all takes instead of stitching each take's own bed, and marks the joined file
once.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from creation.ops.folder import next_versioned_path

RECORD_KIND = "fictora-finish-record"


@dataclass(frozen=True)
class FinishRecord:
    """One ``finish`` run on one take (paths relative to the desk when inside it)."""

    episode: int
    take_id: str
    complete: bool
    pre_bed: str | None
    master: str
    final: str
    bed: str | None
    bed_db: float
    duck_db: float | None
    path: Path | None = None

    def resolve(self, desk: Path, name: str) -> Path | None:
        """Absolute path of one stored file (``pre_bed``, ``master``, ``final``, ``bed``)."""

        stored = getattr(self, name)
        if not stored:
            return None
        path = Path(stored)
        return path if path.is_absolute() else desk / path


def _stored(desk: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(desk.resolve()))
    except ValueError:
        return str(resolved)


def write_finish_record(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    complete: bool,
    pre_bed: Path | None,
    master: Path,
    final: Path,
    bed: Path | None,
    bed_db: float,
    duck_db: float | None,
) -> Path:
    """Write ``takes/take-epNN-tK-finish-vN.json`` (a new version; never overwrites).

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        The take.
    complete
        Music, SFX and the mix all went on.
    pre_bed
        What the mix read (the take before the bed), or ``None`` when the mix never ran.
    master
        The un-marked last file (what the mark went on).
    final
        The finished file.
    bed, bed_db, duck_db
        The bed and how it was mixed.

    Returns
    -------
    Path
        The record.
    """

    takes = desk / f"ep{episode:02d}" / "takes"
    path = next_versioned_path(takes, f"take-ep{episode:02d}-{take_id}-finish", ".json")
    body = {
        "kind": RECORD_KIND,
        "episode": episode,
        "take_id": take_id,
        "complete": complete,
        "pre_bed": _stored(desk, pre_bed),
        "master": _stored(desk, master),
        "final": _stored(desk, final),
        "bed": _stored(desk, bed),
        "bed_db": bed_db,
        "duck_db": duck_db,
    }
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path


def _version(path: Path) -> int:
    match = re.search(r"-v(\d+)$", path.stem)
    return int(match.group(1)) if match else 0


def _load(path: Path) -> FinishRecord | None:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("kind") != RECORD_KIND:
        return None
    body = {f.name: raw.get(f.name) for f in fields(FinishRecord) if f.name != "path"}
    return FinishRecord(**body, path=path)


def finish_records(desk: Path, episode: int) -> list[FinishRecord]:
    """Every finish record of an episode, oldest version first per take."""

    takes = desk / f"ep{episode:02d}" / "takes"
    found = sorted(takes.glob(f"take-ep{episode:02d}-t*-finish-v*.json"), key=_version)
    return [record for path in found if (record := _load(path)) is not None]


def latest_finish_record(desk: Path, episode: int, take_id: str) -> FinishRecord | None:
    """The newest complete finish record of one take."""

    records = [
        r for r in finish_records(desk, episode) if r.take_id == take_id and r.complete
    ]
    return records[-1] if records else None


def record_for_file(desk: Path, file: Path) -> FinishRecord | None:
    """The newest finish record that names ``file`` as its final, master or pre-bed take."""

    wanted = file.expanduser().resolve()
    match = re.match(r"take-ep(\d+)-", wanted.name)
    episodes = [int(match.group(1))] if match else []
    hits: list[FinishRecord] = []
    for episode in episodes:
        for record in finish_records(desk, episode):
            names = ("final", "master", "pre_bed")
            if any(
                (p := record.resolve(desk, n)) is not None and p.resolve() == wanted
                for n in names
            ):
                hits.append(record)
    return hits[-1] if hits else None
