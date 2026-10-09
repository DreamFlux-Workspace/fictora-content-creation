"""Hand-placed sound on one take: mute stray speech, lay a dry voice line, lay a hand cue.

``finish --mute A-B``, ``--voice PATH@SECONDS[@DB]`` and ``--cue PATH@SECONDS[@DB]``
(each repeatable) land here. All times are seconds on the take **as filmed**:
``deboard`` replaces the board frames at the head without cutting them, so the
timeline never moves and nothing here is shifted.

- **Mute** silences the take's own audio inside ``A-B``, with 30 ms fades just
  outside the window (no clicks). It runs before the SFX step, so it silences
  the model's stray speech and never an effect laid later.
- **Voice** lays a dry line (``voice-line``) into the take's own audio at its
  start, levelled to -18 LUFS unless ``@DB`` gives its gain. Because it is part
  of the take's audio, the mix ducks the bed under it like any spoken line.
  A line that would run past the take is refused, never cut mid-word.
- **Cue** lays a hand cue (``cue``) at the house cue level (-8 dB; ``@DB`` sets
  it), clamped to end 0.15 s before the take does, ducked 10 dB while someone
  speaks like the take's own effects. A silent cue file is refused before
  anything runs, and the laid cue layer is measured: a cue that comes out
  silent there fails the step loudly.

No loudness normalisation touches the take, and no limiter is added here: the
mix gives the take one gain and ends on the one limiter.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from creation.post.media import (
    measure_loudness,
    measure_rms_windows,
    media_duration,
    probe_video,
    run_ffmpeg,
)
from creation.post.sfx import (
    SFX_GAIN_DB,
    SFX_SPEECH_DUCK_DB,
    SILENCE_DB,
    BedReference,
    bed_levelled_gain,
    rendered_peak_db,
)

#: Fades just outside a mute window (seconds), so the edges do not click.
MUTE_FADE_SECONDS = 0.03
#: A dry voice line is levelled to this before it is laid in (unless ``@DB`` is given).
VOICE_TARGET_LUFS = -18.0
#: With the harness's music in the take, the take's own audio drops this much under a hand-laid line
#: (L-20261004-5: the mix ducks only a bed the kit lays, so nothing else made room for the line).
TAKE_UNDER_VOICE_DUCK_DB = 10.0
#: The take's own audio ramps down and back up over this, either side of a hand-laid line.
TAKE_UNDER_VOICE_RAMP_SECONDS = 0.25
#: A hand cue ends at least this long before its take does.
CUE_TAKE_END_MARGIN_SECONDS = 0.15
#: The shortest stretch of cue worth laying once it is clamped.
CUE_MIN_SECONDS = 0.25
#: A laid cue whose window never rises above this in the cue layer is silent.
CUE_SILENT_DB = -70.0
#: Window used to verify the laid cue layer.
VERIFY_WINDOW_SECONDS = 0.1
#: ``@DB`` range for a voice line or a cue: outside it is a typo, not a level.
GAIN_RANGE_DB = (-40.0, 30.0)
#: A voice line may run this far past the take end (encoder padding) before it is refused.
VOICE_END_SLACK_SECONDS = 0.05


@dataclass(frozen=True)
class Placed:
    """One hand-made file placed on a take (a voice line or a cue).

    Parameters
    ----------
    path
        The audio file.
    start
        Seconds into the take as filmed.
    gain_db
        Explicit gain; ``None`` uses the default (voice: levelled; cue: -8 dB).
    """

    path: Path
    start: float
    gain_db: float | None = None

    def one_line(self) -> str:
        """``name @4.20s -12 dB`` (``auto`` gain when none was given)."""

        gain = "auto" if self.gain_db is None else f"{self.gain_db:+.0f} dB"
        return f"{self.path.name} @{self.start:.2f}s {gain}"


def parse_range(spec: str) -> tuple[float, float]:
    """Read one ``--mute`` value: ``A-B`` seconds on the take as filmed.

    Parameters
    ----------
    spec
        ``6.9-8.3``.

    Returns
    -------
    tuple[float, float]
        Start and end.

    Raises
    ------
    ValueError
        When it does not parse, or B is not after A.
    """

    start, sep, end = spec.strip().partition("-")
    try:
        lo, hi = float(start), float(end)
    except ValueError:
        raise ValueError(
            f"--mute wants A-B seconds, e.g. 6.9-8.3; got {spec!r}"
        ) from None
    if not sep or hi <= lo or lo < 0:
        raise ValueError(
            f"--mute wants A-B seconds with B after A, e.g. 6.9-8.3; got {spec!r}"
        )
    return lo, hi


def parse_caption_label(spec: str) -> tuple[str, float, float]:
    """Read one ``--caption-label`` value: ``TEXT@A-B`` seconds on the take as filmed.

    A caption with no spoken line under it: a one-word line the take never says
    clearly ("Blinking!"), shown as text only.

    Parameters
    ----------
    spec
        ``Blinking!@6.9-7.6``.

    Returns
    -------
    tuple[str, float, float]
        Text, start and end.

    Raises
    ------
    ValueError
        When there is no text, or the window does not parse.
    """

    text, sep, window = spec.rpartition("@")
    if not sep or not text.strip():
        raise ValueError(
            f"--caption-label wants TEXT@A-B seconds, e.g. 'Blinking!@6.9-7.6'; got {spec!r}"
        )
    try:
        start, end = parse_range(window)
    except ValueError:
        raise ValueError(
            f"--caption-label wants TEXT@A-B seconds with B after A, e.g. 'Blinking!@6.9-7.6'; got {spec!r}"
        ) from None
    return text.strip(), start, end


def parse_placed(spec: str, *, flag: str) -> Placed:
    """Read one ``--voice`` or ``--cue`` value: ``PATH@SECONDS[@DB]``.

    Parameters
    ----------
    spec
        ``epNN/voices/voice-ep01-aya-v1.mp3@5.2`` or ``epNN/sfx/cue-sting-v1.mp3@4.2@-12``.
    flag
        ``--voice`` or ``--cue`` (for the message).

    Returns
    -------
    Placed
        The file, its start and its gain.

    Raises
    ------
    ValueError
        When it does not parse or the gain is outside -40..+30 dB.
    """

    path, *numbers = spec.split("@")
    if not numbers or not path.strip() or len(numbers) > 2:
        raise ValueError(
            f"{flag} wants PATH@SECONDS[@DB], e.g. file.mp3@4.2@-12; got {spec!r}"
        )
    try:
        values = [float(value) for value in numbers]
    except ValueError:
        raise ValueError(
            f"{flag} wants PATH@SECONDS[@DB] with numbers after the @; got {spec!r}"
        ) from None
    gain = values[1] if len(values) > 1 else None
    if gain is not None and not GAIN_RANGE_DB[0] <= gain <= GAIN_RANGE_DB[1]:
        raise ValueError(
            f"{flag} gain must be {GAIN_RANGE_DB[0]:.0f} to {GAIN_RANGE_DB[1]:+.0f} dB; got {spec!r}"
        )
    return Placed(path=Path(path).expanduser(), start=values[0], gain_db=gain)


@dataclass(frozen=True)
class HandPlan:
    """What the operator asked ``finish`` to place by hand, checked against the take.

    Parameters
    ----------
    mutes
        Windows to silence in the take's own audio (clamped to the take).
    voices
        Dry voice lines, each with its length.
    cues
        Hand cues, each with the length it is laid for (clamped to the take).
    """

    mutes: tuple[tuple[float, float], ...] = ()
    voices: tuple[tuple[Placed, float], ...] = ()
    cues: tuple[tuple[Placed, float], ...] = ()

    @property
    def voice_windows(self) -> tuple[tuple[float, float], ...]:
        """Where the hand voice lines speak."""

        return tuple(
            (line.start, line.start + seconds) for line, seconds in self.voices
        )


def check_hand_plan(
    take_seconds: float,
    *,
    mutes: tuple[tuple[float, float], ...] = (),
    voices: tuple[Placed, ...] = (),
    cues: tuple[Placed, ...] = (),
) -> HandPlan:
    """Check every hand layer against the take before anything runs; fail loud on any that cannot go on.

    Parameters
    ----------
    take_seconds
        Length of the take (deboard keeps it; nothing is shifted).
    mutes, voices, cues
        Parsed ``--mute``, ``--voice`` and ``--cue`` values.

    Returns
    -------
    HandPlan
        The checked plan (mutes and cues clamped to the take).

    Raises
    ------
    FileNotFoundError
        When a voice or cue file is missing.
    ValueError
        A mute, voice or cue that starts outside the take, a voice line that
        runs past it, a cue with no room before the take ends, or a silent file.
    """

    checked_mutes: list[tuple[float, float]] = []
    for start, end in mutes:
        if start >= take_seconds:
            raise ValueError(
                f"--mute {start:g}-{end:g} starts past the end of the {take_seconds:.2f}s take"
            )
        checked_mutes.append((start, min(end, take_seconds)))
    checked_voices: list[tuple[Placed, float]] = []
    for line in voices:
        seconds = _audible_seconds(line, flag="--voice")
        if not 0 <= line.start < take_seconds:
            raise ValueError(
                f"--voice {line.one_line()} starts outside the {take_seconds:.2f}s take"
            )
        if line.start + seconds > take_seconds + VOICE_END_SLACK_SECONDS:
            raise ValueError(
                f"--voice {line.one_line()} ({seconds:.2f}s) runs past the end of the {take_seconds:.2f}s take; "
                f"start it by {take_seconds - seconds:.2f}s or use a shorter line"
            )
        checked_voices.append((line, seconds))
    checked_cues: list[tuple[Placed, float]] = []
    for cue in cues:
        seconds = _audible_seconds(cue, flag="--cue")
        checked_cues.append(
            (cue, clamp_cue(cue, cue_seconds=seconds, take_seconds=take_seconds))
        )
    return HandPlan(tuple(checked_mutes), tuple(checked_voices), tuple(checked_cues))


def _audible_seconds(item: Placed, *, flag: str) -> float:
    if not item.path.is_file():
        raise FileNotFoundError(f"{flag} file not found: {item.path}")
    levels = measure_rms_windows(item.path, window_seconds=0.5)
    if not levels or max(levels) <= SILENCE_DB:
        raise ValueError(
            f"{flag} {item.path.name} is silent (no half second above {SILENCE_DB:.0f} dB RMS)"
        )
    return media_duration(item.path)


def clamp_cue(cue: Placed, *, cue_seconds: float, take_seconds: float) -> float:
    """How much of the cue is laid: all of it, or up to 0.15 s before the take ends.

    Parameters
    ----------
    cue
        Placed cue.
    cue_seconds
        Length of the cue file.
    take_seconds
        Length of the take.

    Returns
    -------
    float
        Seconds of cue to lay.

    Raises
    ------
    ValueError
        When the cue starts outside the take or has under 0.25 s of room before it ends.
    """

    if not 0 <= cue.start < take_seconds:
        raise ValueError(
            f"--cue {cue.one_line()} starts outside the {take_seconds:.2f}s take"
        )
    room = take_seconds - CUE_TAKE_END_MARGIN_SECONDS - cue.start
    if room < CUE_MIN_SECONDS:
        raise ValueError(
            f"--cue {cue.one_line()} has no room before the {take_seconds:.2f}s take ends"
        )
    return round(min(cue_seconds, room), 3)


def mute_expression(
    windows: tuple[tuple[float, float], ...], fade: float = MUTE_FADE_SECONDS
) -> str:
    """An ffmpeg ``volume`` filter that is 0 inside each window, 1 outside, with ``fade`` ramps just outside."""

    if not windows:
        return "anull"
    terms = []
    for start, end in windows:
        fall = f"min(1\\,max(0\\,({start:.3f}-t)/{fade:.3f}))"
        rise = f"min(1\\,max(0\\,(t-{end:.3f})/{fade:.3f}))"
        terms.append(f"max({fall}\\,{rise})")
    return f"volume='{'*'.join(terms)}':eval=frame"


def duck_expression(
    windows: tuple[tuple[float, float], ...],
    depth_db: float,
    ramp: float = TAKE_UNDER_VOICE_RAMP_SECONDS,
) -> str:
    """An ffmpeg ``volume`` filter ``-depth_db`` inside each window, 1 outside, ramped over ``ramp`` either side."""

    if not windows:
        return "anull"
    floor = 10 ** (-abs(depth_db) / 20)
    terms = []
    for start, end in windows:
        rise = f"min(1\\,max(0\\,(t-({start - ramp:.3f}))/{ramp:.3f}))"
        fall = f"min(1\\,max(0\\,({end + ramp:.3f}-t)/{ramp:.3f}))"
        terms.append(f"(1-{1 - floor:.4f}*{rise}*{fall})")
    gain = terms[0]
    for term in terms[1:]:
        # The deepest duck wins where lines overlap (never a double duck).
        gain = f"min({gain}\\,{term})"
    return f"volume='{gain}':eval=frame"


def lay_voice(
    take: Path, out: Path, plan: HandPlan, *, duck_take_db: float | None = None
) -> Path:
    """Mute ``plan.mutes`` in the take's own audio and lay ``plan.voices`` in (picture copied).

    Parameters
    ----------
    take
        The take (after deboard).
    out
        New file; must not exist.
    plan
        Checked hand plan.
    duck_take_db
        Drop the take's own audio this many dB under each laid line (ramped over
        :data:`TAKE_UNDER_VOICE_RAMP_SECONDS`); ``None`` leaves it as filmed.
        ``finish`` sets it on a new desk whose take carries the harness's music.

    Returns
    -------
    Path
        ``out``.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    # 1 ms audio frames so the mute gain moves sample-accurately enough that the window itself is silent.
    graph = [
        f"[0:a]aresample=48000,asetnsamples=n=48:p=0,{mute_expression(plan.mutes)}"
        + (
            f",{duck_expression(plan.voice_windows, duck_take_db)}"
            if duck_take_db is not None and plan.voices
            else ""
        )
        + "[take]"
    ]
    labels = ["[take]"]
    inputs: list[str] = ["-i", str(take)]
    for number, (line, _seconds) in enumerate(plan.voices, start=1):
        inputs += ["-i", str(line.path)]
        gain = line.gain_db if line.gain_db is not None else voice_gain(line.path)
        delay = round(line.start * 1000)
        graph.append(
            f"[{number}:a]aresample=48000,highpass=f=90,lowpass=f=8500,volume={gain:+.1f}dB,"
            f"adelay={delay}|{delay}[v{number}]"
        )
        labels.append(f"[v{number}]")
    graph.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[a]"
    )
    run_ffmpeg(
        [*inputs, "-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
    )  # fmt: skip
    return out


def voice_gain(path: Path) -> float:
    """Gain that brings a dry line to -18 LUFS (0 when it cannot be measured)."""

    lufs = measure_loudness(path)
    return round(VOICE_TARGET_LUFS - lufs, 1) if math.isfinite(lufs) else 0.0


def _cue_chain(
    position: int, cue: Placed, seconds: float, speech: tuple[tuple[float, float], ...]
) -> str:
    gain = SFX_GAIN_DB if cue.gain_db is None else cue.gain_db
    fade_out = min(0.3, seconds / 3)
    delay = round(cue.start * 1000)
    end = cue.start + seconds
    ducks = "".join(
        f",volume=enable='between(t,{max(a, cue.start):.3f},{min(b, end):.3f})':volume={SFX_SPEECH_DUCK_DB:+.1f}dB"
        for a, b in speech
        if a < end and b > cue.start
    )
    # Trim and fade before the delay; nothing after it but levels (a trim after adelay once silenced every cue).
    return (
        f"[{position}:a]aresample=48000,atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS,volume={gain:+.1f}dB,"
        f"afade=t=in:st=0:d=0.02,afade=t=out:st={seconds - fade_out:.3f}:d={fade_out:.3f},"
        f"adelay={delay}|{delay}{ducks}[c{position}]"
    )


def silent_cues(layer: Path, plan: HandPlan) -> list[str]:
    """Each laid cue whose window never rises above -70 dB in the cue-only layer."""

    levels = measure_rms_windows(layer, window_seconds=VERIFY_WINDOW_SECONDS)
    silent = []
    for cue, seconds in plan.cues:
        lo = int(math.floor(cue.start / VERIFY_WINDOW_SECONDS))
        hi = max(lo + 1, int(math.ceil((cue.start + seconds) / VERIFY_WINDOW_SECONDS)))
        span = levels[lo:hi]
        if not span or max(span) < CUE_SILENT_DB:
            silent.append(f"{cue.one_line()} came out SILENT in the cue layer")
    return silent


@dataclass(frozen=True)
class _CueSpan:
    """What :func:`creation.post.sfx.bed_levelled_gain` reads of a cue: where, how long, at what gain."""

    start: float
    seconds: float
    gain_db: float


def level_hand_cues(
    plan: HandPlan,
    bed: BedReference | None,
    *,
    measure: Callable[[Path], tuple[float, ...]] | None = None,
) -> tuple[HandPlan, tuple[str, ...]]:
    """Each hand cue (``--cue``) levelled against the music bed it plays under, like the planned effects.

    The same rule as the sfx step (:func:`creation.post.sfx.bed_levelled_gain`,
    kit #190): a cue whose loudest window would sit more than 6 dB under the bed
    where it plays is raised to that, at most +18 dB, never past +10 dB gain and
    never over the take's loudest spoken window. A cue given a lower ``@DB`` than
    the default stays that much further under; a cue already heard is never
    lowered. Only the level changes: same file, same start, same length.

    Parameters
    ----------
    plan
        The checked hand plan.
    bed
        The measured bed (``None``: no bed to sit against, every cue keeps its level).
    measure
        RMS per 0.5 s window of a cue file (default :func:`creation.post.media.measure_rms_windows`).

    Returns
    -------
    tuple[HandPlan, tuple[str, ...]]
        The plan with each raised cue's ``gain_db`` set, and one ``name +N dB (why)`` per raised cue.
    """

    if bed is None or not plan.cues:
        return plan, ()
    meter = measure or (
        lambda path: measure_rms_windows(path, window_seconds=bed.window_seconds)
    )
    cues: list[tuple[Placed, float]] = []
    notes: list[str] = []
    for cue, seconds in plan.cues:
        gain = SFX_GAIN_DB if cue.gain_db is None else cue.gain_db
        peak = rendered_peak_db(tuple(meter(cue.path)), seconds, bed.window_seconds)
        raised, why = bed_levelled_gain(_CueSpan(cue.start, seconds, gain), peak, bed)  # type: ignore[arg-type]
        if why:
            notes.append(f"{cue.path.name} @{cue.start:.2f}s {why}")
            cue = replace(cue, gain_db=raised)
        cues.append((cue, seconds))
    return replace(plan, cues=tuple(cues)), tuple(notes)


def lay_cues(
    take: Path,
    out: Path,
    plan: HandPlan,
    *,
    speech: tuple[tuple[float, float], ...] = (),
) -> Path:
    """Lay ``plan.cues`` under the take's own audio into ``out``, after checking the cue layer is not silent.

    Parameters
    ----------
    take
        The take (after SFX, when that ran).
    out
        New file; must not exist.
    plan
        Checked hand plan.
    speech
        Speaking windows: each cue drops a further 10 dB inside them.

    Returns
    -------
    Path
        ``out``.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    RuntimeError
        When a laid cue comes out silent (nothing is written).
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    total = probe_video(take).duration_seconds
    inputs: list[str] = []
    chains: list[str] = []
    for position, (cue, seconds) in enumerate(plan.cues):
        inputs += ["-i", str(cue.path)]
        chains.append(_cue_chain(position, cue, seconds, speech))
    labels = "".join(f"[c{position}]" for position in range(len(plan.cues)))
    mixed = (
        f"{labels}amix=inputs={len(plan.cues)}:duration=longest:normalize=0,"
        if len(plan.cues) > 1
        else f"{labels}"
    )
    with tempfile.TemporaryDirectory() as scratch:
        layer = Path(scratch) / "cues.wav"
        run_ffmpeg(
            [*inputs, "-filter_complex", ";".join([*chains, f"{mixed}apad=whole_dur={total:.3f}[cues]"]),
             "-map", "[cues]", "-t", f"{total:.3f}", "-c:a", "pcm_s16le", str(layer)]
        )  # fmt: skip
        silent = silent_cues(layer, plan)
        if silent:
            raise RuntimeError("; ".join(silent) + ": check the cue file and its level")
        run_ffmpeg(
            ["-i", str(take), "-i", str(layer), "-filter_complex",
             "[0:a]aresample=48000[t];[1:a]aresample=48000[c];[t][c]amix=inputs=2:duration=first:normalize=0[a]",
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
        )  # fmt: skip
    return out
