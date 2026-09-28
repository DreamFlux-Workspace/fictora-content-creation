"""Shared fixtures: a real series desk bound to a fake Drama API, and real ffmpeg media for local post."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands, orchestrate
from creation.ops.floor import init_series_desk
from creation.ops.folder import next_versioned_path
from creation.production_state import ProductionState, ensure_production, load_production, save_production
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


def turbo_take_usd(seconds: float = 15.0) -> float:
    """What an H3 Max Turbo 768P take costs today: $0.02/s through 2026-09-30, $0.04/s after (fal page)."""

    rate = 0.02 if date.today() <= date(2026, 9, 30) else 0.04
    return round(rate * max(seconds, 5.0), 2)


def set_phase(desk: Path, phase: str, **fields: Any) -> None:
    """Move the desk's phase machine (test setup only)."""

    state = load_production(desk)
    state.phase = phase  # type: ignore[assignment]
    for key, value in fields.items():
        setattr(state, key, value)
    save_production(desk, state)


# --- local post (finish, voice, revoice) ------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")

SPINE = {
    "spine_id": "spine_test",
    "spine_version": "sha256:" + "0" * 64,
    "microdrama_genre": "urban_romance",
    "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
    "cast": [
        {"cast_id": "cast_kenji", "name": "Kenji", "voice_brief": {"provider_voice": "Roger"}},
        {"cast_id": "cast_aya", "name": "Aya", "voice_brief": {"provider_voice": "Laura"}},
    ],
    "beats": [
        {
            "episode_id": "episode_01",
            "dialogue_lines": [
                {"line_id": "l1", "cast_id": "cast_kenji", "text": "Wait for me here."},
                {"line_id": "l2", "cast_id": "cast_aya", "text": "Not tonight."},
            ],
        }
    ],
}


def make_take(path: Path, *, seconds: float = 5.0, tones: tuple[tuple[float, float, int], ...] = ((1.0, 2.0, 440),),
              size: str = "192x336", colour: str = "gray") -> Path:  # fmt: skip
    """A test take: flat colour picture, sine tones in the given windows, silence elsewhere."""

    path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = ["-f", "lavfi", "-i", f"color=c={colour}:s={size}:d={seconds}:r=24"]
    graph = []
    for index, (start, end, freq) in enumerate(tones, start=1):
        inputs += ["-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}:sample_rate=48000"]
        graph.append(f"[{index}:a]volume='if(between(t,{start},{end}),0.5,0)':eval=frame[t{index}]")
    labels = "".join(f"[t{i}]" for i in range(1, len(tones) + 1))
    graph.append(f"{labels}amix=inputs={len(tones)}:normalize=0[a]")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip
    return path


def make_tone(path: Path, *, seconds: float = 1.0, freq: int = 660, volume: float = 0.5) -> Path:
    """A plain sine audio file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}:sample_rate=48000",
         "-af", f"volume={volume}", str(path)],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture
def post_desk(tmp_path: Path) -> Path:
    """Local post tests: a series desk bound to ``spine_test`` with the spine saved under ``ep01/api``."""

    made = init_series_desk(tmp_path, "Post Test", band="15s", episode_count=1, day=date(2026, 9, 28))
    state = ProductionState(
        session_id="content-ops-test", prompt="p", preset_id="x", preset_version="1", spine_id="spine_test"
    )
    save_production(made, state)
    api = made / "ep01" / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "03_spine.json").write_text(json.dumps(SPINE), encoding="utf-8")
    (made / "ep01" / "takes").mkdir(exist_ok=True)
    return made
