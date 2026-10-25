"""The episode's pitch card is the server's (decision A, 21 Oct 2026).

The card is the server's card of the written script: each take's shots in
plain words (camera, what we see, the line, the face), where the episode opens
and ends, and the world's rules. The app's Create flow and the kit read and
approve the same card, through the same routes:

- ``GET /v1/spines/{id}/episodes/{n}/pitch`` reads it (free, no model call).
- ``POST …/pitch/approve`` records the human's yes on the draft they saw.
- ``POST …/pitch/changes`` records a change the human asked for; the change
  itself is made with ``line`` / ``edit`` / ``rewrite``, and a change to the
  shots opens the next draft, which needs its own yes.

The hold is the server's too: every kit request announces
``X-Drama-Client-Capabilities: pitch-card-1``
(:data:`creation.harness.http_util.CLIENT_CAPABILITIES`), so the server refuses
paid drawing and filming for an episode whose card has no yes (409
``pitch_not_approved``, nothing sent). An episode already on its boards or
filmed is never held. The writer decides shouted lines and voices; neither is
on the card.

The desk-only card the kit used to keep (``epNN/pitch-vN.json`` and
``epNN/pitch-approvals.json``, written before the draft) is no longer read or
written, except once: a desk yes on an episode in progress is carried to the
server's card for that episode (:func:`carry_desk_pitch_yes`), so a show
mid-episode is not stopped; the server's hold starts at its next episode.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, TextIO

from creation.card_lines import idea_text, read_card_lines, shot_line

#: The server code a held paid step answers with.
PITCH_NOT_APPROVED = "pitch_not_approved"
#: The desk files the kit used to keep (read once to carry a yes, never written).
_DESK_VERSION = re.compile(r"^pitch-v(\d+)\.json$")
_DESK_APPROVALS = "pitch-approvals.json"


class _Api(Protocol):
    """The slice of :class:`creation.harness.session.DramaApiRunSession` used here."""

    def get_optional(self, path: str) -> tuple[int, Any]: ...

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]: ...


class PitchError(RuntimeError):
    """The server refused a pitch-card request; the message says why in plain words."""


def pitch_path(spine_id: str, episode: int) -> str:
    """The card's route for one episode.

    Parameters
    ----------
    spine_id
        The desk's story.
    episode
        Episode ordinal.

    Returns
    -------
    str
        ``/v1/spines/{spine_id}/episodes/{episode}/pitch``.
    """

    return f"/v1/spines/{spine_id}/episodes/{episode}/pitch"


def _error(answer: Any) -> tuple[str, str]:
    error = answer.get("error") if isinstance(answer, Mapping) else None
    if isinstance(error, Mapping):
        return str(error.get("code") or ""), str(error.get("message") or "")
    return "", str(answer)


def read_card(run: _Api, spine_id: str, episode: int) -> dict[str, Any]:
    """Read the episode's card from the server (free).

    Parameters
    ----------
    run
        The API session.
    spine_id
        The desk's story.
    episode
        Episode ordinal.

    Returns
    -------
    dict[str, Any]
        The card (``fictora.drama-pitch-card.v1``).

    Raises
    ------
    PitchError
        The server has no such story or episode, or refused the read.
    """

    status, answer = run.get_optional(pitch_path(spine_id, episode))
    if status == 200 and isinstance(answer, Mapping):
        return dict(answer)
    code, message = _error(answer)
    raise PitchError(
        f"Could not read episode {episode}'s card ({status} {code}): {message}"
    )


def card_text(card: Mapping[str, Any]) -> str:
    """The card as the human reads it: status, then each take's shots, then the world's rules.

    Parameters
    ----------
    card
        The server's card.

    Returns
    -------
    str
        Printable block.
    """

    episode = card.get("episode_ordinal")
    title = card.get("episode_title")
    head = f"Pitch card, episode {episode}" + (f": {title}" if title else "")
    status = card.get("status")
    if status == "not_ready":
        return head + "\n  Not ready: the episode's script is not written yet."
    if card.get("approved"):
        state = f"draft {card.get('draft')}, approved"
    elif status == "change_requested":
        state = f"draft {card.get('draft')}, a change is being made"
    else:
        state = f"draft {card.get('draft')}, NOT approved yet"
    if card.get("already_made"):
        state += " (already on its boards or filmed: never held)"
    out = [f"{head} ({state})"]
    for take in card.get("takes") or []:
        out.append(f"  Take {take.get('take')}:")
        for shot in take.get("shots") or []:
            marks = " [opens]" if shot.get("opens") else ""
            marks += " [ends on]" if shot.get("ends_on") else ""
            light = shot_line(shot)
            if light is not None:
                # The server's lighter script card (lighter cards v3): the
                # moment's action line, the spoken line, then the shot line.
                action = shot.get("action_short")
                out.append(
                    f"    {shot.get('shot')}.{marks} {action}"
                    if action
                    else f"    {shot.get('shot')}.{marks}"
                )
                line = shot.get("line")
                if isinstance(line, Mapping):
                    out.append(f'       {line.get("speaker")}: "{line.get("text")}"')
                out.append(f"       [shot] {light}")
                detail = f"{shot.get('camera')}" + (
                    f" · {shot['camera_move']}" if shot.get("camera_move") else ""
                )
                face = f"; face: {shot['expression']}" if shot.get("expression") else ""
                out.append(
                    f"       (operator detail: {detail}; we see: {shot.get('scene')}{face})"
                )
                continue
            camera = str(shot.get("camera") or "")
            if shot.get("camera_move"):
                camera += f" · {shot['camera_move']}"
            out.append(f"    {shot.get('shot')}. {camera}{marks}: {shot.get('scene')}")
            line = shot.get("line")
            if isinstance(line, Mapping):
                out.append(f'       {line.get("speaker")}: "{line.get("text")}"')
            if shot.get("expression"):
                out.append(f"       ({shot['expression']})")
    rules = card.get("world_rules") or []
    if rules:
        out.append("  World rules (check every line and shot against these):")
        out.extend(f"    - {rule}" for rule in rules)
    return "\n".join(out)


def approve_card(run: _Api, spine_id: str, episode: int) -> dict[str, Any]:
    """Send the human's yes on the card as it reads now (free).

    Parameters
    ----------
    run
        The API session.
    spine_id
        The desk's story.
    episode
        Episode ordinal.

    Returns
    -------
    dict[str, Any]
        The card after the yes.

    Raises
    ------
    PitchError
        The script is not written yet, or the card moved on while it was
        being approved (show the new draft and ask again).
    """

    card = read_card(run, spine_id, episode)
    if card.get("status") == "not_ready":
        raise PitchError(
            f"Episode {episode}'s script is not written yet, so there is no card to approve."
        )
    if card.get("approved"):
        return card
    body = {
        "pitch_digest": card.get("pitch_digest"),
        "draft": card.get("draft"),
        "via": "tap",
    }
    status, answer = run.post_optional(f"{pitch_path(spine_id, episode)}/approve", body)
    if status == 200 and isinstance(answer, Mapping):
        return dict(answer)
    code, message = _error(answer)
    raise PitchError(
        f"Episode {episode}'s card was not approved ({status} {code}): {message}"
    )


def request_change(
    run: _Api, spine_id: str, episode: int, message: str
) -> dict[str, Any]:
    """Record a change the human asked for on the card they saw (free).

    Parameters
    ----------
    run
        The API session.
    spine_id
        The desk's story.
    episode
        Episode ordinal.
    message
        The change in the human's words.

    Returns
    -------
    dict[str, Any]
        The server's answer (``from_draft``, ``next_draft``).

    Raises
    ------
    PitchError
        The script is not written yet or the card moved on.
    """

    card = read_card(run, spine_id, episode)
    if card.get("status") == "not_ready":
        raise PitchError(
            f"Episode {episode}'s script is not written yet, so there is no card to change."
        )
    body = {"pitch_digest": card.get("pitch_digest"), "message": message}
    status, answer = run.post_optional(f"{pitch_path(spine_id, episode)}/changes", body)
    if status in (200, 202) and isinstance(answer, Mapping):
        return dict(answer)
    code, text = _error(answer)
    raise PitchError(
        f"The change to episode {episode}'s card was not recorded ({status} {code}): {text}"
    )


# --- the one carry from the old desk card ----------------------------------------------------------


def _desk_digest(pitch: Mapping[str, Any]) -> str:
    text = json.dumps(pitch, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def desk_pitch_yes(desk: Path, episode: int) -> bool:
    """Whether the desk's old card for the episode had the human's yes on its newest version.

    Reads ``epNN/pitch-vN.json`` and ``epNN/pitch-approvals.json`` only; never writes them.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.

    Returns
    -------
    bool
        ``True`` when the newest desk card, exactly as it reads, had a yes.
    """

    folder = Path(desk).expanduser().resolve() / f"ep{episode:02d}"
    if not folder.is_dir():
        return False
    newest: tuple[int, dict[str, Any]] | None = None
    for path in folder.iterdir():
        match = _DESK_VERSION.match(path.name)
        if not match:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pitch = record.get("pitch") if isinstance(record, dict) else None
        if isinstance(pitch, dict) and (
            newest is None or int(match.group(1)) > newest[0]
        ):
            newest = (int(match.group(1)), pitch)
    if newest is None:
        return False
    try:
        raw = json.loads((folder / _DESK_APPROVALS).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    rows = raw.get("approvals") if isinstance(raw, dict) else None
    digest = _desk_digest(newest[1])
    return any(
        isinstance(row, dict)
        and row.get("version") == newest[0]
        and row.get("digest") == digest
        for row in rows or []
    )


def carry_desk_pitch_yes(
    desk: Path, run: _Api, spine_id: str, episode: int, *, out: TextIO | None = None
) -> bool:
    """Carry the desk's old pitch yes for the episode in progress to the server's card, once.

    Only when the desk card had its yes, the server's card is written, not on
    its boards yet, and nobody acted on it on the server (draft 1, no yes): a
    show mid-episode keeps going, and the server's hold starts at its next
    episode. A card that moved on (draft 2 or later) is never carried.

    Parameters
    ----------
    desk
        Series desk.
    run
        The API session.
    spine_id
        The desk's story.
    episode
        The episode in progress.
    out
        Where to say it was carried (stderr by default).

    Returns
    -------
    bool
        ``True`` when the yes was carried now.
    """

    if not desk_pitch_yes(desk, episode):
        return False
    try:
        card = read_card(run, spine_id, episode)
    except PitchError:
        return False
    if (
        card.get("status") != "draft"
        or card.get("draft") != 1
        or card.get("approved")
        or card.get("already_made")
    ):
        return False
    try:
        approve_card(run, spine_id, episode)
    except PitchError:
        return False
    print(
        f"Carried this desk's earlier pitch yes for episode {episode} to the server's card (draft 1). "
        "From the next episode the server's card is the one the human approves.",
        file=out or sys.stderr,
    )
    return True


def carry_before_paid_step(desk: Path, episode: int) -> None:
    """Before a paid step: carry the desk's old yes for this episode, if any (best effort, free).

    Never stops the step: the server holds it if the card still needs a yes.

    Parameters
    ----------
    desk
        Series desk.
    episode
        The episode the step draws or films.
    """

    from creation.post.desk import open_api
    from creation.production_state import load_production

    desk = Path(desk).expanduser().resolve()
    if not desk_pitch_yes(desk, episode):
        return
    try:
        spine_id = load_production(desk).spine_id
    except (FileNotFoundError, ValueError):
        return
    if not spine_id:
        return
    session = open_api(desk, episode)
    try:
        carry_desk_pitch_yes(desk, session, spine_id, episode)
    except Exception:  # noqa: BLE001 - best effort: the server's hold still answers
        return
    finally:
        session.client.close()


# --- commands -------------------------------------------------------------------------------------


def _session(desk: Path, episode: int, api: _Api | None) -> tuple[_Api, Any, str]:
    from creation.post.desk import open_api, spine_id

    sid = spine_id(desk)
    if api is not None:
        return api, None, sid
    session = open_api(desk, episode)
    return session, session, sid


def run_pitch(
    desk: Path,
    *,
    episode: int,
    change: str | None = None,
    out: TextIO | None = None,
    api: _Api | None = None,
) -> int:
    """``pitch --desk D --episode N [--change "…"]``: print the server's card, or record a change (free).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    change
        A change the human asked for, recorded on the card they saw.
    out
        Output stream (stdout by default).
    api
        An API session (tests); one is opened on the desk otherwise.

    Returns
    -------
    int
        ``0``.
    """

    stream: TextIO = out if out is not None else sys.stdout
    desk = Path(desk).expanduser().resolve()
    run, session, sid = _session(desk, episode, api)
    try:
        carry_desk_pitch_yes(desk, run, sid, episode, out=stream)
        if change is not None:
            if not change.strip():
                raise ValueError("--change needs the human's words")
            answer = request_change(run, sid, episode, " ".join(change.split()))
            print(
                f"Change recorded on episode {episode}'s card (draft {answer.get('from_draft')}). Make it with "
                "`line`, `edit` or `rewrite`; once the shots change the card is the next draft and needs its "
                "own yes.",
                file=stream,
            )
        # The idea card and the episode's people, in the server's words (lighter cards v3).
        lines = read_card_lines(run, sid, episode)
        if lines is not None:
            status, spine = run.get_optional(f"/v1/spines/{sid}")
            print(
                idea_text(
                    lines,
                    spine if status == 200 and isinstance(spine, Mapping) else None,
                    episode,
                ),
                file=stream,
            )
        card = read_card(run, sid, episode)
        print(card_text(card), file=stream)
        if card.get("status") != "not_ready" and not card.get("approved"):
            print(
                f"Show the human this card; after their yes: "
                f"`fictora-produce approve --desk {desk} --gate pitch --episode {episode}`.",
                file=stream,
            )
    finally:
        if session is not None:
            session.client.close()
    return 0


def run_approve_pitch(
    desk: Path, *, episode: int, out: TextIO | None = None, api: _Api | None = None
) -> dict[str, Any]:
    """``approve --gate pitch``: send the human's yes on the server's card as it reads now (free).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    out
        Output stream (stdout by default).
    api
        An API session (tests); one is opened on the desk otherwise.

    Returns
    -------
    dict[str, Any]
        The card after the yes.
    """

    stream: TextIO = out if out is not None else sys.stdout
    desk = Path(desk).expanduser().resolve()
    run, session, sid = _session(desk, episode, api)
    try:
        card = approve_card(run, sid, episode)
    finally:
        if session is not None:
            session.client.close()
    print(
        f"Pitch approved on the server: episode {episode}, draft {card.get('draft')}. "
        "A change to the shots needs a new yes.",
        file=stream,
    )
    return card


__all__ = [
    "PITCH_NOT_APPROVED",
    "PitchError",
    "approve_card",
    "card_text",
    "carry_before_paid_step",
    "carry_desk_pitch_yes",
    "desk_pitch_yes",
    "pitch_path",
    "read_card",
    "request_change",
    "run_approve_pitch",
    "run_pitch",
]
