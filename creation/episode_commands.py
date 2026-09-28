"""Episode flow commands on top of the phase machine: arc, next episodes, edits, redraws, line checks.

Each command calls the deployed Drama API, saves what it read or made on the
desk with a versioned name, and never approves (the human's yes goes through
``fictora-produce approve``). None of them spends except ``redraw-board``
(one still per board) and an ``edit --select-regen`` cascade.

- ``arc --list`` / ``arc --pick N``: episode 2's series arc (``director/brief``, ``series-arc``).
- ``brief --episode N``: the next-episode directions (for ``author --direction K``).
- ``author --episode N``: write episode N (2 on) with a direction; points the desk at it.
- ``memory --note`` / ``--thread``: standing series notes.
- ``edit``: a beat's shot, a frame's brief, or a line (pin the performed line) —
  ``PATCH`` before the script gate, the cascade after it (paid items off by default).
- ``look`` / ``look-note``: pin the style frame by URL, add or remove look notes.
- ``spine --refresh``: save the story again.
- ``redraw-board``: redraw one board on ``/boards/{set}/regenerate``.
- ``check-lines``: were the approved lines in the take's instructions (take facts, never the prompt)?
- ``film --episode N [--take tK]``: price, then (``--confirm-spend``) film episode N alone or only take K
  of it. Nothing earlier is filmed or booked again. A re-film needs a written cause.

A resumable job (author, redraw) records its ``Idempotency-Key`` in
``production.json`` before the POST and its job id right after, so re-running an
interrupted command picks up the same job and never pays twice.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from creation.harness import stages_gated as stages
from creation.harness.http_util import api_error_text, describe_job_error
from creation.harness.raw_video import wait_for_raw_scene_clips
from creation.harness.session import DramaApiRunSession
from creation.harness.stages_gated import scene_prompt
from creation.harness.visual_first_ep1 import reuse_generation_body
from creation.ops.floor import add_episode, record_estimate, record_spend, record_verdict
from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.ops.state import episode_by_ordinal, load_series
from creation.orchestrate import (
    _estimate_usd,
    _money,
    _open_run,
    board_report,
    collect_takes,
    download_boards,
    envelope_line,
    foreign_warning,
    save_spine_snapshot,
    script_gate_text,
    seed_attempt_for,
    sync_spine_lines,
)
from creation.prices import STILL_USD, lane_label, lane_take_usd, reference_images_ceiling
from creation.production_config import load_production_config
from creation.production_state import ProductionState, load_production, save_production, start_episode
from creation.spine_view import dialogue_line_ids, episode_id_for, episode_summary, frames_by_set, frames_digest

ARC_TITLE_MAX = 80
ARC_LINE_MAX = 400
MAX_LOOK_NOTES = 5
LOOK_NOTE_MAX = 160
PAID_TIER = "media"
"""Cascade items at this ``estimated_tier`` redraw or re-film: provider money."""
MEMORY_FIELDS = {"note": "notes", "thread": "threads"}
MEMORY_KEYS = ("canon", "threads", "knowledge", "notes", "craft", "decisions", "last_image", "on_screen", "through_episode_ordinal")
MEMORY_LIST_MAX = {"canon": 60, "threads": 20, "knowledge": 30, "notes": 20, "craft": 20, "decisions": 20, "on_screen": 8}
JOB_ID_KEYS = ("job_id", "extension_job_id")
"""Most routes answer ``job_id``; ``pilot-episodes/{n}/author`` answers ``extension_job_id``."""
REDRAW_CAUSE_IS_A_LABEL = (
    "The cause is a label for the desk and the run notes; the regenerate route takes no notes, so it does not "
    "change what is drawn. To change the drawing, edit the frames first (edit --frame ...) or add a look note. "
    "A beat edit made after the board was drawn is carried into the redraw by the server (it re-authors the "
    "take's frames first)"
)


class CommandStopped(RuntimeError):
    """A command stopped and says why; the CLI prints it and exits 2."""


# --- Plumbing ------------------------------------------------------------------------------------


def _desk_session(desk: Path) -> tuple[Path, ProductionState, DramaApiRunSession]:
    desk = desk.expanduser().resolve()
    state = load_production(desk)
    if not state.spine_id:
        raise CommandStopped("this desk has no story on the API yet; run `fictora-produce step` (draft) first")
    save_production(desk, state)  # persists the stable idempotency prefix
    return desk, state, _open_run(desk, state)


def _save_desk_json(desk: Path, stem: str, payload: Any) -> Path:
    folder = desk / "api"
    folder.mkdir(parents=True, exist_ok=True)
    path = next_versioned_path(folder, stem, ".json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _note(desk: Path, episode: int, body: str) -> None:
    ep_dir = desk / f"ep{episode:02d}"
    if (ep_dir / "run-notes.md").is_file():
        append_run_note(ep_dir, body)


def admitted_job_id(response: Mapping[str, Any]) -> str:
    """Return the admitted job's id under whichever key the route uses.

    Parameters
    ----------
    response
        The route's JSON answer.

    Returns
    -------
    str
        The id, or empty when the answer carries none.
    """

    return next((str(response[key]) for key in JOB_ID_KEYS if response.get(key)), "")


def run_unit(
    desk: Path,
    run: DramaApiRunSession,
    *,
    unit: str,
    path: str,
    body: dict[str, Any],
    video_route: bool,
    deadline_seconds: float,
) -> dict[str, Any]:
    """POST one resumable job with a recorded key, poll it to terminal, and fail with the server's reason.

    Parameters
    ----------
    desk
        Series desk (``production.json`` records the key and job id).
    run
        Session that owns the spine.
    unit
        Unit name (``author-ep02``, ``boards-ep01-t2-redraw``).
    path
        Route to POST.
    body
        JSON body.
    video_route
        Poll ``/v1/video-generations/{id}`` (images, takes) instead of ``/v1/jobs/{id}`` (writing).
    deadline_seconds
        Poll cap; the job keeps running on the server after it.

    Returns
    -------
    dict[str, Any]
        The completed job.

    Raises
    ------
    CommandStopped
        When the job ends failed or cancelled (the message names the server's code and rule).
    """

    state = load_production(desk)
    pending = state.pending.get(unit)
    if pending is None:
        pending = {"key": f"{state.idempotency_prefix}-{unit}-a{state.attempts.get(unit, 0) + 1}", "job_id": None}
        state.pending[unit] = pending
        save_production(desk, state)
    job_id = pending.get("job_id")
    if job_id:
        print(f"[{unit}] Picking up job {job_id} from the last run (same key, no second charge).", file=sys.stderr)
    else:
        response = run.post(path, body, idempotency_key=str(pending["key"]))
        job_id = admitted_job_id(response)
        if not job_id:
            raise CommandStopped(f"{path} answered without a job id ({', '.join(sorted(response)) or 'empty body'})")
        state = load_production(desk)
        state.pending[unit]["job_id"] = job_id
        save_production(desk, state)
    terminal = run.poll_job(str(job_id), label=unit, video_route=video_route, deadline_seconds=deadline_seconds)
    state = load_production(desk)
    state.pending.pop(unit, None)
    state.attempts[unit] = state.attempts.get(unit, 0) + 1
    save_production(desk, state)
    if terminal.get("status") != "completed":
        raise CommandStopped(f"job {job_id} ({unit}) {describe_job_error(terminal)}")
    return terminal


# --- Arc and briefs ------------------------------------------------------------------------------


def _brief(run: DramaApiRunSession, spine_id: str, version: str, *, episodes: int | None) -> dict[str, Any]:
    body: dict[str, Any] = {"spine_version": version}
    if episodes is not None:
        body["season_target_episode_count"] = episodes
    return run.post(f"/v1/spines/{quote(spine_id, safe='')}/director/brief", body)


def _check_run_length(episodes: int | None) -> None:
    if episodes is not None and not 7 <= episodes <= 240:
        raise CommandStopped("--episodes is the intended run, 7-240 (a soft default; the season can continue past it)")


def run_arc_list(desk: Path, *, episodes: int | None = None, out: Any = None) -> list[dict[str, str]]:
    """Read the episode-2 brief and record its series arcs on the desk. Spends nothing.

    Parameters
    ----------
    desk
        Series desk; episode 1 approved on the server.
    episodes
        Intended run in episodes (7-240); the arcs are sized to it. A soft default, never a stop.
    out
        Text stream.

    Returns
    -------
    list[dict[str, str]]
        ``[{arc_id, title, line}, ...]``.
    """

    out = out or sys.stdout
    _check_run_length(episodes)
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        brief = _brief(run, state.spine_id or "", str(spine["spine_version"]), episodes=episodes)
    finally:
        run.client.close()
    saved = _save_desk_json(desk, "brief-ep02-arcs", brief)
    facts = brief.get("facts") or {}
    arcs = brief.get("arc_options") or []
    if not facts.get("arc_pick") or not arcs:
        raise CommandStopped(
            "the brief offers no series arcs "
            f"(outline_mode={spine.get('outline_mode')!r}, arc_pick={facts.get('arc_pick')!r}). Arcs are offered "
            "only on a story drafted with episode 1 alone, after episode 1 is approved and before an arc is kept. "
            f"Brief saved to {saved}."
        )
    options = [{"arc_id": str(a["arc_id"]), "title": str(a["title"]), "line": str(a["line"])} for a in arcs]
    state = load_production(desk)
    state.arc_options = options
    save_production(desk, state)
    if brief.get("recap"):
        print(f"recap: {brief['recap']}", file=out)
    for number, arc in enumerate(options, start=1):
        print(f"{number}. {arc['title']} — {arc['line']}", file=out)
    print(f"(saved {saved.name}; paste the arcs to the human, then `arc --pick N`)", file=out)
    return options


def run_arc_pick(
    desk: Path,
    *,
    option: int,
    title: str | None = None,
    line: str | None = None,
    episodes: int | None = None,
    out: Any = None,
) -> list[dict[str, Any]]:
    """Keep arc ``option`` on the story (``POST series-arc``), then read episode 2's directions. Spends nothing.

    Parameters
    ----------
    desk
        Series desk with arcs recorded by :func:`run_arc_list`.
    option
        1-based option number.
    title, line
        The human's rewrite (optional).
    episodes
        Intended run kept as the story's season target (soft default).
    out
        Text stream.

    Returns
    -------
    list[dict[str, Any]]
        Episode 2's directions.
    """

    out = out or sys.stdout
    _check_run_length(episodes)
    state = load_production(desk.expanduser().resolve())
    if not state.arc_options:
        raise CommandStopped("no arcs recorded on this desk; run `arc --list` first")
    if not 1 <= option <= len(state.arc_options):
        raise CommandStopped(f"--pick must be 1..{len(state.arc_options)}")
    offered = state.arc_options[option - 1]
    new_title = " ".join((title or "").split()) or offered["title"]
    new_line = " ".join((line or "").split()) or offered["line"]
    if len(new_title) > ARC_TITLE_MAX or len(new_line) > ARC_LINE_MAX:
        raise CommandStopped(f"an arc title is at most {ARC_TITLE_MAX} characters and its line at most {ARC_LINE_MAX}")
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        body: dict[str, Any] = {
            "spine_version": spine["spine_version"],
            "arc": {"arc_id": offered["arc_id"], "title": new_title, "line": new_line},
        }
        if episodes is not None:
            body["season_target_episode_count"] = episodes
        updated = run.post(f"/v1/spines/{quote(state.spine_id or '', safe='')}/series-arc", body)
        state = load_production(desk)
        state.series_arc = {
            **body["arc"],
            "option": option,
            "rewritten": (new_title, new_line) != (offered["title"], offered["line"]),
            "spine_version": str(updated.get("spine_version") or ""),
        }
        save_production(desk, state)
        _save_desk_json(desk, "series-arc", updated)
        brief = _brief(run, state.spine_id or "", str(updated.get("spine_version") or spine["spine_version"]), episodes=None)
    finally:
        run.client.close()
    saved = _save_desk_json(desk, "brief-ep02", brief)
    directions = list(brief.get("directions") or [])
    print(f"kept arc {option}: {new_title} — {new_line}", file=out)
    for number, direction in enumerate(directions, start=1):
        print(f"{number}. {direction.get('title')} — {direction.get('line')}", file=out)
    print(f"(saved {saved.name}; the human picks one: `author --episode 2 --direction N` or --line)", file=out)
    return directions


def run_brief(desk: Path, *, episode: int, episodes: int | None = None, out: Any = None) -> list[dict[str, Any]]:
    """Read the next-episode brief and save it as ``api/brief-epNN-vK.json`` (its directions). Spends nothing.

    Parameters
    ----------
    desk
        Series desk; the previous episode approved on the server.
    episode
        The episode the brief is for (the brief's ``next_episode_ordinal`` should match).
    episodes
        Optional intended run (soft default).
    out
        Text stream.

    Returns
    -------
    list[dict[str, Any]]
        The directions.
    """

    out = out or sys.stdout
    _check_run_length(episodes)
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        brief = _brief(run, state.spine_id or "", str(spine["spine_version"]), episodes=episodes)
    finally:
        run.client.close()
    next_ordinal = brief.get("next_episode_ordinal")
    if isinstance(next_ordinal, int) and next_ordinal != episode:
        print(f"!! the server's next episode is {next_ordinal}, not {episode}", file=out)
    saved = _save_desk_json(desk, f"brief-ep{episode:02d}", brief)
    if brief.get("arc_options") and (brief.get("facts") or {}).get("arc_pick"):
        print("!! this brief is the arc pick: run `arc --list` / `arc --pick N` first", file=out)
    directions = list(brief.get("directions") or [])
    for number, direction in enumerate(directions, start=1):
        print(f"{number}. {direction.get('title')} — {direction.get('line')}", file=out)
    print(f"(saved {saved.name})", file=out)
    return directions


# --- Author ---------------------------------------------------------------------------------------


def author_direction(
    desk: Path, episode: int, *, pick: int | None = None, line: str | None = None, title: str | None = None
) -> dict[str, str] | None:
    """Build the author request's ``direction``: a brief direction, or the human's own words.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode being written.
    pick
        Direction N (1-based) from the newest saved ``api/brief-epNN-vK.json``.
    line
        The human's own direction (sent without a ``direction_id``).
    title
        Optional short name for ``line``.

    Returns
    -------
    dict[str, str] | None
        ``{direction_id, title, line}``, ``{line[, title]}``, or ``None``.
    """

    line = " ".join((line or "").split()) or None
    title = " ".join((title or "").split()) or None
    if pick is not None and line is not None:
        raise CommandStopped("--direction N sends the brief's direction; --line sends the human's own words. Pick one")
    if pick is None:
        if line is None:
            if title is not None:
                raise CommandStopped("--title goes with --line")
            return None
        return {"line": line, **({"title": title} if title else {})}
    if title is not None:
        raise CommandStopped("--title goes with --line")
    briefs = sorted(
        (desk.expanduser().resolve() / "api").glob(f"brief-ep{episode:02d}-v*.json"),
        key=lambda path: int(path.stem.rsplit("-v", 1)[1]) if path.stem.rsplit("-v", 1)[1].isdigit() else 0,
    )
    if not briefs:
        raise CommandStopped(
            f"no saved brief for episode {episode} (api/brief-ep{episode:02d}-vN.json); run `brief --episode {episode}` "
            "or pass the direction in the human's words with --line"
        )
    directions = json.loads(briefs[-1].read_text(encoding="utf-8")).get("directions") or []
    if not 1 <= pick <= len(directions):
        raise CommandStopped(f"--direction must be 1..{len(directions)} (from {briefs[-1].name})")
    chosen = directions[pick - 1]
    return {"direction_id": str(chosen["direction_id"]), "title": str(chosen["title"]), "line": str(chosen["line"])}


def run_author(desk: Path, *, episode: int, direction: Mapping[str, str] | None = None, out: Any = None) -> Path:
    """Write episode N (2 on), save the spine, put its lines on the desk, and point the desk at it. Never approves.

    ``POST /v1/spines/{id}/pilot-episodes/{n}/author {spine_version[, direction]}`` answers
    ``extension_job_id``, polled on ``GET /v1/jobs/{id}``. A deterministic authoring
    failure stops once with its rule (code, message, details). The season target is a
    soft default: writing past it just continues the season.

    Parameters
    ----------
    desk
        Series desk (the previous episode approved on the server).
    episode
        Episode ordinal, 2 or more. A missing desk slot is opened.
    direction
        From :func:`author_direction`.
    out
        Text stream for the script gate.

    Returns
    -------
    Path
        ``api/spine.json``.
    """

    if episode < 2:
        raise CommandStopped("episode 1 is written by the draft (`step`); author writes episode 2 on")
    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    if state.phase == "ready_video":
        raise CommandStopped("a take is in flight on this desk; finish it (`step`) before writing the next episode")
    cfg = load_production_config(desk)
    while len(load_series(desk).episodes) < episode:
        opened = add_episode(desk)
        print(f"[author] Opened desk slot {opened.slug}.", file=sys.stderr)
    try:
        spine = run.spine(state.spine_id or "")
        body: dict[str, Any] = {"spine_version": spine["spine_version"]}
        if direction:
            body["direction"] = dict(direction)
        print(f"[author] Writing episode {episode} on the server (beats, lines, shots). Usually 1-3 minutes.", file=sys.stderr)
        terminal = run_unit(
            desk,
            run,
            unit=f"author-ep{episode:02d}",
            path=f"/v1/spines/{state.spine_id}/pilot-episodes/{episode}/author",
            body=body,
            video_route=False,
            deadline_seconds=cfg.poll_plan_deadline_seconds,
        )
        _save_desk_json(desk, f"author-ep{episode:02d}-terminal", terminal)
        spine = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    episode_id = episode_id_for(spine, episode)
    if not any(isinstance(b, Mapping) and b.get("episode_id") == episode_id for b in spine.get("beats") or []):
        raise CommandStopped(f"the job completed but the spine has no beats for {episode_id}; run `spine --refresh`")
    path = save_spine_snapshot(desk, episode, spine)
    counts = sync_spine_lines(desk, spine, episode=episode)
    state = load_production(desk)
    start_episode(state, episode)
    save_production(desk, state)
    summary = episode_summary(spine, episode)
    print(f"ep{episode:02d} ({episode_id}) {summary.get('title') or ''}".rstrip(), file=out)
    if summary.get("summary"):
        print(f"  {summary['summary']}", file=out)
    print(script_gate_text(desk, spine, episode=episode), file=out)
    steer = f" Direction: {direction.get('line')}" if direction else ""
    _note(desk, episode, f"author: {episode_id} ({summary.get('title', '')}), {sum(counts.values())} lines synced.{steer}")
    print(
        f"[author] Done -> {path}. Next: read the lines and shots above; say yes "
        f"(`fictora-produce approve --gate script`) or edit them.",
        file=sys.stderr,
    )
    return path


# --- Memory ----------------------------------------------------------------------------------------


def memory_body(memory: Mapping[str, Any], *, field: str, text: str) -> tuple[dict[str, Any], bool]:
    """Return the memory to PUT with one line appended to ``field``, and whether it was new.

    Only the write contract's fields are sent back, and the list caps are checked before any call.

    Parameters
    ----------
    memory
        ``memory`` from ``GET /v1/spines/{id}/memory``.
    field
        ``notes`` or ``threads``.
    text
        The line in the creator's words.

    Returns
    -------
    tuple[dict[str, Any], bool]
        The memory body, and ``False`` when the line was already there.
    """

    line = text.strip()
    if not line:
        raise CommandStopped("the memory line is empty")
    kept = {key: copy.deepcopy(value) for key, value in memory.items() if key in MEMORY_KEYS}
    lines = [str(item) for item in kept.get(field) or []]
    added = line not in lines
    if added:
        lines.append(line)
    kept[field] = lines
    for key, cap in MEMORY_LIST_MAX.items():
        if len(kept.get(key) or []) > cap:
            raise CommandStopped(f"the series memory keeps at most {cap} {key}; remove one first")
    return kept, added


def run_memory(desk: Path, *, note: str | None = None, thread: str | None = None, out: Any = None) -> list[str]:
    """Add one standing note or one open thread to the series memory. Spends nothing.

    Notes are standing rules and survive approvals; threads are rebuilt at the
    next approval. To steer one episode, send a direction with ``author``.

    Parameters
    ----------
    desk
        Series desk with a story.
    note, thread
        The line to add; exactly one.
    out
        Text stream.

    Returns
    -------
    list[str]
        The list after the change.
    """

    if (note is None) == (thread is None):
        raise CommandStopped("pass exactly one of --note or --thread")
    flag = "note" if note is not None else "thread"
    field = MEMORY_FIELDS[flag]
    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    try:
        current = run.get(f"/v1/spines/{state.spine_id}/memory")
        stored = current.get("memory")
        body, added = memory_body(stored if isinstance(stored, Mapping) else current, field=field, text=str(note or thread))
        if added:
            spine = run.spine(state.spine_id or "")
            answer = run.put(
                f"/v1/spines/{state.spine_id}/memory", {"spine_version": spine["spine_version"], "memory": body}
            )
            _save_desk_json(desk, "memory", answer)
        else:
            print(f"(that {flag} is already in the series memory; nothing written)", file=out)
    finally:
        run.client.close()
    for number, line in enumerate(body[field], start=1):
        print(f"{field} {number}. {line}", file=out)
    return list(body[field])


# --- Edit -------------------------------------------------------------------------------------------


def parse_assignment(raw: str) -> tuple[str, Any]:
    """Parse one ``--set key=value``; a value starting with ``[`` or ``{``, or ``null``, is JSON.

    Parameters
    ----------
    raw
        ``shot_scale=close up``, ``row_direction.camera_move=dolly_in``, ``story_objects=["a"]``.

    Returns
    -------
    tuple[str, Any]
        Dotted key and value.
    """

    key, sep, value = raw.partition("=")
    if not sep or not key.strip():
        raise CommandStopped(f"--set needs key=value: {raw!r}")
    text = value.strip()
    if text[:1] in {"[", "{"} or text == "null":
        try:
            return key.strip(), json.loads(text)
        except json.JSONDecodeError as exc:
            raise CommandStopped(f"--set {key.strip()}: the value looks like JSON but does not parse ({exc})") from exc
    return key.strip(), value


def _apply(target: dict[str, Any], key: str, value: Any, *, label: str) -> None:
    head, _, rest = key.partition(".")
    if head not in target:
        raise CommandStopped(f"{label} has no field {head!r}; it has: {', '.join(sorted(target))}")
    if rest:
        inner = target[head]
        if not isinstance(inner, dict):
            raise CommandStopped(f"{label}.{head} is not an object, so {key!r} cannot be set")
        _apply(inner, rest, value, label=f"{label}.{head}")
        return
    target[head] = value


def _short(value: Any, size: int = 140) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= size else text[: size - 1] + "…"


def _changes(before: Mapping[str, Any], after: Mapping[str, Any], prefix: str = "") -> list[str]:
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            lines += _changes(old, new, f"{prefix}{key}.")
        elif old != new:
            lines.append(f"  {prefix}{key}: {_short(old)}  ->  {_short(new)}")
    return lines


def _find(spine: Mapping[str, Any], items: Sequence[Any], id_key: str, wanted: str, *, episode: int, kind: str) -> dict[str, Any]:
    episode_id = episode_id_for(spine, episode)
    mine = [item for item in items if isinstance(item, dict) and item.get("episode_id") == episode_id]
    for item in mine:
        if str(item.get(id_key)) == wanted or (wanted.isdigit() and int(item.get("ordinal") or 0) == int(wanted)):
            return item
    ids = ", ".join(f"{item.get(id_key)} ({item.get('ordinal')})" for item in mine) or "none"
    raise CommandStopped(f"no {kind} {wanted!r} on {episode_id}; there are: {ids}")


def _find_line(spine: Mapping[str, Any], line_id: str, *, episode: int) -> dict[str, Any]:
    episode_id = episode_id_for(spine, episode)
    found: list[str] = []
    for beat in spine.get("beats") or []:
        if not isinstance(beat, dict) or beat.get("episode_id") != episode_id:
            continue
        for line in beat.get("dialogue_lines") or []:
            if isinstance(line, dict):
                found.append(str(line.get("line_id")))
                if str(line.get("line_id")) == line_id:
                    return line
    raise CommandStopped(f"no line {line_id!r} on {episode_id}; there are: {', '.join(found) or 'none'}")


def build_patch(
    spine: Mapping[str, Any],
    *,
    episode: int,
    beat: str | None = None,
    frame: str | None = None,
    line_id: str | None = None,
    intent: str | None = None,
    assignments: Sequence[tuple[str, Any]] = (),
    text: str | None = None,
    spoken: str | None = None,
    subtitle: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build the spine patch for one beat, frame or line, merged onto what the spine has now.

    The API takes a beat's whole ``motion_direction`` and a frame's whole
    ``visual_brief``, so an edit is applied to a copy of the current one. A line
    patch sets ``text`` (the English script) and/or pins the performed line
    (``spoken_text``, with an optional ``subtitle_text``) on a Japanese or Korean show.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    beat, frame, line_id
        What to edit (id or ordinal for beats and frames); exactly one.
    intent
        A beat's new ``motion_intent``.
    assignments
        ``--set`` pairs: ``motion_direction`` fields on a beat, ``visual_brief`` fields on a frame.
    text, spoken, subtitle
        A line's English script, pinned performed line, and its subtitle.

    Returns
    -------
    tuple[dict[str, Any], list[str]]
        ``{"beats"|"frames"|"dialogue_lines": [...]}`` and one line per changed field.
    """

    targets = [value for value in (beat, frame, line_id) if value is not None]
    if len(targets) != 1:
        raise CommandStopped("pass exactly one of --beat, --frame or --line-id")
    if line_id is not None:
        if assignments or intent is not None:
            raise CommandStopped("a line takes --text, --spoken and --subtitle, not --set/--intent")
        if subtitle is not None and spoken is None:
            raise CommandStopped("--subtitle describes a pinned line: send it with --spoken")
        if spoken is not None and str(spine.get("spoken_language") or "en-US") == "en-US":
            raise CommandStopped("--spoken pins a performed line on a Japanese or Korean show; this show is English")
        found = _find_line(spine, line_id, episode=episode)
        entry: dict[str, Any] = {"line_id": line_id}
        for key, value in (("text", text), ("spoken_text", spoken), ("subtitle_text", subtitle)):
            if value is not None:
                entry[key] = value
        changed = [
            f"  {key}: {_short(found.get(key))}  ->  {_short(value)}"
            for key, value in entry.items()
            if key != "line_id" and found.get(key) != value
        ]
        if not changed:
            raise CommandStopped(f"nothing to change on {line_id}")
        return {"dialogue_lines": [entry]}, changed
    if text is not None or spoken is not None or subtitle is not None:
        raise CommandStopped("--text/--spoken/--subtitle edit a line: pass --line-id")
    if beat is not None:
        found = _find(spine, spine.get("beats") or [], "beat_id", beat, episode=episode, kind="beat")
        direction = copy.deepcopy(found.get("motion_direction") or {})
        new_intent = intent if intent is not None else str(found.get("motion_intent") or "")
        for key, value in assignments:
            _apply(direction, key, value, label="motion_direction")
        before = {"motion_intent": found.get("motion_intent"), "motion_direction": found.get("motion_direction") or {}}
        after = {"motion_intent": new_intent, "motion_direction": direction}
        changed = _changes(before, after)
        if not changed:
            raise CommandStopped(f"nothing to change on {found.get('beat_id')}")
        return {"beats": [{"beat_id": found["beat_id"], "motion_intent": new_intent, "motion_direction": direction}]}, changed
    if intent is not None:
        raise CommandStopped("--intent is a beat field; a frame is edited with --set on its visual_brief")
    found = _find(spine, spine.get("frames") or [], "frame_id", str(frame), episode=episode, kind="frame")
    brief = copy.deepcopy(found.get("visual_brief") or {})
    for key, value in assignments:
        if isinstance(value, Mapping) and isinstance(brief.get(key), dict):
            brief[key] = {**brief[key], **value}
        else:
            _apply(brief, key, value, label="visual_brief")
    changed = _changes({"visual_brief": found.get("visual_brief") or {}}, {"visual_brief": brief})
    if not changed:
        raise CommandStopped(f"nothing to change on {found.get('frame_id')}")
    return {"frames": [{"frame_id": found["frame_id"], "visual_brief": brief}]}, changed


def run_edit(
    desk: Path,
    *,
    episode: int,
    beat: str | None = None,
    frame: str | None = None,
    line_id: str | None = None,
    intent: str | None = None,
    assignments: Sequence[tuple[str, Any]] = (),
    text: str | None = None,
    spoken: str | None = None,
    subtitle: str | None = None,
    select_regen: bool = False,
    preview_only: bool = False,
    out: Any = None,
) -> Path:
    """Edit one beat, frame or line: ``PATCH`` before the script gate, cascade preview + execute after it.

    After the script is approved the plain patch answers 409 ``cascade_required``;
    the cascade's paid (``estimated_tier: media``) items are left out unless
    ``select_regen``. Redraw a board afterwards with ``redraw-board`` so it is
    downloaded and booked.

    Parameters
    ----------
    desk
        Series desk with a story.
    episode
        Episode ordinal.
    beat, frame, line_id, intent, assignments, text, spoken, subtitle
        As :func:`build_patch`.
    select_regen
        After the script gate: also run the cascade's paid items.
    preview_only
        After the script gate: print the cascade and stop.
    out
        Text stream.

    Returns
    -------
    Path
        The refreshed ``api/spine.json``.
    """

    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        patch, changed = build_patch(
            spine,
            episode=episode,
            beat=beat,
            frame=frame,
            line_id=line_id,
            intent=intent,
            assignments=assignments,
            text=text,
            spoken=spoken,
            subtitle=subtitle,
        )
        what = f"beat {beat}" if beat is not None else (f"frame {frame}" if frame is not None else f"line {line_id}")
        print(f"ep{episode:02d} {what}:", file=out)
        for line in changed:
            print(line, file=out)
        cascade = spine.get("approval_state") == "approved"
        if not cascade and preview_only:
            raise CommandStopped("--preview is for an approved script; before the script gate the edit is a plain patch")
        if not cascade:
            try:
                run.patch(f"/v1/spines/{state.spine_id}", {"spine_version": spine["spine_version"], "patch": patch})
            except SystemExit as exc:
                if "cascade_required" not in str(exc.code):
                    raise CommandStopped(str(exc.code)) from None
                cascade = True
                print("(the server asks for a cascade: the script is approved there)", file=out)
        if cascade:
            _run_cascade(desk, run, spine, patch, episode=episode, select_regen=select_regen, preview_only=preview_only, out=out)
        fresh = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    path = save_spine_snapshot(desk, episode, fresh)
    if line_id is not None and not preview_only and fresh.get("approval_state") != "approved":
        sync_spine_lines(desk, fresh, episode=episode)
    if not preview_only:
        _note(desk, episode, f"edit {what}: " + "; ".join(line.strip() for line in changed))
    return path


def _run_cascade(
    desk: Path,
    run: DramaApiRunSession,
    spine: Mapping[str, Any],
    patch: dict[str, Any],
    *,
    episode: int,
    select_regen: bool,
    preview_only: bool,
    out: Any,
) -> None:
    spine_id = str(spine.get("spine_id") or load_production(desk).spine_id or "")
    edit = {"scope": "section", "target_type": "episode", "target_id": episode_id_for(spine, episode), "patch": patch}
    try:
        preview = run.post(f"/v1/spines/{spine_id}/cascade/preview", {"spine_version": spine["spine_version"], "edit": edit})
    except SystemExit as exc:
        raise CommandStopped(str(exc.code)) from None
    saved = _save_desk_json(desk, "cascade-preview", preview)
    items = [item for item in preview.get("items") or [] if isinstance(item, Mapping)]
    chosen = {
        str(item["item_id"]): (select_regen if item.get("estimated_tier") == PAID_TIER else bool(item.get("selected", True)))
        for item in items
    }
    print(f"cascade {preview.get('proposal_id')} ({len(items)} item(s), saved {saved.name}):", file=out)
    for item in items:
        relation = item.get("relation") or {}
        mark = "run " if chosen[str(item["item_id"])] else "skip"
        paid = " (paid)" if item.get("estimated_tier") == PAID_TIER else ""
        print(
            f"  [{mark}] {item.get('item_id')}  {item.get('recipe_id')}  {relation.get('type')}:{relation.get('id')}  "
            f"tier {item.get('estimated_tier')}{paid}  {_short(item.get('reason') or '')}",
            file=out,
        )
    for warning in preview.get("warnings") or []:
        print(f"  warning: {_short(warning)}", file=out)
    if preview_only:
        print("(preview only: nothing was changed)", file=out)
        return
    state = load_production(desk)
    key = f"{state.idempotency_prefix}-cascade-{str(preview['proposal_id'])[-24:]}"
    try:
        answer = run.post(
            f"/v1/spines/{spine_id}/cascade/execute",
            {
                "proposal_id": preview["proposal_id"],
                "spine_version": spine["spine_version"],
                "items": [{"item_id": item_id, "selected": on} for item_id, on in chosen.items()],
            },
            idempotency_key=key,
        )
    except SystemExit as exc:
        raise CommandStopped(str(exc.code)) from None
    _save_desk_json(desk, "cascade-execute", answer)
    for item in items:
        if chosen[str(item["item_id"])] and item.get("estimated_tier") == PAID_TIER:
            recipe = str(item.get("recipe_id") or "")
            if "still" in recipe or "board" in recipe:
                record_spend(desk, episode=episode, usd=float(STILL_USD))
            else:
                print(f"  !! {item.get('item_id')} ran on the server and is not priced here; book it by hand", file=out)
    for stale in answer.get("stale_storyboard_sets") or []:
        if isinstance(stale, Mapping):
            print(
                f"  board t{stale.get('set_index')} of ep{int(stale.get('episode_ordinal') or episode):02d} no longer "
                f"matches the story: `redraw-board --episode {stale.get('episode_ordinal') or episode} "
                f"--take t{stale.get('set_index')} --cause '...'`",
                file=out,
            )


# --- Look -------------------------------------------------------------------------------------------


def run_look(desk: Path, *, url: str, out: Any = None) -> Path:
    """Pin one style frame (a public https URL, one frame, not a collage) as the story's look. Spends nothing.

    The look pins onto a drafted story, so it comes after the draft and before the plates.

    Parameters
    ----------
    desk
        Series desk with a story.
    url
        Public https URL of the frame.
    out
        Text stream.

    Returns
    -------
    Path
        The refreshed ``api/spine.json``.
    """

    out = out or sys.stdout
    if not url.startswith("https://"):
        raise CommandStopped("--url must be a public https URL of one frame (this kit uploads nothing)")
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        answer = run.post(f"/v1/spines/{state.spine_id}/look-register", {"spine_version": spine["spine_version"], "url": url})
        _save_desk_json(desk, "look-register", answer)
        fresh = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    path = save_spine_snapshot(desk, state.episode_ordinal, fresh)
    print(f"look_register_url: {fresh.get('look_register_url') or url}", file=out)
    return path


def run_look_note(desk: Path, *, add: str | None = None, remove: str | None = None, out: Any = None) -> list[str]:
    """Add or remove one look note (at most five, 160 characters each); the next drawing uses them. Spends nothing.

    Parameters
    ----------
    desk
        Series desk with a story.
    add
        A note in the creator's words.
    remove
        A note id, or its 1-based number.
    out
        Text stream.

    Returns
    -------
    list[str]
        The notes after the change.
    """

    if (add is None) == (remove is None):
        raise CommandStopped("pass exactly one of --add or --remove")
    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        notes = [note for note in spine.get("look_notes") or [] if isinstance(note, Mapping)]
        body = {"spine_version": spine["spine_version"]}
        if add is not None:
            text = add.strip()
            if not text or len(text) > LOOK_NOTE_MAX:
                raise CommandStopped(f"a look note is 1-{LOOK_NOTE_MAX} characters")
            if len(notes) >= MAX_LOOK_NOTES:
                raise CommandStopped(f"the story already has {MAX_LOOK_NOTES} look notes (the most it keeps); remove one")
            run.post(f"/v1/spines/{state.spine_id}/look-notes", {**body, "text": text})
        else:
            wanted = str(remove)
            ids = [str(note.get("note_id")) for note in notes]
            note_id = ids[int(wanted) - 1] if wanted.isdigit() and 1 <= int(wanted) <= len(ids) else wanted
            if note_id not in ids:
                raise CommandStopped(f"no look note {wanted!r}; the notes are: {', '.join(ids) or 'none'}")
            run.delete(f"/v1/spines/{state.spine_id}/look-notes/{note_id}", body)
        fresh = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    save_spine_snapshot(desk, state.episode_ordinal, fresh)
    listed = [str(note.get("text")) for note in fresh.get("look_notes") or [] if isinstance(note, Mapping)]
    for number, note in enumerate(fresh.get("look_notes") or [], start=1):
        print(f"{number}. {note.get('note_id')}  {note.get('text')}", file=out)
    return listed


def run_spine_refresh(desk: Path, *, out: Any = None) -> Path:
    """Save ``GET /v1/spines/{id}`` again and say what moved.

    Parameters
    ----------
    desk
        Series desk with a story.
    out
        Text stream.

    Returns
    -------
    Path
        ``api/spine.json``.
    """

    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    try:
        before_path = desk / "api" / "spine.json"
        before = json.loads(before_path.read_text(encoding="utf-8")).get("spine_version") if before_path.is_file() else None
        spine = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    path = save_spine_snapshot(desk, state.episode_ordinal, spine)
    after = spine.get("spine_version")
    moved = "unchanged" if before == after else f"{before or 'none'} -> {after}"
    print(
        f"spine {spine.get('spine_id')} version {moved}; approval {spine.get('approval_state')}; "
        f"{len(spine.get('episode_summaries') or [])} episode(s); {len(spine.get('look_notes') or [])} look note(s); "
        f"{len(spine.get('media_assets') or [])} media asset(s)",
        file=out,
    )
    return path


# --- Board redraw --------------------------------------------------------------------------------


def run_redraw_board(desk: Path, *, episode: int, take_id: str, cause: str, out: Any = None) -> Path:
    """Redraw one board on ``POST /v1/spines/{id}/episodes/{n}/boards/{set}/regenerate``. Spends one still.

    Warns when the take's frame briefs have not changed since the last drawing
    (a re-roll). The server carries a beat edit into the redraw itself (it
    re-authors the take's frames first). Prints the redrawn board's shot list and
    safe-zone check, and sends the desk back to the board gate.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1``, ``t2`` ...
    cause
        Why it is redrawn: a label on the desk (the route takes no notes).
    out
        Text stream.

    Returns
    -------
    Path
        The new board file.
    """

    out = out or sys.stdout
    if not cause.strip():
        raise CommandStopped("--cause is required: why the board is redrawn (a label for the desk)")
    if not (take_id.startswith("t") and take_id[1:].isdigit()):
        raise CommandStopped("--take is t1, t2 ...")
    set_index = int(take_id[1:])
    desk, state, run = _desk_session(desk)
    cfg = load_production_config(desk)
    slot = episode_by_ordinal(load_series(desk), episode)
    if take_id not in {take.take_id for take in slot.takes}:
        raise CommandStopped(f"{take_id} is not a take on ep{episode:02d}")
    print(f"[board] {REDRAW_CAUSE_IS_A_LABEL}.", file=sys.stderr)
    try:
        spine = run.spine(state.spine_id or "")
        drawn = frames_by_set(spine, episode=episode).get(set_index, [])
        before = state.board_digests.get(f"ep{episode:02d}-{take_id}")
        if before and before == frames_digest(drawn):
            print(
                f"!! {take_id}: the frame briefs have not changed since this board was last drawn, so this redraw draws "
                f"the same direction again (a re-roll, ${float(STILL_USD):.2f}) unless a beat was edited since",
                file=out,
            )
        body = reuse_generation_body(
            prompt=scene_prompt(spine, state.prompt),
            spine=spine,
            preset_id=state.preset_id,
            preset_version=state.preset_version,
            video_lane=state.video_lane,
            cut_tempo=cfg.cut_tempo,
            extra={"episode_count": episode},
        )
        terminal = run_unit(
            desk,
            run,
            unit=f"boards-ep{episode:02d}-{take_id}-redraw",
            path=f"/v1/spines/{state.spine_id}/episodes/{episode}/boards/{set_index}/regenerate",
            body=body,
            video_route=True,
            deadline_seconds=cfg.poll_boards_deadline_seconds,
        )
        _save_desk_json(desk, f"boards-redraw-ep{episode:02d}-{take_id}-terminal", terminal)
        spine = run.spine(state.spine_id or "")
        save_spine_snapshot(desk, episode, spine)
        state = load_production(desk)
        made = download_boards(desk, state, spine, episode=episode, sets=[set_index])
        if not made:
            raise CommandStopped(f"the redraw completed but the spine has no current board for {take_id}")
        report = board_report(desk, run, state, spine, episode=episode, made=made, redraw=True, clip_seconds=cfg.clip_duration_seconds)
    finally:
        run.client.close()
    if state.episode_ordinal == episode and state.phase in {"wait_board", "ready_estimate", "wait_spend"}:
        if state.phase != "wait_board":
            report.append("The board changed after its yes: the desk is back at the board gate (approve it again).")
        state.phase = "wait_board"
    save_production(desk, state)
    for line in report:
        print(line, file=out)
    _note(desk, episode, f"board redraw {take_id}: {made[0][1].name}, ${float(STILL_USD):.2f}. Cause (label only): {cause}")
    return made[0][1]


# --- Line check -------------------------------------------------------------------------------------


def _latest(api_dir: Path, stem: str) -> Path | None:
    found = sorted(
        api_dir.glob(f"{stem}-v*.json"),
        key=lambda path: int(path.stem.rsplit("-v", 1)[1]) if path.stem.rsplit("-v", 1)[1].isdigit() else 0,
    )
    return found[-1] if found else None


def lines_not_asked(
    spine: Mapping[str, Any], facts: Mapping[str, Any], *, episode: int, take_index: int, take_count: int
) -> tuple[list[tuple[int, str]], int]:
    """Return the approved lines a take's instructions did not carry, and how many were approved.

    Parameters
    ----------
    spine
        The desk's spine snapshot.
    facts
        ``take_facts`` fetched with ``spine_id`` (``lines`` lists each approved line id with a count).
    episode, take_index, take_count
        Which take.

    Returns
    -------
    tuple[list[tuple[int, str]], int]
        ``[(line number, approved text), ...]`` for lines with count 0 or absent, and the approved total.

    Raises
    ------
    CommandStopped
        When the facts were read without the spine (no ``lines``).
    """

    listed = facts.get("lines")
    if not isinstance(listed, list):
        raise CommandStopped("these take facts were read without the spine, so they carry no line ids")
    counts = {str(row.get("line_id")): int(row.get("count") or 0) for row in listed if isinstance(row, Mapping)}
    approved = dialogue_line_ids(spine, episode=episode, take_index=take_index, take_count=take_count)
    missing = [(number, text) for number, (line_id, text) in enumerate(approved, start=1) if counts.get(line_id, 0) < 1]
    return missing, len(approved)


def run_check_lines(desk: Path, *, episode: int, take_id: str | None = None, out: Any = None) -> int:
    """Say whether each approved line was in the take's instructions, from the saved take facts.

    Reads ``epNN/api/take-facts-epNN-tK-vM.json`` (newest) and the episode's spine
    snapshot. The compiled prompt is never read, printed or saved.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        One take; default every take on the episode.
    out
        Text stream.

    Returns
    -------
    int
        Lines missing across the checked takes (0 = every approved line was asked for).
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    api_dir = desk / f"ep{episode:02d}" / "api"
    spine_path = api_dir / "spine.json" if (api_dir / "spine.json").is_file() else desk / "api" / "spine.json"
    if not spine_path.is_file():
        raise CommandStopped("no spine snapshot on the desk; run `spine --refresh`")
    spine = json.loads(spine_path.read_text(encoding="utf-8"))
    slot = episode_by_ordinal(load_series(desk), episode)
    take_ids = [take.take_id for take in slot.takes] if take_id is None else [take_id]
    missing_total = 0
    for current in take_ids:
        facts_path = _latest(api_dir, f"take-facts-ep{episode:02d}-{current}")
        if facts_path is None:
            print(f"ep{episode:02d} {current}: no take facts on the desk (film the take first)", file=out)
            continue
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        index = [take.take_id for take in slot.takes].index(current) + 1
        missing, total = lines_not_asked(spine, facts, episode=episode, take_index=index, take_count=len(slot.takes))
        for number, text in missing:
            print(f"ep{episode:02d} {current}: line {number} ('{text}') was not in the take's instructions", file=out)
        print(f"ep{episode:02d} {current}: {total - len(missing)} of {total} approved lines asked ({facts_path.name})", file=out)
        missing_total += len(missing)
    return missing_total


# --- Film one episode, or one take of it ---------------------------------------------------------

#: Words that say "again" without naming what in the direction made the fault.
NOT_A_CAUSE = frozenset({"again", "try again", "retry", "redo", "reroll", "re-roll", "one more", "another one", "new take"})


def _check_cause(cause: str | None, *, refilm: bool, what: str) -> str | None:
    text = " ".join((cause or "").split())
    if not refilm:
        return text or None
    if not text:
        raise CommandStopped(
            f"{what} was filmed already. A second render needs a written cause naming what in the direction produced "
            "the fault (--cause \"...\"). \"Try again\" is not a cause"
        )
    if text.casefold().strip(" .!") in NOT_A_CAUSE or len(text) < 12:
        raise CommandStopped(f"--cause {text!r} does not name what in the direction produced the fault")
    return text


def _film_scope(desk: Path, *, episode: int, take_id: str | None) -> tuple[list[str], int | None, str]:
    slot = episode_by_ordinal(load_series(desk), episode)
    desk_takes = [take.take_id for take in slot.takes]
    if take_id is None:
        return desk_takes, None, f"ep{episode:02d}"
    if not (take_id.startswith("t") and take_id[1:].isdigit()):
        raise CommandStopped("--take is t1, t2 ...")
    if take_id not in desk_takes:
        raise CommandStopped(f"{take_id} is not a take on ep{episode:02d} (it has {', '.join(desk_takes)})")
    return [take_id], int(take_id[1:]), f"ep{episode:02d}-{take_id}"


def run_film(
    desk: Path,
    *,
    episode: int,
    take_id: str | None = None,
    cause: str | None = None,
    confirm_spend: bool = False,
    out: Any = None,
) -> str:
    """Price, then film episode N alone, or only take K of it. Nothing earlier is filmed or booked again.

    Without ``confirm_spend`` it prices exactly what will be filmed
    (``batches/estimate`` for episode N, with ``reroll_take_index`` for one take),
    says it against the envelope, records the number and stops for the human's
    yes. With ``confirm_spend`` (only after that number was shown) it sends
    ``POST /v1/video-generations`` with ``episode_count: N, episode_ordinal: N``
    (plus ``reroll_take_index: K, seed_attempt: previous + 1`` for one take),
    collects the new take(s) raw with their take facts and books them.

    A take (or episode) filmed before needs a written cause, recorded on the
    desk as Change this. An older deploy that cannot film one episode alone is
    refused before anything is sent. An interrupted film picks up its job.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal (episodes 1..N approved on the server).
    take_id
        ``tK`` to film that take alone; default the whole episode.
    cause
        Why it is filmed again (required for a re-film).
    confirm_spend
        The human said yes to the printed number.
    out
        Text stream.

    Returns
    -------
    str
        The printed summary.
    """

    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    if episode != state.episode_ordinal:
        run.client.close()
        run = _open_run(desk, replace(state, episode_ordinal=episode))  # artefacts go to that episode's api/
    try:
        return _run_film(desk, state, run, episode=episode, take_id=take_id, cause=cause, confirm_spend=confirm_spend, out=out)
    finally:
        run.client.close()


def _run_film(
    desk: Path,
    state: ProductionState,
    run: DramaApiRunSession,
    *,
    episode: int,
    take_id: str | None,
    cause: str | None,
    confirm_spend: bool,
    out: Any,
) -> str:
    if episode < 1:
        raise CommandStopped("--episode is 1 or more")
    if state.phase == "ready_video":
        raise CommandStopped("a take job is in flight on this desk; finish it with `step` first")
    cfg = load_production_config(desk)
    take_ids, take_index, key = _film_scope(desk, episode=episode, take_id=take_id)
    slot = episode_by_ordinal(load_series(desk), episode)
    no_yes = [take.take_id for take in slot.takes if take.take_id in take_ids and take.board.status != "approved"]
    if no_yes:
        raise CommandStopped(
            f"ep{episode:02d} {', '.join(no_yes)}: no human yes on the board yet (`approve --gate board`); "
            "a take is filmed only from an approved board"
        )
    filmed_before = any(take.filmed_count for take in slot.takes if take.take_id in take_ids)
    what = f"ep{episode:02d} {take_id}" if take_id else f"episode {episode}"
    reason = _check_cause(cause, refilm=filmed_before, what=what)
    if take_index is None and len(take_ids) > 1 and filmed_before:
        print(f"!! this re-films every take of episode {episode}; to re-film one take pass --take tK", file=out)
    if not confirm_spend:
        return _price_film(desk, state, run, cfg, episode=episode, take_ids=take_ids, take_index=take_index, key=key,
                           what=what, reason=reason, out=out)  # fmt: skip
    priced = load_production(desk).film_estimates.get(key)
    if priced is None:
        raise CommandStopped(
            f"no price shown for {what} yet: run `film --episode {episode}"
            + (f" --take {take_id}" if take_id else "")
            + "` without --confirm-spend, show the human the number, then confirm"
        )
    seed = seed_attempt_for(desk, episode=episode, take_ids=take_ids)
    unit = f"film-{key}" + (f"-s{seed}" if seed else "")
    spine = run.spine(state.spine_id or "")
    body = stages.video_request_body(
        run,
        spine=spine,
        prompt=state.prompt,
        preset_id=state.preset_id,
        preset_version=state.preset_version,
        caption_style=cfg.caption_style,
        api_captions=False,
        video_lane=state.video_lane,
        clip_duration_seconds=cfg.clip_duration_seconds,
        cut_tempo=cfg.cut_tempo,
        episode=episode,
        reroll_take_index=take_index,
        seed_attempt=seed,
    )
    _save_desk_json(desk, f"{unit}-request", body)
    if filmed_before and reason:
        for current in take_ids:
            if any(t.take_id == current and t.filmed_count for t in slot.takes):
                record_verdict(desk, episode=episode, take_id=current, verdict="change", cause=reason)
    fresh = load_production(desk)
    pending = fresh.pending.get(unit)
    if pending is None:
        pending = {"key": f"{fresh.idempotency_prefix}-{unit}-a{fresh.attempts.get(unit, 0) + 1}", "job_id": None}
        fresh.pending[unit] = pending
        save_production(desk, fresh)
    job_id = pending.get("job_id")
    if job_id:
        print(f"[film] Picking up job {job_id} from the last run (same key, no second charge).", file=sys.stderr)
    else:
        job = stages.post_video_generation(run, body, idempotency_key=str(pending["key"]), episode=episode)
        job_id = admitted_job_id(job)
        if not job_id:
            raise CommandStopped(f"/v1/video-generations answered without a job id ({', '.join(sorted(job))})")
        fresh = load_production(desk)
        fresh.pending[unit]["job_id"] = job_id
        save_production(desk, fresh)
        _save_desk_json(desk, f"{unit}-enrol", job)
    print(f"[film] Filming {what} (video job {job_id}). Usually 5-15 minutes.", file=sys.stderr)
    raw = wait_for_raw_scene_clips(
        run, str(job_id), deadline_seconds=cfg.poll_video_deadline_seconds, save_as=f"{unit}-raw-scene-clips.json"
    )
    spine = run.spine(state.spine_id or "")
    save_spine_snapshot(desk, episode, spine)
    fresh = load_production(desk)
    got = collect_takes(desk, run, fresh, raw, episode=episode, clip_seconds=cfg.clip_duration_seconds, spine=spine)
    fresh = load_production(desk)
    fresh.pending.pop(unit, None)
    fresh.attempts[unit] = fresh.attempts.get(unit, 0) + 1
    fresh.film_estimates.pop(key, None)
    if got.first_url is None:
        save_production(desk, fresh)
        raise CommandStopped(f"video job {job_id} completed but no take for {what} came back")
    if fresh.episode_ordinal == episode and take_index is None and fresh.phase in {"ready_estimate", "wait_spend"}:
        fresh.phase = "complete"
    fresh.last_video_job_id = str(job_id)
    save_production(desk, fresh)
    cause_note = f" Cause: {reason}." if reason else ""
    _note(desk, episode, f"film {what}: video job `{job_id}`; " + "; ".join(got.jobs) + f". Booked ${got.booked_usd:.2f}.{cause_note}")
    lines = [f"Filmed {what}: {len(got.jobs)} take(s), ${got.booked_usd:.2f} booked. Video job {job_id}."]
    lines += [f"  {line}" for line in got.jobs]
    lines.append(
        f"Watch the new take in ep{episode:02d}/takes/. After the human says Use it: "
        f"`fictora-produce finish --desk <desk> --episode {episode} --take {take_id or 'tK'}`."
    )
    text = "\n".join(lines) + foreign_warning(got.foreign)
    print(text, file=out)
    return text


def _price_film(
    desk: Path,
    state: ProductionState,
    run: DramaApiRunSession,
    cfg: Any,
    *,
    episode: int,
    take_ids: list[str],
    take_index: int | None,
    key: str,
    what: str,
    reason: str | None,
    out: Any,
) -> str:
    estimate = stages.estimate_batch(run, spine_id=state.spine_id or "", episode=episode, reroll_take_index=take_index)
    _save_desk_json(desk, f"film-{key}-estimate", estimate)
    spine = run.spine(state.spine_id or "")
    cast_count = len([card for card in spine.get("cast") or [] if isinstance(card, dict)])
    per_take = lane_take_usd(
        state.video_lane, cfg.clip_duration_seconds, on=date.today(), reference_images=reference_images_ceiling(cast_count)
    )
    table = round(per_take * len(take_ids), 2) if per_take is not None else cfg.fallback_estimate_usd * len(take_ids)
    cost = estimate.get("cost_estimate") if isinstance(estimate.get("cost_estimate"), dict) else None
    if cost is not None and _money(cost.get("total_usd")) is not None:
        usd = _estimate_usd(estimate, fallback_usd=table)
        source = f"server estimate priced {cost.get('priced_on')} ({cost.get('takes')} take(s))"
    else:
        usd = table
        source = f"price table ({lane_label(state.video_lane)}, {cfg.clip_duration_seconds} s a take)"
        if take_index is not None and "reroll_take_index" in str(estimate.get("detail") or ""):
            source += "; the deployed API cannot price one take yet"
    fresh = load_production(desk)
    fresh.film_estimates[key] = usd
    save_production(desk, fresh)
    if take_index is not None:
        record_estimate(desk, episode=episode, take_id=take_ids[0], usd=usd)
    scope = f"only {take_ids[0]} of episode {episode}" if take_index is not None else f"episode {episode} alone ({len(take_ids)} take(s))"
    earlier = f"; episodes 1-{episode - 1} are not filmed or booked again" if episode > 1 else ""
    others = "; the episode's other takes are kept as filmed" if take_index is not None else ""
    lines = [
        f"Film {scope}: about ${usd:.2f} ({source}){earlier}{others}.",
        envelope_line(desk, episode=episode, next_usd=usd),
    ]
    if reason:
        lines.append(f"Cause: {reason}")
    flag = f" --take {take_ids[0]}" if take_index is not None else ""
    cause_flag = f' --cause "{reason}"' if reason else ""
    lines.append(
        f"Human yes to the number, then `fictora-produce film --desk <desk> --episode {episode}{flag}{cause_flag} --confirm-spend`."
    )
    text = "\n".join(lines)
    print(text, file=out)
    _note(desk, episode, f"film {what} priced ${usd:.2f} ({source}).")
    return text


# --- CLI -------------------------------------------------------------------------------------------

EPISODE_COMMANDS = frozenset(
    {"arc", "brief", "author", "memory", "edit", "look", "look-note", "spine", "redraw-board", "check-lines", "film"}
)


def add_episode_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the episode flow commands on ``fictora-produce``.

    Parameters
    ----------
    sub
        Argparse subparser set.
    """

    arc = sub.add_parser("arc", help="Episode 2's series arc: --list the three, then --pick N. Spends nothing.")
    arc.add_argument("--desk", type=Path, required=True)
    mode = arc.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="Read the brief and print its arcs.")
    mode.add_argument("--pick", type=int, metavar="N", help="Keep arc N, then print episode 2's directions.")
    arc.add_argument("--title", default=None, help="With --pick: the human's rewrite of the title (<=80).")
    arc.add_argument("--line", default=None, help="With --pick: the human's rewrite of the line (<=400).")
    arc.add_argument("--episodes", type=int, default=None, help="Intended run, 7-240 (soft default; can continue).")

    brief = sub.add_parser("brief", help="Read the next-episode brief (directions) for episode N. Spends nothing.")
    brief.add_argument("--desk", type=Path, required=True)
    brief.add_argument("--episode", type=int, required=True)
    brief.add_argument("--episodes", type=int, default=None, help="Intended run, 7-240 (soft default).")

    author = sub.add_parser(
        "author",
        help="Write episode N (2 on) on the API, sync its lines, point the desk at it. Never approves.",
    )
    author.add_argument("--desk", type=Path, required=True)
    author.add_argument("--episode", type=int, required=True, help="Episode ordinal, 2 or more.")
    author.add_argument("--direction", type=int, default=None, metavar="N", help="Direction N from the newest saved brief.")
    author.add_argument("--line", default=None, help="The human's own direction for this episode.")
    author.add_argument("--title", default=None, help="With --line: a short name for it.")

    memory = sub.add_parser("memory", help="Add one standing note or open thread to the series memory.")
    memory.add_argument("--desk", type=Path, required=True)
    which = memory.add_mutually_exclusive_group(required=True)
    which.add_argument("--note", default=None)
    which.add_argument("--thread", default=None)

    edit = sub.add_parser(
        "edit",
        help=(
            "Edit a beat's shot, a frame's visual_brief, or a line (pin the performed JA/KO line). PATCH before "
            "the script gate; after it, the cascade with paid items off unless --select-regen."
        ),
    )
    edit.add_argument("--desk", type=Path, required=True)
    edit.add_argument("--episode", type=int, required=True)
    target = edit.add_mutually_exclusive_group(required=True)
    target.add_argument("--beat", default=None, help="Beat id or ordinal.")
    target.add_argument("--frame", default=None, help="Frame id or ordinal.")
    target.add_argument("--line-id", default=None, help="Dialogue line id (line_ep01_01).")
    edit.add_argument("--intent", default=None, help="A beat's new motion_intent.")
    edit.add_argument("--set", dest="assignments", action="append", default=[], metavar="KEY=VALUE",
                      help="motion_direction field (beat) or visual_brief field (frame); dotted keys; JSON values.")
    edit.add_argument("--text", default=None, help="Line: the English script text.")
    edit.add_argument("--spoken", default=None, help="Line: pin the exact performed line (JA/KO shows).")
    edit.add_argument("--subtitle", default=None, help="Line: the subtitle for the pinned line (with --spoken).")
    edit.add_argument("--select-regen", action="store_true", help="After the gate: also run paid regeneration items.")
    edit.add_argument("--preview", action="store_true", help="After the gate: print the cascade and stop.")

    look = sub.add_parser("look", help="Pin the story's look: one style frame by public https URL. Spends nothing.")
    look.add_argument("--desk", type=Path, required=True)
    look.add_argument("--url", required=True)

    note = sub.add_parser("look-note", help="Add or remove a look note (max 5). Spends nothing.")
    note.add_argument("--desk", type=Path, required=True)
    change = note.add_mutually_exclusive_group(required=True)
    change.add_argument("--add", default=None)
    change.add_argument("--remove", default=None, metavar="ID|N")

    spine = sub.add_parser("spine", help="Save the story from the server again (--refresh).")
    spine.add_argument("--desk", type=Path, required=True)
    spine.add_argument("--refresh", action="store_true", required=True)

    redraw = sub.add_parser("redraw-board", help="Redraw one board (regenerate route); prints its shot list. $0.30.")
    redraw.add_argument("--desk", type=Path, required=True)
    redraw.add_argument("--episode", type=int, required=True)
    redraw.add_argument("--take", required=True, help="t1, t2 ...")
    redraw.add_argument("--cause", required=True, help="Why: a LABEL for the desk; the server takes no redraw notes.")

    film = sub.add_parser(
        "film",
        help=(
            "Film episode N alone, or only take K of it (nothing earlier is filmed or booked). Prices first; "
            "films with --confirm-spend. A re-film needs --cause."
        ),
    )
    film.add_argument("--desk", type=Path, required=True)
    film.add_argument("--episode", type=int, required=True)
    film.add_argument("--take", default=None, help="tK: film only this take of the episode.")
    film.add_argument("--cause", default=None, help="Required to film again: what in the direction produced the fault.")
    film.add_argument("--confirm-spend", action="store_true", help="The human said yes to the printed number.")

    check = sub.add_parser("check-lines", help="Were the approved lines in the take's instructions? (take facts)")
    check.add_argument("--desk", type=Path, required=True)
    check.add_argument("--episode", type=int, required=True)
    check.add_argument("--take", default=None)


def dispatch_episode(args: argparse.Namespace) -> int:
    """Run one episode flow command.

    Parameters
    ----------
    args
        Parsed command.

    Returns
    -------
    int
        ``0`` done, ``2`` stopped (the reason is printed), ``5`` lines missing (``check-lines``).
    """

    try:
        if args.command == "arc":
            if args.list:
                run_arc_list(args.desk, episodes=args.episodes)
            else:
                run_arc_pick(args.desk, option=args.pick, title=args.title, line=args.line, episodes=args.episodes)
            return 0
        if args.command == "brief":
            run_brief(args.desk, episode=args.episode, episodes=args.episodes)
            return 0
        if args.command == "author":
            direction = author_direction(args.desk, args.episode, pick=args.direction, line=args.line, title=args.title)
            run_author(args.desk, episode=args.episode, direction=direction)
            return 0
        if args.command == "memory":
            run_memory(args.desk, note=args.note, thread=args.thread)
            return 0
        if args.command == "edit":
            run_edit(
                args.desk,
                episode=args.episode,
                beat=args.beat,
                frame=args.frame,
                line_id=args.line_id,
                intent=args.intent,
                assignments=[parse_assignment(raw) for raw in args.assignments],
                text=args.text,
                spoken=args.spoken,
                subtitle=args.subtitle,
                select_regen=args.select_regen,
                preview_only=args.preview,
            )
            return 0
        if args.command == "look":
            run_look(args.desk, url=args.url)
            return 0
        if args.command == "look-note":
            run_look_note(args.desk, add=args.add, remove=args.remove)
            return 0
        if args.command == "spine":
            run_spine_refresh(args.desk)
            return 0
        if args.command == "redraw-board":
            run_redraw_board(args.desk, episode=args.episode, take_id=args.take, cause=args.cause)
            return 0
        if args.command == "film":
            run_film(args.desk, episode=args.episode, take_id=args.take, cause=args.cause, confirm_spend=args.confirm_spend)
            return 0
        if args.command == "check-lines":
            return 5 if run_check_lines(args.desk, episode=args.episode, take_id=args.take) else 0
    except CommandStopped as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        print(f"Stopped: {api_error_text(exc.code) if not isinstance(exc.code, str) else exc.code}", file=sys.stderr)
        return 2
    raise ValueError(f"unknown episode command {args.command}")


__all__ = [
    "EPISODE_COMMANDS",
    "CommandStopped",
    "add_episode_parsers",
    "admitted_job_id",
    "author_direction",
    "build_patch",
    "dispatch_episode",
    "lines_not_asked",
    "memory_body",
    "parse_assignment",
    "run_arc_list",
    "run_arc_pick",
    "run_author",
    "run_brief",
    "run_check_lines",
    "run_edit",
    "run_film",
    "run_look",
    "run_look_note",
    "run_memory",
    "run_redraw_board",
    "run_spine_refresh",
    "run_unit",
]
