"""Adopt a desk made by the retired internal kit so ``fictora-produce`` / ``fictora-ops`` can open it.

The internal kit (``fictora-drama`` ``scripts/content_ops``) left three things
this kit reads differently:

- Raw takes were ``epNN/takes/take-epNN-tK-vN.mp4``; this kit reads
  ``take-epNN-tK-raw-vN.mp4``. Each is hard-linked (copied when the disk
  cannot link) under the new name. Nothing is renamed, moved or deleted.
- The take jobs were one desk-root ``api/18_takes.json``; this kit reads
  ``epNN/api/17_raw_scene_clips.json``. One is built per episode from the
  rows' job id, URL, episode id and take index. The rows' ``provider_spec``
  is never copied, printed or looked at, and no ``provider-spec*.json`` file
  is opened.
- There was no ``production.json`` / ``production.config.json``. They are
  derived from ``series.json`` (its ``api`` block, spine id, session id, arc),
  ``api/spine.json`` and ``api/draft-request.json``; the phase and episode are
  inferred from the gates and from what was drawn and filmed.

It also copies ``api/spine.json`` into an episode's ``api/`` when that episode
has no spine snapshot (local post reads the episode's lines from there), and
backs ``series.json`` up once to ``series.pre-adopt.json`` before any write.

Only new files are created. An existing file is left alone; an existing
``production.json`` or ``production.config.json`` that differs from the derived
one stops the run unless ``force`` is given, which backs it up first. Running
twice changes nothing the second time.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

from creation.ops.folder import next_versioned_path
from creation.ops.state import EpisodeState, SeriesState, load_series, series_path
from creation.production_config import CONFIG_FILENAME, ProductionConfig
from creation.production_state import PRODUCTION_FILENAME, ProductionState

#: Where ``series.json`` is backed up once, before the first write.
SERIES_BACKUP_FILENAME = "series.pre-adopt.json"
#: The internal kit's desk-root take list.
INTERNAL_TAKES = Path("api") / "18_takes.json"
#: The take list this kit reads, per episode.
RAW_CLIPS_FILENAME = "17_raw_scene_clips.json"

#: Only an internal raw take: ``take-ep01-t1-v2.mp4``. Never a post step
#: (``-colour-v``, ``-mix-v``, ``-cap-v``, ``-deboard-v`` ...) and never a raw one.
INTERNAL_RAW_TAKE = re.compile(r"^take-ep(\d{2,})-t(\d+)-v(\d+)\.mp4$")
#: A clip's row fields this kit keeps; anything else on a row (``provider_spec``) is dropped unread.
_ROW_KEYS = ("episode_id", "take_index", "take_job_id", "video_url")
_TRAILING_NUMBER = re.compile(r"(\d+)$")

ActionKind = Literal["backup", "link", "copy_spine", "clips", "config", "production"]


class AdoptRefused(RuntimeError):
    """The desk cannot be adopted as asked; nothing was written."""


@dataclass(frozen=True)
class Action:
    """One file the adoption creates.

    Parameters
    ----------
    kind
        What it is.
    path
        File created (absolute).
    detail
        One line for the operator.
    source
        File it is made from, when it is a link, copy or backup.
    payload
        JSON written, when it is a JSON file.
    replaces
        True only for a ``--force`` replacement whose backup is planned just before it.
    """

    kind: ActionKind
    path: Path
    detail: str
    source: Path | None = None
    payload: dict[str, Any] | None = None
    replaces: bool = False


@dataclass
class AdoptPlan:
    """Everything the adoption would do on one desk.

    Parameters
    ----------
    desk
        Desk folder.
    actions
        Files to create, in write order.
    in_place
        Files already there as the adoption would make them.
    left_alone
        Files that exist and differ; never touched.
    inferred
        ``(name, value, source)`` for every derived value.
    confirm
        Lines the operator must confirm with the human.
    not_carried
        Internal fields this kit does not use (they stay in ``series.json``).
    """

    desk: Path
    actions: list[Action] = field(default_factory=list)
    in_place: list[str] = field(default_factory=list)
    left_alone: list[str] = field(default_factory=list)
    inferred: list[tuple[str, str, str]] = field(default_factory=list)
    confirm: list[str] = field(default_factory=list)
    not_carried: list[str] = field(default_factory=list)

    def rel(self, path: Path) -> str:
        """Return ``path`` relative to the desk when it is inside it."""

        try:
            return str(path.relative_to(self.desk))
        except ValueError:
            return str(path)

    def lines(self, *, dry_run: bool) -> list[str]:
        """Return the plan as printable lines.

        Parameters
        ----------
        dry_run
            Word the header as a plan (nothing written) or as done.

        Returns
        -------
        list[str]
            Report lines.
        """

        head = "dry run, nothing written" if dry_run else "adopted"
        out = [f"adopt-desk {self.desk} ({head})", "", "Inferred:"]
        width = max((len(name) for name, _, _ in self.inferred), default=0)
        out += [f"  {name.ljust(width)} = {value}  [{source}]" for name, value, source in self.inferred]
        verb = "Would create" if dry_run else "Created"
        out += ["", f"{verb} ({len(self.actions)}):"]
        out += [f"  + {self.rel(a.path)}  ({a.detail})" for a in self.actions] or ["  (nothing: the desk is already adopted)"]
        if self.in_place:
            out += ["", "Already in place:"] + [f"  = {item}" for item in self.in_place]
        if self.left_alone:
            out += ["", "Left alone (exists and differs; never overwritten):"] + [f"  ! {item}" for item in self.left_alone]
        if self.not_carried:
            out += ["", "Not carried into production.json (kept in series.json):"] + [f"  - {item}" for item in self.not_carried]
        if self.confirm:
            out += ["", "CONFIRM with the human before the next paid step:"] + [f"  ? {item}" for item in self.confirm]
        return out


def plan_adoption(desk: Path, *, force: bool = False) -> AdoptPlan:
    """Work out what adopting ``desk`` would create. Reads only; writes nothing.

    Parameters
    ----------
    desk
        A desk made by the internal kit (``series.json`` schema v1).
    force
        Replace a differing ``production.json`` / ``production.config.json``
        (each is backed up first).

    Returns
    -------
    AdoptPlan
        The plan.

    Raises
    ------
    FileNotFoundError
        When the desk has no ``series.json``.
    ValueError
        When ``series.json`` is not a v1 desk.
    AdoptRefused
        When ``production.json`` or its config exists, differs, and ``force`` is off.
    """

    desk = desk.expanduser().resolve()
    series = load_series(desk)
    raw_series = json.loads(series_path(desk).read_text(encoding="utf-8"))
    api_block = raw_series.get("api") if isinstance(raw_series.get("api"), Mapping) else {}
    spine = _read_json(desk / "api" / "spine.json")
    spine = dict(spine["spine"]) if isinstance(spine.get("spine"), Mapping) else spine
    draft = _read_json(desk / "api" / "draft-request.json")
    plan = AdoptPlan(desk=desk)

    backup = desk / SERIES_BACKUP_FILENAME
    if backup.exists():
        plan.in_place.append(SERIES_BACKUP_FILENAME)
    else:
        plan.actions.append(Action("backup", backup, "copy of series.json before any write", source=series_path(desk)))

    _plan_raw_links(plan, series)
    _plan_spine_copies(plan, series, spine)
    rows = _take_rows(desk)
    _plan_raw_clips(plan, rows, spine)

    config = _derive_config(plan, api_block, draft, spine)
    state = _derive_production(plan, series, raw_series, api_block, spine, draft, rows)
    _plan_json(plan, desk / CONFIG_FILENAME, "config", asdict(config), "desk API tuning", force=force)
    _plan_json(plan, desk / PRODUCTION_FILENAME, "production", asdict(state), f"phase {state.phase}, episode {state.episode_ordinal}", force=force)
    _note_not_carried(plan, raw_series, api_block)
    return plan


def apply_adoption(plan: AdoptPlan) -> list[Path]:
    """Create every file in ``plan``. Never overwrites: each target is created exclusively.

    Parameters
    ----------
    plan
        From :func:`plan_adoption`.

    Returns
    -------
    list[Path]
        Files created.

    Raises
    ------
    FileExistsError
        When a target appeared after the plan was made (nothing is overwritten).
    """

    made: list[Path] = []
    for action in plan.actions:
        action.path.parent.mkdir(parents=True, exist_ok=True)
        if action.kind == "link":
            assert action.source is not None
            _link_or_copy(action.source, action.path)
        elif action.kind in {"backup", "copy_spine"}:
            assert action.source is not None
            _copy_exclusive(action.source, action.path)
        else:
            assert action.payload is not None
            if action.replaces and not any(done.name.startswith(action.path.stem + ".pre-adopt") for done in made):
                raise FileExistsError(f"{action.path} would be replaced without its backup; nothing overwritten")
            with action.path.open("w" if action.replaces else "x", encoding="utf-8") as handle:
                handle.write(json.dumps(action.payload, ensure_ascii=False, indent=2) + "\n")
        made.append(action.path)
    return made


def adopt_desk(desk: Path, *, dry_run: bool = False, force: bool = False) -> tuple[AdoptPlan, list[Path]]:
    """Plan the adoption of ``desk`` and, unless ``dry_run``, carry it out.

    Parameters
    ----------
    desk
        Desk made by the internal kit.
    dry_run
        Only plan.
    force
        See :func:`plan_adoption`.

    Returns
    -------
    tuple[AdoptPlan, list[Path]]
        The plan and the files created (empty on a dry run).
    """

    plan = plan_adoption(desk, force=force)
    return plan, ([] if dry_run else apply_adoption(plan))


# --- raw takes -------------------------------------------------------------------------


def internal_raw_takes(takes_dir: Path) -> list[tuple[Path, str]]:
    """Return the internal kit's raw takes in ``takes_dir`` with this kit's name for each.

    Parameters
    ----------
    takes_dir
        ``epNN/takes``.

    Returns
    -------
    list[tuple[Path, str]]
        ``(take-epNN-tK-vN.mp4, take-epNN-tK-raw-vN.mp4)``, sorted.
    """

    if not takes_dir.is_dir():
        return []
    found: list[tuple[Path, str]] = []
    for path in sorted(takes_dir.iterdir()):
        match = INTERNAL_RAW_TAKE.match(path.name)
        if match and path.is_file():
            episode, take, version = match.groups()
            found.append((path, f"take-ep{episode}-t{take}-raw-v{version}.mp4"))
    return found


def _plan_raw_links(plan: AdoptPlan, series: SeriesState) -> None:
    for episode in series.episodes:
        for source, name in internal_raw_takes(plan.desk / episode.slug / "takes"):
            target = source.with_name(name)
            if not target.exists():
                plan.actions.append(Action("link", target, f"hardlink of {source.name}", source=source))
            elif _same_file(source, target):
                plan.in_place.append(plan.rel(target))
            else:
                plan.left_alone.append(f"{plan.rel(target)} (not the same video as {source.name})")


def _same_file(a: Path, b: Path) -> bool:
    try:
        if a.samefile(b):
            return True
    except OSError:
        return False
    return a.stat().st_size == b.stat().st_size and filecmp.cmp(a, b, shallow=False)


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError:
        _copy_exclusive(source, target)


def _copy_exclusive(source: Path, target: Path) -> None:
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
    shutil.copystat(source, target)


# --- spine and take jobs -------------------------------------------------------------


def _plan_spine_copies(plan: AdoptPlan, series: SeriesState, spine: dict[str, Any]) -> None:
    source = plan.desk / "api" / "spine.json"
    if not spine.get("beats"):
        return
    listed = {_episode_ordinal(spine, str(beat.get("episode_id") or "")) for beat in spine["beats"] if isinstance(beat, Mapping)}
    for episode in series.episodes:
        api_dir = plan.desk / episode.slug / "api"
        if episode.ordinal not in listed or not (plan.desk / episode.slug).is_dir():
            continue
        if any(api_dir.glob("*spine*.json")):
            plan.in_place.append(f"{episode.slug}/api/*spine*.json")
            continue
        plan.actions.append(Action("copy_spine", api_dir / "spine.json", "copy of api/spine.json (local post reads the lines here)", source=source))


def _take_rows(desk: Path) -> list[dict[str, Any]]:
    """The internal take list, each row cut to the fields this kit keeps (``provider_spec`` never read)."""

    path = desk / INTERNAL_TAKES
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw if isinstance(raw, list) else []
    return [{key: row.get(key) for key in _ROW_KEYS} for row in rows if isinstance(row, Mapping)]


def _plan_raw_clips(plan: AdoptPlan, rows: list[dict[str, Any]], spine: dict[str, Any]) -> None:
    by_episode: dict[int, dict[int, dict[str, Any]]] = {}
    for row in rows:
        ordinal = _episode_ordinal(spine, str(row.get("episode_id") or ""))
        if ordinal is None or not row.get("take_job_id") or not row.get("video_url"):
            plan.confirm.append(f"api/18_takes.json row {row.get('episode_id')!r} take {row.get('take_index')!r} could not be mapped (no episode, job or URL)")
            continue
        index = int(row.get("take_index") or 1)
        by_episode.setdefault(ordinal, {})[index] = row  # a later row for the same take is the newer film
    for ordinal, takes in sorted(by_episode.items()):
        api_dir = plan.desk / f"ep{ordinal:02d}" / "api"
        clips = [
            {
                "job_id": str(row["take_job_id"]),
                "url": str(row["video_url"]),
                "relation_id": None,
                "set_index": index,
                "episode_id": str(row["episode_id"]),
            }
            for index, row in sorted(takes.items())
        ]
        payload = {"coordinator_job_id": _coordinator_for(api_dir, clips), "clips": clips}
        target = api_dir / RAW_CLIPS_FILENAME
        if not target.exists():
            plan.actions.append(Action("clips", target, f"{len(clips)} take job(s) from api/18_takes.json", payload=payload))
        elif _read_json(target) == payload:
            plan.in_place.append(plan.rel(target))
        else:
            plan.left_alone.append(f"{plan.rel(target)} (this kit already wrote its own)")


def _coordinator_for(api_dir: Path, clips: list[dict[str, Any]]) -> str | None:
    """The video job whose scenes carry the clip URLs (from ``video-terminal-*.json``), if any."""

    urls = {clip["url"] for clip in clips}
    for path in sorted(api_dir.glob("video-terminal-*.json"), reverse=True):
        body = _read_json(path)
        output = body.get("output") if isinstance(body.get("output"), Mapping) else {}
        scenes = output.get("scenes") if isinstance(output.get("scenes"), list) else []
        if any(isinstance(scene, Mapping) and scene.get("video_url") in urls for scene in scenes) and body.get("job_id"):
            return str(body["job_id"])
    return None


def _episode_ordinal(spine: Mapping[str, Any], episode_id: str) -> int | None:
    """Ordinal of an API episode id (``episode_01`` or ``ep_02``): the spine's summaries first, else its number."""

    for summary in spine.get("episode_summaries") or []:
        if isinstance(summary, Mapping) and summary.get("episode_id") == episode_id:
            for key in ("ordinal", "episode_ordinal"):
                value = summary.get(key)
                if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
                    return int(value)
    match = _TRAILING_NUMBER.search(episode_id)
    return int(match.group(1)) if match else None


# --- production.json / production.config.json -----------------------------------------


def _derive_config(plan: AdoptPlan, api_block: Mapping[str, Any], draft: Mapping[str, Any], spine: Mapping[str, Any]) -> ProductionConfig:
    config = ProductionConfig()
    tempo, source = _first(("series.json api.cut_tempo", api_block.get("cut_tempo")), ("api/draft-request.json", draft.get("cut_tempo")), ("api/spine.json", spine.get("cut_tempo")))
    config.cut_tempo = tempo
    plan.inferred.append(("config.cut_tempo", str(tempo), source))
    language, source = _first(("series.json api.spoken_language", api_block.get("spoken_language")), ("api/draft-request.json", draft.get("spoken_language")), ("api/spine.json", spine.get("spoken_language")))
    config.spoken_language = None if not language or str(language).lower().startswith("en") else str(language)
    plan.inferred.append(("config.spoken_language", str(config.spoken_language), f"{source}: {language!r}" if language else source))
    if draft.get("locale"):
        config.locale = str(draft["locale"])
        plan.inferred.append(("config.locale", config.locale, "api/draft-request.json"))
    return config


def _derive_production(
    plan: AdoptPlan,
    series: SeriesState,
    raw_series: Mapping[str, Any],
    api_block: Mapping[str, Any],
    spine: Mapping[str, Any],
    draft: Mapping[str, Any],
    rows: list[dict[str, Any]],
) -> ProductionState:
    def note(name: str, value: Any, source: str) -> None:
        plan.inferred.append((name, str(value), source))

    session_id, source = _first(("series.json api_session_id", raw_series.get("api_session_id")))
    if not session_id:
        session_id, source = f"content-ops-{series.slug[:24]}-{_short_hash(series.slug)}", "made up (no api_session_id)"
        plan.confirm.append("series.json has no api_session_id; a new session id was made (server-side session history starts fresh)")
    note("session_id", session_id, source)

    prompt, source = _first(("api/draft-request.json prompt", draft.get("prompt")))
    if not prompt:
        prompt, source = series.title, "series title (no api/draft-request.json)"
        plan.confirm.append("no api/draft-request.json prompt; production.json carries the series title as the prompt")
    first_line = next((line.strip() for line in str(prompt).splitlines() if line.strip()), "")
    note("prompt", f"{len(str(prompt))} chars, starts {first_line[:60]!r}", source)

    preset_id, source = _first(("series.json api.preset_id", api_block.get("preset_id")), ("api/draft-request.json", draft.get("art_style_preset_id")), ("api/spine.json", spine.get("art_style_preset_id")))
    note("preset_id", preset_id, source)
    if not preset_id:
        plan.confirm.append("no preset id anywhere on the desk; set preset_id in production.json before any draw")
    version, v_source = _first(("series.json api.preset_version", api_block.get("preset_version")), ("api/spine.json art_style_preset_version", spine.get("art_style_preset_version")))
    note("preset_version", version, v_source)
    if not version:
        plan.confirm.append("no preset version on the desk; set preset_version in production.json before any draw")

    lane, l_source = _first(("series.json api.video_lane", api_block.get("video_lane")), ("api/spine.json", spine.get("video_lane")))
    lane = lane or "minimax-h3"
    note("video_lane", lane, l_source if l_source != "missing" else "default")

    spine_id, s_source = _first(("series.json spine_id", raw_series.get("spine_id")), ("api/spine.json", spine.get("spine_id")))
    note("spine_id", spine_id, s_source)

    prefix, p_source = _first(("series.json api.idempotency_prefix", api_block.get("idempotency_prefix")))
    if not prefix:
        prefix, p_source = f"{series.slug[:24]}-{_short_hash(series.slug + str(spine_id))}", "made up (none on the desk)"
    note("idempotency_prefix", prefix, p_source)

    state = ProductionState(
        session_id=str(session_id),
        prompt=str(prompt),
        preset_id=str(preset_id or ""),
        preset_version=str(version or ""),
        video_lane=str(lane),
        band=series.band,
        spine_id=str(spine_id) if spine_id else None,
        idempotency_prefix=str(prefix),
    )
    pending = api_block.get("pending")
    if isinstance(pending, Mapping):
        state.pending = {str(k): dict(v) for k, v in pending.items() if isinstance(v, Mapping)}
        note("pending", ", ".join(f"{k} (job {v.get('job_id')})" for k, v in state.pending.items()) or "none", "series.json api.pending")
    attempts = api_block.get("attempts")
    if isinstance(attempts, Mapping):
        state.attempts = {str(k): int(v) for k, v in attempts.items() if isinstance(v, int)}
        note("attempts", f"{len(state.attempts)} unit(s)", "series.json api.attempts")
    arcs = raw_series.get("arc_options")
    if isinstance(arcs, list):
        state.arc_options = [
            {"arc_id": str(a.get("arc_id") or ""), "title": str(a.get("title") or ""), "line": str(a.get("line") or "")}
            for a in arcs
            if isinstance(a, Mapping)
        ]
        note("arc_options", f"{len(state.arc_options)} arc(s)", "series.json arc_options")
    kept = raw_series.get("series_arc")
    if isinstance(kept, Mapping):
        inner = kept.get("arc") if isinstance(kept.get("arc"), Mapping) else {}
        state.series_arc = {
            **{key: str(inner.get(key) or kept.get(key) or "") for key in ("arc_id", "title", "line")},
            "option": kept.get("option"),
            "rewritten": bool(kept.get("rewritten")),
            "spine_version": str(kept.get("spine_version") or ""),
        }
        note("series_arc", f"option {kept.get('option')}: {state.series_arc['title']!r}", "series.json series_arc")

    _infer_phase(plan, state, series, rows)
    note("episode_ordinal", state.episode_ordinal, "episode slots in series.json")
    note("phase", state.phase, "gates in series.json + boards/takes on disk")
    return state


def _infer_phase(plan: AdoptPlan, state: ProductionState, series: SeriesState, rows: list[dict[str, Any]]) -> None:
    """Point the phase machine at the newest episode with any work on it.

    The phase is the earliest one that agrees with the paid work on disk: a
    desk record that says an earlier gate is still open never sends the machine
    back to pay for a board or take that is already there; it becomes a
    CONFIRM line instead. Where the desk cannot tell two phases apart, the
    earlier one is chosen (an approved board with no take waits on a fresh,
    free estimate, not on the spend yes).
    """

    active = [episode for episode in series.episodes if _has_work(plan.desk, episode)]
    episode = active[-1] if active else series.episodes[0]
    state.episode_ordinal = episode.ordinal
    tag = episode.slug
    boards = {take.take_id: _newest_board(plan.desk, episode, take.take_id) for take in episode.takes}
    filmed = [take for take in episode.takes if take.filmed_count > 0 or _has_raw(plan.desk, episode, take.take_id)]
    board_yes = [take for take in episode.takes if take.board.status == "approved"]

    if not state.spine_id:
        state.phase = "new"
        if active:
            plan.confirm.append("no spine id on the desk but work is on disk; phase is `new`, so `step` would draft again")
        return
    first = episode.ordinal == 1 and not series.continuing
    if first and series.plates.status != "approved" and not (board_yes or filmed or any(boards.values())):
        plates_on_disk = any((plan.desk / "shared" / "plates").glob("*.png")) or any((plan.desk / tag / "plates").glob("*.png"))
        state.phase = "wait_plates" if plates_on_disk else "ready_cast_enrol"
        return

    open_gates = []
    if first and series.plates.status != "approved":
        open_gates.append("plates")
    if episode.script.status != "approved":
        open_gates.append("script")

    if filmed:
        state.phase = "complete"
        clip = next((row for row in rows if _row_is(row, episode.ordinal)), None)
        state.last_delivery_url = str(clip["video_url"]) if clip else None
        state.last_video_job_id = _coordinator_for(plan.desk / tag / "api", [{"url": clip["video_url"]}]) if clip else None
        missing = [take.take_id for take in episode.takes if take not in filmed]
        if missing:
            plan.confirm.append(f"{tag} {', '.join(missing)} not filmed yet; film each alone (`film --episode {episode.ordinal} --take tK`), never the whole episode")
        if open_gates:
            plan.confirm.append(
                f"series.json says {tag} {' and '.join(open_gates)} is still pending, but {tag} was filmed. "
                f"Phase set to `complete` (nothing is drawn or filmed again). If the human did say yes, record it: "
                f"`fictora-ops approve --desk <desk> --gate script --episode {episode.ordinal}`"
            )
        unverdicted = [take.take_id for take in filmed if take.verdict == "pending"]
        if unverdicted:
            plan.confirm.append(f"{tag} {', '.join(unverdicted)} has no Use it / Change this yet; ask before `finish`")
        return

    if any(boards.values()) or board_yes:
        state.board_paths = {take_id: plan.rel(path) for take_id, path in boards.items() if path is not None}
        if len(board_yes) == len(episode.takes):
            state.phase = "ready_estimate"
            if any(take.estimate_usd is not None for take in episode.takes):
                plan.confirm.append(f"{tag} has an estimate from the old kit; `step` asks the server for a fresh one (free) before the spend yes")
        else:
            state.phase = "wait_board"
            unboarded = [take_id for take_id, path in boards.items() if path is None]
            if unboarded:
                plan.confirm.append(f"{tag} {', '.join(unboarded)} has no board on disk; `approve --gate board` needs one per take")
        if open_gates:
            plan.confirm.append(f"series.json says {tag} {' and '.join(open_gates)} is still pending, but boards were drawn; ask the human")
        return

    if episode.script.status == "approved":
        state.phase = "ready_boards_enrol"
        stale = [unit for unit, entry in state.pending.items() if unit.startswith(f"boards-{tag}")]
        if stale:
            plan.confirm.append(
                f"the old kit left {', '.join(stale)} unfinished (no board on disk); `step` enrols {tag}'s boards (paid) once the human says go"
            )
        return
    state.phase = "wait_script"


def _has_work(desk: Path, episode: EpisodeState) -> bool:
    if episode.script.status == "approved" or any(take.lines for take in episode.takes):
        return True
    if any(take.board.status == "approved" or take.filmed_count for take in episode.takes):
        return True
    return any(_newest_board(desk, episode, take.take_id) or _has_raw(desk, episode, take.take_id) for take in episode.takes)


def _newest_board(desk: Path, episode: EpisodeState, take_id: str) -> Path | None:
    boards = desk / episode.slug / "boards"
    found = [p for p in boards.glob(f"board-{episode.slug}-{take_id}*.png") if p.is_file()]
    return max(found, key=lambda p: (_version(p), p.name)) if found else None


def _has_raw(desk: Path, episode: EpisodeState, take_id: str) -> bool:
    takes = desk / episode.slug / "takes"
    prefix = f"take-{episode.slug}-{take_id}-"
    return any(source.name.startswith(prefix) for source, _ in internal_raw_takes(takes)) or any(
        takes.glob(f"take-{episode.slug}-{take_id}-raw-v*.mp4")
    )


def _row_is(row: Mapping[str, Any], ordinal: int) -> bool:
    match = _TRAILING_NUMBER.search(str(row.get("episode_id") or ""))
    return bool(match and int(match.group(1)) == ordinal and row.get("video_url"))


def _version(path: Path) -> int:
    match = re.search(r"-v(\d+)$", path.stem)
    return int(match.group(1)) if match else 0


def _plan_json(plan: AdoptPlan, path: Path, kind: ActionKind, payload: dict[str, Any], detail: str, *, force: bool) -> None:
    if not path.exists():
        plan.actions.append(Action(kind, path, detail, payload=payload))
        return
    if _read_json(path) == payload:
        plan.in_place.append(plan.rel(path))
        return
    if not force:
        raise AdoptRefused(
            f"{path.name} already exists and differs from what adoption derives; nothing was written. "
            "The desk may already run on this kit. Pass --force to replace it (it is backed up first)."
        )
    backup = next_versioned_path(path.parent, path.name.removesuffix(".json") + ".pre-adopt", ".json")
    plan.actions.append(Action("backup", backup, f"backup of {path.name} before --force", source=path))
    plan.actions.append(Action(kind, path, f"{detail} (replaces the backed-up file)", payload=payload, replaces=True))


def _note_not_carried(plan: AdoptPlan, raw_series: Mapping[str, Any], api_block: Mapping[str, Any]) -> None:
    used_api = {"idempotency_prefix", "preset_id", "preset_version", "cut_tempo", "video_lane", "spoken_language", "pending", "attempts"}
    plan.not_carried += [f"api.{key}" for key in api_block if key not in used_api]
    used_top = {"spine_id", "api_session_id", "arc_options", "series_arc", "api"}
    plan.not_carried += [key for key in raw_series if key not in used_top and key not in _modelled_series_keys()]


def _modelled_series_keys() -> set[str]:
    return {item.name for item in fields(SeriesState)}


# --- small helpers -----------------------------------------------------------------------


def _first(*candidates: tuple[str, Any]) -> tuple[Any, str]:
    for source, value in candidates:
        if value not in (None, ""):
            return value, source
    return None, "missing"


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}
