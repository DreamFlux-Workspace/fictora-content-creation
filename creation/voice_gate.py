"""The voices gate: every speaking character's voice is heard and approved (or kept) before filming.

It applies only to a show whose voice mode is ``locked`` (:mod:`creation.voice_mode`).
On a ``model`` show the video model voices each character from the cast card's
voice description: there is no locked voice to hear, so the gate prints that it
is skipped and filming is not held for it.

The server picks a voice for each character at draft time (the cast card's
``voice_brief.provider_voice``) and takes film in it. Nobody heard it first, so
the gate sits after the plates yes: ``step`` lists each speaking character's
voice and offers ``voice --audition``; the human keeps each one
(``voice --cast NAME --keep`` / ``voice --keep-all``) or swaps it
(``voice --audition``, then ``--pick N``). Filming (``step --confirm-spend``,
``film --confirm-spend``) is refused until every character who speaks in the
episode has a voice the human approved; a voice that changed after its yes
(a new pick, a new cast card) needs a new one.

The yeses live on the server, on the spine (``spine.voice_approvals``,
fictora-drama #603), so the kit and the creator app read the same ones. A keep
is ``POST /v1/spines/{id}/voice-approvals`` (it records each card's voice as it
is now); a pick records its own yes on the server through
``voice-auditions/pick``. Each record names the voice it was given for.

The desk's ``shared/voices/approvals.json`` (where the yeses lived before) is
now a mirror and a fallback:

- **Migration.** A yes recorded only on the desk is pushed to the server once,
  and only while the card still locks the voice it was given for (the keep
  route records the card's current voice, so a stale yes is never pushed).
- **Fallback.** When the server has no keep route yet (an older deploy: a bare
  404) or cannot be reached, the gate reads the desk file and says so; a keep
  is written to the desk file and pushed when the server can take it.
- **Grandfathering** stays on the desk: a desk that had filmed takes before
  this gate existed (no approvals file yet) is never blocked, and says so.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from creation.harness.http_util import api_error_text
from creation.post.desk import episode_dialogue, spine_body

APPROVALS_PATH = Path("shared") / "voices" / "approvals.json"
AUDITION_USD = 0.30
#: The server's keep route (fictora-drama #603): records each named card's current voice as kept.
APPROVALS_ROUTE = "/v1/spines/{spine_id}/voice-approvals"
#: The keep route takes one to four characters per call.
KEEP_BATCH = 4
OLD_SERVER_NOTE = (
    "Note: this Drama API has no voice-approvals route yet (an older deploy, before fictora-drama #603), "
    f"so voice approvals are read from and kept in this desk's {APPROVALS_PATH.as_posix()}. "
    "They go to the server once it has the route."
)
UNREACHABLE_NOTE = (
    "Note: the Drama API could not be reached ({error}), so voice approvals are read from and kept in "
    f"this desk's {APPROVALS_PATH.as_posix()}. They go to the server once it answers."
)


class _Api(Protocol):
    """The slice of :class:`creation.harness.session.DramaApiRunSession` the gate uses."""

    def spine(self, spine_id: str) -> dict[str, Any]: ...

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]: ...


def voice_name(value: object) -> str:
    """A voice as the gate compares it: blank and ``unspecified`` both mean no voice (``""``), as on the server."""

    text = str(value or "").strip()
    return "" if text.lower() == "unspecified" else text


@dataclass(frozen=True)
class CastVoice:
    """One speaking character and the voice locked on their cast card."""

    cast_id: str
    name: str
    #: ``voice_brief.provider_voice``; ``""`` when the card locks none (the take then speaks in the model's own voice).
    provider_voice: str
    #: ``voice_brief.seedance_vocal_signature``: the server's short words for the voice, when it has them.
    description: str | None = None
    #: ``voice_brief.reference_audio_url``: a sample of the locked voice, when one was rendered.
    sample_url: str | None = None


def speaking_voices(
    spine: Mapping[str, Any], *, episode: int | None = None
) -> list[CastVoice]:
    """Every character who speaks a line (in ``episode``, or anywhere), in cast order.

    Parameters
    ----------
    spine
        Spine JSON (a ``{"spine": {...}}`` answer is unwrapped).
    episode
        Episode ordinal, or ``None`` for the whole spine.

    Returns
    -------
    list[CastVoice]
        One row per speaking character.
    """

    body = spine_body(spine)
    if episode is not None:
        speakers = [line["cast_id"] for line in episode_dialogue(body, episode)]
    else:
        speakers = [
            str(line.get("cast_id") or "")
            for beat in body.get("beats") or []
            if isinstance(beat, Mapping)
            for line in beat.get("dialogue_lines") or []
            if isinstance(line, Mapping) and str(line.get("text") or "").strip()
        ]
    wanted = [cast_id for cast_id in dict.fromkeys(speakers) if cast_id]
    cards = {
        str(card.get("cast_id") or ""): card
        for card in body.get("cast") or []
        if isinstance(card, Mapping)
    }
    order = {cast_id: index for index, cast_id in enumerate(cards)}
    wanted.sort(key=lambda cast_id: order.get(cast_id, len(order)))
    rows: list[CastVoice] = []
    for cast_id in wanted:
        card = cards.get(cast_id) or {}
        brief = card.get("voice_brief") or {}
        brief = brief if isinstance(brief, Mapping) else {}
        rows.append(
            CastVoice(
                cast_id=cast_id,
                name=str(card.get("name") or cast_id),
                provider_voice=voice_name(brief.get("provider_voice")),
                description=str(brief.get("seedance_vocal_signature") or "").strip()
                or None,
                sample_url=str(brief.get("reference_audio_url") or "").strip() or None,
            )
        )
    return rows


def desk_has_filmed(desk: Path) -> bool:
    """Whether any take on the desk was filmed (a raw take file, or a take the desk counts as filmed)."""

    from creation.ops.state import load_series

    if any(desk.glob("ep*/takes/take-ep*-raw-v*.mp4")):
        return True
    try:
        series = load_series(desk)
    except (FileNotFoundError, ValueError):
        return False
    return any(
        take.filmed_count for episode in series.episodes for take in episode.takes
    )


def load_approvals(desk: Path) -> dict[str, Any]:
    """The desk's voice yeses: ``{"grandfathered": bool, "voices": {cast_id: {...}}}``.

    A desk with no approvals file that already filmed a take is grandfathered,
    and that is written down the first time it is read, so the desk stays
    grandfathered for every later episode. A desk that has not filmed gets its
    file (not grandfathered) the first time the gate reads it, before anything
    can film: the yeses themselves live on the server now, so the file's
    existence, not a desk keep, is what keeps a gated desk from later reading
    as grandfathered by its own takes.
    """

    desk = desk.expanduser().resolve()
    path = desk / APPROVALS_PATH
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("grandfathered", False)
        data.setdefault("voices", {})
        return data
    data: dict[str, Any] = {"grandfathered": False, "voices": {}}
    if desk_has_filmed(desk):
        data["grandfathered"] = True
        data["note"] = (
            "Takes were filmed before the voices gate existed: filming is not held for voice approval."
        )
    _save(desk, data)
    return data


def _save(desk: Path, data: Mapping[str, Any]) -> None:
    path = desk / APPROVALS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def record_voice(
    desk: Path, voice: CastVoice, *, how: str, on_server: bool = False
) -> None:
    """Mirror the human's yes on ``voice`` on the desk (``how``: ``kept`` or ``picked``).

    ``on_server`` says the server holds the same yes; a record without it is
    pushed to the server by :func:`sync_approvals` (once) while its voice is
    still the card's.
    """

    desk = desk.expanduser().resolve()
    data = load_approvals(desk)
    data["voices"][voice.cast_id] = {
        "name": voice.name,
        "provider_voice": voice.provider_voice,
        "how": how,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "on_server": on_server,
    }
    _save(desk, data)


@dataclass(frozen=True)
class Approvals:
    """The yeses the gate reads: the server's (the truth), or the desk file's when the server can't answer."""

    grandfathered: bool
    #: ``{cast_id: {"provider_voice": str, "how": str, ...}}``.
    voices: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    source: Literal["server", "local"] = "server"
    #: Why the desk file was read instead of the server (``None`` when the server answered).
    note: str | None = None

    def approved(self, voice: CastVoice) -> bool:
        """Whether the human said yes to this character's current voice."""

        record = self.voices.get(voice.cast_id)
        return record is not None and voice_name(
            record.get("provider_voice")
        ) == voice_name(voice.provider_voice)


def server_approvals(spine: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The spine's ``voice_approvals`` by cast id (empty on a spine with none, or from an older server)."""

    records: dict[str, dict[str, Any]] = {}
    for item in spine_body(spine).get("voice_approvals") or []:
        if isinstance(item, Mapping) and item.get("cast_id"):
            records[str(item["cast_id"])] = {
                "provider_voice": voice_name(item.get("provider_voice")),
                "how": str(item.get("how") or "kept"),
                "at": item.get("approved_at"),
            }
    return records


def _card_voices(spine: Mapping[str, Any]) -> dict[str, str]:
    cards = [c for c in spine_body(spine).get("cast") or [] if isinstance(c, Mapping)]
    return {
        str(card.get("cast_id") or ""): voice_name(
            (card.get("voice_brief") or {}).get("provider_voice")
            if isinstance(card.get("voice_brief"), Mapping)
            else ""
        )
        for card in cards
    }


def _error_code(answer: Any) -> str:
    error = answer.get("error") if isinstance(answer, Mapping) else None
    return str(error.get("code") or "") if isinstance(error, Mapping) else ""


def keep_on_server(
    run: _Api, desk: Path, spine: Mapping[str, Any], cast_ids: Sequence[str]
) -> tuple[dict[str, Any] | None, str | None]:
    """Keep these characters' current voices on the server (``POST …/voice-approvals``, free).

    Parameters
    ----------
    run
        Open API session.
    desk
        Series desk (its ``spine_id`` when the spine does not carry one).
    spine
        The spine the human was looking at.
    cast_ids
        Characters whose voice is kept.

    Returns
    -------
    tuple[dict[str, Any] | None, str | None]
        ``(spine after the keep, None)``, or ``(None, note)`` when the server is
        older than the route (a bare 404) or cannot be reached: the caller then
        keeps the yes on the desk.

    Raises
    ------
    RuntimeError
        Any other refusal (an unknown character, a version conflict twice in a row).
    """

    body = spine_body(spine)
    sid = str(body.get("spine_id") or "")
    if not sid:
        from creation.post.desk import spine_id as desk_spine_id

        sid = desk_spine_id(desk)
    path = APPROVALS_ROUTE.format(spine_id=sid)
    wanted = list(dict.fromkeys(cast_ids))
    for start in range(0, len(wanted), KEEP_BATCH):
        chunk = wanted[start : start + KEEP_BATCH]
        for attempt in (1, 2):
            payload = {"spine_version": body.get("spine_version"), "cast_ids": chunk}
            try:
                status, answer = run.post_optional(path, payload)
            except httpx.TransportError as exc:
                return None, UNREACHABLE_NOTE.format(error=type(exc).__name__)
            if 200 <= status < 300:
                kept = spine_body(answer) if isinstance(answer, Mapping) else {}
                body = kept if kept.get("cast") else spine_body(run.spine(sid))
                break
            code = _error_code(answer)
            if status in (404, 405) and not code:
                return None, OLD_SERVER_NOTE
            if status == 409 and code == "spine_version_conflict" and attempt == 1:
                body = spine_body(run.spine(sid))
                continue
            raise RuntimeError(
                f"the server refused the voice approvals (HTTP {status}): {api_error_text(answer)}"
            )
    return body, None


def sync_approvals(
    desk: Path, run: _Api, spine: Mapping[str, Any]
) -> tuple[dict[str, Any], Approvals]:
    """Push desk-only yeses to the server once, then read the server's.

    A desk record is pushed when it is not marked ``on_server``, the card still
    locks the voice it was given for, and the server has no yes on that voice.
    When the server is too old for the keep route or cannot be reached, the
    desk file is read instead and the note is printed (stderr).

    Returns
    -------
    tuple[dict[str, Any], Approvals]
        The spine (re-read after a push) and the yeses to gate on.
    """

    desk = desk.expanduser().resolve()
    data = load_approvals(desk)
    body = spine_body(spine)
    current = _card_voices(body)
    server = server_approvals(body)
    push = [
        cast_id
        for cast_id, record in data["voices"].items()
        if not record.get("on_server")
        and cast_id in current
        and voice_name(record.get("provider_voice")) == current[cast_id]
        and (server.get(cast_id) or {}).get("provider_voice") != current[cast_id]
    ]
    if push:
        kept, note = keep_on_server(run, desk, body, push)
        if kept is None:
            print(note, file=sys.stderr)
            return body, Approvals(
                bool(data["grandfathered"]), data["voices"], "local", note
            )
        body = kept
        for cast_id in push:
            data["voices"][cast_id]["on_server"] = True
        _save(desk, data)
        server = server_approvals(body)
    return body, Approvals(bool(data["grandfathered"]), server, "server")


def local_approvals(desk: Path, note: str | None = None) -> Approvals:
    """The desk file's yeses, for when the server cannot answer."""

    data = load_approvals(desk.expanduser().resolve())
    return Approvals(bool(data["grandfathered"]), data["voices"], "local", note)


def pending_voices(
    spine: Mapping[str, Any], approvals: Approvals, *, episode: int | None = None
) -> list[CastVoice]:
    """Speaking characters whose current voice has no yes (never approved, or changed since)."""

    return [
        voice
        for voice in speaking_voices(spine, episode=episode)
        if not approvals.approved(voice)
    ]


def _voice_words(voice: CastVoice) -> str:
    words = (
        voice.provider_voice
        or "no voice locked (the take would use the video model's own voice)"
    )
    if voice.description:
        words += f": {voice.description}"
    return words


def voice_rows(
    spine: Mapping[str, Any], approvals: Approvals, *, episode: int | None = None
) -> list[str]:
    """One printed row per speaking character: name, voice, its words, approved or not."""

    rows: list[str] = []
    for voice in speaking_voices(spine, episode=episode):
        mark = (
            f"approved ({approvals.voices[voice.cast_id].get('how', 'kept')})"
            if approvals.approved(voice)
            else "needs a yes"
        )
        rows.append(
            f"  - {voice.name} ({voice.cast_id}): {_voice_words(voice)} [{mark}]"
        )
        if voice.sample_url:
            rows.append(f"      sample: {voice.sample_url}")
    return rows


def _commands(desk: Path, pending: list[CastVoice]) -> list[str]:
    lines = [
        "Play each voice to the human (the sample, or an audition), then for each character:",
    ]
    for voice in pending:
        lines.append(
            f"  {voice.name}: keep it: fictora-produce voice --desk {desk} --cast {voice.cast_id} --keep"
        )
        lines.append(
            f"  {voice.name}: hear others (${AUDITION_USD:.2f}, only after the human's yes): "
            f"fictora-produce voice --desk {desk} --cast {voice.cast_id} --audition, "
            f"then fictora-produce voice --desk {desk} --cast {voice.cast_id} --pick N"
        )
    lines.append(
        f"Or keep every voice as it is: fictora-produce voice --desk {desk} --keep-all"
    )
    return lines


def render_gate(
    desk: Path,
    spine: Mapping[str, Any],
    approvals: Approvals,
    *,
    episode: int | None = None,
) -> str:
    """The Voices gate text for these yeses (``""`` when nobody speaks)."""

    desk = desk.expanduser().resolve()
    rows = voice_rows(spine, approvals, episode=episode)
    if not rows:
        return ""
    head = "Voices gate (before filming): the voices the takes will speak in."
    tail = [approvals.note] if approvals.note else []
    if approvals.grandfathered:
        return "\n".join(
            [
                head,
                *rows,
                "Note: this desk filmed before the voices gate; filming is not held for it.",
                *tail,
            ]
        )
    pending = pending_voices(spine, approvals, episode=episode)
    if not pending:
        return "\n".join([head, *rows, "Every voice has the human's yes.", *tail])
    return "\n".join([head, *rows, *_commands(desk, pending), *tail])


def gate_text(
    desk: Path, spine: Mapping[str, Any], *, episode: int | None = None, run: _Api
) -> str:
    """The Voices gate as ``step`` prints it after the plates: whose voices, each voice, and what to run.

    The show's voice mode comes first (:mod:`creation.voice_mode`; the desk's
    ``start --voice-mode`` is sent once the story exists). On a ``model`` show
    there is no locked voice to hear, so the gate says it is skipped. Desk-only
    yeses are pushed to the server first (:func:`sync_approvals`). Returns
    ``""`` when nobody speaks.
    """

    from creation.voice_mode import MODEL_GATE_SKIPPED, voices_for_film

    voices = voices_for_film(desk, run, spine)
    if not speaking_voices(spine, episode=episode):
        return ""
    if not voices.locked:
        return "\n".join([voices.line(), MODEL_GATE_SKIPPED])
    body, approvals = sync_approvals(desk, run, spine)
    text = render_gate(desk, body, approvals, episode=episode)
    return "\n".join([voices.line(), text]) if text else ""


def _model_voices(desk: Path, spine: Mapping[str, Any], run: _Api) -> bool:
    """Whether the show films with the video model's own voices (no Voices gate)."""

    from creation.voice_mode import voices_for_film

    return not voices_for_film(desk, run, spine).locked


def pending_for_film(
    desk: Path, spine: Mapping[str, Any], *, episode: int, run: _Api
) -> list[CastVoice]:
    """The speaking characters filming episode ``episode`` waits on (none on a grandfathered desk or a ``model`` show)."""

    desk = desk.expanduser().resolve()
    if _model_voices(desk, spine, run):
        return []
    if load_approvals(desk)["grandfathered"]:
        return []
    body, approvals = sync_approvals(desk, run, spine)
    return pending_voices(body, approvals, episode=episode)


def film_refusal(
    desk: Path,
    spine: Mapping[str, Any],
    *,
    episode: int,
    run: _Api,
    stage: str = "filming",
    rerun: str = "the film command",
) -> str | None:
    """The refusal before filming episode ``episode``, or ``None`` when every speaking voice has a yes.

    The yeses are the server's (desk-only ones are pushed first); the desk file
    is read only when the server is too old or cannot be reached. A
    grandfathered desk is never refused (:func:`load_approvals`), nor is a
    show filmed with the video model's own voices (voice mode ``model``: there
    is no locked voice to approve).
    """

    desk = desk.expanduser().resolve()
    if _model_voices(desk, spine, run):
        return None
    if load_approvals(desk)["grandfathered"]:
        return None
    body, approvals = sync_approvals(desk, run, spine)
    pending = pending_voices(body, approvals, episode=episode)
    if not pending:
        return None
    who = ", ".join(
        f"{v.name} ({v.provider_voice or 'no voice locked'})" for v in pending
    )
    return "\n".join(
        [
            f"Stopped before {stage}. Nothing was sent.",
            f"No human yes yet on these voices in episode {episode}: {who}.",
            *voice_rows(body, approvals, episode=episode),
            *_commands(desk, pending),
            *([approvals.note] if approvals.note else []),
            f"Then run {rerun} again.",
        ]
    )


def thought_and_narration_names(spine: Mapping[str, Any]) -> list[str]:
    """Who is heard in a voice made after filming: a character's thoughts, or a narrator's lines.

    Thoughts are inner-voice cues; a narrator is a voice-only character with a
    line. Both are spoken in the character's kept voice, never the video
    model's, so on a show filmed in the model's own voices they never match the
    voice that character speaks in (Don't Look, Hana: about 5 h, three voices).

    Parameters
    ----------
    spine
        The show's spine.

    Returns
    -------
    list[str]
        Their names, in cast order; empty when nobody thinks aloud or narrates.
    """

    heard: set[str] = set()
    for summary in spine.get("episode_summaries") or []:
        for cue in summary.get("inner_voice") or []:
            if cue.get("speaker_cast_id"):
                heard.add(str(cue["speaker_cast_id"]))
    speakers = {
        str(line.get("cast_id"))
        for beat in spine.get("beats") or []
        for line in beat.get("dialogue_lines") or []
    }
    for card in spine.get("cast") or []:
        if card.get("voice_only") is True and card.get("cast_id") in speakers:
            heard.add(str(card["cast_id"]))
    return [
        str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or []
        if card.get("cast_id") in heard
    ]


def locked_voices_needed(desk: Path, names: Sequence[str]) -> str:
    """Why a show with thoughts or narration cannot use the video model's own voices, and what to run."""

    return (
        f"This show has thoughts or narration ({', '.join(names)}). They are spoken in each character's kept "
        "voice, and the video model invents a new voice on every take, so they could never match. "
        f"Lock the voices: fictora-produce voice-mode --desk {desk} --set locked, then hear and keep each one "
        f"(fictora-produce voice --desk {desk} --list)."
    )


def voices_first_refusal(
    desk: Path, spine: Mapping[str, Any], *, episode: int, run: _Api, stage: str
) -> str | None:
    """The stop before plates or boards on a new desk until the voices are decided, or ``None``.

    Voices are the operator's choice, made before anything is drawn: on a
    locked show every speaking voice needs the human's yes first (the film
    gate, asked earlier); a show on the video model's own voices goes on only
    when nobody thinks aloud or narrates (:func:`thought_and_narration_names`).
    """

    from creation.voice_mode import voices_for_film

    if not voices_for_film(desk, run, spine).locked:
        names = thought_and_narration_names(spine)
        if not names:
            return None
        return f"Stopped before {stage}. Nothing was sent.\n" + locked_voices_needed(
            desk, names
        )
    return film_refusal(
        desk, spine, episode=episode, run=run, stage=stage, rerun="`step`"
    )


def grandfathered_note(desk: Path) -> str | None:
    """A one-line note when the desk is grandfathered, else ``None``."""

    if load_approvals(desk.expanduser().resolve())["grandfathered"]:
        return (
            "Note: this desk filmed takes before the voices gate, so filming is not held for voice approval. "
            "A new character's voice is still worth an audition (`voice --audition`)."
        )
    return None
