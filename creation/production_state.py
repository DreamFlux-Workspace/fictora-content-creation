"""Desk-bound production state for API orchestration."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Phase = Literal[
    "new",
    "ready_cast_enrol",
    "wait_plates",
    "wait_script",
    "ready_boards_enrol",
    "wait_board",
    "ready_estimate",
    "wait_spend",
    "ready_video",
    "complete",
    "failed",
]

PRODUCTION_FILENAME = "production.json"


@dataclass
class ProductionState:
    """API orchestration state for one episode on a series desk."""

    session_id: str
    prompt: str
    preset_id: str
    preset_version: str
    video_lane: str = "minimax-h3"
    episode_ordinal: int = 1
    band: str = "15s"
    spine_id: str | None = None
    phase: Phase = "new"
    estimate_usd: float | None = None
    last_delivery_url: str | None = None
    last_error: str | None = None
    exposure_accept_dim: bool = False
    video_idempotency_suffix: str = ""
    last_video_job_id: str | None = None

    @staticmethod
    def new_session_id(desk_slug: str) -> str:
        """Return a stable-enough session id for one desk."""

        slug = desk_slug.replace("/", "-")[:24]
        return f"content-ops-{slug}-{uuid.uuid4().hex[:8]}"


def production_path(desk: Path) -> Path:
    """Return ``production.json`` on the desk root."""

    return desk.expanduser().resolve() / PRODUCTION_FILENAME


def api_dir_for_episode(desk: Path, episode_ordinal: int) -> Path:
    """Return ``epNN/api`` under the desk."""

    slug = f"ep{episode_ordinal:02d}"
    return desk / slug / "api"


def load_production(desk: Path) -> ProductionState:
    """Load production state from the desk.

    Raises
    ------
    FileNotFoundError
        When ``production.json`` is missing.
    """

    path = production_path(desk)
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ProductionState(**raw)


def save_production(desk: Path, state: ProductionState) -> None:
    """Persist production state."""

    path = production_path(desk)
    path.write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")


def ensure_production(
    desk: Path,
    *,
    prompt: str,
    preset_id: str,
    preset_version: str,
    video_lane: str = "minimax-h3",
    episode_ordinal: int = 1,
    band: str = "15s",
) -> ProductionState:
    """Create or update production binding on a desk."""

    desk = desk.expanduser().resolve()
    if production_path(desk).is_file():
        state = load_production(desk)
        state.prompt = prompt.strip()
        state.preset_id = preset_id
        state.preset_version = preset_version
        state.video_lane = video_lane
        state.episode_ordinal = episode_ordinal
        state.band = band
        save_production(desk, state)
        return state
    session_id = ProductionState.new_session_id(desk.name)
    state = ProductionState(
        session_id=session_id,
        prompt=prompt.strip(),
        preset_id=preset_id,
        preset_version=preset_version,
        video_lane=video_lane,
        episode_ordinal=episode_ordinal,
        band=band,
    )
    save_production(desk, state)
    return state
