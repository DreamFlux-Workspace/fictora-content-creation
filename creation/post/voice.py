"""Change a character's voice without regenerating anything: audition, pick, revoice.

"The boy's voice is off" never means re-making the story. The voice is a lock
on the cast card, and a take that is already filmed takes a new voice in post:

- ``voice --audition``: ``POST /v1/spines/{id}/cast/{cast_id}/voice-auditions``
  compiles 4-10 Eleven v3 candidates on the character's real lines (the API
  returns voice ids and text only). Each candidate is rendered here on Fal
  ``fal-ai/elevenlabs/tts/eleven-v3`` into
  ``shared/voices/<cast>/audition-vN/NN-<voice>.mp3`` with ``auditions.json``.
- ``voice --pick N``: ``POST .../voice-auditions/pick`` locks candidate N on the
  cast card and saves the spine again. Takes filmed from now on use it.
- ``revoice``: for a filmed take, each line that character speaks is rendered
  dry in the locked voice, its window found from Whisper words on the take,
  the original muted there (0.08 s before to 0.15 s after) and the new line
  laid in at the same start. Picture copied, other characters left as filmed.
  Writes ``take-epNN-tK-revoice-vN.mp4``; then run ``finish --take-file`` on it.

When a dub will not sit (lips visibly wrong, a shouted line), re-film only the
takes that character speaks in, never the story.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.post.desk import (
    cast_slug,
    episode_dialogue,
    find_cast,
    latest_raw_take,
    open_api,
    refresh_spine,
    spine_id,
)
from creation.post.fal import VOICE_ENDPOINT, FalCalls, FalClientCalls, download, output_url, run_fal_once
from creation.post.finish import book
from creation.post.media import media_duration, probe_video, run_ffmpeg
from creation.post.whisper import Transcriber, line_windows, load_words, transcribe_with_fal

AUDITION_SET_USD = 0.30
"""One audition set, as the runbook prices it."""
AUDITION_MAX_LINES = 3
MIN_CANDIDATES, MAX_CANDIDATES = 4, 10
VOICE_STABILITY = 0.45
ELEVEN_V3_USD_PER_1000_CHARS = 0.10
MUTE_LEAD_SECONDS = 0.08
MUTE_TAIL_SECONDS = 0.15


def voice_arguments(text: str, voice: str) -> dict[str, Any]:
    """Eleven v3 arguments for one dry line (English)."""

    return {
        "text": text,
        "voice": voice,
        "stability": VOICE_STABILITY,
        "apply_text_normalization": "auto",
        "output_format": "mp3_44100_128",
        "language_code": "en",
    }


def _unit(prefix: str, arguments: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:10]
    return f"{prefix}-{digest}"


def cast_lines(spine: dict[str, Any], cast_id: str, episode: int | None) -> list[str]:
    """The character's authored lines (``text``), in order, from one episode or the whole spine."""

    if episode is not None:
        lines = [line["text"] for line in episode_dialogue(spine, episode) if line["cast_id"] == cast_id]
    else:
        lines = []
        for beat in spine.get("beats") or []:
            for line in beat.get("dialogue_lines") or []:
                if line.get("cast_id") == cast_id and str(line.get("text") or "").strip():
                    lines.append(str(line["text"]).strip())
    unique: list[str] = []
    for line in lines:
        if line not in unique:
            unique.append(line)
    return unique


def audition_dirs(desk: Path, slug: str) -> list[Path]:
    """``shared/voices/<cast>/audition-vN`` folders, oldest first."""

    root = desk / "shared" / "voices" / slug
    found = []
    for path in root.glob("audition-v*") if root.is_dir() else []:
        number = path.name.removeprefix("audition-v")
        if path.is_dir() and number.isdigit():
            found.append((int(number), path))
    return [path for _, path in sorted(found)]


@dataclass(frozen=True)
class Candidate:
    """One rendered audition candidate."""

    number: int
    provider_voice: str
    path: Path
    url: str
    seconds: float


def run_voice_audition(
    desk: Path,
    *,
    cast: str,
    episode: int | None = None,
    count: int = 8,
    cause: str | None = None,
    fal: FalCalls | None = None,
    out: TextIO | None = None,
) -> Path:
    """Audition voices for one character on their real lines; save candidates and a listing.

    Parameters
    ----------
    desk
        Series desk bound to a spine.
    cast
        ``cast_id`` or name.
    episode
        Take the audition lines from this episode only.
    count
        Candidates, 4-10.
    cause
        Why a second audition set is paid for (required when one exists).
    fal
        Fal calls (``FalClientCalls`` by default).
    out
        Where the listing is printed.

    Returns
    -------
    Path
        The audition folder.

    Raises
    ------
    ValueError
        Count out of range, no lines for the character, or a second set without a cause.
    """

    out = out or sys.stdout
    if not MIN_CANDIDATES <= count <= MAX_CANDIDATES:
        raise ValueError(f"--count must be {MIN_CANDIDATES}-{MAX_CANDIDATES}")
    ledger_episode = episode or 1
    run = open_api(desk, ledger_episode)
    try:
        spine = refresh_spine(run, desk, ledger_episode)
        card = find_cast(spine, cast)
        cast_id, name = str(card["cast_id"]), str(card.get("name") or card["cast_id"])
        slug = cast_slug(cast_id)
        lines = cast_lines(spine, cast_id, episode)[:AUDITION_MAX_LINES]
        if not lines:
            where = f" in episode {episode}" if episode else ""
            raise ValueError(f"{name} speaks no line{where} on the spine; an audition needs their real lines")
        existing = audition_dirs(desk, slug)
        unfinished = existing[-1] if existing and not (existing[-1] / "auditions.json").is_file() else None
        finished = [path for path in existing if path != unfinished]
        if finished and not (cause and cause.strip()):
            raise ValueError(
                f"{name} already has an audition set ({finished[-1].name}); pick from it with --pick N, "
                f'or pass --cause "..." to pay for a second set (${AUDITION_SET_USD:.2f})'
            )
        client = fal or FalClientCalls()
        body = {"spine_version": spine["spine_version"], "lines": lines, "candidate_count": count}
        compiled = run.post(f"/v1/spines/{spine_id(desk)}/cast/{cast_id}/voice-auditions", body)
        run.save(f"voice-auditions-{slug}.json", compiled)
    finally:
        run.client.close()
    folder = unfinished or desk / "shared" / "voices" / slug / f"audition-v{len(existing) + 1}"
    folder.mkdir(parents=True, exist_ok=True)
    print(
        f"Auditioning {len(compiled.get('candidates') or [])} voices for {name} on {len(lines)} real line(s)", file=out
    )
    made: list[Candidate] = []
    for number, candidate in enumerate(compiled.get("candidates") or [], start=1):
        voice = str(candidate["provider_voice"])
        arguments = {k: v for k, v in voice_arguments(str(candidate["text"]), voice).items() if k != "language_code"}
        target = folder / f"{number:02d}-{cast_slug(voice)}.mp3"
        fal_run = run_fal_once(
            desk,
            unit=_unit(f"audition-{slug}", arguments),
            endpoint=VOICE_ENDPOINT,
            arguments=arguments,
            fal=client,
            collect=lambda output, dest=target: download(output_url(output, "audio"), dest),
        )
        made.append(Candidate(number, voice, fal_run.path, output_url(fal_run.output, "audio"),
                              round(media_duration(fal_run.path), 3)))  # fmt: skip
    listing = {
        "cast_id": cast_id,
        "name": name,
        "lines": lines,
        "cause": cause,
        "cost_usd": AUDITION_SET_USD,
        "candidates": [
            {"number": c.number, "provider_voice": c.provider_voice, "file": c.path.name, "url": c.url,
             "seconds": c.seconds}
            for c in made
        ],
    }  # fmt: skip
    (folder / "auditions.json").write_text(json.dumps(listing, indent=2) + "\n", encoding="utf-8")
    book(desk, episode=ledger_episode, usd=AUDITION_SET_USD, stream=out)
    locked = str((card.get("voice_brief") or {}).get("provider_voice") or "none")
    print(f"{name}: {len(made)} candidates in {folder} (locked now: {locked})", file=out)
    for item in made:
        print(f"  {item.number:>2}. {item.provider_voice:<14} {item.seconds:5.2f}s  {item.path.name}", file=out)
    _note(desk, ledger_episode, f"voice audition: {name} ({cast_id}), {len(made)} candidates in `{folder.name}`"
          + (f"; cause: {cause}" if cause else ""))  # fmt: skip
    print(f"Next: play them to the human. Their pick: fictora-produce voice --desk {desk} --cast {cast_id} --pick N",
          file=out)  # fmt: skip
    return folder


def run_voice_pick(desk: Path, *, cast: str, pick: int, out: TextIO | None = None) -> str:
    """Lock audition candidate ``pick`` on the character's cast card (spends nothing).

    Returns
    -------
    str
        The locked provider voice.

    Raises
    ------
    FileNotFoundError
        When the character has no finished audition set on the desk.
    ValueError
        When ``pick`` is not a candidate number.
    """

    out = out or sys.stdout
    run = open_api(desk, 1)
    try:
        spine = refresh_spine(run, desk, 1)
        card = find_cast(spine, cast)
        cast_id, name = str(card["cast_id"]), str(card.get("name") or card["cast_id"])
        listed = [
            p / "auditions.json" for p in audition_dirs(desk, cast_slug(cast_id)) if (p / "auditions.json").is_file()
        ]
        if not listed:
            raise FileNotFoundError(f"{name} has no audition set on the desk; run voice --cast {cast_id} --audition")
        listing = json.loads(listed[-1].read_text(encoding="utf-8"))
        chosen = next((c for c in listing["candidates"] if int(c["number"]) == pick), None)
        if chosen is None:
            numbers = ", ".join(str(c["number"]) for c in listing["candidates"])
            raise ValueError(f"no candidate {pick} in {listed[-1].parent.name}; the numbers are {numbers}")
        body = {
            "spine_version": spine["spine_version"],
            "url": chosen["url"],
            "provider_voice": chosen["provider_voice"],
            "seconds": chosen["seconds"],
        }
        run.post(f"/v1/spines/{spine_id(desk)}/cast/{cast_id}/voice-auditions/pick", body)
        refresh_spine(run, desk, 1)
    finally:
        run.client.close()
    print(f"{name} now speaks as {chosen['provider_voice']} (candidate {pick}, {listed[-1].parent.name})", file=out)
    _note(desk, 1, f"voice pick: {name} -> {chosen['provider_voice']} (candidate {pick}).")
    print(
        "Next: takes filmed from now on use the new voice. For a take already filmed: "
        f"fictora-produce revoice --desk {desk} --cast {cast_id} --episode N --take tK, then finish --take-file.",
        file=out,
    )
    return str(chosen["provider_voice"])


@dataclass(frozen=True)
class Replacement:
    """One original line replaced by a dry line."""

    line: str
    voice: Path
    start: float
    end: float

    def mute(self, take_seconds: float) -> tuple[float, float]:
        """The window silenced on the take."""

        return (max(0.0, self.start - MUTE_LEAD_SECONDS), min(take_seconds, self.end + MUTE_TAIL_SECONDS))


def replace_lines(take: Path, out: Path, replacements: tuple[Replacement, ...], *, voice_db: float = 0.0) -> Path:
    """Mute each original line and lay its dry replacement in at the same start (picture copied).

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    seconds = probe_video(take).duration_seconds
    mutes = "".join(
        f"volume=0:enable='between(t,{a:.3f},{b:.3f})'," for a, b in (r.mute(seconds) for r in replacements)
    )
    graph = [f"[0:a]aresample=48000,{mutes}anull[orig]"]
    labels = ["[orig]"]
    inputs: list[str] = []
    for number, item in enumerate(replacements, start=1):
        inputs += ["-i", str(item.voice)]
        delay = round(item.start * 1000)
        graph.append(f"[{number}:a]aresample=48000,volume={voice_db:+.1f}dB,adelay={delay}|{delay}[v{number}]")
        labels.append(f"[v{number}]")
    graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first[a]")
    run_ffmpeg(
        ["-i", str(take), *inputs, "-filter_complex", ";".join(graph), "-map", "0:v", "-map", "[a]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(out)]
    )  # fmt: skip
    return out


def run_revoice(
    desk: Path,
    *,
    cast: str,
    episode: int = 1,
    take_id: str = "t1",
    take_file: Path | None = None,
    words_json: Path | None = None,
    voice_db: float = 0.0,
    fal: FalCalls | None = None,
    transcriber: Transcriber | None = None,
    out: TextIO | None = None,
) -> Path:
    """Re-voice one character's lines on a filmed take in their locked voice.

    Parameters
    ----------
    desk
        Series desk.
    cast
        ``cast_id`` or name.
    episode
        Episode ordinal.
    take_id
        ``t1``.
    take_file
        Take to revoice; default the newest raw take.
    words_json
        Saved Whisper words for the take; default transcribe it now (pennies).
    voice_db
        Gain on every new line.
    fal
        Fal calls for the dry lines.
    transcriber
        Whisper call (Fal by default).
    out
        Where results print.

    Returns
    -------
    Path
        ``take-epNN-tK-revoice-vN.mp4``.

    Raises
    ------
    ValueError
        No locked voice, no line for the character, or none of their lines heard.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    source = take_file.expanduser().resolve() if take_file else latest_raw_take(desk, episode, take_id)
    run = open_api(desk, episode)
    try:
        spine = refresh_spine(run, desk, episode)
    finally:
        run.client.close()
    card = find_cast(spine, cast)
    cast_id, name = str(card["cast_id"]), str(card.get("name") or card["cast_id"])
    voice = str((card.get("voice_brief") or {}).get("provider_voice") or "").strip()
    if not voice:
        raise ValueError(f"{name} has no locked voice on the cast card; run voice --audition then --pick N first")
    dialogue = episode_dialogue(spine, episode)
    theirs = [i for i, line in enumerate(dialogue) if line["cast_id"] == cast_id]
    if not theirs:
        raise ValueError(f"{name} speaks no line in episode {episode} on the spine; nothing to revoice")
    takes = source.parent
    if words_json is None:
        target = next_versioned_path(takes, f"take-ep{episode:02d}-{take_id}-revoice-words", ".json")
        words_json = (transcriber or (lambda media, dest: transcribe_with_fal(media, dest, fal=fal)))(source, target)
    windows = line_windows(load_words(words_json), tuple(line["text"] for line in dialogue))
    client = fal or FalClientCalls()
    voices_dir = desk / f"ep{episode:02d}" / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    slug = cast_slug(cast_id)
    replacements: list[Replacement] = []
    missing: list[str] = []
    paid = 0.0
    for index in theirs:
        window = windows[index]
        text = dialogue[index]["text"]
        if window.start is None or window.end is None:
            missing.append(text)
            continue
        arguments = voice_arguments(text, voice)
        dest = next_versioned_path(voices_dir, f"voice-ep{episode:02d}-{slug}", ".mp3")
        fal_run = run_fal_once(
            desk,
            unit=_unit(f"voice-ep{episode:02d}-{slug}", arguments),
            endpoint=VOICE_ENDPOINT,
            arguments=arguments,
            fal=client,
            collect=lambda output, target=dest: download(output_url(output, "audio"), target),
        )
        paid += round(len(text) * ELEVEN_V3_USD_PER_1000_CHARS / 1000, 4)
        fal_run.path.with_suffix(".json").write_text(
            json.dumps({"line": text, "cast_id": cast_id, "voice": voice, "endpoint": VOICE_ENDPOINT,
                        "request_id": fal_run.request_id}, indent=2) + "\n",
            encoding="utf-8",
        )  # fmt: skip
        replacements.append(Replacement(text, fal_run.path, window.start, window.end))
    if not replacements:
        raise ValueError(
            f"none of {name}'s lines was heard in {source.name}; pass --words-json, or re-film only this take"
        )
    dest = replace_lines(
        source,
        next_versioned_path(takes, f"take-ep{episode:02d}-{take_id}-revoice", ".mp4"),
        tuple(replacements),
        voice_db=voice_db,
    )
    seconds = probe_video(source).duration_seconds
    record = {
        "source": source.name,
        "cast_id": cast_id,
        "voice": voice,
        "words_json": str(words_json),
        "lines": [{"line": r.line, "voice": str(r.voice), "original_window": [r.start, r.end],
                   "muted": list(r.mute(seconds))} for r in replacements],
        "not_heard": missing,
        "cost_usd": round(paid, 4),
    }  # fmt: skip
    dest.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if paid:
        book(desk, episode=episode, usd=round(paid, 4), take_id=take_id, stream=out)
    for item in replacements:
        print(f"{item.start:6.2f}-{item.end:6.2f}s  {item.line!r} -> {item.voice.name}", file=out)
    for line in missing:
        print(f"!! not heard in the take, left as filmed: {line!r}", file=out)
    print(dest, file=out)
    _note(desk, episode, f"revoice: {name} on `{source.name}` -> `{dest.name}`, {len(replacements)} line(s) "
          f"replaced in {voice}" + (f", {len(missing)} not heard" if missing else ""))  # fmt: skip
    print(
        f"Next: listen to it, then fictora-produce finish --desk {desk} --episode {episode} --take {take_id} "
        f"--take-file {dest}. If the dub does not sit (lips visibly wrong), re-film only this take, never the story.",
        file=out,
    )
    return dest


def _note(desk: Path, episode: int, body: str) -> None:
    run_dir = desk / f"ep{episode:02d}"
    if (run_dir / "run-notes.md").is_file():
        append_run_note(run_dir, body)
