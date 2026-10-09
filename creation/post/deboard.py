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

The clones keep the timeline, but they are a held still: the viewer's first
frame would wait up to half a second for the first motion. So ``finish``
cuts them off LAST, after every effect, duck and caption was laid on the
take as filmed, the same way it cuts the server's trim handles
(:func:`creation.post.take_handles.apply_take_handles`): picture and sound
together, captions moved, the cut recorded as a ``handles`` edit (source
``kit-deboard``) so ``join``, ``review`` and ``reel`` move the take facts
with it. :func:`head_cut` decides where: at the first real frame, never past
the first line's onset (a line that starts under the board frames keeps its
first word; the cut then stops short and says so). When the server set the
handles itself, its cut wins and nothing more is cut here.
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
    crop: tuple[int, int, int, int] | None = None,
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
    crop
        ``(x, y, width, height)`` of the frame compared with the board (a
        letterbox file's picture); ``None`` compares the whole frame.

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
    head = decode_frames(
        take, width=width, height=height, max_frames=max_frames + 1, crop=crop
    )
    later = decode_frames(take, width=width, height=height, fps=1.0, crop=crop)[1:]
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


def pick_board(
    take: Path,
    boards: list[Path],
    *,
    max_frames: int = BOARD_LEAK_MAX_FRAMES,
) -> tuple[Path, BoardLeak]:
    """The drawing of the board the take opens on, of every drawing on the desk.

    A take compiled before its board was redrawn opens on the earlier drawing
    (Sweet Racket ep 4 t2, L-20261009-1: twelve frames of board v1, measured
    against the approved v2 only). Each drawing is measured; the one with the
    most board frames at the head wins, and on a tie the earlier one in
    ``boards`` (the approved board comes first) is kept.

    Parameters
    ----------
    take
        Video.
    boards
        Board images, the approved one first (:func:`creation.post.desk.board_versions`).
    max_frames
        Cap on leading frames counted.

    Returns
    -------
    tuple[Path, BoardLeak]
        The chosen board and its measurement.

    Raises
    ------
    ValueError
        When ``boards`` is empty.
    """

    if not boards:
        raise ValueError("no board to measure the take against")
    best: tuple[Path, BoardLeak] | None = None
    for board in boards:
        leak = measure_board_leak(take, board, max_frames=max_frames)
        if best is None or leak.frames > best[1].frames:
            best = (board, leak)
    assert best is not None
    return best


def opened_on_earlier_drawing(
    take: Path, boards: list[Path], *, max_frames: int = BOARD_LEAK_MAX_FRAMES
) -> Path | None:
    """The earlier drawing a take surely opens on, when it is not the approved board (``boards[0]``).

    Used when the server left the start as filmed because it was not sure it
    was the board: it measured against the board the take was compiled with
    or the current one, and a grid of an earlier drawing reads only partly
    like it (likeness 0.57-0.82 in L-20261009-1). The earlier drawing counts
    only when it measures more board frames than the approved board and frame
    0 is closer to it than to the approved board.

    Parameters
    ----------
    take
        Video.
    boards
        Board images, the approved one first.
    max_frames
        Cap on leading frames counted.

    Returns
    -------
    Path | None
        The earlier drawing, or ``None`` (the doubt stands).
    """

    if len(boards) < 2:
        return None
    approved = measure_board_leak(take, boards[0], max_frames=max_frames)
    chosen, leak = pick_board(take, boards[1:], max_frames=max_frames)
    if leak.frames <= approved.frames or not leak.psnr_db or not approved.psnr_db:
        return None
    if leak.psnr_db[0] <= approved.psnr_db[0]:
        return None
    return chosen


#: A cut at the head stays this far before the first line's onset (a frame at 24 fps).
SPEECH_GUARD_SECONDS = 0.042
#: The ``handles`` edit's source for the kit's own head cut.
HEAD_CUT_SOURCE = "kit-deboard"


def head_cut(
    frames: int, fps: float, *, first_speech: float | None, known: bool
) -> tuple[float | None, str]:
    """Where to cut a deboarded take's held head: at the first real frame, short of the first line.

    Parameters
    ----------
    frames
        Board frames the clones replaced.
    fps
        The take's frame rate.
    first_speech
        The first line's onset on the take as filmed (``None`` when no line plays).
    known
        Whether line times are known at all (captions, laid voices).

    Returns
    -------
    tuple[float | None, str]
        The cut (seconds, on a frame; ``None``: no cut) and the line saying why.
    """

    if frames <= 0:
        return None, "no board frames: nothing held at the head"
    want = frames / fps
    if not known:
        return None, (
            f"the {frames} cloned board frame(s) stay at the head ({want:.3f} s held still): no line times "
            "to check a cut against (no captions, no laid voice)"
        )
    if first_speech is None or first_speech - SPEECH_GUARD_SECONDS >= want - 1e-6:
        return round(want, 4), (
            f"held head cut: the {frames} cloned board frame(s) ({want:.3f} s) go, so frame 0 is the first real "
            "frame and the picture moves from the start"
        )
    keep = math.floor(max(0.0, first_speech - SPEECH_GUARD_SECONDS) * fps)
    if keep <= 0:
        return None, (
            f"the {frames} cloned board frame(s) stay at the head: the first line starts at {first_speech:.2f} s, "
            "under them; cutting would clip its first word"
        )
    return round(keep / fps, 4), (
        f"held head cut short: {keep} of {frames} cloned board frame(s) go; the first line starts at "
        f"{first_speech:.2f} s, under the rest"
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
