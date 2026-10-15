"""Punch up a take's big impact on the frame it lands: a punch-in and a shake, never a flash ($0, ffmpeg).

What worked in production, by hand: a punch-in or a crop rescued a weak key
moment (L-20260929-24), and an explosion was sold with a decaying crop shake
on the blast frame and a full-level boom (L-20261009-9). The white flash of
that edit is never drawn: a gunshot, or any impact, gets a zoom and a shake
only (founder, 13 Oct 2026, Motion & Physics review). The manual tools (``zoom``, ``freeze``,
``soften``...) stay; this is the automatic pass ``finish`` runs as its
``impact`` step, the same pass the server runs on every hosted episode
(fictora-drama ``impact_post``), with the same numbers:

* **Which beats.** The effects the ``sfx`` step laid (the take facts' plan:
  the story's stated impacts and Sound-line events) that name a big impact:
  :data:`BLAST_STEMS` (an explosion, a blast, a boom, a gunshot, a detonation)
  or :data:`HIT_STEMS` (a slam, a bang, a crash, a punch, a slap...): the
  server's lists.
  A knock, a thud, a creak, a cue naming several hits ("three cane bangs",
  :func:`names_several_hits`) or the opening hook gets nothing.
* **Where.** On MEASURED motion: the clearest motion spike in the effect's
  filmed shot (:func:`creation.post.sfx_motion.action_moment`, the trace the
  ``sfx`` step snapped the effect to), and only where the effect is heard
  (within :data:`SOUND_TOLERANCE_SECONDS`). No clear spike, no effect.
* **What.** Every moment is a punch-in (held, then eased back) and a
  decaying crop shake, never a flash: a blast (an explosion, a gunshot, a
  boom) punches in further (:data:`BLAST_PUNCH_ZOOM`) and shakes harder; a
  hard hit gets :data:`PUNCH_ZOOM` and a small shake. Each stays inside its
  filmed shot. :class:`Move` has no flash: there is no flash path here.
* **Taste.** At most :data:`MAX_MOMENTS_PER_TAKE` per take (blasts first,
  then the strongest motion), never overlapping, never mid-line.

Same length, same frames; the sound is copied untouched. Desks created before
6 Oct 2026 do not run it: their effects do not follow the action yet
(:mod:`creation.post.sfx_motion`), so a punch-in would not land with its boom.
"""

from __future__ import annotations

import math
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Literal

import numpy as np

from creation.harness_rules import is_opening_cue
from creation.post.media import MediaToolError, ffmpeg_bin, ffprobe_bin
from creation.post.sfx_motion import Trace, action_moment

ImpactStyle = Literal["blast", "hit"]

# The two word lists mirror ``creation/post/ambience.py`` (kit PR #219; the server's ``impact_words``, #767)
# exactly, so the sound and the picture agree on what is an explosion and what is a hit; this moves to an
# import from ``ambience`` once #219 lands.
#: Things that go off (a gunshot too): the bigger punch-in and the harder shake. A "bang" is never one.
BLAST_STEMS = frozenset(
    {
        "explosion",
        "explode",
        "blast",
        "boom",
        "gunshot",
        "detonation",
        "detonate",
        "kaboom",
    }
)
#: A body or an object striking hard: the punch-in and a small shake. A knock, thud, crack or clatter is not one.
HIT_STEMS = frozenset(
    {
        "slam",
        "bang",
        "crash",
        "smash",
        "shatter",
        "punch",
        "kick",
        "slap",
        "smack",
        "whack",
        "thwack",
    }
)


def _forms(stems: frozenset[str]) -> re.Pattern[str]:
    """Every stem with its endings (as ``ambience._forms``): explode/explodes/exploded, slam/slammed."""

    parts = [
        f"{stem[:-1]}(?:e|es|ed|ing)"
        if stem.endswith("e")
        else f"{stem}(?:s|es|ed|ing|med|ming)?"
        for stem in sorted(stems)
    ]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


_EXPLOSION = _forms(BLAST_STEMS)
_HARD_HIT = _forms(HIT_STEMS)
#: "A blast of cold air", "music blasts": a blast that is wind, air, water or sound is not an explosion.
_NOT_AN_EXPLOSION = re.compile(
    r"\bblast(?:s|ing)?\s+of\s+(?:cold|hot|warm|icy|fresh)?\s*(?:air|wind|water|steam|music|noise|sound|horn|light)\b"
    r"|\b(?:music|radio|speakers?|stereo|horns?|siren|tv|television)\s+blast",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"\b(?:no|not|never|nor|without)\b|n't\b", re.IGNORECASE)
_CLAUSES = re.compile(r"[,;:.!?—–]|\s-\s|\bthen\b", re.IGNORECASE)
#: Words that make a cue several hits, not one moment (a superset of #767's ``sfx_motion`` list): no effect.
_SEVERAL_HITS = re.compile(
    r"\b(?:twice|thrice|repeatedly|again and again|flurry|volley|barrage|footsteps|several|multiple|many|"
    r"knocking|banging|hammering|pounding|clattering|slamming|explosions|gunshots|detonations|"
    r"(?:two|three|four|five|six|seven|eight|nine|ten|\d+|a few)(?:\s+\w+){0,2}?\s+"
    r"(?:times|hits|bangs|knocks|shots|blows|slams|thuds|cracks|punches|kicks|booms|blasts|strikes|taps))\b",
    re.IGNORECASE,
)


def names_several_hits(sound: str) -> bool:
    """Whether a cue names several hits ("three cane bangs", "knocks twice", "explosions"), not one moment.

    Parameters
    ----------
    sound
        The cue's words.

    Returns
    -------
    bool
        True when one punch-in could not stand for the cue.
    """

    return bool(_SEVERAL_HITS.search(sound))


def is_explosion(sound: str) -> bool:
    """Whether a cue names a true explosion (an explosion, a blast, a detonation, a boom), not denied.

    Parameters
    ----------
    sound
        The cue's words.

    Returns
    -------
    bool
        False for "no explosion", "a blast of cold air", "music blasts" and every other sound.
    """

    if _NOT_AN_EXPLOSION.search(sound):
        return False
    return _names(_EXPLOSION, sound)


def _names(words: re.Pattern[str], sound: str) -> bool:
    """Whether some clause names one of ``words`` without denying it ("no explosion")."""

    for clause in _CLAUSES.split(sound):
        match = words.search(clause)
        if match is not None and not _NEGATED.search(clause[: match.start()]):
            return True
    return False


#: At most this many punched-up moments in one take: one or two, never a light show.
MAX_MOMENTS_PER_TAKE = 2
#: A moment this close to a spoken line (either side) is mid-line: left alone.
SPEECH_GUARD_SECONDS = 0.15
#: The spike must be this close to where the effect was laid: the punch-in lands with its boom.
SOUND_TOLERANCE_SECONDS = 0.1

#: The blast's punch-in (an explosion, a gunshot, a boom): further in than a hit's. Never a white flash
#: (founder, 13 Oct 2026: a gunshot gets a zoom and a shake only). The server's number.
BLAST_PUNCH_ZOOM = 1.14
#: The blast's crop shake: peak offset (fraction of the frame), how long.
BLAST_SHAKE_AMPLITUDE = 0.015
BLAST_SHAKE_SECONDS = 0.5
#: The hard hit's punch-in: how far in, held this long on the hit, then eased back over this long.
PUNCH_ZOOM = 1.1
PUNCH_HOLD_SECONDS = 0.3
PUNCH_RELEASE_SECONDS = 0.5
#: The hard hit's small shake.
HIT_SHAKE_AMPLITUDE = 0.006
HIT_SHAKE_SECONDS = 0.25
#: The shake's decay time constant and its two frequencies (Hz, across and up).
SHAKE_DECAY_SECONDS = 0.18
SHAKE_HZ_X = 13.0
SHAKE_HZ_Y = 9.0
#: Where a punch-in aims (fraction of the height): faces sit high in a 9:16 frame.
PUNCH_ANCHOR_Y = 0.42

#: Every word the server reads as a hit (fictora-drama ``impact_sfx._IMPACT_STEMS``): the FIRST one in
#: a sound decides it, as on the server ("a door slams with a thud" is a slam).
_IMPACT_STEMS = (
    "crack", "crunch", "snap", "slam", "bang", "boom", "thud", "thump", "crash", "smash",
    "shatter", "clang", "clank", "clatter", "grind", "knock", "punch", "slap", "smack",
    "whack", "thwack", "stomp", "kick", "splinter", "gunshot", "explosion", "explode",
)  # fmt: skip
_IMPACT = re.compile(
    r"(" + "|".join(_IMPACT_STEMS) + r")(s|es|ed|d|ing|ped|ping|med|ming|ded|ding)?",
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"\b(?:no|not|never|nor|without|silent|silently)\b|n't\b", re.IGNORECASE
)
_CLAUSE_SPLIT = re.compile(r"[,;:.!?—–]|\s-\s|\bthen\b", re.IGNORECASE)
_DETERMINERS = frozenset(
    {
        "a",
        "an",
        "the",
        "his",
        "her",
        "its",
        "their",
        "my",
        "your",
        "our",
        "one",
        "that",
        "this",
    }
)
#: The exact colour round trip: without these flags swscale moves every pixel ~2 levels (yuv -> rgb -> yuv).
_EXACT_SCALE = "accurate_rnd+full_chroma_int"


def impact_stem(sound: str) -> str | None:
    """The first impact word a sound names, as its stem, or ``None`` (none, denied, or an adjective).

    Parameters
    ----------
    sound
        A cue's words.

    Returns
    -------
    str | None
        ``"slam"``, ``"explode"``...; ``None`` for "no explosion", "the cracked mug" or no hit.
    """

    for clause in _CLAUSE_SPLIT.split(sound):
        words = re.findall(r"[A-Za-z0-9'’-]+", clause)
        for index, word in enumerate(words):
            match = _IMPACT.fullmatch(word)
            if match is None:
                continue
            suffix = (match.group(2) or "").casefold()
            before = words[index - 1].casefold() if index else ""
            if suffix in {"ed", "d", "ped", "med", "ded"} and before in _DETERMINERS:
                continue
            if _NEGATION.search(" ".join(words[:index])):
                continue
            return match.group(1).casefold()
    return None


def impact_style(
    sound: str, kind: str, called: str | None = None
) -> ImpactStyle | None:
    """Whether a laid effect is a big impact beat.

    Parameters
    ----------
    sound
        The effect's words.
    kind
        ``event`` or ``sustained``.
    called
        The server's own call from the take facts (``sfx_cues[].impact``): it wins (a fight's strike,
        "Ren elbows the guard", names no impact word the lists below know).

    Returns
    -------
    ImpactStyle | None
        ``blast`` (a true explosion), ``hit`` (a hard hit, a bang) or ``None`` (several hits too).
    """

    if kind != "event" or is_opening_cue(sound) or names_several_hits(sound):
        return None
    if called == "blast" or called == "hit":
        return called
    # As the server (``impact_words.impact_weight``): a blast in the words wins over a hit.
    if is_explosion(sound):
        return "blast"
    if _names(_HARD_HIT, sound):
        return "hit"
    return None


@dataclass(frozen=True)
class Move:
    """The picture on one frame: zoom about ``(anchor_x, anchor_y)`` and a shift (fractions). Never a flash."""

    zoom: float = 1.0
    anchor_x: float = 0.5
    anchor_y: float = 0.5
    shift_x: float = 0.0
    shift_y: float = 0.0

    def matrix(self, width: int, height: int) -> np.ndarray:
        """The 2x3 affine (output pixel -> source pixel) for OpenCV ``warpAffine`` with ``WARP_INVERSE_MAP``.

        Parameters
        ----------
        width, height
            The frame size.

        Returns
        -------
        numpy.ndarray
            ``[[s, 0, tx], [0, s, ty]]``.
        """

        ax, ay = self.anchor_x * width, self.anchor_y * height
        s = 1.0 / self.zoom
        dx, dy = self.shift_x * width, self.shift_y * height
        return np.asarray(
            [[s, 0.0, ax * (1.0 - s) + dx], [0.0, s, ay * (1.0 - s) + dy]],
            dtype=np.float64,
        )


@dataclass(frozen=True)
class ImpactMoment:
    """One punched-up impact: the measured frame it lands on, when its effect is over, its style."""

    time: float
    until: float
    style: ImpactStyle
    label: str
    #: ``planned`` from the take's impact cues; ``editor`` for an ``--impact-at`` hit (the server's ``source``).
    source: str = "planned"
    #: Seconds the picture holds on the hit frame (the server's ``freeze_s``: a fight's strike freeze);
    #: 0 for none. The held frames replace the frames after the hit: same length, same sound.
    freeze: float = 0.0

    def one_line(self) -> str:
        """``a gas tank explodes: big punch-in + shake at 2.04s`` (``+ 0.12s freeze`` when it holds)."""

        what = "big punch-in + shake" if self.style == "blast" else "punch-in + shake"
        held = f" + {self.freeze:.2f}s freeze" if self.freeze > 0 else ""
        return f"{self.label}: {what}{held} at {self.time:.2f}s"


def effect_seconds(style: ImpactStyle) -> float:
    """How long a moment's effect lasts from its impact frame.

    Parameters
    ----------
    style
        The moment's style.

    Returns
    -------
    float
        Seconds.
    """

    shake = BLAST_SHAKE_SECONDS if style == "blast" else HIT_SHAKE_SECONDS
    return max(PUNCH_HOLD_SECONDS + PUNCH_RELEASE_SECONDS, shake)


def _shake(dt: float, amplitude: float, seconds: float) -> tuple[float, float, float]:
    if dt >= seconds:
        return 0.0, 0.0, 0.0
    envelope = amplitude * math.exp(-dt / SHAKE_DECAY_SECONDS)
    return (
        envelope * math.sin(2 * math.pi * SHAKE_HZ_X * dt),
        envelope * math.cos(2 * math.pi * SHAKE_HZ_Y * dt),
        envelope,
    )


def moment_move(moment: ImpactMoment, seconds: float) -> Move | None:
    """The picture's move at ``seconds`` from one moment, or ``None`` outside it.

    Parameters
    ----------
    moment
        The impact.
    seconds
        Time into the take.

    Returns
    -------
    Move | None
        Zoom and shift, never a flash; the zoom always covers the shake (no frame edge shows).
    """

    if not moment.time <= seconds < moment.until:
        return None
    dt = seconds - moment.time
    blast = moment.style == "blast"
    peak = BLAST_PUNCH_ZOOM if blast else PUNCH_ZOOM
    if dt < PUNCH_HOLD_SECONDS:
        zoom = peak
    else:
        p = min(1.0, (dt - PUNCH_HOLD_SECONDS) / PUNCH_RELEASE_SECONDS)
        zoom = peak + (1.0 - peak) * (p * p * (3.0 - 2.0 * p))
    if blast:
        sx, sy, envelope = _shake(dt, BLAST_SHAKE_AMPLITUDE, BLAST_SHAKE_SECONDS)
    else:
        sx, sy, envelope = _shake(dt, HIT_SHAKE_AMPLITUDE, HIT_SHAKE_SECONDS)
    # A blast fills the frame (centre); a hit aims where faces sit.
    return Move(
        zoom=max(zoom, 1.0 + 2.5 * envelope),
        anchor_y=0.5 if blast else PUNCH_ANCHOR_Y,
        shift_x=sx,
        shift_y=sy,
    )


@dataclass(frozen=True)
class ImpactPlan:
    """The take's punched-up moments and why other impacts were left alone."""

    moments: tuple[ImpactMoment, ...] = ()
    skipped: tuple[str, ...] = ()

    def move_at(self, seconds: float) -> Move | None:
        """The move at ``seconds``, or ``None`` (the frame is left exactly as it is).

        Parameters
        ----------
        seconds
            Time into the take.

        Returns
        -------
        Move | None
            The move.
        """

        for moment in self.moments:
            moved = moment_move(moment, seconds)
            if moved is not None:
                return moved
        return None

    def held_from(self, index: int, fps: float) -> int:
        """The frame shown at ``index``: the hit frame inside a freeze (the server's plan), else ``index``.

        Parameters
        ----------
        index
            Frame number, 0-based.
        fps
            Frames per second.

        Returns
        -------
        int
            The source frame, never later than ``index``.
        """

        for moment in self.moments:
            if moment.freeze <= 0:
                continue
            hit = round(moment.time * fps)
            if hit < index <= hit + max(1, round(moment.freeze * fps)):
                return hit
        return index

    def one_line(self) -> str:
        """What was punched up and what was left alone."""

        parts = [moment.one_line() for moment in self.moments]
        if self.skipped:
            parts.append("left alone: " + "; ".join(self.skipped))
        return "; ".join(parts) or "no big impact on this take"


def plan_impacts(
    cues: Sequence[Any],
    windows: Mapping[int, tuple[float, float]],
    trace: Trace,
    *,
    cuts: Sequence[float] = (),
    speech: Sequence[tuple[float, float]] = (),
) -> ImpactPlan:
    """Choose the take's punched-up moments on its measured motion.

    Parameters
    ----------
    cues
        The laid effects (:class:`creation.post.sfx.SfxCue`: ``sound``, ``kind``, ``shot_index``,
        ``start``), on their filmed shots.
    windows
        ``shot_index -> (start, end)`` as filmed.
    trace
        :func:`creation.post.sfx_motion.measure_motion`.
    cuts
        Cuts measured on the take.
    speech
        Spoken-line windows.

    Returns
    -------
    ImpactPlan
        At most :data:`MAX_MOMENTS_PER_TAKE` moments in time order, none overlapping, none mid-line,
        each on a measured spike where its effect is heard; every impact left alone is named.
    """

    typical = median(v for _t, v in trace) if trace else 0.0
    candidates: list[tuple[int, float, ImpactMoment]] = []
    skipped: list[str] = []
    for cue in cues:
        style = impact_style(cue.sound, cue.kind, getattr(cue, "impact", None))
        if style is None:
            continue
        shot = windows.get(cue.shot_index)
        if shot is None:
            skipped.append(f"{cue.sound} (its shot was not filmed)")
            continue
        peak = action_moment(trace, around=cue.start, shot=shot, cuts=tuple(cuts))
        if peak.time is None:
            skipped.append(f"{cue.sound} ({peak.reason})")
            continue
        if abs(peak.time - cue.start) > SOUND_TOLERANCE_SECONDS:
            skipped.append(
                f"{cue.sound} (the action at {peak.time:.2f}s is not where the effect plays, {cue.start:.2f}s)"
            )
            continue
        if any(
            a - SPEECH_GUARD_SECONDS <= peak.time <= b + SPEECH_GUARD_SECONDS
            for a, b in speech
        ):
            skipped.append(f"{cue.sound} (mid-line at {peak.time:.2f}s)")
            continue
        strength = (
            max((v for t, v in trace if abs(t - peak.time) < 1e-3), default=typical)
            - typical
        )
        until = min(peak.time + effect_seconds(style), shot[1])
        candidates.append(
            (
                0 if style == "blast" else 1,
                -strength,
                ImpactMoment(peak.time, round(until, 3), style, cue.sound),
            )
        )
    chosen: list[ImpactMoment] = []
    for _rank, _strength, moment in sorted(
        candidates, key=lambda c: (c[0], c[1], c[2].time)
    ):
        if any(abs(moment.time - other.time) < 1e-3 for other in chosen):
            continue  # the same hit laid twice
        if len(chosen) >= MAX_MOMENTS_PER_TAKE:
            skipped.append(
                f"{moment.label} (already {MAX_MOMENTS_PER_TAKE} on this take)"
            )
            continue
        if any(
            moment.time < other.until and other.time < moment.until for other in chosen
        ):
            skipped.append(f"{moment.label} (overlaps a stronger moment)")
            continue
        chosen.append(moment)
    return ImpactPlan(tuple(sorted(chosen, key=lambda m: m.time)), tuple(skipped))


# --- the server's plan ------------------------------------------------------------------------------

#: The server route that decides a take's punch-ins from this kit's measurements (fictora-drama).
IMPACT_PLAN_PATH = "/v1/jobs/{job_id}/impact-plan"
#: At most this many ``--impact-at`` hits per take: the server refuses more (422), so the kit sends the first ones.
MAX_EDITOR_HITS = 8
#: A punched-in moment with no laid effect this close to it has no hit sound under it.
SOUND_MATCH_SECONDS = 1.0
_IMPACT_AT = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?::\s*(hit|blast)\s*)?$", re.IGNORECASE
)


@dataclass(frozen=True)
class ImpactHit:
    """A frame the editor says is a hit (``finish --impact-at 2.40`` or ``2.40:blast``)."""

    at: float
    style: ImpactStyle = "hit"


def parse_impact_at(raw: str) -> tuple[ImpactHit, ...]:
    """``"2.4, 5.1:blast"`` -> two hits; seconds into the take as filmed.

    Parameters
    ----------
    raw
        One ``--impact-at`` value: comma-separated ``SECONDS[:hit|blast]``.

    Returns
    -------
    tuple[ImpactHit, ...]
        The hits, in the order written.

    Raises
    ------
    ValueError
        For a part that is not ``SECONDS`` or ``SECONDS:hit|blast``.
    """

    hits: list[ImpactHit] = []
    for part in raw.split(","):
        if not part.strip():
            continue
        match = _IMPACT_AT.match(part)
        if match is None:
            raise ValueError(
                f"--impact-at {part.strip()!r}: write SECONDS or SECONDS:blast (e.g. 2.40 or 5.10:blast)"
            )
        style: ImpactStyle = (
            "blast" if (match.group(2) or "").casefold() == "blast" else "hit"
        )
        hits.append(ImpactHit(float(match.group(1)), style))
    return tuple(hits)


class ImpactPlanUnavailable(RuntimeError):
    """The server did not answer with a plan (no take job, unreachable, or an older server)."""


def plan_from_server_answer(body: Any) -> ImpactPlan:
    """The kit's :class:`ImpactPlan` from a ``POST /v1/jobs/{id}/impact-plan`` answer.

    Parameters
    ----------
    body
        The JSON answer.

    Returns
    -------
    ImpactPlan
        The server's moments, as it chose them, and what it left alone.

    Raises
    ------
    ImpactPlanUnavailable
        When the answer is not an impact plan.
    """

    if not isinstance(body, dict) or not isinstance(body.get("moments"), list):
        raise ImpactPlanUnavailable("the server's answer is not an impact plan")
    moments: list[ImpactMoment] = []
    for item in body["moments"]:
        try:
            style = item["style"]
            time, until = float(item["time_s"]), float(item["until_s"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ImpactPlanUnavailable(
                f"the server's impact plan has a malformed moment ({exc!r})"
            ) from exc
        if style not in ("blast", "hit") or until <= time:
            raise ImpactPlanUnavailable(
                f"the server's impact plan has a malformed moment ({item!r})"[:200]
            )
        label = str(item.get("label") or "impact")
        source = "editor" if item.get("source") == "editor" else "planned"
        freeze = item.get("freeze_s") or 0.0
        if (
            isinstance(freeze, bool)
            or not isinstance(freeze, (int, float))
            or not 0 <= freeze <= 0.5
        ):
            raise ImpactPlanUnavailable(
                f"the server's impact plan has a malformed freeze ({freeze!r})"
            )
        moments.append(ImpactMoment(time, until, style, label, source, float(freeze)))
    skipped = tuple(str(reason) for reason in body.get("skipped") or [])
    return ImpactPlan(tuple(sorted(moments, key=lambda m: m.time)), skipped)


def impact_plan_request(
    trace: Trace,
    *,
    windows: Mapping[int, tuple[float, float]] | None,
    cuts: Sequence[float] = (),
    speech: Sequence[tuple[float, float]] | None = None,
    hits: Sequence[ImpactHit] = (),
    take_seconds: float | None = None,
) -> dict[str, Any]:
    """The body of ``POST /v1/jobs/{id}/impact-plan``: what this kit measured on its copy of the take.

    Parameters
    ----------
    trace
        :func:`creation.post.sfx_motion.measure_motion` on the take.
    windows
        ``shot_index -> (start, end)`` as filmed; ``None`` lets the server keep the planned shots.
    cuts
        Cuts measured on the take.
    speech
        Spoken windows on the filmed timeline (a locked line, a laid voice); ``None`` lets the server use
        the take's own.
    hits
        ``--impact-at`` hits.
    take_seconds
        The take's length.

    Returns
    -------
    dict[str, Any]
        JSON-ready body.
    """

    return {
        "motion": [[round(t, 4), round(v, 4)] for t, v in trace],
        "shots": [
            {"shot_index": index, "start_seconds": start, "end_seconds": end}
            for index, (start, end) in sorted((windows or {}).items())
            if end > start
        ],
        "cuts": [round(c, 4) for c in cuts],
        "speech": None if speech is None else [[a, b] for a, b in speech if b > a],
        "hits": [{"at_s": hit.at, "style": hit.style} for hit in hits],
        "take_seconds": take_seconds,
    }


@dataclass(frozen=True)
class ImpactResult:
    """What :func:`apply_impacts` wrote."""

    output: Path
    plan: ImpactPlan

    def one_line(self) -> str:
        """Report line."""

        return (
            f"{self.plan.one_line()} -> {self.output.name}; length and sound unchanged"
        )


def _probe(path: Path) -> tuple[int, int, float, bool]:
    import json

    run = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,color_space", "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    streams = json.loads(run.stdout or "{}").get("streams") or []
    if run.returncode != 0 or not streams:
        raise MediaToolError(f"no picture in {path.name}")
    stream = streams[0]
    num, _, den = str(stream.get("r_frame_rate") or "24/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 24.0
    return (
        int(stream["width"]),
        int(stream["height"]),
        fps if fps > 0 else 24.0,
        str(stream.get("color_space") or "") == "bt709",
    )


def apply_impacts(take: Path, plan: ImpactPlan, out: Path) -> ImpactResult:
    """Render the moments onto the take: same size, length and frames, the sound copied untouched.

    Parameters
    ----------
    take
        Video.
    plan
        :func:`plan_impacts` (with at least one moment).
    out
        New file.

    Returns
    -------
    ImpactResult
        The file and the plan.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    MediaToolError
        When ffmpeg fails.
    """

    import cv2

    if out.exists():
        raise FileExistsError(f"{out} exists; impact never overwrites")
    width, height, fps, bt709 = _probe(take)
    ffmpeg = ffmpeg_bin()
    picture = out.with_name(f"{out.stem}.picture.mp4")
    matrix = ":out_color_matrix=bt709" if bt709 else ""
    tags = (
        ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
        if bt709
        else []
    )
    reader = subprocess.Popen(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(take), "-map", "0:v:0",
         "-vf", f"fps={fps:.6f},scale=flags={_EXACT_SCALE}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    writer = subprocess.Popen(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{width}x{height}", "-r", f"{fps:g}", "-i", "-",
         "-vf", f"scale=flags={_EXACT_SCALE}{matrix},format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", "16", *tags, "-an", "-y", str(picture)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )  # fmt: skip
    assert reader.stdout is not None and writer.stdin is not None
    size = width * height * 3
    index = 0
    # The server's freeze: held frames show the hit frame, with the hit frame's own move; nothing is added.
    anchor: np.ndarray | None = None
    try:
        while True:
            raw = reader.stdout.read(size)
            if len(raw) < size:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            source = plan.held_from(index, fps)
            if source < index and anchor is not None:
                frame = anchor
            else:
                anchor = frame
            move = plan.move_at(source / fps)
            if move is not None:
                frame = cv2.warpAffine(
                    frame, move.matrix(width, height), (width, height),
                    flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT,
                )  # fmt: skip
            writer.stdin.write(np.ascontiguousarray(frame).tobytes())
            index += 1
        writer.stdin.close()
        reader.wait(timeout=600)
        writer.wait(timeout=600)
    except (subprocess.TimeoutExpired, BrokenPipeError) as exc:
        reader.kill()
        writer.kill()
        raise MediaToolError(f"the impact render failed: {exc}") from exc
    if reader.returncode != 0 or writer.returncode != 0 or index == 0:
        err = writer.stderr.read().decode("utf-8", "replace") if writer.stderr else ""
        picture.unlink(missing_ok=True)
        raise MediaToolError(f"the impact render failed: {err[-300:]}")
    run = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-i", str(picture), "-i", str(take), "-map", "0:v:0", "-map", "1:a?",
         "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", str(out)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    picture.unlink(missing_ok=True)
    if run.returncode != 0:
        raise MediaToolError(f"the impact mux failed: {(run.stderr or '')[-300:]}")
    return ImpactResult(out, plan)


__all__ = [
    "IMPACT_PLAN_PATH",
    "MAX_EDITOR_HITS",
    "SOUND_MATCH_SECONDS",
    "ImpactHit",
    "ImpactPlanUnavailable",
    "impact_plan_request",
    "parse_impact_at",
    "plan_from_server_answer",
    "BLAST_PUNCH_ZOOM",
    "BLAST_STEMS",
    "HIT_STEMS",
    "MAX_MOMENTS_PER_TAKE",
    "PUNCH_ZOOM",
    "SOUND_TOLERANCE_SECONDS",
    "ImpactMoment",
    "ImpactPlan",
    "ImpactResult",
    "Move",
    "apply_impacts",
    "effect_seconds",
    "impact_stem",
    "impact_style",
    "is_explosion",
    "moment_move",
    "names_several_hits",
    "plan_impacts",
]
