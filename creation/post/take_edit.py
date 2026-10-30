"""Hand edits on one take go to the server's take edits (fictora-drama #1189 / #1190; 31 Oct 2026).

``finish --take tK`` with ``--mute``, ``--voice``, ``--cue`` or ``--impact-at``
used to be refused ("not live yet", kit #316). The server now edits a delivered
take on its media worker, answers at once, and puts the episode back together
with the edit in the background. Server-heavy rule (founder, 15 Oct 2026): the
kit only turns each setting into the server's field, sends it and prints the
server's answer. It keeps no edit of its own and checks nothing the server
checks (overlaps, lengths, whether a take's voices can be changed).

=================================  =================================================================
Setting                            Server field
=================================  =================================================================
``--mute A-B``                     ``mutes[] {start_seconds, end_seconds}``
``--voice FILE@SECONDS[@DB]``      ``dub_lines[] {text, audio_url, start_seconds, gain_db}``: a line
                                   made with ``voice-line`` (its saved note names the server's copy)
``--voice CHARACTER``              ``POST .../takes/{n}/revoice {cast_id}``: every line that
                                   character says in the take, again in their locked voice
``--cue SOUND@SECONDS[@DB]``       ``cues[] {sound, start_seconds, gain_db}``; a saved ``cue`` file
                                   sends its own words and length
``--impact-at SECONDS[:blast]``    ``impact_hits[] {at_seconds, style}``
=================================  =================================================================

Routes: ``POST /v1/video-generations/{job}/takes/{n}/post-edit`` (the edit),
``.../post-edit/quote`` and ``.../revoice`` with ``dry_run`` (``finish --preview``:
the tokens, nothing changed). Times are seconds on the take as the server
delivered it. Every edit is sent with ``add_to_current_edit`` (fictora-drama
#1193): the server adds it to the take's current edit, so an operator's
earlier edits on the take are kept and only the new paid work is charged. A
server older than that refuses the field (422); the kit then sends the edit
once more without it and says plainly that the take's earlier edits were
replaced. A re-voice keeps the take's other edits either way.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from creation.harness.http_util import api_error_text

POST_EDIT_ROUTE = "/v1/video-generations/{job_id}/takes/{take_index}/post-edit"
QUOTE_ROUTE = POST_EDIT_ROUTE + "/quote"
REVOICE_ROUTE = "/v1/video-generations/{job_id}/takes/{take_index}/revoice"
#: Sent on every edit: the server adds the edit to the take's current one instead of replacing it.
ADD_TO_CURRENT_FIELD = "add_to_current_edit"
#: The edit answers once its lines and sounds are made (the episode is rebuilt after, in the background).
EDIT_TIMEOUT_SECONDS = 300.0
#: Said after every edit the server took: the new cut is put together in the background.
NEXT_STEP = "Next: run finish again (without the edit settings) in a few minutes to download the new cut."
NOTHING_CHANGED = "Nothing was changed or charged."

_SPAN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")
_NUMBER = re.compile(r"^\s*[+-]?\d+(?:\.\d+)?\s*$")


class TakeEditStop(RuntimeError):
    """A setting the kit cannot turn into the server's field, or the server's refusal: one plain line."""


@dataclass
class TakeEditRequest:
    """One take's settings as the server's fields, plus the characters to re-voice."""

    mutes: list[dict[str, Any]] = field(default_factory=list)
    dub_lines: list[dict[str, Any]] = field(default_factory=list)
    cues: list[dict[str, Any]] = field(default_factory=list)
    impact_hits: list[dict[str, Any]] = field(default_factory=list)
    revoice: list[str] = field(default_factory=list)

    def edit_body(self) -> dict[str, Any] | None:
        """The ``post-edit`` body, or ``None`` when only re-voices were asked."""

        body = {
            key: value
            for key, value in (
                ("mutes", self.mutes),
                ("dub_lines", self.dub_lines),
                ("cues", self.cues),
                ("impact_hits", self.impact_hits),
            )
            if value
        }
        if not body:
            return None
        body[ADD_TO_CURRENT_FIELD] = True
        return body

    def empty(self) -> bool:
        """Whether no edit setting was given."""

        return self.edit_body() is None and not self.revoice


def parse_mute(raw: str) -> dict[str, Any]:
    """``"1.2-2.0"`` -> ``{"start_seconds": 1.2, "end_seconds": 2.0}`` (the server checks the window)."""

    match = _SPAN.match(raw)
    if match is None:
        raise TakeEditStop(
            f"--mute {raw!r}: write A-B seconds on the take, like 1.2-2.0"
        )
    return {
        "start_seconds": float(match.group(1)),
        "end_seconds": float(match.group(2)),
    }


def _at_and_level(raw: str, flag: str, shape: str) -> tuple[str, float, float | None]:
    """``WHAT@SECONDS[@DB]`` -> its three parts (``@DB`` optional)."""

    parts = raw.rsplit("@", 2)
    if len(parts) == 3 and _NUMBER.match(parts[1]) and _NUMBER.match(parts[2]):
        return parts[0], float(parts[1]), float(parts[2])
    if len(parts) >= 2 and _NUMBER.match(parts[-1]):
        return raw.rsplit("@", 1)[0], float(parts[-1]), None
    raise TakeEditStop(f"{flag} {raw!r}: write {shape}")


def _saved_note(path: Path) -> Mapping[str, Any] | None:
    note = path.with_suffix(".json")
    try:
        payload = json.loads(note.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def parse_voice(
    raw: str, *, desk: Path, spine: Mapping[str, Any] | None
) -> tuple[str, Any]:
    """One ``--voice`` value: ``("dub", line)`` for ``FILE@SECONDS[@DB]``, ``("revoice", cast_id)`` otherwise.

    A dub line is a file ``voice-line`` made: the server already holds it, and
    its saved note names that copy and the line's words. A bare name re-voices
    that character's lines on the take in their locked voice.
    """

    if "@" not in raw:
        wanted = raw.strip()
        if not wanted:
            raise TakeEditStop(
                "--voice needs a character to re-voice, or FILE@SECONDS for one line"
            )
        if spine is None:
            return "revoice", wanted
        from creation.post.desk import find_cast

        try:
            return "revoice", str(find_cast(spine, wanted)["cast_id"])
        except ValueError as exc:
            raise TakeEditStop(f"--voice {wanted!r}: {exc}") from None
    name, start, level = _at_and_level(
        raw, "--voice", "FILE@SECONDS[@DB] (a voice-line file) or a character's name"
    )
    path = Path(name).expanduser()
    if not path.is_absolute():
        path = desk / path
    note = _saved_note(path)
    url = str(note.get("url") or "") if note else ""
    text = str(note.get("line") or "").strip() if note else ""
    if not url or not text:
        raise TakeEditStop(
            f"--voice {name!r}: only a line made with voice-line can be laid (the server lays its own "
            "copy of it, named in the line's saved note); make it with voice-line first"
        )
    line: dict[str, Any] = {"text": text, "audio_url": url, "start_seconds": start}
    if level is not None:
        line["gain_db"] = level
    return "dub", line


def parse_cue(raw: str, *, desk: Path) -> dict[str, Any]:
    """``SOUND@SECONDS[@DB]`` -> one added sound; a saved ``cue`` file sends its own words and length."""

    name, start, level = _at_and_level(
        raw, "--cue", 'SOUND@SECONDS[@DB], like "low rumble@2.5"'
    )
    path = Path(name).expanduser()
    if not path.is_absolute():
        path = desk / path
    note = _saved_note(path) if path.suffix else None
    cue: dict[str, Any]
    if note and str(note.get("description") or "").strip():
        cue = {"sound": str(note["description"]).strip(), "start_seconds": start}
        if isinstance(note.get("seconds"), int | float):
            cue["duration_seconds"] = float(note["seconds"])
    elif name.strip():
        cue = {"sound": name.strip(), "start_seconds": start}
    else:
        raise TakeEditStop(
            f'--cue {raw!r}: say what the sound is, like "low rumble@2.5"'
        )
    if level is not None:
        cue["gain_db"] = level
    return cue


def build_request(
    *,
    desk: Path,
    episode: int,
    mutes: Sequence[str] = (),
    voices: Sequence[str] = (),
    cues: Sequence[str] = (),
    impact_at: Sequence[str] = (),
) -> TakeEditRequest:
    """Turn the ``finish`` settings into the server's fields (format only; the server checks the rest)."""

    from creation.post.impact_post import parse_impact_at

    request = TakeEditRequest()
    request.mutes = [parse_mute(raw) for raw in mutes]
    spine: Mapping[str, Any] | None = None
    if any("@" not in raw for raw in voices):
        from creation.post.desk import saved_spine

        found = saved_spine(desk, episode)
        spine = found[0] if found else None
    for raw in voices:
        kind, value = parse_voice(raw, desk=desk, spine=spine)
        if kind == "dub":
            request.dub_lines.append(value)
        elif value not in request.revoice:
            request.revoice.append(value)
    request.cues = [parse_cue(raw, desk=desk) for raw in cues]
    for raw in impact_at:
        try:
            hits = parse_impact_at(raw)
        except ValueError as exc:
            raise TakeEditStop(str(exc)) from None
        request.impact_hits += [
            {"at_seconds": hit.at, "style": hit.style} for hit in hits
        ]
    return request


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _code_and_message(body: Any) -> tuple[str, str]:
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return "", ""
    return str(error.get("code") or ""), str(error.get("message") or "").strip()


def refusal_line(status: int, body: Any, *, take_id: str) -> str:
    """The server's refusal of a take edit as one plain line with the next step (never a traceback)."""

    code, message = _code_and_message(body)
    said = f"{message.rstrip('.')}." if message else ""
    if status == 404 and isinstance(body, dict) and body.get("detail") == "Not Found":
        return (
            "This server cannot edit a take yet (it needs the take-edit update). Tell the core team. "
            + NOTHING_CHANGED
        )
    if code == "take_post_edit_in_progress":
        return (
            f"An edit of {take_id} is still running on the server. Wait a minute, then run finish without "
            "the edit settings to download the new cut. Nothing was charged twice."
        )
    if code == "take_post_edit_unavailable":
        return f"{said or 'Editing a take is not available on this server right now.'} {NOTHING_CHANGED}"
    if code == "take_not_ready":
        return f"{take_id} has not been delivered yet. Wait until filming is complete, then run this again. {NOTHING_CHANGED}"
    if code == "take_revoice_unavailable":
        return (
            f"{said or f'{take_id} cannot be re-voiced.'} Film the take again instead if the voice must change. "
            + NOTHING_CHANGED
        )
    if status == 404:
        return f"The server does not have {take_id} on this episode's filming ({said or 'not found'}). {NOTHING_CHANGED}"
    if status == 422:
        return (
            f"The server would not take this edit: {said or api_error_text(body)[:300]} Change the setting "
            f"and run it again. {NOTHING_CHANGED}"
        )
    if status in (401, 403):
        return f"The server refused the kit's key (HTTP {status}). Check the kit's drama API token. {NOTHING_CHANGED}"
    if status == 429:
        return f"The server is over its spending limit for now (HTTP 429). Try again later. {NOTHING_CHANGED}"
    if code == "take_post_edit_failed" or code == "take_post_edit_rejoin_unready":
        return f"{said or 'The take could not be edited right now.'} Run the same command again in a few minutes."
    if status >= 500:
        return f"The server is busy or restarting (HTTP {status}). Run the same command again in a few minutes."
    return f"The server refused the edit (HTTP {status}): {said or api_error_text(body)[:300]}"


def refuses_add_to_current(status: int, body: Any) -> bool:
    """Whether an older server refused ``add_to_current_edit`` as a field it does not know (422).

    Parameters
    ----------
    status
        The HTTP status.
    body
        The answer's JSON (or text).

    Returns
    -------
    bool
        True for a 422 naming the field as not allowed; any other refusal is the server's real answer.
    """

    if status != 422 or not isinstance(body, dict):
        return False
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    rows = details.get("validation_errors") or body.get("detail") or []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        loc = [str(part) for part in row.get("loc") or []]
        if (
            loc
            and loc[-1] == ADD_TO_CURRENT_FIELD
            and (
                row.get("type") == "extra_forbidden"
                or "not permitted" in str(row.get("msg", ""))
            )
        ):
            return True
    message = str(error.get("message") or "")
    return ADD_TO_CURRENT_FIELD in message and "not permitted" in message


def replaced_warning(take_id: str, *, preview: bool) -> str:
    """The plain warning when an older server replaced (or would replace) the take's earlier edits."""

    if preview:
        return (
            f"Warning: this server is older and does not keep earlier edits. Sending this would replace "
            f"every earlier edit on {take_id} (mutes, laid lines, sounds, hits) with only this one."
        )
    return (
        f"Warning: this server is older and does not keep earlier edits, so every earlier edit on {take_id} "
        "(mutes, laid lines, sounds, hits) was replaced by this one. To get them back, run finish once with "
        "all of them together."
    )


def _post(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
    *,
    key: str | None,
) -> tuple[int, Any]:
    sent = dict(headers)
    if key is not None:
        sent["Idempotency-Key"] = key
    try:
        response = client.post(url, headers=sent, json=dict(body))
    except httpx.TimeoutException:
        raise TakeEditStop(
            "The server did not answer in time. The edit may still land: run finish without the edit "
            "settings in a few minutes and look at the new cut before sending it again."
        ) from None
    except httpx.TransportError as exc:
        raise TakeEditStop(
            f"Could not reach the server ({type(exc).__name__}). Check the internet connection and run "
            f"it again. {NOTHING_CHANGED}"
        ) from None
    return response.status_code, _body(response)


def _counts(body: Mapping[str, Any]) -> str:
    named = (
        ("mutes", "muted stretch"),
        ("dub_lines", "laid line"),
        ("cues", "added sound"),
        ("impact_hits", "hit"),
    )
    parts = []
    for key, word in named:
        count = len(body.get(key) or [])
        if count:
            parts.append(
                f"{count} {word}{'' if count == 1 else 'es' if word.endswith('ch') else 's'}"
            )
    return ", ".join(parts)


def _tokens(value: Any) -> str:
    tokens = int(value or 0)
    return (
        "free (0 tokens)"
        if tokens == 0
        else f"{tokens} token{'' if tokens == 1 else 's'}"
    )


def send_take_edit(
    *,
    base_url: str,
    headers: Mapping[str, str],
    job_id: str,
    take_index: int,
    take_id: str,
    request: TakeEditRequest,
    preview: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """Send one take's edit (then any re-voices) and answer the server's words as lines to print.

    Raises
    ------
    TakeEditStop
        With one plain line when the server refused or could not be reached.
    """

    base = base_url.rstrip("/")
    lines: list[str] = []
    run = uuid.uuid4().hex[:12]
    edit = request.edit_body()
    with httpx.Client(transport=transport, timeout=EDIT_TIMEOUT_SECONDS) as client:
        if edit is not None:
            route = QUOTE_ROUTE if preview else POST_EDIT_ROUTE
            url = base + route.format(job_id=job_id, take_index=take_index)
            key = None if preview else f"kit-take-edit-{take_id}-{run}"
            status, answer = _post(client, url, headers, edit, key=key)
            replaced = False
            if refuses_add_to_current(status, answer):
                # An older server: the edit replaces the take's earlier one. Sent once more without the field.
                edit = {
                    name: value
                    for name, value in edit.items()
                    if name != ADD_TO_CURRENT_FIELD
                }
                retry_key = None if key is None else f"{key}-replace"
                status, answer = _post(client, url, headers, edit, key=retry_key)
                replaced = True
            if not 200 <= status < 300 or not isinstance(answer, dict):
                raise TakeEditStop(refusal_line(status, answer, take_id=take_id))
            if replaced:
                lines.append(replaced_warning(take_id, preview=preview))
            if preview:
                lines.append(
                    f"{take_id} edit ({_counts(edit)}) would cost {_tokens(answer.get('tokens'))}. "
                    "Preview only: nothing was changed or charged."
                )
            else:
                lines.append(
                    f"{take_id} edited on the server ({_counts(edit)}): {_tokens(answer.get('tokens'))}."
                )
                lines.append(
                    str(answer.get("episode_not_redelivered") or "")
                    or "The episode was put back together with the edit."
                )
        for cast_id in request.revoice:
            body = {"cast_id": cast_id, "dry_run": preview}
            status, answer = _post(
                client,
                base + REVOICE_ROUTE.format(job_id=job_id, take_index=take_index),
                headers,
                body,
                key=f"kit-take-revoice-{take_id}-{cast_id}-{'dry-' if preview else ''}{run}",
            )
            if not 200 <= status < 300 or not isinstance(answer, dict):
                raise TakeEditStop(refusal_line(status, answer, take_id=take_id))
            quote = answer.get("quote") if isinstance(answer.get("quote"), dict) else {}
            if preview:
                lines.append(
                    f"Re-voicing {cast_id} on {take_id} would cost {_tokens(quote.get('tokens'))}. "
                    "Preview only: nothing was changed or charged."
                )
                continue
            applied = (
                answer.get("applied") if isinstance(answer.get("applied"), dict) else {}
            )
            lines.append(
                f"{cast_id}'s lines on {take_id} re-voiced on the server: "
                f"{_tokens(applied.get('tokens', quote.get('tokens')))}."
            )
            lines.append(
                str(applied.get("episode_not_redelivered") or "")
                or "The episode was put back together with the edit."
            )
    if not preview:
        lines.append(NEXT_STEP)
    return lines


def run_take_edit(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    request: TakeEditRequest,
    preview: bool = False,
    api: tuple[str, Mapping[str, str]] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> list[str]:
    """Find the take's filming on the desk's records and send its edit; the lines to print.

    Raises
    ------
    TakeEditStop
        With one plain line.
    """

    from creation.post.server_finish import _api
    from creation.post.take_facts import take_number
    from creation.post.take_handles import take_coordinate

    try:
        take_number(take_id)
    except ValueError as exc:
        raise TakeEditStop(f"{exc}. {NOTHING_CHANGED}") from None
    desk = desk.expanduser().resolve()
    where = take_coordinate(desk, episode, take_id)
    if where is None:
        raise TakeEditStop(
            f"The desk has no filmed clip of {take_id} in episode {episode}, so the server cannot find it to "
            f"edit. Film it (or collect it) first. {NOTHING_CHANGED}"
        )
    try:
        base, headers = api if api is not None else _api(desk)
    except SystemExit as exc:  # no kit token
        raise TakeEditStop(str(exc.code)) from None
    return send_take_edit(
        base_url=base,
        headers=headers,
        job_id=where.video_job_id,
        take_index=where.take_index,
        take_id=take_id,
        request=request,
        preview=preview,
        transport=transport,
    )


__all__ = [
    "ADD_TO_CURRENT_FIELD",
    "NEXT_STEP",
    "TakeEditRequest",
    "TakeEditStop",
    "build_request",
    "parse_cue",
    "parse_mute",
    "parse_voice",
    "refusal_line",
    "refuses_add_to_current",
    "replaced_warning",
    "run_take_edit",
    "send_take_edit",
]
