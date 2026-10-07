"""Whose voices a show's takes speak in: one choice per show (founder decision, 2026-10-05).

The server keeps the choice on the spine (``voice_mode``, fictora-drama
per-show voice mode):

- ``locked``: each line is rendered in the character's locked voice (the cast
  card's ``voice_brief.provider_voice``) and the take films to that dialogue
  track (option C). The Voices gate applies: the human hears and keeps (or
  picks) every speaking voice before anything films.
- ``model``: the video model voices each character itself from the cast
  card's voice description, as every take did before option C. There is no
  locked voice to hear, so the Voices gate is skipped; finish lays the show's
  music as it did on those episodes.

A new show follows the server setting (locked on production). A show whose
earlier episodes were filmed with the model's voices stays on them unless the
human chooses otherwise. ``start --voice-mode`` records the desk's choice; it
is sent to the server once the story exists (and never over a choice the
server already stores, for example one made in the app). ``voice-mode --desk D
[--set locked|model]`` reads or changes it on an existing show.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TextIO

import httpx

from creation.harness.http_util import api_error_text
from creation.post.desk import open_api, refresh_spine, spine_body
from creation.rules_epoch import is_legacy
from creation.voice_gate import locked_voices_needed, thought_and_narration_names

VoiceMode = Literal["locked", "model"]
VOICE_MODES: tuple[VoiceMode, ...] = ("locked", "model")
#: The server's route (GET reads, POST sets).
VOICE_MODE_ROUTE = "/v1/spines/{spine_id}/voice-mode"

#: What each mode means, in the words the kit prints.
VOICE_MODE_WORDS: Mapping[str, str] = {
    "locked": "locked voices (each character's voice on their cast card, rendered and filmed as the take's dialogue track)",
    "model": "the video model's own voices, from each character's voice description on the cast card",
}
SOURCE_WORDS: Mapping[str, str] = {
    "show": "set for this show",
    "inferred": "the voices this show's earlier episodes were filmed with",
    "default": "the default for a new show",
    "run": "this run only (operator override)",
    "older-server": "this Drama API has no voice-mode route yet (an older deploy), so its setting decides",
    "unreachable": "the Drama API could not be reached; read from the saved spine",
}
MODEL_GATE_SKIPPED = (
    "Voices gate skipped: this show uses the video model's own voices (voice mode `model`), so there is no "
    "locked voice to hear or keep. Each character sounds as their voice description on the cast card says."
)


class _Api(Protocol):
    """The slice of :class:`creation.harness.session.DramaApiRunSession` used here."""

    def get_optional(self, path: str) -> tuple[int, Any]: ...

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]: ...

    def spine(self, spine_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ShowVoices:
    """The voices the show's next take speaks in, and why."""

    mode: VoiceMode
    #: ``show`` | ``inferred`` | ``default`` (the server's), or ``older-server`` / ``unreachable``.
    source: str
    filmed_takes: int = 0
    model_voice_takes: int = 0

    @property
    def locked(self) -> bool:
        """Whether the Voices gate applies (locked voices)."""

        return self.mode == "locked"

    def line(self) -> str:
        """``Voices for the next take: … (why).``"""

        return f"Voices for the next take: {VOICE_MODE_WORDS[self.mode]} ({SOURCE_WORDS.get(self.source, self.source)})."


def _spine_id(spine: Mapping[str, Any]) -> str:
    return str(spine_body(spine).get("spine_id") or "")


def _from_spine(spine: Mapping[str, Any], source: str) -> ShowVoices:
    stored = str(spine_body(spine).get("voice_mode") or "")
    if stored == "model":
        return ShowVoices("model", "show")
    if stored == "locked":
        return ShowVoices("locked", "show")
    return ShowVoices("locked", source)


def show_voices(run: _Api, spine: Mapping[str, Any]) -> ShowVoices:
    """Ask the server whose voices the show's next take speaks in (free).

    Parameters
    ----------
    run
        Open API session.
    spine
        The show's spine (its ``spine_id`` names the show).

    Returns
    -------
    ShowVoices
        The server's answer. On an older server (no route: a bare 404) or no
        connection, the spine's stored ``voice_mode`` when it has one, else
        ``locked`` (what such a server films with option C on).

    Raises
    ------
    RuntimeError
        Any other refusal.
    """

    sid = _spine_id(spine)
    try:
        status, answer = run.get_optional(VOICE_MODE_ROUTE.format(spine_id=sid))
    except httpx.TransportError:
        return _from_spine(spine, "unreachable")
    if 200 <= status < 300 and isinstance(answer, Mapping):
        mode = str(answer.get("voice_mode") or "")
        if mode in VOICE_MODES:
            return ShowVoices(
                mode,  # type: ignore[arg-type]
                str(answer.get("source") or "show"),
                int(answer.get("filmed_takes") or 0),
                int(answer.get("model_voice_takes") or 0),
            )
    error = answer.get("error") if isinstance(answer, Mapping) else None
    if status in (404, 405) and not (isinstance(error, Mapping) and error.get("code")):
        return _from_spine(spine, "older-server")
    raise RuntimeError(
        f"the server could not say which voices the show uses (HTTP {status}): {api_error_text(answer)}"
    )


def set_show_voices(
    run: _Api, spine: Mapping[str, Any], mode: VoiceMode
) -> dict[str, Any]:
    """Store ``mode`` as the show's voices (``POST …/voice-mode``, free).

    A stale spine version is read again and sent once more.

    Returns
    -------
    dict[str, Any]
        The server's answer (``previous_voice_mode``, ``changed``, ``applied``, ``notice``, …).

    Raises
    ------
    RuntimeError
        The server is older than the route, or refused the change.
    """

    if mode not in VOICE_MODES:
        raise ValueError(f"voice mode is locked or model, not {mode!r}")
    body = spine_body(spine)
    sid = _spine_id(body)
    path = VOICE_MODE_ROUTE.format(spine_id=sid)
    for attempt in (1, 2):
        status, answer = run.post_optional(
            path, {"spine_version": body.get("spine_version"), "voice_mode": mode}
        )
        if 200 <= status < 300 and isinstance(answer, Mapping):
            return dict(answer)
        error = answer.get("error") if isinstance(answer, Mapping) else None
        code = str(error.get("code") or "") if isinstance(error, Mapping) else ""
        if status == 409 and code == "spine_version_conflict" and attempt == 1:
            body = spine_body(run.spine(sid))
            continue
        if status in (404, 405) and not code:
            raise RuntimeError(
                "This Drama API has no voice-mode route yet (an older deploy): the show's voices follow the "
                "server setting. Nothing was changed."
            )
        raise RuntimeError(
            f"the server refused the voice mode (HTTP {status}): {api_error_text(answer)}"
        )
    raise AssertionError("unreachable")


def desk_voice_mode(desk: Path) -> str | None:
    """The desk's ``voice_mode`` (``start --voice-mode``), or ``None``."""

    from creation.production_config import load_production_config

    value = load_production_config(desk).voice_mode
    return value if value in VOICE_MODES else None


def send_desk_choice(
    desk: Path, run: _Api, spine: Mapping[str, Any], *, out: TextIO | None = None
) -> None:
    """Send the desk's ``start --voice-mode`` to the server once the story exists.

    Only while the server stores no choice for the show: a choice made since
    (in the app, or with ``voice-mode --set``) is never overwritten. A server
    without the route is left alone, with a note.
    """

    wanted = desk_voice_mode(desk)
    body = spine_body(spine)
    if wanted is None or not _spine_id(body) or body.get("voice_mode") in VOICE_MODES:
        return
    names = thought_and_narration_names(body)
    if wanted == "model" and names and not is_legacy(desk):
        print(
            "Note: the desk's voice mode `model` (start --voice-mode) was not sent. "
            + locked_voices_needed(desk, names),
            file=out or sys.stderr,
        )
        return
    try:
        answer = set_show_voices(run, body, wanted)  # type: ignore[arg-type]
    except RuntimeError as exc:
        print(
            f"Note: the desk's voice mode `{wanted}` was not sent: {exc}",
            file=out or sys.stderr,
        )
        return
    print(
        f"Voice mode `{wanted}` (from start --voice-mode) is now set for this show."
        + (f" {answer['notice']}" if answer.get("notice") else ""),
        file=out or sys.stderr,
    )


def voices_for_film(desk: Path, run: _Api, spine: Mapping[str, Any]) -> ShowVoices:
    """The desk's choice sent first (once), then the server's answer: what the next take speaks in."""

    send_desk_choice(desk, run, spine)
    return show_voices(run, spine)


def next_take_voices_line(desk: Path, run: _Api, spine: Mapping[str, Any]) -> str:
    """The line ``step``, the estimate and ``film`` print: whose voices the next take speaks in.

    Never stops the caller: a server that cannot answer is said in the line.
    """

    try:
        voices = voices_for_film(desk, run, spine)
    except RuntimeError as exc:
        return f"Voices for the next take: not known ({exc})."
    if voices.locked:
        return voices.line()
    return f"{voices.line()} No Voices gate: there is no locked voice to hear."


def run_voice_mode(
    desk: Path, *, set_to: str | None = None, out: TextIO | None = None
) -> ShowVoices:
    """``voice-mode --desk D [--set locked|model]``: read or change the show's voices (free).

    Parameters
    ----------
    desk
        Series desk bound to a story.
    set_to
        ``locked`` or ``model`` to store it for the show; ``None`` to read it.
    out
        Text stream.

    Returns
    -------
    ShowVoices
        The show's voices after the call.
    """

    from creation.production_config import (
        load_production_config,
        save_production_config,
    )
    from creation.production_state import load_production

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    episode = load_production(desk).episode_ordinal
    run = open_api(desk, episode)
    try:
        spine = refresh_spine(run, desk, episode)
        if set_to == "model" and not is_legacy(desk):
            names = thought_and_narration_names(spine)
            if names:
                raise RuntimeError(
                    "Not changed: the show stays on locked voices. "
                    + locked_voices_needed(desk, names)
                )
        if set_to is not None:
            answer = set_show_voices(run, spine, set_to)  # type: ignore[arg-type]
            config = load_production_config(desk)
            config.voice_mode = set_to
            save_production_config(desk, config)
            if answer.get("changed"):
                print(f"Voice mode set to `{set_to}` for this show.", file=out)
            else:
                print(
                    f"Voice mode `{set_to}` is now stored for this show (new takes already used it).",
                    file=out,
                )
            if answer.get("notice"):
                print(str(answer["notice"]), file=out)
        voices = show_voices(run, spine)
    finally:
        run.client.close()
    print(voices.line(), file=out)
    if voices.filmed_takes:
        print(
            f"Filmed so far: {voices.filmed_takes} take(s), {voices.model_voice_takes} of them with the "
            "video model's own voices. Filmed takes keep the voices they were filmed with.",
            file=out,
        )
    print(
        "Voices gate: applies (hear and keep every speaking voice before filming: "
        f"fictora-produce voice --desk {desk} --list)."
        if voices.locked
        else "Voices gate: skipped (there is no locked voice to hear).",
        file=out,
    )
    other = "model" if voices.locked else "locked"
    print(
        f"Change it: fictora-produce voice-mode --desk {desk} --set {other}", file=out
    )
    return voices


__all__ = [
    "MODEL_GATE_SKIPPED",
    "VOICE_MODES",
    "VOICE_MODE_ROUTE",
    "VOICE_MODE_WORDS",
    "ShowVoices",
    "VoiceMode",
    "desk_voice_mode",
    "next_take_voices_line",
    "run_voice_mode",
    "send_desk_choice",
    "set_show_voices",
    "show_voices",
    "voices_for_film",
]
