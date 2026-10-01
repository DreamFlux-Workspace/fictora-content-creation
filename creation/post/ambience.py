"""Location ambience under a locked-voice take: the place's own sound, made once per episode.

A take filmed with ``soundtrack.mode == "target_audio"`` comes back with
exactly our dialogue track, so the video model's own location sound (the
arcade, the surf, the street) is gone. Room tone alone (about -52 dB) leaves the
take studio-dry: the Hanakaze ep 1 native take carried its arcade at about
-27 dB between the lines, option C sat at -35 (2026-10-01 check).

So ``finish`` makes one ambience cue for the episode on the server, from the
take's own sound plan (never from the compiled prompt, which this kit does not
read):

- the **location**: the spine's ``frames[].visual_brief.location`` of the
  frames this take films (the beats whose lines are on its dialogue track; the
  episode's frames when none match), the most common one;
- the **planned ambience**: the take facts' sustained cues (the Sound lines the
  server classes as going on: wind, hum, crowd) and the take's beats'
  ``motion_direction.sound_cue``.

The description always ends with "no music, no speech". It is rendered with the
same generated-audio route as the planned effects (``POST
/v1/spines/{id}/sfx-cues``, about $0.002 a second, at most 22 s), saved in
``epNN/ambience/`` with a record (``ambience-epNN.json``), and reused by every
take of the episode and every re-finish: a re-finish pays nothing.

It is laid under the whole take (:func:`lay_ambience`): looped seamlessly to
cover the episode, levelled so the gaps between lines sit at
:data:`AMBIENCE_GAP_DB` after the mix's take gain, dropped
:data:`AMBIENCE_DUCK_DB` inside each line window over :data:`AMBIENCE_RAMP_SECONDS`
ramps, faded in at the episode's head and out at its tail. Take ``tN`` lays the
slice of the one episode ambience that starts at the sum of the raw lengths of
``t1`` .. ``tN-1``, so takes joined on a straight cut carry one continuous
ambience across the seam (L-20260929-23).

When no cue can be made (no location and no planned ambience, a refusal, a
wrong shape, no answer), ``finish`` falls back to room tone and says why.
Native takes never get an ambience: the model's own is on them.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from creation.post.audio_service import AudioService, download
from creation.post.media import probe_video, run_ffmpeg

#: RMS (dBFS) the ambience sits at between the lines once the mix's take gain is on it.
AMBIENCE_GAP_DB = -28.0
#: How far the ambience drops inside each line window.
AMBIENCE_DUCK_DB = 10.0
#: Ramp into and out of each duck.
AMBIENCE_RAMP_SECONDS = 0.25
#: Fade in at the episode's head, fade out at its tail.
AMBIENCE_FADE_IN_SECONDS = 0.3
AMBIENCE_FADE_OUT_SECONDS = 0.5
#: The generated-audio route's limits and price (the planned effects' route).
AMBIENCE_MIN_SECONDS = 5.0
AMBIENCE_MAX_SECONDS = 22.0
AMBIENCE_USD_PER_SECOND = 0.002
#: The route takes at most this many characters of description.
DESCRIPTION_LIMIT = 300
#: Loop points are crossfaded over this long (at most a quarter of the cue).
LOOP_CROSSFADE_SECONDS = 1.0
#: Every description ends with this: an ambience is never music or words.
DESCRIPTION_TAIL = "steady natural background sound of the place, distant activity and air; no music, no speech"
_RATE = 48000
_SILENCE = 10 ** (-60.0 / 20)

Maker = Callable[[str, float, Path], tuple[Path, float]]
"""``(description, seconds, target) -> (rendered file, cost in USD)``; raises when nothing usable came back."""


@dataclass(frozen=True)
class AmbienceBrief:
    """What the ambience of a take should sound like, and where the words came from."""

    description: str
    location: str | None
    planned: tuple[str, ...]


def _one_line(text: Any) -> str:
    return " ".join(str(text or "").split())


def _episode_id(spine: Mapping[str, Any], episode: int) -> str:
    for summary in spine.get("episode_summaries") or []:
        if isinstance(summary, Mapping) and int(summary.get("ordinal") or 0) == episode:
            return str(summary.get("episode_id") or "")
    return f"episode_{episode:02d}"


def take_line_ids(facts: Mapping[str, Any]) -> set[str]:
    """The line ids a take films: its dialogue track's lines, else the take facts' ``lines``."""

    body = facts.get("take_facts", facts)
    soundtrack = body.get("soundtrack")
    track = soundtrack.get("lines") if isinstance(soundtrack, Mapping) else None
    ids = {
        str(line.get("line_id"))
        for line in track or []
        if isinstance(line, Mapping) and line.get("line_id")
    }
    if not ids:
        ids = {
            str(line.get("line_id"))
            for line in body.get("lines") or []
            if isinstance(line, Mapping) and line.get("line_id")
        }
    return ids


def ambience_brief(
    facts: Mapping[str, Any], spine: Mapping[str, Any] | None, *, episode: int
) -> AmbienceBrief | None:
    """The ambience description for a take, from its take facts and the spine; ``None`` when there is nothing to go on.

    Parameters
    ----------
    facts
        The take's saved take facts (``{"take_facts": {...}}`` or the facts alone).
    spine
        The saved spine (``None``: no location is known).
    episode
        Episode ordinal.

    Returns
    -------
    AmbienceBrief | None
        ``None`` when the spine names no location for the take and the take
        facts plan no ambience: a cue would be a guess.
    """

    body = facts.get("take_facts", facts)
    lines = take_line_ids(facts)
    beats: list[Mapping[str, Any]] = []
    frames: dict[str, Mapping[str, Any]] = {}
    if spine:
        episode_id = _episode_id(spine, episode)
        beats = [
            beat
            for beat in spine.get("beats") or []
            if isinstance(beat, Mapping) and beat.get("episode_id") == episode_id
        ]
        frames = {
            str(frame.get("frame_id")): frame
            for frame in spine.get("frames") or []
            if isinstance(frame, Mapping) and frame.get("episode_id") == episode_id
        }
    own = [
        beat
        for beat in beats
        if any(
            isinstance(line, Mapping) and str(line.get("line_id")) in lines
            for line in beat.get("dialogue_lines") or []
        )
    ]
    take_beats = own or beats
    chosen = [
        frames[str(beat.get("frame_id"))]
        for beat in take_beats
        if str(beat.get("frame_id")) in frames
    ] or list(frames.values())
    places = [
        _one_line((frame.get("visual_brief") or {}).get("location"))
        for frame in chosen
        if isinstance(frame.get("visual_brief"), Mapping)
    ]
    places = [place for place in places if place]
    location = Counter(places).most_common(1)[0][0] if places else None
    planned: list[str] = []
    sounds = [
        _one_line(cue.get("sound"))
        for cue in body.get("sfx_cues") or []
        if isinstance(cue, Mapping) and cue.get("kind") == "sustained"
    ] + [
        _one_line(beat["motion_direction"].get("sound_cue"))
        for beat in take_beats
        if isinstance(beat.get("motion_direction"), Mapping)
        and isinstance(beat["motion_direction"].get("sound_cue"), str)
    ]
    for sound in sounds:
        if sound and sound.casefold() not in {p.casefold() for p in planned}:
            planned.append(sound.rstrip("."))
    planned = planned[:3]
    if not location and not planned:
        return None
    return AmbienceBrief(describe(location, tuple(planned)), location, tuple(planned))


def describe(location: str | None, planned: Sequence[str]) -> str:
    """``Location ambience: <place>; <planned sounds>; <tail>`` cut to :data:`DESCRIPTION_LIMIT` characters.

    The tail (no music, no speech) is always kept whole; the place is shortened first.
    """

    middle = "; ".join(planned)
    head = "Location ambience"
    fixed = (
        len(head)
        + len("; ")
        + len(DESCRIPTION_TAIL)
        + (len(middle) + 2 if middle else 0)
    )
    room = DESCRIPTION_LIMIT - fixed - len(": ")
    if location and room > 20:
        place = (
            location
            if len(location) <= room
            else location[: room - 1].rstrip(" ,;") + "…"
        )
        head = f"{head}: {place}"
    text = "; ".join(part for part in (head, middle, DESCRIPTION_TAIL) if part)
    return text[:DESCRIPTION_LIMIT]


def cue_key(description: str, seconds: float) -> str:
    """Same description and length -> same file (and the same request key)."""

    raw = json.dumps([description.strip().casefold(), round(seconds, 2)])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def cue_seconds(episode_seconds: float) -> float:
    """How long a cue to make: the episode's length, within the route's 5-22 s (longer is looped)."""

    return round(
        min(AMBIENCE_MAX_SECONDS, max(AMBIENCE_MIN_SECONDS, episode_seconds)), 2
    )


def service_maker(audio: AudioService, spine_id: str) -> Maker:
    """Make the cue on the server (``POST /v1/spines/{id}/sfx-cues``, the planned effects' route)."""

    def make(description: str, seconds: float, target: Path) -> tuple[Path, float]:
        answer = audio.sfx_cue(
            spine_id=spine_id,
            sound=description,
            seconds=seconds,
            key=f"ambience-{cue_key(description, seconds)}",
        )
        if answer.get("shape_problem"):
            raise ValueError(f"wrong shape: {answer['shape_problem']}")
        path = download(str(answer["audio_url"]), target)
        cost = answer.get("cost_usd")
        if answer.get("cached"):
            return path, 0.0
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            return path, float(cost)
        return path, round(seconds * AMBIENCE_USD_PER_SECOND, 4)

    return make


# --- the episode's ambience on the desk -------------------------------------------------------------------------


def ambience_dir(desk: Path, episode: int) -> Path:
    """``epNN/ambience``."""

    return desk / f"ep{episode:02d}" / "ambience"


def record_path(desk: Path, episode: int) -> Path:
    """``epNN/ambience/ambience-epNN.json``: the episode's one ambience."""

    return ambience_dir(desk, episode) / f"ambience-ep{episode:02d}.json"


@dataclass(frozen=True)
class EpisodeAmbience:
    """The episode's ambience cue on the desk."""

    description: str
    location: str | None
    path: Path
    seconds: float
    #: True when this run made it (paid); False when it was on the desk already.
    made: bool = False
    cost_usd: float = 0.0


def saved_ambience(desk: Path, episode: int) -> EpisodeAmbience | None:
    """The episode's saved ambience, when its record and its file are both on the desk."""

    path = record_path(desk, episode)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cue = ambience_dir(desk, episode) / str(raw["file"])
        found = EpisodeAmbience(
            str(raw["description"]),
            raw.get("location") or None,
            cue,
            float(raw["seconds"]),
        )
    except (ValueError, KeyError, TypeError):
        return None
    return found if cue.is_file() else None


def _same_place(a: str | None, b: str | None) -> bool:
    return not a or not b or a.casefold() == b.casefold()


def episode_ambience(
    desk: Path,
    episode: int,
    brief: AmbienceBrief,
    *,
    episode_seconds: float,
    make: Maker,
) -> tuple[EpisodeAmbience, str]:
    """The episode's one ambience: the saved one when this take is in the same place, else a new cue.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    brief
        This take's :func:`ambience_brief`.
    episode_seconds
        The episode's filmed length so far (sets the cue's length).
    make
        Cue maker (:func:`service_maker` in production).

    Returns
    -------
    tuple[EpisodeAmbience, str]
        The ambience, and a note (``""``, or why this take has its own).

    Raises
    ------
    RuntimeError, OSError, ValueError, KeyError
        When the cue cannot be made (the caller falls back to room tone).
    """

    saved = saved_ambience(desk, episode)
    if saved is not None and _same_place(saved.location, brief.location):
        return saved, ""
    note = ""
    if saved is not None:
        note = (
            f"this take is set in another place ({brief.location}) than the episode's ambience "
            f"({saved.location}): it gets its own, so the seam into it changes ambience"
        )
    seconds = cue_seconds(episode_seconds)
    key = cue_key(brief.description, seconds)
    folder = ambience_dir(desk, episode)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"ambience-{key}.mp3"
    cost = 0.0
    made = False
    if not target.is_file():
        target, cost = make(brief.description, seconds, target)
        made = True
    found = EpisodeAmbience(
        brief.description, brief.location, target, seconds, made, cost
    )
    if saved is None:
        record_path(desk, episode).write_text(
            json.dumps(
                {"description": brief.description, "location": brief.location,
                 "planned": list(brief.planned), "file": target.name, "seconds": seconds},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )  # fmt: skip
    return found, note


# --- laying it ---------------------------------------------------------------------------------------------------


def _decode(path: Path) -> np.ndarray:
    from creation.post.join import decode_stereo

    return decode_stereo(path)


def _write(samples: np.ndarray, out: Path) -> Path:
    from creation.post.join import write_wav

    return write_wav(samples, out)


def rms_db(samples: np.ndarray) -> float:
    """RMS of the first channel in dBFS (what :func:`creation.post.media.measure_rms_windows` reads)."""

    if samples.size == 0:
        return -120.0
    power = float(np.mean(samples[:, 0] ** 2))
    return 10 * math.log10(power) if power > 0 else -120.0


def trimmed(samples: np.ndarray) -> np.ndarray:
    """The cue without its silent head and tail.

    Raises
    ------
    ValueError
        When the cue is silent (or under half a second of sound).
    """

    loud = np.flatnonzero(np.abs(samples).max(axis=1) > _SILENCE)
    if loud.size == 0 or (loud[-1] - loud[0]) < 0.5 * _RATE:
        raise ValueError("the ambience cue is silent")
    return samples[loud[0] : loud[-1] + 1]


def looped(samples: np.ndarray, seconds: float) -> np.ndarray:
    """The cue looped to at least ``seconds`` (silent head and tail cut, equal-power crossfades).

    Raises
    ------
    ValueError
        When the cue is silent.
    """

    body = trimmed(samples)
    overlap = int(min(LOOP_CROSSFADE_SECONDS, len(body) / _RATE / 4) * _RATE)
    ramp = (np.arange(overlap) + 0.5)[:, None] / max(overlap, 1) * (math.pi / 2)
    fade_out, fade_in = np.cos(ramp), np.sin(ramp)
    need = int(math.ceil(seconds * _RATE))
    out = body
    while len(out) < need:
        seam = out[len(out) - overlap :] * fade_out + body[:overlap] * fade_in
        out = np.concatenate([out[: len(out) - overlap], seam, body[overlap:]])
    return out[:need]


def duck_envelope(
    count: int,
    windows: Sequence[tuple[float, float]],
    *,
    depth_db: float = AMBIENCE_DUCK_DB,
    ramp: float = AMBIENCE_RAMP_SECONDS,
) -> np.ndarray:
    """A gain per sample: 1 outside the windows, ``-depth_db`` inside, ramped over ``ramp`` either side."""

    t = np.arange(count) / _RATE
    floor = 10 ** (-abs(depth_db) / 20)
    gain = np.ones(count)
    for start, end in windows:
        rise = np.clip((t - (start - ramp)) / ramp, 0.0, 1.0)
        fall = np.clip(((end + ramp) - t) / ramp, 0.0, 1.0)
        gain = np.minimum(gain, 1 - (1 - floor) * rise * fall)
    return gain


@dataclass(frozen=True)
class LaidAmbience:
    """What :func:`lay_ambience` wrote."""

    output: Path
    #: The ambience's RMS between the lines as laid (before the mix's take gain).
    laid_db: float
    #: Where the take's slice starts on the episode's ambience.
    offset: float


def lay_ambience(
    take: Path,
    cue: Path,
    out: Path,
    *,
    windows: Sequence[tuple[float, float]],
    level_db: float,
    offset: float = 0.0,
    fade_in: bool = True,
    fade_out: bool = True,
    duck_db: float = AMBIENCE_DUCK_DB,
) -> LaidAmbience:
    """Lay the episode ambience's slice for this take under the take's own sound (picture copied).

    Parameters
    ----------
    take
        The take (its own sound kept as it is).
    cue
        The episode's ambience cue.
    out
        New file; must not exist.
    windows
        Line windows, in take seconds: the ambience drops ``duck_db`` inside them.
    level_db
        RMS (dBFS) the ambience sits at outside the windows, in this file.
    offset
        Seconds into the episode where this take starts (its slice of the one ambience).
    fade_in, fade_out
        Fade at the take's head (the episode's first take) and tail (its last).
    duck_db
        Depth inside the line windows.

    Returns
    -------
    LaidAmbience
        The file and the level laid.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    ValueError
        When the take has no audio or the cue is silent.
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    info = probe_video(take)
    if not info.has_audio:
        raise ValueError(f"{take.name} has no audio to lay an ambience under")
    total = info.duration_seconds
    decoded = _decode(cue)
    body = looped(decoded, offset + total)
    start = int(round(offset * _RATE))
    piece = body[start : start + int(math.ceil(total * _RATE))].copy()
    # One gain from the whole cue, never from this slice: every take of the episode gets the same
    # level, so the ambience runs on unbroken across a seam.
    piece *= 10 ** ((level_db - rms_db(trimmed(decoded))) / 20)
    piece *= duck_envelope(len(piece), windows, depth_db=duck_db)[:, None]
    if fade_in:
        n = min(len(piece), int(AMBIENCE_FADE_IN_SECONDS * _RATE))
        piece[:n] *= np.linspace(0.0, 1.0, n)[:, None]
    if fade_out:
        n = min(len(piece), int(AMBIENCE_FADE_OUT_SECONDS * _RATE))
        piece[len(piece) - n :] *= np.linspace(1.0, 0.0, n)[:, None]
    bed = _write(piece[:, [0, 0]], out.with_name(out.stem + "-bed.wav"))
    try:
        graph = (
            # The air goes in as one channel: a stereo bed on a mono take made ffmpeg
            # re-lay the take's own voice about 4 dB quieter (Hanakaze live check).
            "[0:a]aresample=48000[take];[1:a]aresample=48000,pan=mono|c0=c0[air];"
            "[take][air]amix=inputs=2:duration=first:normalize=0[a]"
        )
        run_ffmpeg(
            ["-i", str(take), "-i", str(bed), "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)]
        )  # fmt: skip
    finally:
        bed.unlink(missing_ok=True)
    return LaidAmbience(out, round(gap_level(piece, windows), 1), round(offset, 3))


def gap_level(samples: np.ndarray, windows: Sequence[tuple[float, float]]) -> float:
    """RMS (dBFS) of the samples outside the windows and the head/tail fades."""

    if len(samples) == 0:
        return -120.0
    t = np.arange(len(samples)) / _RATE
    keep = (t >= AMBIENCE_FADE_IN_SECONDS) & (t <= t[-1] - AMBIENCE_FADE_OUT_SECONDS)
    for start, end in windows:
        keep &= ~(
            (t >= start - AMBIENCE_RAMP_SECONDS) & (t <= end + AMBIENCE_RAMP_SECONDS)
        )
    return rms_db(samples[keep]) if keep.any() else rms_db(samples)


def episode_offset(lengths: Sequence[float | None]) -> float | None:
    """Where take ``tN`` starts on the episode: the sum of the lengths before it (``None`` if one is unknown)."""

    before = list(lengths[:-1])
    if any(length is None for length in before):
        return None
    return round(sum(float(length) for length in before if length is not None), 3)


__all__ = [
    "AMBIENCE_DUCK_DB",
    "AMBIENCE_GAP_DB",
    "AmbienceBrief",
    "EpisodeAmbience",
    "LaidAmbience",
    "Maker",
    "ambience_brief",
    "cue_key",
    "cue_seconds",
    "describe",
    "duck_envelope",
    "episode_ambience",
    "episode_offset",
    "lay_ambience",
    "record_path",
    "saved_ambience",
    "service_maker",
    "take_line_ids",
]
