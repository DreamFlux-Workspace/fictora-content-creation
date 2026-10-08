"""Put an action's sound on the frame the action happens (L-20260930-10).

The take facts place each effect by rule: at the shot's cut, half-way in, or
at the row step its words name, and the finish then moves it with its shot
onto the filmed cuts (:func:`creation.post.sfx.follow_filmed_cuts`). Inside a
shot the model films the action when it films it: a door slam planned half-way
into a 3 s shot lands at 1.9 s on one take and 2.6 s on the next, and the
slam is heard before (or after) the door moves (Seedlings ep 6, 5 Oct 2026).

This module reads the picture. A cue tied to an on-screen action (a one-off
event whose words name a hit: a slam, a crack, a punch, a knock...) moves to
the clearest motion spike near where it was placed: the frame-difference trace
(``tblend=difference``, the same trace the cut detector reads, on a larger
frame) inside the cue's filmed shot, within :data:`MOTION_SNAP_WINDOW_SECONDS`
of its placed start, away from any cut. A cue whose window has no clear spike
(a still frame, steady motion, two spikes alike) keeps the time it had. Beds,
the episode's opening hook and sounds that name no action never move.

No model call and nothing billed: one ffmpeg pass over the take. Desks created
before 6 Oct 2026 never run it (``finish`` asks
:func:`creation.rules_epoch.legacy_rules`): this changes when effects are heard.

The server's mix (fictora-drama ``sfx_motion``) does the same with the same
numbers.
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
        True for a one-off event that names a hit (or comes from a stated impact), never the opening hook.
    """

    if kind != "event" or is_opening_cue(sound):
        return False
    return source == "impact" or bool(_ACTION.search(sound))


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
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if run.returncode != 0:
        raise RuntimeError(f"ffmpeg motion trace failed: {(run.stderr or '')[-400:]}")
    return parse_frame_motion(run.stdout)


@dataclass(frozen=True)
class Peak:
    """Where the action lands, or why no time was found."""

    time: float | None
    reason: str


def action_moment(
    trace: Trace,
    *,
    around: float,
    shot: tuple[float, float],
    cuts: tuple[float, ...] = (),
    window: float = MOTION_SNAP_WINDOW_SECONDS,
) -> Peak:
    """The clearest motion spike within ``window`` of ``around`` inside ``shot``.

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
        How far the cue may move.

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
    near = [(t, v) for t, v in inside if abs(t - around) <= window + 1e-9]
    if not near:
        return Peak(None, f"no picture within {window:g} s")
    time, value = max(near, key=lambda sample: sample[1])
    if (
        value < typical * MOTION_PEAK_OVER_MEDIAN
        or value - typical < MOTION_PEAK_MIN_RISE
    ):
        return Peak(None, "no clear motion spike")
    others = [v for t, v in near if abs(t - time) > MOTION_PEAK_SPREAD_SECONDS]
    if others and value < max(others) * MOTION_PEAK_OVER_OTHERS:
        return Peak(None, "two motion spikes alike")
    return Peak(round(time, 3), "motion spike")


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
    """Move each action cue to its action's spike (:func:`action_moment`); every other cue is returned as it was.

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

    out = []
    moved: list[tuple[str, float, float]] = []
    kept: list[tuple[str, float, str]] = []
    for cue in cues:
        shot = windows.get(cue.shot_index)
        if shot is None or not names_an_action(cue.sound, cue.kind, cue.source):
            out.append(cue)
            continue
        peak = action_moment(
            trace, around=cue.start, shot=shot, cuts=cuts, window=window
        )
        if peak.time is None or abs(peak.time - cue.start) < 1e-3:
            kept.append(
                (
                    cue.sound,
                    cue.start,
                    peak.reason if peak.time is None else "already on it",
                )
            )
            out.append(cue)
            continue
        moved.append((cue.sound, cue.start, peak.time))
        out.append(replace(cue, start=peak.time))
    return tuple(out), MotionSnaps(tuple(moved), tuple(kept))


__all__ = [
    "MOTION_CUT_GUARD_SECONDS",
    "MOTION_PEAK_MIN_RISE",
    "MOTION_PEAK_OVER_MEDIAN",
    "MOTION_PEAK_OVER_OTHERS",
    "MOTION_SCALE",
    "MOTION_SNAP_WINDOW_SECONDS",
    "MotionMeter",
    "MotionSnaps",
    "Peak",
    "action_moment",
    "measure_motion",
    "names_an_action",
    "snap_to_action",
]
