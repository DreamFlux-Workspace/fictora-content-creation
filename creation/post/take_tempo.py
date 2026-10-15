"""``tempo`` on a locked-voice take: set on the server, which keeps the voices at natural speed (L-20261014-20).

The kit's own ``tempo`` (``setpts`` + ``atempo`` on a finished file) re-sped
everything under the picture: on a locked-voice take the character voices
and the music too, so lines had to be re-rendered and the music and
ambience re-laid by hand, and captions timed on the take facts went up about
0.9 s late. The server now owns a take's tempo
(``POST /v1/video-generations/{job}/takes/{index}/tempo``, fictora-drama): it
plays the picture at the factor, lays the take's own voices at natural speed
where their words now start, lays its room, music and effects again at natural
speed, replaces the take and reports its take facts on the new timeline.

This module only asks and saves (the kit keeps no tempo logic):

- ``tempo --preview`` sends ``dry_run`` and prints where each line goes;
- ``tempo`` sends it, saves the take facts again and downloads the new take
  as the newest raw take, so ``finish`` lays captions, effects and the mark on
  the new timeline;
- ``tempo --revert`` puts back what the take played before (same saves).

A take whose sound the video model made (``take_tempo_native_sound``), or a
server without the route, answers ``None``: the caller runs the local tempo,
which still speeds the voices with the picture, and says so.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, TextIO

#: The server's answer that leaves the tempo to the kit's local edit.
LOCAL_TEMPO_CODES = frozenset({"take_tempo_native_sound"})


def _code(text: str) -> str | None:
    found = re.search(
        r"\b(take_tempo_[a-z_]+|take_not_[a-z_]+|video_generation_not_found)\b", text
    )
    return found.group(1) if found else None


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
        ``0`` applied, previewed or reverted; ``2`` refused; ``None`` when the
        take's voices are the video model's own (or the server has no route):
        run the local tempo.
    """

    from creation import episode_commands as ec
    from creation.harness.raw_video import fetch_take_facts
    from creation.media_fetch import download_to_versioned
    from creation.ops.notes import append_run_note
    from creation.post.desk import saved_spine
    from creation.post.take_facts import save_take_facts
    from creation.post.take_handles import take_coordinate

    desk = desk.expanduser().resolve()
    label = f"ep{episode:02d} {take_id}"
    where = take_coordinate(desk, episode, take_id)
    if where is None:
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
                idempotency_key=f"{run.prefix}-tempo-ep{episode:02d}-{take_id}-{uuid.uuid4().hex[:12]}",
            )
        except SystemExit as exc:
            text = exc.code if isinstance(exc.code, str) else str(exc.code)
            code = _code(text)
            if code in LOCAL_TEMPO_CODES:
                print(
                    f"{label}: the video model made this take's sound, voices included, so the server cannot keep "
                    "them at natural speed; the local tempo speeds them with the picture.",
                    file=out,
                )
                return None
            if code is None and re.search(r"HTTP (404|405)\b", text):
                print(
                    f"!! {label}: this server has no take tempo (fictora-drama tempo route not deployed): the local "
                    "tempo speeds the voices and music with the picture.",
                    file=out,
                )
                return None
            out.flush()
            print(f"Stopped: {text}", file=sys.stderr)
            print(f"Refused: {ec.refusal_reason(text)}", file=sys.stderr)
            return 2
        print(str(answer.get("message") or ""), file=out)
        for line in answer.get("lines") or []:
            print(
                f"  {line['line_id']}: filmed at {float(line['filmed_start_s']):.2f} s, now "
                f"{float(line['start_s']):.2f}-{float(line['end_s']):.2f} s (natural speed)",
                file=out,
            )
        if answer.get("status") == "planned":
            print(
                "Not applied: --preview showed the tempo; nothing was changed", file=out
            )
            return 0
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
            print(
                f"  take facts: `{saved.name}` (on the take's new timeline)", file=out
            )
        video = answer.get("video_url")
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
        append_run_note(run_dir, f"Tempo {label} on the server: {note}")
    print(
        f"Next: `fictora-produce finish --desk {desk} --episode {episode} --take {take_id}`",
        file=out,
    )
    return 0


__all__ = ["LOCAL_TEMPO_CODES", "run_take_tempo"]
