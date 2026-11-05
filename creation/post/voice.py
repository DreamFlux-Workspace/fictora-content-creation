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

from creation.harness.http_util import api_error_text

from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.post.audio_service import AudioService, DramaApiAudio, download
from creation.post.desk import (
    cast_slug,
    episode_dialogue,
    find_cast,
    open_api,
    refresh_spine,
    saved_spine,
    spine_id,
)
from creation.post.finish import book
from creation.post.media import media_duration, run_ffmpeg
from creation.prices import audition_quote, audition_usd
from creation.production_state import load_production
from creation.stylised_only import server_notices, voice_notice_text
from creation.voice_gate import (
    OLD_SERVER_NOTE,
    UNREACHABLE_NOTE,
    Approvals,
    CastVoice,
    keep_on_server,
    keep_unheard_refusal,
    server_first_line_samples,
    with_samples,
    local_approvals,
    record_voice,
    render_gate,
    server_approvals,
    speaking_voices,
    sync_approvals,
    voice_name,
)

AUDITION_MAX_LINES = 3
MIN_CANDIDATES, MAX_CANDIDATES = 4, 10
ELEVEN_V3_USD_PER_1000_CHARS = 0.10
REEL_GAP_SECONDS = 0.8
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
                f'or pass --cause "..." to pay for a second set '
                f"({audition_quote([wording] if wording else lines, len(wanted) or count)})"
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
    cost = float(
        answer.get("cost_usd")
        or audition_usd([wording] if wording else lines, len(wanted) or count)
    )
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
    desk: Path,
    *,
    cast: str,
    pick: int | str,
    accept_notices: Sequence[str] = (),
    out: TextIO | None = None,
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
    SystemExit
        When the server answers the one-tap notice (409 ``brief_needs_creator_choice``:
        the voice names a listed real person) and ``accept_notices`` does not
        answer it; the text says how to go on (fictora-drama #833).
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
        if accept_notices:
            body["acknowledged_notices"] = sorted(set(accept_notices))
        route = f"/v1/spines/{spine_id(desk)}/cast/{cast_id}/voice-auditions/pick"
        status, answer = run.post_optional(route, body)
        notices = server_notices(answer) if status == 409 else None
        if notices:
            raise SystemExit(
                voice_notice_text(
                    notices, f"voice --desk {desk} --cast {cast_id} --pick {pick}"
                )
            )
        if not 200 <= status < 300:
            raise SystemExit(f"HTTP {status} POST {route}: {api_error_text(answer)}")
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
        f"fictora-produce revoice --desk {desk} --cast {cast_id} --episode N --take tK (the server re-voices it).",
        file=out,
    )
    return str(chosen["provider_voice"])


def run_voice_gate(
    desk: Path,
    *,
    cast: str | None = None,
    keep: bool = False,
    episode: int | None = None,
    heard: bool = False,
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
    heard
        ``--heard``: the human heard each voice (its sample or audition). On a desk from
        6 Oct 2026 a keep is refused without it, and for a voice with nothing to hear.
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
        # The server's first-line samples, made side by side ahead of time (fictora-drama
        # voices-card): a ready one is a voice the human can hear, like a free sample.
        samples = server_first_line_samples(
            run, str(spine.get("spine_id") or ""), episode=None if keep else episode
        )
        voices = with_samples(
            speaking_voices(spine, episode=None if keep else episode), samples
        )
        if cast is not None:
            card = find_cast(spine, cast)
            voices = [v for v in voices if v.cast_id == str(card["cast_id"])]
            if not voices:
                raise ValueError(
                    f"{card.get('name') or card['cast_id']} speaks no line on the spine; nothing to approve"
                )
        if keep and cast is None:
            # Keep all voices keeps everyone the server holds filming for too
            # (its `pending_cast_ids`, fictora-drama #1131), a character heard
            # only as an inner voice included: the line speakers alone missed them.
            from creation.voice_gate import server_held_cast_ids, voices_for_cast_ids

            held = server_held_cast_ids(
                run, str(spine.get("spine_id") or ""), episode=episode or gate_episode
            )
            listed = {voice.cast_id for voice in voices}
            extra = [
                voice
                for voice in voices_for_cast_ids(spine, held or [])
                if voice.cast_id not in listed
            ]
            voices = voices + with_samples(extra, samples)
        if not keep:
            from creation.voice_mode import MODEL_GATE_SKIPPED, show_voices

            voices_now = show_voices(run, spine)
            if not voices_now.locked:
                print(voices_now.line(), file=out)
                print(MODEL_GATE_SKIPPED, file=out)
                return []
            text = render_gate(desk, spine, approvals, episode=episode, samples=samples)
            print(
                text or "Nobody speaks a line on the spine: no voice to approve.",
                file=out,
            )
            return []
        if not voices:
            print("Nobody speaks a line on the spine: no voice to approve.", file=out)
            return []
        refused = keep_unheard_refusal(desk, voices, heard=heard)
        if refused:
            raise ValueError(refused)
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


def _note(desk: Path, episode: int, body: str) -> None:
    run_dir = desk / f"ep{episode:02d}"
    if (run_dir / "run-notes.md").is_file():
        append_run_note(run_dir, body)
