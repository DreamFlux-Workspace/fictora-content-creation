"""Review: a free, local, numbers-only read of one take (raw or finished), before anyone judges it.

``fictora-produce review`` measures and prints one block the producer pastes
into the verdict. It is a read, never a gate: every section is marked ✓ or ⚠
with the threshold it used (``–`` when there was nothing to measure), and the
command exits 0 whatever it finds.

1. **Loudness** - integrated LUFS (:func:`creation.post.media.measure_loudness`)
   and true peak (``ebur128=peak=true``). A raw take is only checked for being
   effectively silent (below ``SILENT_BELOW_LUFS``: it needs cues); a finished
   take must sit in the mix band and under ``TRUE_PEAK_MAX_DBTP``. When the
   mix's un-ducked, ducked and key bus WAVs sit beside the file or its mix
   (``finish`` writes them beside ``take-epNN-tK-mix-vN.mp4``) the duck depth
   under the voice is measured from them too.
2. **Cuts** - hard cuts from the ``tblend`` difference trace
   (:func:`creation.post.edit.measure_cuts`, the same detector ``soften`` uses),
   compared with the shot changes in the saved take facts
   (``epNN/api/take-facts-epNN-tK-vN.json``): a planned shot change with no cut
   near it, and a cut no shot change explains, are both named.
3. **Frozen / stacked frames** - at 8 fps: a run of frames that do not change
   (a stall), and the stacked double frame (top half equals bottom half,
   the internal kit's top-vs-bottom RMSE).
4. **Board** - every frame is compared with the approved board by PSNR, the
   same measurement ``deboard`` uses, so a board frame anywhere in the take is
   found, not only at the head. Head frames on a raw take are what ``finish``
   removes; on a deboarded or finished file any board frame is a fault.
   **Text** - drawn subtitles: :func:`creation.post.take_text.check_take_text`
   reads sampled frames of the raw take (a finished file carries house
   captions, so its raw take is read) with ``tesseract`` when installed, and
   saves a crop sheet (``takes/text-check-epNN-tK-vN.png``) of what it found.
   Without tesseract the section says the check was skipped.
5. **Lines** - :func:`creation.episode_commands.run_check_lines` (were the
   approved lines in the take's instructions, from the take facts), and when a
   Whisper transcript is on the desk (``--words-json``, a saved
   ``take-…-words-vN.json``, or ``--transcribe``) which approved lines were
   heard, matched by :func:`creation.post.whisper.line_windows`. A Japanese
   line matched on the server's readings is reported ``(by sound, NN%)``. The
   ``check-lines`` output is folded in as it prints it (each line's shot and
   board row; an ``!!`` on-screen speaker out of frame marks the section ⚠).
   Take 1's first line starting after 0.5 s is flagged the same way: trim the head.
6. **People** - the head count the take facts expect per shot (``shots[].people``,
   newer servers) with the names from the story's cast, and the by-eye check
   for one person drawn twice (no face detector: a human counts). ``–`` always;
   one line says so when the server sent no head counts.
7. **Safe zones** (finished files only) - the caption safe-zone check
   (:func:`creation.post.safe_zones.run_review`, unchanged): the caption box on
   sampled frames against the covered zones and the caption band, with the zone
   sheet and its JSON written beside the take for the face check by eye.
8. **Opening** (take 1 of every episode, and every reel) - the first second
   (:mod:`creation.post.opening`): a dark frame 0 (and first 0.5 s), a static
   head (no meaningful motion in the first second), and no face on frame 0
   (the local face detector when installed, else the take facts' head count
   for the first shot; not checked when the opening beat has no spoken line,
   or for a reel when no caption plays in its first second). Warnings only.
9. **Ending** (reels) - more than 0.3 s settled after the last caption or the
   last motion: the cut should land on the peak.

With no ``--take-file`` (alias ``--file``) it reads the newest finished file for
the take, else the newest raw take, and the block's first line says which.

It writes nothing but an optional transcript and the text check's crop sheet.
Everything is local and free except ``--transcribe``, which asks the server for
a Whisper transcript of the take's stored URL (a few cents; nothing is uploaded).
The compiled prompt is never read. The internal kit's per-row forbidden-elements
checklist is not here: it needs the server's image-compile rules.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from PIL import Image

from creation.post.take_timeline import facts_shift_s, server_board_frames
from creation.post.deboard import (
    BOARD_LEAK_MARGIN_DB,
    LEAK_ANALYSIS_SIZE,
    _psnr,
    measure_board_leak,
)
from creation.post.edit import FRAME_DIFF_CUT_THRESHOLD, measure_cuts
from creation.post.finish_record import record_for_file
from creation.post.media import (
    MediaToolError,
    decode_frames,
    ffmpeg_bin,
    measure_loudness,
    measure_rms_windows,
    probe_video,
)
from creation.post.mix import LUFS_BAND, TARGET_LUFS
from creation.harness_rules import late_opening_line, short_line_hold

#: Below this a take is effectively silent: it needs cues (internal kit).
SILENT_BELOW_LUFS = -30.0
#: A finished file's true peak must not pass this (the streaming platforms' ceiling).
TRUE_PEAK_MAX_DBTP = -1.0
#: Measured duck depth under the voice, when no depth was asked (internal kit, sidechain ducker).
DUCK_TARGET_DB = (6.0, 12.0)
#: Bus verification windows, and the key level that counts as voice (internal kit).
DUCK_WINDOW_SECONDS = 0.1
DUCK_KEY_ACTIVE_DB = -30.0
#: The key must have been quiet this long before a window counts as "outside" the duck.
DUCK_RELEASE_SECONDS = 0.6

#: A detected cut within this of a planned shot change is that shot change.
CUT_MATCH_SECONDS = 0.5
#: Two hard cuts closer than this are a flash or a stutter (a shot a few frames long), not two shots.
CLOSE_CUT_SECONDS = 0.25
#: A line with fewer words than this is never a re-film call on the transcript alone ("check by ear").
SHORT_LINE_WORDS = 3

#: Frozen / stacked analysis: 8 fps at 96x168 (internal kit's stack score).
STACK_FPS = 8.0
STACK_SIZE = (96, 168)
#: Stacked double frames score about 0.16 top-vs-bottom RMSE (0-1); clean wides 0.24 and up.
STACK_THRESHOLD = 0.2
#: A stacked stretch longer than this is a fault to look at full size.
STACK_MIN_SECONDS = 0.3
#: A "stacked" stretch over at least this share of the take is the set (dark, even, symmetric), not a
#: split frame: a real stacked double frame comes and goes. Reported as a note, not a warning.
STACK_WHOLE_TAKE_SHARE = 0.9
#: Frame-to-frame RMSE (0-1) below which the picture did not move (real H3 takes: lowest pair ~0.0035).
FREEZE_THRESHOLD = 0.002
#: A still stretch this long or longer is a stall to look at (a deboarded head is at most 12 frames = 0.5 s).
FREEZE_MIN_SECONDS = 0.6

#: File steps that come after the mix: such a file is a finished take (``final``/``captions``: adopted desks).
FINISHED_STEPS = (
    "mix",
    "cap",
    "sokii",
    "trim",
    "tempo",
    "final",
    "captions",
    "reel",
    "handles",
)
#: File steps after which the head board frames should be gone.
DEBOARDED_STEPS = (
    "deboard",
    "soften",
    "freeze",
    "colour",
    "sfx",
    "cues",
    "voice",
    *FINISHED_STEPS,
)
#: Steps that move times: the take facts' shot times no longer hold.
#: A reel (``fictora-produce reel``) is cut and reordered: its times are not the take facts'.
RETIMED_STEPS = ("trim", "tempo", "reel")

OK, WARN, NONE = "✓", "⚠", "–"


@dataclass
class Section:
    """One measured section of the review.

    Parameters
    ----------
    name
        ``Loudness``, ``Cuts``, ``Frames``, ``Board`` or ``Lines``.
    status
        ``✓``, ``⚠`` or ``–`` (nothing to measure).
    summary
        The one-line reading.
    threshold
        The thresholds used, as printed.
    details
        Further lines under the summary.
    data
        The numbers, for ``--json``.
    """

    name: str
    status: str
    summary: str
    threshold: str
    details: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def lines(self) -> list[str]:
        """The printed lines of this section."""

        return [
            f"{self.status} {self.name}: {self.summary}  [{self.threshold}]",
            *(f"    {d}" for d in self.details),
        ]

    def as_json(self) -> dict[str, Any]:
        """JSON-able form."""

        return {"name": self.name, "status": self.status, "summary": self.summary, "threshold": self.threshold,
                "details": list(self.details), **self.data}  # fmt: skip


@dataclass
class TakeReview:
    """Every section of one review.

    Parameters
    ----------
    take
        The file read.
    kind
        ``raw`` or ``finished``.
    duration_seconds, fps
        Probe.
    sections
        The sections, in order.
    """

    take: Path
    kind: str
    duration_seconds: float
    fps: float
    sections: list[Section]
    episode: int = 1
    take_id: str = "t1"
    chosen: str = "given with --take-file"

    @property
    def warnings(self) -> int:
        """Sections marked ⚠."""

        return sum(1 for section in self.sections if section.status == WARN)

    def block(self) -> str:
        """The block the producer pastes into the verdict."""

        head = (
            f"Review ep{self.episode:02d} {self.take_id}: {self.take.name} ({self.kind}, "
            f"{self.duration_seconds:.2f} s at {self.fps:g} fps; {self.chosen})"
        )
        rows = [head]
        for section in self.sections:
            rows += section.lines()
        verdict = (
            f"{self.warnings} section(s) to look at"
            if self.warnings
            else "nothing measured out of line"
        )
        rows.append(
            f"Numbers only: {verdict}. Watch the take, then say Use it or Change this."
        )
        return "\n".join(rows)

    def as_json(self) -> dict[str, Any]:
        """JSON-able form."""

        return {
            "take": str(self.take),
            "episode": self.episode,
            "take_id": self.take_id,
            "kind": self.kind,
            "chosen": self.chosen,
            "duration_seconds": round(self.duration_seconds, 3),
            "fps": self.fps,
            "warnings": self.warnings,
            "sections": [section.as_json() for section in self.sections],
        }


def _steps(take: Path) -> set[str]:
    return set(re.split(r"-", take.stem))


def take_kind(take: Path) -> str:
    """``finished`` when the file name carries a step after the mix, else ``raw``."""

    return "finished" if _steps(take) & set(FINISHED_STEPS) else "raw"


def _times(values: tuple[float, ...] | list[float]) -> str:
    return ", ".join(f"{value:.2f}s" for value in values) or "none"


# --- 1. Loudness ----------------------------------------------------------------------------------------


def measure_true_peak(path: Path) -> float:
    """True peak (dBTP) of a file's audio; ``-inf`` for silence.

    Raises
    ------
    MediaToolError
        When ffmpeg fails.
    """

    result = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true",
         "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(
            f"true-peak scan failed on {path.name}: {result.stderr.strip()[-300:]}"
        )
    summary = result.stderr.rsplit("True peak:", 1)
    found = (
        re.findall(r"Peak:\s+(-?[0-9.]+|-inf)\s+dBFS", summary[-1])
        if len(summary) == 2
        else []
    )
    if not found or found[-1] == "-inf":
        return -math.inf
    return float(found[-1])


@dataclass(frozen=True)
class DuckReading:
    """Duck depth from the mix buses: median reduction under the voice and where the voice is quiet."""

    under_db: float
    outside_db: float
    windows: int

    def one_line(self) -> str:
        """``ducking 8.9 dB under the voice (31 windows), 0.1 dB outside``."""

        return (
            f"ducking {self.under_db:.1f} dB under the voice ({self.windows} windows), "
            f"{self.outside_db:.1f} dB outside"
        )


def find_duck_buses(take: Path) -> tuple[Path, Path, Path] | None:
    """The un-ducked, ducked and key bus WAVs of the mix behind ``take``, when they are on the desk.

    Looked for beside the file itself, then beside the newest ``take-epNN-tK-…mix-vN.mp4`` of the same take.
    """

    def beside(path: Path) -> tuple[Path, Path, Path] | None:
        stem = path.with_suffix("")
        found = tuple(Path(f"{stem}-{bus}-bus.wav") for bus in ("raw", "ducked", "key"))
        return (
            (found[0], found[1], found[2]) if all(p.is_file() for p in found) else None
        )

    direct = beside(take)
    if direct is not None:
        return direct
    base = re.match(r"(take-ep\d+-t\d+)", take.name)
    if base is None:
        return None
    mixes = sorted(
        take.parent.glob(f"{base.group(1)}-*mix-v*.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for mix in mixes:
        found = beside(mix)
        if found is not None:
            return found
    return None


def measure_duck(raw_bus: Path, ducked_bus: Path, key_bus: Path) -> DuckReading:
    """Diff the un-ducked and ducked buses on the same 0.1 s windows (the internal kit's verification).

    Returns
    -------
    DuckReading
        Median reduction where the key speaks, and where it has been quiet for the release time.
    """

    raw = np.array(measure_rms_windows(raw_bus, window_seconds=DUCK_WINDOW_SECONDS))
    ducked = np.array(
        measure_rms_windows(ducked_bus, window_seconds=DUCK_WINDOW_SECONDS)
    )
    key = np.array(measure_rms_windows(key_bus, window_seconds=DUCK_WINDOW_SECONDS))
    size = min(len(raw), len(ducked), len(key))
    raw, ducked, key = raw[:size], ducked[:size], key[:size]
    audible = raw > -60.0
    diff = raw - ducked
    voiced = (key > DUCK_KEY_ACTIVE_DB) & audible
    silent_key = key < DUCK_KEY_ACTIVE_DB - 15.0
    settle = int(math.ceil(DUCK_RELEASE_SECONDS / DUCK_WINDOW_SECONDS))
    settled = np.array(
        [silent_key[max(0, i - settle) : i + 1].all() for i in range(size)], dtype=bool
    )
    quiet = settled & audible
    under = float(np.median(diff[voiced])) if voiced.any() else 0.0
    outside = float(np.median(diff[quiet])) if quiet.any() else 0.0
    return DuckReading(round(under, 1), round(outside, 1), int(voiced.sum()))


def loudness_section(take: Path, kind: str, *, has_audio: bool = True) -> Section:
    """Integrated LUFS, true peak and (finished, with bus files) duck depth."""

    if not has_audio:
        threshold = (
            f"raw: silent below {SILENT_BELOW_LUFS:.0f} LUFS"
            if kind == "raw"
            else "finished: has a sound track"
        )
        return Section(
            "Loudness",
            WARN,
            "no audio track in the file",
            threshold,
            data={"lufs": None},
        )
    lufs = measure_loudness(take)
    peak = measure_true_peak(take)
    lufs_text = f"{lufs:.1f} LUFS" if math.isfinite(lufs) else "silent"
    peak_text = f"{peak:.1f} dBTP" if math.isfinite(peak) else "no peak"
    faults: list[str] = []
    details: list[str] = []
    data: dict[str, Any] = {
        "lufs": lufs if math.isfinite(lufs) else None,
        "true_peak_dbtp": peak if math.isfinite(peak) else None,
    }
    if kind == "raw":
        threshold = f"raw: silent below {SILENT_BELOW_LUFS:.0f} LUFS"
        if not math.isfinite(lufs) or lufs < SILENT_BELOW_LUFS:
            faults.append("effectively silent: build cues")
    else:
        low, high = LUFS_BAND
        threshold = (
            f"finished: {low:.0f} to {high:.0f} LUFS (target {TARGET_LUFS:.0f}); "
            f"true peak ≤ {TRUE_PEAK_MAX_DBTP:.1f} dBTP"
        )
        if not math.isfinite(lufs) or not low <= lufs <= high:
            faults.append("outside the mix band")
        if math.isfinite(peak) and peak > TRUE_PEAK_MAX_DBTP:
            faults.append("true peak over the ceiling: listen for clipping")
        buses = find_duck_buses(take)
        if buses is None:
            details.append(
                "duck depth: not measured (no mix bus files beside it or its mix; finish again to write them)"
            )
            data["duck"] = None
        else:
            duck = measure_duck(*buses)
            low_d, high_d = DUCK_TARGET_DB
            flag = (
                ""
                if low_d <= duck.under_db <= high_d
                else f"  OUTSIDE {low_d:.0f}-{high_d:.0f} dB"
            )
            details.append(f"{duck.one_line()} (buses `{buses[0].name}`){flag}")
            threshold += f"; duck {low_d:.0f}-{high_d:.0f} dB under the voice"
            data["duck"] = {
                "under_db": duck.under_db,
                "outside_db": duck.outside_db,
                "windows": duck.windows,
            }
            if flag:
                faults.append("duck depth out of band")
    summary = f"{lufs_text}, true peak {peak_text}" + (
        f": {'; '.join(faults)}" if faults else ""
    )
    return Section(
        "Loudness", WARN if faults else OK, summary, threshold, details, data
    )


# --- 2. Cuts ---------------------------------------------------------------------------------------------


def planned_shot_changes(facts: Mapping[str, Any]) -> tuple[float, ...] | None:
    """Shot-change times from take facts (each shot's start after the first), or ``None`` when no shots."""

    body = facts.get("take_facts", facts)
    shots = [
        s
        for s in body.get("shots") or []
        if isinstance(s, Mapping) and s.get("start_seconds") is not None
    ]
    if not shots:
        return None
    ordered = sorted(shots, key=lambda s: float(s["start_seconds"]))
    return tuple(round(float(s["start_seconds"]), 3) for s in ordered[1:])


def compare_cuts(
    cuts: tuple[float, ...],
    planned: tuple[float, ...],
    *,
    tolerance: float = CUT_MATCH_SECONDS,
) -> tuple[list[float], list[float]]:
    """Planned shot changes with no cut near them, and cuts no planned change explains.

    Each planned change takes at most one cut, the nearest within ``tolerance``.

    Returns
    -------
    tuple[list[float], list[float]]
        ``(missing, extra)``.
    """

    free = list(cuts)
    missing: list[float] = []
    for change in planned:
        near = [cut for cut in free if abs(cut - change) <= tolerance]
        if not near:
            missing.append(change)
            continue
        free.remove(min(near, key=lambda cut: abs(cut - change)))
    return missing, free


def close_cuts(
    cuts: tuple[float, ...], *, within: float = CLOSE_CUT_SECONDS
) -> list[tuple[float, float]]:
    """Pairs of consecutive hard cuts less than ``within`` seconds apart (a flash or a stutter).

    Parameters
    ----------
    cuts
        Hard-cut times in seconds.
    within
        The gap below which two cuts are one flash (default 0.25 s).

    Returns
    -------
    list[tuple[float, float]]
        ``(first, second)`` per close pair, in time order.
    """

    ordered = sorted(cuts)
    return [
        (round(a, 3), round(b, 3))
        for a, b in zip(ordered, ordered[1:])
        if b - a < within - 1e-9
    ]


def _close_cut_lines(pairs: list[tuple[float, float]]) -> list[str]:
    return [
        f"!! two hard cuts {b - a:.2f} s apart at {a:.2f}s and {b:.2f}s: a shot a few frames long reads as "
        "a flash or a stutter. Watch it full size; trim past it or soften the cut"
        for a, b in pairs
    ]


def cuts_section(
    take: Path,
    *,
    head_board_frames: int,
    facts: Mapping[str, Any] | None,
    retimed: bool,
    retimed_by: str | None = None,
) -> Section:
    """Hard cuts from the ``tblend`` trace, against the take facts' shot changes."""

    cuts = measure_cuts(take, skip_head_frames=head_board_frames)
    threshold = (
        f"tblend mean luma diff ≥ {FRAME_DIFF_CUT_THRESHOLD:g}; "
        f"matched to shot changes within ±{CUT_MATCH_SECONDS:g} s"
    )
    data: dict[str, Any] = {
        "cuts": list(cuts),
        "planned": None,
        "missing": [],
        "extra": [],
    }
    flashes = close_cuts(cuts)
    data["close_cuts"] = [list(pair) for pair in flashes]
    flash_lines = _close_cut_lines(flashes)
    found = f"{len(cuts)} hard cut(s)" + (f" at {_times(cuts)}" if cuts else "")
    planned = planned_shot_changes(facts) if facts is not None else None
    if planned is None:
        why = (
            "no take facts on the desk"
            if facts is None
            else "the take facts list no shots"
        )
        return Section(
            "Cuts",
            WARN if flash_lines else NONE if not cuts else OK,
            f"{found}; not compared ({why})",
            threshold,
            flash_lines,
            data=data,
        )
    data["planned"] = list(planned)
    if retimed:
        why = retimed_by or "a trim or tempo moved the times off the take facts"
        return Section(
            "Cuts",
            WARN if flash_lines else OK,
            f"{found}; not compared ({why})",
            threshold,
            flash_lines,
            data=data,
        )
    missing, extra = compare_cuts(cuts, planned)
    data["missing"], data["extra"] = missing, extra
    summary = f"{found}; take facts plan {len(planned)} shot change(s)" + (
        f" at {_times(planned)}" if planned else ""
    )
    details = [
        f"no hard cut near the planned shot change at {t:.2f}s (a camera move, or a shot that never came)"
        for t in missing
    ]
    details += [
        f"extra cut at {t:.2f}s the take facts do not plan (a forced cut or an H3 cell seam: soften it)"
        for t in extra
    ]
    details += flash_lines
    return Section(
        "Cuts",
        WARN if missing or extra or flash_lines else OK,
        summary,
        threshold,
        details,
        data,
    )


# --- 3. Frozen / stacked frames --------------------------------------------------------------------------


def _runs(mask: npt.NDArray[np.bool_], step: float) -> list[tuple[float, float]]:
    """Consecutive true samples as ``(start, end)`` times of their first and last sample."""

    runs: list[list[float]] = []
    for index in np.where(mask)[0]:
        time = float(index) * step
        if runs and time - runs[-1][1] <= step + 1e-6:
            runs[-1][1] = time
        else:
            runs.append([time, time])
    return [(round(a, 3), round(b, 3)) for a, b in runs]


def measure_frames(
    take: Path,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]], float, float]:
    """Frozen and stacked stretches at 8 fps.

    Returns
    -------
    tuple
        ``(frozen, stacked, lowest frame-to-frame RMSE, lowest top-vs-bottom RMSE)``: each stretch is
        ``(start, end)`` in seconds, already filtered to the ones long enough to report.
    """

    frozen, stacked, lowest_move, lowest_stack, _ = _measure_frames(take)
    return frozen, stacked, lowest_move, lowest_stack


def _measure_frames(
    take: Path,
) -> tuple[
    list[tuple[float, float]],
    list[tuple[float, float]],
    float,
    float,
    npt.NDArray[np.float64],
]:
    """:func:`measure_frames` plus the top-vs-bottom score of every 8 fps sample."""

    width, height = STACK_SIZE
    frames = decode_frames(take, width=width, height=height, fps=STACK_FPS) / 255.0
    step = 1.0 / STACK_FPS
    half = height // 2
    stack_scores = np.sqrt(
        ((frames[:, :half] - frames[:, half : half * 2]) ** 2).mean(axis=(1, 2, 3))
    )
    stacked = [
        r
        for r in _runs(stack_scores < STACK_THRESHOLD, step)
        if r[1] - r[0] + step > STACK_MIN_SECONDS
    ]
    if len(frames) > 1:
        moves = np.sqrt(((frames[1:] - frames[:-1]) ** 2).mean(axis=(1, 2, 3)))
        # A still pair (i, i+1) freezes both samples: the stretch runs from sample i to sample i+1.
        still = [(a, b + step) for a, b in _runs(moves < FREEZE_THRESHOLD, step)]
        frozen = [
            (round(a, 3), round(b, 3))
            for a, b in still
            if b - a >= FREEZE_MIN_SECONDS - 1e-6
        ]
        lowest_move = float(moves.min())
    else:
        frozen, lowest_move = [], 1.0
    lowest_stack = float(stack_scores.min()) if stack_scores.size else 1.0
    return frozen, stacked, round(lowest_move, 4), round(lowest_stack, 3), stack_scores


def frames_section(take: Path, *, steps: set[str]) -> Section:
    """Frozen stretches (stalls) and stacked double frames."""

    frozen, all_stacked, lowest_move, lowest_stack, scores = _measure_frames(take)
    step = 1.0 / STACK_FPS
    span = max(len(scores) * step, step)

    def score(a: float, b: float) -> float:
        window = scores[int(round(a / step)) : int(round(b / step)) + 1]
        return float(np.median(window)) if window.size else 1.0

    # A stretch over (nearly) the whole take is the set, not a split frame: a note with its score.
    whole = [
        (a, b)
        for a, b in all_stacked
        if (b - a + step) >= STACK_WHOLE_TAKE_SHARE * span
    ]
    stacked = [run for run in all_stacked if run not in whole]
    threshold = (
        f"frozen: frame-to-frame RMSE < {FREEZE_THRESHOLD:g} for ≥ {FREEZE_MIN_SECONDS:g} s; stacked: top-vs-bottom "
        f"RMSE < {STACK_THRESHOLD:g} for > {STACK_MIN_SECONDS:g} s; at {STACK_FPS:g} fps"
    )
    details = [
        f"frozen {a:.2f}-{b:.2f}s: the picture stops; look at it (a stall, or a hold you asked for)"
        for a, b in frozen
    ]
    details += [
        f"stacked double frame {a:.2f}-{b:.2f}s (top-vs-bottom {score(a, b):.3f}, under {STACK_THRESHOLD:g}): "
        "look at it full size (dark or symmetric sets false-positive)"
        for a, b in stacked
    ]
    details += [
        f"note: top and bottom halves look alike over the whole take ({a:.2f}-{b:.2f}s, top-vs-bottom "
        f"{score(a, b):.3f}, under {STACK_THRESHOLD:g}). A dark, even or symmetric set reads this way; a real "
        "stacked double frame comes and goes. Not a warning: glance at the contact sheet"
        for a, b in whole
    ]
    if frozen and "freeze" in steps:
        details.append(
            "this file went through `freeze`: the hold you asked for reads as frozen"
        )
    summary = (
        f"{len(frozen)} frozen, {len(stacked)} stacked stretch(es)"
        if frozen or stacked
        else f"none (lowest frame-to-frame {lowest_move:.4f}, lowest top-vs-bottom {lowest_stack:.3f})"
    )
    if whole and not (frozen or stacked):
        summary = f"none (top and bottom alike over the whole take, top-vs-bottom {score(*whole[0]):.3f}: a note)"
    data = {"frozen": [list(r) for r in frozen], "stacked": [list(r) for r in stacked],
            "stacked_whole_take": [list(r) for r in whole],
            "lowest_frame_rmse": lowest_move, "lowest_stack_rmse": lowest_stack}  # fmt: skip
    return Section(
        "Frames", WARN if frozen or stacked else OK, summary, threshold, details, data
    )


# --- 4. Board frames anywhere ----------------------------------------------------------------------------


def _iter_frames(
    take: Path, width: int, height: int
) -> Iterator[npt.NDArray[np.float64]]:
    """Every frame at ``width`` x ``height``, one at a time (a whole take never sits in memory)."""

    proc = subprocess.Popen(
        [ffmpeg_bin(), "-nostdin", "-v", "error", "-i", str(take), "-vf", f"scale={width}:{height}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )  # fmt: skip
    size = width * height * 3
    assert proc.stdout is not None
    try:
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            yield (
                np.frombuffer(raw, dtype=np.uint8)
                .reshape(height, width, 3)
                .astype(np.float64)
            )
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise MediaToolError(f"frame decode failed on {take.name}: {err[-300:]}")


@dataclass(frozen=True)
class BoardScan:
    """Board frames in a whole take.

    Parameters
    ----------
    head
        Leading frames that are the board (``deboard``'s measurement).
    later
        ``(frame index, PSNR)`` of board frames after the head.
    baseline_db
        Median PSNR of one frame a second from second 1 on.
    """

    head: int
    later: tuple[tuple[int, float], ...]
    baseline_db: float


def scan_board(
    take: Path, board: Path, *, margin_db: float = BOARD_LEAK_MARGIN_DB
) -> BoardScan:
    """Find board frames anywhere in ``take``: the head as ``deboard`` counts it, then every later frame.

    Raises
    ------
    FileNotFoundError
        When the board image is missing.
    ValueError
        When the take is too short to measure a baseline.
    """

    leak = measure_board_leak(take, board, margin_db=margin_db)
    width, height = LEAK_ANALYSIS_SIZE
    with Image.open(board) as opened:
        reference = np.asarray(
            opened.convert("RGB").resize((width, height), Image.Resampling.BILINEAR),
            dtype=np.float64,
        )
    floor = leak.baseline_db + margin_db
    later: list[tuple[int, float]] = []
    for index, frame in enumerate(_iter_frames(take, width, height)):
        if index < leak.frames:
            continue
        value = _psnr(frame, reference)
        if value >= floor:
            later.append((index, round(value, 2)))
    return BoardScan(head=leak.frames, later=tuple(later), baseline_db=leak.baseline_db)


def board_section(
    take: Path, board: Path | None, *, fps: float, deboarded: bool
) -> tuple[Section, int]:
    """Board frames anywhere in the take; returns the section and the head count (for the cut trace)."""

    threshold = f"PSNR vs the board ≥ baseline + {BOARD_LEAK_MARGIN_DB:g} dB, every frame at 192x336"
    if board is None:
        return Section(
            "Board",
            NONE,
            "not measured: no approved board on the desk (pass --board)",
            threshold,
        ), 0
    try:
        scan = scan_board(take, board)
    except ValueError as exc:
        return Section("Board", NONE, f"not measured: {exc}", threshold), 0
    data = {"board": str(board), "head_frames": scan.head, "baseline_db": scan.baseline_db,
            "later": [{"frame": i, "seconds": round(i / fps, 3), "psnr_db": v} for i, v in scan.later]}  # fmt: skip
    details: list[str] = []
    faults = 0
    if scan.head:
        if deboarded:
            faults += 1
            details.append(
                f"the first {scan.head} frame(s) are still the board on a deboarded file: run deboard on the raw take"
            )
        else:
            details.append(
                f"the first {scan.head} frame(s) are the board: finish removes them (deboard)"
            )
    if scan.later:
        faults += 1
        times = ", ".join(f"{i / fps:.2f}s ({v:.1f} dB)" for i, v in scan.later[:12])
        more = f" and {len(scan.later) - 12} more" if len(scan.later) > 12 else ""
        details.append(
            f"{len(scan.later)} board frame(s) after the head at {times}{more}: the board flashes mid-take"
        )
    summary = (
        f"{scan.head} head, {len(scan.later)} later board frame(s) against `{board.name}` "
        f"(baseline {scan.baseline_db:.1f} dB)"
    )
    return Section(
        "Board", WARN if faults else OK, summary, threshold, details, data
    ), scan.head


def text_section(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    take: Path,
    kind: str,
    ocr: Any = None,
) -> Section:
    """Drawn subtitles in the raw take: ⚠ with times and a crop sheet, ``–`` (said so) without OCR."""

    from creation.post.desk import latest_raw_take
    from creation.post.take_text import (
        LINE_SAMPLE_FPS,
        SAMPLE_FPS,
        desk_text_check,
    )

    threshold = f"OCR on {SAMPLE_FPS:g} frames/s ({LINE_SAMPLE_FPS:g} in spoken-line windows), raw take"
    if "reel" in _steps(take):
        # The reel is cut from the finished take: its raw take's text was read on the take's own review
        # (and a reel never adds files to the episode's takes/).
        return Section(
            "Text",
            NONE,
            "not measured on a reel: read on the take's own review",
            threshold,
        )
    read = take
    if kind == "finished":
        try:
            read = latest_raw_take(desk, episode, take_id)
        except FileNotFoundError:
            return Section(
                "Text",
                NONE,
                "not measured: no raw take on the desk (a finished file carries house captions)",
                threshold,
            )
    try:
        check = desk_text_check(desk, episode, take_id, read, ocr=ocr)
    except MediaToolError as exc:
        return Section("Text", NONE, f"not measured: {exc}", threshold)
    details = [line.strip() for line in check.warning_lines(
        desk=desk, episode=episode, take_id=take_id, final=take if kind == "finished" else None
    )[1:]]  # fmt: skip
    details += [f"other text (sign or set dressing?): '{f.text}' at {f.times[0]:.2f}s"
                for f in check.other[:4]]  # fmt: skip
    # A note, not a fault: unreadable glyphs held on a sign (garbled lettering).
    details += check.note_lines()
    if check.note and check.status != "skipped":
        details.append(check.note)
    status = WARN if check.subtitles else NONE if check.status == "skipped" else OK
    summary = check.summary() + (f" in `{read.name}`" if read != take else "")
    return Section(
        "Text", status, summary, threshold, details, {"text_check": check.as_json()}
    )


# --- 5. Script vs audio ----------------------------------------------------------------------------------


_NOT_ASKED = re.compile(
    r"^(?P<who>ep\d+ \S+): line (?P<number>\d+) \('(?P<text>.*)'\) was not in the take's instructions$"
)
_ASKED_COUNT = re.compile(
    r"^(?P<who>ep\d+ \S+): (?P<asked>\d+) of (?P<total>\d+) approved lines asked"
)


def _same_words(a: str, b: str) -> bool:
    def norm(text: str) -> list[str]:
        return re.findall(r"[\w']+", text.casefold())

    return bool(norm(a)) and norm(a) == norm(b)


def hand_laid_rows(
    rows: list[str], hand_voices: tuple[dict[str, Any], ...]
) -> tuple[list[str], int]:
    """Count a line ``finish --voice`` laid by hand as on the take, not as missing.

    Parameters
    ----------
    rows
        ``check-lines`` output rows.
    hand_voices
        The finish record's ``hand_voices`` (``{file, start, seconds, line}``).

    Returns
    -------
    tuple[list[str], int]
        The rows with each hand-laid line said as such (and the count row
        saying how many are on the take), and how many missing lines that covers.
    """

    covered = 0
    out: list[str] = []
    for row in rows:
        found = _NOT_ASKED.match(row.strip())
        voice = (
            next(
                (
                    v
                    for v in hand_voices
                    if _same_words(str(v.get("line") or ""), found["text"])
                ),
                None,
            )
            if found
            else None
        )
        if found and voice is not None:
            covered += 1
            out.append(
                f"{found['who']}: line {found['number']} ('{found['text']}') was laid by hand "
                f"(finish --voice `{voice.get('file')}` at {float(voice.get('start') or 0.0):.2f}s)"
            )
        else:
            out.append(row)
    if covered:
        for index, row in enumerate(out):
            count = _ASKED_COUNT.match(row.strip())
            if count:
                on_take = int(count["asked"]) + covered
                out[index] = (
                    f"{row.strip()}; {covered} laid by hand with finish --voice: "
                    f"{on_take} of {count['total']} on the take"
                )
    return out, covered


def lines_section(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    words_json: Path | None,
    words_note: str | None = None,
    hand_voices: tuple[dict[str, Any], ...] = (),
) -> Section:
    """The line check from the take facts (``check-lines``) and, with a transcript, which lines were heard.

    A line the take was not asked for but ``finish --voice`` laid in by hand
    (the finish record's ``hand_voices``) counts as on the take.
    """

    from creation.episode_commands import CommandStopped, run_check_lines

    threshold = "asked: every approved line in the take facts; heard: ≥ 60% of a line's words in order (names by sound); under 3 words: check by ear"
    details: list[str] = []
    data: dict[str, Any] = {"asked_missing": None, "heard": None}
    faults = False
    measured = False
    asked_text = StringIO()
    try:
        missing = run_check_lines(
            desk, episode=episode, take_id=take_id, out=asked_text
        )
    except (CommandStopped, FileNotFoundError, ValueError) as exc:
        asked_rows = [f"check-lines: not checked ({exc})"]
    else:
        # ``check-lines`` output as it prints it (line counts, each line's shot and board row, ``!!`` flags),
        # with the count first.
        rows = [row for row in asked_text.getvalue().splitlines() if row.strip()]
        rows, covered = hand_laid_rows(rows, hand_voices)
        missing -= covered
        rows.sort(key=lambda row: "approved lines asked" not in row)
        asked_rows = [f"check-lines: {row.strip()}" for row in rows]
        late = late_opening_line(asked_rows, take_id=take_id)
        if late:
            asked_rows.append(late)
            faults = True
        if not any("no take facts" in row for row in rows):
            measured = True
            flagged = sum(1 for row in rows if row.lstrip().startswith("!!"))
            data["asked_missing"] = missing
            data["check_lines_flags"] = flagged
            faults = faults or missing > 0 or flagged > 0
    details += asked_rows
    heard_rows, heard_data, heard_faults = _heard_rows(
        desk, episode=episode, take_id=take_id, words_json=words_json
    )
    if words_note:
        details.append(words_note)
    details += heard_rows
    if heard_data is not None:
        measured = True
        data["heard"] = heard_data
        faults = faults or heard_faults
    summary = details[0] if details else "nothing to check"
    status = WARN if faults else OK if measured else NONE
    return Section("Lines", status, summary, threshold, details[1:], data)


def take_lines(
    desk: Path, episode: int, take_id: str
) -> tuple[list[dict[str, str]], str] | None:
    """The take's approved lines (with every spelling) and the show's language, from the saved spine."""

    from creation.ops.state import episode_by_ordinal, load_series
    from creation.post.desk import episode_dialogue, saved_spine, show_language
    from creation.spine_view import dialogue_line_ids

    found = saved_spine(desk, episode)
    if found is None:
        return None
    spine = found[0]
    takes = [
        take.take_id for take in episode_by_ordinal(load_series(desk), episode).takes
    ]
    index = takes.index(take_id) + 1 if take_id in takes else 1
    ids = dialogue_line_ids(
        spine, episode=episode, take_index=index, take_count=len(takes) or 1
    )
    wanted = [line_id for line_id, _ in ids]
    by_id = {line["line_id"]: line for line in episode_dialogue(spine, episode)}
    lines = [by_id[line_id] for line_id in wanted if line_id in by_id]
    return lines, show_language(spine)


def _heard_rows(
    desk: Path, *, episode: int, take_id: str, words_json: Path | None
) -> tuple[list[str], list[dict[str, Any]] | None, bool]:
    from creation.post.whisper import line_windows, load_words, word_count

    if words_json is None:
        why = "no transcript on the desk; pass --words-json F, or --transcribe for a few cents"
        return [f"heard: not read ({why})"], None, False
    found = take_lines(desk, episode, take_id)
    if found is None:
        return (
            ["heard: not read (no spine snapshot on the desk; run `spine --refresh`)"],
            None,
            False,
        )
    lines, _language = found
    if not lines:
        return [f"heard: no approved lines for {take_id} (a wordless take)"], [], False
    windows = line_windows(
        load_words(words_json),
        tuple(line["performed"] for line in lines),
        alternates=tuple((line["text"], line["spoken_text"]) for line in lines),
    )
    heard = [w for w in windows if w.start is not None]
    rows = [
        f"heard: {len(heard)} of {len(lines)} approved line(s) in `{words_json.name}`"
    ]
    data: list[dict[str, Any]] = []
    long_hold = False
    for window in windows:
        how = {
            "sound": " (by sound, {:.0%})",
            "shape": " (by reading shape, {:.0%})",
        }.get(window.by, "")
        how = how.format(window.ratio)
        short = word_count(window.line) < SHORT_LINE_WORDS
        if (window.start is None or window.end is None) and short:
            # A transcript often misses or misspells a line this short: never a re-film call on its own.
            rows.append(
                f"  {window.index + 1}. CHECK BY EAR  {window.line!r}: under {SHORT_LINE_WORDS} words, "
                "the transcript did not find it; listen to the take before deciding (never re-film on this alone)"
            )
        elif window.start is None or window.end is None:
            rows.append(
                f"  {window.index + 1}. MISSING  {window.line!r}: the one real re-film (name the cause)"
            )
        else:
            when = f"{window.start:.2f}-{window.end:.2f}s"
            rows.append(
                f"  {window.index + 1}. {when}  {window.line!r}  {window.ratio:.0%}{how}"
            )
            held = short_line_hold(
                window.line, start=float(window.start), end=float(window.end)
            )
            if held:
                rows.append(f"  {held}")
                long_hold = True
        data.append({"line": window.index + 1, "text": window.line, "start": window.start, "end": window.end,
                     "ratio": window.ratio, "by": window.by,
                     "check_by_ear": window.start is None and short})  # fmt: skip
    return rows, data, len(heard) < len(lines) or long_hold


_WORDS_VERSION = re.compile(r"-(?:raw-)?v(\d+)-.*words")
_RAW_VERSION = re.compile(r"^take-ep\d+-t\d+-raw-v(\d+)\.mp4$")

WORDS_SOURCE_KEY = "kit_source"
"""Key in a transcript the kit saved: which media it was made from (:func:`media_source`)."""


def raw_take_of(desk: Path, take: Path) -> Path | None:
    """The raw take behind ``take`` (``take`` itself when it is one); ``None`` when the desk has no record."""

    from creation.post.lineage import raw_take_behind

    return take if _RAW_VERSION.match(take.name) else raw_take_behind(desk, take).raw


def raw_version(raw: Path) -> int | None:
    """``3`` for ``take-ep01-t1-raw-v3.mp4``; ``None`` for any other name."""

    match = _RAW_VERSION.match(raw.name)
    return int(match.group(1)) if match else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def media_source(desk: Path, episode: int, take_id: str, raw: Path) -> dict[str, Any]:
    """What a transcript of the raw take ``raw`` is made from: the file's content hash and the stored URL.

    Saved in the transcript (:data:`WORDS_SOURCE_KEY`), so a re-filmed take, whose
    raw file and stored URL are new, never reads the old take's words, and an
    unchanged take keeps its transcript.

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        Which take.
    raw
        The raw take file (``takes/take-epNN-tK-raw-vN.mp4``).

    Returns
    -------
    dict[str, Any]
        ``{"raw_take", "version", "sha256", "size", "url"}``.
    """

    from creation.post.desk import take_stored_url

    return {
        "raw_take": raw.name,
        "version": raw_version(raw),
        "sha256": _sha256(raw),
        "size": raw.stat().st_size,
        "url": take_stored_url(desk, episode, take_id),
    }


def words_source(words: Path) -> dict[str, Any] | None:
    """The media a saved transcript says it was made from (:func:`media_source`), or ``None`` (older transcripts)."""

    try:
        body = json.loads(words.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    source = body.get(WORDS_SOURCE_KEY) if isinstance(body, dict) else None
    return source if isinstance(source, dict) and source.get("sha256") else None


def saved_words(desk: Path, episode: int, take_id: str) -> Path | None:
    """Newest saved Whisper transcript of this take on the desk (``takes/take-epNN-tK-…words-vN.json``)."""

    takes = desk / f"ep{episode:02d}" / "takes"
    found = [
        p
        for p in takes.glob(f"take-ep{episode:02d}-{take_id}-*words*-v*.json")
        if p.is_file()
    ]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def take_words(
    desk: Path, episode: int, take_id: str, take: Path
) -> tuple[Path | None, str]:
    """The newest saved transcript of exactly this take file, or why there is none.

    A transcript belongs to the raw take behind ``take`` (an edit is walked back with
    :func:`creation.post.lineage.raw_take_behind`):

    - when the kit saved where it came from (:data:`WORDS_SOURCE_KEY`, written by
      ``review --transcribe``), only when that is this raw take's content (its
      sha256) and, when both are known, the take's current stored URL;
    - else when its name carries that take's version (``take-ep01-t1-v3-words-…``,
      ``…-raw-v3-words-…``);
    - else (``…-review-words-vN.json``, no version) when it was saved after that raw take.

    A transcript of an earlier version of the take (re-filmed since) is never used:
    its words are another take's.

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        Which take.
    take
        The file being finished or checked.

    Returns
    -------
    tuple[Path | None, str]
        The transcript, or ``None`` and a one-line reason.
    """

    from creation.post.desk import take_stored_url

    raw = raw_take_of(desk, take)
    if raw is None:
        return None, f"no record of the raw take behind `{take.name}`"
    version = raw_version(raw)
    takes = desk / f"ep{episode:02d}" / "takes"
    mine: list[Path] = []
    others: list[tuple[Path, str]] = []
    known: dict[str, Any] = {}

    def current(key: str) -> Any:
        if key not in known:
            known[key] = (
                _sha256(raw)
                if key == "sha256"
                else take_stored_url(desk, episode, take_id)
            )
        return known[key]

    for words in takes.glob(f"take-ep{episode:02d}-{take_id}-*words*-v*.json"):
        if not words.is_file():
            continue
        source = words_source(words)
        named = _WORDS_VERSION.search(words.name)
        if source is not None:
            url = source.get("url")
            same = source["sha256"] == current("sha256") and not (
                url and current("url") and url != current("url")
            )
            whose = f"`{source.get('raw_take') or 'another file'}` as it was when transcribed"
            (mine.append(words) if same else others.append((words, whose)))
        elif named is not None:
            if int(named.group(1)) == version:
                mine.append(words)
            else:
                others.append((words, f"take v{named.group(1)}"))
        elif words.stat().st_mtime >= raw.stat().st_mtime:
            mine.append(words)
        else:
            others.append((words, "a take saved before it"))
    if mine:
        return max(mine, key=lambda p: p.stat().st_mtime), ""
    if others:
        newest, whose = max(others, key=lambda pair: pair[0].stat().st_mtime)
        return None, (
            f"the only transcript, `{newest.name}`, is of {whose}, not `{raw.name}` (re-filmed since)"
        )
    return None, f"no transcript of `{raw.name}`"


def words_belong_note(words: Path, raw: Path | None, *, made: bool = False) -> str:
    """One line naming the transcript and the take version it belongs to, for the Lines section."""

    how = "transcript made on the server from the stored take" if made else "transcript"
    if raw is None:
        return f"{how}: `{words.name}`"
    version = raw_version(raw)
    of = f"take v{version}, `{raw.name}`" if version is not None else f"`{raw.name}`"
    return f"{how}: `{words.name}` (of {of})"


# --- 6. Safe zones (finished takes) -----------------------------------------------------------------------


def safe_zones_section(take: Path) -> Section:
    """The caption safe-zone check (:func:`creation.post.safe_zones.run_review`, unchanged) as a section.

    It samples frames, finds the house caption box by colour, checks it against the covered
    zones and the caption band, and writes the zone sheet and its JSON beside the take.
    """

    from creation.captions import CAPTION_BAND
    from creation.post.safe_zones import SAMPLE_FRAMES, run_review

    report = run_review(None, take_file=take, out=StringIO())
    low, high = CAPTION_BAND
    threshold = (
        f"caption box clear of the top 8%, bottom 20% and right rail, inside {low:.0%}-{high:.0%} of the height, "
        f"on {SAMPLE_FRAMES} sampled frames and every cue of the burned caption file; faces by eye on the "
        "zone sheet (no face detector)"
    )
    found = [
        w
        for w in report.warnings
        if w.startswith("!!") or w.startswith("no house caption")
    ]
    notes = [w for w in report.warnings if w not in found]
    captioned = sum(1 for frame in report.frames if frame.caption is not None)
    summary = f"{captioned} of {len(report.frames)} sampled frame(s) show a caption" + (
        f": {len(found)} finding(s)" if found else ", all clear of the covered zones"
    )
    details = [
        *(w.removeprefix("!! ") for w in found),
        *notes,
        f"zone sheet: {report.sheet.name} (report {report.sheet.with_suffix('.json').name})",
    ]
    return Section(
        "Safe zones",
        WARN if found else OK,
        summary,
        threshold,
        details,
        {"safe_zones": report.as_json()},
    )


# --- Take-facts warnings (fictora-drama #584) -----------------------------------------------------------


def take_warnings_section(
    facts: Mapping[str, Any] | None, cast_names: Mapping[str, str]
) -> Section | None:
    """The server's ``warnings`` on the take facts (``edge_duplicate_risk`` first), or ``None`` when absent."""

    from creation.post.take_facts import take_warning_lines

    lines = take_warning_lines(facts, cast_names)
    if not lines:
        return None
    found = lines[:-1]
    return Section(
        "Take warnings",
        WARN,
        f"{len(found)} warning(s) from the take facts: check the shot(s) before finishing",
        "each warning the server sent with the take facts",
        [line.removeprefix("!! ") for line in found] + [lines[-1].strip()],
        {"take_warnings": [line.removeprefix("!! ") for line in found]},
    )


# --- People on screen (a human check) ----------------------------------------------------------------------


def people_section(
    facts: Mapping[str, Any] | None, cast_names: Mapping[str, str]
) -> Section:
    """Who the take facts expect on screen per shot, and the by-eye check for one person drawn twice.

    Nothing is detected: the kit has no face detector. The head counts come from
    the take facts (``shots[].people``, sent by newer servers); the operator
    counts the people in each shot against them.

    Parameters
    ----------
    facts
        The saved take facts (or ``None``).
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    Section
        Always ``–`` (nothing measured); the per-shot counts and the check as details.
    """

    from creation.post.take_facts import TWICE_CAUSE, shot_people_lines

    threshold = (
        "head counts from the take facts; counted by eye (no face detector here)"
    )
    if facts is None:
        return Section("People", NONE, "no take facts on the desk", threshold)
    shots = shot_people_lines(facts, cast_names)
    if not shots:
        return Section(
            "People",
            NONE,
            "the server didn't send head counts for this take (older server): count by eye",
            threshold,
            data={"people": None},
        )
    details = [
        *shots,
        "Check by eye, shot by shot (on a finished file also the zone sheet): is anyone on screen twice, "
        "the same face in two poses or a look-alike extra? Watch the cuts where a character changes pose.",
        f"If so, re-film with the cause '{TWICE_CAUSE.format(shot='N')}'.",
    ]
    return Section(
        "People",
        NONE,
        f"{len(shots)} shot(s) with an expected head count: count them by eye",
        threshold,
        details,
        {"people": shots},
    )


# --- Soundtrack (whose voices the take's sound is) ------------------------------------------------------------


def soundtrack_section(
    facts: Mapping[str, Any] | None, cast_names: Mapping[str, str]
) -> Section:
    """Whose voices the take's sound is (``soundtrack`` in the take facts), and what finish lays for it.

    Parameters
    ----------
    facts
        The saved take facts (or ``None``).
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    Section
        Always ``–`` (nothing measured here; finish checks each line is heard in its window).
    """

    from creation.post.soundtrack import soundtrack_from, soundtrack_lines

    threshold = "from the take facts"
    if facts is None:
        return Section("Soundtrack", NONE, "no take facts on the desk", threshold)
    soundtrack = soundtrack_from(facts)
    head, *rows = soundtrack_lines(facts, cast_names)
    details = [row.strip() for row in rows]
    if soundtrack.target_audio:
        details.append(
            "The voices are already the locked voices: do not revoice. finish lays the location's ambience "
            "(room tone when none can be made), the bed "
            "(ducked in each line window) and the effects on the measured cuts, and stops if it cannot."
        )
    return Section(
        "Soundtrack",
        NONE,
        head.removeprefix("Soundtrack: "),
        threshold,
        details,
        {"soundtrack": {"mode": soundtrack.mode, "sent": soundtrack.sent, "reason": soundtrack.reason,
                        "lines": len(soundtrack.lines)}},
    )  # fmt: skip


# --- The review ---------------------------------------------------------------------------------------------


def default_take(desk: Path, episode: int, take_id: str) -> tuple[Path, str]:
    """The file ``review`` reads when none is given: the newest finished file, else the newest raw take.

    Returns
    -------
    tuple[Path, str]
        The file, and a note saying which one was chosen and why.

    Raises
    ------
    FileNotFoundError
        When the take has no raw or finished file on the desk.
    """

    from creation.post.desk import latest_raw_take

    takes = desk / f"ep{episode:02d}" / "takes"
    finished = [
        p
        for p in takes.glob(f"take-ep{episode:02d}-{take_id}-*.mp4")
        if p.is_file() and take_kind(p) == "finished"
    ]
    if finished:
        return max(
            finished, key=lambda p: p.stat().st_mtime
        ), "the newest finished file"
    return latest_raw_take(
        desk, episode, take_id
    ), "the newest raw take: no finished file yet"


Transcribe = Callable[[Path, int, str], Path]
"""``(desk, episode, take_id) -> saved words JSON``: a transcript made on the server."""


def server_transcript(
    desk: Path, episode: int, take_id: str, *, take: Path | None = None
) -> Path:
    """Ask the server for a Whisper transcript of the take's stored URL and save it (a few cents).

    The saved transcript is named for the raw take's version
    (``take-epNN-tK-raw-vN-review-words-vM.json``) and records the media it was
    made from (:func:`media_source`: the raw file's sha256 and the stored URL),
    so :func:`take_words` hands it only to that take.

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        Which take.
    take
        The file being checked (its raw take is the one transcribed); default the newest raw take.

    Raises
    ------
    ValueError
        When the take has no stored URL on the desk, or there is no spine snapshot.
    """

    from creation.ops.folder import next_versioned_path
    from creation.post.audio_service import DramaApiAudio
    from creation.post.desk import latest_raw_take, spine_id, take_stored_url
    from creation.post.whisper import transcribe

    stored = take_stored_url(desk, episode, take_id)
    if stored is None:
        raise ValueError(
            f"no stored URL for ep{episode:02d} {take_id} in api/ (17_raw_scene_clips.json or film-*-raw-scene-clips.json) "
            "to transcribe "
            "(this kit never uploads local files); pass --words-json"
        )
    found = take_lines(desk, episode, take_id)
    language = found[1] if found else "en"
    takes = desk / f"ep{episode:02d}" / "takes"
    if take is not None:
        raw = raw_take_of(desk, take)
    else:
        try:
            raw = latest_raw_take(desk, episode, take_id)
        except FileNotFoundError:
            raw = None
    source = media_source(desk, episode, take_id, raw) if raw is not None else None
    version = source.get("version") if source else None
    stem = (
        f"take-ep{episode:02d}-{take_id}-raw-v{version}-review-words"
        if version is not None
        else f"take-ep{episode:02d}-{take_id}-review-words"
    )
    target = next_versioned_path(takes, stem, ".json")
    audio = DramaApiAudio(desk, episode=episode)
    saved = transcribe(
        stored,
        target,
        audio=audio,
        spine_id=spine_id(desk),
        language=language,
        media_version=source["sha256"] if source else None,
    )
    if source is not None:
        body = json.loads(saved.read_text(encoding="utf-8"))
        if isinstance(body, dict):
            body[WORDS_SOURCE_KEY] = source
            saved.write_text(
                json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
    return saved


def _beside_cues(take: Path) -> list[Any]:
    """The caption cues of the ``.ass`` beside ``take`` (a reel's), else none."""

    ass = take.with_suffix(".ass")
    if not ass.is_file():
        return []
    from creation.post.reel import parse_ass_cues

    return parse_ass_cues(ass.read_text(encoding="utf-8"))


def opening_section(
    take: Path,
    *,
    where: str,
    silent_open: bool,
    head_count_face: float | None,
    detector: Any = None,
) -> Section:
    """The first second (:mod:`creation.post.opening`): a dark frame 0, a static head, no face on frame 0.

    Run on the first take of every episode and on every reel. Warnings only.
    """

    from creation.post.opening import (
        DARK_LUMA,
        OPENING_SECONDS,
        STATIC_MOTION,
        measure_opening,
    )

    reading = measure_opening(
        take, detector=detector, head_count_face=head_count_face,
        silent_open=silent_open, where=where,
    )  # fmt: skip
    face = (
        "not checked (a silent / visual opening)"
        if silent_open
        else "not checked (no face detector, no head count in the take facts)"
        if reading.face is None
        else f"{reading.face:.2f} by {reading.face_source.replace('_', ' ')}"
    )
    summary = (
        f"frame 0 luma {reading.frame0_luma:.3f}, first {OPENING_SECONDS:g} s motion {reading.motion:.4f}, "
        f"face {face}"
    )
    threshold = (
        f"dark: luma < {DARK_LUMA:g} on frame 0 and the first 0.5 s; static: mean frame-to-frame < "
        f"{STATIC_MOTION:g} over {OPENING_SECONDS:g} s; face on frame 0 unless the opening beat has no line"
    )
    return Section(
        "Opening", WARN if reading.warnings else OK, summary, threshold,
        list(reading.warnings), {"opening": reading.as_json()},
    )  # fmt: skip


def ending_section(take: Path, *, last_mark: float | None, what: str) -> Section:
    """The tail (:mod:`creation.post.opening`): more than 0.3 s settled after the last line or action."""

    from creation.post.opening import TAIL_MAX_SECONDS, measure_tail, tail_warning

    reading = measure_tail(take, last_mark=last_mark)
    line = tail_warning(reading, what=what)
    summary = f"{reading.settled_seconds:.2f} s after the last line or action" + (
        "" if last_mark is not None else " (no caption times: motion only)"
    )
    return Section(
        "Ending", WARN if line else OK, summary,
        f"settled tail ≤ {TAIL_MAX_SECONDS:g} s; ends hard on the last frame",
        [line] if line else [], {"tail": reading.as_json()},
    )  # fmt: skip


def review_take(
    desk: Path,
    *,
    episode: int = 1,
    take_id: str = "t1",
    take_file: Path | None = None,
    board: Path | None = None,
    words_json: Path | None = None,
    transcribe: bool = False,
    transcriber: Transcribe | None = None,
    text_ocr: Any = None,
    face_detector: Any = "local",
) -> TakeReview:
    """Measure one take and return the review. Reads only; writes nothing but an optional transcript.

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        Which take.
    take_file
        The file to read (raw or finished); default :func:`default_take` (the newest finished file,
        else the newest raw take).
    board
        Board image; default the take's approved board on the desk.
    words_json
        A saved Whisper transcript; default the newest one saved for this take.
    transcribe
        Ask the server for a transcript when none is saved (a few cents).
    transcriber
        Injected for tests.
    text_ocr
        The text check's OCR, injected for tests (default the ``tesseract`` command).
    face_detector
        The opening check's face detector: ``"local"`` (both OpenCV cascades),
        ``None`` (the take facts' head count), or a test's stand-in.

    Returns
    -------
    TakeReview
        Every section, each ✓, ⚠ or – with its threshold.

    Raises
    ------
    FileNotFoundError
        When the take file is missing.
    """

    from creation.post.desk import approved_board, saved_spine
    from creation.post.sfx import saved_take_facts
    from creation.post.take_facts import cast_names_from

    desk = desk.expanduser().resolve()
    if take_file is not None:
        take = take_file.expanduser().resolve()
        if not take.is_file():
            raise FileNotFoundError(f"take not found: {take}")
        chosen = "given with --take-file"
    else:
        take, chosen = default_take(desk, episode, take_id)
    info = probe_video(take)
    fps = info.fps or 24.0
    kind = take_kind(take)
    steps = _steps(take)
    board_path = (
        board.expanduser().resolve()
        if board
        else approved_board(desk, episode, take_id)
    )
    facts_path = saved_take_facts(desk, episode, take_id)
    facts = json.loads(facts_path.read_text(encoding="utf-8")) if facts_path else None
    handled_record = record_for_file(desk, take)
    if handled_record is not None:
        from creation.post.take_handles import facts_on_handled_file

        # A file finish cut to its trim handles: the facts' times minus start_s (fictora-drama #563).
        facts = facts_on_handled_file(facts, handled_record.edits)  # type: ignore[assignment]
    words_note = None
    if "reel" in steps:
        # A transcript of the take does not say what the reel plays: its plan lists the lines it keeps.
        words_json, transcribe = None, False
        words_note = (
            "the reel is cut and reordered from the take: lines heard are checked on the take's own review; "
            "the reel plan (reels/reel-plan-epNN-vN.json) lists which lines it keeps"
        )
    elif words_json is None:
        # Only a transcript of this exact take: after a re-film the old take's words are another take's.
        raw = raw_take_of(desk, take)
        if raw is None:
            words_json, why = saved_words(desk, episode, take_id), ""
        else:
            words_json, why = take_words(desk, episode, take_id, take)
        if words_json is not None:
            words_note = words_belong_note(words_json, raw)
            if raw is None:
                words_note += " (the desk has no record of the raw take behind this file: check it is this take's)"
        elif transcribe:
            words_json = (
                transcriber(desk, episode, take_id)
                if transcriber is not None
                else server_transcript(desk, episode, take_id, take=take)
            )
            words_note = words_belong_note(words_json, raw, made=True)
        elif why:
            words_note = f"saved transcript not used: {why}; `--transcribe` makes one of this take (a few cents)"
    loud = loudness_section(take, kind, has_audio=info.has_audio)
    board_sec, head = board_section(
        take, board_path, fps=fps, deboarded=bool(steps & set(DEBOARDED_STEPS))
    )
    held = server_board_frames(facts)
    if held is not None:
        # The server already cleaned the raw take: its head reads 0 here, and nothing is shifted.
        board_sec.details.extend(held.lines())
        board_sec.data["server_held"] = {
            "head_frames": held.head_frames,
            "tail_frames": held.tail_frames,
            "original": (
                {
                    "url": held.original.url,
                    "content_sha256": held.original.content_sha256,
                    "content_length": held.original.content_length,
                }
                if held.original is not None
                else None
            ),
            "unsure": [
                {"end": run.end, "frames": run.frames, "reason": run.reason}
                for run in held.unsure
            ],
        }
        if held.unsure_at("head") is not None and head:
            # finish leaves an end the server was unsure of: say so instead of "finish removes them".
            board_sec.details = [
                detail.replace(
                    "finish removes them (deboard)",
                    "finish leaves them (the server was unsure); if they are the board, "
                    "`deboard --hold-unsure` then finish --take-file the result",
                )
                for detail in board_sec.details
            ]
        if held.unsure and board_sec.status == OK:
            board_sec.status = WARN
    moved = facts_shift_s(facts)
    if moved is not None:
        board_sec.details.append(
            f"take facts moved {moved:.3f}s earlier: the server cut this take's head board frames "
            "with their sound (fictora-drama #543)"
        )
    cuts = cuts_section(
        take,
        head_board_frames=head,
        facts=facts,
        retimed=bool(steps & set(RETIMED_STEPS)),
        retimed_by="the reel cut and reordered the take" if "reel" in steps else None,
    )
    frames = frames_section(take, steps=steps)
    text = text_section(
        desk, episode=episode, take_id=take_id, take=take, kind=kind, ocr=text_ocr
    )
    # Only the record that names this file: the raw take has no hand lines on it.
    record = record_for_file(desk, take)
    lines = lines_section(
        desk,
        episode=episode,
        take_id=take_id,
        words_json=words_json,
        words_note=words_note,
        hand_voices=record.hand_voices if record is not None else (),
    )
    found = saved_spine(desk, episode)
    people = people_section(facts, cast_names_from(found[0] if found else None))
    sections = [loud, cuts, frames, board_sec, text, lines, people]
    warned = take_warnings_section(facts, cast_names_from(found[0] if found else None))
    if warned is not None:
        sections.append(warned)
    if facts is not None and "soundtrack" in facts.get("take_facts", facts):
        sections.append(
            soundtrack_section(facts, cast_names_from(found[0] if found else None))
        )
    if take_id == "t1" or "reel" in steps:
        # Every episode's first take, and every reel: the first second decides the scroll.
        from creation.post.faces import detector_for
        from creation.post.opening import OPENING_SECONDS, opening_context

        if "reel" in steps:
            cues = _beside_cues(take)
            silent, head_face = not any(c.start < OPENING_SECONDS for c in cues), None
            where = "the reel's first second"
        else:
            silent, head_face = opening_context(desk, episode, take_id)
            where = f"ep{episode:02d} {take_id}"
        sections.append(
            opening_section(
                take,
                where=where,
                silent_open=silent,
                head_count_face=head_face,
                detector=face_detector
                if face_detector != "local"
                else detector_for(desk, episode),
            )  # fmt: skip
        )
    if "reel" in steps:
        cues = _beside_cues(take)
        sections.append(
            ending_section(
                take,
                last_mark=max((c.end for c in cues), default=None),
                what="the reel",
            )
        )
    if kind == "finished":
        sections.append(safe_zones_section(take))
    return TakeReview(
        take=take,
        kind=kind,
        duration_seconds=info.duration_seconds,
        fps=fps,
        sections=sections,
        episode=episode,
        take_id=take_id,
        chosen=chosen,
    )


__all__ = [
    "CUT_MATCH_SECONDS",
    "FREEZE_MIN_SECONDS",
    "FREEZE_THRESHOLD",
    "SILENT_BELOW_LUFS",
    "STACK_THRESHOLD",
    "TRUE_PEAK_MAX_DBTP",
    "Section",
    "TakeReview",
    "close_cuts",
    "compare_cuts",
    "default_take",
    "safe_zones_section",
    "take_warnings_section",
    "measure_duck",
    "measure_frames",
    "measure_true_peak",
    "people_section",
    "planned_shot_changes",
    "review_take",
    "scan_board",
    "take_kind",
]
