"""Re-score already-made episodes with the harness's current music rules, then rebuild the kit's episode file.

Music rules change (since 14 Oct 2026: one continuous piece under each
episode, picked from the labelled library, swelling into its key line, a
gentler mix), and each change only reaches takes filmed after it. ``fictora-produce rescore-music``
asks the harness to give an older episode, or the whole show, the music a take
filmed today would get (``POST /v1/spines/{spine_id}/music-rescore``):

- **opt-in and planned first.** Every run sends a plan (``dry_run``) and prints
  it in plain words: per episode, which takes get the new music, which are
  skipped and why (the video model made the music, no stored voices-and-room
  track, a music note the creator scoped there, the story changed, a later
  re-roll, already current), and the cost (only newly made pieces cost
  anything). Nothing is changed or spent without ``--yes``.
- **never filming.** The harness lays the new music on each take's stored
  voices-and-room track and joins its episode again (a new cut; the old one is
  kept). The kit never lays, uploads or edits music itself.
- **revert.** ``--revert`` puts back what the latest re-score replaced
  (``--rescore-id`` names one re-score), free.

After the harness applies (or reverts), it puts each changed episode together
again on the server with the new music. For each changed episode, in episode
order, the kit then asks the server for the finished episode and downloads it
under the next readable name (``<slug>_epNN_draft_vN_<date>.mp4``), exactly as
``finish`` does (:mod:`creation.post.server_finish`; since 30 Oct 2026 nothing is
finished on this laptop and no take is downloaded raw). Nothing is overwritten.
When the server's new cut is not written yet, the old file stays and the kit
says to run ``finish`` again in a few minutes.
"""

from __future__ import annotations

import json
import shlex
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.post.episode_name import episode_files
from creation.production_state import paid_key_prefix

#: Every re-score (and revert) the kit applied, one JSON line each.
RESCORES_FILE = Path("shared") / "music-rescores.jsonl"
#: The harness's full answers (plans and applies).
RESCORE_ANSWERS = Path("shared") / "music-rescores"
#: Exit code when the harness did not apply it (refused, or the connection dropped).
NOT_APPLIED = 2
#: Exit code when the harness applied it but an episode file on this laptop was not rebuilt.
REBUILD_NOT_DONE = 5


def _route(spine_id: str) -> str:
    return f"/v1/spines/{spine_id}/music-rescore"


def _money(value: Any) -> str:
    return f"${float(value or 0):.2f}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _scope(episode: int | None) -> str:
    return "the whole show" if episode is None else f"ep{episode:02d}"


def request_body(
    *,
    episode: int | None,
    revert: bool,
    rescore_id: str | None,
    apply: bool,
) -> dict[str, Any]:
    """The ``music-rescore`` request: a plan (``dry_run``) or an apply (``confirm``).

    Parameters
    ----------
    episode
        One episode; ``None`` is the whole show (the harness goes in episode order).
    revert
        Put back what the latest re-score replaced.
    rescore_id
        With ``revert``: only undo this re-score.
    apply
        ``True`` sends ``confirm``; ``False`` sends ``dry_run``.

    Returns
    -------
    dict[str, Any]
        The JSON body.
    """

    body: dict[str, Any] = {"confirm": True} if apply else {"dry_run": True}
    if episode is not None:
        body["episode_ordinal"] = int(episode)
    if revert:
        body["revert"] = True
        if rescore_id:
            body["rescore_id"] = rescore_id
    return body


# --- the desk's record of re-scores -------------------------------------------------------------


def rescore_entries(desk: Path) -> list[dict[str, Any]]:
    """Every saved re-score entry, oldest first."""

    path = desk / RESCORES_FILE
    if not path.is_file():
        return []
    found: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("id"):
            found.append(entry)
    return found


def _write_entries(desk: Path, entries: list[dict[str, Any]]) -> None:
    path = desk / RESCORES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text(
        "".join(json.dumps(entry, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )
    tmp.replace(path)


def _update_entry(desk: Path, entry_id: str, **fields: Any) -> dict[str, Any]:
    entries = rescore_entries(desk)
    for entry in entries:
        if entry["id"] == entry_id:
            entry.update({k: v for k, v in fields.items() if v is not None})
            _write_entries(desk, entries)
            return entry
    raise KeyError(f"no saved re-score {entry_id}")


def _pending_entry(desk: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    """The entry an apply is sent under: one still ``sending`` for the same request, else a new one.

    A run whose answer was lost (a dropped connection) is run again with the same
    ``Idempotency-Key``, so the harness answers with what it already did instead of
    re-scoring twice.
    """

    entries = rescore_entries(desk)
    for entry in reversed(entries):
        if entry.get("status") == "sending" and entry.get("body") == dict(body):
            return entry
    entry = {
        "id": "r" + uuid.uuid4().hex[:11],
        "body": dict(body),
        "status": "sending",
        "attempt": 1,
        "created_at": _now(),
    }
    entries.append(entry)
    _write_entries(desk, entries)
    return entry


def _save_answer(desk: Path, stage: str, answer: Any) -> Path:
    folder = desk / RESCORE_ANSWERS
    folder.mkdir(parents=True, exist_ok=True)
    path = next_versioned_path(folder, f"rescore-{stage}", ".json")
    path.write_text(
        json.dumps(answer, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


# --- the plan in plain words ---------------------------------------------------------------------


def _take_label(take: Mapping[str, Any]) -> str:
    episode = take.get("episode_ordinal")
    number = take.get("take") or take.get("take_index")
    head = (
        f"ep{int(episode):02d}"
        if episode is not None
        else str(take.get("episode_id") or "?")
    )
    return f"{head} t{number}" if number is not None else head


def _music_words(music: Any) -> str:
    if not isinstance(music, Mapping):
        return ""
    if music.get("laid") is False:
        return "no music"
    parts = []
    if music.get("version"):
        parts.append(f"music v{music['version']}")
    piece = music.get("file") or music.get("url")
    if piece:
        parts.append(str(piece).rsplit("/", 1)[-1])
    return ", ".join(parts)


def plan_lines(answer: Mapping[str, Any]) -> list[str]:
    """The harness's plan (or what ran) in plain words, one line each, in episode order.

    Parameters
    ----------
    answer
        The ``music-rescore`` response.

    Returns
    -------
    list[str]
        Operator lines.
    """

    revert = answer.get("kind") == "revert"
    takes = [t for t in answer.get("takes") or [] if isinstance(t, Mapping)]
    takes.sort(
        key=lambda t: (
            t.get("episode_ordinal") or 0,
            t.get("take") or t.get("take_index") or 0,
        )
    )
    lines: list[str] = []
    if not takes:
        lines.append("  Takes: none delivered in scope.")
    else:
        lines.append("  Takes:")
    applied = answer.get("status") == "applied"
    for take in takes:
        label = _take_label(take)
        action = take.get("action")
        if action == "skip":
            lines.append(
                f"    {label}: skipped: {take.get('reason') or 'no reason given'}"
            )
            continue
        before, after = (
            _music_words(take.get("previous_music")),
            _music_words(take.get("music")),
        )
        change = (
            f" ({before} -> {after})" if before and after and before != after else ""
        )
        what = (
            "the music it played before the re-score is put back, free"
            if revert
            else "gets the music today's rules give, laid on its own voices and room, no filming"
        )
        cues = int(take.get("new_cue_count") or 0)
        price = (
            f"; {cues} new cue version(s), {_money(take.get('new_cues_usd'))}"
            if cues
            else ""
        )
        if applied:
            done = (
                " - done"
                if take.get("changed")
                else f" - NOT changed: {take.get('reason') or 'no reason given'}"
            )
        else:
            done = ""
        lines.append(f"    {label}: {what}{change}{price}{done}")
    cost = answer.get("cost") or {}
    if revert:
        lines.append("  Cost: free (nothing new is made)")
    else:
        spend = (
            "the harness may make new cue versions"
            if cost.get("spend_allowed", True)
            else "provider spend is off on the harness: a section with no version yet plays one the show or "
            "the library already has, nothing is paid"
        )
        tail = (
            f"; spent by this call {_money(cost['spent_usd'])}"
            if cost.get("spent_usd")
            else ""
        )
        lines.append(
            f"  Cost: {_money(cost.get('new_cues_usd'))} for {int(cost.get('new_cue_count') or 0)} new cue "
            f"version(s) (only new versions cost anything; {spend}){tail}"
        )
    if answer.get("message"):
        lines.append(f"  Harness says: {answer['message']}")
    return lines


def _actionable(answer: Mapping[str, Any]) -> int:
    return sum(
        1
        for take in answer.get("takes") or []
        if isinstance(take, Mapping) and take.get("action") in ("rescore", "revert")
    )


def _command(
    desk: Path, episode: int | None, *, revert: bool, rescore_id: str | None
) -> str:
    parts = ["fictora-produce", "rescore-music", "--desk", str(desk)]
    parts += ["--all"] if episode is None else ["--episode", str(episode)]
    if revert:
        parts.append("--revert")
        if rescore_id:
            parts += ["--rescore-id", rescore_id]
    parts.append("--yes")
    return shlex.join(parts)


def _refused(text: str) -> bool:
    """A 4xx other than 409: the server said no (resending the same request will not help)."""

    return text.startswith("HTTP 4") and not text.startswith("HTTP 409")


# --- rebuilding the kit's episode file -----------------------------------------------------------


@dataclass
class EpisodeRebuild:
    """What happened to one changed episode's file on this laptop."""

    episode: int
    #: ``rebuilt``, ``not_rebuilt`` (the old file stays), or ``nothing_to_rebuild`` (no joined file here).
    state: str
    file: Path | None = None
    lines: list[str] = field(default_factory=list)


def _refresh_facts(
    run: Any, desk: Path, episode: int, take_id: str, job_id: str, spine_id: str
) -> None:
    """Save the take's facts as they are after the re-score (finish reads the newest)."""

    from creation.harness.raw_video import fetch_take_facts
    from creation.post.desk import saved_spine
    from creation.post.take_facts import save_take_facts

    facts = fetch_take_facts(run, job_id, spine_id=spine_id)
    if facts is None:
        return
    found = saved_spine(desk, episode)
    save_take_facts(
        desk,
        episode=episode,
        take_id=take_id,
        facts=facts,
        spine=found[0] if found else None,
    )


def _server_finish(desk: Path, episode: int, out: TextIO) -> Any:
    """Ask the server for the episode, finished, and download it (:mod:`creation.post.server_finish`)."""

    from creation.post.server_finish import run_server_finish

    return run_server_finish(desk, episode=episode, stream=out)


def rebuild_episode(
    desk: Path,
    episode: int,
    takes: Sequence[Mapping[str, Any]],
    *,
    run: Any,
    spine_id: str,
    out: TextIO,
) -> EpisodeRebuild:
    """Get one changed episode's new file from the server (item 01, phase 4; 30 Oct 2026).

    The harness puts the episode together again with the new music on the server; the kit
    asks the server for the finished episode and downloads it (:func:`_server_finish`).
    Nothing is downloaded raw or finished on this laptop. When the server still hands back
    the cut already on the laptop, its new cut is not written yet: the old file stays and
    the line says to run ``finish`` again in a few minutes.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    takes
        The episode's changed takes from the apply answer.
    run
        Harness session (for the takes' new facts).
    spine_id
        The story.
    out
        Where the lines go.

    Returns
    -------
    EpisodeRebuild
        ``rebuilt`` with the new file, ``not_rebuilt`` with why (the old file stays), or
        ``nothing_to_rebuild`` (no episode file on this laptop yet).
    """

    report = EpisodeRebuild(episode=episode, state="not_rebuilt")
    folder = desk / f"ep{episode:02d}" / "takes"
    for take in takes:
        number = take.get("take")
        if number is None or not take.get("take_job_id"):
            continue
        try:
            _refresh_facts(
                run,
                desk,
                episode,
                f"t{int(number)}",
                str(take["take_job_id"]),
                spine_id,
            )
        except SystemExit:
            pass  # the take facts on the desk are for reading only; the server finishes
    if not episode_files(folder, episode):
        report.state = "nothing_to_rebuild"
        report.lines.append(
            "no episode file on this laptop yet: nothing to rebuild; `finish` downloads the new cut"
        )
        return report
    done = _finish(desk, episode, out)
    if not done.complete:
        report.lines.append(f"{done.error} The old file stays.")
        return report
    if done.reused:
        report.lines.append(
            "the server is still putting the episode together with the new music; the old file stays. "
            f"Run `fictora-produce finish --desk {desk} --episode {episode}` again in a few minutes."
        )
        return report
    report.state, report.file = "rebuilt", done.video
    report.lines.append(f"new episode file: {done.video.name} (the old ones are kept)")
    return report


#: The seam tests replace (the real one asks the server and downloads).
_finish = _server_finish


# --- the command ---------------------------------------------------------------------------------


def run_rescore_music(
    desk: Path,
    *,
    episode: int | None,
    all_episodes: bool = False,
    revert: bool = False,
    rescore_id: str | None = None,
    yes: bool = False,
    out: TextIO | None = None,
) -> int:
    """``fictora-produce rescore-music``: the harness's plan first; on ``--yes`` apply and rebuild the kit file.

    Parameters
    ----------
    desk
        Series desk.
    episode
        One episode (``--episode N``).
    all_episodes
        The whole show (``--all``), in episode order.
    revert
        Put back what the latest re-score replaced (``--revert``).
    rescore_id
        With ``revert``: only undo this re-score.
    yes
        Apply after the plan. Without it nothing is changed or spent.
    out
        Where the lines go.

    Returns
    -------
    int
        ``0`` planned, applied and rebuilt; :data:`NOT_APPLIED` when the harness did
        not apply it; :data:`REBUILD_NOT_DONE` when it applied but an episode file on
        this laptop was not rebuilt.

    Raises
    ------
    ValueError
        On a wrong combination of flags.
    """

    from creation import episode_commands as ec

    out = out or sys.stdout
    if (episode is None) == (not all_episodes):
        raise ValueError(
            "rescore-music takes one of --episode N or --all (the whole show)"
        )
    if rescore_id and not revert:
        raise ValueError(
            "--rescore-id names the re-score to undo: use it with --revert"
        )
    desk = desk.expanduser().resolve()
    scope = _scope(episode)
    _desk, state, run = ec._desk_session(desk)
    spine_id = state.spine_id or ""
    try:
        what = (
            "Put back the music from before the re-score"
            if revert
            else "Re-score with today's music rules"
        )
        print(
            f"{what} for {scope}: the harness's plan (a dry run: nothing is made, laid, filmed or spent)",
            file=out,
        )
        plan_body = request_body(
            episode=episode, revert=revert, rescore_id=rescore_id, apply=False
        )
        try:
            plan = run.post(
                _route(spine_id),
                plan_body,
                idempotency_key=f"{run.prefix}-music-rescore-plan-{uuid.uuid4().hex[:8]}",
            )
        except SystemExit as exc:
            print(
                f"!! the harness did not plan it: {exc.code}. Nothing was changed.",
                file=out,
            )
            return NOT_APPLIED
        _save_answer(desk, "plan", plan)
        for line in plan_lines(plan):
            print(line, file=out)
        if not _actionable(plan):
            print(
                "Nothing to change: every take in scope is skipped (reasons above). Nothing was spent.",
                file=out,
            )
            return 0
        if not yes:
            cost = plan.get("cost") or {}
            price = "free" if revert else f"costs {_money(cost.get('new_cues_usd'))}"
            print(
                f"Not applied: nothing was changed or spent. Applying {price}; the old episode cuts are kept "
                f"and can be put back with --revert. To apply: "
                f"{_command(desk, episode, revert=revert, rescore_id=rescore_id)}",
                file=out,
            )
            return 0
        body = request_body(
            episode=episode, revert=revert, rescore_id=rescore_id, apply=True
        )
        entry = _pending_entry(desk, body)
        key = f"{paid_key_prefix(_desk, state, run.prefix)}-music-rescore-{entry['id']}-apply-{entry['attempt']}"
        _update_entry(desk, entry["id"], idempotency_key=key)
        try:
            answer = run.post(_route(spine_id), body, idempotency_key=key)
        except SystemExit as exc:
            text = str(exc.code)
            if _refused(text):
                _update_entry(
                    desk, entry["id"], status="refused", last_error=text, sent_at=_now()
                )
            else:
                _update_entry(desk, entry["id"], last_error=text, sent_at=_now())
            print(
                f"!! not applied: {text}. "
                + (
                    "Nothing changed."
                    if _refused(text)
                    else "Running the same command again repeats the same request, never a second re-score."
                ),
                file=out,
            )
            return NOT_APPLIED
        saved = _save_answer(desk, "applied", answer)
        status = str(answer.get("status") or "")
        spent = float((answer.get("cost") or {}).get("spent_usd") or 0)
        _update_entry(
            desk, entry["id"], status=status or "applied", kind=answer.get("kind"),
            rescore_id=answer.get("rescore_id"), job_id=answer.get("job_id"), spent_usd=spent,
            response=str(saved.relative_to(desk)), sent_at=_now(),
        )  # fmt: skip
        if status != "applied":
            print(
                f"Nothing was applied ({status or 'no status'}): {answer.get('message')}",
                file=out,
            )
            for line in plan_lines(answer)[:-1]:
                print(line, file=out)
            return NOT_APPLIED
        print(f"Applied: {answer.get('message') or 'done'}", file=out)
        for line in plan_lines(answer):
            print(line, file=out)
        changed = [
            t
            for t in answer.get("takes") or []
            if isinstance(t, Mapping)
            and t.get("changed")
            and t.get("action") in ("rescore", "revert")
        ]
        if spent > 0:
            from creation.post.finish import book

            first = min(
                (int(t.get("episode_ordinal") or 1) for t in changed),
                default=episode or 1,
            )
            book(desk, episode=first, usd=spent, stream=out, unit="music-rescore")
        if answer.get("rescore_id") and answer.get("kind") == "rescore":
            print(
                f"  To put it back later: fictora-produce rescore-music --desk {desk} "
                + ("--all" if episode is None else f"--episode {episode}")
                + f" --revert --rescore-id {answer['rescore_id']}",
                file=out,
            )
        by_episode: dict[int, list[Mapping[str, Any]]] = {}
        unplaced = 0
        for take in changed:
            if take.get("episode_ordinal") is None or not take.get("video_url"):
                unplaced += 1
                continue
            by_episode.setdefault(int(take["episode_ordinal"]), []).append(take)
        worst = 0
        if unplaced:
            print(
                f"  !! {unplaced} changed take(s) came back without an episode or a take file: "
                "their episode file on this laptop is not rebuilt.",
                file=out,
            )
            worst = REBUILD_NOT_DONE
        if by_episode:
            print(
                "Your episode files on this laptop (rebuilt in episode order):",
                file=out,
            )
        for number in sorted(by_episode):
            rebuilt = rebuild_episode(
                desk, number, by_episode[number], run=run, spine_id=spine_id, out=out
            )
            head = {
                "rebuilt": "rebuilt",
                "nothing_to_rebuild": "nothing to rebuild",
                "not_rebuilt": "!! NOT rebuilt, the old file stays",
            }[rebuilt.state]
            print(f"  ep{number:02d}: {head}", file=out)
            for line in rebuilt.lines:
                print(f"    {line}", file=out)
            if rebuilt.state == "not_rebuilt":
                worst = REBUILD_NOT_DONE
            run_dir = desk / f"ep{number:02d}"
            if (run_dir / "run-notes.md").is_file():
                append_run_note(
                    run_dir,
                    (
                        "Music re-score reverted"
                        if revert
                        else "Music re-scored with today's rules"
                    )
                    + f" by the harness (re-score {answer.get('rescore_id')}, ${spent:.2f}); kit file "
                    + (
                        rebuilt.file.name
                        if rebuilt.file
                        else rebuilt.state.replace("_", " ")
                    ),
                )
        if by_episode:
            print(
                "  Reels and final copies made before keep the old music: make the reel again and approve the "
                "new draft (`fictora-ops approve --gate post --episode N`) when the human says it is final.",
                file=out,
            )
        return worst
    finally:
        run.client.close()


__all__: Sequence[str] = (
    "EpisodeRebuild",
    "NOT_APPLIED",
    "REBUILD_NOT_DONE",
    "RESCORES_FILE",
    "RESCORE_ANSWERS",
    "plan_lines",
    "rebuild_episode",
    "request_body",
    "rescore_entries",
    "run_rescore_music",
)
