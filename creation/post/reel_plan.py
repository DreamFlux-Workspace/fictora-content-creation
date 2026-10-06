"""Plan a social reel cut from an episode's rendered footage (pure: no ffmpeg, no files).

The reel (founder decisions 1 Oct 2026, "H8 reel cut") is a separate edit for
IG / TikTok made from footage the episode already has: $0, no new video. It
may reorder: it opens on the episode's strongest moment as a short
flash-forward, then plays how we got there, and it ends on the episode's new
fact (its last beat), never on a calm cool-down after it.

This module decides WHAT to cut, deterministically, from numbers the kit
already has; :mod:`creation.post.reel` measures them and renders the cut.

1. **Beats on the take.** Each beat of the episode owns the board rows from its
   own frame's row to the row before the next beat's; each row is one shot of
   the take facts, as filmed (planned changes moved to the measured hard cuts).
2. **Roles.** The last beat is the ``new_fact``; the beat before it the one
   ``pivot`` the reel keeps; the first beat the ``plant``; any beat between is
   an ``escalation``. The new fact ends on its peak: a breath
   (:data:`PAYOFF_HOLD_SECONDS`) after its last caption or effect, or the last
   frame the action after it still moves (up to :data:`ACTION_REACH_SECONDS`
   on), never on a settled tail.
3. **Strongest frame** (the flash-forward, the cold open). Every 8 fps sample
   gets ``story * (0.5 + 0.5 * picture)``. Story is the beat's role (pivot
   0.7, escalation 0.5, plant 0.35), +0.15 on a payoff beat, at most 1.
   Picture weighs four signals by the series' genre family
   (:data:`PICTURE_WEIGHTS`, one small table; weights, never rules): motion
   (frame-to-frame change, the samples at a hard cut left out: a cut is not
   action), contrast (luma spread), face (a close-up reads 1, by the local face
   detector, else the take facts' head count, :mod:`creation.post.faces`) and
   stillness (a held frame: no motion either side for a beat, the held
   tension of a stare). Motion and contrast are scaled to the take's 95th
   percentile. The 1-2 s window between two cut points with the best mean
   (nearest 1.5 s) is the cold open; its best sample is the strongest frame.
   **The cold open never comes from the last beat** (the new fact): a window
   that touches it is never scored, so the reel cannot open on its own ending.
   It comes from the pivot or the peak before the reveal; its role, face score
   and weights go in the plan JSON (``cold_open``).
4. **Fit ``--seconds``.** Escalation beats go first (lowest score first), then
   the calm stretches between lines (no caption, lowest motion; the plant and
   escalations before the pivot, never the new fact, never the hook's first
   second), then heads of segments as a last resort (a ⚠ names any line lost).
5. **Cut points** sit off words: on a hard cut, in a gap between lines, or at a
   word's onset; a cut inside a word is a ⚠.

A line addressed to the viewer (a call to action, "comment below", "what would
you do?") never goes in: its span is cut out of every segment, with a ⚠. The
call to action belongs in the post text (:func:`post_text`). A POV episode
(the brief opens "POV: …", founder decision 6 Oct 2026; detected like
fictora-drama's ``pov_episode.scene_prompt_is_pov``) keeps a line said to the
camera ("what would you do?"): the camera is the viewer standing in the scene.
A call to action is still cut.

6. **Edges** (:mod:`creation.post.opening`). The reel's first second is read
   from the measured samples (dark frame 0, static, no face on frame 0 when a
   caption plays in the cold open) and its last segment for a settled tail
   after the last line or action; each is a ⚠ and the numbers go in the plan
   JSON (``opening``, ``tail``). The ending is hard on the last frame; a plan
   may ask for ``freeze-black`` (a short freeze on the peak, then black).

Nothing here stops: every problem is a ⚠ in the plan's ``warnings``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from creation.captions import Cue

#: The reel's default length and the range ``--seconds`` accepts.
DEFAULT_SECONDS = 15.0
MIN_SECONDS = 6.0
MAX_SECONDS = 30.0
#: The flash-forward's length (snapped edges keep it inside the range).
COLD_OPEN_SECONDS = 1.5
COLD_OPEN_RANGE = (1.0, 2.0)
#: The new fact holds this breath after its last caption or effect (a word's release), then the reel ends.
PAYOFF_HOLD_SECONDS = 0.15
#: Action still moving after the last line keeps the cut going, this far past the line at most.
ACTION_REACH_SECONDS = 1.0
#: Motion over this many times the take's median (and over the static floor) is action, not ambience.
ACTION_OVER_MEDIAN = 1.5
#: The episode's hook (its first second) is never trimmed.
HOOK_SECONDS = 1.0
#: A calm stretch keeps this much either side of the cut (a breath before and after a line).
CALM_MARGIN_SECONDS = 0.12
#: A calm stretch shorter than this is not cut into.
MIN_CALM_SECONDS = 0.5
#: A shot left this short beside a trim is a flash: the trim edge moves onto the hard cut.
FLASH_SECONDS = 0.3
#: Calm trims are made in steps this long.
TRIM_STEP_SECONDS = 0.1
#: A segment shorter than this is dropped rather than kept as a flash.
MIN_SEGMENT_SECONDS = 0.6
#: Over the length by no more than this, the plan keeps its lines (a ⚠ says by how much).
OVER_TOLERANCE_SECONDS = 0.5
#: How far a cut may move to land off a word.
SNAP_SECONDS = 0.5
#: Samples this close to a hard cut do not count as motion (the cut is not action).
CUT_MOTION_GUARD_SECONDS = 0.2
#: Samples this close to either end of a take are never the flash-forward (board frames, hand-off).
EDGE_GUARD_SECONDS = 0.3
#: A caption is kept on the reel when this much of it plays (or it plays for MIN_CUE_SECONDS).
CUE_VISIBLE_FRACTION = 0.6
MIN_CUE_SECONDS = 0.8

ROLES = ("cold_open", "plant", "escalation", "pivot", "new_fact")
#: The story weight of each role in the strongest-frame score (the new fact is never the cold open).
ROLE_WEIGHT = {"new_fact": 1.0, "pivot": 0.7, "escalation": 0.5, "plant": 0.35}
#: Roles the cold open may come from: never the new fact (the reel would open on its own ending).
COLD_OPEN_ROLES = ("pivot", "escalation", "plant")
#: Extra cost of trimming a calm stretch of this role (lower goes first).
TRIM_PENALTY = {"plant": 0.0, "escalation": 0.0, "pivot": 0.35}
#: What a piece keeps at least when its calm stretches are trimmed.
KEEP_SECONDS = {"pivot": 1.5, "plant": 1.0}
#: The picture part of the strongest-frame score (``story * (0.5 + 0.5 * picture)``), by genre family.
#: Weights, not rules: every signal counts in every genre; each row sums to 1. Tune here.
PICTURE_WEIGHTS: dict[str, dict[str, float]] = {
    #                motion  contrast  face   stillness
    "action":   {"motion": 0.55, "contrast": 0.15, "face": 0.20, "stillness": 0.10},
    "intimate": {"motion": 0.15, "contrast": 0.15, "face": 0.55, "stillness": 0.15},
    "horror":   {"motion": 0.15, "contrast": 0.15, "face": 0.35, "stillness": 0.35},
    "default":  {"motion": 0.35, "contrast": 0.20, "face": 0.30, "stillness": 0.15},
}  # fmt: skip
#: Which genre words land in which family (the spine's ``microdrama_genre``, split on ``_`` / spaces).
#: First match in this order wins; no match is ``default``.
GENRE_FAMILY_WORDS: dict[str, tuple[str, ...]] = {
    "horror": ("horror", "scp", "ghost", "haunted", "haunting", "occult", "zombie", "creepypasta"),
    "action": ("action", "war", "martial", "murim", "wuxia", "fight", "battle", "heist", "revenge"),
    "intimate": ("romance", "romantic", "bl", "gl", "slice", "cozy", "cosy", "love", "healing"),
}  # fmt: skip
#: A held frame: no motion this many samples either side (8 fps: about a third of a second each way).
STILL_REACH_SAMPLES = 3
#: Cut points are looked for on this grid (the measurement's rate).
MEASURE_RATE = 8.0
PAYOFF_BONUS = 0.15
#: The optional ending (a creator/operator flag, default ``hard``): freeze on the peak, then black.
ENDING_STYLES = ("hard", "freeze-black")
FREEZE_SECONDS = 0.4
BLACK_SECONDS = 0.3

PLAN_KIND = "fictora-reel-plan"
PLAN_SCHEMA = 1

#: A call to action, or a line said to an audience: never in the reel, on any show.
_CALL_TO_ACTION = re.compile(
    r"\b(comment(s)? (below|down)|drop a comment|in the comments|subscribe|follow (for|me|us|to)|"
    r"like and (share|follow|subscribe)|smash (that|the)|link in (the )?bio|part \d+\b|"
    r"let me know|tell me (in|below)|"
    r"stay tuned|don'?t forget to|watch (till|until|to) the end|you guys|y'?all|"
    r"everyone watching|dear viewers?)\b",
    re.IGNORECASE,
)
#: A question or remark said to "you", the one watching: cut, except on a POV episode (said to the camera).
_SAID_TO_YOU = re.compile(
    r"\b(tell me what|what would you do|would you (dare|rather|have)|"
    r"did you (see|catch|notice) (that|it)|can you (guess|spot|find)|guess what happens)\b",
    re.IGNORECASE,
)
#: fictora-drama ``pov_episode``: a markdown heading, and "POV:" / "POV -" / "POV —" opening the first prose line.
_HEADING = re.compile(r"^\s*#")
_POV_OPENING = re.compile(r"^[\s>*_\-\"'“‘(\[]*pov\s*[:：\-–—]", re.IGNORECASE)

#: Words that never go in the post text: other apps, model and provider names.
_POST_BANNED = re.compile(
    r"\b(minimax|hailuo|h3|kling|seedance|sora|veo|runway|pika|luma|fal|gpt|openai|chatgpt|"
    r"claude|anthropic|gemini|elevenlabs|eleven ?v3|whisper|midjourney|stable diffusion|"
    r"character\.?ai|zeta|melting|reelshort|dramabox|shortmax|talkie|replika|fictora drama)\b",
    re.IGNORECASE,
)
_PRICE = re.compile(r"[$€£¥₹]|\b\d+(\.\d+)?\s?(usd|dollars?|cents?)\b", re.IGNORECASE)


# --- inputs ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Shot:
    """One shot of a take as filmed: its index (board row) and window in take seconds."""

    index: int
    start: float
    end: float
    #: Named characters the take facts put in the shot (0 when unknown).
    named: int = 0


@dataclass(frozen=True)
class TakeInput:
    """What the planner knows about one finished take, all on the take's own timeline.

    Parameters
    ----------
    take_id
        ``t1``, ``t2`` ...
    duration
        Seconds of the pre-caption source.
    fps
        Its frame rate (cut points land on its frame grid).
    shots
        The take facts' shots as filmed, earliest first (may be empty).
    cuts
        Hard cuts measured on the source.
    cues
        The caption cues as the accepted cut showed them (word flicker or whole lines).
    sample_seconds, motion, contrast
        The 8 fps measurement: sample times, frame-to-frame change and luma spread.
    events
        Start times of the take's planned sound events (impacts), as filmed.
    luma
        Mean luma (0-1) of each sample (empty: not measured).
    faces
        Face close-up score (0-1) of each sample by the local detector (empty:
        no detector; the shots' head count stands in).
    """

    take_id: str
    duration: float
    fps: float = 24.0
    shots: tuple[Shot, ...] = ()
    cuts: tuple[float, ...] = ()
    cues: tuple[Cue, ...] = ()
    sample_seconds: tuple[float, ...] = ()
    motion: tuple[float, ...] = ()
    contrast: tuple[float, ...] = ()
    events: tuple[float, ...] = ()
    luma: tuple[float, ...] = ()
    faces: tuple[float, ...] = ()

    @property
    def face_source(self) -> str:
        """``detector`` (measured faces), ``head_count`` (the take facts' shots) or ``none``."""

        if self.faces:
            return "detector"
        return "head_count" if self.shots else "none"


@dataclass(frozen=True)
class BeatInput:
    """One beat of the episode as the spine says it: which take, which board rows."""

    ordinal: int
    take_id: str
    #: First and last board row the beat plays on (1-based; 0 when the spine gives none).
    first_row: int = 0
    last_row: int = 0
    label: str = ""
    payoff: bool = False
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class BeatSpan:
    """A beat placed on its take: role and window in take seconds."""

    ordinal: int
    take_id: str
    start: float
    end: float
    role: str
    label: str = ""
    payoff: bool = False
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Segment:
    """One piece of the reel: take seconds ``start``-``end`` playing as ``role``, and why."""

    take: str
    start: float
    end: float
    role: str
    why: str = ""

    @property
    def seconds(self) -> float:
        return self.end - self.start

    def as_json(self) -> dict[str, Any]:
        return {
            "take": self.take,
            "start_s": round(self.start, 3),
            "end_s": round(self.end, 3),
            "role": self.role,
            "why": self.why,
        }


@dataclass(frozen=True)
class Strongest:
    """The strongest frame and the window around it that opens the reel."""

    take: str
    at: float
    score: float
    parts: dict[str, float]
    start: float
    end: float
    role: str
    #: The genre family whose picture weights scored it, and where its face score came from.
    family: str = "default"
    face_source: str = "none"

    def as_json(self) -> dict[str, Any]:
        return {
            "take": self.take,
            "at_s": round(self.at, 3),
            "score": round(self.score, 3),
            "parts": {k: round(v, 3) for k, v in self.parts.items()},
            "window": [round(self.start, 3), round(self.end, 3)],
            "beat_role": self.role,
            "genre_family": self.family,
            "face_source": self.face_source,
        }


@dataclass
class ReelPlan:
    """The plan: ordered segments, the strongest frame, warnings (never a stop)."""

    episode: int
    seconds: float
    segments: list[Segment]
    strongest: Strongest | None = None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: The last beat (the new fact) on its take: ``(take, start, end)``; the cold open never touches it.
    last_beat: tuple[str, float, float] | None = None
    #: ``hard`` (the default) or ``freeze-black`` (:data:`ENDING_STYLES`).
    ending: str = "hard"
    #: The reel's first second and its tail as measured (:func:`check_plan` fills them).
    opening: dict[str, Any] | None = None
    tail: dict[str, Any] | None = None

    @property
    def total(self) -> float:
        return sum(segment.seconds for segment in self.segments)

    def ends_on_new_fact(self) -> bool:
        return bool(self.segments) and self.segments[-1].role == "new_fact"

    def lines(self) -> list[str]:
        """The plan in plain words, one line per segment."""

        rows = [
            f"Reel plan ep{self.episode:02d}: {len(self.segments)} segment(s), "
            f"{self.total:.2f} s of {self.seconds:g} s"
        ]
        if self.strongest is not None:
            s = self.strongest
            parts = ", ".join(f"{k} {v:.2f}" for k, v in s.parts.items())
            rows.append(
                f"Strongest frame: {s.take} {s.at:.2f} s (score {s.score:.2f}: {parts}; {s.role.replace('_', ' ')} "
                f"beat; {s.family} weights; face by {s.face_source.replace('_', ' ')})"
            )
        if self.ending != "hard":
            rows.append(
                f"Ending: {self.ending} (freeze {FREEZE_SECONDS:g} s on the last frame, then {BLACK_SECONDS:g} s black)"
            )
        clock = 0.0
        for index, seg in enumerate(self.segments, start=1):
            rows.append(
                f"  {index}. {clock:5.2f}-{clock + seg.seconds:5.2f} s  {seg.role:<10} {seg.take} "
                f"{seg.start:.2f}-{seg.end:.2f} s  {seg.why}"
            )
            clock += seg.seconds
        rows += [f"  note: {note}" for note in self.notes]
        rows += [f"⚠ {warning}" for warning in self.warnings]
        return rows


# --- small helpers -----------------------------------------------------------------------------


def frame_snap(seconds: float, fps: float) -> float:
    """``seconds`` on the take's frame grid."""

    return round(round(seconds * fps) / fps, 4)


def brief_is_pov(brief: str | None) -> bool:
    """True when a creator's brief opens as a POV episode (fictora-drama ``scene_prompt_is_pov``).

    The first non-blank line that is not a markdown heading starts with
    ``POV:`` (or ``POV -`` / ``POV —``), in any case. "POV of Mira" or a POV
    line further down is a camera note, not a POV episode.
    """

    for line in (brief or "").splitlines():
        if not line.strip() or _HEADING.match(line):
            continue
        return bool(_POV_OPENING.match(line))
    return False


def viewer_address(text: str, *, pov: bool = False) -> bool:
    """True when ``text`` is said to the viewer (a call to action or a question to the audience).

    Parameters
    ----------
    text
        A caption or script line.
    pov
        The episode is POV (:func:`brief_is_pov`): a line said to "you" is
        said to the camera, a character in the story, and stays. A call to
        action is still cut.

    Returns
    -------
    bool
        Whether the line must stay out of the reel.
    """

    if _CALL_TO_ACTION.search(text or ""):
        return True
    return not pov and bool(_SAID_TO_YOU.search(text or ""))


def _inside_cue(t: float, cues: Sequence[Cue], pad: float = 0.02) -> bool:
    return any(c.start + pad < t < c.end - pad for c in cues)


def speech_runs(
    cues: Sequence[Cue], *, join: float = 0.12
) -> list[tuple[float, float]]:
    """Caption cues merged into stretches of speech (cues closer than ``join`` are one run)."""

    runs: list[tuple[float, float]] = []
    for cue in sorted(cues, key=lambda c: c.start):
        if runs and cue.start - runs[-1][1] <= join:
            runs[-1] = (runs[-1][0], max(runs[-1][1], cue.end))
        else:
            runs.append((cue.start, cue.end))
    return runs


def snap_cut(
    t: float,
    take: TakeInput,
    *,
    lo: float = 0.0,
    hi: float | None = None,
    reach: float = SNAP_SECONDS,
) -> tuple[float, bool]:
    """Move a cut to the nearest point off a word.

    Candidates within ``reach``: a measured hard cut outside a word (best), a
    point between lines (the time itself, or a caption's end), a word's onset
    (a caption's start). The nearest wins after those preferences.

    Parameters
    ----------
    t
        The wanted cut, take seconds.
    take
        The take (its cues and cuts).
    lo, hi
        The cut must stay inside this window.
    reach
        How far it may move.

    Returns
    -------
    tuple[float, bool]
        The cut, and True when no point off a word was in reach (the cut is inside a word).
    """

    hi = take.duration if hi is None else hi
    low, high = max(lo, t - reach), min(hi, t + reach)
    found: list[tuple[float, float]] = []
    if not _inside_cue(t, take.cues):
        found.append((0.1, t))
    for cut in take.cuts:
        if low <= cut <= high and not _inside_cue(cut, take.cues):
            found.append((0.0 + abs(cut - t), cut))
    for cue in take.cues:
        if low <= cue.end <= high and not _inside_cue(cue.end, take.cues):
            found.append((0.1 + abs(cue.end - t), cue.end))
        if low <= cue.start <= high:
            found.append((0.3 + abs(cue.start - t), cue.start))
    if not found:
        return min(max(t, lo), hi), True
    return min(found)[1], False


def _sample_index(take: TakeInput, t: float) -> int:
    if not take.sample_seconds:
        return 0
    step = (
        take.sample_seconds[1] - take.sample_seconds[0]
        if len(take.sample_seconds) > 1
        else 0.125
    )
    return max(0, min(len(take.sample_seconds) - 1, int(round(t / step))))


def _norm(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    ordered = sorted(values)
    top = ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))] or max(ordered)
    if top <= 0:
        return [0.0 for _ in values]
    return [min(1.0, v / top) for v in values]


def _motion_without_cuts(take: TakeInput) -> list[float]:
    """Frame-to-frame change with the samples at hard cuts replaced by the take's median."""

    motion = list(take.motion)
    if not motion:
        return []
    median = sorted(motion)[len(motion) // 2]
    for i, t in enumerate(take.sample_seconds[: len(motion)]):
        if any(abs(t - cut) <= CUT_MOTION_GUARD_SECONDS for cut in take.cuts):
            motion[i] = median
    return motion


# --- beats -------------------------------------------------------------------------------------


def assign_roles(count: int) -> list[str]:
    """Roles for ``count`` beats in order: plant, escalation..., pivot, new_fact."""

    if count <= 0:
        return []
    if count == 1:
        return ["new_fact"]
    if count == 2:
        return ["plant", "new_fact"]
    return ["plant", *["escalation"] * (count - 3), "pivot", "new_fact"]


def beat_spans(
    beats: Sequence[BeatInput], takes: Mapping[str, TakeInput]
) -> list[BeatSpan]:
    """Place each beat on its take: board rows to filmed shots, roles by position.

    A beat's rows map to the shot of the same index when the take has one shot
    per row; otherwise the take is split evenly by row (or by beat, when the
    spine names no rows).

    Parameters
    ----------
    beats
        The episode's beats in order.
    takes
        The finished takes by id.

    Returns
    -------
    list[BeatSpan]
        One span per beat on a take the desk has, in order.
    """

    roles = assign_roles(len(beats))
    spans: list[BeatSpan] = []
    by_take: dict[str, list[tuple[int, BeatInput]]] = {}
    for position, beat in enumerate(beats):
        by_take.setdefault(beat.take_id, []).append((position, beat))
    for take_id, members in by_take.items():
        take = takes.get(take_id)
        if take is None:
            continue
        rows = max((b.last_row for _, b in members), default=0)
        shots = {shot.index: shot for shot in take.shots}
        for order, (position, beat) in enumerate(members):
            if (
                beat.first_row
                and rows
                and len(shots) == rows
                and all(r in shots for r in range(beat.first_row, beat.last_row + 1))
            ):
                start = shots[beat.first_row].start
                end = shots[beat.last_row].end
            elif beat.first_row and rows:
                start = take.duration * (beat.first_row - 1) / rows
                end = take.duration * beat.last_row / rows
            else:
                start = take.duration * order / len(members)
                end = take.duration * (order + 1) / len(members)
            if order == 0:
                start = 0.0
            if order == len(members) - 1:
                end = take.duration
            spans.append(
                BeatSpan(
                    ordinal=beat.ordinal,
                    take_id=take_id,
                    start=start,
                    end=min(end, take.duration),
                    role=roles[position],
                    label=beat.label,
                    payoff=beat.payoff,
                    lines=beat.lines,
                )  # fmt: skip
            )
    spans.sort(key=lambda s: beats_index(beats, s.ordinal))
    return spans


def beats_index(beats: Sequence[BeatInput], ordinal: int) -> int:
    for index, beat in enumerate(beats):
        if beat.ordinal == ordinal:
            return index
    return len(beats)


def payoff_end(span: BeatSpan, take: TakeInput) -> tuple[float, str]:
    """Where the new fact stops: on its peak, never on a settled tail.

    A breath (:data:`PAYOFF_HOLD_SECONDS`) after its last caption or sound
    event; when the action after that line still moves (motion over
    :data:`ACTION_OVER_MEDIAN` times the take's median and over the static
    floor), the cut runs on to its last moving sample, at most
    :data:`ACTION_REACH_SECONDS` past the line. Inside the beat, and at least
    1 s of it.
    """

    from creation.post.opening import STATIC_MOTION

    last_cue = max(
        (c.end for c in take.cues if span.start <= c.start < span.end), default=None
    )
    last_event = max(
        (t + 0.6 for t in take.events if span.start <= t < span.end), default=None
    )
    marks = [m for m in (last_cue, last_event) if m is not None]
    if not marks:
        return span.end, "no caption or sound event in the beat: plays to its end"
    line = max(marks)
    end = line + PAYOFF_HOLD_SECONDS
    motion = _motion_without_cuts(take)
    if motion:
        median = sorted(motion)[len(motion) // 2]
        floor = max(STATIC_MOTION, ACTION_OVER_MEDIAN * median)
        step = 1.0 / MEASURE_RATE
        moving = [
            t
            for t, m in zip(take.sample_seconds, motion, strict=False)
            if line < t <= min(span.end, line + ACTION_REACH_SECONDS) and m > floor
        ]
        if moving:
            end = max(end, max(moving) + step)
    end = min(span.end, end)
    end = max(end, min(span.end, span.start + 1.0))
    if span.end - end >= 0.2:
        why = f"ends on the peak; settled tail {end:.2f}-{span.end:.2f} s cut"
    else:
        why = "plays to the end of the take"
    return end, why


# --- strongest frame ---------------------------------------------------------------------------


def genre_family(genre: str) -> str:
    """The picture-weight family of a genre (:data:`GENRE_FAMILY_WORDS`): ``action``, ``intimate``, ``horror`` or ``default``.

    Parameters
    ----------
    genre
        The spine's ``microdrama_genre`` (``slice_of_life``, ``war_god_return`` ...).

    Returns
    -------
    str
        A key of :data:`PICTURE_WEIGHTS`.
    """

    words = {w for w in re.split(r"[^a-z0-9]+", (genre or "").lower()) if w}
    for family, keys in GENRE_FAMILY_WORDS.items():
        if words & set(keys):
            return family
    return "default"


def _stillness(motion: Sequence[float]) -> list[float]:
    """A held frame: 1 minus the most motion within :data:`STILL_REACH_SAMPLES` either side (0-1)."""

    k = STILL_REACH_SAMPLES
    return [
        max(0.0, 1.0 - max(motion[max(0, i - k) : i + k + 1]))
        for i in range(len(motion))
    ]


def _face_signal(take: TakeInput) -> list[float]:
    """Each sample's face score: the detector's, else the shot's head count (:mod:`creation.post.faces`)."""

    from creation.post.faces import HEAD_COUNT_FACE

    if take.faces:
        return [float(take.faces[i]) if i < len(take.faces) else 0.0
                for i in range(len(take.sample_seconds))]  # fmt: skip
    out: list[float] = []
    for t in take.sample_seconds:
        shot = next((s for s in take.shots if s.start <= t < s.end), None)
        out.append(HEAD_COUNT_FACE if shot is not None and shot.named > 0 else 0.0)
    return out


def _span_at(mine: Sequence[BeatSpan], t: float) -> BeatSpan | None:
    """The beat a sample plays in; past the last beat's end (rounding) the last beat; before any, none."""

    span = next((s for s in mine if s.start <= t < s.end), None)
    if span is None and mine and t >= max(s.end for s in mine):
        span = max(mine, key=lambda s: s.end)
    return span


def frame_scores(
    take: TakeInput, spans: Sequence[BeatSpan], *, family: str = "default"
) -> list[tuple[float, float, dict[str, float], str]]:
    """Score every sample of a take: ``(time, score, parts, beat role)``.

    ``score = story * (0.5 + 0.5 * picture)``; picture weighs motion,
    contrast, face and stillness by ``family`` (:data:`PICTURE_WEIGHTS`): the
    story (the beat's role, a payoff) says where the strongest moment is; the
    picture says which frame of it. Motion alone would crown a camera pan over
    an empty counter; a face alone would crown every two-shot.

    Parameters
    ----------
    take
        The take with its 8 fps measurement.
    spans
        The episode's beats on the takes.
    family
        The genre family (:func:`genre_family`).

    Returns
    -------
    list
        One row per sample; empty when the take was not measured. A sample
        before every beat (no spine beats there) scores as the plant.
    """

    weights = PICTURE_WEIGHTS.get(family, PICTURE_WEIGHTS["default"])
    motion = _norm(_motion_without_cuts(take))
    contrast = _norm(list(take.contrast))
    still = _stillness(motion)
    faces = _face_signal(take)
    mine = [s for s in spans if s.take_id == take.take_id]
    rows: list[tuple[float, float, dict[str, float], str]] = []
    for i, t in enumerate(take.sample_seconds):
        span = _span_at(mine, t)
        role = span.role if span else "plant"
        story = ROLE_WEIGHT.get(role, 0.35)
        if span is not None and span.payoff:
            story += PAYOFF_BONUS
        story = min(1.0, story)
        signals = {
            "motion": motion[i] if i < len(motion) else 0.0,
            "contrast": contrast[i] if i < len(contrast) else 0.0,
            "face": faces[i] if i < len(faces) else 0.0,
            "stillness": still[i] if i < len(still) else 0.0,
        }
        picture = sum(weights.get(k, 0.0) * v for k, v in signals.items())
        parts = {**signals, "story": story}
        rows.append((t, story * (0.5 + 0.5 * picture), parts, role))
    return rows


def cut_points(take: TakeInput) -> list[float]:
    """Every time a cut may land on: off a word (between lines, a caption's end, a hard cut) or a word's onset."""

    step = 1.0 / MEASURE_RATE
    points = {round(i * step, 4) for i in range(int(take.duration / step) + 1)}
    points |= {round(c, 4) for c in take.cuts}
    points |= {round(c.end, 4) for c in take.cues} | {
        round(c.start, 4) for c in take.cues
    }
    onsets = {round(c.start, 4) for c in take.cues}
    return sorted(
        t
        for t in points
        if 0 <= t <= take.duration and (t in onsets or not _inside_cue(t, take.cues))
    )


def strongest_window(
    takes: Sequence[TakeInput],
    spans: Sequence[BeatSpan],
    *,
    seconds: float = COLD_OPEN_SECONDS,
    banned: Sequence[tuple[str, float, float]] = (),
    family: str = "default",
) -> Strongest | None:
    """The best-scoring window of the episode (the flash-forward), both edges off words.

    Every window from one cut point (:func:`cut_points`) to another, 1-2 s
    long and clear of the take's first and last 0.3 s, is scored by the mean
    of its samples (:func:`frame_scores`), less 0.02 per 0.1 s away from
    ``seconds``; the best wins (the earliest on a tie). Its peak sample is the
    strongest frame. A window touching the new fact (the last beat) is never
    scored: the cold open may not spoil the ending.

    Parameters
    ----------
    takes
        The finished takes.
    spans
        Their beats.
    seconds
        The preferred window length.
    banned
        ``(take, start, end)`` stretches that may not be shown (viewer-address lines).
    family
        The genre family whose picture weights score the frames.

    Returns
    -------
    Strongest | None
        The window and its peak frame; ``None`` when nothing was measured, or
        every window touches the new fact.
    """

    lo_len, hi_len = COLD_OPEN_RANGE
    best: Strongest | None = None
    best_value = -math.inf
    # The ending: the new fact's beat, never shown first.
    ending = [
        (s.take_id, s.start, s.end) for s in spans if s.role not in COLD_OPEN_ROLES
    ]
    banned = [*banned, *ending]
    for take in takes:
        rows = frame_scores(take, spans, family=family)
        if not rows:
            continue
        points = [
            p
            for p in cut_points(take)
            if EDGE_GUARD_SECONDS <= p <= take.duration - EDGE_GUARD_SECONDS
        ]
        for i, start in enumerate(points):
            for end in points[i + 1 :]:
                length = end - start
                if length < lo_len - 1e-6:
                    continue
                if length > hi_len + 1e-6:
                    break
                if any(
                    t == take.take_id and a < end and start < b for t, a, b in banned
                ):
                    continue
                window = [r for r in rows if start <= r[0] < end]
                if not window:
                    continue
                mean = sum(r[1] for r in window) / len(window)
                value = mean - 0.2 * abs(length - seconds)
                if value > best_value + 1e-9:
                    peak = max(window, key=lambda r: r[1])
                    best_value = value
                    best = Strongest(
                        take=take.take_id, at=peak[0], score=peak[1], parts=peak[2],
                        start=frame_snap(start, take.fps), end=frame_snap(end, take.fps), role=peak[3],
                        family=family, face_source=take.face_source,
                    )  # fmt: skip
    return best


# --- fitting -----------------------------------------------------------------------------------


@dataclass
class _Piece:
    take: str
    start: float
    end: float
    role: str
    beat: BeatSpan
    trims: list[tuple[float, float]] = field(default_factory=list)


def _calm_gaps(
    piece: _Piece, take: TakeInput, protect: float
) -> list[tuple[float, float]]:
    """Stretches of a piece with no caption (padded), long enough to cut into."""

    busy = [
        (c.start - CALM_MARGIN_SECONDS, c.end + CALM_MARGIN_SECONDS) for c in take.cues
    ]
    busy += [(t - 0.1, t + 0.5) for t in take.events]
    busy.sort()
    gaps: list[tuple[float, float]] = []
    cursor = max(piece.start, protect)
    for a, b in busy:
        if b <= cursor:
            continue
        if a >= piece.end:
            break
        if a - cursor >= MIN_CALM_SECONDS:
            gaps.append((cursor, min(a, piece.end)))
        cursor = max(cursor, b)
    if piece.end - cursor >= MIN_CALM_SECONDS:
        gaps.append((cursor, piece.end))
    return [(a, b) for a, b in gaps if b - a >= MIN_CALM_SECONDS]


def _quietest(
    take: TakeInput, a: float, b: float, length: float
) -> tuple[float, float]:
    """The lowest-motion window of ``length`` inside ``a``-``b``: ``(start, mean motion)``.

    Raw frame-to-frame change (not scaled per take), so stretches of different
    takes compare on one scale.
    """

    motion = _motion_without_cuts(take)
    if not motion:
        return a, 0.0
    best_start, best = a, math.inf
    t = a
    while t + length <= b + 1e-6:
        i0, i1 = _sample_index(take, t), _sample_index(take, t + length)
        window = motion[i0 : max(i1, i0 + 1)]
        mean = sum(window) / len(window)
        if mean < best - 1e-9:
            best_start, best = t, mean
        t += 0.125
    return best_start, best


def _trim_calm(
    pieces: list[_Piece], takes: Mapping[str, TakeInput], need: float, first_take: str
) -> float:
    """Cut the calmest stretches (no caption, low motion) until ``need`` seconds are gone; returns what is left."""

    gaps: list[tuple[_Piece, float, float]] = []
    for piece in pieces:
        if piece.role not in TRIM_PENALTY:
            continue
        protect = (
            HOOK_SECONDS
            if piece.take == first_take and piece.beat.role == "plant"
            else 0.0
        )
        for a, b in _calm_gaps(piece, takes[piece.take], protect):
            gaps.append((piece, a, b))
    removed = [0.0] * len(gaps)

    def kept(piece: _Piece) -> float:
        cut = sum(
            r for (owner, _, _), r in zip(gaps, removed, strict=True) if owner is piece
        )
        return piece.end - piece.start - cut

    while need > 1e-6 and gaps:
        best_i, best_cost = -1, math.inf
        for i, (piece, a, b) in enumerate(gaps):
            room = b - a
            step = min(TRIM_STEP_SECONDS, need)
            if removed[i] + step > room + 1e-6:
                continue
            # The one pivot always keeps enough to read as the turn.
            if (
                kept(piece) - step
                < KEEP_SECONDS.get(piece.role, MIN_SEGMENT_SECONDS) - 1e-6
            ):
                continue
            _, motion = _quietest(takes[piece.take], a, b, removed[i] + step)
            cost = motion + TRIM_PENALTY[piece.role]
            if cost < best_cost - 1e-9:
                best_i, best_cost = i, cost
        if best_i < 0:
            break
        step = min(TRIM_STEP_SECONDS, need)
        removed[best_i] += step
        need -= step
    for (piece, a, b), length in zip(gaps, removed, strict=True):
        if length <= 1e-6:
            continue
        take = takes[piece.take]
        start, _ = _quietest(take, a, b, length)
        end = start + length
        # A hard cut just past an edge would leave a flash of the old shot: move the edge onto it.
        for cut in take.cuts:
            if end < cut <= end + FLASH_SECONDS and not any(
                end < c.start < cut for c in take.cues
            ):
                need -= cut - end
                end = cut
            if start - FLASH_SECONDS <= cut < start and not any(
                cut < c.end < start for c in take.cues
            ):
                need -= start - cut
                start = cut
        # A sliver left between the trim and the piece's edge would be dropped anyway: take it now.
        if 0 < start - piece.start < MIN_SEGMENT_SECONDS and piece.role != "pivot":
            need -= start - piece.start
            start = piece.start
        if 0 < piece.end - end < MIN_SEGMENT_SECONDS and piece.role != "pivot":
            need -= piece.end - end
            end = piece.end
        piece.trims.append((start, end))
    return need


def _split(piece: _Piece) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    cursor = piece.start
    for a, b in sorted(piece.trims):
        if a > cursor:
            spans.append((cursor, a))
        cursor = max(cursor, b)
    if piece.end > cursor:
        spans.append((cursor, piece.end))
    return spans


def _trim_heads(
    pieces: list[_Piece],
    takes: Mapping[str, TakeInput],
    need: float,
    plan: ReelPlan,
    seconds: float,
    *,
    whole_lines: bool,
) -> float:
    """Cut the heads of pieces (plant, escalation, pivot; the new fact only its calm lead-in).

    Without ``whole_lines`` only the stretch before a piece's first caption goes.
    With it, whole speech runs go from the head (a cut never lands inside a
    line), each lost line named in a ⚠. Returns the seconds still to cut.
    """

    for role in ("plant", "escalation", "pivot", "new_fact"):
        if role == "new_fact" and whole_lines:
            continue
        for piece in [p for p in pieces if p.role == role]:
            if need <= 1e-6:
                return need
            take = takes[piece.take]
            runs = [
                r
                for r in speech_runs(take.cues)
                if r[1] > piece.start and r[0] < piece.end
            ]
            keep_min = 1.5 if role == "new_fact" else MIN_SEGMENT_SECONDS
            limit = piece.end - keep_min
            if not whole_lines:
                first = runs[0][0] - CALM_MARGIN_SECONDS if runs else limit
                target = min(piece.start + need, first, limit)
            else:
                target = piece.start
                for a, b in runs:
                    if target - piece.start >= need or b + CALM_MARGIN_SECONDS > limit:
                        break
                    candidate = b + CALM_MARGIN_SECONDS
                    # A line goes only when the reel ends up nearer the length than it is now.
                    if abs((candidate - piece.start) - need) >= need:
                        break
                    target = candidate
                if target - piece.start < need and not runs:
                    target = min(piece.start + need, limit)
            if target <= piece.start + 1e-6:
                continue
            target = frame_snap(target, take.fps)
            lost = [c.text for c in take.cues if piece.start <= c.start < target]
            trimmed = (
                target
                - piece.start
                - sum(
                    max(0.0, min(b, target) - max(a, piece.start))
                    for a, b in piece.trims
                )
            )
            plan.notes.append(
                f"{piece.take} {piece.start:.2f}-{target:.2f} s cut from the head of beat "
                f"{piece.beat.ordinal} ({role}) to fit"
            )
            if lost:
                plan.warnings.append(
                    f"beat {piece.beat.ordinal}: {len(lost)} caption cue(s) left out to fit {seconds:g} s "
                    f'(from "{lost[0][:40]}"); edit the plan to keep them'
                )
            piece.trims = [(max(a, target), b) for a, b in piece.trims if b > target]
            piece.start = target
            need -= trimmed
    return need


def _piece_seconds(pieces: Sequence[_Piece]) -> float:
    return sum(b - a for p in pieces for a, b in _split(p))


def _mean_score(
    take: TakeInput,
    spans: Sequence[BeatSpan],
    a: float,
    b: float,
    family: str = "default",
) -> float:
    rows = [r for r in frame_scores(take, spans, family=family) if a <= r[0] < b]
    return sum(r[1] for r in rows) / len(rows) if rows else 0.0


def _why(role: str, beat: BeatSpan) -> str:
    said = f' ("{beat.lines[0][:48]}")' if beat.lines else ""
    what = {
        "plant": "plants the setup the new fact pays off",
        "escalation": "raises the stakes",
        "pivot": "the turn into the new fact",
        "new_fact": "the episode's new fact (its last beat)",
    }[role]
    return f"beat {beat.ordinal}, {what}{said}"


def plan_reel(
    episode: int,
    takes: Sequence[TakeInput],
    beats: Sequence[BeatInput],
    *,
    seconds: float = DEFAULT_SECONDS,
    genre: str = "",
    ending: str = "hard",
    pov: bool = False,
) -> ReelPlan:
    """Plan the reel: a flash-forward from the strongest frame, then the beats, ending on the new fact.

    Works on any episode: every number comes from that episode's own takes and beats.

    Parameters
    ----------
    episode
        Episode ordinal.
    takes
        The finished takes in order, measured.
    beats
        The episode's beats in order.
    seconds
        The reel's target length (6-30 s).
    genre
        The series' genre (the spine's ``microdrama_genre``): picks the picture
        weights of the cold-open score (:func:`genre_family`).
    ending
        ``hard`` (default: the reel ends on the last frame) or ``freeze-black``.
    pov
        A POV episode (:func:`brief_is_pov`): lines said to the camera stay in.

    Returns
    -------
    ReelPlan
        Ordered segments; anything off target is a ⚠ in ``warnings``, never an error.

    Raises
    ------
    ValueError
        When ``seconds`` is outside 6-30 or there is no take.
    """

    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise ValueError(
            f"--seconds {seconds:g}: a reel is {MIN_SECONDS:g}-{MAX_SECONDS:g} s"
        )
    if not takes:
        raise ValueError("no finished take to cut the reel from")
    if ending not in ENDING_STYLES:
        raise ValueError(f"--ending {ending!r}: one of {', '.join(ENDING_STYLES)}")
    by_id = {take.take_id: take for take in takes}
    family = genre_family(genre)
    plan = ReelPlan(episode=episode, seconds=seconds, segments=[], ending=ending)
    spans = beat_spans(beats, by_id)
    if not spans:
        last = takes[-1]
        spans = [
            BeatSpan(
                0,
                last.take_id,
                max(0.0, last.duration - min(last.duration, seconds * 0.4)),
                last.duration,
                "new_fact",
            )
        ]
        plan.warnings.append(
            "the spine names no beats for this episode: the new fact is taken as the last "
            f"{spans[0].end - spans[0].start:.1f} s of {last.take_id}; check it ends on the episode's payoff"
        )
    banned = [
        (take.take_id, cue.start - 0.05, cue.end + 0.05)
        for take in takes
        for cue in take.cues
        if viewer_address(cue.text, pov=pov)
    ]
    for take_id, a, b in banned:
        plan.warnings.append(
            f"{take_id} {a:.2f}-{b:.2f} s is said to the viewer; it is cut out of the reel "
            "(the call to action goes in the post text)"
        )
    for span in spans:
        for line in span.lines:
            if viewer_address(line, pov=pov):
                plan.warnings.append(
                    f'beat {span.ordinal} line "{line[:60]}" reads as said to the viewer; its caption span is cut'
                )

    last = next((sp for sp in reversed(spans) if sp.role == "new_fact"), None)
    if last is not None:
        plan.last_beat = (last.take_id, last.start, last.end)
    cold = strongest_window(takes, spans, banned=banned, family=family)
    budget = seconds - (cold.end - cold.start if cold else 0.0)

    pieces: list[_Piece] = []
    for span in spans:
        take = by_id[span.take_id]
        end = span.end
        if span.role == "new_fact":
            end, tail = payoff_end(span, take)
            plan.notes.append(f"new fact (beat {span.ordinal}): {tail}")
        pieces.append(_Piece(span.take_id, span.start, end, span.role, span))

    # 0. Over the length: the flash-forward gives back down to its shortest first.
    over = _piece_seconds(pieces) - budget
    if (
        cold is not None
        and over > 0
        and cold.end - cold.start > COLD_OPEN_RANGE[0] + 0.05
    ):
        take = by_id[cold.take]
        floor = cold.start + COLD_OPEN_RANGE[0]
        target = max(floor, cold.end - over)
        options = [target] if not _inside_cue(target, take.cues) else []
        options += [
            c.end
            for c in take.cues
            if floor <= c.end <= cold.end and not _inside_cue(c.end, take.cues)
        ]
        options += [c.start for c in take.cues if floor <= c.start <= cold.end]
        if options:
            end = frame_snap(
                min(options, key=lambda t: (abs(t - target), -t)), take.fps
            )
            if end < cold.end - 1e-6:
                plan.notes.append(
                    f"flash-forward shortened {cold.end - cold.start:.2f} -> {end - cold.start:.2f} s to fit"
                )
                cold = replace(cold, end=end)
                budget = seconds - (cold.end - cold.start)

    # 1. Escalations go first, lowest score first, while they do not overshoot by much.
    escalations = sorted(
        (p for p in pieces if p.role == "escalation"),
        key=lambda p: _mean_score(by_id[p.take], spans, p.start, p.end, family),
    )
    for piece in escalations:
        over = _piece_seconds(pieces) - budget
        if over <= 0:
            break
        length = piece.end - piece.start
        if over - length >= -4.0:
            pieces.remove(piece)
            plan.notes.append(
                f"beat {piece.beat.ordinal} (escalation, {length:.1f} s) left out: lowest score, over the length"
            )

    # 2. The calm stretches between lines.
    first_take = takes[0].take_id
    need = _piece_seconds(pieces) - budget
    if need > 0:
        left = _trim_calm(pieces, by_id, need, first_take)
        trimmed = [(p, a, b) for p in pieces for a, b in p.trims]
        for piece, a, b in trimmed:
            plan.notes.append(
                f"{piece.take} {a:.2f}-{b:.2f} s cut from beat {piece.beat.ordinal} ({piece.role}): "
                "a calm stretch, no line, lowest motion"
            )
        need = left

    # 3. Heads of segments: first only the calm before the first line, then (more than
    #    OVER_TOLERANCE_SECONDS over) whole lines, plant first; the new fact keeps its lines.
    if need > 1e-6:
        need = _trim_heads(pieces, by_id, need, plan, seconds, whole_lines=False)
    if need > OVER_TOLERANCE_SECONDS:
        need = _trim_heads(pieces, by_id, need, plan, seconds, whole_lines=True)

    segments: list[Segment] = []
    if cold is not None:
        segments.append(
            Segment(
                cold.take,
                cold.start,
                cold.end,
                "cold_open",
                f"flash-forward from the {cold.role.replace('_', ' ')}: the strongest frame before the reveal, "
                f"{cold.take} {cold.at:.2f} s (score {cold.score:.2f}); then how we got there",
            )  # fmt: skip
        )
        plan.strongest = cold
    elif any(take.sample_seconds for take in takes):
        plan.warnings.append(
            "no 1-2 s window before the new fact to flash forward to (the episode is one beat, or every "
            "window touches the ending): no flash-forward; the reel opens on the plant"
        )
    else:
        plan.warnings.append(
            "no frames were measured: no flash-forward; the reel opens on the plant"
        )
    for piece in pieces:
        take = by_id[piece.take]
        for a, b in _split(piece):
            a, b = frame_snap(a, take.fps), frame_snap(b, take.fps)
            if b - a < MIN_SEGMENT_SECONDS and piece.role != "new_fact":
                continue
            segments.append(
                Segment(piece.take, a, b, piece.role, _why(piece.role, piece.beat))
            )
    segments = cut_out(segments, banned)
    plan.segments = segments
    plan.warnings += check_plan(plan, takes, pov=pov)
    return plan


def cut_out(
    segments: Sequence[Segment], banned: Sequence[tuple[str, float, float]]
) -> list[Segment]:
    """Remove each banned stretch (a viewer-address line) from every segment that holds it."""

    out = list(segments)
    for take, a, b in banned:
        kept: list[Segment] = []
        for seg in out:
            if seg.take != take or seg.end <= a or seg.start >= b:
                kept.append(seg)
                continue
            if seg.start < a - 0.05:
                kept.append(replace(seg, end=a))
            if seg.end > b + 0.05:
                kept.append(replace(seg, start=b))
        out = kept
    return out


def _overlaps(take: str, a: float, b: float, other: tuple[str, float, float]) -> bool:
    return take == other[0] and a < other[2] - 1e-6 and other[1] < b - 1e-6


def cold_open_spoils(plan: ReelPlan) -> list[str]:
    """⚠ when the cold open shows the ending: the last beat, or the reel's last segment.

    Parameters
    ----------
    plan
        The plan (made here or edited by hand).

    Returns
    -------
    list[str]
        One warning per spoiler.
    """

    out: list[str] = []
    segments = plan.segments
    colds = [seg for seg in segments if seg.role == "cold_open"]
    if not colds:
        return out
    ending = [seg for seg in segments if seg.role == "new_fact"]
    if segments and segments[-1].role != "cold_open":
        ending.append(segments[-1])
    for cold in colds:
        if plan.last_beat is not None and _overlaps(
            cold.take, cold.start, cold.end, plan.last_beat
        ):
            take, a, b = plan.last_beat
            out.append(
                f"the cold open {cold.take} {cold.start:.2f}-{cold.end:.2f} s is inside the last beat "
                f"({take} {a:.2f}-{b:.2f} s, the new fact): the reel opens on its own ending. Take the cold open "
                "from the pivot or the peak before the reveal"
            )
            continue
        for seg in ending:
            if seg is not cold and _overlaps(
                cold.take, cold.start, cold.end, (seg.take, seg.start, seg.end)
            ):
                out.append(
                    f"the cold open {cold.take} {cold.start:.2f}-{cold.end:.2f} s overlaps the ending "
                    f"({seg.role} {seg.take} {seg.start:.2f}-{seg.end:.2f} s): the reel opens on its own ending"
                )
                break
    return out


def _reel_samples(
    segments: Sequence[Segment], by_id: Mapping[str, TakeInput], seconds: float
) -> list[tuple[TakeInput, int, bool]]:
    """The take samples the reel plays in its first ``seconds``: ``(take, sample index, first after a cut)``."""

    out: list[tuple[TakeInput, int, bool]] = []
    clock = 0.0
    previous: Segment | None = None
    for seg in segments:
        take = by_id.get(seg.take)
        if take is None or clock > seconds:
            break
        joined = (
            previous is not None
            and previous.take == seg.take
            and abs(previous.end - seg.start) < 0.03
        )
        first = True
        for i, t in enumerate(take.sample_seconds):
            if (
                seg.start - 1e-6 <= t < seg.end
                and clock + (t - seg.start) < seconds - 1e-6
            ):
                out.append((take, i, first and previous is not None and not joined))
                first = False
        clock += seg.end - seg.start
        previous = seg
    return out


def plan_edges(
    plan: ReelPlan, takes: Sequence[TakeInput]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    """The reel's first second and its tail, from the takes' samples (:mod:`creation.post.opening`).

    The opening is silent / visual (no face check) when no caption plays in
    the reel's first segment. A cut inside the first second counts as motion
    (the luma step across it, a lower bound of the change).

    Parameters
    ----------
    plan
        The plan.
    takes
        The measured takes.

    Returns
    -------
    tuple
        ``(opening, tail, warnings)``: the JSON blocks (``None`` when not
        measured) and their ⚠ lines.
    """

    from creation.post.faces import HEAD_COUNT_FACE
    from creation.post.opening import (
        OPENING_SECONDS,
        opening_reading,
        tail_reading,
        tail_warning,
    )

    by_id = {t.take_id: t for t in takes}
    warnings: list[str] = []
    opening: dict[str, Any] | None = None
    tail: dict[str, Any] | None = None
    if not plan.segments:
        return None, None, warnings
    played = _reel_samples(plan.segments, by_id, OPENING_SECONDS)
    if played and all(take.luma for take, _, _ in played):
        lumas = [take.luma[i] for take, i, _ in played]
        moves: list[float] = []
        for k, (take, i, after_cut) in enumerate(played):
            move = take.motion[i] if i < len(take.motion) else 0.0
            if after_cut and k:
                move = max(move, abs(lumas[k] - lumas[k - 1]))
            moves.append(move)
        first_take, first_i, _ = played[0]
        face: float | None
        if first_take.faces:
            face = first_take.faces[first_i] if first_i < len(first_take.faces) else 0.0
        elif first_take.shots:
            t0 = first_take.sample_seconds[first_i]
            shot = next((s for s in first_take.shots if s.start <= t0 < s.end), None)
            face = HEAD_COUNT_FACE if shot is not None and shot.named > 0 else 0.0
        else:
            face = None
        head = plan.segments[0]
        silent = (
            not any(
                c.start < head.end and head.start < c.end for c in by_id[head.take].cues
            )
            if head.take in by_id
            else True
        )
        reading = opening_reading(
            lumas, moves, face=face, face_source=first_take.face_source,
            silent_open=silent, where="the reel's first second",
        )  # fmt: skip
        opening = {"take": head.take, "role": head.role, **reading.as_json()}
        warnings += list(reading.warnings)
    last = plan.segments[-1]
    take = by_id.get(last.take)
    if take is not None and take.sample_seconds:
        marks = [
            min(c.end, last.end)
            for c in take.cues
            if c.start < last.end and last.start < c.end
        ]
        marks += [
            min(t + 0.6, last.end) for t in take.events if last.start <= t < last.end
        ]
        motion = _motion_without_cuts(take)
        reading_t = tail_reading(
            take.sample_seconds, motion, end=last.end, start=last.start,
            last_mark=max(marks) if marks else None,
        )  # fmt: skip
        tail = {"take": last.take, "role": last.role, **reading_t.as_json()}
        line = tail_warning(reading_t, what="the reel")
        if line and plan.ending == "hard":
            warnings.append(line)
        elif line:
            warnings.append(
                line + f" (the {plan.ending} ending freezes this settled frame)"
            )
    return opening, tail, warnings


def check_plan(
    plan: ReelPlan, takes: Sequence[TakeInput], *, pov: bool = False
) -> list[str]:
    """The ⚠ lines for a plan: not ending on the new fact, over length, a cut inside a word, a viewer line,
    a cold open that shows the ending, a dark / static / faceless first second, a settled tail.

    Fills ``plan.opening`` and ``plan.tail`` with what it measured.

    Parameters
    ----------
    plan
        The plan (made here or edited by hand).
    takes
        The takes it cuts.

    Returns
    -------
    list[str]
        Warnings, never a stop.
    """

    warnings: list[str] = []
    by_id = {t.take_id: t for t in takes}
    warnings += cold_open_spoils(plan)
    opening, tail, edge_warnings = plan_edges(plan, takes)
    plan.opening, plan.tail = opening, tail
    warnings += edge_warnings
    if not plan.ends_on_new_fact():
        warnings.append(
            "the reel does not end on the new fact: end it on the episode's last beat (role new_fact)"
        )
    if plan.total > plan.seconds + 0.05:
        warnings.append(
            f"the reel runs {plan.total:.2f} s, over --seconds {plan.seconds:g}"
        )
    segments = plan.segments
    for index, seg in enumerate(segments):
        take = by_id.get(seg.take)
        if take is None:
            continue
        before = segments[index - 1] if index else None
        after = segments[index + 1] if index + 1 < len(segments) else None
        edges = []
        # A segment that runs straight on from the one before (same take, same time) is no cut.
        if not (
            before and before.take == seg.take and abs(before.end - seg.start) < 0.03
        ):
            edges.append(seg.start)
        if not (after and after.take == seg.take and abs(after.start - seg.end) < 0.03):
            edges.append(seg.end)
        for edge in edges:
            if 0.05 < edge < take.duration - 0.05 and _inside_cue(
                edge, take.cues, pad=0.05
            ):
                if not any(abs(edge - c.start) < 0.03 for c in take.cues):
                    warnings.append(
                        f"{seg.role} {seg.take} cuts inside a word at {edge:.2f} s"
                    )
        for cue in take.cues:
            if (
                seg.start < cue.end
                and cue.start < seg.end
                and viewer_address(cue.text, pov=pov)
            ):
                warnings.append(
                    f'{seg.role} {seg.take} holds a line said to the viewer: "{cue.text[:50]}"'
                )
    return list(dict.fromkeys(warnings))


# --- the reel's timeline -----------------------------------------------------------------------


def segment_map(
    segments: Sequence[Segment], fps: float = 24.0
) -> list[tuple[Segment, float]]:
    """Each segment with where it starts on the reel (frame-exact lengths)."""

    placed: list[tuple[Segment, float]] = []
    clock = 0
    for seg in segments:
        frames = round(seg.end * fps) - round(seg.start * fps)
        placed.append((seg, clock / fps))
        clock += max(0, frames)
    return placed


def contiguous_runs(segments: Sequence[Segment], fps: float = 24.0) -> list[Segment]:
    """Segments that run straight on (same take, the next starts on the frame the last ended) merged.

    Parameters
    ----------
    segments
        The reel's segments in order.
    fps
        Frame rate (frame-exact comparison).

    Returns
    -------
    list[Segment]
        One segment per real cut; the merged one keeps the first one's role.
    """

    runs: list[Segment] = []
    for seg in segments:
        if (
            runs
            and runs[-1].take == seg.take
            and round(runs[-1].end * fps) == round(seg.start * fps)
        ):
            runs[-1] = replace(runs[-1], end=seg.end)
        else:
            runs.append(seg)
    return runs


def reel_seconds(segments: Sequence[Segment], fps: float = 24.0) -> float:
    """The reel's length on the frame grid."""

    return (
        sum(max(0, round(s.end * fps) - round(s.start * fps)) for s in segments) / fps
    )


def retime_cues(
    segments: Sequence[Segment],
    cues_by_take: Mapping[str, Sequence[Cue]],
    fps: float = 24.0,
) -> list[Cue]:
    """Move the accepted cut's captions onto the reel through the segment map.

    A caption plays where its picture plays: each cue of a segment's take that
    overlaps the segment is clipped to it and moved to the segment's place on
    the reel. A cue is kept when most of it plays (``CUE_VISIBLE_FRACTION``) or
    it plays at least ``MIN_CUE_SECONDS``. A word-flicker cue whose earlier words
    were cut away (its text starts with the cue before it, which is not on the
    reel next to it) shows only its own words.

    Parameters
    ----------
    segments
        The reel's segments in order.
    cues_by_take
        Caption cues on each take's own timeline.
    fps
        The reel's frame rate.

    Returns
    -------
    list[Cue]
        Cues on the reel's timeline, in order.
    """

    out: list[Cue] = []
    for seg, at in segment_map(contiguous_runs(segments, fps), fps):
        a, b = round(seg.start * fps) / fps, round(seg.end * fps) / fps
        cues = sorted(cues_by_take.get(seg.take, ()), key=lambda c: c.start)
        #: What each cue of this segment showed (a flicker cue builds on the one before it).
        shown: dict[int, str] = {}
        for index, cue in enumerate(cues):
            start, end = max(cue.start, a), min(cue.end, b)
            seen = end - start
            if seen <= 0:
                continue
            full = max(1e-6, cue.end - cue.start)
            if seen < CUE_VISIBLE_FRACTION * full and seen < MIN_CUE_SECONDS:
                continue
            text = cue.text
            before = cues[index - 1] if index else None
            if (
                before is not None
                and abs(before.end - cue.start) < 0.02
                and text.startswith(before.text)
                and len(text) > len(before.text)
            ):
                # The words this cue adds to the one before; only what played before stays in front.
                added = text[len(before.text) :].strip()
                prior = shown.get(index - 1)
                text = f"{prior} {added}".strip() if prior else added
            out.append(
                Cue(round(at + start - a, 3), round(at + end - a, 3), text, cue.italic)
            )
            shown[index] = text
    return out


# --- plan files --------------------------------------------------------------------------------


def plan_json(
    plan: ReelPlan,
    *,
    takes: Mapping[str, Mapping[str, Any]],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The plan as the JSON a human may edit (``--plan-only``) and render (``--plan FILE``)."""

    body: dict[str, Any] = {
        "kind": PLAN_KIND,
        "schema": PLAN_SCHEMA,
        "episode": plan.episode,
        "seconds": plan.seconds,
        "total_s": round(plan.total, 3),
        "takes": {k: dict(v) for k, v in takes.items()},
        "strongest": plan.strongest.as_json() if plan.strongest else None,
        "cold_open": cold_open_json(plan),
        "last_beat": (
            {
                "take": plan.last_beat[0],
                "start_s": round(plan.last_beat[1], 3),
                "end_s": round(plan.last_beat[2], 3),
            }
            if plan.last_beat
            else None
        ),  # fmt: skip
        "opening": plan.opening,
        "tail": plan.tail,
        "ending": {
            "style": plan.ending,
            "freeze_s": FREEZE_SECONDS,
            "black_s": BLACK_SECONDS,
        }
        if plan.ending != "hard"
        else {"style": "hard"},
        "segments": [segment.as_json() for segment in plan.segments],
        "patches": [],
        "notes": list(plan.notes),
        "warnings": list(plan.warnings),
    }
    body.update(dict(extra or {}))
    return body


def cold_open_json(plan: ReelPlan) -> dict[str, Any] | None:
    """The plan JSON's ``cold_open`` block: the role it came from, its face score and the weights that chose it.

    Parameters
    ----------
    plan
        The plan.

    Returns
    -------
    dict | None
        ``None`` when the reel has no cold open.
    """

    seg = next((s for s in plan.segments if s.role == "cold_open"), None)
    if seg is None:
        return None
    s = plan.strongest
    body: dict[str, Any] = {
        "take": seg.take,
        "window": [round(seg.start, 3), round(seg.end, 3)],
        "beat_role": s.role if s else None,
        "face": round(s.parts.get("face", 0.0), 3) if s else None,
        "stillness": round(s.parts.get("stillness", 0.0), 3) if s else None,
        "face_source": s.face_source if s else None,
        "genre_family": s.family if s else None,
        "weights": dict(PICTURE_WEIGHTS.get(s.family, PICTURE_WEIGHTS["default"]))
        if s
        else None,
        "spoils_ending": bool(cold_open_spoils(plan)),
    }
    return body


def last_beat_from_json(raw: Any) -> tuple[str, float, float] | None:
    """A plan file's ``last_beat`` back as ``(take, start, end)`` (``None`` when absent or unreadable)."""

    if not isinstance(raw, Mapping):
        return None
    try:
        return str(raw["take"]), float(raw["start_s"]), float(raw["end_s"])
    except (KeyError, TypeError, ValueError):
        return None


def ending_from_json(raw: Any) -> str:
    """A plan file's ending style (``hard`` when absent or unknown)."""

    style = raw.get("style") if isinstance(raw, Mapping) else raw
    return str(style) if style in ENDING_STYLES else "hard"


def segments_from_json(body: Mapping[str, Any]) -> list[Segment]:
    """Read the segments of a plan file.

    Raises
    ------
    ValueError
        When the file is not a reel plan, or a segment has no take, a bad role or an empty window.
    """

    if body.get("kind") != PLAN_KIND:
        raise ValueError(
            f"not a reel plan (kind {body.get('kind')!r}, expected {PLAN_KIND!r})"
        )
    segments: list[Segment] = []
    for index, raw in enumerate(body.get("segments") or [], start=1):
        try:
            seg = Segment(
                str(raw["take"]), float(raw["start_s"]), float(raw["end_s"]),
                str(raw.get("role") or ""), str(raw.get("why") or ""),
            )  # fmt: skip
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"segment {index}: needs take, start_s and end_s ({exc})"
            ) from exc
        if seg.role not in ROLES:
            raise ValueError(
                f"segment {index}: role {seg.role!r} is not one of {', '.join(ROLES)}"
            )
        if seg.end <= seg.start:
            raise ValueError(
                f"segment {index}: end_s {seg.end:g} is not after start_s {seg.start:g}"
            )
        segments.append(seg)
    if not segments:
        raise ValueError("the plan has no segments")
    return segments


def strongest_from_json(raw: Any) -> Strongest | None:
    """A plan file's ``strongest`` block back as a :class:`Strongest` (``None`` when absent or unreadable)."""

    if not isinstance(raw, Mapping):
        return None
    try:
        window = raw.get("window") or [0.0, 0.0]
        return Strongest(
            take=str(raw["take"]), at=float(raw["at_s"]), score=float(raw["score"]),
            parts={str(k): float(v) for k, v in (raw.get("parts") or {}).items()},
            start=float(window[0]), end=float(window[1]), role=str(raw.get("beat_role") or ""),
            family=str(raw.get("genre_family") or "default"), face_source=str(raw.get("face_source") or "none"),
        )  # fmt: skip
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def genre_hashtags(genre: str, *, limit: int = 2) -> list[str]:
    """One hashtag word per genre: ``slice_of_life`` -> ``sliceoflife``, never ``#slice #of``.

    Several genres may be given, split by ``,`` ``/`` ``|`` or ``;``. Each is
    lowercased and loses everything but letters and digits.

    Parameters
    ----------
    genre
        The spine's ``microdrama_genre`` (``war_god_return``, ``Slice of Life``,
        ``romance, horror``).
    limit
        At most this many.

    Returns
    -------
    list[str]
        Tag words without the ``#``, in order, no repeats.
    """

    tags: list[str] = []
    for entry in re.split(r"[,/|;]+", genre or ""):
        tag = re.sub(r"[^a-z0-9]+", "", entry.lower())
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:limit]


def post_text(
    *,
    series: str,
    episode: int,
    title: str = "",
    question: str = "",
    genre: str = "",
    has_next: bool = True,
    premise_line: str = "",
) -> str:
    """The suggested post text: where the call to action goes (never in the reel).

    Plain words; a sentence naming another app, a model or provider, or a price
    is left out.

    Parameters
    ----------
    series, episode, title
        What the post is.
    question
        The episode's hook question (asked of the viewer here, in the post, not on screen).
    genre
        For up to two plain hashtags, one per genre (:func:`genre_hashtags`).
    has_next
        Whether to point at the next episode.
    premise_line
        The show's one-sentence premise (``spine.premise_line``). When set it is the post's
        title and "Series · Episode N" moves under it.

    Returns
    -------
    str
        The post text, ending in a newline.
    """

    head = f"{series} · Episode {episode}" + (f": {title}" if title else "")
    lines = [premise_line.strip(), head, ""] if premise_line.strip() else [head, ""]
    if question:
        lines.append(question.strip())
    lines.append(
        f"Episode {episode + 1} is next. Follow so you don't miss it."
        if has_next
        else "Follow for the next one."
    )
    tags = genre_hashtags(genre)
    if tags:
        lines += ["", " ".join(f"#{t}" for t in [*tags, "shortdrama"])]
    kept = [
        line for line in lines if not (_POST_BANNED.search(line) or _PRICE.search(line))
    ]
    return "\n".join(kept).strip() + "\n"


__all__ = [
    "BeatInput",
    "BeatSpan",
    "DEFAULT_SECONDS",
    "ReelPlan",
    "Segment",
    "Shot",
    "Strongest",
    "TakeInput",
    "assign_roles",
    "beat_spans",
    "check_plan",
    "contiguous_runs",
    "cut_out",
    "ENDING_STYLES",
    "PICTURE_WEIGHTS",
    "cold_open_json",
    "cold_open_spoils",
    "ending_from_json",
    "frame_scores",
    "genre_family",
    "genre_hashtags",
    "last_beat_from_json",
    "plan_edges",
    "payoff_end",
    "plan_json",
    "plan_reel",
    "post_text",
    "reel_seconds",
    "retime_cues",
    "segment_map",
    "segments_from_json",
    "snap_cut",
    "speech_runs",
    "strongest_from_json",
    "strongest_window",
    "brief_is_pov",
    "viewer_address",
]
