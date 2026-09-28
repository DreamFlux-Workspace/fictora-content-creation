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
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from creation.post.audio_service import AudioService, download
from creation.post.media import LIMITER, measure_rms_windows, probe_video, run_ffmpeg

SFX_USD_PER_SECOND = 0.002
SFX_GAIN_DB = -8.0
SFX_GAIN_RANGE_DB = (-30.0, 0.0)
SFX_MIN_SECONDS = 0.5
SFX_MAX_SECONDS = 22.0
SFX_MIN_PLACED_SECONDS = 0.25
SFX_SPEECH_DUCK_DB = -10.0
SILENCE_DB = -50.0

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
        :data:`SFX_GAIN_DB` when the cue carries no offset (an older server, or no level note).
    """

    offset = cue.get("gain_offset_db")
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        return SFX_GAIN_DB
    return _clamp_gain(SFX_GAIN_DB + float(offset))


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


def saved_take_facts(desk: Path, episode: int, take_id: str) -> Path | None:
    """Newest ``epNN/api/take-facts-epNN-tK-vN.json``."""

    api = desk / f"ep{episode:02d}" / "api"
    found = list(api.glob(f"take-facts-ep{episode:02d}-{take_id}-v*.json"))

    def version(path: Path) -> int:
        match = re.search(r"-v(\d+)$", path.stem)
        return int(match.group(1)) if match else 0

    return max(found, key=version) if found else None


def shape_problem(kind: str, levels: tuple[float, ...]) -> str | None:
    """What is wrong with a rendered cue's shape, or ``None``: an event must hit in its first second,
    a sustained sound must not collapse after the first half second."""

    if not levels:
        return "no audio"
    peak = max(levels)
    if peak <= SILENCE_DB:
        return "silent"
    if kind == "event":
        return None if max(levels[:2]) >= peak - 12.0 else "event starts late"
    tail = levels[1:]
    if not tail:
        return None
    held = sum(1 for level in tail if level >= peak - 18.0 and level > SILENCE_DB)
    return None if held / len(tail) >= 0.5 else "sustained sound collapses"


def service_renderer(audio: AudioService, spine_id: str) -> Renderer:
    """Render a cue on the server (``POST /v1/spines/{id}/sfx-cues``) from its authored Sound label.

    The server checks the shape too; a cue it flags is refused here (skipped, not mixed).
    """

    def render(cue: SfxCue, target: Path) -> Path:
        seconds = round(max(SFX_MIN_SECONDS, min(SFX_MAX_SECONDS, cue.seconds)), 2)
        answer = audio.sfx_cue(
            spine_id=spine_id,
            sound=cue.sound,
            seconds=seconds,
            key=f"sfx-{cue.cache_key}",
        )
        if answer.get("shape_problem"):
            raise ValueError(f"wrong shape: {answer['shape_problem']}")
        return download(str(answer["audio_url"]), target)

    return render


@dataclass(frozen=True)
class SfxResult:
    """What the SFX step wrote."""

    output: Path
    mixed: tuple[SfxCue, ...]
    skipped: tuple[str, ...]
    rendered: int
    cost_usd: float
    #: Each mixed cue's loudest 0.5 s window as placed (rendered level + its gain), same order as ``mixed``.
    peaks_db: tuple[float, ...] = ()


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


def lay_sfx(
    take: Path,
    plan: SfxPlan,
    *,
    cache_dir: Path,
    output: Path,
    adjustments: tuple[Adjustment, ...] = (),
    render: Renderer,
    measure: Meter = measure_rms_windows,
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
            kept.append((cue, cached))
            continue
        good: Path | None = None
        for _attempt in (1, 2):
            try:
                path = render(cue, cached)
            except (RuntimeError, OSError, KeyError, ValueError) as exc:
                skipped.append(f"{cue.sound} (render failed: {str(exc)[:120]})")
                break
            rendered += 1
            cost += max(SFX_MIN_SECONDS, cue.seconds) * SFX_USD_PER_SECOND
            if shape_problem(cue.kind, measure(path)) is None:
                good = path
                break
            path.unlink(missing_ok=True)
        else:
            skipped.append(f"{cue.sound} (wrong shape twice)")
        if good is not None:
            kept.append((cue, good))
    if not kept:
        raise RuntimeError(
            "no sound effect could be laid: "
            + ("; ".join(skipped) if skipped else "the take facts plan no cue")
        )
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
    )
