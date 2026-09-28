"""Deboard: replace the storyboard frames a Turbo take opens on with its first real frame.

On H3 Max Turbo the take is image-to-video from the approved board, so the
video's first frame (sometimes two) is the still board grid before motion
starts. Hosted post is off, so nothing trims them and they ship. ``deboard``
measures them and removes them:

- Each opening frame is compared with the board by PSNR at a quarter of the
  768x1344 frame. A board frame sits at least ``BOARD_LEAK_MARGIN_DB`` above
  the baseline, the median PSNR of one frame a second from the second second
  on (so a long leak cannot hide inside its own baseline).
- The count is measured, never assumed, and capped at ``BOARD_LEAK_MAX_FRAMES``
  (hitting the cap is reported: look at the head at full rate).
- The board frames are dropped and the gap is filled by cloning the first real
  frame, so the length, the frame count and the sound are unchanged: every
  cue, line and caption time on the raw take still holds on the new file.
- The new frame 0 is checked against the board again; still the board is an
  error.

No board frames measured is a no-op: nothing is written.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PIL import Image

from creation.post.media import (
    MediaToolError,
    count_frames,
    decode_frames,
    probe_video,
    run_ffmpeg,
)

#: Board-leak analysis size (the 768x1344 frame at a quarter).
LEAK_ANALYSIS_SIZE = (192, 336)
#: Never count more than this many opening frames as the board.
BOARD_LEAK_MAX_FRAMES = 12
#: A board frame sits at least this far above the settled PSNR baseline.
BOARD_LEAK_MARGIN_DB = 3.0


@dataclass(frozen=True)
class BoardLeak:
    """How many opening frames are the storyboard.

    Parameters
    ----------
    frames
        Leading frames measured as the board.
    psnr_db
        PSNR against the board for each leading frame examined.
    baseline_db
        Settled PSNR of later frames against the board.
    capped
        True when every frame up to the cap read as board.
    """

    frames: int
    psnr_db: tuple[float, ...]
    baseline_db: float
    capped: bool

    def one_line(self) -> str:
        """``board leak 2 frame(s) [0:38.1, 1:37.9 dB], baseline 12.4 dB``."""

        head = ", ".join(
            f"{index}:{value:.1f}"
            for index, value in enumerate(self.psnr_db[: self.frames])
        )
        cap = (
            f" (CAP of {BOARD_LEAK_MAX_FRAMES} hit: look at the head at full rate)"
            if self.capped
            else ""
        )
        detail = f" [{head} dB]" if head else ""
        return f"board leak {self.frames} frame(s){detail}, baseline {self.baseline_db:.1f} dB{cap}"


@dataclass(frozen=True)
class DeboardResult:
    """What ``deboard`` did.

    Parameters
    ----------
    source
        The take measured.
    output
        The new file, or ``None`` when there were no board frames (nothing written).
    leak
        The measurement that decided it.
    after_psnr_db
        PSNR of the new frame 0 against the board.
    """

    source: Path
    output: Path | None
    leak: BoardLeak
    after_psnr_db: float | None = None

    @property
    def removed(self) -> int:
        """Board frames replaced."""

        return self.leak.frames if self.output is not None else 0

    def one_line(self) -> str:
        """The run-notes line."""

        if self.output is None:
            return f"no board frames at the head of {self.source.name} ({self.leak.one_line()}); nothing written"
        after = (
            f", new frame 0 at {self.after_psnr_db:.1f} dB"
            if self.after_psnr_db is not None
            else ""
        )
        cap = " CAP HIT" if self.leak.capped else ""
        return (
            f"{self.output.name}: replaced {self.leak.frames} board frame(s){cap} with the first real frame"
            f"{after}; length, frame count and sound unchanged"
        )


def _psnr(frame: npt.NDArray[np.float64], reference: npt.NDArray[np.float64]) -> float:
    mse = float(((frame - reference) ** 2).mean())
    return 99.0 if mse <= 1e-9 else 10.0 * math.log10(255.0**2 / mse)


def measure_board_leak(
    take: Path,
    board: Path,
    *,
    max_frames: int = BOARD_LEAK_MAX_FRAMES,
    margin_db: float = BOARD_LEAK_MARGIN_DB,
) -> BoardLeak:
    """Count the opening frames that are the storyboard, by PSNR against the board.

    Parameters
    ----------
    take
        Video.
    board
        The storyboard image that was the start image.
    max_frames
        Cap on leading frames counted.
    margin_db
        Separation from the baseline that marks a board frame.

    Returns
    -------
    BoardLeak
        The count, the per-frame PSNR, the baseline, and whether the cap was hit.

    Raises
    ------
    FileNotFoundError
        When the board image is missing.
    ValueError
        When the take is too short to measure a baseline (under about two seconds).
    """

    if not board.is_file():
        raise FileNotFoundError(f"board image not found: {board}")
    width, height = LEAK_ANALYSIS_SIZE
    head = decode_frames(take, width=width, height=height, max_frames=max_frames + 1)
    later = decode_frames(take, width=width, height=height, fps=1.0)[1:]
    if len(head) < 2 or len(later) < 1:
        raise ValueError(f"{take.name} is too short to measure board frames")
    with Image.open(board) as opened:
        reference = np.asarray(
            opened.convert("RGB").resize((width, height), Image.Resampling.BILINEAR),
            dtype=np.float64,
        )
    scores = [_psnr(frame, reference) for frame in head]
    baseline = statistics.median(_psnr(frame, reference) for frame in later)
    count = 0
    for value in scores[: min(max_frames, len(scores))]:
        if value < baseline + margin_db:
            break
        count += 1
    return BoardLeak(
        frames=count,
        psnr_db=tuple(round(value, 2) for value in scores[: max_frames + 1]),
        baseline_db=round(baseline, 2),
        capped=count >= max_frames,
    )


def deboard(
    take: Path, board: Path, out: Path, *, max_frames: int = BOARD_LEAK_MAX_FRAMES
) -> DeboardResult:
    """Replace the measured board frames at the head with clones of the first real frame.

    Parameters
    ----------
    take
        Raw take (never overwritten).
    board
        The approved board that was the start image.
    out
        New file; written only when board frames were found.
    max_frames
        Cap on frames replaced.

    Returns
    -------
    DeboardResult
        What was replaced (``output`` is ``None`` when nothing was).

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    MediaToolError
        When the length changed or the new frame 0 still reads as the board.
    """

    leak = measure_board_leak(take, board, max_frames=max_frames)
    if leak.frames == 0:
        return DeboardResult(source=take, output=None, leak=leak)
    if out.exists():
        raise FileExistsError(f"{out} exists; deboard never overwrites")
    before = probe_video(take)
    frames_before = count_frames(take)
    fps = before.fps or 24.0
    count = leak.frames
    # setpts goes AFTER tpad: before it, tpad stamps the clones at pts 0 and the encoder drops them,
    # so the picture loses the padded frames and runs ahead of the sound.
    graph = f"[0:v]select='gte(n\\,{count})',tpad=start={count}:start_mode=clone,setpts=N/{fps:g}/TB[v]"
    args = ["-i", str(take), "-filter_complex", graph, "-map", "[v]"]
    if before.has_audio:
        args += ["-map", "0:a", "-c:a", "copy"]
    args += [
        "-c:v",
        "libx264",
        "-crf",
        "14",
        "-pix_fmt",
        "yuv420p",
        "-r",
        f"{fps:g}",
        str(out),
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args)
    after = probe_video(out)
    frames_after = count_frames(out)
    if (
        frames_after != frames_before
        or abs(after.duration_seconds - before.duration_seconds) > 0.05
    ):
        raise MediaToolError(
            f"deboard changed the length: {frames_before} frames / {before.duration_seconds:.3f}s -> "
            f"{frames_after} frames / {after.duration_seconds:.3f}s"
        )
    check = measure_board_leak(out, board, max_frames=max_frames)
    first = check.psnr_db[0] if check.psnr_db else None
    if check.frames and not leak.capped:
        raise MediaToolError(f"{out.name}: frame 0 is still the board ({first} dB)")
    return DeboardResult(source=take, output=out, leak=leak, after_psnr_db=first)
