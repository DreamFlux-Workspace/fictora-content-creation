"""Mix one take: the show bed under the take's own sound, ducked under the voice, at a measured gain.

- The take's own audio (voice, and the SFX layer when it ran) gets one gain,
  measured so the mix lands near -18 LUFS (band -20 to -15): the take is
  measured, mixed, and corrected once when the bed moved it more than 1 LU.
- The bed loops to cover the picture, fades in over 1 s and out over 1.5 s.
- Ducking: by default a sidechain compressor keyed on the take's audio; with
  ``duck_db`` the bed drops by exactly that many dB inside the voice windows
  (ramped over 80 ms).
- One limiter at -1 dBFS. The picture is copied.
- A sound-effect cue whose loudest window sits more than 12 dB under the bed
  (the bed's own level at that moment, before ducking) is named in a ``!!``
  warning: it is mixed, but nobody will hear it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from creation.post.media import (
    LIMITER,
    measure_loudness,
    measure_rms_windows,
    probe_video,
    run_ffmpeg,
)

TARGET_LUFS = -18.0
LUFS_BAND = (-20.0, -15.0)
GAIN_RANGE_DB = (-8.0, 15.0)
GAIN_TOLERANCE_LU = 1.0
DUCK_DB_RANGE = (1.0, 30.0)
DUCK_RAMP_SECONDS = 0.08
VOICE_KEY_DB = -30.0
VOICE_WINDOW_SECONDS = 0.1
VOICE_MERGE_SECONDS = 0.6
BED_FADE_IN_SECONDS = 1.0
BED_FADE_OUT_SECONDS = 1.5
#: A cue this far under the bed is inaudible (a -31 dB RMS neck crack at +8 dB sat 21 dB under, SCP-173).
QUIET_CUE_DB = 12.0
#: Where the warning asks a raised cue to land: this far under the bed at most.
AUDIBLE_CUE_DB = 6.0
BED_WINDOW_SECONDS = 0.5
COMPRESSOR = "sidechaincompress=threshold=0.045:ratio=2.5:attack=60:release=600:makeup=1:level_sc=1"


def check_duck_db(duck_db: float | None) -> None:
    """Refuse a ducking depth outside 1-30 dB.

    Raises
    ------
    ValueError
        When out of range.
    """

    if duck_db is not None and not DUCK_DB_RANGE[0] <= duck_db <= DUCK_DB_RANGE[1]:
        raise ValueError(
            f"--duck-db must be {DUCK_DB_RANGE[0]:.0f}-{DUCK_DB_RANGE[1]:.0f} dB; got {duck_db}"
        )


def duck_expression(windows: list[tuple[float, float]], depth_db: float) -> str:
    """An ffmpeg ``volume`` filter that drops by exactly ``depth_db`` inside each window (ramped)."""

    if not windows:
        return "anull"
    floor = 10 ** (-abs(depth_db) / 20.0)
    ramp = DUCK_RAMP_SECONDS
    terms = []
    for start, end in windows:
        rise = f"min(1\\,max(0\\,(t-{start - ramp:.3f})/{ramp:.3f}))"
        fall = f"min(1\\,max(0\\,({end + ramp:.3f}-t)/{ramp:.3f}))"
        terms.append(f"1-{1 - floor:.6f}*{rise}*{fall}")
    gain = terms[0]
    for term in terms[1:]:
        gain = f"min({gain}\\,{term})"
    return f"volume='{gain}':eval=frame"


def voice_windows(source: Path, *, total: float) -> list[tuple[float, float]]:
    """Where the take speaks (RMS above -30 dB in 0.1 s windows), merged across pauses under 0.6 s."""

    spans = []
    for index, level in enumerate(
        measure_rms_windows(source, window_seconds=VOICE_WINDOW_SECONDS)
    ):
        if level > VOICE_KEY_DB:
            start = index * VOICE_WINDOW_SECONDS
            spans.append((start, min(total, start + VOICE_WINDOW_SECONDS)))
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] < VOICE_MERGE_SECONDS:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def pick_gain(take_lufs: float) -> float:
    """Gain that brings the take to the target, clamped to -8..+15 dB (0 for silence)."""

    if not math.isfinite(take_lufs):
        return 0.0
    return round(
        min(max(TARGET_LUFS - take_lufs, GAIN_RANGE_DB[0]), GAIN_RANGE_DB[1]), 1
    )


@dataclass(frozen=True)
class CueLevel:
    """One laid sound-effect cue as the mix sees it."""

    sound: str
    start: float
    seconds: float
    #: Loudest 0.5 s window as placed (rendered level + the cue's gain), before the take gain.
    peak_db: float
    #: The cue's own gain (cues go no higher than 0 dB).
    gain_db: float = 0.0


def quiet_cue_warnings(
    cues: Sequence[CueLevel],
    *,
    bed_levels: tuple[float, ...],
    bed_db: float,
    take_gain_db: float,
    window_seconds: float = BED_WINDOW_SECONDS,
) -> list[str]:
    """Name each cue that peaks more than :data:`QUIET_CUE_DB` under the bed where it plays.

    Parameters
    ----------
    cues
        The laid cues with their placed peaks.
    bed_levels
        The bed file's RMS per window (it loops under the picture).
    bed_db
        Bed gain in the mix.
    take_gain_db
        The take gain, which the cues ride on.
    window_seconds
        Length of one ``bed_levels`` window.

    Returns
    -------
    list[str]
        One ``!!`` line per inaudible cue, with the fix; empty when every cue is heard.
    """

    if not bed_levels:
        return []
    count = len(bed_levels)
    warnings: list[str] = []
    for cue in cues:
        first = int(cue.start / window_seconds)
        last = max(
            first,
            math.ceil((cue.start + max(cue.seconds, window_seconds)) / window_seconds)
            - 1,
        )
        bed = (
            max(bed_levels[index % count] for index in range(first, last + 1)) + bed_db
        )
        heard = cue.peak_db + take_gain_db
        gap = bed - heard
        if gap <= QUIET_CUE_DB:
            continue
        need = math.ceil(gap - AUDIBLE_CUE_DB)
        room = max(0, math.floor(-cue.gain_db))
        raise_by = min(need, room)
        fix = (
            f'raise it (`--sfx-adjust "{cue.sound}=+{raise_by}"`)'
            if raise_by > 0
            else "it is already at 0 dB"
        )
        if need > raise_by:
            fix += f"{' and' if raise_by > 0 else ';'} lower the bed about {need - raise_by} dB (`--bed-db`)"
        warnings.append(
            f"!! cue {cue.sound!r} @{cue.start:.2f}s peaks {gap:.0f} dB under the music bed "
            f"({heard:.0f} dB vs {bed:.0f} dB): nobody will hear it. {fix[0].upper()}{fix[1:]}."
        )
    return warnings


@dataclass(frozen=True)
class MixResult:
    """The mix and how it was levelled."""

    output: Path
    take_lufs: float
    gain_db: float
    mix_lufs: float
    passes: int
    ducking: str
    bed: Path | None
    warnings: tuple[str, ...] = ()

    def one_line(self) -> str:
        """Operator line."""

        band = (
            "in band"
            if LUFS_BAND[0] <= self.mix_lufs <= LUFS_BAND[1]
            else "OUT OF BAND"
        )
        bed = "" if self.bed else "; NO MUSIC BED"
        return (
            f"auto take gain {self.gain_db:+.1f} dB (take {self.take_lufs:.1f} LUFS) -> mix {self.mix_lufs:.1f} LUFS "
            f"({band}, target {TARGET_LUFS:.0f}), {self.passes} pass(es); ducking {self.ducking}{bed}"
        ) + "".join(f"\n{line}" for line in self.warnings)


def _mix_once(
    take: Path,
    out: Path,
    *,
    gain_db: float,
    bed: Path | None,
    bed_db: float,
    total: float,
    windows: list[tuple[float, float]] | None,
    duck_db: float | None,
) -> None:
    inputs = ["-i", str(take)]
    graph = [f"[0:a]aresample=48000,volume={gain_db:+.1f}dB[take]"]
    if bed is None:
        graph.append(f"[take]{LIMITER}[a]")
    else:
        inputs += ["-stream_loop", "-1", "-i", str(bed)]
        fade_out = max(0.0, total - BED_FADE_OUT_SECONDS)
        graph.append(
            f"[1:a]aresample=48000,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={bed_db:+.1f}dB,"
            f"afade=t=in:st=0:d={BED_FADE_IN_SECONDS},afade=t=out:st={fade_out:.3f}:d={BED_FADE_OUT_SECONDS}[bed]"
        )
        if duck_db is None:
            graph.append("[take]asplit=2[tk][key]")
            graph.append(f"[bed][key]{COMPRESSOR}[bd]")
            front = "[tk]"
        else:
            graph.append(f"[bed]{duck_expression(windows or [], duck_db)}[bd]")
            front = "[take]"
        graph.append(
            f"{front}[bd]amix=inputs=2:duration=first:normalize=0,{LIMITER}[a]"
        )
    run_ffmpeg(
        [*inputs, "-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", str(out)]
    )  # fmt: skip


def mix_take(
    take: Path,
    out: Path,
    *,
    bed: Path | None,
    bed_db: float = -16.5,
    duck_db: float | None = None,
    voice_source: Path | None = None,
    cues: Sequence[CueLevel] = (),
) -> MixResult:
    """Mix ``take`` with ``bed`` into ``out`` at a measured take gain.

    Parameters
    ----------
    take
        Take with its own audio (after the SFX layer, when it ran).
    out
        New file; must not exist.
    bed
        Show bed, or ``None`` (the take is then NOT DONE).
    bed_db
        Bed level.
    duck_db
        Exact duck depth inside the voice windows; ``None`` uses the sidechain compressor.
    voice_source
        Audio to find the voice windows in (the take before SFX); default ``take``.
    cues
        The sound-effect cues on ``take``; each one far under the bed is warned about (never refused).

    Returns
    -------
    MixResult
        Output and levels.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    ValueError
        When ``duck_db`` is out of range.
    """

    check_duck_db(duck_db)
    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    info = probe_video(take)
    if not info.has_audio:
        raise ValueError(f"{take.name} has no audio to mix")
    total = info.duration_seconds
    windows = (
        voice_windows(voice_source or take, total=total)
        if duck_db is not None
        else None
    )
    take_lufs = measure_loudness(take)
    gain = pick_gain(take_lufs)
    kwargs = {
        "bed": bed,
        "bed_db": bed_db,
        "total": total,
        "windows": windows,
        "duck_db": duck_db,
    }
    _mix_once(take, out, gain_db=gain, **kwargs)  # type: ignore[arg-type]
    mixed = measure_loudness(out)
    passes = 1
    miss = TARGET_LUFS - mixed
    if math.isfinite(miss) and abs(miss) > GAIN_TOLERANCE_LU:
        corrected = round(min(max(gain + miss, GAIN_RANGE_DB[0]), GAIN_RANGE_DB[1]), 1)
        if corrected != gain:
            out.unlink()
            gain = corrected
            _mix_once(take, out, gain_db=gain, **kwargs)  # type: ignore[arg-type]
            mixed = measure_loudness(out)
            passes = 2
    if duck_db is None:
        ducking = "sidechain compressor" if bed else "none"
    else:
        ducking = f"{duck_db:.0f} dB in {len(windows or [])} voice window(s)"
    warnings: list[str] = []
    if bed is not None and cues:
        levels = measure_rms_windows(bed, window_seconds=BED_WINDOW_SECONDS)
        warnings = quiet_cue_warnings(
            cues, bed_levels=levels, bed_db=bed_db, take_gain_db=gain
        )
    return MixResult(out, take_lufs, gain, mixed, passes, ducking, bed, tuple(warnings))
