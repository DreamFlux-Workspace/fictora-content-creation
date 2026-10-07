"""``collect-takes``: collect the filmed takes of a film job that stopped moving. Spends nothing.

A film job can stall (NOCLIP ep 1, 6 Oct 2026: stuck at 50 % for an hour
while its three take jobs had finished). ``film`` and ``step`` wait only for
the film job itself to finish, so the takes sat on the server, paid for and
unused. By hand the operator then had to write the clip record in ``api/``
and refresh each take's facts before ``finish`` would lay sound effects
(L-20261006-4).

This command reads the desk's current film job and its take jobs (GET only)
and collects the takes only when:

* every take the episode asked for is filmed (counted against the takes the
  film asked for, never the film job's own list, which fills in as filming goes,
  L-20260925-1), and
* the film job has not moved for :data:`COLLECT_AFTER_IDLE_SECONDS` (its own
  ``updated_at``), or it ended failed / cancelled after every take was filmed.

It then writes the same clip record the film path writes
(:func:`creation.harness.raw_video.raw_clips_record`), collects and books the
takes through the film path's own code, marks the desk as the film path does,
fetches any take facts that collect did not bring, and says on the desk and in
its output that the takes came from a stuck job, not a completed one.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import httpx

from creation.harness.http_util import _parse_utc, describe_job_error
from creation.harness.raw_video import (
    COLLECT_AFTER_IDLE_SECONDS,
    _job_record,
    clip_from_child,
    raw_clips_name,
    raw_clips_record,
    video_generation_failure,
)
from creation.ops.state import episode_by_ordinal, load_series
from creation.production_state import (
    ProductionState,
    api_dir_for_episode,
    load_production,
    save_production,
)

_FILM_UNIT = re.compile(r"^film-(ep(\d+)(?:-(t\d+))?)(?:-s\d+)?$")
_ENROL_RECORD = re.compile(r"^(film-.+)-enrol(?:-v\d+)?\.json$")
_OVER = frozenset({"failed", "cancelled"})
_STILL_FILMING = frozenset({"queued", "running", ""})


@dataclass(frozen=True)
class FilmJob:
    """The desk's film job: which episode and takes it filmed, and where its clip record goes."""

    job_id: str
    episode: int
    take_ids: tuple[str, ...]
    #: The ``film`` unit (``film-ep02-t2-s2``); ``None`` for the ``step``'s own film.
    unit: str | None = None
    #: Its scope key (``ep02-t2``); ``None`` for the step.
    key: str | None = None
    #: ``tK`` for a one-take film.
    take_id: str | None = None

    @property
    def record_name(self) -> str:
        """The clip record's file name in ``epNN/api`` (the one the film path writes)."""

        return raw_clips_name(self.unit)

    @property
    def what(self) -> str:
        """``episode 2`` or ``ep02 t2``."""

        return (
            f"ep{self.episode:02d} {self.take_id}"
            if self.take_id
            else f"episode {self.episode}"
        )


@dataclass
class JobReading:
    """What the server says about the film job and each take job it lists."""

    status: str
    progress: Any
    updated_at: datetime | None
    error: str | None
    children: list[str]
    clips: list[dict[str, Any]] = field(default_factory=list)
    filming: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Verdict:
    """Collect or not, and why, in plain words."""

    collect: bool
    why: str


def _step_film_job(desk: Path, state: ProductionState) -> FilmJob | None:
    """The ``step``'s film job when the desk is waiting on it (or stopped while waiting on it)."""

    waiting = state.phase == "ready_video" or (
        state.phase == "failed" and state.failed_phase == "ready_video"
    )
    if not waiting:
        return None
    # Only the job enrolled with the desk's current retry suffix (as ``step`` resumes it).
    enrolled = state.video_enrolled_suffix or ""
    if enrolled != state.video_idempotency_suffix:
        return None
    path = api_dir_for_episode(desk, state.episode_ordinal) / "16_video_enrol.json"
    try:
        enrol = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    job_id = enrol.get("job_id") if isinstance(enrol, dict) else None
    if not isinstance(job_id, str) or not job_id.strip():
        return None
    slot = episode_by_ordinal(load_series(desk), state.episode_ordinal)
    return FilmJob(
        job_id=job_id.strip(),
        episode=state.episode_ordinal,
        take_ids=tuple(take.take_id for take in slot.takes),
    )


def _unit_film_job(desk: Path, unit: str, job_id: str) -> FilmJob | None:
    """A ``film`` unit's job, with the takes that film asked for."""

    from creation.episode_commands import _film_scope

    match = _FILM_UNIT.match(unit)
    if not match:
        return None
    episode, take_id = int(match.group(2)), match.group(3)
    take_ids, _, key = _film_scope(desk, episode=episode, take_id=take_id)
    return FilmJob(
        job_id=job_id,
        episode=episode,
        take_ids=tuple(take_ids),
        unit=unit,
        key=key,
        take_id=take_id,
    )


def _enrolled_film_units(desk: Path) -> dict[str, str]:
    """``job_id -> unit`` from the ``film`` enrol records saved in ``desk/api``."""

    from creation.episode_commands import admitted_job_id

    found: dict[str, str] = {}
    folder = desk / "api"
    if not folder.is_dir():
        return found
    for path in sorted(folder.iterdir()):
        match = _ENROL_RECORD.match(path.name)
        if not match:
            continue
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        job = admitted_job_id(body) if isinstance(body, dict) else ""
        if job:
            found[job] = match.group(1)
    return found


def find_film_job(
    desk: Path, state: ProductionState, *, job_id: str | None = None
) -> FilmJob:
    """The desk's current film job: the ``step``'s, or a ``film`` the desk is still waiting on.

    With ``job_id``, that job, also when ``film`` already let it go after the
    server failed it (its enrol record on the desk says which film it was).

    Raises
    ------
    CommandStopped
        When the desk waits on no film job, or on more than one and none was named.
    """

    from creation.episode_commands import CommandStopped

    candidates: list[FilmJob] = []
    step = _step_film_job(desk, state)
    if step is not None:
        candidates.append(step)
    for unit, entry in state.pending.items():
        pending_job = entry.get("job_id") if isinstance(entry, dict) else None
        if unit.startswith("film-") and pending_job:
            found = _unit_film_job(desk, unit, str(pending_job))
            if found is not None:
                candidates.append(found)
    if job_id:
        named = [job for job in candidates if job.job_id == job_id]
        if named:
            return named[0]
        unit = _enrolled_film_units(desk).get(job_id)
        found = _unit_film_job(desk, unit, job_id) if unit else None
        if found is None:
            raise CommandStopped(
                f"film job {job_id} is not a film this desk started (no enrol record in api/ names it); "
                "nothing was read or written"
            )
        return found
    if not candidates:
        raise CommandStopped(
            "this desk is not waiting on a film job: nothing to collect. If a film job the desk let go "
            "(the server failed it) has every take filmed, pass its id from the run notes with --job-id"
        )
    if len(candidates) > 1:
        raise CommandStopped(
            "this desk waits on more than one film job; name one with --job-id: "
            + "; ".join(f"{job.job_id} ({job.what})" for job in candidates)
        )
    return candidates[0]


def read_film_job(run: Any, job_id: str) -> JobReading:
    """Read the film job and every take job it lists (GET only).

    A failed or cancelled answer from ``/v1/video-generations/{id}`` counts as
    the film job's status (``/v1/jobs`` can lag behind it).
    """

    parent = _job_record(run.get(f"/v1/jobs/{job_id}"))
    status = str(parent.get("status") or "")
    error: str | None = describe_job_error(parent) if status in _OVER else None
    generation = video_generation_failure(run, job_id)
    if generation is not None and status not in _OVER:
        status = str(generation.get("status") or "failed")
        error = describe_job_error(generation)
    depends = parent.get("depends_on")
    children = (
        [str(item) for item in depends if item] if isinstance(depends, list) else []
    )
    reading = JobReading(
        status=status,
        progress=parent.get("progress"),
        updated_at=_parse_utc(parent.get("updated_at")),
        error=error,
        children=children,
    )
    for child_id in children:
        child = _job_record(run.get(f"/v1/jobs/{child_id}"))
        child_status = str(child.get("status") or "")
        if child_status in _OVER:
            reading.failed.append(f"{child_id} ({describe_job_error(child)})")
            continue
        clip = (
            None if child_status in _STILL_FILMING else clip_from_child(child_id, child)
        )
        if clip is None:
            reading.filming.append(child_id)
        else:
            reading.clips.append(clip)
    return reading


def _hhmm(moment: datetime) -> str:
    return f"{moment.astimezone(timezone.utc):%H:%M} UTC"


def decide(reading: JobReading, *, expected: int, now: datetime) -> Verdict:
    """Collect only a film job whose every asked-for take is filmed and which has stopped (or ended failed).

    Parameters
    ----------
    reading
        :func:`read_film_job`.
    expected
        How many takes the film asked for (never the film job's own, growing list).
    now
        The time now (UTC).

    Returns
    -------
    Verdict
        Whether to collect, and why (or why not yet, and when to try again).
    """

    over = reading.status in _OVER
    if reading.failed:
        return Verdict(
            False,
            "a take job failed: "
            + "; ".join(reading.failed)
            + ". This is not a stuck film with every take "
            "filmed, so there is nothing to collect. The failed take needs a new film (paid, after the human's yes)",
        )
    if reading.status == "completed":
        return Verdict(
            False,
            "the film job finished normally. Pick it up with the command that started it: `step` (or "
            "`film ... --confirm-spend` again); it reads the same job and nothing is charged twice",
        )
    if len(reading.children) > expected:
        return Verdict(
            False,
            f"the film job lists {len(reading.children)} take jobs but this film asked for {expected}. "
            "The kit cannot tell which belong here: tell engineering with the film job id",
        )
    filmed = len(reading.clips)
    if filmed < expected:
        listed = (
            f"; the film job lists only {len(reading.children)} take job(s) so far"
            if len(reading.children) < expected
            else ""
        )
        still = (
            f" ({', '.join(reading.filming)} still filming)" if reading.filming else ""
        )
        if over:
            return Verdict(
                False,
                f"the film job ended {reading.status} with {filmed} of {expected} take(s) filmed{still}{listed}: "
                f"{reading.error or 'no reason given'}. Only a film with every take filmed is collected; the "
                "missing takes need a new film (paid, after the human's yes)",
            )
        return Verdict(
            False,
            f"{filmed} of {expected} take(s) are filmed{still}{listed}. Filming is still going: try again in "
            f"about {int(COLLECT_AFTER_IDLE_SECONDS // 60)} minutes",
        )
    if over:
        return Verdict(
            True,
            f"the film job ended {reading.status} after all {expected} take(s) were filmed "
            f"({reading.error or 'no reason given'})",
        )
    if reading.updated_at is None:
        return Verdict(
            False,
            "every take is filmed, but the server gave no last-update time for the film job, so the kit "
            "cannot tell that it has stopped. Check `fictora-produce status` and tell engineering with the "
            "film job id",
        )
    idle = now - reading.updated_at
    minutes = int(idle.total_seconds() // 60)
    where = f"{reading.status or 'no status'} at {reading.progress or 0}%"
    if idle.total_seconds() >= COLLECT_AFTER_IDLE_SECONDS:
        return Verdict(
            True,
            f"all {expected} take(s) are filmed and the film job has not moved for {minutes} min "
            f"({where}, last update {_hhmm(reading.updated_at)})",
        )
    retry_at = reading.updated_at + timedelta(seconds=COLLECT_AFTER_IDLE_SECONDS)
    return Verdict(
        False,
        f"all {expected} take(s) are filmed, but the film job last moved {minutes} min ago ({where}) and "
        "may still finish on its own. If it has not moved by "
        f"{_hhmm(retry_at)}, run collect-takes again",
    )


def _take_lines(reading: JobReading) -> list[str]:
    return [
        f"  take {clip.get('set_index') or '?'}: take job `{clip['job_id']}` {clip['url']}"
        for clip in reading.clips
    ]


def run_collect_takes(
    desk: Path,
    *,
    job_id: str | None = None,
    dry_run: bool = False,
    out: Any = None,
    now: datetime | None = None,
) -> str:
    """Collect the filmed takes of the desk's stuck film job, or say why not yet. Spends nothing.

    Parameters
    ----------
    desk
        Series desk.
    job_id
        The film job, when the desk waits on more than one or ``film`` let it go.
    dry_run
        Say what would be collected; write nothing.
    out
        Text stream.
    now
        The time now (tests).

    Returns
    -------
    str
        The printed summary.

    Raises
    ------
    CommandStopped
        When there is nothing to collect yet, with why and when to try again.
    """

    from creation import episode_commands, orchestrate
    from creation.episode_commands import (
        CommandStopped,
        book_film_unit,
        run_take_facts,
    )
    from creation.post.sfx import saved_take_facts
    from creation.production_config import load_production_config

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    state = load_production(desk)
    if not state.spine_id:
        raise CommandStopped(
            "this desk has no story on the server yet: nothing was filmed"
        )
    job = find_film_job(desk, state, job_id=job_id)
    run = episode_commands._open_run(desk, replace(state, episode_ordinal=job.episode))
    try:
        try:
            reading = read_film_job(run, job.job_id)
        except httpx.HTTPError as exc:
            raise CommandStopped(
                f"could not reach the server to read film job {job.job_id} ({exc.__class__.__name__}). "
                "Nothing was written; try again in a minute"
            ) from exc
        verdict = decide(
            reading, expected=len(job.take_ids), now=now or datetime.now(timezone.utc)
        )
        header = (
            f"Film job {job.job_id} ({job.what}, {len(job.take_ids)} take(s) asked for)"
        )
        if not verdict.collect:
            raise CommandStopped(
                f"{header}: not collected: {verdict.why}. Nothing was written."
            )
        if dry_run:
            lines = [
                f"{header}: would collect from a stuck job: {verdict.why}.",
                *_take_lines(reading),
                f"Would write ep{job.episode:02d}/api/{job.record_name}, download and book the take(s), "
                "save their take facts and mark the film done. Dry run: nothing was written.",
            ]
            text = "\n".join(lines)
            print(text, file=out)
            return text
        collected_at = datetime.now(timezone.utc)
        stuck = {
            "by": "collect-takes",
            "why": verdict.why,
            "film_job_status": reading.status,
            "film_job_progress": reading.progress,
            "film_job_updated_at": reading.updated_at.isoformat()
            if reading.updated_at
            else None,
            "collected_at": collected_at.isoformat(),
        }
        raw = raw_clips_record(
            job.job_id, reading.clips, collected_from_stuck_job=stuck
        )
        run.save(job.record_name, raw)
        facts_before = {
            take: saved_take_facts(desk, job.episode, take) for take in job.take_ids
        }
        if job.unit is None:
            fresh = load_production(desk)
            fresh.failed_phase = None
            fresh.last_error = None
            save_production(desk, fresh)
            summary = orchestrate.book_filmed_episode(
                desk, run, fresh, raw, cfg=load_production_config(desk), paths=[]
            ).message
        else:
            summary = book_film_unit(
                desk,
                run,
                load_production(desk),
                raw,
                episode=job.episode,
                unit=job.unit,
                key=job.key or "",
                take_ids=list(job.take_ids),
                take_id=job.take_id,
                job_id=job.job_id,
                out=StringIO(),
            )
    finally:
        run.client.close()
    refreshed: list[str] = []
    facts_out = StringIO()
    for take in job.take_ids:
        if saved_take_facts(desk, job.episode, take) != facts_before[take]:
            continue  # collecting just saved them
        try:
            run_take_facts(
                desk, episode=job.episode, take_id=take, refresh=True, out=facts_out
            )
            refreshed.append(f"{take}: take facts refreshed")
        except CommandStopped as exc:
            refreshed.append(f"!! {take}: take facts not refreshed: {exc}")
    note = (
        f"COLLECTED FROM A STUCK FILM JOB (not a completed one): film job `{job.job_id}` ({job.what}); "
        f"{verdict.why}. `collect-takes` wrote `{job.record_name}` (marked collected_from_stuck_job) and "
        "booked the takes the film path's way; nothing new was filmed or charged. Tell engineering with the "
        "film job id."
    )
    episode_commands._note(desk, job.episode, note)
    lines = [
        f"{header}: COLLECTED FROM A STUCK JOB, not a completed one: {verdict.why}.",
        *_take_lines(reading),
        f"Wrote ep{job.episode:02d}/api/{job.record_name} (the clip record the film path writes). "
        "Nothing new was filmed or charged.",
        summary,
        *refreshed,
        "Tell engineering the film job id: the job itself never finished.",
    ]
    text = "\n".join(line for line in lines if line)
    print(text, file=out)
    return text
