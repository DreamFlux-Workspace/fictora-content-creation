"""Change a character's voice without regenerating anything: audition, pick, revoice.

"The boy's voice is off" never means re-making the story. The voice is a lock
on the cast card, and a take that is already filmed takes a new voice in post:

- ``voice --audition``: ``POST /v1/spines/{id}/cast/{cast_id}/voice-auditions``
  compiles 4-10 Eleven v3 candidates on the character's real lines (voice ids
  and text). Each candidate is rendered on the server (the audio service; no
  provider key on this laptop) and saved to
  ``shared/voices/<cast>/audition-vN/NN-<voice>.mp3`` with ``auditions.json``,
  plus one listening reel (``reel-vN.m4a``: the candidates back to back,
  level-matched, short gaps, in candidate-number order; ``reel-vN.txt`` says
  where each number starts). ``--text "..."`` is sent as the route's
  ``text``: any wording up to 300 characters, on the spine or not (wording
  that names one of the character's lines auditions the performed line).
  ``--voices A,B`` is sent as ``voices``: exactly those Eleven v3 voices, in
  that order, instead of the server's default slate. The server checks both
  and refuses a broken rule with a named ``422`` (``voice_audition_unknown_voice``
  and siblings), printed as it says, before anything is spent. A finished set
  that already holds the wording and the named voices makes a new reel for free.
- ``voice --pick N`` (or ``--pick NAME``): ``POST .../voice-auditions/pick`` locks that candidate on the
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
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import httpx

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
    saved_spine,
    show_language,
    spine_id,
    take_stored_url,
)
from creation.post.finish import book
from creation.post.media import media_duration, probe_video, run_ffmpeg
from creation.post.whisper import line_windows, load_words, transcribe
from creation.production_state import load_production
from creation.voice_gate import (
    OLD_SERVER_NOTE,
    UNREACHABLE_NOTE,
    Approvals,
    CastVoice,
    keep_on_server,
    local_approvals,
    record_voice,
    render_gate,
    server_approvals,
    speaking_voices,
    sync_approvals,
    voice_name,
)

AUDITION_SET_USD = 0.30
"""One audition set, as the runbook prices it."""
AUDITION_MAX_LINES = 3
MIN_CANDIDATES, MAX_CANDIDATES = 4, 10
ELEVEN_V3_USD_PER_1000_CHARS = 0.10
MUTE_LEAD_SECONDS = 0.08
MUTE_TAIL_SECONDS = 0.15
REEL_GAP_SECONDS = 0.8
#: A line this many words or fewer ("Blinking!") is short: a transcript often misses it,
#: so ``revoice`` falls back to its planned window (SCP-173 Blink #97).
SHORT_LINE_WORDS = 3
REEL_LOUDNESS_LUFS = -18.0


def _unit(prefix: str, arguments: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[
        :10
    ]
    return f"{prefix}-{digest}"


def cast_lines(spine: dict[str, Any], cast_id: str, episode: int | None) -> list[str]:
    """The character's authored lines (``text``), in order, from one episode or the whole spine."""

    if episode is not None:
        lines = [
            line["text"]
            for line in episode_dialogue(spine, episode)
            if line["cast_id"] == cast_id
        ]
    else:
        lines = []
        for beat in spine.get("beats") or []:
            for line in beat.get("dialogue_lines") or []:
                if (
                    line.get("cast_id") == cast_id
                    and str(line.get("text") or "").strip()
                ):
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


def _key(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def parse_voices(raw: str | Sequence[str] | None) -> list[str]:
    """``"Rachel, aria"`` -> ``["Rachel", "aria"]``: the names as typed, in order, blanks dropped.

    Nothing else is checked here: the server resolves each name against the
    Eleven v3 catalog, drops repeats and caps the count, and refuses a broken
    rule with a named ``422`` that the command prints as it is.
    """

    items = raw.split(",") if isinstance(raw, str) else list(raw or [])
    return [name for name in (item.strip() for item in items) if name]


def _holds(
    listing: Mapping[str, Any],
    *,
    lines: Sequence[str],
    text: str | None,
    voices: Sequence[str],
) -> bool:
    """Whether a finished set already has this wording in every named voice (so a reel from it is free)."""

    if text is not None:
        held = (
            [str(listing.get("text") or "")]
            if listing.get("text")
            else list(listing.get("lines") or [])
        )
        if len(held) != 1 or _key(held[0]) != _key(text):
            return False
    elif listing.get("text") or list(listing.get("lines") or []) != list(lines):
        return False
    offered = {
        str(c["provider_voice"]).casefold() for c in listing.get("candidates") or []
    }
    return all(v.casefold() in offered for v in voices)


def build_reel(
    clips: Sequence[tuple[int, str, Path]],
    out: Path,
    *,
    gap_seconds: float = REEL_GAP_SECONDS,
) -> list[dict[str, Any]]:
    """Put audition clips back to back in one listening file: level-matched, a short gap after each.

    Parameters
    ----------
    clips
        ``(candidate number, voice, file)`` in the order they play.
    out
        Reel to write (``.m4a``); never overwritten.
    gap_seconds
        Silence after each clip.

    Returns
    -------
    list[dict[str, Any]]
        ``{number, voice, start, end}`` per clip, seconds into the reel.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    ValueError
        When there are no clips.
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; local post never overwrites")
    if not clips:
        raise ValueError("no audition clips to put in a reel")
    inputs: list[str] = []
    graph: list[str] = []
    index: list[dict[str, Any]] = []
    at = 0.0
    for n, (number, voice, path) in enumerate(clips):
        inputs += ["-i", str(path)]
        graph.append(
            f"[{n}:a]aformat=channel_layouts=mono,loudnorm=I={REEL_LOUDNESS_LUFS}:TP=-2:LRA=11,"
            f"aresample=48000,apad=pad_dur={gap_seconds}[c{n}]"
        )
        seconds = media_duration(path)
        index.append(
            {
                "number": number,
                "voice": voice,
                "start": round(at, 2),
                "end": round(at + seconds, 2),
            }
        )
        at += seconds + gap_seconds
    graph.append(
        f"{''.join(f'[c{n}]' for n in range(len(clips)))}concat=n={len(clips)}:v=0:a=1[a]"
    )
    run_ffmpeg(
        [
            *inputs,
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[a]",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(out),
        ]
    )
    return index


def _write_reel(
    folder: Path, listing: dict[str, Any], voices: Sequence[str], out: TextIO
) -> Path:
    """Build the set's reel (only ``voices`` when named, else every candidate), record it and print the index."""

    wanted = {v.casefold() for v in voices}
    chosen = [
        c
        for c in listing["candidates"]
        if not wanted or str(c["provider_voice"]).casefold() in wanted
    ]
    if not chosen:
        raise ValueError(
            "none of the named voices is in the audition set; nothing to put in a reel"
        )
    reel = next_versioned_path(folder, "reel", ".m4a")
    index = build_reel(
        [
            (int(c["number"]), str(c["provider_voice"]), folder / c["file"])
            for c in chosen
        ],
        reel,
    )
    lines = [
        f"{i['number']:>2}. {i['voice']:<12} {i['start']:6.2f}-{i['end']:6.2f}s"
        for i in index
    ]
    reel.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    listing.setdefault("reels", []).append(
        {"file": reel.name, "voices": [i["voice"] for i in index], "index": index}
    )
    (folder / "auditions.json").write_text(
        json.dumps(listing, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Listening reel: {reel}", file=out)
    for line in lines:
        print(f"  {line}", file=out)
    return reel


def run_voice_audition(
    desk: Path,
    *,
    cast: str,
    episode: int | None = None,
    count: int = 8,
    cause: str | None = None,
    text: str | None = None,
    voices: Sequence[str] | str | None = None,
    audio: AudioService | None = None,
    out: TextIO | None = None,
) -> Path:
    """Audition voices for one character; save candidates, a listing and a listening reel.

    Parameters
    ----------
    desk
        Series desk bound to a spine.
    cast
        ``cast_id`` or name.
    episode
        Take the audition lines from this episode only (ignored with ``text``).
    count
        Candidates from the server's default slate, 4-10 (ignored with ``voices``).
    cause
        Why a second audition set is paid for (required when one exists).
    text
        New wording, sent as the route's ``text`` instead of the character's
        lines. It need not be on the spine; the server caps its length, checks
        its language and auditions the performed line when it names one.
    voices
        Names (or ``"A,B"``), sent as the route's ``voices``: exactly these
        Eleven v3 voices, in this order, instead of the default slate. The
        server resolves and checks them; an unknown name is its named ``422``.
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
        Count out of range, no lines for the character (without ``text``), or a
        second set without a cause.
    AudioServiceError
        The server refused the wording or the voices (its named ``422``, printed as it is).
    """

    out = out or sys.stdout
    wanted = parse_voices(voices)
    wording = " ".join(text.split()) if text is not None and text.strip() else None
    if not wanted and not MIN_CANDIDATES <= count <= MAX_CANDIDATES:
        raise ValueError(f"--count must be {MIN_CANDIDATES}-{MAX_CANDIDATES}")
    ledger_episode = episode or 1
    run = open_api(desk, ledger_episode)
    try:
        spine = refresh_spine(run, desk, ledger_episode)
        card = find_cast(spine, cast)
        cast_id, name = str(card["cast_id"]), str(card.get("name") or card["cast_id"])
        slug = cast_slug(cast_id)
        lines = (
            [] if wording else cast_lines(spine, cast_id, episode)[:AUDITION_MAX_LINES]
        )
        if not wording and not lines:
            where = f" in episode {episode}" if episode else ""
            raise ValueError(
                f'{name} speaks no line{where} on the spine; audition new wording with --text "..." instead'
            )
        existing = audition_dirs(desk, slug)
        unfinished = (
            existing[-1]
            if existing and not (existing[-1] / "auditions.json").is_file()
            else None
        )
        finished = [path for path in existing if path != unfinished]
        if wanted:
            for held in reversed(finished):
                listing = json.loads(
                    (held / "auditions.json").read_text(encoding="utf-8")
                )
                if _holds(listing, lines=lines, text=wording, voices=wanted):
                    print(f"{name}: {held.name} already holds this wording in these voices; a new reel, nothing paid",
                          file=out)  # fmt: skip
                    _write_reel(held, listing, wanted, out)
                    return held
        if finished and not (cause and cause.strip()):
            raise ValueError(
                f"{name} already has an audition set ({finished[-1].name}); pick from it with --pick N, "
                f'or pass --cause "..." to pay for a second set (${AUDITION_SET_USD:.2f})'
            )
    finally:
        run.client.close()
    service = audio or DramaApiAudio(desk, episode=ledger_episode)
    folder = (
        unfinished
        or desk / "shared" / "voices" / slug / f"audition-v{len(existing) + 1}"
    )
    folder.mkdir(parents=True, exist_ok=True)
    who = f"{len(wanted)} named voice(s)" if wanted else f"{count} voices"
    what = "new wording" if wording else f"{len(lines)} real line(s)"
    print(f"Auditioning {who} for {name} on {what}, rendered on the server", file=out)
    asked: dict[str, Any] = {"set": folder.name}
    asked.update({"text": wording} if wording else {"lines": lines})
    asked.update({"voices": wanted} if wanted else {"count": count})
    key = _unit(f"audition-{slug}", asked)
    answer = service.render_auditions(
        spine_id=spine_id(desk), cast_id=cast_id, spine_version=str(spine["spine_version"]), lines=lines,
        count=count, key=key, text=wording, voices=wanted or None,
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
        **({"text": wording} if wording else {}),
        **({"voices": wanted} if wanted else {}),
        "cause": cause,
        "cost_usd": cost,
        "key": key,
        "candidates": [
            {"number": c.number, "provider_voice": c.provider_voice, "file": c.path.name, "url": c.url,
             "seconds": c.seconds}
            for c in made
        ],
    }  # fmt: skip
    (folder / "auditions.json").write_text(
        json.dumps(listing, indent=2) + "\n", encoding="utf-8"
    )
    book(
        desk,
        episode=ledger_episode,
        usd=cost,
        stream=out,
        unit=f"voice-audition:{slug}",
    )
    if made:
        # The server read exactly the voices asked for (a retired name comes back as its stand-in): all go in.
        _write_reel(folder, listing, [], out)
    locked = str((card.get("voice_brief") or {}).get("provider_voice") or "none")
    print(
        f"{name}: {len(made)} candidates in {folder} (locked now: {locked})", file=out
    )
    for item in made:
        print(
            f"  {item.number:>2}. {item.provider_voice:<14} {item.seconds:5.2f}s  {item.path.name}",
            file=out,
        )
    _note(desk, ledger_episode, f"voice audition: {name} ({cast_id}), {len(made)} candidates in `{folder.name}`"
          + (f' on new wording "{wording}"' if wording else "")
          + (f"; cause: {cause}" if cause else "") + f"; cost ${cost:.3f}")  # fmt: skip
    print(f"Next: play the reel to the human. Their pick: fictora-produce voice --desk {desk} --cast {cast_id} "
          "--pick N (or the voice's name)", file=out)  # fmt: skip
    return folder


def run_voice_pick(
    desk: Path, *, cast: str, pick: int | str, out: TextIO | None = None
) -> str:
    """Lock audition candidate ``pick`` (its number, or its voice's name) on the character's cast card (spends nothing).

    Returns
    -------
    str
        The locked provider voice.

    Raises
    ------
    FileNotFoundError
        When the character has no finished audition set on the desk.
    ValueError
        When ``pick`` is not a candidate number or a voice in the newest set.
    """

    out = out or sys.stdout
    run = open_api(desk, 1)
    try:
        spine = refresh_spine(run, desk, 1)
        card = find_cast(spine, cast)
        cast_id, name = str(card["cast_id"]), str(card.get("name") or card["cast_id"])
        listed = [
            p / "auditions.json"
            for p in audition_dirs(desk, cast_slug(cast_id))
            if (p / "auditions.json").is_file()
        ]
        if not listed:
            raise FileNotFoundError(
                f"{name} has no audition set on the desk; run voice --cast {cast_id} --audition"
            )
        listing = json.loads(listed[-1].read_text(encoding="utf-8"))
        wanted = str(pick).strip()
        chosen = next(
            (c for c in listing["candidates"]
             if (wanted.isdigit() and int(c["number"]) == int(wanted))
             or str(c["provider_voice"]).casefold() == wanted.casefold()),
            None,
        )  # fmt: skip
        if chosen is None:
            numbers = ", ".join(
                f"{c['number']} {c['provider_voice']}" for c in listing["candidates"]
            )
            raise ValueError(
                f"no candidate {pick} in {listed[-1].parent.name}; the set is {numbers}"
            )
        pick = int(chosen["number"])
        body = {
            "spine_version": spine["spine_version"],
            "url": chosen["url"],
            "provider_voice": chosen["provider_voice"],
            "seconds": chosen["seconds"],
        }
        run.post(
            f"/v1/spines/{spine_id(desk)}/cast/{cast_id}/voice-auditions/pick", body
        )
        after = refresh_spine(run, desk, 1)
        # The human picked it: that is their yes on this voice for the voices gate. The server's pick
        # records it (fictora-drama #603); the desk mirrors it, and pushes it later on an older server.
        # The card's voice after the pick (the server may lock a retired name's stand-in).
        picked = voice_name(
            (find_cast(after, cast_id).get("voice_brief") or {}).get("provider_voice")
            or chosen["provider_voice"]
        )
        on_server = (server_approvals(after).get(cast_id) or {}).get(
            "provider_voice"
        ) == picked
        record_voice(
            desk, CastVoice(cast_id, name, picked), how="picked", on_server=on_server
        )
        if not on_server:
            print(OLD_SERVER_NOTE, file=sys.stderr)
    finally:
        run.client.close()
    print(
        f"{name} now speaks as {chosen['provider_voice']} (candidate {pick}, {listed[-1].parent.name})",
        file=out,
    )
    _note(
        desk, 1, f"voice pick: {name} -> {chosen['provider_voice']} (candidate {pick})."
    )
    print(
        "Next: takes filmed from now on use the new voice. For a take already filmed: "
        f"fictora-produce revoice --desk {desk} --cast {cast_id} --episode N --take tK, then finish --take-file.",
        file=out,
    )
    return str(chosen["provider_voice"])


def run_voice_gate(
    desk: Path,
    *,
    cast: str | None = None,
    keep: bool = False,
    episode: int | None = None,
    out: TextIO | None = None,
) -> list[str]:
    """List the speaking characters' voices, or record the human's yes on them as they are (spends nothing).

    The yes goes to the server (``POST /v1/spines/{id}/voice-approvals``) and is
    mirrored on the desk. On a server without the route, or one that can't be
    reached (the saved spine is read then), it is kept on the desk and pushed
    when the server can take it; the command says so.

    Parameters
    ----------
    desk
        Series desk bound to a spine.
    cast
        ``cast_id`` or name: keep (or list) this character alone. ``None``: every speaking character.
    keep
        Record the yes (``voice --keep`` / ``--keep-all``); without it the gate is only printed.
    episode
        List the speakers of this episode only (``--list``); a keep covers the whole spine.
    out
        Text stream.

    Returns
    -------
    list[str]
        The cast ids kept (empty for a listing).

    Raises
    ------
    ValueError
        When ``cast`` speaks no line on the spine.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    ledger_episode = episode or load_production(desk).episode_ordinal
    gate_episode = load_production(desk).episode_ordinal
    run = open_api(desk, ledger_episode)
    try:
        try:
            spine = refresh_spine(run, desk, ledger_episode)
            spine, approvals = sync_approvals(desk, run, spine)
        except httpx.TransportError as exc:
            saved = saved_spine(desk, ledger_episode) or saved_spine(desk, gate_episode)
            if saved is None:
                raise
            spine = saved[0]
            approvals = local_approvals(
                desk, UNREACHABLE_NOTE.format(error=type(exc).__name__)
            )
            print(approvals.note, file=sys.stderr)
        voices = speaking_voices(spine, episode=None if keep else episode)
        if cast is not None:
            card = find_cast(spine, cast)
            voices = [v for v in voices if v.cast_id == str(card["cast_id"])]
            if not voices:
                raise ValueError(
                    f"{card.get('name') or card['cast_id']} speaks no line on the spine; nothing to approve"
                )
        if not keep:
            text = render_gate(desk, spine, approvals, episode=episode)
            print(
                text or "Nobody speaks a line on the spine: no voice to approve.",
                file=out,
            )
            return []
        if not voices:
            print("Nobody speaks a line on the spine: no voice to approve.", file=out)
            return []
        note = approvals.note
        if note is None:
            kept, note = keep_on_server(run, desk, spine, [v.cast_id for v in voices])
            if kept is not None:
                from creation.orchestrate import save_spine_snapshot

                spine = kept
                save_spine_snapshot(desk, ledger_episode, spine)
        for voice in voices:
            record_voice(desk, voice, how="kept", on_server=note is None)
            print(f"{voice.name} keeps {voice.provider_voice or 'no locked voice'} (the human's yes is recorded"
                  + (" on the server)." if note is None else " on this desk only).") , file=out)  # fmt: skip
        if note is not None and approvals.note is None:
            print(note, file=out)
        approvals = (
            local_approvals(desk, note)
            if note is not None
            else Approvals(approvals.grandfathered, server_approvals(spine), "server")
        )
    finally:
        run.client.close()
    _note(desk, ledger_episode, "voices kept: " + ", ".join(
        f"{v.name} -> {v.provider_voice or 'none'}" for v in voices))  # fmt: skip
    rest = render_gate(desk, spine, approvals, episode=gate_episode)
    if rest:
        print(rest, file=out)
    return [v.cast_id for v in voices]


def is_short_line(text: str) -> bool:
    """Whether a line is short enough that a transcript may miss it (``SHORT_LINE_WORDS`` words or fewer)."""

    words = [word for word in text.split() if any(ch.isalnum() for ch in word)]
    return 0 < len(words) <= SHORT_LINE_WORDS


def planned_line_window(
    facts: Mapping[str, Any] | None, line_id: str
) -> tuple[float, float, str] | None:
    """Where the take facts plan a line, when the transcript cannot find it.

    The locked-voice track's own window (``soundtrack.lines[].start_s/end_s``) comes
    first; then the window of the shot the line was written into
    (``lines[].start_seconds/end_seconds``, the planned shot timing). Seconds are on
    the take as filmed.

    Parameters
    ----------
    facts
        A saved take-facts file (``{"take_facts": {...}}`` or the facts alone), or ``None``.
    line_id
        The spine's ``line_id``.

    Returns
    -------
    tuple[float, float, str] | None
        ``(start, end, where it came from)``, or ``None`` when the facts plan no window for it.
    """

    if not facts or not line_id:
        return None
    body = facts.get("take_facts", facts)
    if not isinstance(body, Mapping):
        return None
    soundtrack = body.get("soundtrack")
    sources = (
        (
            soundtrack.get("lines") if isinstance(soundtrack, Mapping) else None,
            "start_s",
            "end_s",
            "the soundtrack's line window",
        ),
        (body.get("lines"), "start_seconds", "end_seconds", "the planned shot timing"),
    )
    for items, start_key, end_key, label in sources:
        for item in items if isinstance(items, list) else ():
            if (
                not isinstance(item, Mapping)
                or str(item.get("line_id") or "") != line_id
            ):
                continue
            start, end = item.get(start_key), item.get(end_key)
            if (
                isinstance(start, (int, float))
                and isinstance(end, (int, float))
                and not isinstance(start, bool)
                and 0 <= start < end
            ):
                return round(float(start), 3), round(float(end), 3), label
    return None


@dataclass(frozen=True)
class Replacement:
    """One original line replaced by a dry line."""

    line: str
    voice: Path
    start: float
    end: float

    def mute(self, take_seconds: float) -> tuple[float, float]:
        """The window silenced on the take."""

        return (
            max(0.0, self.start - MUTE_LEAD_SECONDS),
            min(take_seconds, self.end + MUTE_TAIL_SECONDS),
        )


def replace_lines(
    take: Path,
    out: Path,
    replacements: tuple[Replacement, ...],
    *,
    voice_db: float = 0.0,
) -> Path:
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
        f"volume=0:enable='between(t,{a:.3f},{b:.3f})',"
        for a, b in (r.mute(seconds) for r in replacements)
    )
    graph = [f"[0:a]aresample=48000,{mutes}anull[orig]"]
    labels = ["[orig]"]
    inputs: list[str] = []
    for number, item in enumerate(replacements, start=1):
        inputs += ["-i", str(item.voice)]
        delay = round(item.start * 1000)
        graph.append(
            f"[{number}:a]aresample=48000,volume={voice_db:+.1f}dB,adelay={delay}|{delay}[v{number}]"
        )
        labels.append(f"[v{number}]")
    graph.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first[a]"
    )
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
    over_locked_voices: bool = False,
) -> Path:
    """Re-voice one character's lines on a filmed take in their locked voice.

    A take whose facts say its sound is already the show's locked voices
    (``soundtrack.mode == "target_audio"``) is refused before anything is spent,
    unless ``over_locked_voices`` (then it is warned about and revoiced).

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
    over_locked_voices
        ``--over-locked-voices``: revoice a locked-voice take anyway (a voice picked after it was filmed).

    Returns
    -------
    Path
        ``take-epNN-tK-revoice-vN.mp4``.

    Raises
    ------
    ValueError
        No locked voice, no line for the character, none of their lines heard, or
        the take's sound is already the locked voices (without ``over_locked_voices``).
    """

    from creation.post.soundtrack import locked_voice_refusal, saved_soundtrack

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    soundtrack = saved_soundtrack(desk, episode, take_id)
    if soundtrack.target_audio:
        warning = locked_voice_refusal(
            soundtrack, f"revoice of ep{episode:02d} {take_id}"
        )
        if not over_locked_voices:
            raise ValueError(warning)
        print(f"{warning} (given: revoicing anyway)", file=out)
    source = (
        take_file.expanduser().resolve()
        if take_file
        else latest_raw_take(desk, episode, take_id)
    )
    run = open_api(desk, episode)
    try:
        spine = refresh_spine(run, desk, episode)
    finally:
        run.client.close()
    card = find_cast(spine, cast)
    cast_id, name = str(card["cast_id"]), str(card.get("name") or card["cast_id"])
    voice = str((card.get("voice_brief") or {}).get("provider_voice") or "").strip()
    if not voice:
        raise ValueError(
            f"{name} has no locked voice on the cast card; run voice --audition then --pick N first"
        )
    dialogue = episode_dialogue(spine, episode)
    language = show_language(spine)
    theirs = [i for i, line in enumerate(dialogue) if line["cast_id"] == cast_id]
    if not theirs:
        raise ValueError(
            f"{name} speaks no line in episode {episode} on the spine; nothing to revoice"
        )
    takes = source.parent
    service = audio or DramaApiAudio(desk, episode=episode)
    if words_json is None:
        stored = take_stored_url(desk, episode, take_id)
        if stored is None:
            raise ValueError(
                f"no stored URL for ep{episode:02d} {take_id} in api/ (17_raw_scene_clips.json or film-*-raw-scene-clips.json) "
                "to transcribe "
                "(this kit never uploads local files); pass --words-json"
            )
        target = next_versioned_path(
            takes, f"take-ep{episode:02d}-{take_id}-revoice-words", ".json"
        )
        words_json = transcribe(
            stored, target, audio=service, spine_id=spine_id(desk), language=language
        )
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
    planned: list[str] = []
    facts: dict[str, Any] | None = None
    paid = 0.0
    for index in theirs:
        window = windows[index]
        line = dialogue[index]
        text = line["performed"]
        start, end = window.start, window.end
        if start is None or end is None:
            # A one-word line ("Blinking!") is often not in the transcript at all:
            # place it where the take facts planned it, and say so.
            if facts is None:
                facts = _saved_facts(desk, episode, take_id)
            fallback = (
                planned_line_window(facts, line["line_id"])
                if is_short_line(text)
                else None
            )
            if fallback is None:
                missing.append(text)
                continue
            start, end, where = fallback
            planned.append(
                f"{text!r}: not in the transcript; used its planned window {start:.2f}-{end:.2f}s "
                f"({where}). Listen: the dub may sit early or late"
            )
        key = _unit(
            f"voice-ep{episode:02d}-{slug}",
            {"text": text, "voice": voice, "language": language},
        )
        answer = service.voice_line(
            spine_id=spine_id(desk), cast_id=cast_id, text=text, language=language, key=key,
            spoken_text=line["spoken_text"] or None,
        )  # fmt: skip
        url = str(answer["audio_url"])
        path = download(
            url,
            next_versioned_path(voices_dir, f"voice-ep{episode:02d}-{slug}", ".mp3"),
        )
        paid += float(
            answer.get("cost_usd")
            or round(len(text) * ELEVEN_V3_USD_PER_1000_CHARS / 1000, 4)
        )
        reading = answer.get("reading") or {}
        if reading.get("checked") and not reading.get("read_right"):
            unsure.append(text)
        path.with_suffix(".json").write_text(
            json.dumps({"line": text, "cast_id": cast_id, "voice": answer.get("provider_voice") or voice, "key": key,
                        "url": url, "reading": reading}, indent=2) + "\n",
            encoding="utf-8",
        )  # fmt: skip
        replacements.append(Replacement(text, path, start, end))
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
        "planned_window": planned,
        "cost_usd": round(paid, 4),
    }  # fmt: skip
    dest.with_suffix(".json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    if paid:
        book(
            desk,
            episode=episode,
            usd=round(paid, 4),
            take_id=take_id,
            stream=out,
            unit=f"revoice:{slug}",
        )
    for item in replacements:
        print(
            f"{item.start:6.2f}-{item.end:6.2f}s  {item.line!r} -> {item.voice.name}",
            file=out,
        )
    for line in planned:
        print(f"!! {line}", file=out)
    for line in missing:
        print(f"!! not heard in the take, left as filmed: {line!r}", file=out)
    for line in unsure:
        print(
            f"!! the new line may be misread, listen before using it: {line!r}",
            file=out,
        )
    print(dest, file=out)
    _note(desk, episode, f"revoice: {name} on `{source.name}` -> `{dest.name}`, {len(replacements)} line(s) "
          f"replaced in {voice}" + (f", {len(planned)} on the planned window" if planned else "")
          + (f", {len(missing)} not heard" if missing else "") + f"; cost ${paid:.3f}")  # fmt: skip
    print(
        f"Next: listen to it, then fictora-produce finish --desk {desk} --episode {episode} --take {take_id} "
        f"--take-file {dest}. If the dub does not sit (lips visibly wrong), re-film only this take, never the story.",
        file=out,
    )
    return dest


def _saved_facts(desk: Path, episode: int, take_id: str) -> dict[str, Any]:
    """The newest saved take facts for the take (empty when none are saved)."""

    from creation.post.sfx import saved_take_facts

    path = saved_take_facts(desk, episode, take_id)
    if path is None:
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _note(desk: Path, episode: int, body: str) -> None:
    run_dir = desk / f"ep{episode:02d}"
    if (run_dir / "run-notes.md").is_file():
        append_run_note(run_dir, body)
