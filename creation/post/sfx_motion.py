"""Put an action's sound on the frame the action happens (L-20260930-10).

The take facts place each effect by rule: at the shot's cut, half-way in, or
at the row step its words name, and the finish then moves it with its shot
onto the filmed cuts (:func:`creation.post.sfx.follow_filmed_cuts`). Inside a
shot the model films the action when it films it: a door slam planned half-way
into a 3 s shot lands at 1.9 s on one take and 2.6 s on the next, and the
slam is heard before (or after) the door moves (Seedlings ep 6, 5 Oct 2026).

This module reads the picture. A cue tied to an on-screen action (a one-off
event whose words name a hit: a slam, a crack, a punch, a knock...) moves to its
action's motion spike on the frame-difference trace (``tblend=difference``, the
same trace the cut detector reads, on a larger frame), away from any cut:

1. **The shot's one clear movement.** When one spike dominates the cue's whole
   filmed shot (:data:`MOTION_SHOT_PEAK_OVER_OTHERS` times every other motion
   in it), the cue goes there, however far from where it was placed. A
   "golden crack" placed on 0.00 s whose cracks appear at 3.7 s, a "lid grinds"
   at 9.2 s whose lid moves at 5.25-7.0 s (L-20261030-10, seen four times)
   stayed put while the snap only looked 1 s either way.
2. **Otherwise the clearest spike near it**, within
   :data:`MOTION_SNAP_WINDOW_SECONDS` of its placed start.

A hit lands on the spike's frame; a sound that goes with the movement (a
grind, a scrape, a spreading crack) starts on the movement's first frame. A
cue whose window has no clear spike (a still frame, steady motion, two spikes
alike) keeps the time it had. **One hit per movement**: two cues never land on
one spike (the one placed nearest it goes there), and a cue whose words name
several hits ("knocks three times", "footsteps") is never laid on one
movement. The episode's **opening hook** follows the picture too when its
words name a physical action; a sting, a whoosh, ambience, a deliberate
cut-in or a genre fallback (picked without looking at the picture) stays on
the cut. Beds and sounds that name no action never move.

No model call and nothing billed: one ffmpeg pass over the take. Desks created
before 6 Oct 2026 never run it (``finish`` asks
:func:`creation.rules_epoch.legacy_rules`): this changes when effects are heard.

The server's mix (fictora-drama ``sfx_motion``) does the same with the same
numbers and the same word lists (kept in step by hand: this repo never imports
the server's code).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from statistics import median

from creation.harness_rules import is_opening_cue
from creation.post.edit import parse_frame_motion
from creation.post.media import ffmpeg_bin

#: How far a cue may move to the action's spike, either way, in seconds.
MOTION_SNAP_WINDOW_SECONDS = 1.0
#: Frames this close to a cut (the shot's own edges or any measured cut) are never the action.
MOTION_CUT_GUARD_SECONDS = 0.15
#: The spike must be this many times the shot's typical (median) motion...
MOTION_PEAK_OVER_MEDIAN = 3.0
#: ...and at least this far above it (mean luma difference, 0-255): breathing and blinks sit under 1.
MOTION_PEAK_MIN_RISE = 2.0
#: ...and this many times the strongest other motion in the window (more than this far from it).
MOTION_PEAK_OVER_OTHERS = 1.5
#: What "other motion" means: samples further than this from the spike.
MOTION_PEAK_SPREAD_SECONDS = 0.25
#: A cue moves anywhere in its filmed shot only to a spike this many times every other motion in the shot.
MOTION_SHOT_PEAK_OVER_OTHERS = 2.0
#: The frame the trace is read on: big enough to see a hand or a door, small enough to be quick.
MOTION_SCALE = "64:112"

#: Words that name an action you can see land (the server's impact words, ``impact_sfx._IMPACT_STEMS``,
#: and a few motions that are not hits).
_ACTION_STEMS = (
    "crack", "crunch", "snap", "slam", "bang", "boom", "thud", "thump", "crash", "smash",
    "shatter", "clang", "clank", "clatter", "grind", "knock", "punch", "slap", "smack",
    "whack", "thwack", "stomp", "kick", "splinter", "gunshot", "explosion", "explode",
    "hit", "strike", "stab", "whoosh", "swoosh", "swish",
)  # fmt: skip
_ACTION = re.compile(
    r"\b("
    + "|".join(_ACTION_STEMS)
    + r")(s|es|ed|d|ing|ped|ping|med|ming|ded|ding|ting)?\b",
    re.IGNORECASE,
)

#: Words that make a cue several hits, not one movement: never laid on a single spike.
_SEVERAL_HITS = re.compile(
    r"\b(?:twice|thrice|(?:two|three|four|five|several|many) (?:times|hits|knocks|shots|blows|bangs)|"
    r"again and again|over and over|repeated\w*|rapid\w*|a (?:series|flurry|volley|barrage|hail) of|"
    r"flurry|volley|barrage|footsteps|several|multiple|knocking|banging|hammering|pounding|clattering)\b",
    re.IGNORECASE,
)
#: Sounds that go with a movement rather than land at its end: they start where the movement starts.
_WITH_THE_MOVEMENT = re.compile(
    r"\b(?:grind\w*|scrap(?:e|es|ed|ing)|drag\w*|slid(?:e|es|ing)|creak\w*|roll(?:s|ed|ing)?|spread\w*|"
    r"tear(?:s|ing)?|rip(?:s|ping)?|crumbl\w*|rumbl\w*)\b",
    re.IGNORECASE,
)
#: Opening hooks that are a sound design gesture, not an action you can see.
_NOT_ON_SCREEN = re.compile(
    r"\b(?:sting|stings|stinger|music|musical|synth|cinematic|riser|braam|chord|note|whoosh\w*|swoosh\w*|"
    r"swish\w*|drone|ambience|ambient)\b",
    re.IGNORECASE,
)
#: Ends a deliberate cut-in hook (fictora-drama ``opening_sound.CUT_IN_MARKER``): it keeps its half second.
CUT_IN_MARKER = "cutting in hard by half a second"
#: The opening hooks the server picks by genre alone (fictora-drama ``opening_sound.GENRE_OPENING_FALLBACKS``
#: and ``DEFAULT_OPENING_FALLBACKS``): never tied to the opening picture, so they never follow it.
GENRE_OPENING_HOOKS = frozenset(
    {
        "a single wrong piano note, struck hard", "a door latch clicks behind the camera",
        "a phone buzzes face down on wood", "a camera shutter, one sharp click", "a file drawer slams shut",
        "a pen cap snaps off", "a squeaky chair gives out", "a microwave beeps three times",
        "a can cracks open and fizzes", "a kettle clicks off", "a bicycle bell, two rings",
        "a shop bell jangles over a door", "a paper cup set down hard on a counter", "an umbrella snaps open",
        "a car door shuts outside", "an elevator chime", "a phone buzzes twice against wood",
        "a glass set down hard on marble", "a pen clicks once", "a hard dry impact, close",
        "a glove slaps a heavy bag", "a chain rattles against a fence", "a fan snaps shut",
        "a teacup set down hard on lacquer", "a gong, struck once", "a door shuts hard",
        "a glass set down hard on a table", "a short low music sting, one hit",
    }
)  # fmt: skip
_OPENING_MARKERS = re.compile(
    r",?\s*(?:cutting in hard by half a second|on the first frame)", re.IGNORECASE
)

Trace = tuple[tuple[float, float], ...]
"""``(time, mean luma difference)`` per frame."""

MotionMeter = Callable[[Path], Trace]
"""``take -> motion trace`` (:func:`measure_motion` in production)."""


def names_an_action(sound: str, kind: str, source: str = "") -> bool:
    """Whether a cue is tied to an action on screen (and may follow the picture).

    Parameters
    ----------
    sound
        The cue's Sound label.
    kind
        ``event`` or ``sustained``; a sustained sound never moves.
    source
        The take facts' ``source``: an ``impact`` the story's action states always counts.

    Returns
    -------
    bool
        True for a one-off event that names a hit (or comes from a stated impact); an opening hook only
        when :func:`opening_hook_names_an_action`.
    """

    if kind != "event":
        return False
    if is_opening_cue(sound):
        return opening_hook_names_an_action(sound)
    return source == "impact" or bool(_ACTION.search(sound))


def opening_hook_names_an_action(sound: str) -> bool:
    """Whether an episode's opening hook names a physical action on screen (and may follow it).

    Parameters
    ----------
    sound
        The hook's Sound label, marker included.

    Returns
    -------
    bool
        True when its words name an action ("a golden crack spreads"); False for a sting, a whoosh,
        ambience, a deliberate cut-in, or a genre fallback (:data:`GENRE_OPENING_HOOKS`).
    """

    if CUT_IN_MARKER in sound.casefold():
        return False
    words = " ".join(_OPENING_MARKERS.sub("", sound).split()).strip(" ,;")
    if words.casefold().rstrip(".") in GENRE_OPENING_HOOKS or _NOT_ON_SCREEN.search(
        words
    ):
        return False
    return bool(_ACTION.search(words))


def names_several_hits(sound: str) -> bool:
    """Whether a cue's words name several hits ("knocks three times", "footsteps"), not one movement.

    Parameters
    ----------
    sound
        The cue's Sound label.

    Returns
    -------
    bool
        True when the clip carries more than one hit: it is never laid on a single motion spike.
    """

    return bool(_SEVERAL_HITS.search(sound))


def measure_motion(take: Path) -> Trace:
    """The take's frame-difference trace on a :data:`MOTION_SCALE` grey frame.

    Raises
    ------
    RuntimeError
        When the analysis fails.
    """

    run = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-i", str(take), "-vf",
         f"scale={MOTION_SCALE},format=gray,tblend=all_mode=difference,signalstats,metadata=print:file=-",
         "-an", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if run.returncode != 0:
        raise RuntimeError(f"ffmpeg motion trace failed: {(run.stderr or '')[-400:]}")
    return parse_frame_motion(run.stdout)


@dataclass(frozen=True)
class Peak:
    """Where the action lands, or why no time was found.

    ``time`` is the spike's frame (where a hit lands); ``onset`` the first frame of its movement
    (where a sound that goes with the movement, a grind or a spreading crack, starts).
    """

    time: float | None
    reason: str
    onset: float | None = None


def _clear_spike(
    samples: list[tuple[float, float]], typical: float, over_others: float
) -> Peak:
    """The loudest sample when it is a clear spike over ``typical`` and ``over_others`` times the rest.

    The spike's movement is the run of frames around it that stay over the spike floor (gaps up to
    :data:`MOTION_PEAK_SPREAD_SECONDS` bridged): a lid that moves for 1.75 s is one movement, not
    two spikes alike. "The rest" is every frame further than the spread from that run.
    """

    time, value = max(samples, key=lambda sample: sample[1])
    floor = max(typical * MOTION_PEAK_OVER_MEDIAN, typical + MOTION_PEAK_MIN_RISE)
    if value < floor:
        return Peak(None, "no clear motion spike")
    ordered = sorted(samples)
    at = ordered.index((time, value))
    onset = end = time
    for step in (1, -1):
        last = time
        index = at + step
        while 0 <= index < len(ordered):
            t, v = ordered[index]
            if abs(t - last) > MOTION_PEAK_SPREAD_SECONDS:
                break
            if v >= floor:
                last = t
                onset, end = min(onset, t), max(end, t)
            index += step
    spread = MOTION_PEAK_SPREAD_SECONDS
    others = [v for t, v in samples if t < onset - spread or t > end + spread]
    if others and value < max(others) * over_others:
        return Peak(None, "two motion spikes alike")
    return Peak(round(time, 3), "motion spike", onset=round(onset, 3))


def action_moment(
    trace: Trace,
    *,
    around: float,
    shot: tuple[float, float],
    cuts: tuple[float, ...] = (),
    window: float = MOTION_SNAP_WINDOW_SECONDS,
) -> Peak:
    """The shot's one clear movement, else the clearest motion spike within ``window`` of ``around``.

    Parameters
    ----------
    trace
        :func:`measure_motion`.
    around
        Where the cue sits now.
    shot
        The cue's shot as filmed ``(start, end)``.
    cuts
        Every cut measured on the take (a spike at a cut is the cut, not the action).
    window
        How far the cue may move when no one movement dominates the shot.

    Returns
    -------
    Peak
        ``time`` is the spike's frame; ``None`` with the reason when there is no clear one.
    """

    guard = MOTION_CUT_GUARD_SECONDS
    edges = (shot[0], shot[1], *cuts)

    def usable(time: float) -> bool:
        return shot[0] + guard <= time <= shot[1] - guard and all(
            abs(time - edge) >= guard for edge in edges
        )

    inside = [(t, v) for t, v in trace if usable(t)]
    if len(inside) < 3:
        return Peak(None, "too little picture in the shot")
    typical = median(v for _, v in inside)
    dominant = _clear_spike(inside, typical, MOTION_SHOT_PEAK_OVER_OTHERS)
    if dominant.time is not None:
        return replace(dominant, reason="the shot's one clear movement")
    near = [(t, v) for t, v in inside if abs(t - around) <= window + 1e-9]
    if not near:
        return Peak(None, f"no picture within {window:g} s")
    return _clear_spike(near, typical, MOTION_PEAK_OVER_OTHERS)


@dataclass(frozen=True)
class MotionSnaps:
    """What moved onto the action and what kept its time."""

    moved: tuple[tuple[str, float, float], ...] = ()
    kept: tuple[tuple[str, float, str], ...] = ()
    error: str = ""

    def one_line(self) -> str:
        """``on the filmed action: a door slams 3.32->3.60s; kept: a punch lands 1.20s (no clear motion spike)``."""

        if self.error:
            return f"action cues kept their time (motion not measured: {self.error})"[
                :300
            ]
        parts: list[str] = []
        if self.moved:
            parts.append(
                "on the filmed action: "
                + ", ".join(f"{s} {a:.2f}->{b:.2f}s" for s, a, b in self.moved)
            )
        if self.kept:
            parts.append(
                "action cues kept their time: "
                + ", ".join(f"{s} {t:.2f}s ({why})" for s, t, why in self.kept)
            )
        return "; ".join(parts)


def snap_to_action(
    cues: tuple,
    windows: Mapping[int, tuple[float, float]],
    trace: Trace,
    *,
    cuts: tuple[float, ...] = (),
    window: float = MOTION_SNAP_WINDOW_SECONDS,
) -> tuple[tuple, MotionSnaps]:
    """Move each action cue to its action's spike (:func:`action_moment`), one cue per spike.

    A hit lands on the spike's frame; a sound that goes with the movement (a grind, a scrape, a
    spreading crack) starts on the movement's first frame. A cue naming several hits
    (:func:`names_several_hits`) keeps its time; of two cues headed for one spike, the one placed
    nearest it goes there and the other keeps its time. Every other cue is returned as it was.

    Parameters
    ----------
    cues
        :class:`creation.post.sfx.SfxCue` values on the filmed shots (``source`` ``impact`` always
        counts as an action).
    windows
        ``shot_index -> (start, end)`` as filmed.
    trace
        :func:`measure_motion`.
    cuts
        Cuts measured on the take.
    window
        How far a cue may move.

    Returns
    -------
    tuple[tuple, MotionSnaps]
        The cues, in the same order, and what moved.
    """

    out = list(cues)
    peaks: dict[int, Peak] = {}
    kept_at: dict[int, str] = {}
    for position, cue in enumerate(cues):
        shot = windows.get(cue.shot_index)
        if shot is None or not names_an_action(cue.sound, cue.kind, cue.source):
            continue
        if names_several_hits(cue.sound):
            kept_at[position] = "several hits, not one movement"
            continue
        peak = action_moment(
            trace, around=cue.start, shot=shot, cuts=cuts, window=window
        )
        if peak.time is None:
            kept_at[position] = peak.reason
        else:
            peaks[position] = peak
    # One hit per movement: of the cues headed for one spike, the one placed nearest it goes there.
    for position, peak in peaks.items():
        assert peak.time is not None
        distance = (abs(cues[position].start - peak.time), position)
        for other, theirs in peaks.items():
            if (
                other != position
                and theirs.time is not None
                and abs(theirs.time - peak.time) <= MOTION_PEAK_SPREAD_SECONDS
                and (abs(cues[other].start - peak.time), other) < distance
            ):
                kept_at[position] = "another cue is on this movement"
                break
    moved: list[tuple[str, float, float]] = []
    kept: list[tuple[str, float, str]] = []
    for position, cue in enumerate(cues):
        if position in kept_at:
            kept.append((cue.sound, cue.start, kept_at[position]))
            continue
        peak = peaks.get(position)
        if peak is None or peak.time is None:
            continue
        target = peak.time
        if peak.onset is not None and _WITH_THE_MOVEMENT.search(cue.sound):
            target = peak.onset
        if abs(target - cue.start) < 1e-3:
            kept.append((cue.sound, cue.start, "already on it"))
            continue
        moved.append((cue.sound, cue.start, target))
        out[position] = replace(cue, start=target)
    return tuple(out), MotionSnaps(tuple(moved), tuple(kept))


__all__ = [
    "MOTION_CUT_GUARD_SECONDS",
    "MOTION_PEAK_MIN_RISE",
    "MOTION_PEAK_OVER_MEDIAN",
    "MOTION_PEAK_OVER_OTHERS",
    "CUT_IN_MARKER",
    "GENRE_OPENING_HOOKS",
    "MOTION_SCALE",
    "MOTION_SHOT_PEAK_OVER_OTHERS",
    "MOTION_SNAP_WINDOW_SECONDS",
    "MotionMeter",
    "MotionSnaps",
    "Peak",
    "action_moment",
    "measure_motion",
    "names_an_action",
    "names_several_hits",
    "opening_hook_names_an_action",
    "snap_to_action",
]
