"""JSON persistence for operator desks."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

GateName = Literal["plates", "script", "board", "video"]
GateStatus = Literal["pending", "approved", "blocked"]


@dataclass
class GateState:
    """Human gate for one production step."""

    status: GateStatus = "pending"
    note: str | None = None
    at_utc: str | None = None


@dataclass
class DeskRecord:
    """One series production desk bound to a drama spine."""

    desk_id: str
    title: str
    prompt: str
    band: str
    session_id: str
    preset_id: str
    preset_version: str
    video_lane: str
    spine_id: str | None = None
    spine_version: str | None = None
    episode_id: str | None = None
    waiting_gate: GateName = "plates"
    gates: dict[str, GateState] = field(default_factory=dict)
    exposure: dict[str, Any] | None = None
    estimate: dict[str, Any] | None = None
    last_job: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None
    media: dict[str, Any] = field(default_factory=dict)
    created_at_utc: str = field(default_factory=lambda: _now())
    updated_at_utc: str = field(default_factory=lambda: _now())

    def touch(self) -> None:
        """Stamp ``updated_at_utc``."""

        self.updated_at_utc = _now()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DeskStore:
    """Filesystem-backed desk registry."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, desk_id: str) -> Path:
        return self.root / desk_id / "desk.json"

    def _artifact_dir(self, desk_id: str) -> Path:
        path = self.root / desk_id / "api"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_artifact(self, desk_id: str, name: str, payload: Any) -> None:
        """Write one JSON artefact for debugging."""

        path = self._artifact_dir(desk_id) / name
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    def create(
        self,
        *,
        title: str,
        prompt: str,
        band: str,
        session_id: str,
        preset_id: str,
        preset_version: str,
        video_lane: str,
    ) -> DeskRecord:
        """Create a new desk folder and record."""

        desk_id = f"desk_{uuid.uuid4().hex[:12]}"
        desk_dir = self.root / desk_id
        desk_dir.mkdir(parents=True, exist_ok=False)
        record = DeskRecord(
            desk_id=desk_id,
            title=title.strip(),
            prompt=prompt.strip(),
            band=band,
            session_id=session_id,
            preset_id=preset_id,
            preset_version=preset_version,
            video_lane=video_lane,
            gates={
                "plates": GateState(),
                "script": GateState(),
                "board": GateState(),
                "video": GateState(),
            },
        )
        self.write(record)
        return record

    def write(self, record: DeskRecord) -> None:
        """Persist one desk record."""

        record.touch()
        payload = asdict(record)
        payload["gates"] = {key: asdict(gate) for key, gate in record.gates.items()}
        self._path(record.desk_id).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def get(self, desk_id: str) -> DeskRecord:
        """Load one desk by id.

        Raises
        ------
        FileNotFoundError
            When the desk does not exist.
        """

        raw = json.loads(self._path(desk_id).read_text(encoding="utf-8"))
        gates = {key: GateState(**value) for key, value in raw.get("gates", {}).items()}
        raw["gates"] = gates
        return DeskRecord(**raw)

    def list(self) -> list[DeskRecord]:
        """Return all desks sorted by update time."""

        records: list[DeskRecord] = []
        for path in self.root.glob("desk_*/desk.json"):
            desk_id = path.parent.name
            try:
                records.append(self.get(desk_id))
            except (json.JSONDecodeError, TypeError, FileNotFoundError):
                continue
        records.sort(key=lambda item: item.updated_at_utc, reverse=True)
        return records
