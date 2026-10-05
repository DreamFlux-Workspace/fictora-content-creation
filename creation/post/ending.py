"""How a reel or a joined episode ends: hard on the last frame, or (by choice) a freeze then black.

The default ending is ``hard``: the file ends on the last beat's final frame,
no tail hold, no picture fade, and the music bed stops with it (a click guard
of :data:`creation.post.mix.BED_FADE_OUT_SECONDS`, not a fade).

``freeze-black`` is a style a creator or operator picks (``--ending
freeze-black`` on ``reel`` and ``join``; never forced by genre, and it suits a
quiet episode as well as a loud one): the last frame holds for
:data:`FREEZE_SECONDS`, then a hard cut to black for :data:`BLACK_SECONDS`.
The sound stops hard on the peak frame, so the freeze lands as a beat of
silence; the black is silent too.
"""

from __future__ import annotations

from pathlib import Path

from creation.post.media import probe_video, run_ffmpeg
from creation.post.reel_plan import BLACK_SECONDS, ENDING_STYLES, FREEZE_SECONDS

#: The sound's last few milliseconds ramp down so the hard stop does not click.
CLICK_GUARD_SECONDS = 0.02


def check_ending(style: str) -> str:
    """``style`` when it is one of :data:`ENDING_STYLES`.

    Raises
    ------
    ValueError
        When it is not.
    """

    if style not in ENDING_STYLES:
        raise ValueError(f"--ending {style!r}: one of {', '.join(ENDING_STYLES)}")
    return style


def apply_ending(
    source: Path,
    out: Path,
    *,
    style: str,
    freeze: float = FREEZE_SECONDS,
    black: float = BLACK_SECONDS,
) -> Path:
    """Write ``source`` with its ending style into ``out`` (``hard`` copies the streams as they are).

    Parameters
    ----------
    source
        The finished file (marked, captioned).
    out
        New file.
    style
        ``hard`` or ``freeze-black``.
    freeze, black
        Seconds of the held last frame, then of black.

    Returns
    -------
    Path
        ``out``.
    """

    check_ending(style)
    info = probe_video(source)
    if style == "hard":
        run_ffmpeg(["-i", str(source), "-c", "copy", str(out)])
        return out
    fps = info.fps or 24.0
    total = info.duration_seconds + freeze + black
    video = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={freeze:.3f},"
        f"tpad=stop_mode=add:stop_duration={black:.3f}:color=black,fps={fps:g}[v]"
    )
    args = ["-i", str(source), "-filter_complex", video, "-map", "[v]"]
    if info.has_audio:
        guard = max(0.0, info.duration_seconds - CLICK_GUARD_SECONDS)
        args += [
            "-af",
            f"afade=t=out:st={guard:.3f}:d={CLICK_GUARD_SECONDS},apad=whole_dur={total:.3f}",
            "-map",
            "0:a",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    args += ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-r", f"{fps:g}"]
    args += ["-t", f"{total:.3f}", str(out)]
    run_ffmpeg(args)
    return out


__all__ = ["CLICK_GUARD_SECONDS", "apply_ending", "check_ending"]
