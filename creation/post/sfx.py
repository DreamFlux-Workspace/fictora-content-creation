"""A take's sound effects, laid locally from its take facts (never from the compiled prompt).

The cue plan comes from ``GET /v1/jobs/{take_job}/take-facts`` (``sfx_cues``:
the authored Sound label, shot, start, length, kind; ``shots[].speaks`` for the
speaking windows). Each cue is rendered on the server from its authored Sound
label (:class:`~creation.post.audio_service.AudioService`; about $0.002 a
second; no provider key or effect prompt on this laptop), is checked for shape (an event must hit early, a
sustained sound must hold), and is cached in ``epNN/sfx/`` by sound and length,
so a re-mix at another level costs nothing. Effects sit under the take
(-8 dB by default) and drop a further 10 dB while someone speaks.

The story's drop and level sound notes ("no purring", "the door slam is too
loud") are applied by the server when the facts are fetched with ``spine_id``
(fictora-drama #475): a dropped cue is left out of ``sfx_cues`` and listed in
``sfx_dropped_cues``; a levelled cue carries ``gain_offset_db`` (from the
default level) and ``note_ids``. This kit reads that plan as it is and never
applies the notes a second time. Facts from an older server carry none of
these fields and are laid exactly as before. ``finish --sfx-adjust`` is a
manual per-take change on top.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from creation.harness_rules import is_opening_cue
from creation.post.audio_service import AudioService, download
from creation.rules_epoch import legacy_rules
from creation.post.media import (
    LIMITER,
    AudioLevels,
    MediaToolError,
    measure_levels,
    measure_rms_windows,
    probe_video,
    run_ffmpeg,
)

_log = logging.getLogger(__name__)

SFX_USD_PER_SECOND = 0.002
SFX_GAIN_DB = -8.0
SFX_GAIN_RANGE_DB = (-30.0, 0.0)
SFX_MIN_SECONDS = 0.5
SFX_MAX_SECONDS = 22.0
SFX_MIN_PLACED_SECONDS = 0.25
SFX_SPEECH_DUCK_DB = -10.0
#: The episode's opening hook at 0 s: at most 3 dB over an ordinary effect (it was 4 dB until the
#: writer's own events became hooks, fictora-drama 6 Oct 2026). A ceiling, never a target: each laid
#: hook is measured and levelled under the take (:func:`opening_hook_gain_db`), the same rule as the
#: server's ``opening_sound.opening_hook_level``. The finish's one limiter (``media.LIMITER``) still
#: holds peaks at about -1 dBFS.
OPENING_SOUND_GAIN_DB = -5.0
#: What the hook is held under when the take's audio cannot be measured: a spoken line's level
#: (the server levels locked voices to -18 LUFS) and a true peak under nine in ten levelled lines'.
OPENING_HOOK_REFERENCE_LUFS = -18.0
OPENING_HOOK_REFERENCE_PEAK_DBTP = -6.0
#: How far the hook's loudest 400 ms may sit over the take's loudness: none.
OPENING_HOOK_OVER_TAKE_LU = 0.0
SILENCE_DB = -50.0
#: How much longer a re-made cue is asked for (the server caches cues by content).
REMAKE_NUDGE_SECONDS = 0.1

Renderer = Callable[["SfxCue", Path], Path]
Meter = Callable[[Path], tuple[float, ...]]


@dataclass(frozen=True)
class SfxCue:
    """One effect to render and place, in take seconds."""

    shot_index: int
    sound: str
    kind: str
    start: float
    seconds: float
    gain_db: float = SFX_GAIN_DB
    #: The story's sound notes that moved this cue's level (from the take facts), oldest first.
    note_ids: tuple[str, ...] = ()
    #: Where the take facts say the cue came from: ``sound_line``, ``impact`` (an action the story
    #: states) or ``note``. Read only by the action snap (:mod:`creation.post.sfx_motion`).
    source: str = "sound_line"
    #: 0 for the first render; 1 when a render came back silent or the wrong shape and the SAME
    #: cue (same sound label, same placement) is asked for once more. Never part of the cache key.
    remake: int = 0

    @property
    def cache_key(self) -> str:
        """Same sound, kind and length -> same file."""

        raw = json.dumps(
            [self.sound.strip().casefold(), self.kind, round(self.seconds, 2)]
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class SfxPlan:
    """A take's cues and speaking windows."""

    cues: tuple[SfxCue, ...]
    speech: tuple[tuple[float, float], ...]
    #: Planned cues a sound note dropped (never mixed): ``"<sound> (note <id>)"`` each.
    dropped: tuple[str, ...] = ()


@dataclass(frozen=True)
class Adjustment:
    """``door=-6``, ``hum=drop``, ``shot:3=+4``."""

    match: str = ""
    shot_index: int | None = None
    gain_change_db: float = 0.0
    drop: bool = False


def parse_adjustment(raw: str) -> Adjustment:
    """Read one ``--sfx-adjust`` value.

    Raises
    ------
    ValueError
        When it does not parse or changes nothing.
    """

    target, sep, change = raw.rpartition("=")
    if not sep or not change.strip() or not target.strip():
        raise ValueError(
            f"--sfx-adjust wants <sound words or shot:N>=<dB or drop>, got {raw!r}"
        )
    target = target.strip()
    shot: int | None = None
    if target.casefold().startswith("shot:"):
        shot, target = int(target.split(":", 1)[1]), ""
    if change.strip().casefold() == "drop":
        return Adjustment(match=target, shot_index=shot, drop=True)
    gain = float(change)
    if gain == 0.0:
        raise ValueError(f"--sfx-adjust {raw!r} changes nothing")
    return Adjustment(match=target, shot_index=shot, gain_change_db=gain)


def _applies(cue: SfxCue, adjustment: Adjustment) -> bool:
    if adjustment.shot_index is not None:
        return cue.shot_index == adjustment.shot_index
    words = re.findall(r"\w+", adjustment.match.casefold())
    return bool(words) and all(word in cue.sound.casefold() for word in words)


#: A hand cue this close to an auto cue of the same sound is probably the same event laid twice.
DUPLICATE_CUE_SECONDS = 1.0
#: Words that do not say what a sound is ("a", "sound of").
_CUE_FILLER = frozenset(
    {"a", "an", "the", "of", "and", "with", "on", "in", "at", "to", "sound", "sounds", "cue",
     "soft", "loud", "heavy", "light", "small", "big", "distant", "close", "short", "long"}
)  # fmt: skip


def _cue_words(text: str) -> set[str]:
    """Content words of a cue, with a trailing ``s`` folded so ``slam`` matches ``slams``."""

    words: set[str] = set()
    for word in re.findall(r"[a-z]+", text.casefold()):
        if len(word) <= 2 or word in _CUE_FILLER:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        words.add(word)
    return words


def duplicate_cue_warnings(
    hand: tuple[tuple[str, float], ...],
    laid: tuple[tuple[str, float], ...],
    *,
    window: float = DUPLICATE_CUE_SECONDS,
) -> list[str]:
    """Warn when a hand cue lands on an auto cue of the same sound (the same event twice).

    Parameters
    ----------
    hand
        ``(description, start)`` of each ``--cue`` (the ``cue`` file's description).
    laid
        ``(sound, start)`` of each cue the ``sfx`` step laid from the take facts.
    window
        Seconds apart that still counts as the same moment.

    Returns
    -------
    list[str]
        One ``!!`` line per clash, with the ``--sfx-adjust "<word>=drop"`` that
        takes the auto cue out (the word is checked to match that cue).
    """

    warnings: list[str] = []
    for description, start in hand:
        words = _cue_words(description)
        for sound, at in laid:
            if abs(at - start) > window:
                continue
            laid_words = _cue_words(sound)
            shorter, longer = (
                (words, laid_words)
                if len(words) <= len(laid_words)
                else (laid_words, words)
            )
            # One shared word ("ceramic", "wet") is not the same event.
            if not shorter or not shorter <= longer:
                continue
            probe = SfxCue(0, sound, "event", at, 0.0)
            shared = [
                word
                for word in sorted(shorter)
                if _applies(probe, Adjustment(match=word))
            ]
            if not shared:
                continue
            key = shared[0]
            warnings.append(
                f'!! hand cue "{description}" @{start:.2f}s is {abs(at - start):.2f}s from the auto '
                f'cue "{sound}" @{at:.2f}s: the same sound twice. Keep one: finish again with '
                f'--sfx-adjust "{key}=drop" (takes out every auto "{key}" cue on this take)'
            )
    return warnings


def apply_adjustments(
    cues: tuple[SfxCue, ...], adjustments: tuple[Adjustment, ...]
) -> tuple[SfxCue, ...]:
    """Drop or re-level the cues an adjustment names, on top of the level the take facts gave; gains stay inside -30..0 dB."""

    out: list[SfxCue] = []
    for cue in cues:
        keep = True
        for adjustment in adjustments:
            if not _applies(cue, adjustment):
                continue
            if adjustment.drop:
                keep = False
                break
            cue = replace(
                cue, gain_db=_clamp_gain(cue.gain_db + adjustment.gain_change_db)
            )
        if keep:
            out.append(cue)
    return tuple(out)


def _clamp_gain(gain: float) -> float:
    return max(SFX_GAIN_RANGE_DB[0], min(SFX_GAIN_RANGE_DB[1], gain))


def _cue_identity(cue: dict[str, Any]) -> tuple[Any, ...]:
    return (
        cue.get("shot_index"),
        str(cue.get("sound") or "").strip().casefold(),
        round(float(cue.get("start_seconds") or 0.0), 2),
        round(float(cue.get("duration_seconds") or 0.0), 2),
    )


def noted_gain_db(cue: dict[str, Any]) -> float:
    """The level a take-facts cue is mixed at: the default plus the server's ``gain_offset_db`` (clamped).

    Parameters
    ----------
    cue
        One ``take_facts.sfx_cues`` entry.

    Returns
    -------
    float
        :data:`SFX_GAIN_DB` when the cue carries no offset (an older server, or no level note);
        :data:`OPENING_SOUND_GAIN_DB` for the episode's opening hook.
    """

    # The episode's opening hook plays at its own known level (fictora-drama opening_sound).
    base = (
        OPENING_SOUND_GAIN_DB
        if is_opening_cue(str(cue.get("sound") or ""))
        else SFX_GAIN_DB
    )
    offset = cue.get("gain_offset_db")
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        return base
    return _clamp_gain(base + float(offset))


def plan_from_take_facts(payload: dict[str, Any]) -> SfxPlan:
    """Cues and speaking windows from a saved take-facts response.

    Each cue is mixed at the default level plus its ``gain_offset_db`` when the
    server sent one; a cue in ``sfx_dropped_cues`` is never mixed. The story's
    sound notes are not applied here again: the server already did.

    Parameters
    ----------
    payload
        ``GET /v1/jobs/{id}/take-facts`` JSON (``{"take_facts": {...}}`` or the facts alone).

    Returns
    -------
    SfxPlan
        The plan.
    """

    facts = payload.get("take_facts", payload)
    dropped_cues = [
        cue
        for cue in facts.get("sfx_dropped_cues") or []
        if isinstance(cue, dict) and str(cue.get("sound") or "").strip()
    ]
    dropped_ids = {_cue_identity(cue) for cue in dropped_cues}
    cues = tuple(
        SfxCue(
            shot_index=int(cue["shot_index"]),
            sound=str(cue["sound"]),
            kind="sustained" if cue.get("kind") == "sustained" else "event",
            start=float(cue["start_seconds"]),
            seconds=float(cue["duration_seconds"]),
            gain_db=noted_gain_db(cue),
            note_ids=tuple(str(note) for note in cue.get("note_ids") or []),
            source=str(cue.get("source") or "sound_line"),
        )
        for cue in facts.get("sfx_cues") or []
        if str(cue.get("sound") or "").strip() and _cue_identity(cue) not in dropped_ids
    )
    speech = tuple(
        (float(shot["start_seconds"]), float(shot["end_seconds"]))
        for shot in facts.get("shots") or []
        if shot.get("speaks")
        and float(shot["end_seconds"]) > float(shot["start_seconds"])
    )
    dropped = tuple(
        f"{cue['sound']} (note {cue.get('dropped_by_note_id') or '?'})"
        for cue in dropped_cues
    )
    return SfxPlan(cues, speech, dropped)


# --- filmed cuts -----------------------------------------------------------------------------------

#: A planned shot change moves to the nearest measured cut within this (fictora-drama #487's window).
FILMED_CUT_WINDOW_SECONDS = 1.0
#: Measured cuts this close to either end of the take are ignored (the board-frame flash at the head).
FILMED_CUT_EDGE_SECONDS = 0.25

Cuts = Callable[[Path], tuple[float, ...]]
"""``take -> hard-cut times`` (:func:`creation.post.edit.measure_cuts` in production)."""


@dataclass(frozen=True)
class PlannedShot:
    """One shot of the take facts: its index and planned window, in take seconds."""

    index: int
    start: float
    end: float


def planned_shots(payload: dict[str, Any]) -> tuple[PlannedShot, ...]:
    """The take facts' shots in order (``shots[]``: ``shot_index``, ``start_seconds``, ``end_seconds``).

    Parameters
    ----------
    payload
        ``GET /v1/jobs/{id}/take-facts`` JSON (``{"take_facts": {...}}`` or the facts alone).

    Returns
    -------
    tuple[PlannedShot, ...]
        Shots with a positive window, earliest first; empty when the facts carry none.
    """

    facts = payload.get("take_facts", payload)
    shots: list[PlannedShot] = []
    for shot in facts.get("shots") or []:
        try:
            item = PlannedShot(
                int(shot["shot_index"]),
                float(shot["start_seconds"]),
                float(shot["end_seconds"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if item.end > item.start:
            shots.append(item)
    return tuple(sorted(shots, key=lambda shot: shot.start))


@dataclass(frozen=True)
class FilmedShots:
    """Each planned shot's window on the take as filmed.

    Parameters
    ----------
    planned
        The take facts' shots.
    windows
        ``shot_index -> (start, end)`` as filmed.
    moved
        ``(planned change, filmed cut)`` for each shot change that followed a measured cut.
    kept
        Planned shot changes with no measured cut near them (kept where planned).
    """

    planned: tuple[PlannedShot, ...]
    windows: dict[int, tuple[float, float]]
    moved: tuple[tuple[float, float], ...]
    kept: tuple[float, ...]
    #: How far a planned change could move to a measured cut.
    window: float = FILMED_CUT_WINDOW_SECONDS
    #: Every hard cut measured on the take, earliest first; printed only when ``show_measured``.
    measured: tuple[float, ...] = ()
    show_measured: bool = False

    def one_line(self) -> str:
        """``shot changes on the filmed cuts: 3.80->3.88s, 7.50->7.33s; kept as planned: 11.20s``."""

        parts: list[str] = []
        if self.show_measured:
            parts.append(
                "measured cuts: "
                + (", ".join(f"{t:.2f}s" for t in self.measured) or "none")
            )
        if self.moved:
            parts.append(
                "shot changes on the filmed cuts: "
                + ", ".join(f"{a:.2f}->{b:.2f}s" for a, b in self.moved)
            )
        if self.kept:
            parts.append(
                f"no cut within {self.window:g} s, kept as planned: "
                + ", ".join(f"{t:.2f}s" for t in self.kept)
            )
        return "; ".join(parts) or "no shot changes planned"


def filmed_shot_windows(
    shots: tuple[PlannedShot, ...],
    cuts: tuple[float, ...],
    *,
    duration: float,
    window: float = FILMED_CUT_WINDOW_SECONDS,
    show_measured: bool = False,
) -> FilmedShots:
    """Move each planned shot change to the nearest measured cut within ``window`` (default 1 s).

    Each measured cut is used once, and the changes stay in order. A change with
    no cut near it stays where it was planned. The first shot's start and the
    last shot's end never move.

    Parameters
    ----------
    shots
        :func:`planned_shots`.
    cuts
        Hard-cut times measured on the take.
    duration
        The take's length (cuts within :data:`FILMED_CUT_EDGE_SECONDS` of either end are ignored).
    window
        How far a change may move (:data:`FILMED_CUT_WINDOW_SECONDS`; a locked-voice take widens it).
    show_measured
        Print every measured cut in :meth:`FilmedShots.one_line`.

    Returns
    -------
    FilmedShots
        The filmed windows and what moved.
    """

    free = sorted(
        cut
        for cut in cuts
        if FILMED_CUT_EDGE_SECONDS < cut < duration - FILMED_CUT_EDGE_SECONDS
    )
    starts = [shot.start for shot in shots]
    moved: list[tuple[float, float]] = []
    kept: list[float] = []
    previous = starts[0] if starts else 0.0
    for position in range(1, len(shots)):
        change = shots[position].start
        near = [cut for cut in free if abs(cut - change) <= window and cut > previous]
        if near:
            cut = min(near, key=lambda value: abs(value - change))
            free.remove(cut)
            starts[position] = cut
            moved.append((change, cut))
        else:
            kept.append(change)
            starts[position] = max(change, previous)
        previous = starts[position]
    windows = {
        shot.index: (
            starts[position],
            starts[position + 1] if position + 1 < len(shots) else shot.end,
        )
        for position, shot in enumerate(shots)
    }
    measured = tuple(
        sorted(
            round(cut, 3)
            for cut in cuts
            if FILMED_CUT_EDGE_SECONDS < cut < duration - FILMED_CUT_EDGE_SECONDS
        )
    )
    return FilmedShots(
        shots, windows, tuple(moved), tuple(kept), window, measured, show_measured
    )


def _on_filmed(time: float, shot: PlannedShot, filmed: FilmedShots) -> float:
    """``time`` at the same fraction of ``shot`` as filmed."""

    start, end = filmed.windows[shot.index]
    fraction = min(1.0, max(0.0, (time - shot.start) / (shot.end - shot.start)))
    return round(start + fraction * (end - start), 3)


def _shot_at(time: float, shots: tuple[PlannedShot, ...]) -> PlannedShot | None:
    for shot in shots:
        if shot.start <= time < shot.end:
            return shot
    return shots[-1] if shots and abs(time - shots[-1].end) < 1e-6 else None


def follow_filmed_cuts(plan: SfxPlan, filmed: FilmedShots) -> SfxPlan:
    """Place each cue and speaking window on the shots as filmed, at the same fraction of its shot.

    What fictora-drama #487 does in the server's mix when it is given the planned
    shots: an event keeps its length and moves with its shot; a sustained
    sound's end moves with its shot too when it ends inside it; a cue whose shot
    the facts do not list stays where it was planned.

    Parameters
    ----------
    plan
        :func:`plan_from_take_facts`.
    filmed
        :func:`filmed_shot_windows`.

    Returns
    -------
    SfxPlan
        The same cues and windows on the filmed shots.
    """

    by_index = {shot.index: shot for shot in filmed.planned}
    cues: list[SfxCue] = []
    for cue in plan.cues:
        shot = by_index.get(cue.shot_index)
        if shot is None:
            cues.append(cue)
            continue
        start = _on_filmed(cue.start, shot, filmed)
        seconds = cue.seconds
        end = cue.start + cue.seconds
        if cue.kind == "sustained" and shot.start < end <= shot.end + 1e-6:
            seconds = max(
                SFX_MIN_PLACED_SECONDS, round(_on_filmed(end, shot, filmed) - start, 3)
            )
        cues.append(replace(cue, start=start, seconds=seconds))
    speech: list[tuple[float, float]] = []
    for a, b in plan.speech:
        first, last = _shot_at(a, filmed.planned), _shot_at(b - 1e-6, filmed.planned)
        speech.append(
            (
                _on_filmed(a, first, filmed) if first else a,
                _on_filmed(b, last, filmed) if last else b,
            )
        )
    return SfxPlan(tuple(cues), tuple(speech), plan.dropped)


def saved_take_facts(desk: Path, episode: int, take_id: str) -> Path | None:
    """Newest ``epNN/api/take-facts-epNN-tK-vN.json``."""

    api = desk / f"ep{episode:02d}" / "api"
    found = list(api.glob(f"take-facts-ep{episode:02d}-{take_id}-v*.json"))

    def version(path: Path) -> int:
        match = re.search(r"-v(\d+)$", path.stem)
        return int(match.group(1)) if match else 0

    return max(found, key=version) if found else None


#: New desks (6 Oct 2026 on): no one-second stretch of a sustained cue, after its first, may sit more
#: than this far under the cue's median level (L-20261001-4: a decaying "constant" bed passed).
SUSTAINED_HOLD_DB = 9.0
#: The stretch the hold is measured on.
SUSTAINED_HOLD_SECONDS = 1.0


def _second_levels(levels: tuple[float, ...], window_seconds: float) -> list[float]:
    """The windows pooled into whole one-second stretches (power mean, dB); a short last stretch is left out."""

    per = max(1, round(SUSTAINED_HOLD_SECONDS / window_seconds))
    pooled: list[float] = []
    for start in range(0, len(levels) - per + 1, per):
        block = levels[start : start + per]
        power = sum(10 ** (level / 10) for level in block) / len(block)
        pooled.append(10 * math.log10(power) if power > 0 else -120.0)
    return pooled


def shape_problem(
    kind: str,
    levels: tuple[float, ...],
    *,
    window_seconds: float = 0.5,
    legacy: bool | None = None,
) -> str | None:
    """What is wrong with a rendered cue's shape, or ``None``: an event must hit in its first second,
    a sustained sound must not collapse after the first half second.

    On a desk created on/after 6 Oct 2026 (``legacy`` false; default: the running
    command's rules, :func:`creation.rules_epoch.legacy_rules`) a sustained sound must
    also HOLD: after its first second, no one-second stretch more than
    :data:`SUSTAINED_HOLD_DB` under the cue's median, so a cue that fades away is
    refused (``sustained sound fades``) and re-rendered like any wrong shape. The
    first second is left out (a bed may swell in). Legacy desks keep the half-tail
    check alone.

    Parameters
    ----------
    kind
        ``event`` or ``sustained``.
    levels
        RMS level (dB) per window, :func:`creation.post.media.measure_rms_windows`.
    window_seconds
        The windows' length.
    legacy
        Force the legacy (``True``) or new (``False``) rule.
    """

    if not levels:
        return "no audio"
    peak = max(levels)
    if peak <= SILENCE_DB:
        return "silent"
    if kind == "event":
        return None if max(levels[:2]) >= peak - 12.0 else "event starts late"
    if kind == HIT_THEN_TAIL:
        # A hit then a quieter bed ("a car door slams, then the engine idles"): the hit must land
        # early and the tail must be heard, but it may sit well under the hit (L-20261007-1).
        if max(levels[:2]) < peak - 12.0:
            return "the hit starts late"
        tail = levels[2:]
        if tail and sum(1 for level in tail if level > SILENCE_DB) / len(tail) < 0.5:
            return "the tail after the hit is silent"
        return None
    tail = levels[1:]
    if not tail:
        return None
    held = sum(1 for level in tail if level >= peak - 18.0 and level > SILENCE_DB)
    if held / len(tail) < 0.5:
        return "sustained sound collapses"
    if legacy_rules() if legacy is None else legacy:
        return None
    seconds = _second_levels(levels, window_seconds)
    if len(seconds) < 2:
        return None
    median = statistics.median(seconds)
    low = min(seconds[1:])
    if low < median - SUSTAINED_HOLD_DB:
        return (
            f"sustained sound fades (a second at {low:.0f} dB, {median - low:.0f} dB under its median "
            f"{median:.0f} dB; a constant sound holds within {SUSTAINED_HOLD_DB:g} dB)"
        )
    return None


#: The shape of a sustained cue whose words describe a hit followed by a bed (L-20261007-1).
HIT_THEN_TAIL = "hit_then_tail"
_HIT_WORDS = re.compile(
    r"\b(slam|slams|slammed|bang|bangs|crash|crashes|thud|thuds|clang|clangs|knock|knocks|"
    r"smash|smashes|shut|shuts|snap|snaps|hit|hits|crack|cracks|pop|pops|click|clicks|clinks?)\b",
    re.IGNORECASE,
)
_THEN = re.compile(
    r"\b(then|followed by|before|into|and then|giving way to)\b|,\s*then\b",
    re.IGNORECASE,
)


def checked_shape(cue: "SfxCue") -> str:
    """The shape a cue is checked against: its kind, or :data:`HIT_THEN_TAIL` for a wanted hit then a bed.

    A sustained cue whose words put a hit before a following sound ("a car door slams, then a
    low engine idle") was flagged "sustained sound collapses/fades" though that is the shape
    asked for. Only the check changes: the cue, its words and its placement do not.
    """

    if cue.kind != "sustained":
        return cue.kind
    words = cue.sound
    then = _THEN.search(words)
    if then and _HIT_WORDS.search(words[: then.start()]):
        return HIT_THEN_TAIL
    return cue.kind


def service_renderer(audio: AudioService, spine_id: str) -> Renderer:
    """Render a cue on the server (``POST /v1/spines/{id}/sfx-cues``) from its authored Sound label.

    The server checks the shape too; a cue it flags is refused here (skipped, not mixed).
    """

    def render(cue: SfxCue, target: Path) -> Path:
        seconds = round(max(SFX_MIN_SECONDS, min(SFX_MAX_SECONDS, cue.seconds)), 2)
        key = f"sfx-{cue.cache_key}"
        if cue.remake:
            # The server caches a cue by its content (sound, seconds), so asking again for the same
            # content returns the same silent file. A re-make asks for the SAME sound label a hair
            # longer; it is trimmed back to the cue's length when laid (placement unchanged).
            seconds = round(
                min(
                    SFX_MAX_SECONDS + REMAKE_NUDGE_SECONDS,
                    seconds + REMAKE_NUDGE_SECONDS * cue.remake,
                ),
                2,
            )
            key += f"-remake{cue.remake}"
        answer = audio.sfx_cue(
            spine_id=spine_id,
            sound=cue.sound,
            seconds=seconds,
            key=key,
        )
        if answer.get("shape_problem"):
            raise ValueError(f"wrong shape: {answer['shape_problem']}")
        return download(str(answer["audio_url"]), target)

    return render


class NothingLaid(RuntimeError):
    """No planned effect could be laid (each failed to render or had the wrong shape).

    ``skipped`` and ``dropped`` say why for each cue; ``rendered`` and
    ``cost_usd`` are what the attempts cost, so the caller can still book them.
    """

    def __init__(
        self, message: str, *, skipped: tuple[str, ...], dropped: tuple[str, ...],
        rendered: int, cost_usd: float,
    ) -> None:  # fmt: skip
        super().__init__(message)
        self.skipped = skipped
        self.dropped = dropped
        self.rendered = rendered
        self.cost_usd = cost_usd


@dataclass(frozen=True)
class SfxResult:
    """What the SFX step wrote."""

    output: Path
    mixed: tuple[SfxCue, ...]
    #: Planned cues that could not be laid, each ``sound (reason)``: render failed, wrong shape, past the take.
    skipped: tuple[str, ...]
    rendered: int
    cost_usd: float
    #: Each mixed cue's loudest 0.5 s window as placed (rendered level + its gain), same order as ``mixed``.
    peaks_db: tuple[float, ...] = ()
    #: Planned cues left out on purpose by ``--sfx-adjust ... drop``, each ``sound (reason)``.
    dropped: tuple[str, ...] = ()
    #: Cues raised against the music bed (:func:`bed_levelled_gain`), each ``sound +N dB (why)``.
    levelled: tuple[str, ...] = ()


@dataclass(frozen=True)
class BedReference:
    """The music bed a take's effects will play under, as the mix will lay it.

    ``levels`` is the bed file's RMS per ``window_seconds`` (it loops under the
    picture), ``bed_db`` its gain in the mix, ``take_gain_db`` the gain the mix
    will give the take (and so the effects on it), ``voice_peak_db`` the take's
    loudest spoken window before any effect (an effect is never set over it).
    """

    levels: tuple[float, ...]
    bed_db: float
    take_gain_db: float
    voice_peak_db: float | None = None
    window_seconds: float = 0.5


#: Where a levelled effect's loudest window sits under the bed's over the same stretch: the mix's
#: ``AUDIBLE_CUE_DB`` and the server's ``sfx_bed_level.SFX_UNDER_BED_LU`` (fictora-drama #646).
SFX_UNDER_BED_DB = 6.0
#: The most a quiet render is raised (the server's ``SFX_BED_GAIN_CEILING_DB``).
SFX_BED_RAISE_CEILING_DB = 18.0
#: The highest gain levelling sets. Renders are not loudness-normalised, so a quiet one needs gain
#: over 0 dB (the planned range stops at 0 dB); the finish's one limiter still holds peaks at -1 dBFS
#: and the voice cap keeps every effect under the take's loudest spoken window.
SFX_LEVELLED_MAX_GAIN_DB = 10.0


def bed_levelled_gain(
    cue: "SfxCue", rendered_peak_db: float, bed: BedReference
) -> tuple[float, str]:
    """The gain a kit-laid effect is laid at so it is heard over the music bed, and why.

    The same rule as the server's dialogue-track effects (fictora-drama #646,
    ``sfx_bed_level``), for the effects the kit lays itself (model-voice takes,
    and any cue the server did not lay): an effect whose loudest window would
    sit more than :data:`SFX_UNDER_BED_DB` under the bed where it plays is
    raised to that, never past :data:`SFX_LEVELLED_MAX_GAIN_DB`, never more than
    :data:`SFX_BED_RAISE_CEILING_DB`, and never over the take's loudest spoken
    window. A cue a sound note or ``--sfx-adjust`` lowered stays that much
    further under. An effect already heard is never lowered. Only the level
    changes: the cue, its sound and its placement are the plan's.

    Before this, operators raised buried effects by hand (Hana L-20261001-5,
    ten times; Noodle24's clink 17 dB under the music).

    Returns
    -------
    tuple[float, str]
        The gain, and ``""`` when unchanged or a short reason when raised.
    """

    if (
        not bed.levels
        or not math.isfinite(rendered_peak_db)
        or rendered_peak_db <= SILENCE_DB
    ):
        return cue.gain_db, ""
    count = len(bed.levels)
    first = int(cue.start / bed.window_seconds)
    last = max(
        first,
        math.ceil(
            (cue.start + max(cue.seconds, bed.window_seconds)) / bed.window_seconds
        )
        - 1,
    )
    bed_db = (
        max(bed.levels[index % count] for index in range(first, last + 1)) + bed.bed_db
    )
    heard = rendered_peak_db + cue.gain_db + bed.take_gain_db
    lowered = min(
        0.0, cue.gain_db - SFX_GAIN_DB
    )  # a note's or --sfx-adjust's cut stays on top
    target_gap = SFX_UNDER_BED_DB - lowered
    gap = bed_db - heard
    if gap <= target_gap:
        return cue.gain_db, ""
    gain = cue.gain_db + min(gap - target_gap, SFX_BED_RAISE_CEILING_DB)
    capped = ""
    if gain > SFX_LEVELLED_MAX_GAIN_DB:
        gain, capped = (
            SFX_LEVELLED_MAX_GAIN_DB,
            f"; held at {SFX_LEVELLED_MAX_GAIN_DB:+.0f} dB gain",
        )
    if bed.voice_peak_db is not None and math.isfinite(bed.voice_peak_db):
        voice_cap = bed.voice_peak_db - rendered_peak_db
        if gain > voice_cap:
            gain, capped = max(cue.gain_db, voice_cap), "; held under the voice"
    gain = round(gain, 1)
    if gain <= cue.gain_db:
        return cue.gain_db, ""
    return gain, (
        f"{gain - cue.gain_db:+.0f} dB: it sat {gap:.0f} dB under the music bed, now about "
        f"{max(target_gap, gap - (gain - cue.gain_db)):.0f} dB under{capped}"
    )


def rendered_peak_db(
    levels: tuple[float, ...], seconds: float, window_seconds: float = 0.5
) -> float:
    """The loudest window of a render over the part that is laid (its first ``seconds``)."""

    used = levels[: max(1, math.ceil(seconds / window_seconds))]
    return max(used, default=-120.0)


def _cue_chain(
    position: int, cue: SfxCue, speech: tuple[tuple[float, float], ...]
) -> str:
    length = cue.seconds
    delay = round(cue.start * 1000)
    shape = f"atrim=0:{length:.3f},asetpts=PTS-STARTPTS"
    if cue.kind == "sustained":
        shape += f",afade=t=in:st=0:d={min(0.3, length / 4):.3f}"
        fade = min(0.6, length / 3)
    else:
        fade = min(0.15, length / 4)
    shape += f",afade=t=out:st={length - fade:.3f}:d={fade:.3f}"
    end = cue.start + length
    ducks = "".join(
        f",volume=enable='between(t,{max(a, cue.start):.3f},{min(b, end):.3f})':volume={SFX_SPEECH_DUCK_DB:+.1f}dB"
        for a, b in speech
        if a < end and b > cue.start
    )
    return f"[{position}:a]aresample=48000,{shape},adelay={delay}|{delay},volume={cue.gain_db:+.1f}dB{ducks}[s{position}]"


Levels = Callable[[Path], AudioLevels]
"""``file -> levels`` (:func:`creation.post.media.measure_levels` in production)."""


def opening_hook_gain_db(
    planned_gain_db: float, hook: AudioLevels, take: AudioLevels | None
) -> float:
    """The gain an opening hook is laid at: never awkwardly loud against the take.

    Lowered (never raised past ``planned_gain_db``) until the hook's loudest 400 ms sits at or under
    the take's loudness and its true peak at or under the take's. The server levels its own hooks
    the same way (fictora-drama ``opening_sound.opening_hook_level``).

    Parameters
    ----------
    planned_gain_db
        The cue's planned gain (:func:`noted_gain_db`).
    hook
        The rendered hook's levels.
    take
        The take's own levels; ``None`` (or unmeasurable) holds the hook under a spoken line's.

    Returns
    -------
    float
        The gain, never below the effect floor.
    """

    reference_lufs = (
        take.integrated_lufs
        if take and take.integrated_lufs is not None
        else OPENING_HOOK_REFERENCE_LUFS
    )
    reference_peak = (
        take.true_peak_dbtp
        if take and take.true_peak_dbtp is not None
        else OPENING_HOOK_REFERENCE_PEAK_DBTP
    )
    gain = planned_gain_db
    if hook.max_momentary_lufs is not None:
        gain = min(
            gain, reference_lufs + OPENING_HOOK_OVER_TAKE_LU - hook.max_momentary_lufs
        )
    if hook.true_peak_dbtp is not None:
        gain = min(gain, reference_peak - hook.true_peak_dbtp)
    return round(_clamp_gain(gain), 1)


def level_opening_hooks(
    kept: list[tuple[SfxCue, Path]], take: Path, *, levels: Levels = measure_levels
) -> list[tuple[SfxCue, Path]]:
    """Level each opening hook against the take it opens; every other cue is returned as is.

    Parameters
    ----------
    kept
        The cues to mix with their rendered files.
    take
        The take as delivered, before any effect of ours.
    levels
        Level meter.

    Returns
    -------
    list[tuple[SfxCue, Path]]
        The same cues, each hook at its levelled gain.
    """

    if not any(is_opening_cue(cue.sound) for cue, _path in kept):
        return kept
    try:
        reference: AudioLevels | None = levels(take)
    except MediaToolError as exc:
        _log.warning(
            "opening hook: the take could not be measured (%s); held under a spoken line's level",
            exc,
        )
        reference = None
    out: list[tuple[SfxCue, Path]] = []
    for cue, path in kept:
        if is_opening_cue(cue.sound):
            try:
                gain = opening_hook_gain_db(cue.gain_db, levels(path), reference)
            except MediaToolError as exc:
                _log.warning(
                    "opening hook %r could not be measured (%s); laid at %+.1f dB",
                    cue.sound,
                    exc,
                    cue.gain_db,
                )
                gain = cue.gain_db
            cue = replace(cue, gain_db=gain)
        out.append((cue, path))
    return out


def lay_sfx(
    take: Path,
    plan: SfxPlan,
    *,
    cache_dir: Path,
    output: Path,
    adjustments: tuple[Adjustment, ...] = (),
    render: Renderer,
    measure: Meter = measure_rms_windows,
    levels: Levels = measure_levels,
    bed: BedReference | None = None,
) -> SfxResult:
    """Render (or reuse) each cue and mix the usable ones under the take into ``output``.

    Parameters
    ----------
    take
        The take (its picture is copied).
    plan
        Cues and speaking windows (:func:`plan_from_take_facts`).
    cache_dir
        ``epNN/sfx``: rendered cues by sound.
    output
        New file; must not exist.
    adjustments
        Per-take level changes.
    render
        Cue renderer (:func:`service_renderer` in production).
    measure
        Shape meter (also gives each mixed cue's placed peak, for the mix's quiet-cue check).
    levels
        Level meter for the opening hook and the take it is levelled under (:func:`level_opening_hooks`).
    bed
        The music bed the mix will lay under the take: each buried effect is raised against it
        (:func:`bed_levelled_gain`). ``None`` (no bed, or the music is in the take): planned gains.

    Returns
    -------
    SfxResult
        The new file, the cues on it and the render cost.

    Raises
    ------
    FileExistsError
        When ``output`` exists.
    """

    if output.exists():
        raise FileExistsError(f"{output} exists; local post never overwrites")
    info = probe_video(take)
    cache_dir.mkdir(parents=True, exist_ok=True)
    kept: list[tuple[SfxCue, Path]] = []
    skipped: list[str] = []
    # A cue an adjustment drops is still a planned cue the take will not have: say so.
    dropped = [
        f"{cue.sound} (dropped by --sfx-adjust)"
        for cue in plan.cues
        if not apply_adjustments((cue,), adjustments)
    ]
    rendered = 0
    cost = 0.0
    for cue in apply_adjustments(plan.cues, adjustments):
        room = info.duration_seconds - cue.start
        if room < SFX_MIN_PLACED_SECONDS:
            skipped.append(f"{cue.sound} (starts past the take)")
            continue
        cue = replace(cue, seconds=round(min(cue.seconds, room), 3))
        cached = cache_dir / f"{cue.cache_key}.mp3"
        if cached.is_file():
            if shape_problem(checked_shape(cue), measure(cached)) is None:
                kept.append((cue, cached))
                continue
            # A cached render that is silent (or the wrong shape) is never laid again: re-made below.
            cached.unlink()
        good: Path | None = None
        problem = ""
        # The first render, then ONE re-make of the same cue (same sound label, same placement):
        # a cue that comes back silent or the wrong shape is re-made once, else left out with a
        # warning. It never stops the finish (Gallery L-20261008-20).
        for remake in (0, 1):
            asked = replace(cue, remake=remake)
            try:
                path = render(asked, cached)
            except (RuntimeError, OSError, KeyError, ValueError) as exc:
                text = str(exc)
                if not text.startswith("wrong shape"):
                    skipped.append(f"{cue.sound} (render failed: {text[:120]})")
                    problem = ""
                    break
                rendered += 1
                cost += max(SFX_MIN_SECONDS, cue.seconds) * SFX_USD_PER_SECOND
                problem = text.removeprefix("wrong shape: ").removeprefix("wrong shape")
                continue
            rendered += 1
            cost += max(SFX_MIN_SECONDS, cue.seconds) * SFX_USD_PER_SECOND
            problem = shape_problem(checked_shape(cue), measure(path)) or ""
            if not problem:
                good = path
                break
            path.unlink(missing_ok=True)
        if good is None and problem:
            skipped.append(
                f"{cue.sound} ({problem} twice: re-made once, then left out)"
            )
        if good is not None:
            kept.append((cue, good))
    if not kept:
        raise NothingLaid(
            "no sound effect could be laid: "
            + (
                "; ".join([*skipped, *dropped])
                if skipped or dropped
                else "the take facts plan no cue"
            ),
            skipped=tuple(skipped), dropped=tuple(dropped), rendered=rendered,
            cost_usd=round(cost, 4),
        )  # fmt: skip
    # Every other effect is levelled against the music bed it plays under (all shows: only the
    # level changes, never which cue or which sound).
    levelled: list[str] = []
    if bed is not None:
        placed: list[tuple[SfxCue, Path]] = []
        for cue, path in kept:
            if not is_opening_cue(cue.sound):
                gain, why = bed_levelled_gain(
                    cue, rendered_peak_db(measure(path), cue.seconds), bed
                )
                if why:
                    levelled.append(f"{cue.sound} {why}")
                    cue = replace(cue, gain_db=gain)
            placed.append((cue, path))
        kept = placed
    # The episode's opening hook: heard clearly, never over the take.
    kept = level_opening_hooks(kept, take, levels=levels)
    inputs: list[str] = ["-i", str(take)]
    parts: list[str] = []
    labels = ["[0:a]"]
    for position, (cue, path) in enumerate(kept, start=1):
        inputs += ["-i", str(path)]
        parts.append(_cue_chain(position, cue, plan.speech))
        labels.append(f"[s{position}]")
    parts.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,{LIMITER}[a]"
    )
    run_ffmpeg(
        [*inputs, "-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(output)]
    )  # fmt: skip
    peaks = tuple(
        round(max(measure(path), default=-120.0) + cue.gain_db, 1) for cue, path in kept
    )
    return SfxResult(
        output,
        tuple(cue for cue, _ in kept),
        tuple(skipped),
        rendered,
        round(cost, 4),
        peaks,
        tuple(dropped),
        tuple(levelled),
    )
