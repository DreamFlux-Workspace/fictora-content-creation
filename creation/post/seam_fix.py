"""``join`` fixes a loud seam itself before refusing: a steady bed on the quiet side, then a silent-head trim.

Every desk, those created before 6 Oct 2026 included (founder decision, 7 Oct
2026: :func:`creation.rules_epoch.continuing_fix`, ``seam_fix``).

Why: the join seam (one take ends quiet, the next starts loud, or the
reverse) was the most repeated issue, seen 8 times across four series, and
operators fixed it by hand the same way every time: a steady sound that belongs
in the scene laid under the seam on the quiet side (Fated in the Rain ep 4:
rain under take 2's near-silent head, -19.4 -> -1.4 dB; Hanakaze ep 2: the ep 1
room-tone cue in the 2 s before the cut, +11.1 -> +1.9 dB), and when that was not
enough, a trim of the take's silent head on a filmed cut (Fated ep 3: -13.6 ->
-6.1 with louder rain, then +4.7 after trimming take 2's silent first 1.2 s).

What :func:`fix_seams` does, free and on this laptop, for every seam that steps
over :data:`creation.post.join.SEAM_STEP_DB`:

1. **Steady bed.** It finds the show's own steady sound: one already used in
   this episode (its location ambience, a sustained planned effect, a sustained
   hand cue), then the series' (another episode's, the desk's ``sfx/``), then
   room tone made from the takes' own quiet passages (else band-limited room
   tone). A file is used only when it is steady (at least
   :data:`STEADY_MIN_SECONDS` of sound whose 0.1 s levels spread under
   :data:`STEADY_SPREAD_DB`). It is laid on the joined master over the
   :data:`SEAM_BED_WINDOW_SECONDS` measured on the quiet side of the cut, at a
   level computed from the measured gap (the quiet side to
   :data:`SEAM_FIX_TARGET_DB` under the loud side), with a
   :data:`SEAM_BED_EDGE_FADE_SECONDS` ramp at the cut and a
   :data:`SEAM_BED_FAR_FADE_SECONDS` fade at its far end, so it never pops. Never
   louder than :data:`SEAM_BED_MAX_DB`; re-measured, the level corrected up to
   :data:`SEAM_BED_PASSES` times.
2. **Silent-head trim.** When a seam still steps too far and the quiet take's
   head (or the quiet take's tail, when the quiet side is before the cut) is
   near-silent before its first line or sound, the silent stretch is cut on the
   last (first) filmed cut inside it (:func:`creation.post.edit.measure_cuts`,
   the trace ``soften`` and the effects use). Speech is never cut. The join is
   built again with the trim and the bed laid again.

A seam's bed is kept only when it made that seam smaller; nothing outside the
seam windows changes: the join is mixed again from the same sound before the
mix, at the same take gain, with the bed added after the gain and the duck and
before the one limiter (never in the duck key), silent outside its windows. When every seam passes, the join completes and the
report says what was laid and trimmed, with the before -> after step. When it
does not, the join refuses as before (the master as first joined is kept) and
lists what it tried.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from creation.post.media import MediaToolError, media_duration

if TYPE_CHECKING:
    from creation.post.join import JoinPart, SeamLevel

RATE = 48000
#: The measuring window ``join`` reads (0.1 s RMS).
WINDOW_SECONDS = 0.1
#: The quiet side is brought to this far under the loud side (the step then lands under ~4 dB).
SEAM_FIX_TARGET_DB = 3.0
#: The bed covers this much of the quiet side (the seam measure's window).
SEAM_BED_WINDOW_SECONDS = 2.0
#: The bed ramps in (or out) over this at the cut, where the loud side covers it.
SEAM_BED_EDGE_FADE_SECONDS = 0.05
#: ... and fades over this at its far end, away from the cut.
SEAM_BED_FAR_FADE_SECONDS = 0.5
#: A seam bed is never laid louder than this (0.1 s RMS, dBFS): a room stays under the -18 LUFS dialogue.
SEAM_BED_MAX_DB = -22.0
#: ... nor boosted more than this over the cue's own level.
SEAM_BED_MAX_BOOST_DB = 24.0
#: Lay, measure, correct the level: at most this many times.
SEAM_BED_PASSES = 3
#: A steady cue has at least this much sound ...
STEADY_MIN_SECONDS = 3.0
#: ... whose 0.1 s levels spread (90th minus 10th percentile) under this.
STEADY_SPREAD_DB = 14.0
#: Windows under this are silence (left out of the steadiness and the level).
STEADY_FLOOR_DB = -70.0
#: A take's quiet passage (for room tone made from the take): under the mix's voice key ...
QUIET_PASSAGE_CEILING_DB = -30.0
#: ... and at least this long without a break.
QUIET_PASSAGE_MIN_SECONDS = 1.0
#: The fallback room tone is generated at this level (the seam gain then sets it).
ROOM_TONE_DB = -40.0
#: A take's head (tail) is near-silent while its own sound stays under this.
SILENT_HEAD_DB = -45.0
#: A trim shorter than this is not worth a cut.
TRIM_MIN_SECONDS = 0.25
#: A trim never takes more than this share of a take.
TRIM_MAX_FRACTION = 0.4
#: Known speech is kept clear of a trim by this much.
TRIM_SPEECH_PAD_SECONDS = 0.3
#: Name words of a steady sound (a cue file whose sidecar does not say ``sustained``).
STEADY_NAME_WORDS = (
    "ambience", "ambient", "room", "tone", "rain", "drizzle", "storm", "wind",
    "hum", "buzz", "drone", "traffic", "street", "crowd", "murmur", "surf",
    "waves", "river", "stream", "hvac", "fan", "engine", "cabin", "forest",
    "crickets", "air",
)  # fmt: skip
_AUDIO = (".mp3", ".wav", ".m4a", ".flac", ".ogg")


# --- levels -------------------------------------------------------------------------------------


def window_levels(samples: np.ndarray) -> np.ndarray:
    """0.1 s RMS (dB) of the first channel, what ``join`` measures (silence reads -120)."""

    size = int(RATE * WINDOW_SECONDS)
    mono = samples[:, 0] if samples.ndim == 2 else samples
    count = len(mono) // size
    if count == 0:
        return np.zeros(0)
    power = (mono[: count * size].reshape(count, size) ** 2).mean(axis=1)
    return np.maximum(-120.0, 10 * np.log10(np.maximum(power, 1e-12)))


def steadiness(samples: np.ndarray) -> tuple[str | None, float]:
    """Why a cue is not steady (``None`` when it is), and its median level (dB) over its sound."""

    levels = window_levels(samples)
    heard = levels[levels > STEADY_FLOOR_DB]
    if heard.size * WINDOW_SECONDS < STEADY_MIN_SECONDS:
        return (
            f"{heard.size * WINDOW_SECONDS:.1f}s of sound (needs {STEADY_MIN_SECONDS:.0f}s)",
            -120.0,
        )
    spread = float(np.percentile(heard, 90) - np.percentile(heard, 10))
    median = float(np.median(heard))
    if spread > STEADY_SPREAD_DB:
        return f"not steady (levels spread {spread:.0f} dB)", median
    return None, median


# --- where a steady sound comes from -------------------------------------------------------------


@dataclass(frozen=True)
class SteadyCue:
    """A steady sound to lay under a seam: its samples (stereo, 48 kHz) and where it came from."""

    samples: np.ndarray
    source: str
    path: Path | None = None


def _steady_name(path: Path) -> bool:
    words = set(re.split(r"[^a-z]+", path.stem.casefold()))
    return any(word in words for word in STEADY_NAME_WORDS)


def _sidecar_sustained(path: Path) -> bool | None:
    sidecar = path.with_suffix(".json")
    if not sidecar.is_file():
        return None
    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw.get("kind") == "sustained" and not raw.get("shape_problem")


def _planned_sustained(desk: Path, episode: int) -> Iterator[tuple[Path, str]]:
    """The sustained effects planned on this episode's takes, from the cache ``finish`` laid them from."""

    from creation.post.sfx import plan_from_take_facts

    api = desk / f"ep{episode:02d}" / "api"
    newest: dict[str, tuple[int, Path]] = {}
    for facts in api.glob(f"take-facts-ep{episode:02d}-t*-v*.json"):
        match = re.search(r"-(t\d+)-v(\d+)$", facts.stem)
        if match and int(match.group(2)) >= newest.get(match.group(1), (-1, facts))[0]:
            newest[match.group(1)] = (int(match.group(2)), facts)
    seen: set[Path] = set()
    for take_id in sorted(newest, key=lambda t: int(t[1:])):
        try:
            payload = json.loads(newest[take_id][1].read_text(encoding="utf-8"))
            cues = (
                plan_from_take_facts(payload).cues if isinstance(payload, dict) else ()
            )
        except (ValueError, KeyError, TypeError, OSError):
            continue
        for cue in cues:
            cached = desk / f"ep{episode:02d}" / "sfx" / f"{cue.cache_key}.mp3"
            if cue.kind == "sustained" and cached.is_file() and cached not in seen:
                seen.add(cached)
                yield cached, f"planned effect {cue.sound!r} on {take_id}"


def _episode_cues(desk: Path, episode: int) -> Iterator[tuple[Path, str]]:
    from creation.post.ambience import saved_ambience

    saved = saved_ambience(desk, episode)
    if saved is not None:
        yield saved.path, "location ambience"
    yield from _planned_sustained(desk, episode)
    folder = desk / f"ep{episode:02d}" / "sfx"
    for path in sorted(folder.glob("*")) if folder.is_dir() else []:
        if path.suffix.casefold() not in _AUDIO:
            continue
        sustained = _sidecar_sustained(path)
        if sustained or (sustained is None and _steady_name(path)):
            yield path, "hand cue"


def _desk_episodes(desk: Path) -> list[int]:
    found = []
    for folder in desk.glob("ep[0-9][0-9]"):
        if folder.is_dir():
            found.append(int(folder.name[2:]))
    return sorted(found)


def steady_cue_files(desk: Path, episodes: Sequence[int]) -> Iterator[tuple[Path, str]]:
    """Candidate steady sounds in the order they are preferred, with where each came from.

    This episode's first (``episodes``: the quiet take's, then the loud
    take's): its location ambience, the sustained effects planned on its
    takes, its sustained hand cues. Then the series': the same from the other
    episodes (nearest first), then steady-named files in the desk's ``sfx/``
    and ``shared/sfx/``.
    """

    seen: set[Path] = set()

    def fresh(
        items: Iterator[tuple[Path, str]], where: str
    ) -> Iterator[tuple[Path, str]]:
        for path, what in items:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield path, f"{where} {what} `{path.name}`"

    ordered = list(dict.fromkeys(episodes))
    for episode in ordered:
        yield from fresh(
            _episode_cues(desk, episode), f"this episode's (ep{episode:02d})"
        )
    anchor = ordered[0] if ordered else 1
    others = sorted(
        (n for n in _desk_episodes(desk) if n not in ordered),
        key=lambda n: (abs(n - anchor), n),
    )
    for episode in others:
        yield from fresh(_episode_cues(desk, episode), f"the series' (ep{episode:02d})")
    for folder in (desk / "sfx", desk / "shared" / "sfx"):
        files = sorted(folder.glob("*")) if folder.is_dir() else []
        yield from fresh(
            (
                (p, "cue")
                for p in files
                if p.suffix.casefold() in _AUDIO and _steady_name(p)
            ),
            "the series'",
        )


def quiet_passage(
    sound: np.ndarray, speech: Sequence[tuple[float, float]]
) -> np.ndarray | None:
    """The take's longest stretch of room (no speech, under the voice key, not silence), or ``None``."""

    levels = window_levels(sound)
    size = int(RATE * WINDOW_SECONDS)
    best: tuple[int, int] = (0, 0)
    start = None
    for index, level in enumerate([*levels, -200.0]):
        t = index * WINDOW_SECONDS + WINDOW_SECONDS / 2
        spoken = any(
            a - TRIM_SPEECH_PAD_SECONDS <= t < b + TRIM_SPEECH_PAD_SECONDS
            for a, b in speech
        )
        room = STEADY_FLOOR_DB < level <= QUIET_PASSAGE_CEILING_DB and not spoken
        if room and start is None:
            start = index
        elif not room and start is not None:
            if index - start > best[1] - best[0]:
                best = (start, index)
            start = None
    if (best[1] - best[0]) * WINDOW_SECONDS < QUIET_PASSAGE_MIN_SECONDS:
        return None
    return sound[best[0] * size : best[1] * size]


def room_tone(seconds: float, *, seed: int = 204) -> np.ndarray:
    """Band-limited (60-1500 Hz) brown room tone at :data:`ROOM_TONE_DB`, stereo, the same on every run."""

    count = max(int(seconds * RATE), RATE)
    white = np.random.default_rng(seed).standard_normal(count)
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(count, 1 / RATE)
    shape = np.where((freqs >= 60) & (freqs <= 1500), 1 / np.maximum(freqs, 1.0), 0.0)
    tone = np.fft.irfft(spectrum * shape, n=count)
    rms = float(np.sqrt(np.mean(tone**2))) or 1.0
    tone = tone / rms * 10 ** (ROOM_TONE_DB / 20)
    return np.stack([tone, tone], axis=1)


def find_steady_cue(
    desk: Path,
    episodes: Sequence[int],
    takes: Sequence[tuple[str, np.ndarray, Sequence[tuple[float, float]]]],
    *,
    decode: Callable[[Path], np.ndarray],
    tried: list[str],
) -> SteadyCue:
    """The first steady sound in :func:`steady_cue_files`, else room tone from the takes, else generated.

    Parameters
    ----------
    takes
        ``(label, sound, speech)`` of the loud take, then the quiet take: their
        own quiet passages are the next choice after the files.
    tried
        Candidates passed over and why are appended here.
    """

    for path, source in steady_cue_files(desk, episodes):
        try:
            samples = decode(path)
        except (MediaToolError, OSError) as exc:
            tried.append(f"passed over {source}: could not be read ({exc})")
            continue
        why, _ = steadiness(samples)
        if why is None:
            return SteadyCue(samples, source, path)
        tried.append(f"passed over {source}: {why}")
    for label, sound, speech in takes:
        passage = quiet_passage(sound, speech)
        if passage is None:
            continue
        levels = window_levels(passage)
        if (
            float(np.percentile(levels, 90) - np.percentile(levels, 10))
            <= STEADY_SPREAD_DB
        ):
            return SteadyCue(
                passage,
                f"room tone made from {label}'s own quiet passage "
                f"({len(passage) / RATE:.1f}s)",
            )
    return SteadyCue(
        room_tone(SEAM_BED_WINDOW_SECONDS + SEAM_BED_FAR_FADE_SECONDS + 1.0),
        "generated room tone (no steady cue on the desk and no quiet passage in the takes)",
    )


# --- laying a bed -------------------------------------------------------------------------------


@dataclass
class SeamBed:
    """One seam's bed: where it goes and how loud."""

    seam_index: int
    at: float
    #: True when the take after the cut is the quiet side.
    quiet_after: bool
    cue: SteadyCue
    gain_db: float = 0.0
    #: The level the quiet side is brought to (dB).
    target_db: float = 0.0
    #: Seconds on the joined file the bed covers (set when laid).
    span: tuple[float, float] = (0.0, 0.0)
    #: The bed's own level at full gain (0.1 s RMS median, dB), once laid.
    laid_db: float = -120.0

    def describe(self) -> str:
        """``laid ... under 2.50-4.50s at -31.2 dB (+16.0 dB on the cue)``."""

        side = "after" if self.quiet_after else "before"
        return (
            f"laid {self.cue.source} on the quiet side ({side} the cut) under "
            f"{self.span[0]:.2f}-{self.span[1]:.2f}s at {self.laid_db:.1f} dB "
            f"({self.gain_db:+.1f} dB on the cue), {SEAM_BED_EDGE_FADE_SECONDS:g}s ramp at the cut, "
            f"{SEAM_BED_FAR_FADE_SECONDS:g}s fade away from it"
        )


def _loop(samples: np.ndarray, count: int) -> np.ndarray:
    from creation.post.ambience import looped

    try:
        return looped(samples, count / RATE + 0.01)[:count]
    except ValueError:
        reps = int(math.ceil(count / max(len(samples), 1)))
        return np.tile(samples, (reps, 1))[:count]


def _ramp(count: int) -> np.ndarray:
    if count <= 0:
        return np.zeros(0)
    return 0.5 - 0.5 * np.cos(np.pi * (np.arange(count) + 0.5) / count)


def bed_layer(bed: SeamBed, total_samples: int) -> tuple[int, np.ndarray, float]:
    """The bed's samples at unity gain, where they start, and their level at full (dB).

    The quiet side after the cut: from the cut over :data:`SEAM_BED_WINDOW_SECONDS`,
    then the far fade. Before the cut: the mirror image, ending on the cut.
    """

    edge = int(SEAM_BED_EDGE_FADE_SECONDS * RATE)
    far = int(SEAM_BED_FAR_FADE_SECONDS * RATE)
    window = int(SEAM_BED_WINDOW_SECONDS * RATE)
    cut = int(round(bed.at * RATE))
    if bed.quiet_after:
        start, end = cut, min(total_samples, cut + window + far)
    else:
        start, end = max(0, cut - window - far), cut
    count = max(0, end - start)
    layer = _loop(bed.cue.samples, count) if count else np.zeros((0, 2))
    full = np.ones(count)
    if bed.quiet_after:
        full[: min(edge, count)] = _ramp(min(edge, count))
        tail = min(far, count)
        full[count - tail :] = np.minimum(full[count - tail :], _ramp(tail)[::-1])
        held = layer[: min(window, count)]
    else:
        head = min(far, count)
        full[:head] = _ramp(head)
        tail = min(edge, count)
        full[count - tail :] = np.minimum(full[count - tail :], _ramp(tail)[::-1])
        held = layer[max(0, count - window) :]
    levels = window_levels(held)
    heard = levels[levels > STEADY_FLOOR_DB]
    level = float(np.median(heard)) if heard.size else -120.0
    return start, layer * full[:, None], level


def bed_gain(
    quiet_db: float, loud_db: float, cue_db: float
) -> tuple[float, float] | None:
    """Gain on the cue that brings the quiet side to :data:`SEAM_FIX_TARGET_DB` under the loud side.

    Powers add: the bed fills the gap between what the quiet side has and the
    target. Capped at :data:`SEAM_BED_MAX_DB` and :data:`SEAM_BED_MAX_BOOST_DB`.

    Returns
    -------
    tuple[float, float] | None
        ``(gain dB, target dB)``, or ``None`` when the quiet side is already there.
    """

    target = loud_db - SEAM_FIX_TARGET_DB
    need = 10 ** (target / 10) - 10 ** (quiet_db / 10)
    if need <= 0 or cue_db <= -119:
        return None
    wanted = min(10 * math.log10(need), SEAM_BED_MAX_DB)
    return round(min(wanted - cue_db, SEAM_BED_MAX_BOOST_DB), 1), target


def capped_gain(gain_db: float, cue_db: float) -> float:
    """``gain_db`` held under :data:`SEAM_BED_MAX_DB` and :data:`SEAM_BED_MAX_BOOST_DB`."""

    return round(min(gain_db, SEAM_BED_MAX_DB - cue_db, SEAM_BED_MAX_BOOST_DB), 1)


def lay_beds(sound: np.ndarray, beds: Sequence[SeamBed]) -> np.ndarray:
    """``sound`` with each bed added at its gain inside its span only (a copy; peaks kept under 0.97).

    ``join`` lays them on silence: the seam layer it mixes in before the limiter.
    """

    out = sound.copy()
    for bed in beds:
        start, layer, level = bed_layer(bed, len(out))
        if not len(layer):
            continue
        scaled = layer * 10 ** (bed.gain_db / 20)
        here = out[start : start + len(scaled)]
        peak = float(np.abs(here + scaled).max()) if here.size else 0.0
        if peak > 0.97:
            room = max(0.0, 0.97 - float(np.abs(here).max()))
            top = float(np.abs(scaled).max()) or 1.0
            factor = min(1.0, room / top)
            scaled = scaled * factor
            bed.gain_db = round(bed.gain_db + 20 * math.log10(max(factor, 1e-6)), 1)
        out[start : start + len(scaled)] += scaled
        bed.span = (round(start / RATE, 2), round((start + len(scaled)) / RATE, 2))
        bed.laid_db = round(level + bed.gain_db, 1)
    return out


# --- trimming a silent head ---------------------------------------------------------------------


@dataclass(frozen=True)
class Trim:
    """A silent head (or tail) cut off one part on a filmed cut."""

    part_index: int
    label: str
    head: bool
    seconds: float
    frame: int
    silent_seconds: float

    def describe(self) -> str:
        """``trimmed ep01 t2's silent head: 0.00-1.21s cut on the filmed cut at frame 29``."""

        where = "head" if self.head else "tail"
        span = (
            f"0.00-{self.seconds:.2f}s"
            if self.head
            else f"its last {self.seconds:.2f}s"
        )
        return (
            f"trimmed {self.label}'s silent {where} ({span}; near-silent for "
            f"{self.silent_seconds:.2f}s) on the filmed cut at frame {self.frame}"
        )


def plan_silent_trim(
    *,
    part_index: int,
    label: str,
    sound: np.ndarray,
    seconds: float,
    speech: Sequence[tuple[float, float]],
    cuts: Sequence[float],
    head: bool,
    fps: float = 24.0,
) -> Trim | str:
    """A trim of the part's near-silent head (``head``) or tail on a filmed cut, or why there is none.

    The near-silent stretch runs until the part's own sound first rises over
    :data:`SILENT_HEAD_DB` or known speech starts (less
    :data:`TRIM_SPEECH_PAD_SECONDS`). The trim ends on the last filmed cut inside
    it (a tail trim starts on the first), at least :data:`TRIM_MIN_SECONDS` and at
    most :data:`TRIM_MAX_FRACTION` of the part. No filmed cut there: no trim.
    """

    levels = window_levels(sound)[: int(seconds / WINDOW_SECONDS)]
    loud = np.flatnonzero(levels > SILENT_HEAD_DB)
    where = "head" if head else "tail"
    if loud.size == 0 and not speech:
        return f"{label}: its {where} is silent end to end (nothing to keep)"
    limit = TRIM_MAX_FRACTION * seconds
    if head:
        silent_end = loud[0] * WINDOW_SECONDS if loud.size else seconds
        if speech:
            silent_end = min(
                silent_end, min(a for a, _ in speech) - TRIM_SPEECH_PAD_SECONDS
            )
        reach = min(silent_end, limit)
        frames = [round(t * fps) for t in cuts if TRIM_MIN_SECONDS <= t <= reach + 1e-6]
        frames = [
            f for f in frames if TRIM_MIN_SECONDS - 1e-6 <= f / fps <= reach + 1e-6
        ]
        if silent_end < TRIM_MIN_SECONDS:
            return f"{label}: its head is not near-silent ({max(silent_end, 0):.2f}s before its first sound)"
        if not frames:
            return (
                f"{label}: its head is near-silent for {silent_end:.2f}s but has no filmed cut inside it "
                "to trim on"
            )
        frame = max(frames)
        return Trim(
            part_index, label, True, round(frame / fps, 4), frame, round(silent_end, 2)
        )
    silent_start = (loud[-1] + 1) * WINDOW_SECONDS if loud.size else 0.0
    if speech:
        silent_start = max(
            silent_start, max(b for _, b in speech) + TRIM_SPEECH_PAD_SECONDS
        )
    silent = seconds - silent_start
    if silent < TRIM_MIN_SECONDS:
        return f"{label}: its tail is not near-silent ({max(silent, 0):.2f}s after its last sound)"
    frames = [round(t * fps) for t in cuts]
    frames = [
        f for f in frames
        if f / fps >= silent_start - 1e-6 and TRIM_MIN_SECONDS - 1e-6 <= seconds - f / fps <= limit + 1e-6
    ]  # fmt: skip
    if not frames:
        return f"{label}: its tail is near-silent for {silent:.2f}s but has no filmed cut inside it to trim on"
    frame = min(frames)
    return Trim(
        part_index,
        label,
        False,
        round(seconds - frame / fps, 4),
        frame,
        round(silent, 2),
    )


# --- the fix ------------------------------------------------------------------------------------


@dataclass
class Joined:
    """One built join: its master, seams, the speech on it and each seam's levels."""

    master: Path
    seams: list[float]
    speech: list[tuple[float, float]]
    levels: list[SeamLevel]
    heads: list[float]
    tails: list[float]
    mixed: Any = None
    #: The join's speech notes (which speech was left out of the measure, short thoughts).
    notes: list[str] = field(default_factory=list)
    #: What the caller needs to mix this join again with a seam bed (``join``: the bedless take, bed, gain).
    sources: Any = None

    def steps(self) -> list[float]:
        return [level.step_db for level in self.levels]


@dataclass
class SeamFixReport:
    """What ``join`` tried on its loud seams, and how each seam came out."""

    before: list[float]
    after: list[float]
    laid: list[str] = field(default_factory=list)
    trimmed: list[str] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)
    fixed: bool = False
    seams_before: list[float] = field(default_factory=list)
    seams_after: list[float] = field(default_factory=list)

    def lines(self, threshold: float) -> list[str]:
        """Operator lines: what was laid / trimmed and the before -> after steps, or what was tried."""

        steps = ", ".join(
            f"{at:.2f}s {a:+.1f} -> {b:+.1f} dB"
            for at, a, b in zip(self.seams_after, self.before, self.after, strict=True)
            if abs(a) > threshold
        )
        if self.fixed:
            head = f"SEAM FIXED by join (free): {steps}"
            return [head, *(f"  {line}" for line in [*self.trimmed, *self.laid])]
        head = f"SEAM FIX TRIED (free), still over {threshold:.0f} dB: {steps}"
        return [head, *(f"  tried: {line}" for line in self.tried)]

    def as_json(self) -> dict[str, Any]:
        return {
            "fixed": self.fixed,
            "steps_before_db": self.before,
            "steps_after_db": self.after,
            "laid": self.laid,
            "trimmed": self.trimmed,
            "tried": self.tried,
        }


def _seam_parts(index: int, quiet_after: bool) -> tuple[int, int]:
    """``(quiet part, loud part)`` of seam ``index``."""

    return (index + 1, index) if quiet_after else (index, index + 1)


def lay_seam_beds(
    joined: Joined,
    loud: Sequence[int],
    *,
    pick_cue: Callable[[int, bool], SteadyCue],
    measure: Callable[[Path, list[float], list[tuple[float, float]]], list[SeamLevel]],
    render: Callable[[Joined, list[SeamBed], Path], Path],
    scratch: Path,
    tag: str,
    threshold: float,
    tried: list[str],
) -> tuple[Joined, list[SeamBed]]:
    """Lay a bed under every loud seam of ``joined``, correcting the level; keep only beds that helped.

    Returns
    -------
    tuple[Joined, list[SeamBed]]
        The bedded join (``joined`` itself when no bed helped) and the beds kept.
    """

    original = joined.steps()
    total = int(round(media_duration(joined.master) * RATE))
    beds: list[SeamBed] = []
    for index in loud:
        level = joined.levels[index]
        quiet_after = level.step_db < 0
        quiet_db, loud_db = (
            (level.after_db, level.before_db)
            if quiet_after
            else (level.before_db, level.after_db)
        )
        bed = SeamBed(
            index, joined.seams[index], quiet_after, pick_cue(index, quiet_after)
        )
        _, _, cue_db = bed_layer(bed, total)
        found = bed_gain(quiet_db, loud_db, cue_db)
        if found is None:
            tried.append(
                f"seam at {bed.at:.2f}s: no bed (the quiet side is not under the loud side by the measure)"
            )
            continue
        bed.gain_db, bed.target_db = found
        beds.append(bed)
    if not beds:
        return joined, []
    best: dict[int, tuple[float, float]] = {}
    last: tuple[Path, list[SeamLevel], list[float]] | None = None
    for attempt in range(1, SEAM_BED_PASSES + 1):
        candidate = render(joined, beds, scratch / f"seam-bed-{tag}-{attempt}.mp4")
        levels = measure(candidate, joined.seams, joined.speech)
        last = (candidate, levels, [bed.gain_db for bed in beds])
        for bed in beds:
            step = levels[bed.seam_index].step_db
            if bed.seam_index not in best or abs(step) < abs(best[bed.seam_index][1]):
                best[bed.seam_index] = (bed.gain_db, step)
        if all(abs(levels[b.seam_index].step_db) <= threshold for b in beds):
            break
        for bed in beds:
            level = levels[bed.seam_index]
            quiet_now = level.after_db if bed.quiet_after else level.before_db
            _, _, cue_db = bed_layer(bed, total)
            bed.gain_db = capped_gain(bed.gain_db + (bed.target_db - quiet_now), cue_db)
    kept = []
    for bed in beds:
        gain, step = best[bed.seam_index]
        if abs(step) < abs(original[bed.seam_index]):
            bed.gain_db = gain
            kept.append(bed)
        else:
            tried.append(
                f"seam at {bed.at:.2f}s: {bed.cue.source} did not make it smaller "
                f"({original[bed.seam_index]:+.1f} -> {step:+.1f} dB); not laid"
            )
    if not kept:
        return joined, []
    if (
        last is not None
        and len(kept) == len(beds)
        and last[2] == [bed.gain_db for bed in kept]
    ):
        final, levels = last[0], last[1]
    else:
        final = render(joined, kept, scratch / f"seam-bed-{tag}-final.mp4")
        levels = measure(final, joined.seams, joined.speech)
    return (
        Joined(
            final,
            joined.seams,
            joined.speech,
            levels,
            joined.heads,
            joined.tails,
            joined.mixed,
            joined.notes,
            joined.sources,
        ),  # fmt: skip
        kept,
    )


def fix_seams(
    joined: Joined,
    *,
    parts: Sequence[JoinPart],
    threshold: float,
    pick_cue: Callable[[Joined, int, bool], SteadyCue],
    plan_trim: Callable[[int, bool], Trim | str],
    rejoin: Callable[[list[float], list[float], Path], Joined],
    measure: Callable[[Path, list[float], list[tuple[float, float]]], list[SeamLevel]],
    render: Callable[[Joined, list[SeamBed], Path], Path],
    scratch: Path,
) -> tuple[Joined, SeamFixReport]:
    """Try a steady bed, then a silent-head trim with the bed again, on every seam over ``threshold``.

    Returns
    -------
    tuple[Joined, SeamFixReport]
        The join to keep (``joined`` itself unless every seam now passes) and the report.
    """

    before = joined.steps()
    report = SeamFixReport(before=before, after=before, seams_before=list(joined.seams),
                           seams_after=list(joined.seams))  # fmt: skip
    loud = [i for i, step in enumerate(before) if abs(step) > threshold]
    if not loud:
        return joined, report

    def passes(candidate: Joined) -> bool:
        steps = candidate.steps()
        return all(abs(s) <= threshold for s in steps) and all(
            abs(steps[i]) < abs(before[i]) for i in loud
        )

    def bedded(base: Joined, tag: str) -> tuple[Joined, list[SeamBed]]:
        still = [i for i, s in enumerate(base.steps()) if abs(s) > threshold]
        if not still:
            return base, []
        return lay_seam_beds(
            base, still, pick_cue=lambda i, after: pick_cue(base, i, after), measure=measure,
            render=render, scratch=scratch, tag=tag, threshold=threshold,
            tried=report.tried,
        )  # fmt: skip

    first, beds = bedded(joined, "bed")
    for bed in beds:
        report.tried.append(
            f"seam at {bed.at:.2f}s: {bed.describe()} -> {first.levels[bed.seam_index].step_db:+.1f} dB"
        )
    if beds and passes(first):
        report.fixed, report.after = True, first.steps()
        report.laid = [bed.describe() for bed in beds]
        return first, report

    trims: list[Trim] = []
    for index in [i for i, s in enumerate(first.steps()) if abs(s) > threshold]:
        quiet_after = before[index] < 0
        quiet, _ = _seam_parts(index, quiet_after)
        planned = plan_trim(quiet, quiet_after)
        if isinstance(planned, str):
            report.tried.append(
                f"seam at {joined.seams[index]:.2f}s: no trim: {planned}"
            )
            continue
        if any(
            t.part_index == planned.part_index and t.head == planned.head for t in trims
        ):
            continue
        trims.append(planned)
    if not trims:
        report.after = first.steps()
        return joined, report
    heads = list(joined.heads)
    tails = list(joined.tails)
    for trim in trims:
        if trim.head:
            heads[trim.part_index] += trim.seconds
        else:
            tails[trim.part_index] += trim.seconds
    try:
        trimmed = rejoin(heads, tails, scratch / "seam-trim.mp4")
    except (MediaToolError, ValueError) as exc:
        report.tried.append(f"trim not joined: {exc}")
        report.after = first.steps()
        return joined, report
    trim_lines = [trim.describe() for trim in trims]
    report.tried += [
        f"{line} -> seams {', '.join(f'{s:+.1f}' for s in trimmed.steps())} dB"
        for line in trim_lines
    ]
    final, beds = bedded(trimmed, "trim-bed")
    for bed in beds:
        report.tried.append(
            f"after the trim, seam at {bed.at:.2f}s: {bed.describe()} -> "
            f"{final.levels[bed.seam_index].step_db:+.1f} dB"
        )
    if passes(final):
        report.fixed, report.after = True, final.steps()
        report.seams_after = list(final.seams)
        report.trimmed = trim_lines
        report.laid = [bed.describe() for bed in beds]
        return final, report
    best = min((first, final), key=lambda c: max(abs(s) for s in c.steps()))
    report.after = best.steps()
    return joined, report


def keep(joined: Joined, master: Path) -> None:
    """Put the kept join's file at ``master`` (the path this join run reserved)."""

    if joined.master != master:
        shutil.move(str(joined.master), str(master))
        joined.master = master


__all__ = [
    "SEAM_BED_MAX_DB",
    "SEAM_FIX_TARGET_DB",
    "Joined",
    "SeamBed",
    "SeamFixReport",
    "SteadyCue",
    "Trim",
    "bed_gain",
    "find_steady_cue",
    "fix_seams",
    "keep",
    "lay_beds",
    "lay_seam_beds",
    "plan_silent_trim",
    "quiet_passage",
    "room_tone",
    "steadiness",
    "steady_cue_files",
    "window_levels",
]
