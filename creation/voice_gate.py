"""The voices gate: every speaking character's voice is heard and approved (or kept) before filming.

The server picks a voice for each character at draft time (the cast card's
``voice_brief.provider_voice``) and takes film in it. Nobody heard it first, so
the gate sits after the plates yes: ``step`` lists each speaking character's
voice and offers ``voice --audition``; the human keeps each one
(``voice --cast NAME --keep`` / ``voice --keep-all``) or swaps it
(``voice --audition``, then ``--pick N``). Filming (``step --confirm-spend``,
``film --confirm-spend``) is refused until every character who speaks in the
episode has a voice the human approved; a voice that changed after its yes
(a new pick, a new cast card) needs a new one.

The yeses live on the desk in ``shared/voices/approvals.json``. A desk that had
filmed takes before this gate existed (no approvals file yet) is grandfathered:
it is never blocked, and says so.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from creation.post.desk import episode_dialogue, spine_body

APPROVALS_PATH = Path("shared") / "voices" / "approvals.json"
AUDITION_USD = 0.30


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
                provider_voice=str(brief.get("provider_voice") or "").strip(),
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
    grandfathered for every later episode. A new desk's file is written by the
    first keep or pick, before anything can film, so it is never grandfathered.
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


def record_voice(desk: Path, voice: CastVoice, *, how: str) -> None:
    """Record the human's yes on ``voice`` (``how``: ``kept`` or ``picked``)."""

    desk = desk.expanduser().resolve()
    data = load_approvals(desk)
    data["voices"][voice.cast_id] = {
        "name": voice.name,
        "provider_voice": voice.provider_voice,
        "how": how,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save(desk, data)


def pending_voices(
    desk: Path, spine: Mapping[str, Any], *, episode: int | None = None
) -> list[CastVoice]:
    """Speaking characters whose current voice has no yes (never approved, or changed since)."""

    approved = load_approvals(desk)["voices"]
    pending: list[CastVoice] = []
    for voice in speaking_voices(spine, episode=episode):
        record = approved.get(voice.cast_id)
        if record is None or record.get("provider_voice") != voice.provider_voice:
            pending.append(voice)
    return pending


def _voice_words(voice: CastVoice) -> str:
    words = (
        voice.provider_voice
        or "no voice locked (the take would use the video model's own voice)"
    )
    if voice.description:
        words += f": {voice.description}"
    return words


def voice_rows(
    desk: Path, spine: Mapping[str, Any], *, episode: int | None = None
) -> list[str]:
    """One printed row per speaking character: name, voice, its words, approved or not."""

    approved = load_approvals(desk)["voices"]
    pending = {v.cast_id for v in pending_voices(desk, spine, episode=episode)}
    rows: list[str] = []
    for voice in speaking_voices(spine, episode=episode):
        mark = (
            "needs a yes"
            if voice.cast_id in pending
            else f"approved ({approved[voice.cast_id].get('how', 'kept')})"
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


def gate_text(
    desk: Path, spine: Mapping[str, Any], *, episode: int | None = None
) -> str:
    """The Voices gate as ``step`` prints it after the plates: each voice, and what to run.

    Returns ``""`` when nobody speaks.
    """

    desk = desk.expanduser().resolve()
    rows = voice_rows(desk, spine, episode=episode)
    if not rows:
        return ""
    data = load_approvals(desk)
    pending = pending_voices(desk, spine, episode=episode)
    head = "Voices gate (before filming): the voices the takes will speak in."
    if data["grandfathered"]:
        return "\n".join(
            [
                head,
                *rows,
                "Note: this desk filmed before the voices gate; filming is not held for it.",
            ]
        )
    if not pending:
        return "\n".join([head, *rows, "Every voice has the human's yes."])
    return "\n".join([head, *rows, *_commands(desk, pending)])


def film_refusal(desk: Path, spine: Mapping[str, Any], *, episode: int) -> str | None:
    """The refusal before filming episode ``episode``, or ``None`` when every speaking voice has a yes.

    A grandfathered desk is never refused (:func:`load_approvals`).
    """

    desk = desk.expanduser().resolve()
    if load_approvals(desk)["grandfathered"]:
        return None
    pending = pending_voices(desk, spine, episode=episode)
    if not pending:
        return None
    who = ", ".join(
        f"{v.name} ({v.provider_voice or 'no voice locked'})" for v in pending
    )
    return "\n".join(
        [
            "Stopped before filming. Nothing was sent.",
            f"No human yes yet on these voices in episode {episode}: {who}.",
            *voice_rows(desk, spine, episode=episode),
            *_commands(desk, pending),
            "Then run the film command again.",
        ]
    )


def grandfathered_note(desk: Path) -> str | None:
    """A one-line note when the desk is grandfathered, else ``None``."""

    if load_approvals(desk.expanduser().resolve())["grandfathered"]:
        return (
            "Note: this desk filmed takes before the voices gate, so filming is not held for voice approval. "
            "A new character's voice is still worth an audition (`voice --audition`)."
        )
    return None
