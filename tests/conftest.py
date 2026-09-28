"""Shared fixtures: a real series desk bound to a fake Drama API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands, orchestrate
from creation.ops.floor import init_series_desk
from creation.ops.folder import next_versioned_path
from creation.production_state import ensure_production, load_production, save_production
from fake_api import FakeApi, png_bytes, spine_fixture


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    """A 15s desk with episode 1 drafted (spine ``sp1``), ready for later stages."""

    path = init_series_desk(tmp_path, "Closing Time", band="15s", episode_count=1)
    state = ensure_production(path, prompt="A shop at closing time.", preset_id="modern-romance", preset_version="2")
    state.spine_id = "sp1"
    save_production(path, state)
    return path


@pytest.fixture
def api(desk: Path, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """The fake API every command on ``desk`` talks to; downloads write a tiny real PNG/MP4."""

    fake = FakeApi(desk / "ep01" / "api", spine=spine_fixture())
    monkeypatch.setattr(orchestrate, "_open_run", lambda _desk, _state: fake)
    monkeypatch.setattr(episode_commands, "_open_run", lambda _desk, _state: fake)

    def download(client: Any, url: str, directory: Path, stem: str, *, default_suffix: str = ".png") -> Path:
        suffix = ".mp4" if url.endswith(".mp4") else default_suffix
        directory.mkdir(parents=True, exist_ok=True)
        target = next_versioned_path(directory, stem, suffix)
        target.write_bytes(png_bytes() if suffix == ".png" else b"mp4")
        fake.events.append(("download", {"url": url, "path": str(target)}))
        return target

    monkeypatch.setattr(orchestrate, "download_to_versioned", download)
    return fake


def set_phase(desk: Path, phase: str, **fields: Any) -> None:
    """Move the desk's phase machine (test setup only)."""

    state = load_production(desk)
    state.phase = phase  # type: ignore[assignment]
    for key, value in fields.items():
        setattr(state, key, value)
    save_production(desk, state)
