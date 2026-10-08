"""Give a stretch of a take's voice a source: intercom, phone or radio. Local ffmpeg, $0.

An off-screen voice played over another character's face is heard as that
face speaking. A source treatment (a speaker grille, a phone line, a radio)
tells the viewer where the voice comes from. ``voice-fx`` treats only the
chosen range of the file's own audio:

- band-pass (the preset's band, about 450-2800 Hz for the intercom),
- compression, then a soft clip,
- a short metallic echo (intercom) and faint static under the range,
- the treated range matched to the level it had before (RMS), so the mix
  around it does not jump, with 20 ms ramps at both edges.

The picture is copied. It writes ``<name>-<preset>-vN.<ext>`` next to the
file and never overwrites anything. Several ranges (``--range`` repeated) are
treated in one pass into one file, each matched to its own level.
"""

from __future__ import annotations

import math
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from creation.ops.folder import next_versioned_path
from creation.post.media import ffmpeg_bin, media_duration, probe_video, run_ffmpeg

#: Ramp at each edge of the treated range, seconds.
EDGE_SECONDS = 0.02
#: Gain matching never moves the treated range more than this, dB.
MAX_MATCH_DB = 24.0


@dataclass(frozen=True)
class Preset:
    """One source treatment: an ffmpeg audio chain plus a static level."""

    chain: str
    static: float


PRESETS: dict[str, Preset] = {
    "intercom": Preset(
        "highpass=f=450,highpass=f=450,lowpass=f=2800,lowpass=f=2800,"
        "acompressor=threshold=0.08:ratio=6:attack=5:release=60:makeup=2,"
        "asoftclip=type=tanh,aecho=0.8:0.55:13|21:0.32|0.18",
        0.004,
    ),
    "phone": Preset(
        "highpass=f=300,highpass=f=300,lowpass=f=3400,lowpass=f=3400,"
        "acompressor=threshold=0.1:ratio=4:attack=5:release=80:makeup=2,asoftclip=type=tanh",
        0.002,
    ),
    "radio": Preset(
        "highpass=f=500,highpass=f=500,lowpass=f=2600,lowpass=f=2600,"
        "acompressor=threshold=0.06:ratio=8:attack=3:release=50:makeup=3,asoftclip=type=atan",
        0.008,
    ),
}


def parse_range(raw: str) -> tuple[float, float]:
    """``"0-3.5"`` -> ``(0.0, 3.5)``.

    Raises
    ------
    ValueError
        Not ``a-b`` seconds with ``a < b``.
    """

    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*", raw)
    if not match or float(match[1]) >= float(match[2]):
        raise ValueError(
            f"--range must be START-END in seconds with START < END (like 0-3.5); got {raw!r}"
        )
    return float(match[1]), float(match[2])


def range_rms_db(
    source: Path, start: float, end: float, chain: str | None = None
) -> float:
    """RMS level (dB) of ``source``'s audio between ``start`` and ``end``, after ``chain`` if given.

    The chain runs on the whole file first, so its echo and compressor state
    are what the range really gets.
    """

    trim = f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS"
    graph = f"aresample=48000,pan=mono|c0=c0,{chain + ',' if chain else ''}{trim},astats=metadata=0"
    result = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(source), "-vn", "-af", graph, "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    found = re.findall(r"RMS level dB:\s*(-?[0-9.]+|-inf)", result.stderr)
    if result.returncode != 0 or not found:
        return -math.inf
    value = found[-1]
    return -math.inf if value == "-inf" else float(value)


def match_gain_db(before: float, after: float) -> float:
    """dB that brings the treated range back to the level it had; 0 when either side is silent."""

    if not (math.isfinite(before) and math.isfinite(after)):
        return 0.0
    return max(-MAX_MATCH_DB, min(MAX_MATCH_DB, before - after))


def apply_voice_fx(
    source: Path,
    *,
    preset: str,
    start: float | None = None,
    end: float | None = None,
    ranges: Sequence[tuple[float, float]] = (),
    out: Path | None = None,
) -> Path:
    """Treat ``source``'s audio in one or more ranges with ``preset``; write one new file.

    Parameters
    ----------
    source
        A take (video with audio) or an audio file.
    preset
        ``intercom``, ``phone`` or ``radio``.
    start, end
        One range treated, seconds (or give ``ranges``).
    ranges
        Every range treated, seconds (``voice-fx --range`` repeated): one
        pass, one output file. Each range is level-matched on its own.
    out
        Where to write; default ``<stem>-<preset>-vN<suffix>`` next to ``source``.

    Returns
    -------
    Path
        The new file.

    Raises
    ------
    ValueError
        Unknown preset, no range, a range outside the file, or ranges that overlap.
    FileExistsError
        When ``out`` exists (post never overwrites).
    """

    if preset not in PRESETS:
        raise ValueError(f"--preset is one of {', '.join(PRESETS)}; got {preset!r}")
    spans = list(ranges) or (
        [(start, end)] if start is not None and end is not None else []
    )
    if not spans:
        raise ValueError("voice-fx needs a --range")
    total = media_duration(source)
    spans = sorted(spans)
    for (_, first_end), (second_start, _) in zip(spans, spans[1:]):
        if second_start < first_end:
            raise ValueError(
                f"--range {second_start:g}-… starts before the range ahead of it ends ({first_end:g}s); "
                "give ranges that do not overlap"
            )
    for begin, _ in spans:
        if begin >= total:
            raise ValueError(
                f"the range starts at {begin:.2f}s but {source.name} is {total:.2f}s long"
            )
    spans = [(begin, min(finish, total)) for begin, finish in spans]
    if out is None:
        out = next_versioned_path(
            source.parent, f"{source.stem}-{preset}", source.suffix
        )
    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    chosen = PRESETS[preset]
    windows = [
        f"clip((t-{a})/{EDGE_SECONDS},0,1)*clip(({b}-t)/{EDGE_SECONDS},0,1)"
        for a, b in spans
    ]
    # Each range is brought back to its own level: the wet branch's gain is a sum over the windows.
    gains = [
        10 ** (match_gain_db(range_rms_db(source, a, b), range_rms_db(source, a, b, chosen.chain)) / 20)
        for a, b in spans
    ]  # fmt: skip
    inside = "+".join(f"({w})" for w in windows)
    wet_level = "+".join(f"{g:.4f}*({w})" for g, w in zip(gains, windows))
    graph = ";".join(
        [
            "[0:a]aresample=48000,asplit=2[dryin][wetin]",
            f"[dryin]volume='1-({inside})':eval=frame[dry]",
            f"[wetin]{chosen.chain},volume='{wet_level}':eval=frame[wet]",
            f"anoisesrc=color=pink:amplitude={chosen.static}:sample_rate=48000:duration={total:.3f},"
            f"volume='{inside}':eval=frame[static]",
            "[dry][wet][static]amix=inputs=3:normalize=0:duration=first[a]",
        ]
    )
    has_video = source.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv"}
    if has_video:
        probe_video(source)
        codecs = [
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ]
    else:
        codecs = ["-map", "[a]"] + (
            ["-c:a", "aac", "-b:a", "192k"] if source.suffix.lower() == ".m4a" else []
        )
    run_ffmpeg(["-i", str(source), "-filter_complex", graph, *codecs, str(out)])
    return out


__all__ = ["PRESETS", "apply_voice_fx", "match_gain_db", "parse_range", "range_rms_db"]
