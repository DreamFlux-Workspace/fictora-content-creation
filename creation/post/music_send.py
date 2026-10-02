"""Send a music change note to the harness: the plan and its price first, applied only on a yes.

The music is always the harness's. An operator describes a change ("calmer",
"quieter under the lines") or asks for an earlier version back, and the
server (``POST /v1/spines/{spine_id}/music-notes``) makes it:

- a **level** note re-mixes the same bed at a new level (free);
- a **new music** note makes one new bed for the show (about $0.20);
- a **revert** puts an earlier version back (free).

Takes with a voices-and-room stem are re-mixed without filming (free); takes
whose music the video model made can only change by filming again, at the
usual take price, and only with ``--confirm-refilm``.

Every send here is two calls. First a ``dry_run``: the plan is printed in
plain words (what kind of change, which takes re-mix free, which need a
re-film and their price, what new music costs) and nothing is made or
spent. Then, only with ``--yes``, the same note is applied. Every note is
saved on the desk first (``shared/music-notes.jsonl``) with a stable id; the
apply call's ``Idempotency-Key`` is built from that id, so a note re-sent
after a dropped connection is the same request, never a second bed. What
the harness answered is written back onto the entry (``status``,
``version``, ``job_id``, ``spent_usd``) and the full answers are kept under
``shared/music-notes/``. An applied note is never sent again.

After an apply, each episode whose takes were re-mixed is put together again
on the server; this polls the re-mixed takes' facts (``refinish``) and says
when the delivered cut changed.
"""

from __future__ import annotations

import json
import shlex
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.post.bed import (
    MUSIC_NOTES_FILE,
    music_note_entries,
    save_music_note,
    update_music_note,
)

#: Where the harness's full answers to sent notes are kept on the desk.
MUSIC_NOTE_ANSWERS = Path("shared") / "music-notes"
#: How long ``music-note --yes`` waits for the episodes to be put together again (``--wait``).
DEFAULT_WAIT_SECONDS = 600.0
#: Refinish states still in progress.
IN_PROGRESS = frozenset({"queued", "running"})
#: Exit code when the change did not run (blocked, refused, or a re-film without a verified price).
NOT_APPLIED = 2

Sleep = Callable[[float], None]
_sleep: Sleep = time.sleep


def _route(spine_id: str) -> str:
    return f"/v1/spines/{spine_id}/music-notes"


def _money(value: Any) -> str:
    return f"${float(value):.2f}"


def _scope_words(entry: Mapping[str, Any]) -> str:
    if entry.get("episode") is None:
        return "the whole show"
    return f"ep{int(entry['episode']):02d}" + (
        f" {entry['take']}" if entry.get("take") else ""
    )


def _what(entry: Mapping[str, Any]) -> str:
    if entry.get("revert_to_version") is not None:
        return f"put music v{entry['revert_to_version']} back"
    return f'"{entry["note"]}"'


def request_body(
    entry: Mapping[str, Any], *, dry_run: bool, confirm_refilm: bool
) -> dict[str, Any]:
    """The ``music-notes`` request for one saved entry."""

    body: dict[str, Any] = {"dry_run": dry_run, "confirm_refilm": confirm_refilm}
    if entry.get("revert_to_version") is not None:
        body["revert_to_version"] = int(entry["revert_to_version"])
    else:
        body["note"] = str(entry["note"])
    if entry.get("episode") is not None:
        body["episode_ordinal"] = int(entry["episode"])
    if entry.get("take"):
        body["take"] = str(entry["take"])
    return body


def apply_key(prefix: str, entry: Mapping[str, Any], *, confirm_refilm: bool) -> str:
    """The stable ``Idempotency-Key`` for applying one note.

    Built from the desk's idempotency prefix and the note's saved id, so the
    same note sent again (a dropped connection, a re-run) is the same request.
    ``attempt`` moves on only after the harness answered without running it
    (blocked, refused), so a later try is a new request, not that answer replayed.
    """

    attempt = int(entry.get("attempt") or 1)
    return f"{prefix}-music-note-{entry['id']}-apply-{attempt}" + (
        "-refilm" if confirm_refilm else ""
    )


def _take_label(take: Mapping[str, Any]) -> str:
    episode = take.get("episode_ordinal")
    number = take.get("take") or take.get("take_index")
    head = (
        f"ep{int(episode):02d}"
        if episode is not None
        else str(take.get("episode_id") or "?")
    )
    return f"{head} t{number}" if number is not None else head


def _kind_line(answer: Mapping[str, Any]) -> str:
    kind = answer.get("kind")
    cost = answer.get("cost") or {}
    if kind == "level_only":
        return "level only: the same music re-mixed at a new level. Free, nothing new is made."
    if kind == "revert":
        version = answer.get("version") or {}
        which = f" v{version['version']}" if version.get("version") else ""
        return (
            f"revert: the earlier music{which} is put back. Free, nothing new is made."
        )
    if kind == "new_music":
        return (
            f"new music: one new bed for the show, {_money(cost.get('new_bed_usd') or 0)}. "
            "Every take filmed later uses it too."
        )
    return f"{kind}"


def plan_lines(answer: Mapping[str, Any], *, confirm_refilm: bool) -> list[str]:
    """The harness's plan (or what ran) in plain words, one line each.

    Parameters
    ----------
    answer
        The ``music-notes`` response.
    confirm_refilm
        Whether re-films were confirmed on this call.

    Returns
    -------
    list[str]
        Operator lines.
    """

    lines = [f"  What it is: {_kind_line(answer)}"]
    if answer.get("direction"):
        lines.append(f"  Words sent for the new music: {answer['direction']}")
    version = answer.get("version") or {}
    if version.get("version"):
        based = f", based on v{version['based_on']}" if version.get("based_on") else ""
        note = f" ({version['note']})" if version.get("note") else ""
        lines.append(f"  Music version: v{version['version']}{note}{based}")
    takes = [take for take in answer.get("takes") or [] if isinstance(take, Mapping)]
    if takes:
        lines.append("  Takes:")
    for take in takes:
        label = _take_label(take)
        reason = f" ({take['reason']})" if take.get("reason") else ""
        action = take.get("action")
        if action == "remix":
            done = " - re-mixed" if take.get("remixed") else ""
            lines.append(
                f"    {label}: re-mixed onto its voices, free, no filming{done}{reason}"
            )
        elif action == "refilm":
            price = (
                _money(take["refilm_usd"])
                if take.get("refilm_usd") is not None
                else "no verified price"
            )
            filmed = (
                f" - filming again: job {take['refilm_job_id']}"
                if take.get("refilm_job_id")
                else ""
            )
            lines.append(
                f"    {label}: its music is the video model's own; only filming it again changes it, "
                f"{price}{filmed}{reason}"
            )
        else:
            lines.append(f"    {label}: unchanged{reason}")
    if not takes:
        lines.append(
            "  Takes: none delivered in scope yet; every take filmed from now on uses this music."
        )
    cost = answer.get("cost") or {}
    parts = [f"new music {_money(cost.get('new_bed_usd') or 0)}"]
    count = int(cost.get("refilm_take_count") or 0)
    if count:
        price = (
            _money(cost["refilm_usd"])
            if cost.get("refilm_usd") is not None
            else "no verified price"
        )
        when = "confirmed" if confirm_refilm else "only with --confirm-refilm"
        parts.append(f"filming {count} take(s) again {price} ({when})")
    if cost.get("spent_usd"):
        parts.append(f"spent by this call {_money(cost['spent_usd'])}")
    lines.append("  Cost: " + "; ".join(parts))
    if answer.get("message"):
        lines.append(f"  Harness says: {answer['message']}")
    return lines


def _save_answer(desk: Path, entry: Mapping[str, Any], stage: str, answer: Any) -> Path:
    folder = desk / MUSIC_NOTE_ANSWERS
    folder.mkdir(parents=True, exist_ok=True)
    path = next_versioned_path(folder, f"{entry['id']}-{stage}", ".json")
    path.write_text(
        json.dumps(answer, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _refused(text: str) -> bool:
    """A 4xx answer: the server said no to this request (resending the same one will not help)."""

    return text.startswith("HTTP 4") and not text.startswith("HTTP 409")


def _apply_command(desk: Path, entry: Mapping[str, Any], *, refilm: bool) -> str:
    parts = ["fictora-produce", "music-note", "--desk", str(desk)]
    if entry.get("revert_to_version") is not None:
        parts += ["--revert", str(entry["revert_to_version"])]
    else:
        if entry.get("episode") is not None:
            parts += ["--episode", str(entry["episode"])]
        if entry.get("take"):
            parts += ["--take", str(entry["take"])]
        parts.append(str(entry["note"]))
    parts.append("--yes")
    if refilm:
        parts.append("--confirm-refilm")
    return shlex.join(parts)


def _book(desk: Path, entry: Mapping[str, Any], usd: float, out: TextIO) -> None:
    if usd <= 0:
        return
    from creation.post.finish import book

    book(
        desk,
        episode=int(entry.get("episode") or 1),
        usd=usd,
        take_id=entry.get("take"),
        stream=out,
        unit="music-note",
    )


def poll_refinish(
    run: Any,
    answer: Mapping[str, Any],
    *,
    spine_id: str,
    wait_seconds: float,
    out: TextIO,
    sleep: Sleep | None = None,
) -> dict[str, dict[str, Any]]:
    """Follow each re-mixed episode's re-finish through its takes' facts and say where it stands.

    Parameters
    ----------
    run
        Harness session.
    answer
        The applied ``music-notes`` response.
    spine_id
        The story.
    wait_seconds
        How long to keep polling while an episode is ``queued`` or ``running``; 0 reads once.
    out
        Where the lines go.
    sleep
        Pause between polls (tests pass a no-op).

    Returns
    -------
    dict[str, dict[str, Any]]
        Each episode id's last ``refinish`` (``state``, ``video_url``, ``reason``...).
    """

    from creation.harness.raw_video import fetch_take_facts

    pause = sleep or _sleep
    takes = [t for t in answer.get("takes") or [] if isinstance(t, Mapping)]
    labels: dict[str, str] = {}
    take_jobs: dict[str, str] = {}
    for take in takes:
        episode_id = str(take.get("episode_id") or "")
        if take.get("episode_ordinal") is not None:
            labels.setdefault(episode_id, f"ep{int(take['episode_ordinal']):02d}")
        if take.get("action") == "remix" and take.get("take_job_id"):
            take_jobs.setdefault(episode_id, str(take["take_job_id"]))
    states: dict[str, dict[str, Any]] = {}
    for episode in answer.get("episodes") or []:
        if not isinstance(episode, Mapping):
            continue
        episode_id = str(episode.get("episode_id") or "")
        refinish = episode.get("refinish")
        if isinstance(refinish, Mapping):
            states[episode_id] = dict(refinish)
        else:
            states[episode_id] = {
                "state": "unavailable",
                "reason": episode.get("reason")
                or "the server did not put this episode together again",
            }
    interval = float(getattr(run, "poll_interval_seconds", 15.0) or 15.0)
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    while True:
        pending = [
            eid for eid, state in states.items()
            if state.get("state") in IN_PROGRESS and eid in take_jobs
        ]  # fmt: skip
        for eid in pending:
            facts = fetch_take_facts(run, take_jobs[eid], spine_id=spine_id)
            refinish = (facts or {}).get("refinish")
            if isinstance(refinish, Mapping) and refinish.get("episode_id") in (
                None,
                eid,
            ):
                states[eid] = dict(refinish)
        pending = [eid for eid in pending if states[eid].get("state") in IN_PROGRESS]
        if not pending or time.monotonic() >= deadline:
            break
        pause(interval)
    for eid, state in states.items():
        label = labels.get(eid, eid)
        phase = state.get("state")
        if phase == "done":
            url = state.get("video_url")
            print(
                f"  {label}: the delivered cut changed: it now has the new music. Re-download it"
                + (f": {url}" if url else "")
                + " (any copy on this laptop or already posted has the old music).",
                file=out,
            )
        elif phase in IN_PROGRESS:
            print(
                f"  {label}: still being put together again ({phase}); the old cut plays until it is done. "
                f"Check later with `fictora-produce take-facts --desk … --episode N --take tK --refresh` "
                "(refinish), then re-download the episode.",
                file=out,
            )
        else:
            print(
                f"  !! {label}: not put together again ({phase or 'unknown'}): "
                f"{state.get('reason') or 'no reason given'}. The old cut still plays.",
                file=out,
            )
    return states


def send_entry(
    desk: Path,
    entry: dict[str, Any],
    *,
    yes: bool,
    confirm_refilm: bool,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    out: TextIO | None = None,
    sleep: Sleep | None = None,
) -> int:
    """Plan one saved note on the server, and apply it on a yes.

    Parameters
    ----------
    desk
        Series desk (resolved).
    entry
        The saved note (from :func:`creation.post.bed.save_music_note`).
    yes
        Apply after the plan. Without it nothing is changed or spent.
    confirm_refilm
        Also film again the takes whose music the video model made (their price is shown first).
    wait_seconds
        How long to follow the episodes being put together again.
    out
        Where the lines go.
    sleep
        Pause between refinish polls.

    Returns
    -------
    int
        ``0`` planned or applied; :data:`NOT_APPLIED` when it did not run.
    """

    from creation import episode_commands as ec

    out = out or sys.stdout
    if entry.get("status") == "applied":
        print(
            f"Music note {_what(entry)} ({_scope_words(entry)}) was already applied"
            + (f" (music v{entry['version']})" if entry.get("version") else "")
            + "; it is never sent twice. Describe a new change to change it again.",
            file=out,
        )
        return 0
    _desk, state, run = ec._desk_session(desk)
    spine_id = state.spine_id or ""
    try:
        print(
            f"Music note {_what(entry)} for {_scope_words(entry)}: the harness's plan "
            "(a dry run: nothing is made, re-mixed, filmed or spent)",
            file=out,
        )
        try:
            plan = run.post(
                _route(spine_id),
                request_body(entry, dry_run=True, confirm_refilm=confirm_refilm),
                idempotency_key=f"{run.prefix}-music-note-{entry['id']}-plan-{uuid.uuid4().hex[:8]}",
            )
        except SystemExit as exc:
            text = str(exc.code)
            status = "refused" if _refused(text) else entry.get("status")
            update_music_note(
                desk, entry["id"], status=status, last_error=text, sent_at=_now()
            )
            print(
                f"!! the harness did not plan it: {text}. Nothing was changed.",
                file=out,
            )
            return NOT_APPLIED
        saved = _save_answer(desk, entry, "plan", plan)
        for line in plan_lines(plan, confirm_refilm=confirm_refilm):
            print(line, file=out)
        cost = plan.get("cost") or {}
        refilm_count = int(cost.get("refilm_take_count") or 0)
        if plan.get("status") == "blocked":
            update_music_note(
                desk, entry["id"], status="blocked", kind=plan.get("kind"),
                response=str(saved.relative_to(desk)), sent_at=_now(),
            )  # fmt: skip
            print(
                "Blocked: this change needs new music and the harness has provider spend off. "
                "Nothing was changed; the note stays saved (send it later with --send-saved).",
                file=out,
            )
            return NOT_APPLIED
        update_music_note(
            desk, entry["id"], status="planned", kind=plan.get("kind"),
            response=str(saved.relative_to(desk)), sent_at=_now(),
        )  # fmt: skip
        if confirm_refilm and refilm_count and cost.get("refilm_usd") is None:
            print(
                "!! Not filming: a take to film again has no verified price, so the total cannot be shown. "
                "Run it without --confirm-refilm (those takes keep their old music) or ask the core team.",
                file=out,
            )
            return NOT_APPLIED
        if not yes:
            total = float(cost.get("new_bed_usd") or 0) + (
                float(cost.get("refilm_usd") or 0) if confirm_refilm else 0.0
            )
            print(
                f"Not applied: nothing was changed or spent. Applying costs {_money(total)}. To apply: "
                f"{_apply_command(desk, entry, refilm=confirm_refilm)}",
                file=out,
            )
            if refilm_count and not confirm_refilm:
                print(
                    f"  {refilm_count} take(s) keep their old music unless filmed again "
                    f"({_money(cost['refilm_usd']) if cost.get('refilm_usd') is not None else 'no verified price'} "
                    "total): add --confirm-refilm to film them again.",
                    file=out,
                )
            return 0
        if refilm_count and not confirm_refilm:
            print(
                f"  {refilm_count} take(s) whose music the video model made keep their old music: "
                "only --confirm-refilm (with its price) films them again.",
                file=out,
            )
        key = apply_key(run.prefix, entry, confirm_refilm=confirm_refilm)
        update_music_note(desk, entry["id"], idempotency_key=key)
        try:
            answer = run.post(
                _route(spine_id),
                request_body(entry, dry_run=False, confirm_refilm=confirm_refilm),
                idempotency_key=key,
            )
        except SystemExit as exc:
            text = str(exc.code)
            if _refused(text):
                update_music_note(
                    desk, entry["id"], status="refused", last_error=text,
                    attempt=int(entry.get("attempt") or 1) + 1, sent_at=_now(),
                )  # fmt: skip
            else:
                update_music_note(desk, entry["id"], last_error=text, sent_at=_now())
            print(
                f"!! not applied: {text}. "
                + (
                    "The note stays saved; fix it and send again."
                    if _refused(text)
                    else "Sending it again (same command, or --send-saved) repeats the same request, "
                    "never a second change."
                ),
                file=out,
            )
            return NOT_APPLIED
        saved = _save_answer(desk, entry, "applied", answer)
        status = str(answer.get("status") or "")
        spent = float((answer.get("cost") or {}).get("spent_usd") or 0)
        version = (answer.get("version") or {}).get("version")
        fields: dict[str, Any] = {
            "status": status or "applied",
            "kind": answer.get("kind"),
            "version": version,
            "job_id": answer.get("job_id"),
            "spent_usd": spent,
            "refilm_confirmed": confirm_refilm or None,
            "response": str(saved.relative_to(desk)),
            "sent_at": _now(),
        }
        if status == "blocked":
            fields["attempt"] = int(entry.get("attempt") or 1) + 1
        update_music_note(desk, entry["id"], **fields)
        print(
            f"Applied: {answer.get('message') or status}"
            if status == "applied"
            else f"Not applied ({status}): {answer.get('message')}",
            file=out,
        )
        for line in plan_lines(answer, confirm_refilm=confirm_refilm)[1:]:
            print(line, file=out)
        if status != "applied":
            return NOT_APPLIED
        _book(desk, entry, spent, out)
        if (
            entry.get("episode") is not None
            and (desk / f"ep{int(entry['episode']):02d}" / "run-notes.md").is_file()
        ):
            append_run_note(
                desk / f"ep{int(entry['episode']):02d}",
                f"Music note {_what(entry)} applied by the harness: {answer.get('kind')}"
                + (f", music v{version}" if version else "")
                + f", ${spent:.2f} (job {answer.get('job_id')})",
            )
        if any(
            isinstance(take, Mapping) and take.get("action") == "remix"
            for take in answer.get("takes") or []
        ):
            print(
                "  Re-mixed takes' stored files changed on the server: their take files and saved facts on this "
                "laptop have the old music (`take-facts --refresh` reads the new ones).",
                file=out,
            )
        if answer.get("episodes"):
            print("Episodes:", file=out)
            poll_refinish(
                run, answer, spine_id=spine_id, wait_seconds=wait_seconds, out=out, sleep=sleep,
            )  # fmt: skip
        return 0
    finally:
        run.client.close()


def unsent_entries(desk: Path) -> list[dict[str, Any]]:
    """Saved notes the harness has not applied yet, oldest first."""

    return [
        entry for entry in music_note_entries(desk) if entry.get("status") != "applied"
    ]


def run_music_note(
    desk: Path,
    *,
    note: str | None = None,
    episode: int | None = None,
    take_id: str | None = None,
    revert: int | None = None,
    send_saved: bool = False,
    save_only: bool = False,
    yes: bool = False,
    confirm_refilm: bool = False,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    out: TextIO | None = None,
    sleep: Sleep | None = None,
) -> int:
    """``fictora-produce music-note``: save the note, show the harness's plan, apply on ``--yes``.

    Parameters
    ----------
    desk
        Series desk.
    note
        The change in the operator's words.
    episode, take_id
        Where it applies (``None``: the whole show).
    revert
        Put this earlier music version back instead.
    send_saved
        Send every saved note not yet applied (each planned first; ``--yes`` applies).
    save_only
        Only save the note on the desk (send later with ``--send-saved``).
    yes, confirm_refilm, wait_seconds, out, sleep
        See :func:`send_entry`.

    Returns
    -------
    int
        ``0`` done; :data:`NOT_APPLIED` when any change did not run.

    Raises
    ------
    ValueError
        On a wrong combination of flags or an empty note.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    asked = sum(bool(x) for x in (note is not None, revert is not None, send_saved))
    if asked != 1:
        raise ValueError(
            'music-note takes one of: a note ("calmer"), --revert N, or --send-saved'
        )
    if send_saved and (episode is not None or take_id is not None or save_only):
        raise ValueError(
            "--send-saved sends every saved note as it was saved: no --episode, --take or --save-only"
        )
    if revert is not None and (take_id is not None or save_only):
        raise ValueError(
            "--revert puts a version back for the show (or one --episode): no --take or --save-only"
        )
    if confirm_refilm and save_only:
        raise ValueError("--confirm-refilm goes with sending, not --save-only")
    if send_saved:
        pending = unsent_entries(desk)
        if not pending:
            print(
                f"No saved music notes waiting to be sent ({desk / MUSIC_NOTES_FILE}).",
                file=out,
            )
            return 0
        print(f"{len(pending)} saved music note(s) not applied yet:", file=out)
        worst = 0
        for entry in pending:
            code = send_entry(
                desk, entry, yes=yes, confirm_refilm=confirm_refilm,
                wait_seconds=wait_seconds, out=out, sleep=sleep,
            )  # fmt: skip
            worst = max(worst, code)
        return worst
    entry, new = save_music_note(
        desk, note, episode=episode, take_id=take_id, revert_to_version=revert
    )
    where = f"{desk / MUSIC_NOTES_FILE} (note {entry['id']}{'' if new else ', saved before'})"
    if save_only:
        print(
            f"Music note saved, not sent: {where}. Send it with "
            f"`fictora-produce music-note --desk {desk} --send-saved` (a plan first; --yes applies).",
            file=out,
        )
        return 0
    print(f"Saved: {where}", file=out)
    return send_entry(
        desk, entry, yes=yes, confirm_refilm=confirm_refilm,
        wait_seconds=wait_seconds, out=out, sleep=sleep,
    )  # fmt: skip


__all__: Sequence[str] = (
    "DEFAULT_WAIT_SECONDS",
    "MUSIC_NOTE_ANSWERS",
    "NOT_APPLIED",
    "apply_key",
    "plan_lines",
    "poll_refinish",
    "request_body",
    "run_music_note",
    "send_entry",
    "unsent_entries",
)
