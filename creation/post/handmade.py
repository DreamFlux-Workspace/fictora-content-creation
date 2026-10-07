"""Two paid pieces made by hand on the server: one sound cue, one dry voice line.

- ``cue``: one cue from a written description (``POST /v1/spines/{id}/sfx-cues``,
  about $0.002 a second), saved to ``epNN/sfx/cue-<words>-vN.mp3`` with a JSON
  sidecar. Its RMS per half second and the event / sustained shape check are
  printed. Place it with ``finish --cue PATH@SECONDS[@DB]``.
- ``voice-line``: one dry line in a character's locked voice
  (``POST /v1/spines/{id}/cast/{cast_id}/voice-lines``, $0.10 per 1,000
  characters; the server reads it back with Whisper), saved to
  ``epNN/voices/voice-epNN-<cast>-vN.mp3`` with a JSON sidecar. Lay it with
  ``finish --mute A-B --voice PATH@SECONDS``.

Both are made on the server (no provider key on this laptop), never overwrite a
file, book their cost on the desk ledger, and send a stable ``Idempotency-Key``:
the same description and length (or the same line in the same voice) answers the
first file again and is never paid twice.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.harness_rules import creature_sound_stop, mouth_sound_note
from creation.post.audio_service import AudioService, DramaApiAudio, download
from creation.post.desk import (
    cast_slug,
    find_cast,
    open_api,
    refresh_spine,
    show_language,
    spine_id,
)
from creation.post.finish import book
from creation.post.media import measure_rms_windows
from creation.post.sfx import (
    SFX_MAX_SECONDS,
    SFX_MIN_SECONDS,
    SFX_USD_PER_SECOND,
    shape_problem,
)

#: Default cue length: an event should hit early and stay under about 3 s.
CUE_DEFAULT_SECONDS = 1.5
#: RMS window the cue's shape is printed and checked in.
CUE_SHAPE_WINDOW_SECONDS = 0.5
ELEVEN_V3_USD_PER_1000_CHARS = 0.10


def _key(prefix: str, arguments: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[
        :10
    ]
    return f"{prefix}-{digest}"


def _answer_usd(answer: dict[str, Any], estimate: float) -> float:
    value = answer.get("cost_usd")
    return (
        round(float(value), 4) if isinstance(value, int | float) else round(estimate, 4)
    )


def _note(desk: Path, episode: int, body: str) -> None:
    run_dir = desk / f"ep{episode:02d}"
    if (run_dir / "run-notes.md").is_file():
        append_run_note(run_dir, body)


def cue_usd(seconds: float) -> float:
    """What one cue render costs: billed seconds (at least 0.5) at the SFX rate."""

    return round(max(SFX_MIN_SECONDS, seconds) * SFX_USD_PER_SECOND, 4)


def rms_line(
    levels: tuple[float, ...], window: float = CUE_SHAPE_WINDOW_SECONDS
) -> str:
    """``0.0s -14.2  0.5s -18.0 ...``: RMS dB per window."""

    return "  ".join(
        f"{index * window:.1f}s {level:.1f}" for index, level in enumerate(levels)
    )


def _words(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.casefold())[:4]) or "cue"


def run_cue(
    desk: Path,
    *,
    episode: int,
    description: str,
    seconds: float = CUE_DEFAULT_SECONDS,
    audio: AudioService | None = None,
    out: TextIO | None = None,
) -> Path:
    """Have the server render one hand cue; save it, print its shape, book its cost.

    Parameters
    ----------
    desk
        Series desk bound to a spine.
    episode
        Episode the cue is for (its ``sfx/`` folder and the ledger).
    description
        What the cue should sound like (``a descending comic brass sting``).
    seconds
        Length, 0.5-22 s.
    audio
        Generated-audio service (the Drama API by default).
    out
        Where the result is printed.

    Returns
    -------
    Path
        ``epNN/sfx/cue-<words>-vN.mp3``.

    Raises
    ------
    ValueError
        When the description is empty, the length is out of range, or a non-human
        cue does not name its sound.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    sound = description.strip()
    if not sound:
        raise ValueError("--description is empty: say what the cue should sound like")
    creature = creature_sound_stop(sound)
    if creature:
        raise ValueError(creature + " Nothing was sent.")
    mouth = mouth_sound_note(sound)
    if mouth:
        print(mouth, file=out)
    if not SFX_MIN_SECONDS <= seconds <= SFX_MAX_SECONDS:
        raise ValueError(
            f"--seconds must be {SFX_MIN_SECONDS:g}-{SFX_MAX_SECONDS:g}; got {seconds:g}"
        )
    sfx_dir = desk / f"ep{episode:02d}" / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    service = audio or DramaApiAudio(desk, episode=episode)
    key = _key(f"cue-ep{episode:02d}", {"sound": sound, "seconds": round(seconds, 2)})
    print(f"Rendering the cue {sound!r} ({seconds:g} s) on the server", file=out)
    answer = service.sfx_cue(
        spine_id=spine_id(desk), sound=sound, seconds=round(seconds, 2), key=key
    )
    dest = download(
        str(answer["audio_url"]),
        next_versioned_path(sfx_dir, f"cue-{_words(sound)}", ".mp3"),
    )
    cost = _answer_usd(answer, cue_usd(seconds))
    if cost:
        book(desk, episode=episode, usd=cost, stream=out, unit=f"cue:{_words(sound)}")
    levels = measure_rms_windows(dest, window_seconds=CUE_SHAPE_WINDOW_SECONDS)
    kind = "sustained" if answer.get("kind") == "sustained" else "event"
    problem = answer.get("shape_problem") or shape_problem(
        kind, levels, window_seconds=CUE_SHAPE_WINDOW_SECONDS
    )
    dest.with_suffix(".json").write_text(
        json.dumps({"description": sound, "seconds": seconds, "url": answer.get("audio_url"), "key": key,
                    "kind": kind, "cached": bool(answer.get("cached")), "cost_usd": cost,
                    "rms_db": list(levels), "shape_problem": problem}, indent=2) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    print(dest, file=out)
    print(f"RMS per {CUE_SHAPE_WINDOW_SECONDS:g} s: {rms_line(levels)}", file=out)
    if problem:
        print(
            f"!! shape ({kind}): {problem}. Change the description and render once more; "
            "a cue wrong twice is dropped with a line in the run notes",
            file=out,
        )
    else:
        loudest = (
            max(range(len(levels)), key=lambda index: levels[index]) if levels else 0
        )
        print(
            f"shape ({kind}): ok; loudest at {loudest * CUE_SHAPE_WINDOW_SECONDS:.1f} s",
            file=out,
        )
    cached = " (the server had it cached)" if answer.get("cached") else ""
    _note(
        desk,
        episode,
        f"cue: `{dest.name}` ({sound!r}, {seconds:g} s), ${cost:.3f}{cached}; shape {problem or 'ok'}.",
    )
    print(
        f"Next: place it on the frame where its action lands: fictora-produce finish --desk {desk} "
        f"--episode {episode} --cue {dest}@START[@DB]",
        file=out,
    )
    return dest


def locked_voice(card: dict[str, Any]) -> str:
    """The cast card's locked voice (``voice_brief.provider_voice``), ``""`` when none is locked."""

    return str((card.get("voice_brief") or {}).get("provider_voice") or "").strip()


def voice_line_key(
    *,
    episode: int,
    cast_id: str,
    text: str,
    voice: str,
    language: str,
    spoken_text: str | None = None,
) -> str:
    """The ``Idempotency-Key`` for one dry line: the same key ``revoice`` sends for the same line in the same voice."""

    arguments: dict[str, Any] = {"text": text, "voice": voice, "language": language}
    if spoken_text:
        arguments["spoken"] = spoken_text
    return _key(f"voice-ep{episode:02d}-{cast_slug(cast_id)}", arguments)


def saved_voice_line(voices_dir: Path, key: str) -> Path | None:
    """A dry line already on the desk for ``key`` (its sidecar ``.json`` names the key), newest first."""

    for meta in sorted(voices_dir.glob("voice-*.json"), reverse=True):
        try:
            recorded = json.loads(meta.read_text(encoding="utf-8")).get("key")
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
        audio = meta.with_suffix(".mp3")
        if recorded == key and audio.is_file():
            return audio
    return None


@dataclass(frozen=True)
class DryLine:
    """One dry line on the desk: the file, what it cost this time, and whether a saved one was reused."""

    path: Path
    cost_usd: float
    reused: bool
    reading: dict[str, Any]


def make_voice_line(
    desk: Path,
    *,
    spine: dict[str, Any],
    card: dict[str, Any],
    text: str,
    episode: int,
    spoken_text: str | None = None,
    audio: AudioService,
    out: TextIO,
    take_id: str | None = None,
    reuse: bool = False,
    extra: dict[str, Any] | None = None,
) -> DryLine:
    """Render one dry line in the card's locked voice on the server, save it with its sidecar and book it.

    Parameters
    ----------
    desk
        Series desk bound to a spine.
    spine
        Spine JSON (the show's language).
    card
        The speaker's cast card.
    text
        The line as performed.
    episode
        Episode (its ``voices/`` folder and the ledger).
    spoken_text
        Optional phonetic spelling sent to the voice instead.
    audio
        Generated-audio service.
    out
        Where the booking note prints.
    take_id
        Take the spend is booked on, when known.
    reuse
        Answer a line already on the desk for the same key (same words, voice and
        language) without a call or a booking (``finish``'s inner-voice lines).
    extra
        More fields for the sidecar (``cue_id``).

    Returns
    -------
    DryLine

    Raises
    ------
    ValueError
        When the character has no locked voice.
    AudioServiceError
        When the server refuses the line.
    """

    cast_id, name = str(card["cast_id"]), str(card.get("name") or card["cast_id"])
    voice = locked_voice(card)
    if not voice:
        raise ValueError(
            f"{name} has no locked voice on the cast card; run voice --audition then --pick N first"
        )
    language = show_language(spine)
    slug = cast_slug(cast_id)
    voices_dir = desk / f"ep{episode:02d}" / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    # The same key revoice sends for the same line in the same voice: a line made by either is never paid twice.
    key = voice_line_key(
        episode=episode, cast_id=cast_id, text=text, voice=voice, language=language,
        spoken_text=spoken_text,
    )  # fmt: skip
    if reuse:
        saved = saved_voice_line(voices_dir, key)
        if saved is not None:
            meta = json.loads(saved.with_suffix(".json").read_text(encoding="utf-8"))
            return DryLine(saved, 0.0, True, dict(meta.get("reading") or {}))
    answer = audio.voice_line(
        spine_id=spine_id(desk),
        cast_id=cast_id,
        text=text,
        language=language,
        key=key,
        spoken_text=spoken_text,
    )
    url = str(answer["audio_url"])
    dest = download(
        url, next_versioned_path(voices_dir, f"voice-ep{episode:02d}-{slug}", ".mp3")
    )
    cost = _answer_usd(
        answer, len(spoken_text or text) * ELEVEN_V3_USD_PER_1000_CHARS / 1000
    )
    if cost:
        book(desk, episode=episode, usd=cost, take_id=take_id, stream=out,
             unit=f"voice-line:{slug}")  # fmt: skip
    reading = answer.get("reading") or {}
    dest.with_suffix(".json").write_text(
        json.dumps({"line": text, "spoken_text": spoken_text, "cast_id": cast_id, "voice": answer.get("provider_voice")
                    or voice, "key": key, "url": url, "reading": reading, "cost_usd": cost, **(extra or {})},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    return DryLine(dest, cost, False, dict(reading))


def run_voice_line(
    desk: Path,
    *,
    cast: str,
    text: str,
    episode: int = 1,
    spoken_text: str | None = None,
    audio: AudioService | None = None,
    out: TextIO | None = None,
) -> Path:
    """Have the server render one dry line in a character's locked voice; save it and book its cost.

    Parameters
    ----------
    desk
        Series desk bound to a spine.
    cast
        ``cast_id`` or name.
    text
        The line as performed, in the show's spoken language.
    episode
        Episode the line is for (its ``voices/`` folder and the ledger).
    spoken_text
        Optional phonetic spelling the voice is sent instead (``わらってます`` for 笑ってます).
    audio
        Generated-audio service (the Drama API by default).
    out
        Where the result is printed.

    Returns
    -------
    Path
        ``epNN/voices/voice-epNN-<cast>-vN.mp3``.

    Raises
    ------
    ValueError
        An empty line, or a character with no locked voice.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    line = text.strip()
    if not line:
        raise ValueError("--text is empty")
    spoken = (spoken_text or "").strip() or None
    run = open_api(desk, episode)
    try:
        spine = refresh_spine(run, desk, episode)
    finally:
        run.client.close()
    card = find_cast(spine, cast)
    name = str(card.get("name") or card["cast_id"])
    voice = locked_voice(card)
    if not voice:
        raise ValueError(
            f"{name} has no locked voice on the cast card; run voice --audition then --pick N first"
        )
    service = audio or DramaApiAudio(desk, episode=episode)
    print(
        f"Voicing {name}'s line in the locked voice {voice!r} on the server", file=out
    )
    made = make_voice_line(
        desk, spine=spine, card=card, text=line, episode=episode, spoken_text=spoken,
        audio=service, out=out,
    )  # fmt: skip
    dest, cost = made.path, made.cost_usd
    print(dest, file=out)
    if made.reading.get("checked") and not made.reading.get("read_right"):
        print(f"!! the line may be misread, listen before using it: {line!r}", file=out)
    _note(
        desk,
        episode,
        f"voice-line: {name} {line!r} -> `{dest.name}` in {voice}, ${cost:.3f}.",
    )
    print(
        f"Next: listen to it. To use it: fictora-produce finish --desk {desk} --episode {episode} "
        f"[--mute A-B] --voice {dest}@START[@DB]",
        file=out,
    )
    return dest
