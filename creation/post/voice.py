"""Change a character's voice without regenerating anything: audition, pick, revoice.

"The boy's voice is off" never means re-making the story. The voice is a lock
on the cast card, and a take that is already filmed takes a new voice in post:

- ``voice --audition``: ``POST /v1/spines/{id}/cast/{cast_id}/voice-auditions``
  compiles 4-10 Eleven v3 candidates on the character's real lines (voice ids
  and text). Each candidate is rendered on the server (the audio service; no
  provider key on this laptop) and saved to
  ``shared/voices/<cast>/audition-vN/NN-<voice>.mp3`` with ``auditions.json``.
- ``voice --pick N``: ``POST .../voice-auditions/pick`` locks candidate N on the
  cast card and saves the spine again. Takes filmed from now on use it.
- ``revoice``: for a filmed take, each line that character speaks is rendered
  dry in the locked voice on the server, its window found from Whisper words on the take,
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
from creation.post.audio_service import AudioService, DramaApiAudio, download
from creation.post.desk import (
    cast_slug,
    episode_dialogue,
    find_cast,
    latest_raw_take,
    open_api,
    refresh_spine,
    show_language,
    spine_id,
    take_stored_url,
)
from creation.post.finish import book
from creation.post.media import media_duration, probe_video, run_ffmpeg
from creation.post.whisper import line_windows, load_words, transcribe

AUDITION_SET_USD = 0.30
"""One audition set, as the runbook prices it."""
AUDITION_MAX_LINES = 3
MIN_CANDIDATES, MAX_CANDIDATES = 4, 10
ELEVEN_V3_USD_PER_1000_CHARS = 0.10
MUTE_LEAD_SECONDS = 0.08
MUTE_TAIL_SECONDS = 0.15


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
    audio: AudioService | None = None,
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
    audio
        Generated-audio service (the Drama API by default).
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
    finally:
        run.client.close()
    service = audio or DramaApiAudio(desk, episode=ledger_episode)
    folder = unfinished or desk / "shared" / "voices" / slug / f"audition-v{len(existing) + 1}"
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Auditioning {count} voices for {name} on {len(lines)} real line(s), rendered on the server", file=out)
    key = _unit(f"audition-{slug}", {"lines": lines, "count": count, "set": folder.name})
    answer = service.render_auditions(
        spine_id=spine_id(desk), cast_id=cast_id, spine_version=str(spine["spine_version"]), lines=lines,
        count=count, key=key,
    )  # fmt: skip
    made: list[Candidate] = []
    for number, candidate in enumerate(answer.get("candidates") or [], start=1):
        voice = str(candidate["voice_id"])
        url = str(candidate["audio_url"])
        path = download(url, folder / f"{number:02d}-{cast_slug(voice)}.mp3")
        seconds = float(candidate.get("seconds") or 0) or round(media_duration(path), 3)
        made.append(Candidate(number, voice, path, url, seconds))
    cost = float(answer.get("cost_usd") or AUDITION_SET_USD)
    listing = {
        "cast_id": cast_id,
        "name": name,
        "lines": lines,
        "cause": cause,
        "cost_usd": cost,
        "key": key,
        "candidates": [
            {"number": c.number, "provider_voice": c.provider_voice, "file": c.path.name, "url": c.url,
             "seconds": c.seconds}
            for c in made
        ],
    }  # fmt: skip
    (folder / "auditions.json").write_text(json.dumps(listing, indent=2) + "\n", encoding="utf-8")
    book(desk, episode=ledger_episode, usd=cost, stream=out)
    locked = str((card.get("voice_brief") or {}).get("provider_voice") or "none")
    print(f"{name}: {len(made)} candidates in {folder} (locked now: {locked})", file=out)
    for item in made:
        print(f"  {item.number:>2}. {item.provider_voice:<14} {item.seconds:5.2f}s  {item.path.name}", file=out)
    _note(desk, ledger_episode, f"voice audition: {name} ({cast_id}), {len(made)} candidates in `{folder.name}`"
          + (f"; cause: {cause}" if cause else "") + f"; cost ${cost:.3f}")  # fmt: skip
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
    audio: AudioService | None = None,
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
    audio
        Generated-audio service for the dry lines and the transcript (the Drama API by default).
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
    language = show_language(spine)
    theirs = [i for i, line in enumerate(dialogue) if line["cast_id"] == cast_id]
    if not theirs:
        raise ValueError(f"{name} speaks no line in episode {episode} on the spine; nothing to revoice")
    takes = source.parent
    service = audio or DramaApiAudio(desk, episode=episode)
    if words_json is None:
        stored = take_stored_url(desk, episode, take_id)
        if stored is None:
            raise ValueError(
                f"no stored URL for ep{episode:02d} {take_id} in api/17_raw_scene_clips.json to transcribe "
                "(this kit never uploads local files); pass --words-json"
            )
        target = next_versioned_path(takes, f"take-ep{episode:02d}-{take_id}-revoice-words", ".json")
        words_json = transcribe(stored, target, audio=service, spine_id=spine_id(desk), language=language)
    # Windows are found on what is heard: the performed line, in the show's language.
    # A line's other spellings (the script ``text``, a kana-pinned ``spoken_text``) count as heard too.
    windows = line_windows(
        load_words(words_json),
        tuple(line["performed"] for line in dialogue),
        alternates=tuple((line["text"], line["spoken_text"]) for line in dialogue),
    )
    voices_dir = desk / f"ep{episode:02d}" / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    slug = cast_slug(cast_id)
    replacements: list[Replacement] = []
    missing: list[str] = []
    unsure: list[str] = []
    paid = 0.0
    for index in theirs:
        window = windows[index]
        line = dialogue[index]
        text = line["performed"]
        if window.start is None or window.end is None:
            missing.append(text)
            continue
        key = _unit(f"voice-ep{episode:02d}-{slug}", {"text": text, "voice": voice, "language": language})
        answer = service.voice_line(
            spine_id=spine_id(desk), cast_id=cast_id, text=text, language=language, key=key,
            spoken_text=line["spoken_text"] or None,
        )  # fmt: skip
        url = str(answer["audio_url"])
        path = download(url, next_versioned_path(voices_dir, f"voice-ep{episode:02d}-{slug}", ".mp3"))
        paid += float(answer.get("cost_usd") or round(len(text) * ELEVEN_V3_USD_PER_1000_CHARS / 1000, 4))
        reading = answer.get("reading") or {}
        if reading.get("checked") and not reading.get("read_right"):
            unsure.append(text)
        path.with_suffix(".json").write_text(
            json.dumps({"line": text, "cast_id": cast_id, "voice": answer.get("provider_voice") or voice, "key": key,
                        "url": url, "reading": reading}, indent=2) + "\n",
            encoding="utf-8",
        )  # fmt: skip
        replacements.append(Replacement(text, path, window.start, window.end))
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
    for line in unsure:
        print(f"!! the new line may be misread, listen before using it: {line!r}", file=out)
    print(dest, file=out)
    _note(desk, episode, f"revoice: {name} on `{source.name}` -> `{dest.name}`, {len(replacements)} line(s) "
          f"replaced in {voice}" + (f", {len(missing)} not heard" if missing else "") + f"; cost ${paid:.3f}")  # fmt: skip
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
