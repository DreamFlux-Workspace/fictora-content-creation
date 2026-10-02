"""Episode flow commands on top of the phase machine: arc, next episodes, edits, redraws, line checks.

Each command calls the deployed Drama API, saves what it read or made on the
desk with a versioned name, and never approves (the human's yes goes through
``fictora-produce approve``). None of them spends except ``redraw-board``
(one still per board), ``redraw-plate`` (one still), ``look-frame`` (one still) and an
``edit --select-regen`` cascade.

- ``arc --list`` / ``arc --pick N``: episode 2's series arc (``director/brief``, ``series-arc``).
- ``brief --episode N``: the next-episode directions (for ``author --direction K``).
- ``author --episode N``: write episode N (2 on) with a direction; points the desk at it.
- ``memory --note`` / ``--thread``: standing series notes.
- ``edit``: a beat's shot, shot plan or expression (``--expression KIND|none``), a frame's brief, or a line
  (pin the performed line) — ``PATCH`` before the script gate, the cascade after it (paid items off by default).
- ``expressions [--episode N]``: the expressions the deploy offers (``GET /v1/capabilities``) and what each beat asks for.
- ``line``: change one line's words, performed line, speaker or seen/heard, add a line to a beat, remove one,
  or add a voice that is only heard with its line, on the server and the desk in one step, and say what that
  does to the script approval (``line`` with no change lists the lines).
- ``look-frame``: draw our own style frame on the server from a written description (one still).
- ``look`` / ``look-note``: pin the style frame by URL, add or remove look notes.
- ``sound-note``: add a sound to one take, or drop / level one on every take; ``--remove``; list.
- ``take-facts --refresh``: read a filmed take's facts again (new sound notes, planned impacts), versioned.
- ``spine --refresh``: save the story again.
- ``redraw-board``: redraw one board on ``/boards/{set}/regenerate``; ``--note "what's wrong"`` first turns the note into
  shot edits through the director (the app's path) and prints them per row; stops unpaid when nothing it is drawn
  from changed (``--same-shots`` / ``--reroll`` to re-roll).
- ``plates``: retired (was ``plates --cast NAME --cause``); prints a pointer to ``redraw-plate --note`` and sends nothing.
- ``redraw-plate --cast X --note "..."``: note one character, then redraw only their plate (one still).
- ``check-lines``: were the approved lines in the take's instructions, which shot and board row each fell in,
  and is its on-screen speaker in that row (take facts and board frames, never the prompt)?
- ``film --episode N [--take tK]``: price, then (``--confirm-spend``) film episode N alone or only take K
  of it. Nothing earlier is filmed or booked again. A re-film needs a written cause.

A resumable job (author, redraw) records its ``Idempotency-Key`` in
``production.json`` before the POST and its job id right after, so re-running an
interrupted command picks up the same job and never pays twice.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shlex
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

import httpx

from creation import inner_voice
from creation.captions import is_english
from creation.authoring_warnings import (
    authoring_warnings,
    for_episode,
    introduced,
    say_warnings,
)
from creation import orchestrate as _orchestrate
from creation.narrator_cast import (
    NarratorAnswer,
    NarratorQuestionOpen,
    add_narrator_answer_args,
    interactive_ask,
    load_answers,
    named_like_narrator,
    save_answer,
    settle_narrators,
    stop_message,
)
from creation.narrator_cast import question as narrator_question
from creation.cli_text import TextArgError, text_or_file
from creation.new_cast import new_cast_notice, newcomers
from creation.desk_media_urls import drawn_cast_rows
from creation.post.desk import name_matches
from creation.harness import stages_gated as stages
from creation.harness.http_util import api_error_text, describe_job_error
from creation.harness.minor_scene_refusal import (
    MINOR_IN_INTIMATE_SCENE,
    minor_scene_fix,
)
from creation.harness.raw_video import (
    STEP_RAW_CLIPS,
    VideoJobFailed,
    raw_clips_name,
    wait_for_raw_scene_clips,
)
from creation.harness.session import DramaApiRunSession
from creation.harness.stages_gated import scene_prompt
from creation.harness.visual_first_ep1 import reuse_generation_body
from creation.stylised_only import photoreal_look_warning
from creation.look_gate import (
    look_approved,
    look_frame_url,
    look_gate_refusal,
    newest_look_frame,
    pinned_look_url,
    record_look_frame_url,
    unapproved_look_frame,
)
from creation.ops.floor import (
    add_episode,
    approve_series_gate,
    record_estimate,
    record_spend,
    record_verdict,
)
from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.ops.state import GateRecord, episode_by_ordinal, load_series, save_series
from creation.patch_refusal import (
    FRAME_CAST_FIXES,
    INVALID_PATCH,
    LINE_DELIVERIES,
    STRICT_FIELDS,
    delivery_refusal,
    delivery_values,
    explain_invalid_patch,
    refusal_code,
    server_named_rules,
)
from creation.post.take_facts import (
    cast_names_from,
    save_take_facts,
    shot_people_lines,
    sfx_plan_changes,
    sfx_plan_lines,
    stale_facts_reason,
    take_number,
)
from creation.orchestrate import (
    _open_run,
    board_report,
    collect_takes,
    download_boards,
    envelope_line,
    foreign_warning,
    unplaced_warning,
    price_estimate,
    save_spine_snapshot,
    script_gate_text,
    seed_attempt_for,
    sync_spine_lines,
)
from creation.prices import STILL_USD, server_lane
from creation.production_config import load_production_config
from creation.production_state import (
    ProductionState,
    load_production,
    save_production,
    start_episode,
)
from creation.board_note import (
    BOARD_NOTE_MAX,
    CAST_NOTE_MAX,
    DIRECTOR_STAGE,
    check_note_length,
    beat_change_lines,
    director_message,
    note_refused_by_server,
    outcome_lines,
    redraw_note_outcome,
    regenerate_note_support,
    row_change_lines,
    take_patch,
)
from creation.board_note import take_beats as board_take_beats
from creation.expression import (
    CAPABILITIES_PATH,
    OLDER_SERVER,
    ExpressionError,
    beat_expression,
    expression_options,
    resolve_expression,
    server_takes_expression,
    vocabulary_lines,
)
from creation.stranded_voice import (
    STRAND_FLAG,
    explain_film_refusal,
    strand_override_note,
    strand_refusal,
    stranded_preflight,
    voices_left_without_lines,
)
from creation.harness_rules import (
    early_extra_shots_stop,
    film_stop_message,
    hook_mouth_edit_stop,
    inner_voice_wording_stop,
    on_screen_speaker_stop,
)
from creation.shot_plan import (
    OLDER_SERVER_HINT,
    ShotPlanError,
    plan_from_json,
    plan_from_shots,
    plan_lines,
    same_plan,
)
from creation.spine_view import (
    BOARD_INPUT_NAMES,
    beats_by_take,
    board_inputs,
    dialogue_line_ids,
    episode_id_for,
    episode_summary,
    frame_cast,
    frames_by_set,
    named_cast_stop,
    heard_line_ids,
    shot_rows,
)

ARC_TITLE_MAX = 80
ARC_LINE_MAX = 400
MAX_LOOK_NOTES = 5
LOOK_NOTE_MAX = 160
PAID_TIER = "media"
LOOK_FRAME_SIZES = ("1088x1936", "1936x1088", "1024x1024")
LOOK_FRAME_DEFAULT_SIZE = "1088x1936"
LOOK_FRAME_MAX_CHARS = 4000
OLD_SERVER_LOOK_FRAME = (
    "this Drama API has no look-frame route yet (404 on POST /v1/spines/{id}/look-frame): it is an older deploy. "
    "Nothing was drawn or booked. Tell engineering the server needs the look-frame route; never draw it with "
    "your own provider key. Meanwhile `approve --gate look --url` still pins a frame engineering hands you"
)
LOOK_FRAME_HINTS = {
    "look_frame_text_only": "a look frame is drawn from words only: take the link out of the description",
    "look_frame_description_empty": "write the look down in the file first",
    "operator_look_frame_unavailable": "the Drama API has no image provider configured; tell engineering",
    "operator_audio_unavailable": "the Drama API has no media storage configured; tell engineering",
    "operator_audio_in_progress": "the same frame is still being drawn: run the same command again in a minute (never pays twice)",
    "operator_audio_failed": "the draw failed on the server: run the same command again (it retries; up to three attempts)",
    "budget_cap_exceeded": "the budget cap is reached; tell engineering",
    "rate_limited": "wait a minute and run the same command again",
}
"""Cascade items at this ``estimated_tier`` redraw or re-film: provider money."""
MEMORY_FIELDS = {"note": "notes", "thread": "threads"}
MEMORY_KEYS = (
    "canon",
    "threads",
    "knowledge",
    "notes",
    "craft",
    "decisions",
    "last_image",
    "on_screen",
    "through_episode_ordinal",
)
MEMORY_LIST_MAX = {
    "canon": 60,
    "threads": 20,
    "knowledge": 30,
    "notes": 20,
    "craft": 20,
    "decisions": 20,
    "on_screen": 8,
}
#: Where a job-admitting route puts the job id (``author``: ``extension_job_id``; ``rewrite``: ``rewrite_job_id``).
JOB_ID_KEYS = ("job_id", "extension_job_id", "rewrite_job_id")
PLATE_DEADLINE_SECONDS = 3600.0
"""Poll cap on one plate redraw (the cast enrol's cap)."""
"""Most routes answer ``job_id``; ``pilot-episodes/{n}/author`` answers ``extension_job_id``."""
REDRAW_CAUSE_IS_A_LABEL = (
    "The cause is a label for the desk and the run notes; it does not change what is drawn. To change the "
    'drawing, say what is wrong with --note "..." (turned into shot edits through the director before the '
    "redraw), or edit the frames first (edit --frame ...), the beat (edit --beat N --shot ...) or add a look "
    "note; a redraw with none of these changed stops unless --same-shots. A beat edit made after the board was "
    "drawn is carried into the redraw by the server (it re-authors the take's frames first)"
)
NOTE_CHANGED_NOTHING = (
    'the note did not change any shot of {take}: the director answered "{reply}" and edited none of the take\'s '
    "beats. Nothing was drawn or paid. Say it as what the camera should see (size, framing, what is in or out of "
    "the frame), or edit the frame directly (`edit --episode {episode} --frame N --set FIELD=VALUE`)."
)

NEW_VOICE_HINT = (
    "To add someone who is only heard (an intercom, a caller), add their line with a new voice: "
    '`line --add --beat N --text "..." --new-voice NAME --role "..." --voice-description "..."`'
)


class CommandStopped(RuntimeError):
    """A command stopped and says why; the CLI prints it and exits 2."""


class EditRefused(CommandStopped):
    """An ``edit`` / ``line`` change the server did not take, after its changes were printed.

    Parameters
    ----------
    message
        The refusal, as the CLI prints it after ``Stopped:``.
    items
        The changes that were printed (:func:`change_items`), each named again as refused.
    """

    def __init__(self, message: str, *, items: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.items = list(items)


#: Commands that print a change before sending it, so they end on a verdict line (L-20261001-25).
EDIT_VERDICT_COMMANDS = frozenset({"edit", "line", "cast", "language"})


def change_items(changed: Sequence[str]) -> list[str]:
    """Name each change an edit printed (``intent``, ``speaker``, ``+ beat 2 (b2)  Hana`` ...).

    Only the top-level change rows count: a shot plan's ``was`` / ``now`` rows
    and a ``!!`` warning are part of the row above them, not changes of their own.

    Parameters
    ----------
    changed
        The change rows :func:`build_patch` / :func:`build_line_add_remove_patch` printed.

    Returns
    -------
    list[str]
        One short name per change.
    """

    items: list[str] = []
    for row in changed:
        if not row.startswith("  ") or row.startswith("   ") or row.startswith("  !!"):
            continue
        text = row.strip()
        items.append(_short(text.split(":", 1)[0].strip() if ":" in text else text, 60))
    return items


def refusal_reason(message: str) -> str:
    """The first line of a refusal, short enough for the verdict line."""

    first = next((ln.strip() for ln in str(message).splitlines() if ln.strip()), "")
    return _short(first or "the command stopped", 200)


def edit_verdict(
    items: Sequence[str],
    *,
    refused: str | None = None,
    not_kept: Sequence[str] = (),
    preview: bool = False,
) -> list[str]:
    """The lines an edit ends on, so a change that did not land is never missed (L-20261001-25).

    The change rows are printed before anything is sent and a refusal used to
    come last, easy to read past. Now the very last line says it: ``Applied``
    or ``Refused: <reason>``; with more than one change each is named first.

    Parameters
    ----------
    items
        The changes (:func:`change_items`).
    refused
        Why the server (or the kit, before sending) refused; ``None`` when it took the edit.
    not_kept
        Changes the server answered for but did not hold (a field an older deploy drops).
    preview
        ``--preview``: the cascade was shown and nothing was sent.

    Returns
    -------
    list[str]
        Per-change rows when there are several, then the final verdict line.
    """

    many = len(items) > 1
    if preview:
        rows = [f"  Not sent: {item}" for item in items] if many else []
        return rows + ["Not applied: --preview showed the cascade; nothing was sent"]
    if refused is not None:
        rows = [f"  Refused: {item}" for item in items] if many else []
        tally = f" ({len(items)} changes, none made)" if many else ""
        return rows + [f"Refused: {refusal_reason(refused)}{tally}"]
    dropped = [item for item in items if item in set(not_kept)] or list(not_kept)
    if dropped:
        rows = (
            [
                f"  {'Refused' if item in dropped else 'Applied'}: {item}"
                for item in items
            ]
            if many
            else []
        )
        return rows + [
            f"Refused: the server answered but did not keep {', '.join(dropped)} "
            f"(likely an older deploy); {len(items) - len(dropped)} of {len(items)} applied"
        ]
    rows = [f"  Applied: {item}" for item in items] if many else []
    return rows + [f"Applied: all {len(items)} changes" if many else "Applied"]


# --- Plumbing ------------------------------------------------------------------------------------


def _desk_session(desk: Path) -> tuple[Path, ProductionState, DramaApiRunSession]:
    desk = desk.expanduser().resolve()
    state = load_production(desk)
    if not state.spine_id:
        raise CommandStopped(
            "this desk has no story on the API yet; run `fictora-produce step` (draft) first"
        )
    save_production(desk, state)  # persists the stable idempotency prefix
    return desk, state, _open_run(desk, state)


def _save_desk_json(desk: Path, stem: str, payload: Any) -> Path:
    folder = desk / "api"
    folder.mkdir(parents=True, exist_ok=True)
    path = next_versioned_path(folder, stem, ".json")
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def _hold_for_look(desk: Path, command: str) -> None:
    """Refuse a paid drawing, before anything is sent, while a drawn look frame awaits its yes (as ``step`` does)."""

    refusal = look_gate_refusal(desk.expanduser().resolve(), command=command)
    if refusal:
        raise RuntimeError(refusal)


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
        pending = {
            "key": f"{state.idempotency_prefix}-{unit}-a{state.attempts.get(unit, 0) + 1}",
            "job_id": None,
        }
        state.pending[unit] = pending
        save_production(desk, state)
    job_id = pending.get("job_id")
    if job_id:
        print(
            f"[{unit}] Picking up job {job_id} from the last run (same key, no second charge).",
            file=sys.stderr,
        )
    else:
        response = run.post(path, body, idempotency_key=str(pending["key"]))
        job_id = admitted_job_id(response)
        if not job_id:
            raise CommandStopped(
                f"{path} answered without a job id ({', '.join(sorted(response)) or 'empty body'})"
            )
        state = load_production(desk)
        state.pending[unit]["job_id"] = job_id
        save_production(desk, state)
    terminal = run.poll_job(
        str(job_id),
        label=unit,
        video_route=video_route,
        deadline_seconds=deadline_seconds,
    )
    state = load_production(desk)
    state.pending.pop(unit, None)
    state.attempts[unit] = state.attempts.get(unit, 0) + 1
    save_production(desk, state)
    if terminal.get("status") != "completed":
        raise CommandStopped(f"job {job_id} ({unit}) {describe_job_error(terminal)}")
    return terminal


# --- Arc and briefs ------------------------------------------------------------------------------


def _brief(
    run: DramaApiRunSession, spine_id: str, version: str, *, episodes: int | None
) -> dict[str, Any]:
    body: dict[str, Any] = {"spine_version": version}
    if episodes is not None:
        body["season_target_episode_count"] = episodes
    return run.post(f"/v1/spines/{quote(spine_id, safe='')}/director/brief", body)


def _check_run_length(episodes: int | None) -> None:
    if episodes is not None and not 7 <= episodes <= 240:
        raise CommandStopped(
            "--episodes is the intended run, 7-240 (a soft default; the season can continue past it)"
        )


def run_arc_list(
    desk: Path, *, episodes: int | None = None, out: Any = None
) -> list[dict[str, str]]:
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
        brief = _brief(
            run, state.spine_id or "", str(spine["spine_version"]), episodes=episodes
        )
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
    options = [
        {"arc_id": str(a["arc_id"]), "title": str(a["title"]), "line": str(a["line"])}
        for a in arcs
    ]
    state = load_production(desk)
    state.arc_options = options
    save_production(desk, state)
    if brief.get("recap"):
        print(f"recap: {brief['recap']}", file=out)
    for number, arc in enumerate(options, start=1):
        print(f"{number}. {arc['title']} — {arc['line']}", file=out)
    print(
        f"(saved {saved.name}; paste the arcs to the human, then `arc --pick N`)",
        file=out,
    )
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
        raise CommandStopped(
            f"an arc title is at most {ARC_TITLE_MAX} characters and its line at most {ARC_LINE_MAX}"
        )
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        body: dict[str, Any] = {
            "spine_version": spine["spine_version"],
            "arc": {"arc_id": offered["arc_id"], "title": new_title, "line": new_line},
        }
        if episodes is not None:
            body["season_target_episode_count"] = episodes
        updated = run.post(
            f"/v1/spines/{quote(state.spine_id or '', safe='')}/series-arc", body
        )
        state = load_production(desk)
        state.series_arc = {
            **body["arc"],
            "option": option,
            "rewritten": (new_title, new_line) != (offered["title"], offered["line"]),
            "spine_version": str(updated.get("spine_version") or ""),
        }
        save_production(desk, state)
        _save_desk_json(desk, "series-arc", updated)
        brief = _brief(
            run,
            state.spine_id or "",
            str(updated.get("spine_version") or spine["spine_version"]),
            episodes=None,
        )
    finally:
        run.client.close()
    saved = _save_desk_json(desk, "brief-ep02", brief)
    directions = list(brief.get("directions") or [])
    print(f"kept arc {option}: {new_title} — {new_line}", file=out)
    for number, direction in enumerate(directions, start=1):
        print(f"{number}. {direction.get('title')} — {direction.get('line')}", file=out)
    print(
        f"(saved {saved.name}; the human picks one: `author --episode 2 --direction N` or --line)",
        file=out,
    )
    return directions


def run_brief(
    desk: Path, *, episode: int, episodes: int | None = None, out: Any = None
) -> list[dict[str, Any]]:
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
        brief = _brief(
            run, state.spine_id or "", str(spine["spine_version"]), episodes=episodes
        )
    finally:
        run.client.close()
    next_ordinal = brief.get("next_episode_ordinal")
    if isinstance(next_ordinal, int) and next_ordinal != episode:
        print(
            f"!! the server's next episode is {next_ordinal}, not {episode}", file=out
        )
    saved = _save_desk_json(desk, f"brief-ep{episode:02d}", brief)
    if brief.get("arc_options") and (brief.get("facts") or {}).get("arc_pick"):
        print(
            "!! this brief is the arc pick: run `arc --list` / `arc --pick N` first",
            file=out,
        )
    directions = list(brief.get("directions") or [])
    for number, direction in enumerate(directions, start=1):
        print(f"{number}. {direction.get('title')} — {direction.get('line')}", file=out)
    print(f"(saved {saved.name})", file=out)
    return directions


# --- Author ---------------------------------------------------------------------------------------


def author_direction(
    desk: Path,
    episode: int,
    *,
    pick: int | None = None,
    line: str | None = None,
    title: str | None = None,
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
        raise CommandStopped(
            "--direction N sends the brief's direction; --line sends the human's own words. Pick one"
        )
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
        key=lambda path: (
            int(path.stem.rsplit("-v", 1)[1])
            if path.stem.rsplit("-v", 1)[1].isdigit()
            else 0
        ),
    )
    if not briefs:
        raise CommandStopped(
            f"no saved brief for episode {episode} (api/brief-ep{episode:02d}-vN.json); run `brief --episode {episode}` "
            "or pass the direction in the human's words with --line"
        )
    directions = (
        json.loads(briefs[-1].read_text(encoding="utf-8")).get("directions") or []
    )
    if not 1 <= pick <= len(directions):
        raise CommandStopped(
            f"--direction must be 1..{len(directions)} (from {briefs[-1].name})"
        )
    chosen = directions[pick - 1]
    return {
        "direction_id": str(chosen["direction_id"]),
        "title": str(chosen["title"]),
        "line": str(chosen["line"]),
    }


#: ``author --line`` limit when the deploy's ``/openapi.json`` does not say (``DramaPilotEpisodeDirection.line``).
DIRECTION_LINE_LIMIT = 400
#: The OpenAPI schema (suffix) of the author request's ``direction``.
DIRECTION_SCHEMA = "DramaPilotEpisodeDirection"


def direction_line_limit(openapi: Any) -> int | None:
    """The longest ``direction.line`` the deploy takes, as its ``/openapi.json`` says.

    Parameters
    ----------
    openapi
        ``/openapi.json`` as read from the deploy (anything else reads as unknown).

    Returns
    -------
    int | None
        ``maxLength`` of ``DramaPilotEpisodeDirection.line``, or ``None`` when the schema does not say.
    """

    if not isinstance(openapi, Mapping):
        return None
    schemas = (openapi.get("components") or {}).get("schemas") or {}
    for name, schema in schemas.items() if isinstance(schemas, Mapping) else ():
        if not str(name).endswith(DIRECTION_SCHEMA) or not isinstance(schema, Mapping):
            continue
        line = (schema.get("properties") or {}).get("line")
        limit = line.get("maxLength") if isinstance(line, Mapping) else None
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            return limit
    return None


def check_direction_length(
    run: DramaApiRunSession, line: str, *, out: Any, label: str = "author"
) -> int:
    """Count an ``author --line`` direction and stop before sending one the server would refuse (HTTP 422).

    A line within :data:`DIRECTION_LINE_LIMIT` is sent without a schema read. A
    longer one is checked against the deploy's own limit
    (``/openapi.json``, :func:`direction_line_limit`), so a deploy that raised
    it takes the longer line; when the schema cannot be read the default holds.

    Parameters
    ----------
    run
        Session (reads ``/openapi.json`` only for a long line).
    line
        The direction, whitespace already collapsed (the server collapses it too).
    out
        Where the count is said when the line is long but allowed.
    label
        The command saying it (``author``, ``rewrite``).

    Returns
    -------
    int
        The limit the line was checked against.

    Raises
    ------
    CommandStopped
        The line is longer than the limit; nothing was sent.
    """

    count = len(line)
    if count <= DIRECTION_LINE_LIMIT:
        return DIRECTION_LINE_LIMIT
    status, doc = run.get_optional("/openapi.json")
    read = direction_line_limit(doc) if 200 <= status < 300 else None
    limit = read or DIRECTION_LINE_LIMIT
    source = (
        "this deploy's /openapi.json"
        if read
        else f"the known limit; /openapi.json did not say (HTTP {status})"
    )
    if count > limit:
        raise CommandStopped(
            f"--line is {count} characters; the server takes at most {limit} ({source}). Nothing was sent. "
            f"Cut it by {count - limit} characters: keep where the episode goes and what turns, and leave "
            f"staging to the script edit after `{label}`."
        )
    print(
        f"[{label}] --line is {count} characters (this deploy takes up to {limit}).",
        file=out,
    )
    return limit


def run_author(
    desk: Path,
    *,
    episode: int,
    direction: Mapping[str, str] | None = None,
    narrator_heard_only: Sequence[str] = (),
    narrator_on_screen: Sequence[str] = (),
    ask: Callable[[str], str] | None = None,
    out: Any = None,
) -> Path:
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
    narrator_heard_only, narrator_on_screen, ask
        Answers to "heard only, never seen?" for a character named like a
        narrator the episode brings in (:mod:`creation.narrator_cast`). With
        no answer and nobody to ask, the episode is kept and the command stops
        after it, naming both flags; nothing is drawn until it is answered.
    out
        Text stream for the script gate.

    Returns
    -------
    Path
        ``api/spine.json``.

    Raises
    ------
    NarratorQuestionOpen
        A narrator-named character has no answer and ``ask`` is ``None``.
    """

    if episode < 2:
        raise CommandStopped(
            "episode 1 is written by the draft (`step`); author writes episode 2 on"
        )
    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    if state.phase == "ready_video":
        raise CommandStopped(
            "a take is in flight on this desk; finish it (`step`) before writing the next episode"
        )
    if direction and direction.get("line"):
        try:
            check_direction_length(run, str(direction["line"]), out=sys.stderr)
        except CommandStopped:
            run.client.close()
            raise
    cfg = load_production_config(desk)
    while len(load_series(desk).episodes) < episode:
        opened = add_episode(desk)
        print(f"[author] Opened desk slot {opened.slug}.", file=sys.stderr)
    try:
        spine = run.spine(state.spine_id or "")
        before = spine
        body: dict[str, Any] = {"spine_version": spine["spine_version"]}
        if direction:
            body["direction"] = dict(direction)
        print(
            f"[author] Writing episode {episode} on the server (beats, lines, shots). Usually 1-3 minutes.",
            file=sys.stderr,
        )
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
    return _show_authored(
        desk,
        state.spine_id or "",
        before=before,
        spine=spine,
        terminal=terminal,
        episode=episode,
        steer=f" Direction: {direction.get('line')}" if direction else "",
        label="author",
        narrator_heard_only=narrator_heard_only,
        narrator_on_screen=narrator_on_screen,
        ask=ask,
        out=out,
    )


def _show_authored(
    desk: Path,
    spine_id: str,
    *,
    before: Mapping[str, Any],
    spine: Mapping[str, Any],
    terminal: Mapping[str, Any],
    episode: int,
    steer: str,
    label: str,
    narrator_heard_only: Sequence[str],
    narrator_on_screen: Sequence[str],
    ask: Callable[[str], str] | None,
    out: Any,
) -> Path:
    """Save a freshly written episode on the desk and print it for the script gate (``author``, ``rewrite``).

    Saves the spine snapshot, syncs the episode's lines, points the desk at the
    episode (script gate), prints its title, summary, shots and lines, the
    newcomers and the authoring nudges, notes it in ``run-notes.md``, and asks
    the narrator question for a narrator-named newcomer.

    Returns
    -------
    Path
        ``api/spine.json``.
    """

    episode_id = episode_id_for(spine, episode)
    if not any(
        isinstance(b, Mapping) and b.get("episode_id") == episode_id
        for b in spine.get("beats") or []
    ):
        raise CommandStopped(
            f"the job completed but the spine has no beats for {episode_id}; run `spine --refresh`"
        )
    path = save_spine_snapshot(desk, episode, spine)
    counts = sync_spine_lines(desk, spine, episode=episode)
    state = load_production(desk)
    start_episode(state, episode)
    save_production(desk, state)
    summary = episode_summary(spine, episode)
    print(
        f"ep{episode:02d} ({episode_id}) {summary.get('title') or ''}".rstrip(),
        file=out,
    )
    if summary.get("summary"):
        print(f"  {summary['summary']}", file=out)
    print(script_gate_text(desk, spine, episode=episode), file=out)
    arrived = new_cast_notice(newcomers(before, spine))
    for line in arrived:
        print(line, file=out)
    # Nudges only (fictora-drama #538): the job's own list, else the spine's for this episode.
    notes = say_warnings(
        for_episode(
            authoring_warnings(terminal) or authoring_warnings(spine), episode_id, spine
        ),
        spine=spine,
        out=out,
    )
    if arrived:
        steer += " " + " ".join(arrived)
    if notes:
        steer += "\n" + "\n".join(notes)
    _note(
        desk,
        episode,
        f"{label}: {episode_id} ({summary.get('title', '')}), {sum(counts.values())} lines synced.{steer}",
    )
    print(
        f"[{label}] Done -> {path}. Next: read the lines and shots above; say yes "
        f"(`fictora-produce approve --gate script`) or edit them.",
        file=sys.stderr,
    )
    # A narrator-named character this episode brought in: heard only or drawn,
    # as the operator says (founder decision 5), asked before the script gate.
    _, _, run = _desk_session(desk)
    try:
        settle_narrators(
            desk, run, run.spine(spine_id), heard_only=narrator_heard_only,
            on_screen=narrator_on_screen, ask=ask, rerun=f"fictora-produce step --desk {desk}", out=sys.stderr,
        )  # fmt: skip
    finally:
        run.client.close()
    return path


def _episode_notes(spine: Mapping[str, Any], episode: int) -> list[Mapping[str, Any]]:
    """The creator steers on episode ``episode`` (``episode_summaries[].creator_notes``), oldest first."""

    notes = episode_summary(spine, episode).get("creator_notes") or []
    return [note for note in notes if isinstance(note, Mapping)]


def rewrite_note_text(line: str | None, title: str | None = None) -> str:
    """The episode steer ``rewrite`` sends: the human's words, after their short name when given.

    Parameters
    ----------
    line
        The human's direction for the rewrite.
    title
        Optional short name for it.

    Returns
    -------
    str
        One line, whitespace collapsed (the server folds line breaks too).

    Raises
    ------
    CommandStopped
        The line is empty.
    """

    line = " ".join((line or "").split())
    title = " ".join((title or "").split())
    if not line:
        raise CommandStopped(
            "--line is empty: say what the rewrite should change, in the human's words"
        )
    return f"{title}: {line}" if title else line


def run_rewrite(
    desk: Path,
    *,
    episode: int,
    line: str,
    title: str | None = None,
    narrator_heard_only: Sequence[str] = (),
    narrator_on_screen: Sequence[str] = (),
    ask: Callable[[str], str] | None = None,
    out: Any = None,
) -> Path:
    """Re-write a drafted, not yet approved episode N (2 on) from the human's direction. Never approves; spends nothing.

    ``author`` refuses an episode that is already drafted (``409
    prior_episode_not_approved`` / ``invalid_extension_ordinal``). This adds the
    direction as an episode steer (``POST /v1/spines/{id}/episodes/{episode_id}/notes
    {spine_version, text}``), first removing the steer the last ``rewrite`` of this
    episode added (``DELETE …/notes/{note_id}``; the human's other steers stay), then
    ``POST /v1/spines/{id}/pilot-episodes/{n}/rewrite {spine_version}``, which answers
    ``rewrite_job_id``, polled on ``GET /v1/jobs/{id}`` like ``author``. The desk is
    then pointed at the episode and its script printed exactly as ``author`` does.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal, 2 or more, drafted and not approved on the server.
    line
        The human's direction (counted against ``author --line``'s limit before sending).
    title
        Optional short name for ``line``.
    narrator_heard_only, narrator_on_screen, ask
        As :func:`run_author`.
    out
        Text stream for the script gate.

    Returns
    -------
    Path
        ``api/spine.json``.
    """

    if episode < 2:
        raise CommandStopped(
            "episode 1 is written by the draft (`step`), not re-written: change its lines with `edit` / `line` "
            "before the script yes. `rewrite` re-writes a drafted episode 2 on"
        )
    text = rewrite_note_text(line, title)
    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    try:
        if state.phase == "ready_video":
            raise CommandStopped(
                "a take is in flight on this desk; finish it (`step`) before re-writing an episode"
            )
        check_direction_length(run, text, out=sys.stderr, label="rewrite")
        cfg = load_production_config(desk)
        spine_id = state.spine_id or ""
        spine = run.spine(spine_id)
        before = spine
        summary = episode_summary(spine, episode)
        authoring_state = summary.get("authoring_state")
        if authoring_state == "approved":
            raise CommandStopped(
                f"episode {episode}'s script is approved; `rewrite` only replaces a draft. Change it with "
                f"`edit --episode {episode}` / `line` (the cascade after the script gate). Nothing was sent."
            )
        if not summary or authoring_state != "drafted":
            raise CommandStopped(
                f"episode {episode} is not written yet ({authoring_state or 'not on the story'}); write it with "
                f"`author --episode {episode}`. Nothing was sent."
            )
        episode_id = episode_id_for(spine, episode)
        unit = f"rewrite-ep{episode:02d}"
        notes_path = f"/v1/spines/{spine_id}/episodes/{episode_id}/notes"
        recorded = state.rewrite_notes.get(episode_id) or {}
        if unit in state.pending:
            # The last run's steer is already on the server and pinned in its job: resume, add nothing.
            if recorded.get("text") and recorded.get("text") != text:
                print(
                    f"[rewrite] The last rewrite of episode {episode} is still running with its own direction "
                    f"({recorded['text']!r}); picking it up. Run `rewrite` again after it for this --line.",
                    file=sys.stderr,
                )
        else:
            listed = {
                str(note.get("note_id")) for note in _episode_notes(spine, episode)
            }
            if recorded.get("note_id") and recorded["note_id"] in listed:
                spine = run.delete(
                    f"{notes_path}/{quote(recorded['note_id'], safe='')}",
                    {"spine_version": spine["spine_version"]},
                )
                print(
                    f"[rewrite] Removed the last rewrite's direction: {recorded.get('text', '')}",
                    file=sys.stderr,
                )
            known = {
                str(note.get("note_id")) for note in _episode_notes(spine, episode)
            }
            spine = run.post(
                notes_path, {"spine_version": spine["spine_version"], "text": text}
            )
            added = [
                note
                for note in _episode_notes(spine, episode)
                if str(note.get("note_id")) not in known
            ]
            if not added:
                raise CommandStopped(
                    f"{notes_path} answered without the new note; run `spine --refresh` and check episode "
                    f"{episode}'s notes before rewriting"
                )
            state = load_production(desk)
            state.rewrite_notes[episode_id] = {
                "note_id": str(added[-1]["note_id"]),
                "text": text,
            }
            save_production(desk, state)
        while len(load_series(desk).episodes) < episode:
            opened = add_episode(desk)
            print(f"[rewrite] Opened desk slot {opened.slug}.", file=sys.stderr)
        print(
            f"[rewrite] Re-writing episode {episode} on the server from its notes (beats, lines, shots). "
            "Usually 1-3 minutes. Free: a draft.",
            file=sys.stderr,
        )
        terminal = run_unit(
            desk,
            run,
            unit=unit,
            path=f"/v1/spines/{spine_id}/pilot-episodes/{episode}/rewrite",
            body={"spine_version": spine["spine_version"]},
            video_route=False,
            deadline_seconds=cfg.poll_plan_deadline_seconds,
        )
        _save_desk_json(desk, f"{unit}-terminal", terminal)
        spine = run.spine(spine_id)
    finally:
        run.client.close()
    return _show_authored(
        desk,
        spine_id,
        before=before,
        spine=spine,
        terminal=terminal,
        episode=episode,
        steer=f" Rewrite direction: {text}",
        label="rewrite",
        narrator_heard_only=narrator_heard_only,
        narrator_on_screen=narrator_on_screen,
        ask=ask,
        out=out,
    )


# --- Memory ----------------------------------------------------------------------------------------


def memory_body(
    memory: Mapping[str, Any], *, field: str, text: str
) -> tuple[dict[str, Any], bool]:
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
    kept = {
        key: copy.deepcopy(value) for key, value in memory.items() if key in MEMORY_KEYS
    }
    lines = [str(item) for item in kept.get(field) or []]
    added = line not in lines
    if added:
        lines.append(line)
    kept[field] = lines
    for key, cap in MEMORY_LIST_MAX.items():
        if len(kept.get(key) or []) > cap:
            raise CommandStopped(
                f"the series memory keeps at most {cap} {key}; remove one first"
            )
    return kept, added


def run_memory(
    desk: Path, *, note: str | None = None, thread: str | None = None, out: Any = None
) -> list[str]:
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
        body, added = memory_body(
            stored if isinstance(stored, Mapping) else current,
            field=field,
            text=str(note or thread),
        )
        if added:
            spine = run.spine(state.spine_id or "")
            answer = run.put(
                f"/v1/spines/{state.spine_id}/memory",
                {"spine_version": spine["spine_version"], "memory": body},
            )
            _save_desk_json(desk, "memory", answer)
        else:
            print(
                f"(that {flag} is already in the series memory; nothing written)",
                file=out,
            )
    finally:
        run.client.close()
    for number, line in enumerate(body[field], start=1):
        print(f"{field} {number}. {line}", file=out)
    return list(body[field])


# --- Edit -------------------------------------------------------------------------------------------


#: ``--set`` values read as JSON: a quoted string, a list, an object, ``null``, ``true`` or ``false``.
_JSON_VALUE_START = ('"', "[", "{")
_JSON_LITERALS = {"null", "true", "false"}


def parse_assignment(raw: str) -> tuple[str, Any]:
    """Parse one ``--set key=value``: a JSON value when the value is one, else the raw text.

    A value that starts with ``"``, ``[`` or ``{``, or is ``null``, ``true`` or
    ``false``, is read as JSON, so ``shot_scale="extreme close-up"`` stores
    ``extreme close-up`` (not the quotes). Anything else is kept as typed
    (``shot_scale=close up``); numbers stay text, as every brief field is text.

    Parameters
    ----------
    raw
        ``shot_scale=close up``, ``shot_scale="extreme close-up"``,
        ``row_direction.camera_move=dolly_in``, ``story_objects=["a"]``,
        ``subject_blocking.0.pose=arms crossed``.

    Returns
    -------
    tuple[str, Any]
        Dotted key and value.

    Raises
    ------
    CommandStopped
        No ``=``, an empty key, or a value that looks like JSON but does not parse.
    """

    key, sep, value = raw.partition("=")
    if not sep or not key.strip():
        raise CommandStopped(f"--set needs key=value: {raw!r}")
    text = value.strip()
    if text[:1] in _JSON_VALUE_START or text in _JSON_LITERALS:
        try:
            return key.strip(), json.loads(text)
        except json.JSONDecodeError as exc:
            raise CommandStopped(
                f"--set {key.strip()}: the value looks like JSON but does not parse ({exc}); "
                "quote a JSON string with double quotes, or pass the text without quotes"
            ) from exc
    return key.strip(), value


#: visual_brief fields the server omits from a saved frame while they are empty
#: (server #583: ``story_signs``); ``edit --frame --set`` may still set them.
OPTIONAL_VISUAL_BRIEF_FIELDS = frozenset({"story_signs"})


def _apply(target: Any, key: str, value: Any, *, label: str) -> None:
    """Set a dotted ``key`` inside ``target``; a numeric part indexes a list (``subject_blocking.0.pose``)."""

    head, _, rest = key.partition(".")
    if isinstance(target, list):
        if not head.isdigit():
            raise CommandStopped(
                f"{label} is a list, so {head!r} must be a number (0 to {len(target) - 1})"
            )
        index = int(head)
        if index >= len(target):
            raise CommandStopped(
                f"{label} has {len(target)} item(s); {head} is past the end (0 to {len(target) - 1})"
            )
        where = f"{label}.{index}"
        if rest:
            _apply(target[index], rest, value, label=where)
        else:
            target[index] = value
        return
    if not isinstance(target, dict):
        raise CommandStopped(
            f"{label} is not an object or a list, so {key!r} cannot be set"
        )
    if head not in target:
        if (
            not rest
            and label.endswith("visual_brief")
            and head in OPTIONAL_VISUAL_BRIEF_FIELDS
        ):
            # Optional fields the server leaves out of a saved frame when empty.
            target[head] = value
            return
        raise CommandStopped(
            f"{label} has no field {head!r}; it has: {', '.join(sorted(target))}"
        )
    if rest:
        inner = target[head]
        if not isinstance(inner, (dict, list)):
            raise CommandStopped(
                f"{label}.{head} is not an object or a list, so {key!r} cannot be set"
            )
        _apply(inner, rest, value, label=f"{label}.{head}")
        return
    target[head] = value


def _short(value: Any, size: int = 140) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= size else text[: size - 1] + "…"


def _changes(
    before: Mapping[str, Any], after: Mapping[str, Any], prefix: str = ""
) -> list[str]:
    lines: list[str] = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            lines += _changes(old, new, f"{prefix}{key}.")
        elif (
            isinstance(old, list)
            and isinstance(new, list)
            and len(old) == len(new)
            and old != new
            and all(isinstance(item, Mapping) for item in [*old, *new])
        ):
            # One field changed inside a list of objects (subject_blocking.0.pose): name that field.
            for index, (was, now) in enumerate(zip(old, new)):
                lines += _changes(was, now, f"{prefix}{key}.{index}.")
        elif old != new:
            lines.append(f"  {prefix}{key}: {_short(old)}  ->  {_short(new)}")
    return lines


def _shot_plan_change(
    beat: Mapping[str, Any], plan: list[dict[str, str]] | None
) -> list[str]:
    """Printable lines for a beat's plan going from what it has to ``plan`` (``None`` = cleared); empty if equal."""

    before = beat.get("shot_plan") or None
    if before == plan:
        return []
    old = plan_lines(before, indent="    was ") or [
        "    was: no plan (the frames author chooses the shots)"
    ]
    new = plan_lines(plan, indent="    now ") or [
        "    now: no plan (the frames author chooses the shots)"
    ]
    return ["  shot_plan:", *old, *new]


def _expression_change(beat: Mapping[str, Any], kind: str | None) -> list[str]:
    """Printable line for a beat's expression going from what it asks for to ``kind`` (``None`` = cleared)."""

    before = beat_expression(beat)
    if before == kind:
        return []
    chosen = "none (the frames author chooses)"
    return [f"  expression: {before or chosen}  ->  {kind or chosen}"]


def _find(
    spine: Mapping[str, Any],
    items: Sequence[Any],
    id_key: str,
    wanted: str,
    *,
    episode: int,
    kind: str,
) -> dict[str, Any]:
    episode_id = episode_id_for(spine, episode)
    mine = [
        item
        for item in items
        if isinstance(item, dict) and item.get("episode_id") == episode_id
    ]
    for item in mine:
        if str(item.get(id_key)) == wanted or (
            wanted.isdigit() and int(item.get("ordinal") or 0) == int(wanted)
        ):
            return item
    ids = (
        ", ".join(f"{item.get(id_key)} ({item.get('ordinal')})" for item in mine)
        or "none"
    )
    raise CommandStopped(f"no {kind} {wanted!r} on {episode_id}; there are: {ids}")


def _find_line(
    spine: Mapping[str, Any], line_id: str, *, episode: int
) -> dict[str, Any]:
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
    raise CommandStopped(
        f"no line {line_id!r} on {episode_id}; there are: {', '.join(found) or 'none'}"
    )


def episode_lines(
    spine: Mapping[str, Any], *, episode: int
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """An episode's ``(beat, line)`` pairs in script order (beats by ordinal).

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[tuple[dict[str, Any], dict[str, Any]]]
        Each line with the beat that holds it; line N of the episode is item N-1.
    """

    episode_id = episode_id_for(spine, episode)
    beats = [
        b
        for b in spine.get("beats") or []
        if isinstance(b, dict) and b.get("episode_id") == episode_id
    ]
    beats.sort(key=lambda beat: int(beat.get("ordinal") or 0))
    return [
        (beat, line)
        for beat in beats
        for line in beat.get("dialogue_lines") or []
        if isinstance(line, dict)
    ]


def _cast_names(spine: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(card.get("cast_id")): str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    }


def line_listing(spine: Mapping[str, Any], *, episode: int) -> list[str]:
    """Number an episode's lines for ``line --line N``: number, id, beat, speaker, text.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[str]
        One printable row per line.
    """

    names = _cast_names(spine)
    heard_ids = heard_line_ids(spine)
    rows = []
    for number, (beat, line) in enumerate(
        episode_lines(spine, episode=episode), start=1
    ):
        who = names.get(str(line.get("cast_id")), str(line.get("cast_id") or "?"))
        heard = " (off-screen)" if str(line.get("line_id")) in heard_ids else ""
        spoken = (
            f"  performed: {line['spoken_text']}" if line.get("spoken_text") else ""
        )
        rows.append(
            f"  {number}. {line.get('line_id')}  beat {beat.get('ordinal')}  {who}{heard}: "
            f"{line.get('text') or ''}{spoken}"
        )
    return rows


def resolve_line_id(spine: Mapping[str, Any], ref: str, *, episode: int) -> str:
    """Turn ``--line`` (a line id, or its number in the episode) into the line id.

    Parameters
    ----------
    spine
        Spine JSON.
    ref
        ``line_episode_01_02`` or ``2``.
    episode
        Episode ordinal.

    Returns
    -------
    str
        The line id.
    """

    pairs = episode_lines(spine, episode=episode)
    for _, line in pairs:
        if str(line.get("line_id")) == ref:
            return ref
    if ref.isdigit() and 1 <= int(ref) <= len(pairs):
        return str(pairs[int(ref) - 1][1]["line_id"])
    listing = "\n".join(line_listing(spine, episode=episode)) or "  (none)"
    raise CommandStopped(
        f"no line {ref!r} in episode {episode}; its lines are:\n{listing}"
    )


def resolve_speaker(spine: Mapping[str, Any], who: str) -> str:
    """Turn ``--speaker`` (a cast name or id) into a cast id already on the story.

    Parameters
    ----------
    spine
        Spine JSON.
    who
        ``D-9341``, ``d-9341`` or ``cast_d-9341``.

    Returns
    -------
    str
        The cast id.
    """

    names = _cast_names(spine)
    for cast_id, name in names.items():
        if name_matches(cast_id, name, who.strip()):
            return cast_id
    cast = ", ".join(f"{name} ({cast_id})" for cast_id, name in names.items()) or "none"
    raise CommandStopped(
        f"no cast member {who!r} on this story; the cast is: {cast}. {NEW_VOICE_HINT}"
    )


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
    speaker: str | None = None,
    off_screen: bool | None = None,
    shot_plan: list[dict[str, str]] | None = None,
    clear_shot_plan: bool = False,
    set_reaction_kind: bool = False,
    reaction_kind: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build the spine patch for one beat, frame or line, merged onto what the spine has now.

    The API takes a beat's whole ``motion_direction`` and a frame's whole
    ``visual_brief``, so an edit is applied to a copy of the current one. A line
    patch sets ``text`` (the English script), pins the performed line
    (``spoken_text``, with an optional ``subtitle_text``) on a Japanese or Korean
    show, gives the line to another cast member already on the story
    (``cast_id``) and/or marks the speaker heard, not seen (``off_screen``).

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
    speaker
        A line's new speaker: a cast name or id already on the story.
    off_screen
        A line's speaker heard, not seen (``True``) or back on screen (``False``).
    shot_plan
        A beat's new ``shot_plan`` (checked by :mod:`creation.shot_plan`); it replaces the whole plan.
    clear_shot_plan
        Send ``shot_plan: null``: the beat's plan is removed and the frames author chooses its shots again.
    set_reaction_kind, reaction_kind
        Send the beat's ``reaction_kind`` (its expression, already checked against the deploy's library by
        :func:`creation.expression.resolve_expression`); ``None`` clears it.

    Returns
    -------
    tuple[dict[str, Any], list[str]]
        ``{"beats"|"frames"|"dialogue_lines": [...]}`` and one line per changed field. A beat edit that only
        changes the plan and/or the expression sends ``{beat_id, shot_plan?, reaction_kind?}`` and nothing else.
    """

    targets = [value for value in (beat, frame, line_id) if value is not None]
    if len(targets) != 1:
        raise CommandStopped("pass exactly one of --beat, --frame or --line-id")
    if set_reaction_kind and beat is None:
        raise CommandStopped("--expression belongs to a beat: pass --beat N")
    if line_id is not None:
        if assignments or intent is not None:
            raise CommandStopped(
                "a line takes --text, --spoken, --subtitle, --speaker and --off-screen, not --set/--intent"
            )
        if subtitle is not None and spoken is None:
            raise CommandStopped(
                "--subtitle describes a pinned line: send it with --spoken"
            )
        if (
            spoken is not None
            and str(spine.get("spoken_language") or "en-US") == "en-US"
        ):
            raise CommandStopped(
                "--spoken pins a performed line on a Japanese or Korean show; this show is English"
            )
        found = _find_line(spine, line_id, episode=episode)
        entry: dict[str, Any] = {"line_id": line_id}
        for key, value in (
            ("text", text),
            ("spoken_text", spoken),
            ("subtitle_text", subtitle),
        ):
            if value is not None:
                entry[key] = value
        if speaker is not None:
            entry["cast_id"] = resolve_speaker(spine, speaker)
        if off_screen is not None:
            entry["off_screen"] = off_screen
        names = _cast_names(spine)
        current = {**found, "off_screen": found.get("off_screen") is True}

        def shown(key: str, value: Any) -> str:
            return _short(names.get(str(value), value) if key == "cast_id" else value)

        changed = [
            f"  {'speaker' if key == 'cast_id' else key}: {shown(key, current.get(key))}  ->  {shown(key, value)}"
            for key, value in entry.items()
            if key != "line_id" and current.get(key) != value
        ]
        if not changed:
            raise CommandStopped(f"nothing to change on {line_id}")
        if speaker is not None or off_screen is not None:
            _refuse_speaker_after_line_edit(
                spine, episode=episode, line_id=line_id, entry=entry
            )
        return {"dialogue_lines": [entry]}, changed
    if any(
        value is not None for value in (text, spoken, subtitle, speaker, off_screen)
    ):
        raise CommandStopped(
            "--text/--spoken/--subtitle/--speaker/--off-screen edit a line: pass --line-id"
        )
    planning = shot_plan is not None or clear_shot_plan
    if shot_plan is not None and beat is not None and len(shot_plan) > 1:
        found_for_plan = _find(
            spine,
            spine.get("beats") or [],
            "beat_id",
            beat,
            episode=episode,
            kind="beat",
        )
        extra = early_extra_shots_stop(
            spine, episode=episode, beat=found_for_plan, plan=shot_plan
        )
        if extra:
            raise CommandStopped(extra + " Nothing was sent.")
    if planning and beat is None:
        raise CommandStopped(
            "--shot-plan/--shot/--clear-shot-plan belong to a beat: pass --beat N"
        )
    if shot_plan is not None and clear_shot_plan:
        raise CommandStopped("pass a new plan or --clear-shot-plan, not both")
    if beat is not None:
        found = _find(
            spine,
            spine.get("beats") or [],
            "beat_id",
            beat,
            episode=episode,
            kind="beat",
        )
        plan_changed = (
            _shot_plan_change(found, None if clear_shot_plan else shot_plan)
            if planning
            else []
        )
        expression_changed = (
            _expression_change(found, reaction_kind) if set_reaction_kind else []
        )
        if (planning or set_reaction_kind) and intent is None and not assignments:
            if not plan_changed + expression_changed:
                raise CommandStopped(f"nothing to change on {found.get('beat_id')}")
            only: dict[str, Any] = {"beat_id": found["beat_id"]}
            if planning:
                only["shot_plan"] = shot_plan
            if set_reaction_kind:
                only["reaction_kind"] = reaction_kind
            merged = dict(found)
            if shot_plan is not None:
                merged["shot_plan"] = shot_plan
            if set_reaction_kind:
                merged["reaction_kind"] = reaction_kind
            _refuse_compiled_beat(spine, merged)
            return {"beats": [only]}, plan_changed + expression_changed
        direction = copy.deepcopy(found.get("motion_direction") or {})
        new_intent = (
            intent if intent is not None else str(found.get("motion_intent") or "")
        )
        for key, value in assignments:
            _apply(direction, key, value, label="motion_direction")
        before = {
            "motion_intent": found.get("motion_intent"),
            "motion_direction": found.get("motion_direction") or {},
        }
        after = {"motion_intent": new_intent, "motion_direction": direction}
        changed = _changes(before, after)
        entry: dict[str, Any] = {
            "beat_id": found["beat_id"],
            "motion_intent": new_intent,
            "motion_direction": direction,
        }
        if planning:
            entry["shot_plan"] = shot_plan
            changed += plan_changed
        if set_reaction_kind:
            entry["reaction_kind"] = reaction_kind
            changed += expression_changed
        if not changed:
            raise CommandStopped(f"nothing to change on {found.get('beat_id')}")
        merged = {
            **found,
            "motion_intent": new_intent,
            "motion_direction": direction,
        }
        if shot_plan is not None:
            merged["shot_plan"] = shot_plan
        if set_reaction_kind:
            merged["reaction_kind"] = reaction_kind
        _refuse_compiled_beat(spine, merged)
        return {"beats": [entry]}, changed
    if intent is not None:
        raise CommandStopped(
            "--intent is a beat field; a frame is edited with --set on its visual_brief"
        )
    found = _find(
        spine,
        spine.get("frames") or [],
        "frame_id",
        str(frame),
        episode=episode,
        kind="frame",
    )
    return _frame_patch(spine, found, assignments)


def _refuse_compiled_beat(spine: Mapping[str, Any], beat: Mapping[str, Any]) -> None:
    """Stop an edit the take compile would refuse, before anything is sent."""

    names = _cast_names(spine)
    for stop in (
        on_screen_speaker_stop(beat, names),
        inner_voice_wording_stop(beat),
        hook_mouth_edit_stop(spine, beat),
    ):
        if stop:
            raise CommandStopped(stop + " Nothing was sent.")


def _refuse_speaker_after_line_edit(
    spine: Mapping[str, Any],
    *,
    episode: int,
    line_id: str,
    entry: Mapping[str, Any],
) -> None:
    """Stop a line edit that makes beat line 1's speaker differ from the motion subject."""

    episode_id = episode_id_for(spine, episode)
    for beat in spine.get("beats") or []:
        if not isinstance(beat, dict) or beat.get("episode_id") != episode_id:
            continue
        lines = [
            line for line in beat.get("dialogue_lines") or [] if isinstance(line, dict)
        ]
        if not lines or str(lines[0].get("line_id")) != line_id:
            continue
        updated = {
            **lines[0],
            **{key: value for key, value in entry.items() if key != "line_id"},
        }
        _refuse_compiled_beat(spine, {**beat, "dialogue_lines": [updated, *lines[1:]]})
        return


def _blocking_ids(brief: Mapping[str, Any]) -> list[str]:
    return [
        str(entry.get("cast_id"))
        for entry in brief.get("subject_blocking") or []
        if isinstance(entry, Mapping) and entry.get("cast_id")
    ]


def _frame_patch(
    spine: Mapping[str, Any],
    found: Mapping[str, Any],
    assignments: Sequence[tuple[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """A frame's patch: the WHOLE current ``visual_brief`` with only the set fields changed, and its cast.

    The server replaces the frame's brief with what is sent, so the brief always
    goes whole. ``--set cast_refs=[...]`` (cast names or ids) is the frame's own
    field, not the brief's. Who is in the shot is said twice, ``cast_refs`` and
    the brief's ``subject_blocking``, and the two must name the same people:

    - a ``subject_blocking`` edit that changes who is staged also sends
      ``cast_refs`` as the blocking's ids (what fictora-drama #498 derives; an
      older server needs both in one patch);
    - ``cast_refs`` alone (no brief change) sends no brief: the server keeps the
      staging of whoever stays and drops the rest (#498; an older server refuses);
    - both, naming different people, is refused here before anything is sent.
    """

    frame_id = found["frame_id"]
    stored = found.get("visual_brief") or {}
    brief = copy.deepcopy(stored)
    wanted_refs: list[str] | None = None
    for key, value in assignments:
        if key == "cast_refs":
            if not isinstance(value, list) or not all(
                isinstance(v, str) for v in value
            ):
                raise CommandStopped(
                    'cast_refs is a JSON list of cast names or ids: --set \'cast_refs=["Hana", "Ren"]\''
                )
            wanted_refs = [resolve_speaker(spine, who) for who in value]
        elif isinstance(value, Mapping) and isinstance(brief.get(key), dict):
            brief[key] = {**brief[key], **value}
        else:
            _apply(brief, key, value, label="visual_brief")
    old_refs = [
        str(ref) for ref in found.get("cast_refs") or [] if ref
    ] or _blocking_ids(stored)
    changed = _changes({"visual_brief": stored}, {"visual_brief": brief})
    entry: dict[str, Any] = {"frame_id": frame_id}
    if changed:
        entry["visual_brief"] = brief
    staged = _blocking_ids(brief)
    if wanted_refs is not None and changed and sorted(wanted_refs) != sorted(staged):
        raise CommandStopped(
            f"cast_refs {wanted_refs} and the edited subject_blocking {staged} name different people; "
            "send one of them (the other follows) or make them agree"
        )
    refs = wanted_refs
    if refs is None and changed and staged != _blocking_ids(stored):
        refs = staged
    if refs is not None and refs != old_refs:
        names = _cast_names(spine)
        entry["cast_refs"] = refs
        changed.append(
            f"  cast_refs: {', '.join(names.get(r, r) for r in old_refs) or '(nobody)'}  ->  "
            f"{', '.join(names.get(r, r) for r in refs) or '(nobody)'}"
        )
    if not changed:
        raise CommandStopped(f"nothing to change on {found.get('frame_id')}")
    return {"frames": [entry]}, changed


def line_edit_consequences(
    spine: Mapping[str, Any],
    *,
    episode: int,
    line_id: str,
    after_gate: bool,
    desk_was_approved: bool,
    relocalized: bool,
    desk: Path,
    new_line: bool = False,
) -> list[str]:
    """Say what a line edit did to the script approval, and whether its speaker is drawn where it is spoken.

    Parameters
    ----------
    spine
        The spine after the edit.
    episode
        Episode ordinal.
    line_id
        The edited line.
    after_gate
        The edit went through the cascade (the server's script was approved).
    desk_was_approved
        The desk's script gate was approved before the edit (syncing the lines reopens it).
    relocalized
        ``text`` changed on a JA/KO show without a pinned ``spoken_text``.
    desk
        Series desk (for the command to print).
    new_line
        ``line_id`` was just added (its performed JA/KO line is written, not re-written).

    Returns
    -------
    list[str]
        Printable lines.
    """

    out: list[str] = []
    if after_gate:
        out.append(
            "script approval: the server keeps this script approved (the cascade edited the approved story; "
            "nothing is re-drafted)."
        )
    else:
        out.append(
            "script approval: not given yet; this line is part of what the human approves at the script gate "
            "(`fictora-produce approve --gate script`)."
        )
    if desk_was_approved:
        out.append(
            "  The desk's script yes was for the old line, so it is pending again: preflight will not film episode "
            f"{episode} until the human says yes to the new line (`fictora-ops approve --desk {desk} --gate script "
            f"--episode {episode}`)."
        )
    if relocalized and new_line:
        when = (
            "before the take is filmed" if after_gate else "when the script is approved"
        )
        out.append(
            f"  The server writes the new line's performed {_spoken_language(spine)} line {when}. To choose the "
            "words yourself, pin them with --spoken."
        )
    elif relocalized:
        when = (
            "before the take is filmed" if after_gate else "when the script is approved"
        )
        out.append(
            f"  The performed {_spoken_language(spine)} line was dropped (it was written for the old English); the "
            f"server writes a new one {when}. To keep your own words, pin them with --spoken."
        )
    names = _cast_names(spine)
    heard_ids = heard_line_ids(spine)
    for beat, line in episode_lines(spine, episode=episode):
        if (
            str(line.get("line_id")) != line_id
            # Heard off screen (flagged, or the server derives it from the
            # frame and beat): nobody's lips are on that frame.
            or str(line.get("line_id")) in heard_ids
            or not beat.get("frame_id")
        ):
            continue
        frame = next(
            (
                f
                for f in spine.get("frames") or []
                if isinstance(f, Mapping) and f.get("frame_id") == beat["frame_id"]
            ),
            None,
        )
        if frame is None:
            continue
        drawn, _ = frame_cast(frame)
        cast_id = str(line.get("cast_id") or "")
        if cast_id and cast_id not in drawn:
            who = names.get(cast_id, cast_id)
            out.append(
                f"  !! {who} speaks this line on {frame.get('frame_id')} (board row {frame.get('board_row')}), which "
                f"does not draw {who}. Mark the line --off-screen, or edit that frame and redraw the board "
                "(warning only)."
            )
    return out


def _spoken_language(spine: Mapping[str, Any]) -> str:
    return str(spine.get("spoken_language") or "en-US")


def voice_cast_id(name: str) -> str:
    """The cast id a new voice-only character gets: ``cast_`` plus its name, lowercased, hyphens for the rest.

    Parameters
    ----------
    name
        ``Speaker voice``.

    Returns
    -------
    str
        ``cast_speaker-voice`` (the server's own style; at most 64 characters).
    """

    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise CommandStopped(
            f"--new-voice {name!r} needs letters or digits for its cast id"
        )
    return f"cast_{slug}"[:64]


def dialogue_tier(text: str) -> str:
    """The pacing tier the server gives a line of this length: ``micro``, ``standard`` or ``extended``.

    The server's own thresholds (fictora-drama ``classify_dialogue_tier``): up
    to 2 words is ``micro``, up to 6 words in one sentence is ``standard``,
    anything longer is ``extended``. An added line must carry one: a line
    stored without a tier stops the next episode's author ("Every beat must
    set dialogue.tier").

    Parameters
    ----------
    text
        The line as written (``text``, not the performed ``spoken_text``).

    Returns
    -------
    str
        The tier.
    """

    normalized = " ".join(text.split())
    if not normalized:
        return "standard"
    words = len(normalized.split())
    sentences = len([part for part in re.split(r"[.!?]+", normalized) if part.strip()])
    if words <= 2:
        return "micro"
    if words <= 6 and sentences <= 1:
        return "standard"
    return "extended"


def build_line_add_remove_patch(
    spine: Mapping[str, Any],
    *,
    episode: int,
    add: bool = False,
    beat: str | None = None,
    text: str | None = None,
    spoken: str | None = None,
    subtitle: str | None = None,
    speaker: str | None = None,
    off_screen: bool | None = None,
    speaker_moves: bool = False,
    remove: str | None = None,
    new_voice: str | None = None,
    role: str | None = None,
    voice_description: str | None = None,
    provider_voice: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build the spine patch that adds a line, removes one, or both, and adds a voice-only character with its line.

    The server (``PATCH /v1/spines/{id}``) keeps one line per beat, so a beat
    that already speaks takes a new line only when the same patch removes the
    old one. A new voice is heard and never drawn: its line is always off
    screen, and it must come with that line. The server checks every rule and
    answers a named 400 (:func:`explain_refusal`); the kit only checks what the
    command line itself got wrong.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    add
        Add a line: needs ``beat``, ``text`` and ``speaker`` or ``new_voice``.
    beat
        The beat's number in the episode, or its id.
    text, spoken, subtitle
        The new line's English script, pinned performed line (JA/KO), and its subtitle.
    speaker
        Someone already in the cast (name or id); left out with ``new_voice``.
    off_screen
        The speaker is heard, not seen (``True``) or seen (``False``).
    speaker_moves
        Also make the speaker the beat's motion subject (the take moves the speaker on a speaking beat).
    remove
        A line to drop: its id or its number in the episode.
    new_voice, role, voice_description, provider_voice
        A new voice-only character who speaks the added line.

    Returns
    -------
    tuple[dict[str, Any], list[str]]
        The ``patch`` object and one printable line per change.
    """

    voice_fields = {
        "--role": role,
        "--voice-description": voice_description,
        "--provider-voice": provider_voice,
    }
    if new_voice is None and any(value is not None for value in voice_fields.values()):
        raise CommandStopped(
            f"{', '.join(k for k, v in voice_fields.items() if v is not None)} describe a --new-voice"
        )
    if not add:
        stray = {"--beat": beat, "--text": text, "--spoken": spoken, "--subtitle": subtitle, "--speaker": speaker,
                 "--new-voice": new_voice, "--off-screen/--on-screen": off_screen}  # fmt: skip
        named = [key for key, value in stray.items() if value is not None] + (
            ["--speaker-moves"] if speaker_moves else []
        )
        if named:
            detail = (
                " (a new voice comes with its line)" if new_voice is not None else ""
            )
            raise CommandStopped(
                f"{', '.join(named)} describe a new line: add --add{detail}"
            )
        if remove is None:
            raise CommandStopped("pass --add, --remove or both")
    names = _cast_names(spine)
    patch: dict[str, Any] = {}
    changed: list[str] = []
    if remove is not None:
        line_id = resolve_line_id(spine, remove, episode=episode)
        found = _find_line(spine, line_id, episode=episode)
        who = names.get(str(found.get("cast_id")), str(found.get("cast_id") or "?"))
        patch["remove_dialogue_line_ids"] = [line_id]
        changed.append(f"  - {line_id}  {who}: {_short(found.get('text') or '')}")
    if not add:
        return patch, changed
    if beat is None or text is None:
        raise CommandStopped('--add needs --beat N and --text "..."')
    if subtitle is not None and spoken is None:
        raise CommandStopped(
            "--subtitle describes a pinned line: send it with --spoken"
        )
    if spoken is not None and _spoken_language(spine) == "en-US":
        raise CommandStopped(
            "--spoken pins the performed line of a JA/KO show; this show is en-US: use --text"
        )
    found_beat = _find(
        spine, spine.get("beats") or [], "beat_id", beat, episode=episode, kind="beat"
    )
    if new_voice is not None:
        if role is None or voice_description is None:
            raise CommandStopped(
                '--new-voice needs --role "..." and --voice-description "..." (how the voice sounds)'
            )
        if off_screen is False:
            raise CommandStopped(
                "a --new-voice is heard, never seen: its line is off screen (drop --on-screen)"
            )
        if speaker is not None and speaker.strip().lower() != new_voice.strip().lower():
            raise CommandStopped(
                f"--new-voice {new_voice!r} speaks the added line; drop --speaker {speaker!r}"
            )
        taken = next(
            (
                cid
                for cid, name in names.items()
                if name.strip().lower() == new_voice.strip().lower()
            ),
            None,
        )
        if taken is not None:
            raise CommandStopped(
                f"{new_voice!r} is already in the cast ({taken}): give them the line with --speaker"
            )
        cast_id = voice_cast_id(new_voice)
        card: dict[str, Any] = {
            "cast_id": cast_id,
            "name": new_voice,
            "role": role,
            "voice_description": voice_description,
        }
        if provider_voice is not None:
            card["provider_voice"] = provider_voice
        patch["add_voice_only_cast"] = [card]
        off_screen = True
        who = new_voice
        voice = (
            f", voice {provider_voice}"
            if provider_voice
            else ", voice: the server picks one (see below)"
        )
        changed.append(
            f"  + voice {new_voice} ({cast_id}), heard, never drawn: {_short(role)}; sounds {_short(voice_description)}{voice}"
        )
    elif speaker is None:
        raise CommandStopped(
            f"--add needs --speaker NAME (someone in the cast) or --new-voice NAME. {NEW_VOICE_HINT}"
        )
    else:
        cast_id = resolve_speaker(spine, speaker)
        who = names.get(cast_id, cast_id)
    entry: dict[str, Any] = {
        "beat_id": found_beat["beat_id"],
        "cast_id": cast_id,
        "text": text,
        "tier": dialogue_tier(text),
    }
    for key, value in (
        ("spoken_text", spoken),
        ("subtitle_text", subtitle),
        ("off_screen", off_screen),
    ):
        if value is not None:
            entry[key] = value
    patch["add_dialogue_lines"] = [entry]
    heard = " (off-screen)" if off_screen else ""
    performed = f"  performed: {spoken}" if spoken else ""
    changed.append(
        f"  + beat {found_beat.get('ordinal')} ({found_beat['beat_id']})  {who}{heard}: {_short(text)}{performed}"
    )
    if speaker_moves:
        direction = copy.deepcopy(found_beat.get("motion_direction") or {})
        before = direction.get("subject_cast_id")
        direction["subject_cast_id"] = cast_id
        patch["beats"] = [
            {"beat_id": found_beat["beat_id"], "motion_direction": direction}
        ]
        changed.append(
            f"  motion subject of {found_beat['beat_id']}: {names.get(str(before), before or 'none')}  ->  {who}"
        )
    return patch, changed


#: What to do about each named refusal of a line edit (fictora-drama ``SpineLineEditRefused`` codes).
REFUSAL_FIXES: dict[str, str] = {
    "beat_already_has_line": (
        "a beat holds one line. To replace it, remove the old line in the same command (`--add ... --remove {lines}`); "
        "to change its words or speaker, `line --line {lines} --text/--speaker`"
    ),
    "line_speaker_not_motion_subject": (
        "a speaking beat moves its speaker, and {beats}. Send the beat's motion subject in the same command: add "
        "--speaker-moves (the shot then moves the speaker), or put the line on a beat where the speaker moves"
    ),
    "voice_only_cast_needs_a_line": "a new voice comes with its line: send --new-voice together with --add",
    "voice_only_cast_on_screen": (
        "a new voice is heard, never seen: keep its line off screen and keep it out of every frame "
        "(lines {lines}, frames {frames})"
    ),
    "cast_id_taken": "someone in the cast already has the id {cast_ids}: give them the line with --speaker, or pick another --new-voice name",
    "cast_limit_reached": "a story holds {limit} characters at most: give the line to someone already in the cast with --speaker",
    "line_id_taken": "that line id is used; the kit lets the server name new lines, so re-save the story (`spine --refresh`) and try again",
    "invalid_patch": "an id is not on the story; list the lines with `line --desk D --episode N` and use their numbers",
    "cascade_edit_out_of_scope": "after the script gate one command edits one episode: add and remove lines of episode {episode} only",
    # fictora-drama #562; the text is built per scene by creation.harness.minor_scene_refusal.minor_scene_fix.
    MINOR_IN_INTIMATE_SCENE: "move the child out of that shot, or change the scene so it is not intimate",
}


def _invalid_patch_fix(message: str) -> str:
    """The fix for an ``invalid_patch``: what the server named (fictora-drama #498), not one fixed guess.

    The id hint is given only when the server names an id it does not know, and as one possible cause
    when an older deploy names nothing.
    """

    details = _refusal_details(message)
    unknown = details.get("unknown_frame_cast_ids") or details.get("unknown_ids")
    named = server_named_rules(message)
    if unknown:
        return f"{REFUSAL_FIXES[INVALID_PATCH]} (not on the story: {unknown})"
    if named is None:
        return (
            "the server named no field (a deploy older than fictora-drama #498). One common cause: "
            + REFUSAL_FIXES[INVALID_PATCH]
        )
    hints = []
    for field, rule in STRICT_FIELDS.items():
        if any(re.search(rf"\b{re.escape(field)}\b", row) for row in named):
            hints.append(f"{field}: {rule}")
    rows = "; ".join(named)
    return f"the server named: {rows}" + "".join(f"\n  rule: {hint}" for hint in hints)


def _refusal_details(message: str) -> dict[str, Any]:
    raw = re.search(r"\(details (\{.*\})\)", message)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.group(1))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def explain_refusal(message: str, spine: Mapping[str, Any], *, episode: int) -> str:
    """Add the fix a named line-edit refusal implies to the server's message.

    Parameters
    ----------
    message
        The kit's ``HTTP 400 PATCH …: code: message (details {…})`` text.
    spine
        Spine JSON before the edit (for names).
    episode
        Episode ordinal.

    Returns
    -------
    str
        ``message`` plus a ``fix:`` line when the code is a known line-edit rule, else ``message``.
    """

    found = re.search(r": ([a-z][a-z0-9_]*): ", message)
    code = found.group(1) if found else ""
    template = REFUSAL_FIXES.get(code)
    if template is None:
        return message
    if code == MINOR_IN_INTIMATE_SCENE:
        if "never in a romantic, sexual or intimate scene" in message:
            return message  # api_error_text already said it
        return f"{message}\n{minor_scene_fix(_refusal_details(message) or None)}"
    if code == INVALID_PATCH:
        return f"the server refused the edit ({code}): {message}\n  fix: {_invalid_patch_fix(message)}"
    details: dict[str, Any] = {}
    raw = re.search(r"\(details (\{.*\})\)", message)
    if raw:
        try:
            details = json.loads(raw.group(1))
        except ValueError:
            details = {}
    names = _cast_names(spine)
    beats = "; ".join(
        f"{b.get('beat_id')} moves {names.get(str(b.get('motion_subject_cast_id')), b.get('motion_subject_cast_id'))}, "
        f"not {names.get(str(b.get('speaker_cast_id')), b.get('speaker_cast_id'))}"
        for b in details.get("beats") or []
        if isinstance(b, Mapping)
    )

    def listed(key: str) -> str:
        return ", ".join(str(v) for v in details.get(key) or []) or "?"

    fix = template.format(
        lines=listed("line_ids"),
        frames=listed("frame_ids"),
        cast_ids=listed("cast_ids"),
        limit=details.get("limit", 4),
        beats=beats or "the beat moves someone else",
        episode=episode,
    )
    return f"the server refused the line edit ({code}): {message}\n  fix: {fix}"


def say_patch_warnings(
    before: Mapping[str, Any], answer: Any, *, out: Any
) -> list[str]:
    """Print the authoring warnings a ``PATCH /v1/spines/{id}`` answer has that the spine before it did not.

    The spine response carries every current warning (fictora-drama #538), so the
    ones the story already had are left out: only what this edit introduced is said.
    Nudges only; nothing stops.

    Parameters
    ----------
    before
        The spine the patch was made on.
    answer
        The PATCH answer (a spine response; anything else reads as no warnings).
    out
        Text stream.

    Returns
    -------
    list[str]
        The printed lines, for the run notes.
    """

    after = answer if isinstance(answer, Mapping) else {}
    return say_warnings(
        introduced(authoring_warnings(before), authoring_warnings(after)),
        spine=after if after.get("beats") else before,
        out=out,
    )


def _send_story_edit(
    desk: Path,
    *,
    episode: int,
    build: Callable[[Mapping[str, Any]], tuple[dict[str, Any], list[str], str]],
    select_regen: bool,
    preview_only: bool,
    out: Any,
) -> tuple[Path, Path, dict[str, Any], bool, str, list[str]]:
    """Send one story edit (``PATCH`` before the script gate, the cascade after it) and save the story again.

    Returns
    -------
    tuple[Path, Path, dict[str, Any], bool, str, list[str]]
        Resolved desk, ``api/spine.json``, the fresh spine, whether the cascade ran, what was edited, the changes.
    """

    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        patch, changed, what = build(spine)
        print(f"ep{episode:02d} {what}:", file=out)
        for line in changed:
            print(line, file=out)
        cascade = spine.get("approval_state") == "approved"
        if not cascade and preview_only:
            raise CommandStopped(
                "--preview is for an approved script; before the script gate the edit is a plain patch"
            )
        notes: list[str] = []
        try:
            if not cascade:
                try:
                    answer = run.patch(
                        f"/v1/spines/{state.spine_id}",
                        {"spine_version": spine["spine_version"], "patch": patch},
                    )
                    notes = say_patch_warnings(spine, answer, out=out)
                except SystemExit as exc:
                    if "cascade_required" not in str(exc.code):
                        raise CommandStopped(str(exc.code)) from None
                    cascade = True
                    print(
                        "(the server asks for a cascade: the script is approved there)",
                        file=out,
                    )
            if cascade:
                notes = _run_cascade(
                    desk,
                    run,
                    spine,
                    patch,
                    episode=episode,
                    select_regen=select_regen,
                    preview_only=preview_only,
                    out=out,
                )
        except CommandStopped as exc:
            fields = _explain_field_refusal(run, str(exc), patch, changed)
            raise EditRefused(
                f"{exc}\n{fields}"
                if fields
                else explain_refusal(str(exc), spine, episode=episode),
                items=change_items(changed),
            ) from None
        except SystemExit as exc:
            code = exc.code
            raise EditRefused(
                code if isinstance(code, str) else api_error_text(code),
                items=change_items(changed),
            ) from None
        fresh = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    if notes and not preview_only:
        _note(desk, episode, "\n".join(notes))
    return (
        desk,
        save_spine_snapshot(desk, episode, fresh),
        fresh,
        cascade,
        what,
        changed,
    )


def _explain_field_refusal(
    run: DramaApiRunSession, message: str, patch: Mapping[str, Any], changed: list[str]
) -> str | None:
    """:func:`creation.patch_refusal.explain_invalid_patch`; the deploy's schema is read only for bare copy."""

    if not (patch.get("frames") or patch.get("beats")):
        return None
    code = refusal_code(message)
    if code in FRAME_CAST_FIXES or (
        code == INVALID_PATCH and server_named_rules(message) is not None
    ):
        return explain_invalid_patch(message, patch, changed)
    if code != INVALID_PATCH:
        return None
    try:
        status, doc = run.get_optional("/openapi.json")
    except (SystemExit, httpx.HTTPError) as exc:
        status, doc = 0, None
        print(
            f"(could not read /openapi.json to check the fields: {exc})",
            file=sys.stderr,
        )
    return explain_invalid_patch(
        message, patch, changed, openapi=doc if 200 <= status < 300 else None
    )


def _after_line_edit(
    desk: Path,
    fresh: dict[str, Any],
    *,
    episode: int,
    line_id: str,
    after_gate: bool,
    relocalized: bool,
    new_line: bool = False,
    out: Any,
) -> None:
    desk_was_approved = (
        episode_by_ordinal(load_series(desk), episode).script.status == "approved"
    )
    counts = sync_spine_lines(desk, fresh, episode=episode)
    print(
        f"desk lines synced from the server: {', '.join(f'{t} {n}' for t, n in counts.items())}",
        file=out,
    )
    for line in line_edit_consequences(
        fresh,
        episode=episode,
        line_id=line_id,
        after_gate=after_gate,
        desk_was_approved=desk_was_approved,
        relocalized=relocalized,
        desk=desk,
        new_line=new_line,
    ):
        print(line, file=out)


def new_voice_pick_warning(
    desk: Path, new_voice: str, *, picked: str | None = None
) -> str:
    """Name the voice the server chose for a ``--new-voice`` sent without ``--provider-voice``, to confirm by ear.

    A server with fictora-drama ec6fec0e picks the catalog voice that best
    fits ``--voice-description``: the gender it names is a filter, then
    accent, age and timbre words rank the rest, and an unused voice beats a
    taken one. An older deploy gave the first catalog voice nobody used (an
    "adult man" got a woman's voice, SCP-173 ep 2; L-20260929-20). The answer
    does not say which rule ran, so the pick is named and heard either way.

    Parameters
    ----------
    desk
        Series desk, for the command to paste.
    new_voice
        The new character's name.
    picked
        ``voice_brief.provider_voice`` on the new card in the server's answer (``None`` on a preview,
        or when the answer does not carry it).

    Returns
    -------
    str
        The pick, how it was made, and the audition command for the new cast id.
    """

    cast_id = voice_cast_id(new_voice)
    chose = (
        f"the server picked {picked} for {new_voice}"
        if picked
        else f"the server picks {new_voice}'s voice when the edit is sent"
    )
    return (
        f"!! No --provider-voice: {chose}. A current server picks the catalog voice that best fits "
        "--voice-description (the gender it names first, then accent, age and timbre); an older deploy "
        "gave the first voice nobody uses, whatever the description says. Hear it before filming: "
        f"`fictora-produce voice --desk {desk} --cast {cast_id} --audition --voices A,B,C,D` "
        f"(include {picked or 'the picked voice'} and three that fit the description), then `voice --pick N`. "
        "Or send the line again with --provider-voice NAME."
    )


def _card_voice(spine: Mapping[str, Any], cast_id: str) -> str | None:
    for card in spine.get("cast") or []:
        if isinstance(card, Mapping) and card.get("cast_id") == cast_id:
            brief = card.get("voice_brief")
            voice = brief.get("provider_voice") if isinstance(brief, Mapping) else None
            return str(voice) if voice else None
    return None


def new_voice_narrator_answer(
    desk: Path,
    new_voice: str,
    *,
    heard_only: Sequence[str] = (),
    on_screen: Sequence[str] = (),
    ask: Callable[[str], str] | None = None,
) -> NarratorAnswer | None:
    """Ask, before anything is sent, whether a narrator-named ``--new-voice`` is heard only.

    ``line --new-voice`` adds someone heard and never seen, so a yes goes
    ahead and a no stops: a drawn character is not added this way. A name
    not like a narrator's is not asked (founder decision 5).

    Parameters
    ----------
    desk
        Series desk (a saved answer for the same cast id is reused).
    new_voice
        The new voice's name.
    heard_only, on_screen
        Names from ``--narrator-heard-only`` / ``--narrator-on-screen``.
    ask
        Prompt function, or ``None`` when nobody can answer.

    Returns
    -------
    NarratorAnswer | None
        The yes to save once the voice is added; ``None`` for a name not like
        a narrator's.

    Raises
    ------
    NarratorQuestionOpen
        Nobody to ask and no flag answers it.
    CommandStopped
        The answer is "on screen".
    """

    if not named_like_narrator(new_voice):
        return None
    key = new_voice.strip().casefold()
    cast_id = voice_cast_id(new_voice)
    saved = load_answers(desk.expanduser().resolve()).get(cast_id)
    if any(name.strip().casefold() == key for name in heard_only):
        heard: bool | None = True
    elif any(name.strip().casefold() == key for name in on_screen):
        heard = False
    elif saved is not None:
        heard = saved.heard_only
    elif ask is not None:
        heard = ask(narrator_question(new_voice)).strip().casefold() in {"y", "yes"}
    else:
        raise NarratorQuestionOpen(
            stop_message([new_voice], rerun="your `line` command again, with")
        )
    if not heard:
        raise CommandStopped(
            f"{new_voice} on screen is an ordinary drawn character, and `line --new-voice` only adds a voice "
            "heard and never seen. A drawn character joins through the story (the next episode's `author`)."
        )
    return NarratorAnswer(cast_id=cast_id, name=new_voice, heard_only=True)


def run_line(
    desk: Path,
    *,
    episode: int,
    line: str | None = None,
    text: str | None = None,
    spoken: str | None = None,
    subtitle: str | None = None,
    speaker: str | None = None,
    off_screen: bool | None = None,
    add: bool = False,
    beat: str | None = None,
    remove: str | None = None,
    speaker_moves: bool = False,
    new_voice: str | None = None,
    role: str | None = None,
    voice_description: str | None = None,
    provider_voice: str | None = None,
    select_regen: bool = False,
    preview_only: bool = False,
    strand_voice: bool = False,
    narrator_heard_only: Sequence[str] = (),
    narrator_on_screen: Sequence[str] = (),
    ask: Callable[[str], str] | None = None,
    look: str | None = None,
    new_character: str | None = None,
    staging: str | None = None,
    language: str | None = None,
    out: Any = None,
) -> Path | None:
    """Change, add or remove a line on the server and on the desk in one step; with no change, list the lines.

    The server's story is edited (``PATCH /v1/spines/{id}`` before the script
    gate, the cascade after it): ``dialogue_lines`` for an existing line,
    ``add_dialogue_lines`` / ``remove_dialogue_line_ids`` to add or drop one, and
    ``add_voice_only_cast`` for a new voice heard with its added line. The story
    is saved again as ``api/spine.json`` and the desk's lines are replaced with
    the server's. A named refusal is printed with the fix it implies
    (:func:`explain_refusal`).

    Parameters
    ----------
    desk
        Series desk with a story.
    episode
        Episode ordinal.
    line
        Line id or its number in the episode, to change (``line`` with no change lists them).
    text, spoken, subtitle, speaker, off_screen
        The changed or added line (:func:`build_patch`, :func:`build_line_add_remove_patch`).
    add, beat, remove, speaker_moves, new_voice, role, voice_description, provider_voice
        As :func:`build_line_add_remove_patch`.
    select_regen, preview_only
        As :func:`run_edit`.
    strand_voice
        Let an edit through that leaves a heard-only character with no lines
        (:func:`creation.stranded_voice.voices_left_without_lines`); without it the kit stops before sending.
    narrator_heard_only, narrator_on_screen, ask
        For a ``new_voice`` named like a narrator: the operator's answer to
        "heard only, never seen?" (:func:`new_voice_narrator_answer`).
    look
        With ``new_voice``: their look, sent once the voice is added
        (:func:`creation.cast_commands.run_cast_look`). With ``new_character``: required.
    new_character, staging
        A new character who is seen (:func:`creation.cast_commands.run_new_character`).
    language
        With ``spoken`` on a show the server holds as English: the language it is really performed in
        (:func:`creation.cast_commands.english_show_pin`).
    out
        Text stream.

    Returns
    -------
    Path | None
        The refreshed ``api/spine.json``; ``None`` when only listing.
    """

    out = out or sys.stdout
    from creation import cast_commands

    if new_character is not None:
        stray = {"--new-voice": new_voice, "--speaker": speaker, "--remove": remove, "--line": line,
                 "--off-screen/--on-screen": off_screen, "--speaker-moves": speaker_moves or None}  # fmt: skip
        named = [key for key, value in stray.items() if value is not None]
        if named or not add:
            raise CommandStopped(
                '--new-character comes with its line on a silent beat: --add --beat N --text "..." '
                f"--new-character NAME --role ... --voice-description ... --look ...; drop {', '.join(named) or '-'}"
            )
        return cast_commands.run_new_character(
            desk, episode=episode, beat=beat, text=text, name=new_character, role=role,
            voice_description=voice_description, provider_voice=provider_voice, look=look, staging=staging,
            spoken=spoken, subtitle=subtitle, select_regen=select_regen, preview_only=preview_only, out=out,
        )  # fmt: skip
    if staging is not None:
        raise CommandStopped("--staging goes with --new-character")
    if look is not None and new_voice is None:
        raise CommandStopped(
            "--look goes with --new-voice or --new-character. To give someone in the cast a look: "
            "`fictora-produce cast --desk D --name NAME --look @look.txt`"
        )
    if spoken is not None and (line is not None or add):
        cast_commands.english_show_pin(
            desk,
            episode=episode,
            line=line,
            beat=beat,
            spoken=spoken,
            subtitle=subtitle,
            language=language,
        )
    elif language is not None:
        raise CommandStopped("--language goes with --spoken")
    adding = add or remove is not None or new_voice is not None
    narrator = (
        new_voice_narrator_answer(
            desk,
            new_voice,
            heard_only=narrator_heard_only,
            on_screen=narrator_on_screen,
            ask=ask,
        )
        if new_voice is not None
        else None
    )
    if adding:
        if line is not None:
            raise CommandStopped(
                "--line changes a line; --add/--remove add or drop one: run them as two commands"
            )
        if look is not None and new_voice is not None:
            _, state, run = _desk_session(desk)
            try:
                before = run.spine(state.spine_id or "")
            finally:
                run.client.close()
            # The look is checked before the voice is sent, and sent once it is added.
            card = {"cast_id": voice_cast_id(new_voice), "name": new_voice}
            cast_commands.look_patch(before, card, look)

        sent: dict[str, Any] = {}

        def build_add(
            spine: Mapping[str, Any],
        ) -> tuple[dict[str, Any], list[str], str]:
            patch, changed = build_line_add_remove_patch(
                spine, episode=episode, add=add, beat=beat, text=text, spoken=spoken, subtitle=subtitle,
                speaker=speaker, off_screen=off_screen, speaker_moves=speaker_moves, remove=remove,
                new_voice=new_voice, role=role, voice_description=voice_description, provider_voice=provider_voice,
            )  # fmt: skip
            what = " and ".join(
                part
                for part, on in (
                    ("add a line", add),
                    ("remove a line", remove is not None),
                )
                if on
            )
            stranded = voices_left_without_lines(
                spine,
                removed=patch.get("remove_dialogue_line_ids") or [],
                added=[
                    str(a.get("cast_id")) for a in patch.get("add_dialogue_lines") or []
                ],
            )
            _guard_strand(
                stranded, strand_voice=strand_voice, desk=desk, episode=episode,
                what="removing " + ", ".join(patch.get("remove_dialogue_line_ids") or []),
                changed=changed, out=out,
            )  # fmt: skip
            sent.update(patch)
            return patch, changed, what

        desk, path, fresh, cascade, what, changed = _send_story_edit(
            desk,
            episode=episode,
            build=build_add,
            select_regen=select_regen,
            preview_only=preview_only,
            out=out,
        )
        if not preview_only:
            added = ""
            if add:
                beat_id = sent["add_dialogue_lines"][0]["beat_id"]
                added = next(
                    (
                        str(ln.get("line_id"))
                        for b, ln in episode_lines(fresh, episode=episode)
                        if b.get("beat_id") == beat_id
                    ),
                    "",
                )
                if added:
                    print(f"new line: {added}", file=out)
            relocalized = add and spoken is None and _spoken_language(fresh) != "en-US"
            _after_line_edit(
                desk, fresh, episode=episode, line_id=added, after_gate=cascade, relocalized=relocalized,
                new_line=True, out=out,
            )  # fmt: skip
            _note(
                desk,
                episode,
                f"line: {what}: " + "; ".join(item.strip() for item in changed),
            )
        if new_voice is not None and provider_voice is None:
            picked = (
                None if preview_only else _card_voice(fresh, voice_cast_id(new_voice))
            )
            print(new_voice_pick_warning(desk, new_voice, picked=picked), file=out)
        if narrator is not None and not preview_only:
            save_answer(desk.expanduser().resolve(), narrator)
        items = change_items(changed)
        if look is not None and new_voice is not None:
            if preview_only:
                print(
                    f"(--look: {new_voice}'s look is sent once the voice is added)",
                    file=out,
                )
            else:
                try:
                    cast_commands.run_cast_look(
                        desk,
                        name=voice_cast_id(new_voice),
                        look=look,
                        select_regen=select_regen,
                        verdict=False,
                        out=out,
                    )
                except CommandStopped as exc:
                    raise EditRefused(
                        f"{exc}\n{new_voice} was added as a voice; the look was not sent. Send it with: "
                        f'fictora-produce cast --desk {desk} --name "{new_voice}" --look {shlex.quote(look)}',
                        items=[f"look for {new_voice}"],
                    ) from None
                items.append(f"look for {new_voice}")
        for row in edit_verdict(items, preview=preview_only):
            print(row, file=out)
        return path
    if beat is not None or speaker_moves:
        raise CommandStopped("--beat and --speaker-moves go with --add")
    changes = (text, spoken, subtitle, speaker, off_screen)
    if line is None or all(value is None for value in changes):
        if line is not None:
            raise CommandStopped(
                "say what to change: --text, --spoken, --speaker, --off-screen or --on-screen"
            )
        _, state, run = _desk_session(desk)
        try:
            spine = run.spine(state.spine_id or "")
        finally:
            run.client.close()
        print(
            f"ep{episode:02d} lines (use the number or the id with --line or --remove):",
            file=out,
        )
        for row in line_listing(spine, episode=episode) or ["  (none)"]:
            print(row, file=out)
        return None
    _, state, run = _desk_session(desk)
    try:
        line_id = resolve_line_id(
            run.spine(state.spine_id or ""), line, episode=episode
        )
    finally:
        run.client.close()
    return run_edit(
        desk,
        episode=episode,
        line_id=line_id,
        text=text,
        spoken=spoken,
        subtitle=subtitle,
        speaker=speaker,
        off_screen=off_screen,
        select_regen=select_regen,
        preview_only=preview_only,
        strand_voice=strand_voice,
        out=out,
    )


def _guard_strand(
    stranded: list[tuple[str, str]],
    *,
    strand_voice: bool,
    desk: Path,
    episode: int,
    what: str,
    changed: list[str],
    out: Any,
) -> None:
    """Stop a line edit that leaves a heard-only character with no lines, unless ``--strand-voice``.

    With the flag the warning is printed and added to the change lines (so the run note keeps it).
    """

    if not stranded:
        return
    if not strand_voice:
        raise CommandStopped(
            strand_refusal(stranded, desk=desk, episode=episode, what=what)
        )
    warning = strand_override_note(stranded)
    print(warning, file=out)
    changed.append(f"  {warning}")


def _check_delivery(
    desk: Path, assignments: Sequence[tuple[str, Any]], *, beat: str | None
) -> None:
    """Stop a ``--set delivery=…`` the server would refuse, before anything is sent (L-20260923-3).

    The values come from the deploy's ``/openapi.json`` (one free read, only when a delivery is set),
    else :data:`creation.patch_refusal.LINE_DELIVERIES`. ``null`` clears it and needs no read.
    """

    wanted = [value for key, value in assignments if key.split(".")[-1] == "delivery"]
    if not wanted:
        return
    if beat is None:
        raise CommandStopped(
            "delivery is a beat field (how the speaker plays the beat's line): "
            "pass --beat N, e.g. `edit --episode E --beat N --set delivery=whispered`"
        )
    if all(value is None for value in wanted):
        return
    _, _, check = _desk_session(desk)
    try:
        status, doc = check.get_optional("/openapi.json")
    except (SystemExit, httpx.HTTPError):
        status, doc = 0, None
    finally:
        check.client.close()
    values, source = delivery_values(doc if 200 <= status < 300 else None)
    for value in wanted:
        refused = delivery_refusal(value, values, source)
        if refused:
            raise CommandStopped(refused)


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
    speaker: str | None = None,
    off_screen: bool | None = None,
    shot_plan: list[dict[str, str]] | None = None,
    clear_shot_plan: bool = False,
    expression: str | None = None,
    select_regen: bool = False,
    preview_only: bool = False,
    strand_voice: bool = False,
    out: Any = None,
) -> Path:
    """Edit one beat, frame or line: ``PATCH`` before the script gate, cascade preview + execute after it.

    After the script is approved the plain patch answers 409 ``cascade_required``;
    the cascade's paid (``estimated_tier: media``) items are left out unless
    ``select_regen``. Redraw a board afterwards with ``redraw-board`` so it is
    downloaded and booked.

    A line edit also puts the server's lines on the desk (before and after the
    script gate, so the desk and the story never disagree) and says what it did
    to the script approval.

    Parameters
    ----------
    desk
        Series desk with a story.
    episode
        Episode ordinal.
    beat, frame, line_id, intent, assignments, text, spoken, subtitle, speaker, off_screen
        As :func:`build_patch`.
    shot_plan, clear_shot_plan
        A beat's new shot plan, or remove it (:func:`build_patch`). After the script gate the cascade
        marks the take's frames and its next ``redraw-board`` re-authors them to the plan. A 422 is
        explained as an older server (:data:`creation.shot_plan.OLDER_SERVER_HINT`); a server that
        answers but does not keep the plan is said plainly.
    expression
        A beat's expression: a kind or label from ``GET /v1/capabilities``, or ``none`` to clear. Checked
        against the deploy's library before anything is sent; a deploy older than the field is refused
        (:data:`creation.expression.OLDER_SERVER`). After the script gate it goes through the cascade like
        any beat edit, and the take's board is marked for a redraw.
    select_regen
        After the script gate: also run the cascade's paid items.
    preview_only
        After the script gate: print the cascade and stop.
    strand_voice
        Let a speaker change through that leaves a heard-only character with no lines (as :func:`run_line`).
    out
        Text stream.

    Returns
    -------
    Path
        The refreshed ``api/spine.json``.
    """

    out = out or sys.stdout
    planning = shot_plan is not None or clear_shot_plan
    setting_expression = expression is not None
    _check_delivery(desk, assignments, beat=beat)
    kind: str | None = None
    if setting_expression:
        if beat is None:
            raise CommandStopped("--expression belongs to a beat: pass --beat N")
        _, _, check = _desk_session(desk)
        try:
            kind = resolve_or_stop(str(expression), deploy_expressions(check))
        finally:
            check.client.close()

    def build(spine: Mapping[str, Any]) -> tuple[dict[str, Any], list[str], str]:
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
            speaker=speaker,
            off_screen=off_screen,
            shot_plan=shot_plan,
            clear_shot_plan=clear_shot_plan,
            set_reaction_kind=setting_expression,
            reaction_kind=kind,
        )
        what = (
            f"beat {beat}"
            if beat is not None
            else (f"frame {frame}" if frame is not None else f"line {line_id}")
        )
        moved = {
            str(entry["line_id"]): str(entry["cast_id"])
            for entry in patch.get("dialogue_lines") or []
            if entry.get("cast_id")
        }
        if moved:
            _guard_strand(
                voices_left_without_lines(spine, speakers=moved),
                strand_voice=strand_voice, desk=desk, episode=episode,
                what="giving " + ", ".join(moved) + " to another speaker", changed=changed, out=out,
            )  # fmt: skip
        return patch, changed, what

    try:
        desk, path, fresh, cascade, what, changed = _send_story_edit(
            desk,
            episode=episode,
            build=build,
            select_regen=select_regen,
            preview_only=preview_only,
            out=out,
        )
    except CommandStopped as exc:
        if planning and "HTTP 422" in str(exc):
            raise EditRefused(
                f"the server refused the shot plan: {exc}\n  {OLDER_SERVER_HINT}",
                items=getattr(exc, "items", ()),
            ) from None
        raise
    not_kept: list[str] = []
    if planning and not preview_only:
        if not _report_shot_plan(
            fresh,
            episode=episode,
            beat=str(beat),
            wanted=None if clear_shot_plan else shot_plan,
            out=out,
        ):
            not_kept.append("shot_plan")
    if setting_expression and not preview_only:
        if not _report_expression(
            fresh, episode=episode, beat=str(beat), wanted=kind, out=out
        ):
            not_kept.append("expression")
    if line_id is not None and not preview_only:
        relocalized = (
            text is not None and spoken is None and _spoken_language(fresh) != "en-US"
        )
        _after_line_edit(
            desk,
            fresh,
            episode=episode,
            line_id=line_id,
            after_gate=cascade,
            relocalized=relocalized,
            out=out,
        )
    if not preview_only:
        _note(
            desk,
            episode,
            f"edit {what}: " + "; ".join(line.strip() for line in changed),
        )
    for row in edit_verdict(
        change_items(changed), not_kept=not_kept, preview=preview_only
    ):
        print(row, file=out)
    return path


def _report_shot_plan(
    spine: Mapping[str, Any],
    *,
    episode: int,
    beat: str,
    wanted: list[dict[str, str]] | None,
    out: Any,
) -> bool:
    """Print the beat's plan as the server now holds it, and say so when it did not keep what was sent.

    Returns ``True`` when the server holds the plan that was sent.
    """

    found = _find(
        spine, spine.get("beats") or [], "beat_id", beat, episode=episode, kind="beat"
    )
    held = found.get("shot_plan") or None
    shown = plan_lines(held, indent="  ") or [
        "  (no plan: the frames author chooses the shots)"
    ]
    print(f"{found.get('beat_id')} shot plan on the server now:", file=out)
    for line in shown:
        print(line, file=out)
    if not same_plan(held, wanted):
        print(
            "  !! the server answered but does not hold the plan that was sent: it is likely older than beat shot "
            "plans (fictora-drama #464) and dropped the field. Nothing on the board will follow it.",
            file=out,
        )
        return False
    return True


def deploy_expressions(run: DramaApiRunSession) -> list[dict[str, Any]]:
    """The deploy's expression library, refusing a deploy older than beat expressions. Spends nothing.

    Parameters
    ----------
    run
        Session (reads ``/openapi.json`` and ``GET /v1/capabilities``).

    Returns
    -------
    list[dict[str, Any]]
        ``{kind, label, comedy}`` per expression, in the library's order.

    Raises
    ------
    CommandStopped
        The deploy's beat patch has no ``reaction_kind``, it has no ``/v1/capabilities``, or the answer is
        not one the kit reads.
    """

    status, doc = run.get_optional("/openapi.json")
    if 200 <= status < 300 and server_takes_expression(doc) is False:
        raise CommandStopped(
            OLDER_SERVER.format(why="its spine patch has no beats[].reaction_kind")
        )
    status, body = run.get_optional(CAPABILITIES_PATH)
    if status == 404:
        raise CommandStopped(OLDER_SERVER.format(why=f"it has no {CAPABILITIES_PATH}"))
    if not 200 <= status < 300:
        raise CommandStopped(
            f"{CAPABILITIES_PATH} answered HTTP {status}: {_short(body)}"
        )
    try:
        return expression_options(body)
    except ExpressionError as exc:
        raise CommandStopped(str(exc)) from None


def resolve_or_stop(raw: str, options: Sequence[Mapping[str, Any]]) -> str | None:
    """:func:`creation.expression.resolve_expression`, stopping the command on a kind the deploy lacks."""

    try:
        return resolve_expression(raw, options)
    except ExpressionError as exc:
        raise CommandStopped(str(exc)) from None


def _report_expression(
    spine: Mapping[str, Any],
    *,
    episode: int,
    beat: str,
    wanted: str | None,
    out: Any,
) -> bool:
    """Print the beat's expression as the server now holds it, and say so when it did not keep what was sent.

    Returns ``True`` when the server holds the expression that was sent.
    """

    found = _find(
        spine, spine.get("beats") or [], "beat_id", beat, episode=episode, kind="beat"
    )
    held = beat_expression(found)
    print(
        f"{found.get('beat_id')} expression on the server now: "
        f"{held or 'none (the frames author chooses)'}",
        file=out,
    )
    if held != wanted:
        print(
            "  !! the server answered but does not hold the expression that was sent: it is likely older than beat "
            "expressions (fictora-drama #482) and dropped the field. Nothing on the board will follow it.",
            file=out,
        )
        return False
    return True


def run_expressions(
    desk: Path, *, episode: int | None = None, out: Any = None
) -> list[dict[str, Any]]:
    """Print the deploy's expression library and, with ``episode``, what each of its beats asks for.

    Spends nothing. Refuses a deploy older than beat expressions.

    Parameters
    ----------
    desk
        Series desk with a story.
    episode
        Episode ordinal whose beats to list (optional).
    out
        Text stream.

    Returns
    -------
    list[dict[str, Any]]
        The library.
    """

    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    try:
        options = deploy_expressions(run)
        spine = run.spine(state.spine_id or "") if episode is not None else None
    finally:
        run.client.close()
    print(
        f"expressions this deploy offers ({len(options)}); set one with "
        "`edit --episode N --beat B --expression KIND` (`none` clears):",
        file=out,
    )
    for line in vocabulary_lines(options):
        print(line, file=out)
    if spine is not None and episode is not None:
        episode_id = episode_id_for(spine, episode)
        print(f"ep{episode:02d} beats:", file=out)
        for found in spine.get("beats") or []:
            if isinstance(found, Mapping) and found.get("episode_id") == episode_id:
                print(
                    f"  beat {found.get('ordinal')} ({found.get('beat_id')}): "
                    f"{beat_expression(found) or 'none (the frames author chooses)'}",
                    file=out,
                )
    return options


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
    edit: Mapping[str, Any] | None = None,
) -> list[str]:
    """Preview one edit's cascade, print it (and the warnings it introduces), then execute it unless preview only.

    ``edit`` replaces the episode story edit built from ``patch`` (the ``cast_card`` edit of ``cast --look``).

    Returns
    -------
    list[str]
        The authoring-warning lines printed (:func:`creation.authoring_warnings.say_warnings`), for the run notes.
    """

    spine_id = str(spine.get("spine_id") or load_production(desk).spine_id or "")
    edit = edit or {
        "scope": "section",
        "target_type": "episode",
        "target_id": episode_id_for(spine, episode),
        "patch": patch,
    }
    try:
        preview = run.post(
            f"/v1/spines/{spine_id}/cascade/preview",
            {"spine_version": spine["spine_version"], "edit": edit},
        )
    except SystemExit as exc:
        raise CommandStopped(str(exc.code)) from None
    saved = _save_desk_json(desk, "cascade-preview", preview)
    items = [item for item in preview.get("items") or [] if isinstance(item, Mapping)]
    chosen = {
        str(item["item_id"]): (
            select_regen
            if item.get("estimated_tier") == PAID_TIER
            else bool(item.get("selected", True))
        )
        for item in items
    }
    print(
        f"cascade {preview.get('proposal_id')} ({len(items)} item(s), saved {saved.name}):",
        file=out,
    )
    for item in items:
        relation = item.get("relation") or {}
        mark = "run " if chosen[str(item["item_id"])] else "skip"
        paid = " (paid)" if item.get("estimated_tier") == PAID_TIER else ""
        print(
            f"  [{mark}] {item.get('item_id')}  {item.get('recipe_id')}  {relation.get('type')}:{relation.get('id')}  "
            f"tier {item.get('estimated_tier')}{paid}  {_short(item.get('reason') or '')}",
            file=out,
        )
    # The structured list wins (fictora-drama #538); its messages are also in `warnings`, so skip those there.
    nudges = authoring_warnings(preview)
    shown = {" ".join(str(w.get("message") or "").split()) for w in nudges}
    for warning in preview.get("warnings") or []:
        if " ".join(str(warning).split()) not in shown:
            print(f"  warning: {_short(warning)}", file=out)
    notes = say_warnings(nudges, spine=spine, out=out)
    if preview_only:
        print("(preview only: nothing was changed)", file=out)
        return notes
    state = load_production(desk)
    key = f"{state.idempotency_prefix}-cascade-{str(preview['proposal_id'])[-24:]}"
    try:
        answer = run.post(
            f"/v1/spines/{spine_id}/cascade/execute",
            {
                "proposal_id": preview["proposal_id"],
                "spine_version": spine["spine_version"],
                "items": [
                    {"item_id": item_id, "selected": on}
                    for item_id, on in chosen.items()
                ],
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
                record_spend(
                    desk,
                    episode=episode,
                    usd=float(STILL_USD),
                    unit=f"cascade:{item.get('item_id')}",
                )
            else:
                print(
                    f"  !! {item.get('item_id')} ran on the server and is not priced here; book it by hand",
                    file=out,
                )
    marked = load_production(desk)
    for stale in answer.get("stale_storyboard_sets") or []:
        if isinstance(stale, Mapping):
            key = f"ep{int(stale.get('episode_ordinal') or episode):02d}-t{stale.get('set_index')}"
            if key not in marked.boards_stale:
                marked.boards_stale.append(key)
            print(
                f"  board t{stale.get('set_index')} of ep{int(stale.get('episode_ordinal') or episode):02d} no longer "
                f"matches the story: `redraw-board --episode {stale.get('episode_ordinal') or episode} "
                f"--take t{stale.get('set_index')} --cause '...'`",
                file=out,
            )
    save_production(desk, marked)
    if not nudges:
        # Execute carries the same introduced warnings; said here only when the preview had none.
        notes = say_warnings(authoring_warnings(answer), spine=spine, out=out)
    return notes


# --- Look -------------------------------------------------------------------------------------------


def _pin_look(desk: Path, url: str, out: Any) -> Path:
    if not url.startswith("https://"):
        raise CommandStopped(
            "--url must be a public https URL of one frame (this kit uploads nothing)"
        )
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        answer = run.post(
            f"/v1/spines/{state.spine_id}/look-register",
            {"spine_version": spine["spine_version"], "url": url},
        )
        _save_desk_json(desk, "look-register", answer)
        fresh = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    path = save_spine_snapshot(desk, state.episode_ordinal, fresh)
    print(f"look_register_url: {fresh.get('look_register_url') or url}", file=out)
    return path


def run_look(desk: Path, *, url: str, out: Any = None) -> Path:
    """Pin one style frame (a public https URL, one frame, not a collage) as the story's look. Spends nothing.

    The look pins onto a drafted story, so it comes after the draft and before the plates.
    Pinning is not the human's yes: unless ``series.look`` is already approved, this
    prints that the look gate is still open and how to record it
    (``approve --gate look``, which pins and records together).

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
    desk = desk.expanduser().resolve()
    path = _pin_look(desk, url, out)
    if not look_approved(desk):
        print(
            "Pinned, but the look gate is still open (pinning is not the human's yes). After the yes: "
            f"fictora-produce approve --desk {desk} --gate look --url {url}",
            file=out,
        )
    return path


def _resolve_look_choice(
    desk: Path, url: str | None, frame: Path | None, out: Any
) -> tuple[str, Path | None]:
    if frame is not None:
        frame = frame.expanduser().resolve()
        if not frame.is_file():
            raise CommandStopped(f"no such look frame: {frame}")
        if url is None:
            url = look_frame_url(desk, frame)
            if url is None:
                raise CommandStopped(
                    f"the desk does not know the stored URL of {frame.name}; pass --url <its image_url>"
                )
        return url, frame
    if url is not None:
        return url, None
    newest = newest_look_frame(desk)
    if newest is None:
        raise CommandStopped(
            "no look frame on this desk (shared/look/look-frame-vN.*); pass --url <the frame's https URL>"
        )
    known = look_frame_url(desk, newest)
    if known is None:
        raise CommandStopped(
            f"the desk does not know the stored URL of {newest.relative_to(desk)}; pass --url <its image_url>"
        )
    print(
        f"Using the newest look frame: {newest.relative_to(desk)} ({known})", file=out
    )
    return known, newest


def run_approve_look(
    desk: Path,
    *,
    url: str | None = None,
    path: Path | None = None,
    out: Any = None,
) -> GateRecord:
    """Record the human's yes on the look and pin that frame on the server. Spends nothing.

    Writes ``series.look`` (the record ``fictora-ops approve --gate look`` writes and
    preflight reads) and pins the frame with the same ``look-register`` call as
    ``look --url``. Without ``url`` or ``path`` it takes the newest
    ``shared/look/look-frame-vN`` and prints which. Idempotent: a frame the server
    already holds (``api/spine.json`` ``look_register_url``) is not pinned again, and
    a look already approved for it is left as it is (unless a frame drawn after
    that yes opened the gate again: then the yes is recorded anew and covers it).

    Parameters
    ----------
    desk
        Series desk with a story.
    url
        The frame's public https URL (a ``look-frame`` ``image_url``, or a frame the human picked).
    path
        A ``look-frame-vN`` file on the desk; its URL is read from the desk.
    out
        Text stream.

    Returns
    -------
    GateRecord
        The approved look record.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    url, frame = _resolve_look_choice(desk, url, path, out)
    if not url.startswith("https://"):
        raise CommandStopped(
            "--url must be a public https URL of one frame (this kit uploads nothing)"
        )
    shown = (
        str(frame.relative_to(desk))
        if frame and frame.is_relative_to(desk)
        else (str(frame) if frame else url)
    )
    note = f"pinned {url}"
    current = load_series(desk).look
    if pinned_look_url(desk) == url:
        if (
            current.status == "approved"
            and current.note == note
            and unapproved_look_frame(desk) is None
        ):
            print(f"look already approved and pinned: {shown}", file=out)
            return current
        print(f"look_register_url: {url} (already pinned; not sent again)", file=out)
    else:
        _pin_look(desk, url, out)
    record = approve_series_gate(desk, "look", path=shown, note=note, url=url)
    _note(desk, 1, f"look approved: {shown} ({url}).")
    print(
        f"look {record.status}: {shown}. Next: fictora-produce step --desk {desk}",
        file=out,
    )
    return record


def look_frame_route_missing(message: str) -> bool:
    """Return whether a refused POST was a 404 for the route itself (an older Drama API).

    An unknown route answers FastAPI's bare ``{"detail": "Not Found"}``; a missing
    story answers the API's error envelope (``spine_not_found``), which is not this.

    Parameters
    ----------
    message
        The session's refusal text (``HTTP 404 POST <url>: <body>``).

    Returns
    -------
    bool
        True when the server has no look-frame route.
    """

    return message.startswith("HTTP 404") and '"detail": "Not Found"' in message


def run_look_frame(
    desk: Path,
    *,
    description: str | Path,
    size: str = LOOK_FRAME_DEFAULT_SIZE,
    out: Any = None,
) -> Path:
    """Draw our own style frame on the server from a written description. Books one still; never pins.

    ``POST /v1/spines/{id}/look-frame`` draws it on the product's still model
    from the words alone (no image goes in, and the server refuses a link in
    the description) and answers our stored PNG URL. This saves it as
    ``shared/look/look-frame-vN.png`` and prints the URL; after the human's yes,
    ``approve --gate look`` pins it and records the yes. The same description and size are cached
    on the server, so re-running never pays twice.

    Parameters
    ----------
    desk
        Series desk with a story.
    description
        The written description of the look (1-4000 characters): the words, ``@file``
        or an existing file path (a ``Path`` is always read as a file).
    size
        ``1088x1936`` (default), ``1936x1088`` or ``1024x1024``.
    out
        Text stream.

    Returns
    -------
    Path
        The saved frame.
    """

    out = out or sys.stdout
    if size not in LOOK_FRAME_SIZES:
        raise CommandStopped(f"--size is one of {', '.join(LOOK_FRAME_SIZES)}")
    source = f"@{description}" if isinstance(description, Path) else description
    try:
        words = text_or_file(source, flag="--description")
    except TextArgError as exc:
        raise CommandStopped(str(exc)) from exc
    if source.startswith("@"):
        source_name = Path(source[1:]).name
    elif words != source.strip():
        source_name = Path(source).name
    else:
        source_name = "inline text"
    if not words:
        raise CommandStopped("the description is empty; write the look down first")
    description = words
    if len(description) > LOOK_FRAME_MAX_CHARS:
        raise CommandStopped(
            f"the description is {len(description)} characters; keep it to {LOOK_FRAME_MAX_CHARS}"
        )
    desk, state, run = _desk_session(desk)
    # Founder rule, 1 Oct 2026: stylised styles only. Warned, never blocked:
    # the frame is an operator's calibration drawn from words, and the plates
    # and boards stay bound to the preset (stylised_only module docstring).
    warning = photoreal_look_warning(description)
    if warning:
        print(warning, file=out)
        _note(desk, 1, f"look-frame {warning}")
    digest = hashlib.sha256(f"{size}\n{description}".encode()).hexdigest()[:24]
    try:
        try:
            answer = run.post(
                f"/v1/spines/{state.spine_id}/look-frame",
                {"description": description, "size": size},
                idempotency_key=f"{run.prefix}-look-frame-{digest}",
            )
        except SystemExit as exc:
            message = (
                exc.code if isinstance(exc.code, str) else api_error_text(exc.code)
            )
            if look_frame_route_missing(message):
                raise CommandStopped(OLD_SERVER_LOOK_FRAME) from exc
            hint = next(
                (text for code, text in LOOK_FRAME_HINTS.items() if code in message), ""
            )
            raise CommandStopped(message + (f" -> {hint}" if hint else "")) from exc
        image_url = str(answer.get("image_url") or "")
        if not image_url.startswith("https://"):
            raise CommandStopped(
                f"the look-frame answer has no image_url: {api_error_text(answer)}"
            )
        _save_desk_json(desk, "look-frame", answer)
        fetch = httpx.Client(timeout=120.0)
        try:
            path = _orchestrate.download_to_versioned(
                fetch, image_url, desk / "shared" / "look", "look-frame"
            )
        finally:
            fetch.close()
        record_look_frame_url(desk, path, image_url)
    finally:
        run.client.close()
    # ``cost_usd`` is what this call booked on the server: 0 for a cached frame.
    cost = float(answer.get("cost_usd") or 0.0)
    if cost > 0:
        record_spend(desk, episode=1, usd=cost, unit="look-frame")
    cached = (
        " (already drawn for this description; nothing booked)"
        if answer.get("cached")
        else ""
    )
    _note(
        desk,
        1,
        f"look-frame: {path.name} from {source_name}, ${cost:.2f}{cached}. {image_url}",
    )
    print(str(path), file=out)
    print(f"image_url: {image_url}", file=out)
    print(
        f"Show {path.name} to the human. If it is the look: fictora-produce approve --desk {desk} --gate look "
        f"(pins {path.name} and records the yes; step will not draw plates or boards before it). "
        "To change it, change the description and draw again.",
        file=out,
    )
    return path


def run_look_note(
    desk: Path, *, add: str | None = None, remove: str | None = None, out: Any = None
) -> list[str]:
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
        notes = [
            note for note in spine.get("look_notes") or [] if isinstance(note, Mapping)
        ]
        body = {"spine_version": spine["spine_version"]}
        if add is not None:
            text = add.strip()
            if not text or len(text) > LOOK_NOTE_MAX:
                raise CommandStopped(f"a look note is 1-{LOOK_NOTE_MAX} characters")
            if len(notes) >= MAX_LOOK_NOTES:
                raise CommandStopped(
                    f"the story already has {MAX_LOOK_NOTES} look notes (the most it keeps); remove one"
                )
            warning = photoreal_look_warning(text)
            if warning:
                print(warning, file=out)
                _note(desk, state.episode_ordinal, f"look-note {warning}")
            run.post(f"/v1/spines/{state.spine_id}/look-notes", {**body, "text": text})
        else:
            wanted = str(remove)
            ids = [str(note.get("note_id")) for note in notes]
            note_id = (
                ids[int(wanted) - 1]
                if wanted.isdigit() and 1 <= int(wanted) <= len(ids)
                else wanted
            )
            if note_id not in ids:
                raise CommandStopped(
                    f"no look note {wanted!r}; the notes are: {', '.join(ids) or 'none'}"
                )
            run.delete(f"/v1/spines/{state.spine_id}/look-notes/{note_id}", body)
        fresh = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    save_spine_snapshot(desk, state.episode_ordinal, fresh)
    listed = [
        str(note.get("text"))
        for note in fresh.get("look_notes") or []
        if isinstance(note, Mapping)
    ]
    for number, note in enumerate(fresh.get("look_notes") or [], start=1):
        print(f"{number}. {note.get('note_id')}  {note.get('text')}", file=out)
    return listed


SOUND_NOTE_MAX = 160
SOUND_NOTE_HINTS = {
    "sound_note_needs_take": (
        "pass --take tK (with --episode N), or name it in the words: '... at the end of episode 1 take 2'"
    ),
    "sound_note_scope_add_only": (
        "a drop or level note applies to every take: send it without --take/--shot/--row "
        "(for one take only, use finish --sfx-adjust)"
    ),
    "sound_note_names_no_sound": "say the sound to add: '... a soft chime as she smiles'",
    "sound_notes_limit": (
        "that scope is full (5 drop/level notes per story; 8 added sounds per take, 16 per episode): "
        "remove one first: sound-note --desk D --remove N (list them with sound-note --desk D)"
    ),
    "episode_not_found": "that episode is not on the story; check --episode",
}


def _episode_ordinal(spine: Mapping[str, Any], episode_id: str) -> int | None:
    for number, summary in enumerate(spine.get("episode_summaries") or [], start=1):
        if isinstance(summary, Mapping) and summary.get("episode_id") == episode_id:
            ordinal = summary.get("ordinal")
            return int(ordinal) if isinstance(ordinal, int) else number
    return None


def _sound_note_line(
    spine: Mapping[str, Any], number: int, note: Mapping[str, Any]
) -> str:
    scope = ""
    if note.get("take") is not None:
        ordinal = _episode_ordinal(spine, str(note.get("episode_id") or ""))
        where = f"ep{ordinal:02d}" if ordinal else str(note.get("episode_id"))
        scope = f"  [adds to {where} t{note.get('take')}" + (
            f" shot {note.get('shot')}]" if note.get("shot") is not None else "]"
        )
    else:
        scope = "  [every take: drop/level]"
    return f"{number}. {note.get('note_id')}  {note.get('text')}{scope}"


def run_sound_note(
    desk: Path,
    *,
    text: str | None = None,
    episode: int | None = None,
    take_id: str | None = None,
    shot: int | None = None,
    row: int | None = None,
    remove: str | None = None,
    out: Any = None,
) -> list[dict[str, Any]]:
    """Add, remove or list the story's sound notes (``POST/DELETE /v1/spines/{id}/sound-notes``). Spends nothing.

    A note that adds a sound ("add a dry stone crack at the end") lands on one
    take: ``--take`` (with ``--episode``) and optionally ``--shot`` or ``--row``,
    or the words ("... of episode 1 take 2"). A drop or level note ("no
    purring", "louder rain") applies to every take and takes no scope. The
    server's named refusal is printed as it said it, with the fix. The spine is
    saved on the desk again afterwards.

    Parameters
    ----------
    desk
        Series desk with a story.
    text
        The note in the creator's words.
    episode
        Episode ordinal (default the desk's current episode).
    take_id
        ``t1``, ``t2`` ...: the take an added sound lands on.
    shot
        The shot of that take.
    row
        A row of the take's board (the server resolves it to its shot).
    remove
        A note id, or its 1-based number.
    out
        Text stream.

    Returns
    -------
    list[dict[str, Any]]
        The story's sound notes after the change.

    Raises
    ------
    CommandStopped
        On a bad flag combination, or when the server refuses the note.
    """

    if text is not None and remove is not None:
        raise CommandStopped("pass a note or --remove, not both")
    if (shot is not None or row is not None) and take_id is None:
        raise CommandStopped(
            "--shot and --row name a place inside a take: pass --take tK too"
        )
    if shot is not None and row is not None:
        raise CommandStopped("pass --shot or --row, not both")
    if text is None and (take_id is not None or shot is not None or row is not None):
        raise CommandStopped(
            "--take, --shot and --row go with a note that adds a sound"
        )
    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    episode = episode or state.episode_ordinal
    try:
        spine = run.spine(state.spine_id or "")
        notes = [
            note for note in spine.get("sound_notes") or [] if isinstance(note, Mapping)
        ]
        body: dict[str, Any] = {"spine_version": spine["spine_version"]}
        changed = text is not None or remove is not None
        if text is not None:
            words = text.strip()
            if not words or len(words) > SOUND_NOTE_MAX:
                raise CommandStopped(f"a sound note is 1-{SOUND_NOTE_MAX} characters")
            body["text"] = words
            if take_id is not None:
                try:
                    body["take"] = take_number(take_id)
                except ValueError as exc:
                    raise CommandStopped(str(exc)) from exc
                body["episode_id"] = episode_id_for(spine, episode)
                if shot is not None:
                    body["shot"] = shot
                if row is not None:
                    body["row"] = row
            try:
                run.post(f"/v1/spines/{state.spine_id}/sound-notes", body)
            except SystemExit as exc:
                message = (
                    exc.code if isinstance(exc.code, str) else api_error_text(exc.code)
                )
                hint = next(
                    (fix for code, fix in SOUND_NOTE_HINTS.items() if code in message),
                    "",
                )
                raise CommandStopped(
                    "the server refused the sound note, nothing was saved: "
                    + message
                    + (f" -> {hint}" if hint else "")
                ) from exc
        elif remove is not None:
            ids = [str(note.get("note_id")) for note in notes]
            wanted = str(remove)
            note_id = (
                ids[int(wanted) - 1]
                if wanted.isdigit() and 1 <= int(wanted) <= len(ids)
                else wanted
            )
            if note_id not in ids:
                raise CommandStopped(
                    f"no sound note {wanted!r}; the notes are: {', '.join(ids) or 'none'}"
                )
            run.delete(f"/v1/spines/{state.spine_id}/sound-notes/{note_id}", body)
        fresh = run.spine(state.spine_id or "") if changed else spine
    finally:
        run.client.close()
    save_spine_snapshot(desk, episode, fresh)
    listed = [
        dict(note)
        for note in fresh.get("sound_notes") or []
        if isinstance(note, Mapping)
    ]
    for number, note in enumerate(listed, start=1):
        print(_sound_note_line(fresh, number, note), file=out)
    if not listed:
        print("no sound notes on this story", file=out)
    if text is not None and listed:
        new = listed[-1]
        if new.get("take") is not None:
            ordinal = (
                _episode_ordinal(fresh, str(new.get("episode_id") or "")) or episode
            )
            take = f"t{new.get('take')}"
            print(
                f"Saved. A take filmed from now on carries it. An ep{ordinal:02d} {take} already on the desk keeps "
                f"its saved facts: run `fictora-produce take-facts --desk {desk} --episode {ordinal} --take {take} "
                f"--refresh`, then finish it again.",
                file=out,
            )
        else:
            print(
                "Saved. It drops or levels a sound on every take. A take filmed from now on carries it. A take "
                f"already on the desk keeps its saved facts: run `fictora-produce take-facts --desk {desk} "
                "--episode N --take tK --refresh` for it (it prints the level changes and drops), then finish "
                "it again.",
                file=out,
            )
    if changed:
        what = f"added '{text.strip()}'" if text is not None else f"removed {remove}"
        _note(
            desk,
            episode,
            f"sound-note: {what}; {len(listed)} sound note(s) on the story",
        )
    return listed


INNER_VOICE_IN_FINISH = (
    "Heard in `finish`: the take its start falls in (take N starts where the raw takes before it end, "
    "{at:g}s on the episode) makes it dry in {name}'s locked voice on the server (about $0.10 per 1,000 "
    "characters, once: a re-run reuses the line on the desk), lays it at the cue and captions it in Georgia "
    "italic: `fictora-produce finish --desk {desk} --episode {episode} --take tK`. {name} needs a locked voice "
    "(`voice --audition`, then `--pick N`); without one, finish names the cue and the take is NOT DONE."
)


def run_inner_voice(
    desk: Path,
    *,
    episode: int,
    cast: str | None = None,
    text: str | None = None,
    at: float | None = None,
    until: float | None = None,
    remove: str | None = None,
    clear: bool = False,
    spoken_text: str | None = None,
    out: Any = None,
) -> list[dict[str, Any]]:
    """Add, remove, clear or list an episode's inner-voice cues (a character's own thoughts). Spends nothing.

    ``PUT /v1/spines/{id}/episodes/{n}/inner-voice`` replaces the episode's whole
    cue list (:mod:`creation.inner_voice`), so the command reads the story, changes
    one cue, sends the list back and saves the story on the desk again. A thought
    goes on the character who thinks it (someone already in the cast) and costs
    no cast place, unlike ``line --new-voice``. The server's refusal is printed as
    it said it, with plain words (:func:`creation.inner_voice.refusal_words`).

    Parameters
    ----------
    desk
        Series desk with a story.
    episode
        Episode ordinal.
    cast
        Who thinks it: a cast name or id (with ``text`` and ``at``).
    text
        The thought as captioned (the cue's ``line`` on the server).
    at, until
        Seconds on the episode as filmed (take 1 starts at 0); ``until`` defaults from the word count.
    spoken_text
        With ``text``: the words the voice says when they differ from the caption
        (a Japanese thought under an English caption). Kept on the desk
        (:func:`creation.inner_voice.save_spoken`): the server's cue has no field for it.
    remove
        A cue id, or its number in the listing.
    clear
        Remove every cue of the episode.
    out
        Text stream.

    Returns
    -------
    list[dict[str, Any]]
        The episode's cues after the command.

    Raises
    ------
    CommandStopped
        On a bad flag combination (nothing sent), or when the server refuses the cues.
    """

    adding = any(value is not None for value in (cast, text, at, until))
    spoken = " ".join((spoken_text or "").split()) or None
    if spoken_text is not None and not adding:
        raise CommandStopped(
            "--spoken-text goes with a new thought (--cast, --text, --at): the words the voice says, "
            "with --text as the caption"
        )
    if spoken_text is not None and not spoken:
        raise CommandStopped(
            '--spoken-text is empty: --spoken-text "..." (the words the voice says)'
        )
    if sum((adding, remove is not None, clear)) > 1:
        raise CommandStopped(
            "pass one of: a thought (--cast, --text, --at), --remove, or --clear"
        )
    if adding and (cast is None or text is None or at is None):
        raise CommandStopped(
            'a thought needs --cast NAME (who thinks it), --text "..." and --at S (seconds on the episode)'
        )
    out = out or sys.stdout
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        names = _cast_names(spine)
        cues = inner_voice.episode_cues(spine, episode=episode)
        added: dict[str, Any] | None = None
        what = ""
        dropped: list[str] = []
        if adding:
            wanted = str(cast).strip().lower()
            speaker = next(
                (
                    cid
                    for cid, name in names.items()
                    if wanted in {cid.lower(), name.lower()}
                ),
                None,
            )
            if speaker is None:
                listed = ", ".join(f"{n} ({cid})" for cid, n in names.items()) or "none"
                raise CommandStopped(
                    f"no cast member {cast!r} on this story; the cast is: {listed}. A thought goes on the "
                    "character who thinks it, someone already in the cast"
                )
            try:
                new, added = inner_voice.add_cue(
                    cues,
                    episode=episode,
                    speaker_cast_id=speaker,
                    text=str(text),
                    at=float(at or 0),
                    until=until,
                )
            except inner_voice.InnerVoiceError as exc:
                raise CommandStopped(str(exc)) from None
            what = (
                f"added {added['cue_id']} ({names.get(speaker, speaker)} thinks: {_short(added['line'])})"
                + (f", says {_short(spoken)}" if spoken else "")
            )
        elif remove is not None:
            try:
                new, gone = inner_voice.remove_cue(cues, str(remove))
            except inner_voice.InnerVoiceError as exc:
                raise CommandStopped(str(exc)) from None
            what = f"removed {gone['cue_id']} ({_short(gone.get('line') or '')})"
            dropped = [str(gone["cue_id"])]
        elif clear:
            if not cues:
                raise CommandStopped(
                    f"episode {episode} has no inner-voice cues to clear"
                )
            new = []
            what = f"cleared {len(cues)} cue(s)"
            dropped = [str(cue.get("cue_id")) for cue in cues]
        else:
            print(
                f"ep{episode:02d} inner voice (a character's own thoughts; --remove takes the number or id):",
                file=out,
            )
            spoken_saved = inner_voice.load_spoken(desk, episode)
            for row in inner_voice.cue_listing(cues, names, spoken_saved) or [
                "  (none)"
            ]:
                print(row, file=out)
            return cues
        approved_before = spine.get("approval_state") == "approved"
        body = inner_voice.request_body(spine, episode=episode, cues=new)
        try:
            run.put(f"/v1/spines/{state.spine_id}/episodes/{episode}/inner-voice", body)
        except SystemExit as exc:
            message = (
                exc.code if isinstance(exc.code, str) else api_error_text(exc.code)
            )
            raise CommandStopped(
                f"{message}\n  {inner_voice.refusal_words(message)}"
            ) from None
        fresh = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    save_spine_snapshot(desk, episode, fresh)
    kept = inner_voice.episode_cues(fresh, episode=episode)
    if dropped:
        inner_voice.drop_spoken(desk, episode, dropped)
    if added is not None and spoken:
        saved_at = inner_voice.save_spoken(
            desk,
            episode,
            cue_id=str(added["cue_id"]),
            line=str(added["line"]),
            spoken_text=spoken,
        )
        print(
            f"  spoken words kept on the desk (`{saved_at.relative_to(desk)}`): the voice says them, "
            "the caption is --text",
            file=out,
        )
    fresh_names = _cast_names(fresh)
    print(f"ep{episode:02d} inner voice: {what}", file=out)
    spoken_saved = inner_voice.load_spoken(desk, episode)
    for row in inner_voice.cue_listing(kept, fresh_names, spoken_saved) or ["  (none)"]:
        print(row, file=out)
    if added is not None and not is_english(str(added["line"])):
        print(
            f"!! {added['cue_id']}: the caption is not English, so finish leaves it uncaptioned (NOT ENGLISH). "
            f'Remove it and add it again with --text "<English caption>" --spoken-text "<the words said>".',
            file=out,
        )
    if added is not None and not any(c.get("cue_id") == added["cue_id"] for c in kept):
        print(
            f"!! the server answered but its story does not list {added['cue_id']}: it may not be saved. "
            "Run `inner-voice` again to list the cues before going on.",
            file=out,
        )
    _say_inner_voice_approval(
        desk,
        before=approved_before,
        after=fresh.get("approval_state") == "approved",
        out=out,
    )
    if added is not None:
        _say_new_thought(
            desk,
            fresh,
            episode=episode,
            added=added,
            kept=kept,
            timed=until is not None,
            out=out,
        )
    _note(
        desk, episode, f"inner-voice: {what}; {len(kept)} cue(s) on episode {episode}"
    )
    return kept


def _say_inner_voice_approval(
    desk: Path, *, before: bool, after: bool, out: Any
) -> None:
    if before and not after:
        print(
            "!! script approval: the server no longer has the script approved. Show the human the lines and "
            f"thoughts, then `fictora-produce approve --desk {desk} --gate script`.",
            file=out,
        )
    elif after:
        print(
            "script approval: unchanged (the server keeps the script approved). Thoughts are not spoken lines: "
            "the desk's lines and its script yes stand.",
            file=out,
        )
    else:
        print(
            "script approval: not given yet; the thoughts are saved beside the lines: show the human both at "
            "the script gate.",
            file=out,
        )


def _say_new_thought(
    desk: Path,
    spine: Mapping[str, Any],
    *,
    episode: int,
    added: Mapping[str, Any],
    kept: list[dict[str, Any]],
    timed: bool,
    out: Any,
) -> None:
    names = _cast_names(spine)
    who = str(added["speaker_cast_id"])
    if not timed:
        print(
            f"  (no --until: {(added['end_ms'] - added['start_ms']) / 1000:.2f}s from the word count; "
            "pass --until S to set the end)",
            file=out,
        )
    clash = inner_voice.overlaps(kept, added)
    if clash:
        print(
            f"!! {added['cue_id']} overlaps {', '.join(clash)} in time (warning only).",
            file=out,
        )
    takes = len(episode_by_ordinal(load_series(desk), episode).takes)
    seconds = load_production_config(desk).clip_duration_seconds * max(1, takes)
    if added["start_ms"] >= seconds * 1000:
        print(
            f"!! --at {added['start_ms'] / 1000:g}s is past the episode's {seconds}s (warning only). "
            "Times count from the start of take 1.",
            file=out,
        )
    card = next(
        (
            c
            for c in spine.get("cast") or []
            if isinstance(c, Mapping) and c.get("cast_id") == who
        ),
        {},
    )
    if card.get("voice_only") is True:
        print(
            f"  {names.get(who, who)} is only heard, never seen. That fits a narrator; a character's own "
            "thoughts go on the character we watch.",
            file=out,
        )
    print(
        INNER_VOICE_IN_FINISH.format(
            name=names.get(who, who),
            desk=desk,
            episode=episode,
            at=added["start_ms"] / 1000,
        ),
        file=out,
    )


def _soundtrack_rows(
    facts: dict[str, Any] | None, cast_names: dict[str, str]
) -> list[str]:
    """The take's soundtrack and its line windows, when the server sent one (nothing from an older server)."""

    from creation.post.soundtrack import soundtrack_from, soundtrack_lines

    if not soundtrack_from(facts).sent:
        return []
    return soundtrack_lines(facts, cast_names)


def run_take_facts(
    desk: Path, *, episode: int, take_id: str, refresh: bool = False, out: Any = None
) -> Path:
    """Show a take's saved SFX plan, or (``refresh``) fetch its current facts and say what moved. Spends nothing.

    The facts are saved at filming time. A sound note added after that (or an
    impact the server now plans) reaches the take only through a fresh read:
    ``--refresh`` reads ``GET /v1/jobs/{take_job}/take-facts?spine_id=`` again
    against the current story, saves it as the next
    ``epNN/api/take-facts-epNN-tK-vN.json`` (the old file is kept) and prints
    the SFX cues it adds (``+``), the cues whose level a sound note moved
    (``~``, with the note ids) and the cues no longer planned (``-``, with the
    note that dropped them). ``finish`` then lays the new plan.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1`` ...
    refresh
        Fetch the facts again (else show the newest saved file).
    out
        Text stream.

    Returns
    -------
    Path
        The facts file shown or written.

    Raises
    ------
    CommandStopped
        When the desk has no job for the take, or the server refuses.
    """

    from creation.post.desk import saved_spine, take_job_id
    from creation.post.sfx import saved_take_facts

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    try:
        take_number(take_id)
    except ValueError as exc:
        raise CommandStopped(str(exc)) from exc
    old_path = saved_take_facts(desk, episode, take_id)
    old = json.loads(old_path.read_text(encoding="utf-8")) if old_path else None
    label = f"ep{episode:02d} {take_id}"
    if not refresh:
        if old_path is None or old is None:
            raise CommandStopped(
                f"{label}: no take facts on the desk; pass --refresh to fetch them"
            )
        found = saved_spine(desk, episode)
        stale = stale_facts_reason(
            old, found[0] if found else None, episode=episode, take_id=take_id
        )
        print(f"{label}: {old_path.name}", file=out)
        for line in sfx_plan_lines(old):
            print(f"  {line}", file=out)
        for line in shot_people_lines(
            old, cast_names_from(found[0] if found else None)
        ):
            print(f"  {line}", file=out)
        for line in _soundtrack_rows(old, cast_names_from(found[0] if found else None)):
            print(f"  {line}", file=out)
        if stale:
            print(
                f"!! older than the story's sound notes: {stale}. Run take-facts --desk {desk} --episode {episode} "
                f"--take {take_id} --refresh",
                file=out,
            )
        return old_path
    job = take_job_id(desk, episode, take_id)
    if job is None:
        raise CommandStopped(
            f"{label}: api/ names no job for this take in its clip records "
            f"({STEP_RAW_CLIPS}, film-*-raw-scene-clips.json); film it first"
        )
    desk, state, run = _desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        query = f"?spine_id={quote(state.spine_id or '', safe='')}"
        status, body = run.get_optional(f"/v1/jobs/{job}/take-facts{query}")
    finally:
        run.client.close()
    if not (
        200 <= status < 300
        and isinstance(body, dict)
        and isinstance(body.get("take_facts"), dict)
    ):
        raise CommandStopped(
            f"{label}: the server gave no take facts for job {job} (HTTP {status}: {api_error_text(body)}); "
            "the saved facts are unchanged"
        )
    save_spine_snapshot(desk, episode, spine)
    facts = dict(body["take_facts"])
    path = save_take_facts(
        desk, episode=episode, take_id=take_id, facts=facts, spine=spine
    )
    changes = sfx_plan_changes(old, facts)
    was = f" (was {old_path.name})" if old_path else " (none saved before)"
    print(f"{label}: saved {path.name}{was}", file=out)
    for line in shot_people_lines(facts, cast_names_from(spine)):
        print(f"  {line}", file=out)
    for line in _soundtrack_rows(facts, cast_names_from(spine)):
        print(f"  {line}", file=out)
    if changes:
        print("SFX plan changes:", file=out)
        for line in changes:
            print(f"  {line}", file=out)
        print(
            f"finish --desk {desk} --episode {episode} --take {take_id} lays the new plan (a new version; "
            "cues already rendered are reused)",
            file=out,
        )
    else:
        cues = len(facts.get("sfx_cues") or [])
        print(
            f"SFX plan unchanged ({cues} cue(s)); nothing to finish again for sound",
            file=out,
        )
    _note(
        desk,
        episode,
        f"take-facts {label}: refreshed -> `{path.name}`{was}; {len(changes)} SFX plan change(s)"
        + "".join(f"\n- {line}" for line in changes),
    )
    return path


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
        before = (
            json.loads(before_path.read_text(encoding="utf-8")).get("spine_version")
            if before_path.is_file()
            else None
        )
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


def board_changes(
    state: ProductionState,
    spine: Mapping[str, Any],
    *,
    episode: int,
    take_id: str,
    take_count: int,
) -> list[str] | None:
    """Say what changed in one board's drawing since it was last drawn.

    Compares the digests recorded when the board was drawn
    (``state.board_inputs``: frames, beats, look notes, plates) with the story
    now, and counts a board the server marked stale after a cascade. A desk drawn
    before the kit recorded all four has only the frame digest
    (``state.board_digests``); that one is compared alone.

    Parameters
    ----------
    state
        Production state.
    spine
        Spine JSON now.
    episode
        Episode ordinal.
    take_id
        ``t1``, ``t2`` ...
    take_count
        Takes on the desk for this episode.

    Returns
    -------
    list[str] | None
        What changed, in words (empty: nothing did, a redraw is a re-roll);
        ``None`` when the desk has no record of the last drawing.
    """

    key = f"ep{episode:02d}-{take_id}"
    set_index = int(take_id[1:])
    now = board_inputs(
        spine, episode=episode, set_index=set_index, take_count=take_count
    )
    changed: list[str] = []
    if key in state.boards_stale:
        changed.append("the server marked this board stale after a story edit")
    before = state.board_inputs.get(key)
    if before:
        changed.extend(
            BOARD_INPUT_NAMES[name]
            for name in BOARD_INPUT_NAMES
            if name in before and before[name] != now[name]
        )
        return changed
    legacy = state.board_digests.get(key)
    if legacy:
        if legacy != now["frames"]:
            changed.append(BOARD_INPUT_NAMES["frames"])
        return changed
    return changed or None


def redraw_needs_an_edit(
    desk: Path, *, episode: int, take_id: str, legacy: bool
) -> str:
    """The stop message when a redraw would draw the same board again. Nothing was sent or paid.

    Parameters
    ----------
    desk
        Series desk (named in the commands).
    episode
        Episode ordinal.
    take_id
        ``t1``, ``t2`` ...
    legacy
        The desk only recorded the frame briefs for this board (drawn before the kit kept the rest).

    Returns
    -------
    str
        The message.
    """

    where = f"--desk {desk} --episode {episode}"
    lines = [
        f"!! {take_id}: nothing this board is drawn from has changed since it was last drawn "
        f"(frame briefs, the take's beats, look notes, cast plates), so a redraw draws the same direction "
        f"again: a re-roll for ${float(STILL_USD):.2f}. Nothing was sent or paid.",
        "  --cause is a label for the desk; it never changes the drawing.",
        "  Say what is wrong and the note becomes shot edits before the redraw (the app's director path):",
        f'    fictora-produce redraw-board {where} --take {take_id} --note "what is wrong, as the camera should see it"',
        "  Or make the edits yourself, then redraw (see 'Fixing a board' in the skill):",
        f"    fictora-produce edit {where} --frame N --set FIELD=VALUE   (one board row's visual_brief)",
        f'    fictora-produce edit {where} --beat N --shot "size|subject|camera|angle"   '
        "(the server re-authors the take's frames on the redraw)",
        f'    fictora-produce look-note --desk {desk} --add "..."   (the whole story\'s look)',
        "  After the script gate, run the edit with --preview first and show the human the before/after.",
        "  Only when the frames are right and the drawing was a random miss: redraw-board ... --same-shots "
        f"(pays ${float(STILL_USD):.2f} for the same scene).",
    ]
    if legacy:
        lines.append(
            "  (This board was drawn before the desk recorded its beats, look notes and plates: only the frame "
            "briefs were compared. If a beat, look note or plate did change since, pass --same-shots.)"
        )
    return "\n".join(lines)


def _director_turn(
    run: DramaApiRunSession, spine_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """One director turn; an older service that does not know ``stage`` is asked again without it (as the app does)."""

    path = f"/v1/spines/{quote(spine_id, safe='')}/director/turns"
    try:
        return run.post(path, body)
    except SystemExit as exc:
        if "422" not in str(exc.code) or "stage" not in body:
            raise CommandStopped(
                f"the director could not read the note: {exc.code}"
            ) from None
    older = {key: value for key, value in body.items() if key != "stage"}
    try:
        return run.post(path, older)
    except SystemExit as exc:
        raise CommandStopped(
            f"the director could not read the note: {exc.code}"
        ) from None


def note_to_shot_edits(
    desk: Path,
    run: DramaApiRunSession,
    spine: dict[str, Any],
    *,
    episode: int,
    take_id: str,
    take_count: int,
    note: str,
    out: Any,
) -> dict[str, Any]:
    """Turn "what's wrong with this board" into edits of the take's beats, the way the app does. Spends nothing.

    The note goes to ``POST /v1/spines/{id}/director/turns`` (stage ``storyboard``,
    the episode in view), scoped to the take's beats. Its ``patch_story`` step is
    applied: the server already applied it before the script gate; after it,
    the beats it re-stages go through the cascade (paid items off, as ``edit``).
    The redraw that follows re-authors the take's frames from the edited beats.
    Prints, per board row, the beat before and after.

    Parameters
    ----------
    desk
        Series desk.
    run
        Session that owns the spine.
    spine
        The spine before the note.
    episode
        Episode ordinal.
    take_id
        ``t1``, ``t2`` ...
    take_count
        Takes on the desk for the episode.
    note
        What is wrong with the board.
    out
        Text stream.

    Returns
    -------
    dict[str, Any]
        The spine after the edit.

    Raises
    ------
    CommandStopped
        When the director edited none of the take's beats (nothing is drawn or paid).
    """

    set_index = int(take_id[1:])
    before = board_take_beats(
        spine, episode=episode, set_index=set_index, take_count=take_count
    )
    if not before:
        raise CommandStopped(
            f"{take_id} of ep{episode:02d} has no beats on the server to change; nothing was drawn or paid"
        )
    spine_id = str(spine.get("spine_id") or "")
    body: dict[str, Any] = {
        "spine_version": spine["spine_version"],
        "message": director_message(
            take_id=take_id, episode=episode, beats=before, note=note
        ),
        "episode_id": episode_id_for(spine, episode),
        "stage": DIRECTOR_STAGE,
    }
    print(
        f"[board] {take_id}: asking the director to turn the note into shot edits ...",
        file=out,
    )
    turn = _director_turn(run, spine_id, body)
    _save_desk_json(desk, f"board-note-ep{episode:02d}-{take_id}-turn", turn)
    for step in turn.get("steps") or []:
        if isinstance(step, Mapping):
            print(
                f"  director: [{step.get('state')}] {_short(step.get('summary') or step.get('tool'))}",
                file=out,
            )
    patch, dropped = take_patch(
        turn, allowed_beat_ids={str(beat.get("beat_id")) for _, beat in before}
    )
    for what in dropped:
        print(f"  left out: {what} (the note is about {take_id}'s drawing)", file=out)
    if patch:
        fresh = run.spine(spine_id)
        cascade = fresh.get("approval_state") == "approved"
        try:
            if not cascade:
                try:
                    answer = run.patch(
                        f"/v1/spines/{spine_id}",
                        {"spine_version": fresh["spine_version"], "patch": patch},
                    )
                    say_patch_warnings(fresh, answer, out=out)
                except SystemExit as exc:
                    if "cascade_required" not in str(exc.code):
                        raise CommandStopped(str(exc.code)) from None
                    cascade = True
            if cascade:
                _run_cascade(
                    desk,
                    run,
                    fresh,
                    patch,
                    episode=episode,
                    select_regen=False,
                    preview_only=False,
                    out=out,
                )
        except CommandStopped as exc:
            raise CommandStopped(
                explain_refusal(str(exc), fresh, episode=episode)
                + "\n  Nothing was drawn or paid."
            ) from None
    after = run.spine(spine_id)
    save_spine_snapshot(desk, episode, after)
    changes = beat_change_lines(before, after)
    if not changes:
        raise CommandStopped(
            NOTE_CHANGED_NOTHING.format(
                take=take_id, reply=_short(turn.get("reply") or ""), episode=episode
            )
        )
    print(
        f"{take_id} shot changes from the note (the redraw re-authors these rows' frames):",
        file=out,
    )
    for line in changes:
        print(line, file=out)
    _note(
        desk,
        episode,
        f"board note {take_id}: {note.strip()} -> "
        + "; ".join(line.strip() for line in changes),
    )
    return after


def restaging_warning(
    desk: Path, frames: Sequence[Mapping[str, Any]], *, episode: int, take_id: str
) -> list[str]:
    """Before a paid redraw that re-authors the take's frames: say staging may change, and list the rows now.

    A note (on the redraw, or turned into beat edits) or a beat edit makes the server re-author the
    take's frames when it draws: who is in a row, its size and its role can change, and the server has
    no preview, so the kit sees the new rows only after paying. The rows as they stand are printed so
    the after-draw diff can be read against them, with the free, exact alternative for a staging fix.

    Parameters
    ----------
    desk
        Series desk (for the command to paste).
    frames
        The take's frames as they stand before the redraw.
    episode
        Episode ordinal.
    take_id
        ``t1``, ``t2`` ...

    Returns
    -------
    list[str]
        Printable lines.
    """

    lines = [
        f"!! STAGING MAY CHANGE: the server re-authors {take_id}'s frames when it draws (paid). Who is in a "
        "row (cast_refs, subject_blocking), its size (shot_scale) and its role (cell_role) can change, and "
        "the server has no preview of the new rows: the kit shows them only after the draw.",
        f"  For a fix about one row's who, where or size, edit the frame instead (free, exact): "
        f"`fictora-produce edit --desk {desk} --episode {episode} --frame N --set FIELD=VALUE`, "
        f"then `redraw-board --take {take_id} --cause '...'` draws it as written.",
    ]
    rows = shot_rows(list(frames))
    if rows:
        lines.append(f"{take_id} rows now (compare after the redraw):")
        lines += [f"  {row.one_line()}" for row in rows]
    return lines


def run_redraw_board(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    cause: str | None = None,
    note: str | None = None,
    reroll: bool = False,
    out: Any = None,
) -> Path | None:
    """Redraw one board on ``POST /v1/spines/{id}/episodes/{n}/boards/{set}/regenerate``. Spends one still.

    With ``note`` (what is wrong with the board) the note is first turned into
    edits of the take's beats (:func:`note_to_shot_edits`: the app's director
    path; on a deploy whose regenerate route takes ``note`` it rides on the
    redraw instead, where the server re-authors the take's frames from it and
    draws the board with it as a correction). When ``/openapi.json`` cannot be
    read the note is tried on the redraw, and a server that refuses the field
    (422 naming ``note``) gets it as shot edits instead, under a fresh key. The
    changes are printed per row before anything is drawn; the rows that
    changed in the redraw are printed after it. A note counts as a change: no
    ``--reroll`` is needed with one.

    Stops before anything is paid when nothing the board is drawn from changed
    since it was last drawn (:func:`board_changes`: frame briefs, the take's
    beats, look notes, cast plates, or a server stale mark): that redraw would
    draw the same direction again. The operator turns the human's note into
    edits first, or passes ``reroll`` for a plain re-roll of a random bad draw.
    The server carries a beat edit into the redraw itself (it re-authors the
    take's frames first). Prints the redrawn board's shot list and safe-zone
    check, and sends the desk back to the board gate. Refused before anything
    is sent while a drawn look frame awaits its yes (as ``step`` is).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1``, ``t2`` ...
    cause
        Why it is redrawn: a label on the desk. Defaults to the note.
    note
        What the human said is wrong, in their words: turned into shot edits
        before the redraw, and kept in the run notes and on the take in ``series.json``.
    reroll
        Redraw even though nothing changed (``--same-shots`` / ``--reroll``: a plain re-roll of the same frames).
    out
        Text stream.

    On a deploy that takes the note, the server may decide not to draw
    (``output.board_redraw_note.drawn`` false: the note needed detail, changed
    nothing, failed, or could not be saved). Then nothing was charged: the
    still is not booked, the board file is not replaced, the desk stays at its
    gate, and the reason (with the server's question to put to the human when
    the note needs detail) is printed. When the note changed a beat's shot plan
    the spine copy on the desk is saved again either way.

    Returns
    -------
    Path | None
        The new board file; ``None`` when the server drew nothing.
    """

    out = out or sys.stdout
    if note is not None and not note.strip():
        raise CommandStopped("--note is empty: say what is wrong, or leave it out")
    note = note.strip() if note is not None else None
    if note is not None:
        try:
            check_note_length(note, limit=BOARD_NOTE_MAX, command="redraw-board")
        except ValueError as exc:
            raise CommandStopped(str(exc)) from None
    cause = (cause or "").strip() or (note or "")
    if not cause:
        raise CommandStopped(
            "--cause is required (a label for the desk) unless --note says what is wrong with the board"
        )
    if not (take_id.startswith("t") and take_id[1:].isdigit()):
        raise CommandStopped("--take is t1, t2 ...")
    set_index = int(take_id[1:])
    _hold_for_look(desk, "redraw-board")
    desk, state, run = _desk_session(desk)
    cfg = load_production_config(desk)
    slot = episode_by_ordinal(load_series(desk), episode)
    if take_id not in {take.take_id for take in slot.takes}:
        raise CommandStopped(f"{take_id} is not a take on ep{episode:02d}")
    unit = f"boards-ep{episode:02d}-{take_id}-redraw"
    resuming = bool((state.pending.get(unit) or {}).get("job_id"))
    if note is None:
        print(f"[board] {REDRAW_CAUSE_IS_A_LABEL}.", file=sys.stderr)
    extra: dict[str, Any] = {"episode_count": episode}
    note_on_redraw = False
    try:
        spine = run.spine(state.spine_id or "")
        drawn_before = copy.deepcopy(
            frames_by_set(spine, episode=episode).get(set_index, [])
        )
        key = f"ep{episode:02d}-{take_id}"
        if resuming:
            print(
                f"[board] {take_id}: a redraw is already under way; picking it up (a note was applied before it "
                "and is not sent again).",
                file=out,
            )
        elif note is not None:
            status, openapi = run.get_optional("/openapi.json")
            support = regenerate_note_support(status, openapi)
            if support is not False:
                extra["note"] = note
                note_on_redraw = True
                print(
                    f"[board] {take_id}: the server takes the note on the redraw and turns it into shot edits."
                    if support
                    else f"[board] {take_id}: the server's schema could not be read; sending the note on the redraw "
                    "(if the server refuses it, the note becomes shot edits first).",
                    file=out,
                )
            else:
                spine = note_to_shot_edits(
                    desk,
                    run,
                    spine,
                    episode=episode,
                    take_id=take_id,
                    take_count=len(slot.takes),
                    note=note,
                    out=out,
                )
                state = load_production(desk)
        changed = board_changes(
            state, spine, episode=episode, take_id=take_id, take_count=len(slot.takes)
        )
        if changed == [] and (note_on_redraw or (note is not None and not resuming)):
            # The note's edits are what changed (a legacy desk compares frame briefs only,
            # and the server re-authors the frames from the edited beats on the redraw).
            changed = ["the shots, from the note"]
        if changed == [] and not reroll and not resuming:
            raise CommandStopped(
                redraw_needs_an_edit(
                    desk,
                    episode=episode,
                    take_id=take_id,
                    legacy=key not in state.board_inputs,
                )
            )
        if changed == []:
            print(
                f"--reroll: {take_id} is drawn again from the same frames, look and plates "
                f"(a re-roll, ${float(STILL_USD):.2f})",
                file=out,
            )
        elif changed is None:
            print(
                f"{take_id}: the desk has no record of what this board was drawn from; redrawing as asked",
                file=out,
            )
        else:
            print(f"{take_id} redraws with changed: {'; '.join(changed)}", file=out)
        reauthors = (note is not None and not resuming) or BOARD_INPUT_NAMES[
            "beats"
        ] in (changed or [])
        names = (
            None
            if resuming
            else named_cast_stop(
                spine,
                episode=episode,
                desk=str(desk),
                sets=[set_index],
                action=f"redrawing {take_id}",
            )
        )
        if names and not reauthors:
            # The board gate's name check, before the paid redraw: the frames are drawn as written.
            raise CommandStopped(names)
        if names:
            print(
                f"note: the server re-authors {take_id}'s frames on this redraw; these frames name one "
                "character but list another as they stand, so check the names on the redrawn board:\n"
                + "\n".join(line for line in names.splitlines()[1:] if "!!" in line),
                file=out,
            )
        if reauthors:
            for line in restaging_warning(
                desk, drawn_before, episode=episode, take_id=take_id
            ):
                print(line, file=out)
        body = reuse_generation_body(
            prompt=scene_prompt(spine, state.prompt),
            spine=spine,
            preset_id=state.preset_id,
            preset_version=state.preset_version,
            video_lane=state.video_lane,
            cut_tempo=cfg.cut_tempo,
            extra=extra,
        )
        regenerate_path = f"/v1/spines/{state.spine_id}/episodes/{episode}/boards/{set_index}/regenerate"
        try:
            terminal = run_unit(
                desk,
                run,
                unit=unit,
                path=regenerate_path,
                body=body,
                video_route=True,
                deadline_seconds=cfg.poll_boards_deadline_seconds,
            )
        except SystemExit as exc:
            if not (note_on_redraw and note_refused_by_server(exc.code)):
                raise
            # An older server: the refused body never ran. Turn the note into shot
            # edits the way the app's director does, then redraw under a fresh key.
            print(
                f"[board] {take_id}: this server does not take a note on a redraw yet; the note becomes "
                "shot edits first (the note is kept on the desk either way).",
                file=out,
            )
            _forget_unit(desk, unit)
            extra.pop("note", None)
            note_on_redraw = False
            spine = note_to_shot_edits(
                desk,
                run,
                spine,
                episode=episode,
                take_id=take_id,
                take_count=len(slot.takes),
                note=note or "",
                out=out,
            )
            state = load_production(desk)
            body = reuse_generation_body(
                prompt=scene_prompt(spine, state.prompt),
                spine=spine,
                preset_id=state.preset_id,
                preset_version=state.preset_version,
                video_lane=state.video_lane,
                cut_tempo=cfg.cut_tempo,
                extra=extra,
            )
            terminal = run_unit(
                desk,
                run,
                unit=unit,
                path=regenerate_path,
                body=body,
                video_route=True,
                deadline_seconds=cfg.poll_boards_deadline_seconds,
            )
        _save_desk_json(
            desk, f"boards-redraw-ep{episode:02d}-{take_id}-terminal", terminal
        )
        outcome = redraw_note_outcome(terminal)
        spine = run.spine(state.spine_id or "")
        if outcome is not None and not outcome.drawn:
            # Nothing drawn, nothing charged: keep the board file, the ledger and the gate as they are.
            if outcome.updated_shot_plan_beat_ids:
                save_spine_snapshot(desk, episode, spine)
            for line in outcome_lines(
                outcome, desk=str(desk), episode=episode, take_id=take_id
            ):
                print(line, file=out)
            _note(
                desk,
                episode,
                f"board redraw {take_id}: NOT drawn, $0.00 charged ({outcome.reason_code or 'no reason given'}). "
                f"Note: {note or cause}",
            )
            return None
        save_spine_snapshot(desk, episode, spine)
        state = load_production(desk)
        made = download_boards(desk, state, spine, episode=episode, sets=[set_index])
        if not made:
            raise CommandStopped(
                f"the redraw completed but the spine has no current board for {take_id}"
            )
        report = board_report(
            desk,
            run,
            state,
            spine,
            episode=episode,
            made=made,
            redraw=True,
            clip_seconds=cfg.clip_duration_seconds,
        )
    finally:
        run.client.close()
    if outcome is not None:
        report[:0] = outcome_lines(
            outcome, desk=str(desk), episode=episode, take_id=take_id
        )
    moved = row_change_lines(
        drawn_before, frames_by_set(spine, episode=episode).get(set_index, [])
    )
    if moved:
        report.append(f"{take_id} rows that changed in this redraw:")
        report += moved
    elif note is not None and not resuming:
        report.append(
            f"!! {take_id}: the redrawn board's rows read the same as before the note (size, angle, viewpoint, "
            "camera); look at the board before saying yes."
        )
    if state.episode_ordinal == episode and state.phase in {
        "wait_board",
        "ready_estimate",
        "wait_spend",
    }:
        if state.phase != "wait_board":
            report.append(
                "The board changed after its yes: the desk is back at the board gate (approve it again)."
            )
        state.phase = "wait_board"
    save_production(desk, state)
    for line in report:
        print(line, file=out)
    _record_redraw(
        desk,
        episode=episode,
        take_id=take_id,
        board=made[0][1].name,
        cause=cause,
        note=note,
        reroll=changed == [],
        changed=changed or [],
    )
    return made[0][1]


def _forget_unit(desk: Path, unit: str) -> None:
    """Drop a unit's recorded key after the server refused its body, so the next POST gets a fresh key."""

    state = load_production(desk)
    state.pending.pop(unit, None)
    state.attempts[unit] = state.attempts.get(unit, 0) + 1
    save_production(desk, state)


def _record_redraw(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    board: str,
    cause: str,
    note: str | None,
    reroll: bool,
    changed: Sequence[str],
) -> None:
    """Keep one redraw on the take in ``series.json`` (``extra.redraws``) and in the run notes. Labels only."""

    series = load_series(desk)
    take = next(
        t for t in episode_by_ordinal(series, episode).takes if t.take_id == take_id
    )
    entry: dict[str, Any] = {
        "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "board": board,
        "cause": cause,
        "reroll": reroll,
        "changed": list(changed),
    }
    if note is not None:
        entry["note"] = note.strip()
    take.extra.setdefault("redraws", []).append(entry)
    save_series(desk, series)
    what = (
        "re-roll, same frames"
        if reroll
        else ("; ".join(changed) or "no record of the last drawing")
    )
    _note(
        desk,
        episode,
        f"board redraw {take_id}: {board}, ${float(STILL_USD):.2f} ({what}). Cause (label only): {cause}"
        + (
            f". Note (turned into shot edits): {note.strip()}"
            if note is not None
            else ""
        ),
    )


# --- Plate redraw ------------------------------------------------------------------------------------


def plates_cast_retired(desk: Path | None = None, cast: str | None = None) -> str:
    """The pointer ``plates --cast NAME --cause`` prints now that it is retired (founder decision, 2026-09-28).

    One way to redraw one character: ``redraw-plate --note`` records the correction on
    that character and redraws their plate alone. ``plates --cause`` could only re-roll
    the same drawing (the route takes no notes), so it is gone; the old command name is
    kept so muscle memory lands on this line and nothing is sent or paid.
    """

    where = f"--desk {desk}" if desk else "--desk D"
    who = f'--cast "{cast}"' if cast else "--cast NAME"
    return (
        "`plates --cast NAME --cause` is retired; nothing was sent or paid. To redraw one character, say what "
        f'to change: fictora-produce redraw-plate {where} {who} --note "what to change, as shapes" '
        "(records the note on that character, then redraws their plate alone, $0.30)."
    )


def _refuse_voice_only(card: Mapping[str, Any]) -> None:
    """Stop before anything is sent when ``card`` is a voice-only character (heard, never drawn; no plate)."""

    if card.get("voice_only") is True:
        name = str(card.get("name") or card.get("cast_id"))
        raise CommandStopped(
            f"{name} is voice-only (heard, never drawn): there is no plate to redraw"
        )


# --- One plate ---------------------------------------------------------------------------------------


def _note_key(text: str) -> str:
    return " ".join(text.split()).casefold()


def _plates_home(desk: Path) -> tuple[Path, int]:
    """The folder the cast plates were drawn into (``epNN/plates``) and its episode, else ep01."""

    for folder in sorted(desk.glob("ep[0-9][0-9]/plates")):
        if any(folder.glob("plate-ep*-v*.png")):
            return folder, int(folder.parent.name[2:])
    return desk / "ep01" / "plates", 1


def _newest(folder: Path, stem: str) -> Path | None:
    found: list[tuple[int, Path]] = []
    for path in folder.glob(f"{stem}-v*.*"):
        number = path.stem.rsplit("-v", 1)[-1]
        if number.isdigit():
            found.append((int(number), path))
    return max(found)[1] if found else None


def plate_contact_sheet(
    plates: Sequence[tuple[str, Path | None]], redrawn: str, out: Path
) -> Path:
    """Draw every character's current plate side by side, the redrawn one framed and marked NEW.

    Parameters
    ----------
    plates
        ``(name, plate file or None)`` in cast order.
    redrawn
        Name of the character redrawn now.
    out
        PNG to write (a new versioned path; never overwritten).

    Returns
    -------
    Path
        ``out``.
    """

    from PIL import Image, ImageDraw

    height, label, pad = 480, 36, 12
    tiles: list[Image.Image] = []
    for name, path in plates:
        if path is None:
            art = Image.new("RGB", (270, height), (40, 40, 40))
        else:
            with Image.open(path) as source:
                art = source.convert("RGB")
            art = art.resize((max(1, round(art.width * height / art.height)), height))
        tile = Image.new(
            "RGB", (art.width + 2 * pad, height + label + 2 * pad), (18, 18, 18)
        )
        tile.paste(art, (pad, pad))
        draw = ImageDraw.Draw(tile)
        new = name == redrawn
        if new:
            draw.rectangle(
                (2, 2, tile.width - 3, pad + height + 2),
                outline=(242, 197, 92),
                width=6,
            )
        draw.text(
            (pad, pad + height + 10),
            f"{name}{'  NEW' if new else ''}",
            fill=(242, 197, 92) if new else (230, 230, 230),
        )
        tiles.append(tile)
    sheet = Image.new(
        "RGB",
        (sum(t.width for t in tiles) or 1, max((t.height for t in tiles), default=1)),
        (18, 18, 18),
    )
    x = 0
    for tile in tiles:
        sheet.paste(tile, (x, 0))
        x += tile.width
    sheet.save(out)
    return out


def _plateless_cast(spine: Mapping[str, Any]) -> list[str]:
    """Name every drawn cast member with no current plate on the story (as the plates step checks).

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` body.

    Returns
    -------
    list[str]
        Names (or ``cast_id``) in spine order; empty when every drawn plate is current.
    """

    current = {
        str(a.get("relation_id"))
        for a in spine.get("media_assets") or []
        if isinstance(a, Mapping)
        and a.get("relation_type") == "cast_card"
        and not a.get("stale")
        and a.get("url")
    }
    return [
        str(row.get("name") or row["cast_id"])
        for row in drawn_cast_rows(dict(spine))
        if str(row["cast_id"]) not in current
        and not any(
            row.get(key)
            for key in ("image_url", "portrait_url", "full_body_url", "url")
        )
    ]


def _settle_failed_plates_step(
    desk: Path, spine: Mapping[str, Any], *, out: Any
) -> None:
    """Put a failed plates step at its gate once redraws have drawn every plate (L-20261001-6).

    The plates step (``ready_cast_enrol``) can fail on one character (a safety
    flag) while ``redraw-plate`` later draws them fine. Without this the desk
    stays ``failed``: ``approve --gate plates`` refuses and the only way on is to
    re-run the step and pay for every plate again. When every drawn cast member
    has a current plate on the story, the desk moves to ``wait_plates`` (the
    human's first yes); otherwise it says who is still missing.

    Parameters
    ----------
    desk
        Series desk.
    spine
        The story just after the redraw.
    out
        Text stream.
    """

    state = load_production(desk)
    if state.phase != "failed" or state.failed_phase != "ready_cast_enrol":
        return
    missing = _plateless_cast(spine)
    if missing:
        print(
            f"!! the plates step is still failed: no current plate for {', '.join(missing)}. "
            f'Redraw each (`fictora-produce redraw-plate --desk {desk} --cast NAME --note "..."`); '
            "the plates gate opens once every plate is drawn.",
            file=out,
        )
        return
    state.phase = "wait_plates"
    state.failed_phase = None
    state.last_error = None
    save_production(desk, state)
    _note(
        desk,
        state.episode_ordinal,
        "plates step was failed; every plate is now drawn by redraw-plate -> wait_plates (nothing re-run, $0).",
    )
    print(
        "Every plate is drawn now: the failed plates step is back at its gate (wait_plates); "
        "nothing is re-run or paid again.",
        file=out,
    )


def run_redraw_plate_with_note(
    desk: Path, *, cast: str, note: str, out: Any = None
) -> Path:
    """Correct ONE character and redraw only their plate. Spends one still; nobody else is drawn or paid.

    ``POST /v1/spines/{id}/cast/{cast_id}/notes`` records the correction in the
    creator's words (free; skipped when the same note is already on the card, so
    a re-run never stacks it twice), then ``POST .../cast/{cast_id}/regenerate``
    redraws that character alone with every note on their card. The job key is
    recorded before the POST, so an interrupted run picks up the same job and
    never pays twice. Refused before anything is sent while a drawn look frame
    awaits its yes (as ``step`` is). Saves the plate next to the old ones as a new version,
    draws a contact sheet of the whole cast with the new plate marked, books
    the still on the desk ledger, and writes a run note.

    Parameters
    ----------
    desk
        Series desk with a story and drawn cast.
    cast
        ``cast_id`` or the character's name.
    note
        The correction, in the creator's words.
    out
        Text stream.

    Returns
    -------
    Path
        The new plate file.

    Raises
    ------
    CommandStopped
        Empty note, unknown character, a failed job, or no new plate on the spine.
    RuntimeError
        A drawn look frame the look yes does not cover (nothing is sent).
    """

    out = out or sys.stdout
    note = " ".join(note.split())
    if not note:
        raise CommandStopped(
            "--note is required: what to change about this character, in your words"
        )
    try:
        check_note_length(note, limit=CAST_NOTE_MAX, command="redraw-plate")
    except ValueError as exc:
        raise CommandStopped(str(exc)) from None
    _hold_for_look(desk, "redraw-plate")
    desk, state, run = _desk_session(desk)
    folder, ep = _plates_home(desk)
    try:
        spine = run.spine(state.spine_id or "")
        everyone = [
            card
            for card in spine.get("cast") or []
            if isinstance(card, Mapping) and card.get("cast_id")
        ]
        cards = drawn_cast_rows(spine)
        named = next(
            (
                c
                for c in everyone
                if name_matches(
                    str(c["cast_id"]), str(c.get("name") or ""), cast.strip()
                )
            ),
            None,
        )
        if named is None:
            names = ", ".join(f"{c.get('name')} ({c['cast_id']})" for c in everyone)
            raise CommandStopped(
                f"no character {cast!r} on the story; the cast is: {names}"
            )
        _refuse_voice_only(named)
        index, card = next(
            (i, c)
            for i, c in enumerate(cards, start=1)
            if c["cast_id"] == named["cast_id"]
        )
        cast_id, name = str(card["cast_id"]), str(card.get("name") or card["cast_id"])
        noted = {
            _note_key(str(n.get("text") or ""))
            for n in card.get("creator_notes") or []
            if isinstance(n, Mapping)
        }
        if _note_key(note) in noted:
            print(
                f"[plate] {name} already carries this note; not added again.",
                file=sys.stderr,
            )
        else:
            run.post(
                f"/v1/spines/{state.spine_id}/cast/{cast_id}/notes",
                {"spine_version": spine["spine_version"], "text": note},
            )
            spine = run.spine(state.spine_id or "")
        body = reuse_generation_body(
            prompt=scene_prompt(spine, state.prompt),
            spine=spine,
            preset_id=state.preset_id,
            preset_version=state.preset_version,
            video_lane=state.video_lane,
        )
        digest = hashlib.sha256(_note_key(note).encode()).hexdigest()[:10]
        terminal = run_unit(
            desk,
            run,
            unit=f"plate-{cast_id}-{digest}",
            path=f"/v1/spines/{state.spine_id}/cast/{quote(cast_id, safe='')}/regenerate",
            body=body,
            video_route=True,
            deadline_seconds=PLATE_DEADLINE_SECONDS,
        )
        _save_desk_json(desk, f"plate-redraw-{cast_id}-terminal", terminal)
        spine = run.spine(state.spine_id or "")
        save_spine_snapshot(desk, ep, spine)
        url = next(
            (str(a["url"]) for a in spine.get("media_assets") or []
             if isinstance(a, Mapping) and a.get("relation_type") == "cast_card" and a.get("relation_id") == cast_id
             and not a.get("stale") and a.get("url")),
            "",
        )  # fmt: skip
        if not url:
            raise CommandStopped(
                f"the redraw completed but the spine has no current plate for {name}"
            )
        folder.mkdir(parents=True, exist_ok=True)
        fetch = httpx.Client(timeout=120.0)
        try:
            path = _orchestrate.download_to_versioned(
                fetch, url, folder, f"plate-ep{ep:02d}-{index}"
            )
        finally:
            fetch.close()
    finally:
        run.client.close()
    record_spend(
        desk, episode=ep, usd=float(STILL_USD), unit=f"plate-note-redraw:{cast_id}"
    )
    current = [
        (
            str(c.get("name") or c["cast_id"]),
            path if i == index else _newest(folder, f"plate-ep{ep:02d}-{i}"),
        )
        for i, c in enumerate(cards, start=1)
    ]
    sheet = plate_contact_sheet(
        current, name, next_versioned_path(folder, f"contact-ep{ep:02d}", ".png")
    )
    _note(
        desk,
        ep,
        f"plate redraw: {name} ({cast_id}) alone -> `{path.name}`, ${float(STILL_USD):.2f}. Note: {note}",
    )
    print(str(path), file=out)
    print(f"contact sheet: {sheet}", file=out)
    print(
        f"{name} redrawn alone (${float(STILL_USD):.2f}); nobody else was drawn or paid.",
        file=out,
    )
    _settle_failed_plates_step(desk, spine, out=out)
    if load_production(desk).phase == "wait_plates":
        print(
            f"Show {sheet.name} to the human; their yes: fictora-produce approve --desk {desk} --gate plates",
            file=out,
        )
    elif load_series(desk).plates.status == "approved":
        print(
            f"!! the plates had a yes before this redraw: show {sheet.name} to the human and record the yes again "
            f"on the server (fictora-produce approve --desk {desk} --gate plates --again --path {sheet}; $0, draws "
            "nothing; a yes recorded on the desk alone leaves the story unapproved and boards fail cast_not_approved). "
            "Boards drawn before now still show "
            "the old plate; redraw-board the takes this character is in.",
            file=out,
        )
    return path


# --- Line check -------------------------------------------------------------------------------------


def _latest(api_dir: Path, stem: str) -> Path | None:
    found = sorted(
        api_dir.glob(f"{stem}-v*.json"),
        key=lambda path: (
            int(path.stem.rsplit("-v", 1)[1])
            if path.stem.rsplit("-v", 1)[1].isdigit()
            else 0
        ),
    )
    return found[-1] if found else None


def lines_not_asked(
    spine: Mapping[str, Any],
    facts: Mapping[str, Any],
    *,
    episode: int,
    take_index: int,
    take_count: int,
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
        raise CommandStopped(
            "these take facts were read without the spine, so they carry no line ids"
        )
    counts = {
        str(row.get("line_id")): int(row.get("count") or 0)
        for row in listed
        if isinstance(row, Mapping)
    }
    approved = dialogue_line_ids(
        spine, episode=episode, take_index=take_index, take_count=take_count
    )
    missing = [
        (number, text)
        for number, (line_id, text) in enumerate(approved, start=1)
        if counts.get(line_id, 0) < 1
    ]
    return missing, len(approved)


def _frame_cast_ids(frame: Mapping[str, Any]) -> set[str]:
    """Who a frame draws: its ``cast_refs`` and the cast its ``subject_blocking`` stages."""

    found = {str(ref) for ref in frame.get("cast_refs") or [] if ref}
    brief = frame.get("visual_brief")
    if isinstance(brief, Mapping):
        for blocking in brief.get("subject_blocking") or []:
            if isinstance(blocking, Mapping) and blocking.get("cast_id"):
                found.add(str(blocking["cast_id"]))
    return found


def _approved_lines_with_speaker(
    spine: Mapping[str, Any], *, episode: int, take_index: int, take_count: int
) -> list[tuple[str, str, str, bool]]:
    """``(line_id, text, speaker cast_id, off_screen)`` for one take's approved lines, in order."""

    found: list[tuple[str, str, str, bool]] = []
    heard_ids = heard_line_ids(spine)
    for take_beats in beats_by_take(spine, episode=episode, take_count=take_count)[
        take_index - 1 : take_index
    ]:
        for beat in take_beats:
            for raw in beat.get("dialogue_lines") or []:
                if not isinstance(raw, Mapping) or not raw.get("line_id"):
                    continue
                text = str(raw.get("spoken_text") or raw.get("text") or "").strip()
                if text:
                    found.append(
                        (
                            str(raw["line_id"]),
                            text,
                            str(raw.get("cast_id") or ""),
                            str(raw["line_id"]) in heard_ids,
                        )
                    )
    return found


def line_row_lines(
    spine: Mapping[str, Any],
    facts: Mapping[str, Any],
    *,
    episode: int,
    take_index: int,
    take_count: int,
    label: str,
) -> tuple[list[str], int]:
    """Say which shot and board row each approved line fell in, and flag a speaker out of frame.

    Reads only the take facts' shot windows (``shots``) and each line's shot
    (``lines[].shot_index``), plus the board frames on the spine. On a row board
    the take plays one shot per row, top to bottom, so shot ``k`` is row ``k``
    when the take has as many shots as the board has rows; otherwise the rows
    are not matched and only the shot is reported. A line whose speaker is on
    screen (not ``off_screen``) is flagged when neither frame of its row draws
    that speaker: the take is then likely to play the line over someone else
    (SCP-173 take 1: the denial over the statue's face).

    Parameters
    ----------
    spine
        The desk's spine snapshot.
    facts
        ``take_facts`` fetched with ``spine_id``.
    episode, take_index, take_count
        Which take.
    label
        Prefix for every printed line (``ep01 t1``).

    Returns
    -------
    tuple[list[str], int]
        Printable lines, and how many lines were flagged (speaker not in frame).
    """

    listed = facts.get("lines")
    if not isinstance(listed, list):
        return [], 0
    by_id = {str(row.get("line_id")): row for row in listed if isinstance(row, Mapping)}
    shots = sorted(
        (
            shot
            for shot in facts.get("shots") or []
            if isinstance(shot, Mapping) and shot.get("shot_index") is not None
        ),
        key=lambda shot: (
            float(shot.get("start_seconds") or 0.0),
            int(shot["shot_index"]),
        ),
    )
    position_by_shot = {
        int(shot["shot_index"]): position
        for position, shot in enumerate(shots, start=1)
    }
    frames = frames_by_set(spine, episode=episode).get(take_index, [])
    rows: dict[int, list[Mapping[str, Any]]] = {}
    for frame in frames:
        raw_row = frame.get("board_row")
        rows.setdefault(
            int(raw_row) if str(raw_row).isdigit() else int(frame.get("ordinal") or 0),
            [],
        ).append(frame)
    row_numbers = sorted(rows)
    rows_match = (
        bool(shots)
        and len(shots) == len(row_numbers)
        and all(frame.get("board_row") is not None for frame in frames)
    )
    names = {
        str(card.get("cast_id")): str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping)
    }
    out: list[str] = []
    flagged = 0
    for number, (line_id, text, speaker, off_screen) in enumerate(
        _approved_lines_with_speaker(
            spine, episode=episode, take_index=take_index, take_count=take_count
        ),
        start=1,
    ):
        row = by_id.get(line_id) or {}
        who = names.get(speaker, speaker or "the speaker")
        head = f"{label}: line {number} ('{text}', {who}{', off screen' if off_screen else ''})"
        shot_index = row.get("shot_index")
        if not int(row.get("count") or 0):
            continue  # the missing-line report names it
        if shot_index is None:
            out.append(f"{head}: not placed in a timed shot")
            continue
        position = position_by_shot.get(int(shot_index))
        start, end = row.get("start_seconds"), row.get("end_seconds")
        window = (
            f" ({float(start):.1f}–{float(end):.1f} s)"
            if start is not None and end is not None
            else ""
        )
        if position is None or not rows_match:
            out.append(
                f"{head}: shot {shot_index}{window}; board rows not matched "
                f"({len(shots)} shots, {len(row_numbers)} rows), so the frame check is skipped"
            )
            continue
        board_row = row_numbers[position - 1]
        cells = [_frame_cast_ids(frame) for frame in rows[board_row]]
        if off_screen:
            out.append(
                f"{head}: shot {shot_index}{window}, row {board_row} (heard, not seen)"
            )
            continue
        in_cells = sum(1 for cast in cells if speaker in cast)
        if in_cells == len(cells):
            out.append(
                f"{head}: shot {shot_index}{window}, row {board_row}, {who} in frame"
            )
        elif in_cells:
            out.append(
                f"{head}: shot {shot_index}{window}, row {board_row}, {who} in {in_cells} of {len(cells)} "
                "frames of the row (in frame for part of the shot)"
            )
        else:
            flagged += 1
            out.append(
                f"  !! {head}: spoken in shot {shot_index}{window}, row {board_row}, but {who} is not in that row's "
                "frames. An on-screen line needs its speaker in frame: edit the frame or the line, then redraw "
                "(warning only)"
            )
    return out, flagged


def run_check_lines(
    desk: Path, *, episode: int, take_id: str | None = None, out: Any = None
) -> int:
    """Say whether each approved line was in the take's instructions, and where it fell, from the take facts.

    Reads ``epNN/api/take-facts-epNN-tK-vM.json`` (newest) and the episode's spine
    snapshot. The compiled prompt is never read, printed or saved. For each line
    the take was asked to say it also prints the shot and board row it fell in,
    and flags (``!!``, warning only) a line spoken on a row whose frames do not
    draw its on-screen speaker (``line_row_lines``).

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
    spine_path = (
        api_dir / "spine.json"
        if (api_dir / "spine.json").is_file()
        else desk / "api" / "spine.json"
    )
    if not spine_path.is_file():
        raise CommandStopped("no spine snapshot on the desk; run `spine --refresh`")
    spine = json.loads(spine_path.read_text(encoding="utf-8"))
    slot = episode_by_ordinal(load_series(desk), episode)
    take_ids = [take.take_id for take in slot.takes] if take_id is None else [take_id]
    missing_total = 0
    for current in take_ids:
        facts_path = _latest(api_dir, f"take-facts-ep{episode:02d}-{current}")
        if facts_path is None:
            print(
                f"ep{episode:02d} {current}: no take facts on the desk (film the take first)",
                file=out,
            )
            continue
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        index = [take.take_id for take in slot.takes].index(current) + 1
        missing, total = lines_not_asked(
            spine, facts, episode=episode, take_index=index, take_count=len(slot.takes)
        )
        for number, text in missing:
            print(
                f"ep{episode:02d} {current}: line {number} ('{text}') was not in the take's instructions",
                file=out,
            )
        print(
            f"ep{episode:02d} {current}: {total - len(missing)} of {total} approved lines asked ({facts_path.name})",
            file=out,
        )
        placed, _flagged = line_row_lines(
            spine,
            facts,
            episode=episode,
            take_index=index,
            take_count=len(slot.takes),
            label=f"ep{episode:02d} {current}",
        )
        for line in placed:
            print(line, file=out)
        missing_total += len(missing)
    return missing_total


# --- Film one episode, or one take of it ---------------------------------------------------------

#: Words that say "again" without naming what in the direction made the fault.
NOT_A_CAUSE = frozenset(
    {
        "again",
        "try again",
        "retry",
        "redo",
        "reroll",
        "re-roll",
        "one more",
        "another one",
        "new take",
    }
)


def _check_cause(cause: str | None, *, refilm: bool, what: str) -> str | None:
    text = " ".join((cause or "").split())
    if not refilm:
        return text or None
    if not text:
        raise CommandStopped(
            f"{what} was filmed already. A second render needs a written cause naming what in the direction produced "
            'the fault (--cause "..."). "Try again" is not a cause'
        )
    if text.casefold().strip(" .!") in NOT_A_CAUSE or len(text) < 12:
        raise CommandStopped(
            f"--cause {text!r} does not name what in the direction produced the fault"
        )
    return text


def _film_scope(
    desk: Path, *, episode: int, take_id: str | None
) -> tuple[list[str], int | None, str]:
    slot = episode_by_ordinal(load_series(desk), episode)
    desk_takes = [take.take_id for take in slot.takes]
    if take_id is None:
        return desk_takes, None, f"ep{episode:02d}"
    if not (take_id.startswith("t") and take_id[1:].isdigit()):
        raise CommandStopped("--take is t1, t2 ...")
    if take_id not in desk_takes:
        raise CommandStopped(
            f"{take_id} is not a take on ep{episode:02d} (it has {', '.join(desk_takes)})"
        )
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
        run = _open_run(
            desk, replace(state, episode_ordinal=episode)
        )  # artefacts go to that episode's api/
    try:
        return _run_film(
            desk,
            state,
            run,
            episode=episode,
            take_id=take_id,
            cause=cause,
            confirm_spend=confirm_spend,
            out=out,
        )
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
        raise CommandStopped(
            "a take job is in flight on this desk; finish it with `step` first"
        )
    cfg = load_production_config(desk)
    take_ids, take_index, key = _film_scope(desk, episode=episode, take_id=take_id)
    slot = episode_by_ordinal(load_series(desk), episode)
    no_yes = [
        take.take_id
        for take in slot.takes
        if take.take_id in take_ids and take.board.status != "approved"
    ]
    if no_yes:
        raise CommandStopped(
            f"ep{episode:02d} {', '.join(no_yes)}: no human yes on the board yet (`approve --gate board`); "
            "a take is filmed only from an approved board"
        )
    filmed_before = any(
        take.filmed_count for take in slot.takes if take.take_id in take_ids
    )
    what = f"ep{episode:02d} {take_id}" if take_id else f"episode {episode}"
    reason = _check_cause(cause, refilm=filmed_before, what=what)
    if take_index is None and len(take_ids) > 1 and filmed_before:
        print(
            f"!! this re-films every take of episode {episode}; to re-film one take pass --take tK",
            file=out,
        )
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
    stopped = film_stop_message(spine, episode=episode)
    if stopped:
        raise CommandStopped(stopped)
    for warning in stranded_preflight(spine, unit=key, desk=desk, episode=episode):
        print(warning, file=out)
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
                record_verdict(
                    desk,
                    episode=episode,
                    take_id=current,
                    verdict="change",
                    cause=reason,
                )
    fresh = load_production(desk)
    pending = fresh.pending.get(unit)
    if pending is None:
        pending = {
            "key": f"{fresh.idempotency_prefix}-{unit}-a{fresh.attempts.get(unit, 0) + 1}",
            "job_id": None,
        }
        fresh.pending[unit] = pending
        save_production(desk, fresh)
    job_id = pending.get("job_id")
    if job_id:
        print(
            f"[film] Picking up job {job_id} from the last run (same key, no second charge).",
            file=sys.stderr,
        )
    else:
        try:
            job = stages.post_video_generation(
                run, body, idempotency_key=str(pending["key"]), episode=episode
            )
        except SystemExit as exc:
            message = (
                exc.code if isinstance(exc.code, str) else api_error_text(exc.code)
            )
            plain = explain_film_refusal(message, spine)
            if plain is None:
                raise
            _note(desk, episode, f"film {what} refused, nothing charged: {plain}")
            raise CommandStopped(f"{message}\n{plain}") from None
        job_id = admitted_job_id(job)
        if not job_id:
            raise CommandStopped(
                f"/v1/video-generations answered without a job id ({', '.join(sorted(job))})"
            )
        fresh = load_production(desk)
        fresh.pending[unit]["job_id"] = job_id
        save_production(desk, fresh)
        _save_desk_json(desk, f"{unit}-enrol", job)
    print(
        f"[film] Filming {what} (video job {job_id}). Usually 5-15 minutes.",
        file=sys.stderr,
    )
    try:
        raw = wait_for_raw_scene_clips(
            run,
            str(job_id),
            deadline_seconds=cfg.poll_video_deadline_seconds,
            save_as=raw_clips_name(unit),
            expected_clips=len(take_ids),
        )
    except VideoJobFailed as exc:
        _forget_failed_film(desk, unit, job_id=str(job_id), what=what, episode=episode)
        raise VideoJobFailed(
            f"{exc.code}\nNothing was collected or booked for {what}. The failed job is cleared: "
            f"the next `film --episode {episode}"
            + (f" --take {take_id}" if take_id else "")
            + " --confirm-spend` starts a NEW paid job under a fresh key (only after the human's yes)."
        ) from None
    spine = run.spine(state.spine_id or "")
    save_spine_snapshot(desk, episode, spine)
    collecting = load_production(desk)
    got = collect_takes(
        desk,
        run,
        collecting,
        raw,
        episode=episode,
        clip_seconds=cfg.clip_duration_seconds,
        spine=spine,
        asked=take_ids,
    )
    fresh = load_production(desk)
    fresh.remember_server_lane(collecting.server_lane())
    fresh.pending.pop(unit, None)
    fresh.attempts[unit] = fresh.attempts.get(unit, 0) + 1
    fresh.film_estimates.pop(key, None)
    if got.first_url is None:
        save_production(desk, fresh)
        raise CommandStopped(
            f"video job {job_id} completed but no take for {what} came back"
        )
    if (
        fresh.episode_ordinal == episode
        and take_index is None
        and fresh.phase in {"ready_estimate", "wait_spend"}
    ):
        fresh.phase = "complete"
    fresh.last_video_job_id = str(job_id)
    save_production(desk, fresh)
    cause_note = f" Cause: {reason}." if reason else ""
    _note(
        desk,
        episode,
        f"film {what}: video job `{job_id}`; "
        + "; ".join(got.jobs)
        + f". Booked ${got.booked_usd:.2f}.{cause_note}"
        + (f" NOT FILED: {'; '.join(got.unplaced)}." if got.unplaced else ""),
    )
    lines = [
        f"Filmed {what}: {len(got.jobs)} take(s), ${got.booked_usd:.2f} booked. Video job {job_id}."
    ]
    lines += [f"  {line}" for line in got.jobs + got.on_screen]
    lines.append(
        f"Watch the new take in ep{episode:02d}/takes/. After the human says Use it: "
        f"`fictora-produce finish --desk <desk> --episode {episode} --take {take_id or 'tK'}`."
    )
    text = (
        "\n".join(lines) + foreign_warning(got.foreign) + unplaced_warning(got.unplaced)
    )
    print(text, file=out)
    return text


def _forget_failed_film(
    desk: Path, unit: str, *, job_id: str, what: str, episode: int
) -> None:
    """Clear a film unit whose job ended failed, so the next ``film`` enrols afresh.

    The pending entry is dropped and the unit's attempts bumped, so the next
    idempotency key (``…-<unit>-aN``) is new and the server starts a new job
    instead of answering with the failed one (as ``retry-step`` does for a
    stage). Only for a job the server says is over: a timeout or a broken poll
    keeps the entry, so an interrupted film picks up its job and pays once.
    """

    _forget_unit(desk, unit)
    _note(
        desk,
        episode,
        f"film {what}: video job `{job_id}` failed; nothing collected or booked. "
        "Cleared it: the next film enrols a new job under a fresh key.",
    )


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
    estimate = stages.estimate_batch(
        run,
        spine_id=state.spine_id or "",
        episode=episode,
        reroll_take_index=take_index,
    )
    _save_desk_json(desk, f"film-{key}-estimate", estimate)
    state.remember_server_lane(server_lane(estimate))
    spine = run.spine(state.spine_id or "")
    stopped = film_stop_message(spine, episode=episode)
    if stopped:
        raise CommandStopped(stopped)
    cast_count = len(drawn_cast_rows(spine))
    usd, source, warnings = price_estimate(
        state, cfg, estimate, cast_count=cast_count, takes=len(take_ids)
    )
    warnings = [
        *stranded_preflight(spine, unit=key, desk=desk, episode=episode),
        *warnings,
    ]
    if (
        warnings
        and take_index is not None
        and "reroll_take_index" in str(estimate.get("detail") or "")
    ):
        source += "; the deployed API cannot price one take yet"
    fresh = load_production(desk)
    fresh.film_estimates[key] = usd
    fresh.remember_server_lane(state.server_lane())
    save_production(desk, fresh)
    if take_index is not None:
        record_estimate(desk, episode=episode, take_id=take_ids[0], usd=usd)
    scope = (
        f"only {take_ids[0]} of episode {episode}"
        if take_index is not None
        else f"episode {episode} alone ({len(take_ids)} take(s))"
    )
    earlier = (
        f"; episodes 1-{episode - 1} are not filmed or booked again"
        if episode > 1
        else ""
    )
    others = (
        "; the episode's other takes are kept as filmed"
        if take_index is not None
        else ""
    )
    lines = [
        *warnings,
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
    _note(
        desk,
        episode,
        " ".join([*warnings, f"film {what} priced ${usd:.2f} ({source})."]),
    )
    return text


# --- CLI -------------------------------------------------------------------------------------------

EPISODE_COMMANDS = frozenset(
    {
        "arc",
        "brief",
        "language",
        "author",
        "rewrite",
        "memory",
        "edit",
        "expressions",
        "line",
        "cast",
        "look-frame",
        "look",
        "look-note",
        "sound-note",
        "inner-voice",
        "take-facts",
        "spine",
        "redraw-board",
        "plates",
        "redraw-plate",
        "check-lines",
        "film",
    }
)


def add_episode_parsers(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the episode flow commands on ``fictora-produce``.

    Parameters
    ----------
    sub
        Argparse subparser set.
    """

    from creation.cast_commands import LOOK_HELP, STAGING_HELP

    arc = sub.add_parser(
        "arc",
        help="Episode 2's series arc: --list the three, then --pick N. Spends nothing.",
    )
    arc.add_argument("--desk", type=Path, required=True)
    mode = arc.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--list", action="store_true", help="Read the brief and print its arcs."
    )
    mode.add_argument(
        "--pick",
        type=int,
        metavar="N",
        help="Keep arc N, then print episode 2's directions.",
    )
    arc.add_argument(
        "--title",
        default=None,
        help="With --pick: the human's rewrite of the title (<=80).",
    )
    arc.add_argument(
        "--line",
        default=None,
        help=f"With --pick: the human's rewrite of the line (<={ARC_LINE_MAX} characters).",
    )
    arc.add_argument(
        "--episodes",
        type=int,
        default=None,
        help="Intended run, 7-240 (soft default; can continue).",
    )

    brief = sub.add_parser(
        "brief",
        help="Read the next-episode brief (directions) for episode N; or, with --edit / --strip-narration, "
        "replace the story's stored brief. Spends nothing.",
    )
    brief.add_argument("--desk", type=Path, required=True)
    brief.add_argument(
        "--episode",
        type=int,
        default=None,
        help="Episode whose next-episode brief to read.",
    )
    brief.add_argument(
        "--episodes", type=int, default=None, help="Intended run, 7-240 (soft default)."
    )
    brief_edit = brief.add_mutually_exclusive_group()
    brief_edit.add_argument(
        "--edit",
        default=None,
        metavar="@FILE",
        help="Replace the story's brief with this whole text (@file, a path, or words). Shows the change first.",
    )
    brief_edit.add_argument(
        "--strip-narration",
        action="store_true",
        help="Take the narrator / voice-over lines out of the desk's brief and send the rest.",
    )
    brief.add_argument(
        "--preview",
        action="store_true",
        help="With --edit / --strip-narration: show the change, send nothing.",
    )

    language = sub.add_parser(
        "language",
        help="Change the language the show is performed in (ja, ko or en). Shows what it sets aside first. "
        "Spends nothing.",
    )
    language.add_argument("--desk", type=Path, required=True)
    language.add_argument("--spoken", required=True, help="ja, ko or en.")
    language.add_argument(
        "--preview", action="store_true", help="Show the change, send nothing."
    )
    language.add_argument(
        "--confirm-filmed",
        action="store_true",
        help="Change it although takes were filmed in another language (after the human's yes).",
    )

    author = sub.add_parser(
        "author",
        help="Write episode N (2 on) on the API, sync its lines, point the desk at it. Never approves.",
    )
    author.add_argument("--desk", type=Path, required=True)
    author.add_argument(
        "--episode", type=int, required=True, help="Episode ordinal, 2 or more."
    )
    author.add_argument(
        "--direction",
        type=int,
        default=None,
        metavar="N",
        help="Direction N from the newest saved brief.",
    )
    author.add_argument(
        "--line",
        default=None,
        help="The human's own direction for this episode (400 characters at most, or the deploy's own limit; "
        "counted before sending).",
    )
    author.add_argument(
        "--title", default=None, help="With --line: a short name for it."
    )
    add_narrator_answer_args(author)

    rewrite = sub.add_parser(
        "rewrite",
        help="Re-write a drafted, not yet approved episode N (2 on) from the human's direction (an episode note), "
        "sync its lines, point the desk at it. Free; never approves.",
    )
    rewrite.add_argument("--desk", type=Path, required=True)
    rewrite.add_argument(
        "--episode", type=int, required=True, help="Episode ordinal, 2 or more."
    )
    rewrite.add_argument(
        "--line",
        required=True,
        help="What the rewrite should change, in the human's words (same limit as `author --line`; counted "
        "before sending). Replaces the last `rewrite` direction on this episode.",
    )
    rewrite.add_argument(
        "--title", default=None, help="A short name for the direction."
    )
    add_narrator_answer_args(rewrite)

    memory = sub.add_parser(
        "memory", help="Add one standing note or open thread to the series memory."
    )
    memory.add_argument("--desk", type=Path, required=True)
    which = memory.add_mutually_exclusive_group(required=True)
    which.add_argument("--note", default=None)
    which.add_argument("--thread", default=None)

    edit = sub.add_parser(
        "edit",
        help=(
            "Edit a beat's shot, a frame's visual_brief, or a line (prefer `line` for lines). PATCH before "
            "the script gate; after it, the cascade with paid items off unless --select-regen."
        ),
    )
    edit.add_argument("--desk", type=Path, required=True)
    edit.add_argument("--episode", type=int, required=True)
    target = edit.add_mutually_exclusive_group(required=True)
    target.add_argument("--beat", default=None, help="Beat id or ordinal.")
    target.add_argument("--frame", default=None, help="Frame id or ordinal.")
    target.add_argument(
        "--line-id", default=None, help="Dialogue line id (line_ep01_01)."
    )
    edit.add_argument("--intent", default=None, help="A beat's new motion_intent.")
    edit.add_argument(
        "--set",
        dest="assignments",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help='motion_direction field (beat) or visual_brief field (frame). Dotted keys; a number indexes a list (subject_blocking.0.pose). A value starting with " [ { (or null/true/false) is JSON: \'shot_scale="extreme close-up"\' stores the text without quotes; plain text works too. On a frame, cast_refs=["Hana"] (names or ids) changes who is in the shot. '
        "On a beat, delivery (how the speaker plays the line) is one of "
        + ", ".join(LINE_DELIVERIES)
        + ", or null for plain; checked before sending.",
    )
    edit.add_argument("--text", default=None, help="Line: the English script text.")
    edit.add_argument(
        "--spoken",
        default=None,
        help="Line: pin the exact performed line (JA/KO shows).",
    )
    edit.add_argument(
        "--subtitle",
        default=None,
        help="Line: the subtitle for the pinned line (with --spoken).",
    )
    plan = edit.add_mutually_exclusive_group()
    plan.add_argument(
        "--shot-plan", default=None, metavar="JSON",
        help=(
            "Beat: its shot plan, 1-4 shots [{\"size\", \"subject\", \"camera\"?, \"angle\"?}]; shot 1 is the beat's "
            "first board row. Replaces the whole plan. JSON, or @FILE / an existing file path to read it from."
        ),
    )  # fmt: skip
    plan.add_argument(
        "--shot", dest="shots", action="append", default=None, metavar="SIZE|SUBJECT|CAMERA|ANGLE",
        help="Beat: one shot of its plan, in order; repeat 1-4 times. Camera and angle optional.",
    )  # fmt: skip
    plan.add_argument(
        "--clear-shot-plan",
        action="store_true",
        help="Beat: remove its plan (the frames author chooses again).",
    )
    edit.add_argument(
        "--expression",
        default=None,
        metavar="KIND|none",
        help=(
            "Beat: the expression it plays (a kind from `expressions`, e.g. slow_surprise), or `none` to give the "
            "choice back to the frames author. Checked against the deploy first."
        ),
    )

    expressions = sub.add_parser(
        "expressions",
        help="List the expressions this deploy offers; with --episode, what each beat asks for. Spends nothing.",
    )
    expressions.add_argument("--desk", type=Path, required=True)
    expressions.add_argument("--episode", type=int, default=None)
    edit.add_argument(
        "--select-regen",
        action="store_true",
        help="After the gate: also run paid regeneration items.",
    )
    edit.add_argument(
        "--preview",
        action="store_true",
        help="After the gate: print the cascade and stop.",
    )

    line = sub.add_parser(
        "line",
        help=(
            "Change, add or remove a line on the server AND the desk in one step (words, performed JA/KO line, "
            "speaker, seen/heard; --add on a beat, --remove, --new-voice for someone only heard). Says what happens "
            "to the script approval. With no change, lists the lines."
        ),
        description=(
            "Change a line: --line N with --text/--spoken/--speaker/--off-screen. Add one: --add --beat N --speaker "
            'NAME --text "..." [--off-screen]; a beat holds one line, so to replace it add --remove OLD in the same '
            "command. Drop one: --remove N. A new voice that is heard and never drawn comes with its line: --add --beat "
            'N --text "..." --new-voice NAME --role "..." --voice-description "..." [--provider-voice X].'
        ),
    )
    line.add_argument("--desk", type=Path, required=True)
    line.add_argument("--episode", type=int, required=True)
    line.add_argument(
        "--line",
        default=None,
        help="Line id (line_episode_01_02) or its number in the episode (2).",
    )
    line.add_argument("--text", default=None, help="The English line.")
    line.add_argument(
        "--spoken", default=None, help="Pin the exact performed line (JA/KO shows)."
    )
    line.add_argument(
        "--subtitle",
        default=None,
        help="The subtitle for the pinned line (with --spoken).",
    )
    line.add_argument(
        "--speaker", default=None, help="Someone already in the cast (name or cast id)."
    )
    seen = line.add_mutually_exclusive_group()
    seen.add_argument(
        "--off-screen",
        dest="off_screen",
        action="store_const",
        const=True,
        default=None,
        help="The speaker is heard, not seen (a voice on a speaker, behind a door).",
    )
    seen.add_argument(
        "--on-screen",
        dest="off_screen",
        action="store_const",
        const=False,
        help="The speaker is seen again.",
    )
    line.add_argument(
        "--add",
        action="store_true",
        help="Add a line on --beat (with --text and --speaker or --new-voice).",
    )
    line.add_argument(
        "--beat",
        default=None,
        help="With --add: the beat's number in the episode, or its id.",
    )
    line.add_argument(
        "--remove",
        default=None,
        help="Drop a line: its id or number (with --add, replaces it).",
    )
    line.add_argument(
        "--speaker-moves",
        action="store_true",
        help="With --add: also make the speaker the beat's motion subject (the server asks for it when "
        "the beat moves someone else).",
    )
    line.add_argument(
        "--new-voice",
        default=None,
        help="With --add: a new character who is only heard, speaking the line.",
    )
    line.add_argument(
        "--role",
        default=None,
        help='With --new-voice: who they are ("facility intercom").',
    )
    line.add_argument(
        "--voice-description",
        default=None,
        help='With --new-voice: how the voice sounds ("tinny, clipped, calm").',
    )
    line.add_argument(
        "--provider-voice",
        default=None,
        help="With --new-voice: an Eleven v3 voice. Left out, the server picks one: a current server from "
        "--voice-description (gender, accent, age, timbre), an older one the first voice nobody uses. The "
        "pick is printed: audition the new cast id before filming.",
    )
    line.add_argument(
        "--select-regen",
        action="store_true",
        help="After the gate: also run paid regeneration items.",
    )
    line.add_argument(
        "--preview",
        action="store_true",
        help="After the gate: print the cascade and stop.",
    )
    line.add_argument(
        STRAND_FLAG,
        dest="strand_voice",
        action="store_true",
        help="Remove (or give away) the last line of someone who is only heard anyway. Without it the kit stops: "
        "they would stay in the cast with no look, and the server may refuse to film until its fix is live.",
    )
    line.add_argument(
        "--look",
        default=None,
        help="With --new-voice (optional, for someone who may be drawn later) or --new-character (required): "
        "how they look. " + LOOK_HELP,
    )
    line.add_argument(
        "--new-character",
        default=None,
        help="With --add on a silent beat: a new character who is SEEN speaking the line. Needs --role, "
        "--voice-description and --look; on a drawn beat also --staging. Runs the four edits the server needs "
        "(voice + line, look, line on screen, staged in the frame); re-run the same command to finish one "
        "that stopped.",
    )
    line.add_argument("--staging", default=None, help=STAGING_HELP)
    line.add_argument(
        "--language",
        default=None,
        help="With --spoken on a show the server holds as English: ja or ko, the language it is really "
        "performed in. The line is recorded on the desk, not sent (the server cannot change a show's language).",
    )
    add_narrator_answer_args(line)

    cast = sub.add_parser(
        "cast",
        help="Give a cast member a look (visual description + brief) on the server, e.g. a voice an episode "
        "later put in a frame. Shows the change; PATCH before the script gate, the cast cascade after it.",
    )
    cast.add_argument("--desk", type=Path, required=True)
    cast.add_argument("--name", required=True, help="The character's name or cast id.")
    cast.add_argument("--look", required=True, help=LOOK_HELP)
    cast.add_argument(
        "--select-regen",
        action="store_true",
        help="After the gate: also run paid regeneration items (the plate; or draw it with redraw-plate).",
    )
    cast.add_argument(
        "--preview",
        action="store_true",
        help="Print the change (after the gate, the cascade too) and send nothing.",
    )

    frame = sub.add_parser(
        "look-frame",
        help=(
            "Draw our own style frame on the server from a written description (text only, 1088x1936, $0.30). "
            "Saves shared/look/look-frame-vN.png; never pins (approve --gate look does, with the human's yes)."
        ),
    )
    frame.add_argument("--desk", type=Path, required=True)
    frame.add_argument(
        "--description",
        required=True,
        metavar="TEXT|FILE",
        help="The look, written down: the words, or @FILE / an existing file path.",
    )
    frame.add_argument(
        "--size", default=LOOK_FRAME_DEFAULT_SIZE, choices=LOOK_FRAME_SIZES
    )

    look = sub.add_parser(
        "look",
        help="Pin one style frame by public https URL. Not the look yes: approve --gate look records it. Spends nothing.",
    )
    look.add_argument("--desk", type=Path, required=True)
    look.add_argument("--url", required=True)

    note = sub.add_parser(
        "look-note", help="Add or remove a look note (max 5). Spends nothing."
    )
    note.add_argument("--desk", type=Path, required=True)
    change = note.add_mutually_exclusive_group(required=True)
    change.add_argument("--add", default=None)
    change.add_argument("--remove", default=None, metavar="ID|N")

    sound = sub.add_parser(
        "sound-note",
        help='Add a sound note ("add a dry stone crack at the end" on one take; "no purring" on all), '
        "--remove one, or list them (max 5). Spends nothing.",
    )
    sound.add_argument("--desk", type=Path, required=True)
    sound.add_argument(
        "text", nargs="?", default=None, help="The note in the creator's words."
    )
    sound.add_argument(
        "--episode", type=int, default=None, help="Default: the desk's episode."
    )
    sound.add_argument(
        "--take", default=None, help="t1, t2 ...: the take an added sound lands on."
    )
    place = sound.add_mutually_exclusive_group()
    place.add_argument("--shot", type=int, default=None, help="A shot of that take.")
    place.add_argument(
        "--row", type=int, default=None, help="A row of that take's board."
    )
    sound.add_argument("--remove", default=None, metavar="ID|N")

    thought = sub.add_parser(
        "inner-voice",
        help=(
            "A character's own thoughts on an episode (inner voice): add one (--cast, --text, --at), --remove one, "
            "--clear, or list them. On the character who thinks it; no cast place. Spends nothing."
        ),
        description=(
            'Add a thought: --cast NAME --text "..." [--spoken-text "..."] --at S [--until S] (seconds on the '
            "episode as filmed; take 1 starts at 0; --text is the caption, --spoken-text the words said when "
            "they differ). For someone heard and never seen (an intercom, a phone, a narrator) use `line --add "
            "--new-voice` instead. `finish` makes each cue dry in the thinker's locked voice on the take it falls "
            "in, lays it and captions it in Georgia italic."
        ),
    )
    thought.add_argument("--desk", type=Path, required=True)
    thought.add_argument("--episode", type=int, required=True)
    thought.add_argument(
        "--cast",
        default=None,
        help="Who thinks it: someone already in the cast (name or cast id).",
    )
    thought.add_argument(
        "--text",
        default=None,
        help="The thought as captioned (English). Also what the voice says unless --spoken-text is given.",
    )
    thought.add_argument(
        "--spoken-text",
        default=None,
        help="The words the voice says when they differ from the caption (a Japanese thought under an "
        "English --text), as `voice-line --text/--spoken-text` and `line --spoken/--subtitle` do. Kept on "
        "the desk (epNN/inner-voice-spoken.json).",
    )
    thought.add_argument(
        "--at",
        type=float,
        default=None,
        metavar="S",
        help="Start, seconds on the episode as filmed.",
    )
    thought.add_argument(
        "--until",
        type=float,
        default=None,
        metavar="S",
        help="End, seconds. Left out: about 0.4 s a word (at least 1.2 s).",
    )
    change_thought = thought.add_mutually_exclusive_group()
    change_thought.add_argument("--remove", default=None, metavar="ID|N")
    change_thought.add_argument(
        "--clear", action="store_true", help="Remove every thought on the episode."
    )

    facts = sub.add_parser(
        "take-facts",
        help="Show a take's saved SFX plan, or --refresh it from the server (new version; "
        "prints what changed). Spends nothing.",
    )
    facts.add_argument("--desk", type=Path, required=True)
    facts.add_argument("--episode", type=int, required=True)
    facts.add_argument("--take", required=True, help="t1, t2 ...")
    facts.add_argument("--refresh", action="store_true")

    spine = sub.add_parser(
        "spine", help="Save the story from the server again (--refresh)."
    )
    spine.add_argument("--desk", type=Path, required=True)
    spine.add_argument("--refresh", action="store_true", required=True)

    redraw = sub.add_parser(
        "redraw-board",
        help='Redraw one board; --note "what\'s wrong" edits the shots first and prints them per row. $0.30.',
    )
    redraw.add_argument("--desk", type=Path, required=True)
    redraw.add_argument("--episode", type=int, required=True)
    redraw.add_argument("--take", required=True, help="t1, t2 ...")
    redraw.add_argument(
        "--note",
        help=f"What is wrong with the board, as the camera should see it (<={BOARD_NOTE_MAX} characters; "
        "longer is refused before anything is sent): turned into shot edits (the app's director path) and "
        "printed per row before the redraw; also kept in run notes and series.json.",
    )
    redraw.add_argument(
        "--cause",
        help="Why: a LABEL for the desk (never changes the drawing). Defaults to the note.",
    )
    redraw.add_argument(
        "--same-shots",
        "--reroll",
        dest="reroll",
        action="store_true",
        help="Redraw although the frames, beats, look notes and plates are unchanged (the same scene; "
        "a random bad draw). $0.30.",
    )

    plates = sub.add_parser(
        "plates",
        help="Retired: prints a pointer to `redraw-plate --note` and sends nothing (exit 2).",
    )
    plates.add_argument("--desk", type=Path, help="Ignored; named in the pointer.")
    plates.add_argument("--cast", help="Ignored; named in the pointer.")
    plates.add_argument("--cause", help="Ignored: use `redraw-plate --note`.")
    plate = sub.add_parser(
        "redraw-plate",
        help="Correct ONE character (--note, their words) and redraw only their plate. $0.30; nobody else is paid.",
    )
    plate.add_argument("--desk", type=Path, required=True)
    plate.add_argument("--cast", required=True, help="cast_id or name.")
    plate.add_argument(
        "--note",
        required=True,
        help=f"What to change about this character, in your words (<={CAST_NOTE_MAX} characters).",
    )

    film = sub.add_parser(
        "film",
        help=(
            "Film episode N alone, or only take K of it (nothing earlier is filmed or booked). Prices first; "
            "films with --confirm-spend. A re-film needs --cause."
        ),
    )
    film.add_argument("--desk", type=Path, required=True)
    film.add_argument("--episode", type=int, required=True)
    film.add_argument(
        "--take", default=None, help="tK: film only this take of the episode."
    )
    film.add_argument(
        "--cause",
        default=None,
        help="Required to film again: what in the direction produced the fault.",
    )
    film.add_argument(
        "--confirm-spend",
        action="store_true",
        help="The human said yes to the printed number.",
    )

    check = sub.add_parser(
        "check-lines",
        help="Were the approved lines in the take's instructions? (take facts)",
    )
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
                run_arc_pick(
                    args.desk,
                    option=args.pick,
                    title=args.title,
                    line=args.line,
                    episodes=args.episodes,
                )
            return 0
        if args.command == "brief":
            if args.edit is not None or args.strip_narration:
                from creation.story_setup import run_brief_edit

                try:
                    done = run_brief_edit(
                        args.desk,
                        edit=args.edit,
                        strip=args.strip_narration,
                        preview=args.preview,
                    )
                except CommandStopped as exc:
                    print(f"Stopped: {exc}", file=sys.stderr)
                    print(f"Refused: {refusal_reason(str(exc))}", file=sys.stderr)
                    return 2
                return 0 if done or args.preview else 2
            if args.episode is None:
                raise CommandStopped(
                    "brief: pass --episode N (the next-episode brief), or --edit @FILE / --strip-narration"
                )
            run_brief(args.desk, episode=args.episode, episodes=args.episodes)
            return 0
        if args.command == "language":
            from creation.story_setup import run_language

            done = run_language(
                args.desk,
                spoken=args.spoken,
                preview=args.preview,
                confirm_filmed=args.confirm_filmed,
            )
            return 0 if done or args.preview else 2
        if args.command == "author":
            direction = author_direction(
                args.desk,
                args.episode,
                pick=args.direction,
                line=args.line,
                title=args.title,
            )
            run_author(
                args.desk,
                episode=args.episode,
                direction=direction,
                narrator_heard_only=args.narrator_heard_only,
                narrator_on_screen=args.narrator_on_screen,
                ask=interactive_ask(),
            )
            return 0
        if args.command == "rewrite":
            run_rewrite(
                args.desk,
                episode=args.episode,
                line=args.line,
                title=args.title,
                narrator_heard_only=args.narrator_heard_only,
                narrator_on_screen=args.narrator_on_screen,
                ask=interactive_ask(),
            )
            return 0
        if args.command == "memory":
            run_memory(args.desk, note=args.note, thread=args.thread)
            return 0
        if args.command == "edit":
            try:
                shot_plan = (
                    plan_from_json(args.shot_plan) if args.shot_plan is not None
                    else plan_from_shots(args.shots) if args.shots else None
                )  # fmt: skip
            except ShotPlanError as exc:
                raise CommandStopped(str(exc)) from None
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
                shot_plan=shot_plan,
                clear_shot_plan=args.clear_shot_plan,
                expression=args.expression,
                select_regen=args.select_regen,
                preview_only=args.preview,
            )
            return 0
        if args.command == "expressions":
            run_expressions(args.desk, episode=args.episode)
            return 0
        if args.command == "line":
            run_line(
                args.desk,
                episode=args.episode,
                line=args.line,
                text=args.text,
                spoken=args.spoken,
                subtitle=args.subtitle,
                speaker=args.speaker,
                off_screen=args.off_screen,
                add=args.add,
                beat=args.beat,
                remove=args.remove,
                speaker_moves=args.speaker_moves,
                new_voice=args.new_voice,
                role=args.role,
                voice_description=args.voice_description,
                provider_voice=args.provider_voice,
                select_regen=args.select_regen,
                preview_only=args.preview,
                strand_voice=args.strand_voice,
                narrator_heard_only=args.narrator_heard_only,
                narrator_on_screen=args.narrator_on_screen,
                ask=interactive_ask(),
                look=args.look,
                new_character=args.new_character,
                staging=args.staging,
                language=args.language,
            )
            return 0
        if args.command == "cast":
            from creation.cast_commands import run_cast_look

            run_cast_look(
                args.desk, name=args.name, look=args.look, select_regen=args.select_regen,
                preview_only=args.preview,
            )  # fmt: skip
            return 0
        if args.command == "look-frame":
            run_look_frame(args.desk, description=args.description, size=args.size)
            return 0
        if args.command == "look":
            run_look(args.desk, url=args.url)
            return 0
        if args.command == "look-note":
            run_look_note(args.desk, add=args.add, remove=args.remove)
            return 0
        if args.command == "sound-note":
            run_sound_note(
                args.desk,
                text=args.text,
                episode=args.episode,
                take_id=args.take,
                shot=args.shot,
                row=args.row,
                remove=args.remove,
            )
            return 0
        if args.command == "inner-voice":
            run_inner_voice(
                args.desk,
                episode=args.episode,
                cast=args.cast,
                text=args.text,
                at=args.at,
                until=args.until,
                remove=args.remove,
                clear=args.clear,
                spoken_text=args.spoken_text,
            )
            return 0
        if args.command == "take-facts":
            run_take_facts(
                args.desk,
                episode=args.episode,
                take_id=args.take,
                refresh=args.refresh,
            )
            return 0
        if args.command == "spine":
            run_spine_refresh(args.desk)
            return 0
        if args.command == "redraw-board":
            run_redraw_board(
                args.desk,
                episode=args.episode,
                take_id=args.take,
                cause=args.cause,
                note=args.note,
                reroll=args.reroll,
            )
            return 0
        if args.command == "plates":
            print(plates_cast_retired(args.desk, args.cast), file=sys.stderr)
            return 2
        if args.command == "redraw-plate":
            run_redraw_plate_with_note(args.desk, cast=args.cast, note=args.note)
            return 0
        if args.command == "film":
            run_film(
                args.desk,
                episode=args.episode,
                take_id=args.take,
                cause=args.cause,
                confirm_spend=args.confirm_spend,
            )
            return 0
        if args.command == "check-lines":
            return (
                5
                if run_check_lines(args.desk, episode=args.episode, take_id=args.take)
                else 0
            )
    except CommandStopped as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        _print_refused(args.command, str(exc), getattr(exc, "items", ()))
        return 2
    except SystemExit as exc:
        text = api_error_text(exc.code) if not isinstance(exc.code, str) else exc.code
        print(f"Stopped: {text}", file=sys.stderr)
        _print_refused(args.command, text, ())
        return 2
    raise ValueError(f"unknown episode command {args.command}")


def _print_refused(command: str, message: str, items: Sequence[str]) -> None:
    """End an ``edit`` / ``line`` refusal on ``Refused: <reason>`` (after ``Stopped:``), per change first."""

    if command not in EDIT_VERDICT_COMMANDS:
        return
    for row in edit_verdict(items, refused=message):
        print(row, file=sys.stderr)


__all__ = [
    "EPISODE_COMMANDS",
    "CommandStopped",
    "EditRefused",
    "add_episode_parsers",
    "change_items",
    "edit_verdict",
    "admitted_job_id",
    "author_direction",
    "build_line_add_remove_patch",
    "build_patch",
    "episode_lines",
    "explain_refusal",
    "dispatch_episode",
    "line_edit_consequences",
    "line_listing",
    "lines_not_asked",
    "look_frame_route_missing",
    "memory_body",
    "parse_assignment",
    "resolve_line_id",
    "resolve_speaker",
    "run_arc_list",
    "run_arc_pick",
    "run_author",
    "run_rewrite",
    "rewrite_note_text",
    "run_brief",
    "line_row_lines",
    "run_check_lines",
    "run_edit",
    "run_expressions",
    "deploy_expressions",
    "resolve_or_stop",
    "run_film",
    "run_line",
    "run_approve_look",
    "run_look",
    "run_look_frame",
    "run_look_note",
    "run_inner_voice",
    "run_sound_note",
    "run_take_facts",
    "run_memory",
    "plate_contact_sheet",
    "board_changes",
    "redraw_needs_an_edit",
    "run_redraw_board",
    "plates_cast_retired",
    "run_redraw_plate_with_note",
    "run_spine_refresh",
    "run_unit",
    "voice_cast_id",
]
