"""ffmpeg / ffprobe helpers for local post (audio levels, probes, one limiter)."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: The one limiter every local mix ends on (-1 dBFS ceiling).
LIMITER = "alimiter=limit=0.891:attack=5:release=120:level=disabled"


class MediaToolError(RuntimeError):
    """ffmpeg or ffprobe failed, or is missing."""


def ffmpeg_bin() -> str:
    """Return the ``ffmpeg`` path.

    Raises
    ------
    MediaToolError
        When ffmpeg is not installed.
    """

    found = shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if not Path(found).exists():
        raise MediaToolError("ffmpeg is required for local post (macOS: brew install ffmpeg)")
    return found


def ffprobe_bin() -> str:
    """Return the ``ffprobe`` path next to ffmpeg, else on PATH.

    Raises
    ------
    MediaToolError
        When ffprobe is not installed.
    """

    beside = Path(ffmpeg_bin()).with_name("ffprobe")
    found = str(beside) if beside.exists() else shutil.which("ffprobe")
    if not found:
        raise MediaToolError("ffprobe is required for local post (it ships with ffmpeg)")
    return found


def run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg quietly, overwriting nothing the caller did not name.

    Parameters
    ----------
    args
        Arguments after ``ffmpeg -v error -y``.

    Raises
    ------
    MediaToolError
        When ffmpeg exits non-zero.
    """

    result = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise MediaToolError(f"ffmpeg failed: {result.stderr.strip()[-600:]}")


@dataclass(frozen=True)
class VideoInfo:
    """What ffprobe says about a take."""

    width: int
    height: int
    duration_seconds: float
    fps: float
    has_audio: bool


def probe_video(path: Path) -> VideoInfo:
    """Probe a video's size, length, frame rate and whether it has audio.

    Parameters
    ----------
    path
        Video file.

    Returns
    -------
    VideoInfo
        Probe result.

    Raises
    ------
    MediaToolError
        When ffprobe fails or finds no video stream.
    """

    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries",
         "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(f"ffprobe failed on {path.name}: {result.stderr.strip()[-300:]}")
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaToolError(f"{path.name} has no video stream")
    num, _, den = str(video.get("r_frame_rate") or "24/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 24.0
    return VideoInfo(
        width=int(video["width"]),
        height=int(video["height"]),
        duration_seconds=float(data["format"]["duration"]),
        fps=fps,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def media_duration(path: Path) -> float:
    """Length of any audio or video file in seconds.

    Raises
    ------
    MediaToolError
        When ffprobe fails.
    """

    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(f"ffprobe failed on {path.name}: {result.stderr.strip()[-300:]}")
    return float(json.loads(result.stdout)["format"]["duration"])


def measure_loudness(path: Path) -> float:
    """Integrated loudness (LUFS, gated) of a file's audio; ``-inf`` for silence.

    Raises
    ------
    MediaToolError
        When ffmpeg fails.
    """

    result = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(f"loudness scan failed on {path.name}: {result.stderr.strip()[-300:]}")
    found = re.findall(r"I:\s+(-?[0-9.]+|-inf)\s+LUFS", result.stderr)
    if not found:
        return -math.inf
    value = found[-1]
    return -math.inf if value == "-inf" else float(value)


def measure_rms_windows(path: Path, *, window_seconds: float = 0.5) -> tuple[float, ...]:
    """RMS level (dB) per window of a file's first audio channel; silence reads -120.

    Parameters
    ----------
    path
        Audio or video with audio.
    window_seconds
        Window length.

    Returns
    -------
    tuple[float, ...]
        One level per window.
    """

    samples = max(1, round(48000 * window_seconds))
    result = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-af",
         (f"aresample=48000,pan=mono|c0=c0,asetnsamples=n={samples}:p=0,astats=metadata=1:reset=1,"
          "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"),
         "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    levels: list[float] = []
    for line in result.stdout.splitlines():
        if "RMS_level=" not in line:
            continue
        try:
            value = float(line.split("=", 1)[1].strip())
        except ValueError:
            value = -120.0
        levels.append(-120.0 if math.isnan(value) else max(-120.0, value))
    return tuple(levels)


def extract_wav(source: Path, out: Path, *, rate: int = 16000) -> Path:
    """Write a mono WAV of ``source``'s audio (for Whisper).

    Returns
    -------
    Path
        ``out``.
    """

    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(source), "-vn", "-ac", "1", "-ar", str(rate), str(out)])
    return out
