"""ffmpeg / ffprobe helpers for local post (audio levels, probes, one limiter)."""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

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
        raise MediaToolError(
            "ffmpeg is required for local post (macOS: brew install ffmpeg)"
        )
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
        raise MediaToolError(
            "ffprobe is required for local post (it ships with ffmpeg)"
        )
    return found


def run_ffmpeg(args: list[str]) -> None:
    """Run ffmpeg quietly, overwriting nothing the caller did not name.

    Parameters
    ----------
    args
        Arguments after ``ffmpeg -v error -y`` (run with no stdin).

    Raises
    ------
    MediaToolError
        When ffmpeg exits non-zero.
    """

    result = subprocess.run(
        # No stdin: a kit command inside a `while read` loop must not eat the loop's input
        # (L-20261006-17). stdin, not -nostdin, so the recorded command lines stay what they were.
        [ffmpeg_bin(), "-v", "error", "-y", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
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
         "stream=codec_type,width,height,r_frame_rate:stream_disposition=attached_pic:format=duration",
         "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(
            f"ffprobe failed on {path.name}: {result.stderr.strip()[-300:]}"
        )
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    # An attached cover (finish's thumbnail) is a video stream too: never the picture.
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not _is_cover(s)),
        None,
    )
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


def _is_cover(stream: dict) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def video_streams(path: Path) -> tuple[int, int | None]:
    """Where the picture and the attached cover sit among a file's video streams.

    Parameters
    ----------
    path
        Video file.

    Returns
    -------
    tuple[int, int | None]
        ``(picture, cover)``: indexes for ``0:v:N``. ``cover`` is ``None`` when
        the file has no attached picture (``finish`` embeds one as the platform
        cover art).

    Raises
    ------
    MediaToolError
        When ffprobe fails or finds no picture stream.
    """

    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=index:stream_disposition=attached_pic", "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(
            f"ffprobe failed on {path.name}: {result.stderr.strip()[-300:]}"
        )
    streams = json.loads(result.stdout).get("streams") or []
    picture = next((n for n, s in enumerate(streams) if not _is_cover(s)), None)
    if picture is None:
        raise MediaToolError(f"{path.name} has no video stream")
    cover = next((n for n, s in enumerate(streams) if _is_cover(s)), None)
    return picture, cover


def keep_cover_args(cover: int | None, *, output_stream: int = 1) -> list[str]:
    """ffmpeg output args that copy an attached cover across unchanged.

    Put them after the picture's own ``-c:v`` so the copy wins for the cover.

    Parameters
    ----------
    cover
        The cover's ``0:v:N`` index from :func:`video_streams`, or ``None``.
    output_stream
        The cover's video index in the output (after the picture).

    Returns
    -------
    list[str]
        ``[]`` when there is no cover.
    """

    if cover is None:
        return []
    return [
        "-map", f"0:v:{cover}", f"-c:v:{output_stream}", "copy",
        f"-disposition:v:{output_stream}", "attached_pic",
    ]  # fmt: skip


def media_duration(path: Path) -> float:
    """Length of any audio or video file in seconds.

    Raises
    ------
    MediaToolError
        When ffprobe fails.
    """

    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(
            f"ffprobe failed on {path.name}: {result.stderr.strip()[-300:]}"
        )
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
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(
            f"loudness scan failed on {path.name}: {result.stderr.strip()[-300:]}"
        )
    found = re.findall(r"I:\s+(-?[0-9.]+|-inf)\s+LUFS", result.stderr)
    if not found:
        return -math.inf
    value = found[-1]
    return -math.inf if value == "-inf" else float(value)


@dataclass(frozen=True)
class AudioLevels:
    """A file's EBU R128 levels (``None`` where ffmpeg found nothing to measure).

    Parameters
    ----------
    integrated_lufs
        Integrated (gated) loudness.
    max_momentary_lufs
        The loudest 400 ms.
    true_peak_dbtp
        True peak.
    """

    integrated_lufs: float | None
    max_momentary_lufs: float | None
    true_peak_dbtp: float | None


def _level(raw: str) -> float | None:
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > -70.0 else None


def measure_levels(path: Path) -> AudioLevels:
    """Measure a file's integrated loudness, loudest 400 ms and true peak with ffmpeg ``ebur128``.

    Parameters
    ----------
    path
        Audio, or video with audio.

    Returns
    -------
    AudioLevels
        The levels.

    Raises
    ------
    MediaToolError
        When ffmpeg fails.
    """

    result = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", "aresample=48000,pan=mono|c0=c0,ebur128=peak=true", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(
            f"level scan failed on {path.name}: {result.stderr.strip()[-300:]}"
        )
    summary = result.stderr.rsplit("Summary:", 1)[-1]
    integrated = re.findall(r"I:\s+(-?[0-9.]+|-inf)\s+LUFS", summary)
    peak = re.findall(r"Peak:\s+(-?[0-9.]+|-inf)\s+dBFS", summary)
    momentary = [
        value
        for value in (
            _level(raw) for raw in re.findall(r"\bM:\s*(-?[0-9.]+|-inf)", result.stderr)
        )
        if value is not None
    ]
    return AudioLevels(
        integrated_lufs=_level(integrated[-1]) if integrated else None,
        max_momentary_lufs=max(momentary) if momentary else None,
        true_peak_dbtp=_level(peak[-1]) if peak else None,
    )


def measure_rms_windows(
    path: Path, *, window_seconds: float = 0.5
) -> tuple[float, ...]:
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
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
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


def count_frames(path: Path) -> int:
    """Decoded video frame count (``ffprobe -count_frames``).

    Parameters
    ----------
    path
        Video file.

    Returns
    -------
    int
        Frames in the first video stream.

    Raises
    ------
    MediaToolError
        When ffprobe fails.
    """

    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0 or not result.stdout.strip():
        raise MediaToolError(
            f"ffprobe could not count the frames of {path.name}: {result.stderr.strip()[-300:]}"
        )
    return int(result.stdout.strip().split(",")[0])


def decode_frames(
    path: Path,
    *,
    width: int,
    height: int,
    fps: float | None = None,
    max_frames: int | None = None,
    start: float | None = None,
    crop: tuple[int, int, int, int] | None = None,
) -> npt.NDArray[np.float64]:
    """Decode a video's frames as RGB scaled to ``width`` x ``height``.

    Parameters
    ----------
    path
        Video file.
    width, height
        Analysis size.
    fps
        Resample to this rate first; ``None`` keeps every frame.
    max_frames
        Stop after this many frames.
    start
        Seek to this second first (``None``: from the start).
    crop
        ``(x, y, width, height)`` of the frame to keep before scaling (a
        letterbox file's picture); ``None`` keeps the whole frame.

    Returns
    -------
    numpy.ndarray
        ``(frames, height, width, 3)`` floats in 0-255.

    Raises
    ------
    MediaToolError
        When ffmpeg fails.
    """

    vf = (
        f"scale={width}:{height}"
        if fps is None
        else f"fps={fps},scale={width}:{height}"
    )
    if crop is not None:
        x, y, w, h = crop
        vf = f"crop={w}:{h}:{x}:{y}," + vf
    args = [ffmpeg_bin(), "-nostdin", "-v", "error"]
    if start:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if max_frames is not None:
        args += ["-frames:v", str(max_frames)]
    args += ["-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    result = subprocess.run(
        args, stdin=subprocess.DEVNULL, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise MediaToolError(
            f"frame decode failed on {path.name}: {result.stderr.decode(errors='replace')[-300:]}"
        )
    raw = np.frombuffer(result.stdout, dtype=np.uint8)
    size = width * height * 3
    count = raw.size // size
    return raw[: count * size].reshape(count, height, width, 3).astype(np.float64)


def iter_frames(
    path: Path,
    *,
    width: int,
    height: int,
    fps: float | None = None,
    start: float | None = None,
) -> Iterator[npt.NDArray[np.uint8]]:
    """Stream a video's frames one at a time as RGB ``uint8`` (a whole take never sits in memory).

    Parameters
    ----------
    path
        Video file.
    width, height
        Frame size.
    fps
        Resample to this rate first; ``None`` keeps every frame.
    start
        Seek to this second first (``None``: from the start).

    Yields
    ------
    numpy.ndarray
        ``(height, width, 3)`` uint8 frames.

    Raises
    ------
    MediaToolError
        When ffmpeg fails.
    """

    vf = (
        f"scale={width}:{height}"
        if fps is None
        else f"fps={fps},scale={width}:{height}"
    )
    seek = ["-ss", f"{start:.3f}"] if start else []
    proc = subprocess.Popen(
        [ffmpeg_bin(), "-nostdin", "-v", "error", *seek, "-i", str(path), "-vf", vf,
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )  # fmt: skip
    size = width * height * 3
    assert proc.stdout is not None
    finished = False
    try:
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                finished = True
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        if not finished:
            # The reader stopped early: ffmpeg is still writing, so stop it rather than read the rest.
            proc.kill()
            proc.wait()
        else:
            err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            if proc.wait() != 0:
                raise MediaToolError(
                    f"frame decode failed on {path.name}: {err[-300:]}"
                )
