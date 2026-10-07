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
from creation.production_state import (
    ProductionState,
    ensure_production,
    load_production,
    save_production,
)
from fake_api import FakeApi, png_bytes, spine_fixture


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    """A 15s desk with episode 1 drafted (spine ``sp1``), ready for later stages."""

    path = init_series_desk(tmp_path, "Closing Time", band="15s", episode_count=1)
    state = ensure_production(
        path,
        prompt="A shop at closing time.",
        preset_id="modern-romance",
        preset_version="2",
    )
    state.spine_id = "sp1"
    save_production(path, state)
    return path


@pytest.fixture
def api(desk: Path, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    """The fake API every command on ``desk`` talks to; downloads write a tiny real PNG/MP4."""

    fake = FakeApi(desk / "ep01" / "api", spine=spine_fixture())
    monkeypatch.setattr(orchestrate, "_open_run", lambda _desk, _state: fake)
    monkeypatch.setattr(episode_commands, "_open_run", lambda _desk, _state: fake)

    def download(
        client: Any,
        url: str,
        directory: Path,
        stem: str,
        *,
        default_suffix: str = ".png",
    ) -> Path:
        suffix = ".mp4" if url.endswith(".mp4") else default_suffix
        directory.mkdir(parents=True, exist_ok=True)
        target = next_versioned_path(directory, stem, suffix)
        target.write_bytes(png_bytes() if suffix == ".png" else b"mp4")
        fake.events.append(("download", {"url": url, "path": str(target)}))
        return target

    monkeypatch.setattr(orchestrate, "download_to_versioned", download)
    return fake


def turbo_take_usd(seconds: float = 15.0) -> float:
    """What an H3 Max Turbo 768P take costs now: $0.02/s through 2026-09-30, $0.04/s after, on fal's day."""

    from creation.prices import fal_billing_day

    rate = 0.02 if fal_billing_day() <= date(2026, 9, 30) else 0.04
    return round(rate * max(seconds, 5.0), 2)


#: Plate and board prices ``step`` has shown (``step --confirm-spend`` needs one on new desks).
SHOWN_PRICES = {
    f"{kind}-ep{episode:02d}": 0.30
    for kind in ("plates", "boards")
    for episode in range(1, 6)
}


def set_phase(desk: Path, phase: str, **fields: Any) -> None:
    """Move the desk's phase machine (test setup only)."""

    state = load_production(desk)
    state.phase = phase  # type: ignore[assignment]
    for key, value in fields.items():
        setattr(state, key, value)
    save_production(desk, state)


# --- local post (finish, voice, revoice) ------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg not installed"
)

SPINE = {
    "spine_id": "spine_test",
    "spine_version": "sha256:" + "0" * 64,
    "microdrama_genre": "urban_romance",
    "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
    "cast": [
        {
            "cast_id": "cast_kenji",
            "name": "Kenji",
            "voice_brief": {"provider_voice": "Roger"},
        },
        {
            "cast_id": "cast_aya",
            "name": "Aya",
            "voice_brief": {"provider_voice": "Laura"},
        },
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
    inputs: list[str] = [
        "-f",
        "lavfi",
        "-i",
        f"color=c={colour}:s={size}:d={seconds}:r=24",
    ]
    graph = []
    for index, (start, end, freq) in enumerate(tones, start=1):
        inputs += ["-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}:sample_rate=48000"]
        graph.append(
            f"[{index}:a]volume='if(between(t,{start},{end}),0.5,0)':eval=frame[t{index}]"
        )
    labels = "".join(f"[t{i}]" for i in range(1, len(tones) + 1))
    graph.append(f"{labels}amix=inputs={len(tones)}:normalize=0[a]")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]",
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip
    return path


def make_tone(
    path: Path, *, seconds: float = 1.0, freq: int = 660, volume: float = 0.5
) -> Path:
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

    made = init_series_desk(
        tmp_path, "Post Test", band="15s", episode_count=1, day=date(2026, 10, 6)
    )
    state = ProductionState(
        session_id="content-ops-test",
        prompt="p",
        preset_id="x",
        preset_version="1",
        spine_id="spine_test",
    )
    save_production(made, state)
    api = made / "ep01" / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "03_spine.json").write_text(json.dumps(SPINE), encoding="utf-8")
    (made / "ep01" / "takes").mkdir(exist_ok=True)
    return made


# --- synthetic pictures for the local edit tools (deboard, trim, freeze, tempo, soften) -----------

EDIT_SIZE = (96, 168)


def board_array(width: int = EDIT_SIZE[0], height: int = EDIT_SIZE[1]) -> Any:
    """A 4x2 mosaic of flat colours: stands in for a storyboard grid."""

    import numpy as np

    grid = np.zeros((height, width, 3), dtype=np.uint8)
    colours = [(230, 40, 40), (40, 230, 40), (40, 40, 230), (230, 230, 40),
               (40, 230, 230), (230, 40, 230), (250, 250, 250), (10, 10, 10)]  # fmt: skip
    for index, colour in enumerate(colours):
        row, col = divmod(index, 2)
        grid[
            row * height // 4 : (row + 1) * height // 4,
            col * width // 2 : (col + 1) * width // 2,
        ] = colour
    return grid


def shot_frames(count: int, tint: tuple[int, int, int], *, start: int = 0,
                width: int = EDIT_SIZE[0], height: int = EDIT_SIZE[1]) -> list[Any]:  # fmt: skip
    """``count`` frames of a slowly drifting tinted gradient (gentle motion, no cut inside)."""

    import numpy as np

    x = np.linspace(0, 1, width)[None, :, None]
    y = np.linspace(0, 1, height)[:, None, None]
    frames = []
    for index in range(count):
        value = 0.5 + 0.25 * np.sin(2 * np.pi * (x + (start + index) * 0.02)) + 0.25 * y
        frames.append(
            np.clip(value * np.array(tint)[None, None, :], 0, 255).astype(np.uint8)
        )
    return frames


def write_frames(path: Path, frames: list[Any], *, fps: int = 24,
                 tones: tuple[tuple[float, float, int], ...] | None = None, tone: int = 440) -> Path:  # fmt: skip
    """Encode RGB frames with sound: one steady sine (``tone``), or sine tones in windows (``tones``)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = frames[0].shape[:2]
    seconds = len(frames) / fps
    inputs = [
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
    ]
    if tones:
        graph = []
        for index, (start, end, freq) in enumerate(tones, start=1):
            inputs += [
                "-f",
                "lavfi",
                "-i",
                f"sine=f={freq}:d={seconds}:sample_rate=48000",
            ]
            graph.append(
                f"[{index}:a]volume='if(between(t,{start},{end}),0.5,0)':eval=frame[t{index}]"
            )
        labels = "".join(f"[t{i}]" for i in range(1, len(tones) + 1))
        graph.append(f"{labels}amix=inputs={len(tones)}:normalize=0[a]")
        audio = ["-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]"]
    else:
        inputs += ["-f", "lavfi", "-i", f"sine=f={tone}:d={seconds}:sample_rate=48000"]
        audio = ["-map", "0:v", "-map", "1:a"]
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, *audio, "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-t", f"{seconds}", str(path)],
        input=b"".join(frame.tobytes() for frame in frames), check=False, capture_output=True,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr.decode()[-400:]
    return path


@pytest.fixture
def real_ocr() -> str:
    """The ``tesseract`` command; the test is skipped on a machine without it."""

    from creation.post import take_text

    found = take_text.tesseract_bin()
    if found is None:
        pytest.skip("tesseract is not installed")
    return found


@pytest.fixture(autouse=True)
def _no_spine_read_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """finish and caption read the current spine from the server; tests stay offline and use the desk's copy.

    A test that checks the read passes ``spine_fetcher`` itself.
    """

    from creation import captions

    monkeypatch.setattr(captions, "api_spine_fetcher", lambda desk, episode: None)


@pytest.fixture(autouse=True)
def _no_ocr_by_default(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep finish/review tests fast and the same on every machine: no OCR unless a test asks for ``real_ocr``."""

    if "real_ocr" in request.fixturenames:
        return
    from creation.post import take_text

    monkeypatch.setattr(take_text, "tesseract_bin", lambda: None)


@pytest.fixture
def voice_gate() -> None:
    """Ask for the real voices gate (see ``_voices_gate_off_by_default``)."""


@pytest.fixture(autouse=True)
def _voices_gate_off_by_default(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tests of filming, the estimate and the plates yes predate the voices gate: it is off unless a test
    asks for ``voice_gate`` (tests/test_voice_gate.py), so their desks need no voice yes and their
    messages and request logs stay as they were.
    """

    if "voice_gate" in request.fixturenames:
        return
    from creation import episode_commands, orchestrate

    monkeypatch.setattr(orchestrate, "voices_film_refusal", lambda *a, **k: None)
    monkeypatch.setattr(orchestrate, "voices_gate_text", lambda *a, **k: "")
    monkeypatch.setattr(orchestrate, "voices_pending_for_film", lambda *a, **k: [])
    monkeypatch.setattr(episode_commands, "voices_film_refusal", lambda *a, **k: None)


@pytest.fixture
def reel_server(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The stand-in for the server's reel engine every reel in a test talks to (``reels`` made offline).

    Every test gets one (:func:`_offline_reel_server`): no test ever uploads to or calls the real
    Drama API. A test that checks what was sent asks for this fixture by name.
    """

    from creation.post import reel_via_server
    from reel_fake_server import FakeReelServer

    fake = FakeReelServer()
    monkeypatch.setattr(reel_via_server, "ReelServer", lambda desk, episode: fake)
    real_job = reel_via_server.reel_job_id

    def job_id(desk: Path, episode: int, take_ids: list[str]) -> str:
        # A test desk with no clip record is filmed by video job ``video-1``.
        try:
            return real_job(desk, episode, take_ids)
        except reel_via_server.ReelServerError:
            return "video-1"

    monkeypatch.setattr(reel_via_server, "reel_job_id", job_id)
    return fake


@pytest.fixture(autouse=True)
def _offline_reel_server(reel_server: Any) -> None:
    """The reel after ``finish`` and every ``reel`` run against :func:`reel_server`, never the network."""
