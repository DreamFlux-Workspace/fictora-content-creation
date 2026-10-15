"""``tempo`` is set on the server: the take plays faster or slower and every time it reports moves (L-20261014-20).

The kit's own ``tempo`` (``setpts`` + ``atempo`` on a finished file) left the
server's take facts on the filmed times, so captions timed on them went up
about 0.9 s late, and effects and finish read the old moments. The server now
owns a take's tempo (``POST /v1/video-generations/{job}/takes/{index}/tempo``,
fictora-drama): it speeds picture and the whole sound together (pitch kept, so
lips stay on their words; founder blind A/B, 15 Oct 2026), replaces the take
and moves every time it reports (take facts, trim handles, post edits, kept
captions).

This module only asks and saves (the kit keeps no tempo logic):

- ``tempo --preview`` sends ``dry_run`` and prints where each line goes;
- ``tempo`` sends it, saves the take facts again and downloads the new take
  as the newest raw take, so ``finish`` lays captions, effects and the mark on
  the new timeline;
- ``tempo --revert`` puts back what the take played before (same saves).

The request key is built from the desk, the take and the tempo, so a retry
after a lost answer is the same request. A take that already plays at a speed
(``take_tempo_already_set``) has its take and facts fetched again. Only a
server without the route (or a take the desk cannot place on its video job)
answers ``None``: the caller runs the local edit and says so.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, TextIO


def _code(text: str) -> str | None:
    found = re.search(
        r"\b(take_tempo_[a-z_]+|take_not_[a-z_]+|video_generation_not_found)\b", text
    )
    return found.group(1) if found else None


def tempo_key(prefix: str, *, episode: int, take_id: str, body: dict[str, Any]) -> str:
    """The request key: the same desk, take and tempo is the same request (a retry is never a second one).

    Parameters
    ----------
    prefix
        The desk's idempotency prefix.
    episode, take_id
        The take.
    body
        The request body.

    Returns
    -------
    str
        ``<prefix>-tempo-epNN-tK-<digest>``.
    """

    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    return f"{prefix}-tempo-ep{episode:02d}-{take_id}-{digest}"


def run_take_tempo(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    factor: float | None,
    start_s: float | None,
    end_s: float | None,
    preview: bool,
    revert: bool,
    out: TextIO,
) -> int | None:
    """Set, preview or revert a take's tempo on the server; ``None`` leaves it to the local edit.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1`` ...
    factor
        Speed inside the window (0.9 is 10% slower).
    start_s, end_s
        The window, seconds on the take as filmed (both or neither).
    preview
        Ask for the plan only (nothing changes).
    revert
        Put back what the take played before its tempo.
    out
        Text stream.

    Returns
    -------
    int | None
        ``0`` applied, previewed, reverted or already set at this speed; ``2``
        refused; ``None`` when the server has no route or the desk cannot
        place the take on its video job: run the local edit.
    """

    from creation import episode_commands as ec
    from creation.post.take_handles import take_coordinate

    desk = desk.expanduser().resolve()
    label = f"ep{episode:02d} {take_id}"
    where = take_coordinate(desk, episode, take_id)
    if where is None:
        print(
            f"!! {label}: the desk has no video job for this take, so the server cannot change its speed: the "
            "local tempo runs instead and the take facts keep their filmed times (captions after it are timed "
            "on speech spans).",
            file=out,
        )
        return None
    body: dict[str, Any] = (
        {"revert": True} if revert else {"factor": factor, "dry_run": preview}
    )
    if not revert and start_s is not None:
        body.update(from_s=start_s, to_s=end_s)
    _desk, state, run = ec._desk_session(desk)
    try:
        try:
            answer = run.post(
                f"/v1/video-generations/{where.video_job_id}/takes/{where.take_index}/tempo",
                body,
                idempotency_key=tempo_key(
                    run.prefix, episode=episode, take_id=take_id, body=body
                ),
            )
        except SystemExit as exc:
            text = exc.code if isinstance(exc.code, str) else str(exc.code)
            code = _code(text)
            if code is None and re.search(r"HTTP (404|405)\b", text):
                print(
                    f"!! {label}: this server has no take tempo (fictora-drama tempo route not deployed): the local "
                    "tempo speeds the take here and the take facts keep their filmed times.",
                    file=out,
                )
                return None
            if code == "take_tempo_already_set":
                # Set earlier (an answer lost on the way?): bring the desk up to what the server plays.
                print(f"{label}: {text.split(': ', 2)[-1]}", file=out)
                _save(desk, run, state, where, episode, take_id, video=None, out=out)
                asked = f"{float(factor):g}x" if factor is not None else None
                if asked is not None and asked in text:
                    print("Applied (already, at this speed)", file=out)
                    return 0
                out.flush()
                print(f"Refused: {ec.refusal_reason(text)}", file=sys.stderr)
                return 2
            out.flush()
            print(f"Stopped: {text}", file=sys.stderr)
            print(f"Refused: {ec.refusal_reason(text)}", file=sys.stderr)
            return 2
        print(str(answer.get("message") or ""), file=out)
        for line in answer.get("lines") or []:
            print(
                f"  {line['line_id']}: filmed at {float(line['filmed_start_s']):.2f} s, now "
                f"{float(line['start_s']):.2f}-{float(line['end_s']):.2f} s",
                file=out,
            )
        if answer.get("status") == "planned":
            print(
                "Not applied: --preview showed the tempo; nothing was changed", file=out
            )
            return 0
        _save(
            desk,
            run,
            state,
            where,
            episode,
            take_id,
            video=str(answer["video_url"]) if answer.get("video_url") else None,
            out=out,
        )
    finally:
        run.client.close()
    run_dir = desk / f"ep{episode:02d}"
    note = json.dumps(
        {
            key: answer.get(key)
            for key in ("status", "factor", "from_s", "to_s", "duration_s")
        }
    )
    if (run_dir / "run-notes.md").is_file():
        from creation.ops.notes import append_run_note

        append_run_note(run_dir, f"Tempo {label} on the server: {note}")
    print(
        f"Next: `fictora-produce finish --desk {desk} --episode {episode} --take {take_id}`",
        file=out,
    )
    return 0


def _save(
    desk: Path,
    run: Any,
    state: Any,
    where: Any,
    episode: int,
    take_id: str,
    *,
    video: str | None,
    out: TextIO,
) -> None:
    """Save the take's facts again and download its stored clip as the newest raw take."""

    from creation.harness.raw_video import fetch_take_facts
    from creation.media_fetch import download_to_versioned
    from creation.post.desk import saved_spine
    from creation.post.take_facts import save_take_facts

    facts = fetch_take_facts(run, where.take_job_id, spine_id=state.spine_id)
    if facts is None:
        print(
            "  !! take facts not saved again: run `take-facts --refresh` before finish",
            file=out,
        )
    else:
        found = saved_spine(desk, episode)
        saved = save_take_facts(desk, episode=episode, take_id=take_id, facts=facts,
                                spine=found[0] if found else None)  # fmt: skip
        print(f"  take facts: `{saved.name}` (on the take's new timeline)", file=out)
        if video is None:
            # The facts name the take's file (``playable_window.media_url``).
            window = facts.get("playable_window") if isinstance(facts, dict) else None
            video = window.get("media_url") if isinstance(window, dict) else None
    if video:
        import httpx

        with httpx.Client(timeout=180.0) as client:
            path = download_to_versioned(
                client,
                str(video),
                desk / f"ep{episode:02d}" / "takes",
                f"take-ep{episode:02d}-{take_id}-raw",
                default_suffix=".mp4",
            )
        print(f"  take: `{path.name}` (the newest raw take)", file=out)


__all__ = ["run_take_tempo", "tempo_key"]
