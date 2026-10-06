"""The reel of a desk created on or after 6 Oct 2026: cut by the server's reel engine ($0).

Rules epoch (6 Oct 2026): a desk made before that day keeps the local reel
exactly as it was (:mod:`creation.post.reel`, frozen for those desks, bug fixes
included); a newer desk's ``fictora-produce reel`` and its reel after every
``finish`` / edit come here. :func:`creation.post.reel.run_reel` and
:func:`creation.post.reel.auto_reel` send a new desk here (:func:`legacy_desk`).

The reel is made by the Drama API's reel route in operator mode
(:mod:`creation.post.reel_server`): the same engine and rules as the app's
"Share as reel" (``fictora-drama`` ``docs/reels/reel-rules.md``), ffmpeg on the
server, no model, nothing paid. This module finds on the desk what only the
desk knows, sends it, and keeps the episode folder's books with the same
helpers as the local reel (``latest.json``, hand-edited plans, ``metrics.csv``,
the operator's notes under the caption).

What is sent, per take: the picture and sound before the bed and captions (the
finish record's ``pre_bed`` or ``--source``, blur patches applied here first;
a letterbox show's 9:16 letterbox final), the accepted caption cues (no em or en
dash), the take facts' shots and sound events, the harness bed and its level,
a saved server cover, the desk's caption style, POV and hook-line flags.
Outputs in ``reels/epNN/`` with the local reel's names and versioning, plus
``uploads.json`` (uploads remembered by digest). When the server does not
answer, the reel is not made and the operator is told to run ``reel`` later;
the finish is done either way.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from creation.captions import Cue
from creation.post.media import probe_video
from creation.post.reel import (
    AUTO_REEL_FAILED,
    LATEST_FILE,
    REELS_DIR,
    TakeSource,
    _named_counts,
    _patched,
    _posting,
    _remember_plan,
    episode_is_pov,
    episode_reels,
    expected_takes,
    hand_edited_plan,
    parse_ass_cues,
    plan_take_changes,
    read_latest,
    reel_paths,
    sources_fingerprint,
    take_sources,
    write_latest,
    write_new,
)
from creation.post.reel_cover import METRICS_FILE, cover_path, record_metrics_row
from creation.post.reel_plan import (
    BLACK_SECONDS,
    DEFAULT_SECONDS,
    FREEZE_SECONDS,
    OPERATOR_DIVIDER,
    post_operator_notes,
)
from creation.post.reel_server import (
    ReelServer,
    ReelServerError,
    ReelServerUnreachable,
    reel_job_id,
    unreachable_message,
)


def legacy_desk(desk: Path) -> bool:
    """Whether the desk was made before the rules epoch (6 Oct 2026): it keeps the local reel.

    :func:`creation.rules_epoch.is_legacy` decides (the desk's stamp, else
    ``series.json``'s day, else its folder date; a desk with none is current).
    """

    from creation.rules_epoch import is_legacy

    return is_legacy(desk)


def plan_lines(plan: Mapping[str, Any], warnings: Sequence[str] = ()) -> list[str]:
    """The server's plan in plain words, one line per segment, then its notes and every warning."""

    segments = [s for s in plan.get("segments") or [] if isinstance(s, Mapping)]
    total = float(plan.get("total_s") or 0.0)
    rows = [
        f"Reel plan ep{int(plan.get('episode') or 0):02d}: {len(segments)} segment(s), "
        f"{total:.2f} s of {float(plan.get('seconds') or DEFAULT_SECONDS):g} s"
    ]
    strongest = plan.get("strongest")
    if isinstance(strongest, Mapping):
        parts = ", ".join(
            f"{k} {float(v):.2f}" for k, v in (strongest.get("parts") or {}).items()
        )
        rows.append(
            f"Strongest frame: {strongest.get('take')} {float(strongest.get('at_s') or 0):.2f} s "
            f"(score {float(strongest.get('score') or 0):.2f}: {parts}; "
            f"{str(strongest.get('beat_role') or '').replace('_', ' ')} beat; {strongest.get('genre_family')} weights; "
            f"face by {str(strongest.get('face_source') or 'none').replace('_', ' ')})"
        )
    ending = plan.get("ending")
    if isinstance(ending, Mapping) and ending.get("style") not in (None, "hard"):
        rows.append(
            f"Ending: {ending.get('style')} (freeze {FREEZE_SECONDS:g} s on the last frame, then "
            f"{BLACK_SECONDS:g} s black)"
        )
    clock = 0.0
    for index, seg in enumerate(segments, start=1):
        start, end = float(seg.get("start_s") or 0.0), float(seg.get("end_s") or 0.0)
        rows.append(
            f"  {index}. {clock:5.2f}-{clock + end - start:5.2f} s  {str(seg.get('role')):<10} {seg.get('take')} "
            f"{start:.2f}-{end:.2f} s  {seg.get('why') or ''}".rstrip()
        )
        clock += end - start
    rows += [f"  note: {note}" for note in plan.get("notes") or []]
    rows += [f"⚠ {warning}" for warning in warnings]
    return rows


def take_timing(
    desk: Path, episode: int, source: TakeSource
) -> tuple[list[dict[str, Any]], list[float]]:
    """The take's shots as filmed and its sound events, from the saved take facts, on the source's timeline.

    What the server's planner reads about a take that it cannot see in the
    picture: which board row plays when (each beat owns its rows) and when the
    impacts land.

    Parameters
    ----------
    desk, episode
        Where the take facts are.
    source
        The take's source files.

    Returns
    -------
    tuple[list[dict], list[float]]
        ``[{index, start_s, end_s, named}]`` and the events' start times (both empty without take facts).
    """

    from creation.post.edit import measure_cuts
    from creation.post.sfx import filmed_shot_windows, planned_shots, saved_take_facts

    facts_path = saved_take_facts(desk, episode, source.take_id)
    if facts_path is None:
        return [], []
    payload = json.loads(facts_path.read_text(encoding="utf-8"))
    if source.record is not None:
        from creation.post.take_handles import facts_on_handled_file

        # The record's files were cut to the take's trim handles: its times minus start_s.
        payload = dict(facts_on_handled_file(payload, source.record.edits) or payload)
    facts = payload.get("take_facts", payload)
    planned = planned_shots(payload)
    if not planned:
        return [], []
    duration = probe_video(source.source).duration_seconds
    filmed = filmed_shot_windows(
        planned, measure_cuts(source.source), duration=duration
    )
    named = _named_counts(facts)
    shots = [
        {
            "index": p.index,
            "start_s": round(max(0.0, filmed.windows[p.index][0]), 3),
            "end_s": round(max(0.0, filmed.windows[p.index][1]), 3),
            "named": named.get(p.index, 0),
        }
        for p in planned
    ]
    events: list[float] = []
    for cue in facts.get("sfx_cues") or []:
        if cue.get("kind") != "event":
            continue
        try:
            t, index = float(cue["start_seconds"]), int(cue["shot_index"])
        except (KeyError, TypeError, ValueError):
            continue
        plan = next((p for p in planned if p.index == index), None)
        if plan is None or plan.end <= plan.start:
            events.append(round(max(0.0, t), 3))
            continue
        a, b = filmed.windows[index]
        events.append(
            round(max(0.0, a + (t - plan.start) / (plan.end - plan.start) * (b - a)), 3)
        )
    return shots, events


def take_cues(source: TakeSource) -> tuple[Cue, ...]:
    """The take's accepted caption cues: rebuilt ones, else its ``.ass``, else none.

    No em or en dash goes to the burn (:mod:`creation.caption_dashes`, 6 Oct 2026).
    """

    from dataclasses import replace as with_text

    from creation.caption_dashes import caption_text

    if source.cues is not None:
        cues = source.cues
    elif source.captions is not None:
        cues = tuple(parse_ass_cues(source.captions.read_text(encoding="utf-8")))
    else:
        return ()
    return tuple(with_text(c, text=caption_text(c.text)) for c in cues)


# --- naming ------------------------------------------------------------------------------------


@dataclass
class ReelResult:
    """What ``reel`` wrote and how it went."""

    #: The plan the server rendered (``fictora-reel-plan`` JSON, with the desk's sources per take).
    plan: dict[str, Any]
    plan_path: Path
    video: Path | None = None
    post: Path | None = None
    seconds: float = 0.0
    loudness: str = ""
    captions: str = ""
    lines: list[str] = field(default_factory=list)
    #: The free cover image for Instagram's "Edit cover" (``None`` with ``--no-cover`` or when none was drawn).
    cover: Path | None = None
    #: Where the cover's picture came from, or why there is none.
    cover_note: str = ""
    #: The reel results sheet the run put its row in.
    metrics: Path | None = None
    #: Named ``…-draft-vN``: the episode still had takes to finish.
    draft: bool = False
    #: The reel's files by role (``video``, ``ass``, ``plan``, ``post``) and what they were cut from.
    paths: dict[str, Path] = field(default_factory=dict)
    sources: dict[str, Any] = field(default_factory=dict)
    series: str = ""
    hook_text: str = ""
    posting_notes: list[str] = field(default_factory=list)
    #: The server's answer (``cached``, ``rules_version``, ``warnings``, ``report``).
    server: dict[str, Any] = field(default_factory=dict)

    @property
    def segments(self) -> list[dict[str, Any]]:
        """The plan's segments as JSON."""

        return [s for s in self.plan.get("segments") or [] if isinstance(s, Mapping)]

    def summary(self) -> str:
        order = " → ".join(
            f"{s.get('role')}({s.get('take')} {float(s.get('start_s', 0)):.1f}-{float(s.get('end_s', 0)):.1f})"
            for s in self.segments
        )
        total = float(self.plan.get("total_s") or 0.0)
        if self.video is None:
            return f"Plan only: {self.plan_path} ({len(self.segments)} segments, {total:.2f} s): {order}"
        cover = f"; cover {self.cover.name}" if self.cover else "; no cover image"
        cached = " (the server's stored reel)" if self.server.get("cached") else ""
        return f"Reel {self.video.name}{cached}: {self.seconds:.2f} s, {self.loudness}, {self.captions}{cover}; {order}"


@dataclass(frozen=True)
class BedChoice:
    """The bed the server lays under the reel, at what level, or why none."""

    bed: Path | None
    bed_db: float
    duck_db: float | None
    music_in_take: bool
    note: str


def reel_bed(desk: Path, sources: Sequence[TakeSource]) -> BedChoice:
    """The harness bed for the reel: the record's, else the one pinned on the desk; none when the takes carry music.

    Parameters
    ----------
    desk
        Series desk.
    sources
        The takes the reel cuts.

    Returns
    -------
    BedChoice
        What the server is asked to lay.
    """

    from creation.post.bed import (
        bed_level,
        chosen_record_level,
        harness_bed,
        pinned_bed,
    )

    record = next((s.record for s in sources if s.record is not None), None)
    # A source that already carries its take's bed (an older desk's mix) gets no second bed.
    baked = [s.take_id for s in sources if s.inferred and s.inferred.bed_in_source]
    # Nor does a take whose own soundtrack carries the harness's music.
    in_take = [
        s.take_id for s in sources if s.record is not None and s.record.music_in_take
    ]
    duck_db = record.duck_db if record else None
    if in_take or baked:
        why = (
            f"{', '.join(in_take)} carry the harness's music in their own soundtrack"
            if in_take
            else f"⚠ {', '.join(baked)} cut from a file with its own bed in"
        )
        note = f"no bed: {why}, so the music cuts with the picture"
        silent = [
            s.take_id
            for s in sources
            if s.take_id not in in_take and s.take_id not in baked
        ]
        if silent:
            note += (
                f"; !! {', '.join(silent)} were finished with the show's bed, which is not in their source: "
                "their stretch of the reel has no music"
            )
        return BedChoice(None, -16.5, duck_db, True, note)
    recorded = record.resolve(desk, "bed") if record and record.bed else None
    bed = (
        recorded if recorded is not None and harness_bed(desk, recorded) else None
    ) or pinned_bed(desk)
    level = bed_level(
        desk,
        bed,
        flag=None,
        recorded=chosen_record_level([(record.bed_db, record.bed_db_source)])
        if record
        else None,
    )
    if bed is None or not bed.is_file():
        return BedChoice(
            None,
            level.db,
            duck_db,
            False,
            "!! no bed on the record or the desk: the reel has no music",
        )
    return BedChoice(
        bed, level.db, duck_db, False, f"bed `{bed.name}` at {level.one_line()}"
    )


def letterbox_finals(
    desk: Path,
    spine: Mapping[str, Any],
    sources: Sequence[TakeSource],
    warnings: list[str],
) -> dict[str, Path] | None:
    """A letterbox show's 9:16 finished file per take, or ``None`` for every other reel.

    Only a show whose ``delivery_format`` is ``letterbox`` AND whose takes are
    really 4:3 (:func:`creation.post.letterbox.is_letterbox_take`) qualifies; the
    reel is then cut from each take's letterbox final (the 1080x1920 canvas,
    captions in the band, the mark and title block), as the app's reel is cut
    from a letterbox delivery: the captions ride with the picture and the bands
    are kept. A take without such a final is a ⚠ and the reel is cut as portrait.
    """

    from creation.post import letterbox as lb
    from creation.post.hook_overlay import delivery_format

    if delivery_format(spine) != "letterbox" or not sources:
        return None
    for source in sources:
        info = probe_video(source.source)
        if not lb.is_letterbox_take(dict(spine), (info.width, info.height)):
            return None
    finals: dict[str, Path] = {}
    for source in sources:
        final = (
            source.record.resolve(desk, "final")
            if source.record is not None and source.record.letterbox
            else None
        )
        if final is None or not final.is_file():
            warnings.append(
                f"letterbox: {source.take_id} has no 9:16 letterbox file on its finish record; the reel is cut "
                "from the 4:3 picture without the canvas (finish the take again)"
            )
            return None
        finals[source.take_id] = final
    return finals


def saved_take_cover(
    desk: Path, episode: int, sources: Sequence[TakeSource], first: str | None
) -> Path | None:
    """A cover the server already drew for one of the episode's takes (``first`` take tried first), or ``None``."""

    from creation.post.desk import take_stored_url
    from creation.post.thumbnail import saved_cover

    order = [first] if first and any(s.take_id == first for s in sources) else []
    order += [s.take_id for s in sources if s.take_id not in order]
    takes_dir = desk / f"ep{episode:02d}" / "takes"
    for take_id in order:
        saved = saved_cover(
            takes_dir,
            f"take-ep{episode:02d}-{take_id}",
            take_stored_url(desk, episode, take_id),
        )
        if saved is not None:
            return saved
    return None


def operator_body(
    *,
    takes: Sequence[Mapping[str, Any]],
    bed: BedChoice,
    bed_upload: tuple[str, str] | None,
    still_upload: tuple[str, str] | None,
    caption_style: str,
    pov: bool,
    seconds: float,
    bands_in_source: bool = False,
    ending: str | None,
    hook_line: str | None,
    no_hook_line: bool,
    hook_line_position: str | None,
    no_cover: bool,
    cover_frame: float | None,
    plan: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The reel route's request in operator mode (``fictora.drama-episode-reel.v1``)."""

    operator: dict[str, Any] = {
        "takes": list(takes),
        "bed_db": round(max(-40.0, min(12.0, bed.bed_db)), 1),
        "duck_db": bed.duck_db,
        "music_in_take": bed.music_in_take,
        "caption_style": caption_style,
        "pov": pov,
    }
    if bands_in_source:
        operator["bands_in_source"] = True
    if bed_upload is not None:
        operator["bed_url"], operator["bed_sha256"] = bed_upload
    if still_upload is not None:
        operator["cover_still_url"], operator["cover_still_sha256"] = still_upload
    body: dict[str, Any] = {
        "seconds": seconds,
        "ending": ending or "hard",
        "no_hook_line": no_hook_line,
        "no_cover": no_cover,
        "operator": operator,
    }
    if hook_line:
        body["hook_line"] = hook_line
    if hook_line_position in ("top", "lower"):
        body["hook_line_position"] = hook_line_position
    if cover_frame is not None:
        body["cover_frame_s"] = cover_frame
    if plan is not None:
        body["plan"] = dict(plan)
    return body


# --- the command -------------------------------------------------------------------------------


def run_reel(
    desk: Path,
    *,
    episode: int,
    seconds: float = DEFAULT_SECONDS,
    plan_only: bool = False,
    plan_file: Path | None = None,
    take_files: Sequence[str] = (),
    sources: Sequence[str] = (),
    captions: Sequence[str] = (),
    caption_style: str | None = None,
    ending: str | None = None,
    hook_line: str | None = None,
    no_hook_line: bool = False,
    hook_line_position: str | None = None,
    stream: TextIO | None = None,
    no_cover: bool = False,
    cover_frame: float | None = None,
    made_by: str = "reel",
    server: ReelServer | None = None,
) -> ReelResult:
    """Make the episode's reel on the server and keep the books. Writes only under ``<desk>/reels/``.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    seconds
        Target length, 6-30 s (a hand-edited plan keeps its own unless given).
    plan_only
        Keep only the plan the server made (``--plan-only``: edit it, then ``--plan FILE``).
    plan_file
        Render this (hand-edited) plan instead of planning.
    take_files, sources, captions
        Which accepted cut, pre-caption source and caption cues per take (see :func:`take_sources`).
    caption_style
        ``house`` / ``plain`` / ``none``; default the desk's.
    ending
        ``hard`` (default) or ``freeze-black``; ``None`` keeps a hand-edited plan's own.
    hook_line, no_hook_line, hook_line_position
        ``--hook-line TEXT`` / ``--no-hook-line`` / ``--hook-line-position top|lower``.
    stream
        Progress output (stdout by default).
    no_cover
        ``--no-cover``: no cover image.
    cover_frame
        ``--cover-frame S``: the cover's picture at ``S`` seconds on the reel.
    made_by
        What made it, for ``latest.json``: ``reel`` (this command), ``finish`` or an edit's name.
    server
        The server client (tests pass a stand-in); default the desk's.

    Returns
    -------
    ReelResult
        The plan and what was written.

    Raises
    ------
    ReelServerUnreachable
        The server did not answer (the message says to re-run later).
    ReelServerError, ValueError
        A refusal, or the desk has no finished take.
    """

    if ending is not None:
        from creation.post.ending import check_ending

        check_ending(ending)
    desk = desk.expanduser().resolve()
    # Edited copies of an inferred source and patched pictures live in a scratch folder, never on the desk.
    with tempfile.TemporaryDirectory(prefix="fictora-reel-") as tmp:
        result = make_reel(
            desk, episode=episode, seconds=seconds, plan_only=plan_only, plan_file=plan_file,
            take_files=take_files, sources=sources, captions=captions, caption_style=caption_style,
            ending=ending, stream=stream, scratch=Path(tmp), hook_line=hook_line, no_hook_line=no_hook_line,
            hook_line_position=hook_line_position, no_cover=no_cover, cover_frame=cover_frame, server=server,
        )  # fmt: skip
    return record_reel(desk, episode, result, made_by=made_by, stream=stream)


def make_reel(
    desk: Path,
    *,
    episode: int,
    seconds: float,
    plan_only: bool,
    plan_file: Path | None,
    take_files: Sequence[str],
    sources: Sequence[str],
    captions: Sequence[str],
    caption_style: str | None,
    ending: str | None,
    stream: TextIO | None,
    scratch: Path,
    hook_line: str | None = None,
    no_hook_line: bool = False,
    hook_line_position: str | None = None,
    no_cover: bool = False,
    cover_frame: float | None = None,
    server: ReelServer | None = None,
) -> ReelResult:
    """The renderer: send the desk's takes to the server's reel engine and download what it made.

    Everything the episode's folder keeps about its reels (``latest.json``,
    the hand-edited plans, ``metrics.csv``) is :func:`record_reel`'s, outside
    this boundary. Writes only the reel's own new files.

    Returns
    -------
    ReelResult
        With ``paths``, ``sources`` (:func:`sources_fingerprint`), ``series``,
        ``hook_text``, ``draft``, the server's report and warnings.
    """

    from creation.captions import resolve_caption_style
    from creation.post.desk import saved_spine
    from creation.spine_view import episode_id_for

    out = stream or sys.stdout
    desk = desk.expanduser().resolve()
    found = saved_spine(desk, episode)
    if found is None:
        raise ValueError(
            f"no saved spine with beats in ep{episode:02d}/api/: the reel plans from the spine's beats"
        )
    spine = found[0]
    body: dict[str, Any] | None = None
    if plan_file is not None:
        body = json.loads(plan_file.expanduser().read_text(encoding="utf-8"))
        planned = body.get("takes") or {}
        # A take the plan inferred (no finish record) is inferred again from its accepted file.
        given_accepted = [
            f"{t}={desk / v['accepted']}"
            for t, v in planned.items()
            if isinstance(v, Mapping) and v.get("inferred") and v.get("accepted")
        ]
        given_sources = [
            f"{t}={desk / v['source']}"
            for t, v in planned.items()
            if isinstance(v, Mapping) and v.get("source") and not v.get("inferred")
        ]
        given_captions = [
            f"{t}={desk / v['captions']}"
            for t, v in planned.items()
            if isinstance(v, Mapping) and v.get("captions")
        ]
        take_files = [*given_accepted, *take_files]
        sources = [*given_sources, *sources]
        captions = [*given_captions, *captions]
    srcs = take_sources(
        desk, episode, take_files=take_files, sources=sources, captions=captions,
        spine=spine, scratch=scratch, stream=out,
    )  # fmt: skip
    print(
        "Reel by the server's reel engine ($0; the takes are uploaded once): "
        + "; ".join(
            f"{s.take_id} {(s.inferred.source if s.inferred else s.source).name}"
            + (
                f" + {' + '.join(e.op for e in s.inferred.edits)} again"
                if s.inferred and s.inferred.edits
                else ""
            )
            + " + captions "
            + (
                s.captions.name
                if s.captions
                else "rebuilt"
                if s.cues is not None
                else "burned in (kept)"
                if s.inferred and s.inferred.burned
                else "none"
            )
            for s in srcs
        ),
        file=out,
        flush=True,
    )
    pov = episode_is_pov(desk, spine)
    if pov:
        print(
            'POV episode (the brief opens "POV:"): lines said to the camera stay in the reel; '
            "calls to action are still cut",
            file=out,
            flush=True,
        )
    patches = (
        [p for s in srcs for p in s.patches]
        if body is None
        else list(body.get("patches") or [])
    )
    warnings: list[str] = []
    for s in srcs:
        if s.record is not None and not s.record.complete:
            warnings.append(
                f"{s.take_id}: its finish record `{s.record.path.name if s.record.path else '?'}` is not complete "
                "(a sound part was missing at finish): check the accepted cut is this one"
            )
        if s.inferred is not None:
            warnings += [note.removeprefix("⚠ ") for note in s.inferred.notes]
        elif s.captions is None and s.cues is None:
            warnings.append(
                f"{s.take_id}: no accepted caption cues (.ass); the reel is uncaptioned there"
            )
        takes_dir = desk / f"ep{episode:02d}" / "takes"
        for variant in sorted(
            takes_dir.glob(f"take-ep{episode:02d}-{s.take_id}-*blur*.mp4")
        ):
            if not any(p.get("take") == s.take_id for p in patches):
                warnings.append(
                    f"{s.take_id}: `{variant.name}` is a blurred variant made after finish; its patch is not on the "
                    "pre-caption source: add it to the plan's patches (--plan-only, edit, --plan FILE)"
                )
    expected = expected_takes(desk, spine, episode)
    draft = expected is not None and len(srcs) < expected
    if draft:
        print(
            f"Draft reel: {len(srcs)} of {expected} take(s) finished; episode incomplete — re-cut when the last "
            "take is finished",
            file=out,
            flush=True,
        )
    style, style_note = resolve_caption_style(desk, caption_style)
    if style == "bold":
        # The server's operator mode burns house / plain / none; Bold (and the lines' emphasis words)
        # is not in its request yet, so a Bold show's reel is captioned in the house (Subtle) look.
        warnings.append(
            "captions: the show is Bold, but the server's reel engine does not take Bold yet; this reel is "
            "captioned Subtle (house yellow word flicker)"
        )
        style = "house"
    bed = reel_bed(desk, srcs)
    finals = letterbox_finals(desk, spine, srcs, warnings)
    if finals is not None:
        bed = BedChoice(
            None, bed.bed_db, bed.duck_db, True,
            "letterbox: cut from the 9:16 letterbox files (captions in the band, the mark and title block on "
            "them, their music cuts with the picture)",
        )  # fmt: skip
        print(
            "[letterbox] 4:3 takes on a letterbox show: the reel is cut from each take's 9:16 letterbox file "
            "(as finish makes it)",
            file=out,
            flush=True,
        )
        if hook_line or no_hook_line:
            warnings.append(
                "letterbox: the title block is part of each take's letterbox file; --hook-line / --no-hook-line "
                "change it on the next finish, not on the reel"
            )
    sizes = {
        (i.width, i.height)
        for i in (probe_video(finals[s.take_id] if finals else s.source) for s in srcs)
    }
    if len(sizes) != 1:
        raise ValueError(
            f"the takes differ in size ({sorted(sizes)}); a reel needs one frame size"
        )
    client = server or ReelServer(desk, episode)
    try:
        job_id = reel_job_id(desk, episode, [s.take_id for s in srcs])
        still = (
            None
            if no_cover or cover_frame is not None
            else saved_take_cover(desk, episode, srcs, None)
        )

        def send() -> dict[str, Any]:
            takes: list[dict[str, Any]] = []
            for s in srcs:
                picture = finals[s.take_id] if finals else _patched(s, scratch, patches)
                url, sha = client.upload(picture, kind="video")
                shots, events = take_timing(desk, episode, s)
                burned = bool(s.inferred and s.inferred.burned) or finals is not None
                takes.append(
                    {
                        "take_id": s.take_id,
                        "video_url": url,
                        "sha256": sha,
                        "cues": [
                            {
                                "start_s": round(c.start, 3),
                                "end_s": round(c.end, 3),
                                "text": c.text,
                                "italic": c.italic,
                            }
                            for c in take_cues(s)
                            if c.end > c.start and c.text.strip()
                        ],
                        "shots": shots,
                        "sound_events_s": events,
                        "captions_burned": burned,
                        "marked": burned,
                    }
                )
            bed_upload = (
                client.upload(bed.bed, kind="audio") if bed.bed is not None else None
            )
            still_upload = (
                client.upload(still, kind="image") if still is not None else None
            )
            request = operator_body(
                takes=takes, bed=bed, bed_upload=bed_upload, still_upload=still_upload, caption_style=style,
                pov=pov, seconds=seconds, ending=ending, hook_line=hook_line, no_hook_line=no_hook_line,
                hook_line_position=hook_line_position, no_cover=no_cover, cover_frame=cover_frame, plan=body,
                bands_in_source=finals is not None,
            )  # fmt: skip
            return client.make(job_id, episode_id_for(spine, episode), request)

        try:
            answer = send()
        except ReelServerUnreachable:
            if not client.reused:
                raise
            # A file uploaded on an earlier run may be gone from storage: upload everything again, once.
            print(
                "Reel: the server could not cut from the earlier uploads; uploading again",
                file=out,
                flush=True,
            )
            client.forget_uploads()
            client.reused = False
            answer = send()
        paths = reel_paths(desk, episode, draft=draft)
        plan = dict(answer.get("plan") or {})
        plan["take_windows"] = plan.get("takes") or {}
        plan["takes"] = {s.take_id: s.as_json(desk) for s in srcs}
        plan["patches"] = patches
        plan["edited_from"] = plan_file.name if plan_file else None
        if finals is not None:
            # review and the safe zones read a reel as letterbox from its plan (safe_zones.letterbox_file).
            plan["letterbox"] = True
            plan["caption_colour"] = next(
                (
                    s.record.caption_colour
                    for s in srcs
                    if s.record and s.record.caption_colour
                ),
                None,
            )
        plan["server"] = {
            "rules_version": answer.get("rules_version"),
            "cached": answer.get("cached"),
            "job_id": job_id,
        }
        write_new(paths["plan"], json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
        all_warnings = list(dict.fromkeys([*warnings, *(answer.get("warnings") or [])]))
        for line in plan_lines(plan, all_warnings):
            print(line, file=out)
        series = str(spine.get("title") or desk.name)
        hook = (
            plan.get("hook_line") if isinstance(plan.get("hook_line"), Mapping) else {}
        )
        result = ReelResult(
            plan=plan, plan_path=paths["plan"], draft=draft, paths=dict(paths),
            sources=sources_fingerprint(desk, srcs), series=series,
            hook_text=str(hook.get("text") or "") if hook and hook.get("mode") else "", server=answer,
        )  # fmt: skip
        if plan_only:
            print(
                f"Plan: {paths['plan']} (edit it, then: reel --desk D --episode {episode} --plan FILE)",
                file=out,
            )
            return result
        client.download(str(answer["reel_video_url"]), paths["video"])
        cover: Path | None = None
        if answer.get("cover_url"):
            cover = client.download(
                str(answer["cover_url"]), cover_path(paths["video"])
            )
    finally:
        if server is None:
            client.close()
    posting, posting_notes = _posting(desk)
    todo = post_operator_notes(cover=cover.name if cover else None, **posting)
    caption = str(answer.get("caption_text") or "").rstrip("\n") + "\n"
    write_new(paths["post"], caption + OPERATOR_DIVIDER + "\n" + "\n".join(todo) + "\n")
    report = [str(line) for line in answer.get("report") or []]
    result.video, result.post = paths["video"], paths["post"]
    result.seconds = int(answer.get("duration_ms") or 0) / 1000.0
    result.loudness = str(plan.get("loudness") or "loudness not reported")
    result.captions = str(plan.get("captions") or "")
    result.cover = cover
    result.cover_note = str(
        plan.get("cover") or ("no cover image (--no-cover)" if no_cover else "")
    )
    result.posting_notes = posting_notes + [
        w for w in all_warnings if w.startswith("cover")
    ]
    result.lines = [bed.note, *report] + ([style_note] if style_note else [])
    for line in result.lines:
        print(f"- {line}", file=out)
    for line in result.posting_notes:
        print(f"⚠ {line}", file=out)
    for key in ("video", "plan", "post"):
        if paths[key].exists():
            print(f"{key}: {paths[key]}", file=out)
    if cover is not None:
        print(f"cover: {cover}", file=out)
    return result


def record_reel(
    desk: Path,
    episode: int,
    result: ReelResult,
    *,
    made_by: str = "reel",
    stream: TextIO | None = None,
) -> ReelResult:
    """Keep the books on a reel :func:`make_reel` made: the plan's hash, ``latest.json``, ``metrics.csv``.

    Parameters
    ----------
    desk, episode
        The desk and episode.
    result
        What the renderer made (a plan only: just the plan's hash is kept).
    made_by
        ``reel``, ``finish`` or an edit's name.
    stream
        Progress output (stdout by default).

    Returns
    -------
    ReelResult
        ``result``, with ``metrics`` set when a reel was rendered.
    """

    out = stream or sys.stdout
    _remember_plan(desk, episode, result.plan_path)
    if result.video is None:
        return result
    paths = result.paths
    posting, _ = _posting(desk)
    cold = next((s for s in result.segments if s.get("role") == "cold_open"), None)
    strongest = (
        result.plan.get("strongest")
        if isinstance(result.plan.get("strongest"), Mapping)
        else None
    )
    write_latest(
        desk, episode, paths=paths, cover=result.cover, draft=result.draft, made_by=made_by,
        sources=result.sources,
    )  # fmt: skip
    result.metrics = record_metrics_row(
        desk / REELS_DIR / METRICS_FILE,
        {
            "reel_file": result.video.name, "cover_file": result.cover.name if result.cover else "",
            "series": result.series, "part": episode, "account": posting["account"],
            "lane": posting["lane"], "planned_post_slot": posting["posting_slot"],
            "cold_open_role": str(strongest.get("beat_role") or "") if strongest and cold else "",
            "cold_open_time": (
                f"{cold.get('take')} {float(cold.get('start_s', 0)):.2f}-{float(cold.get('end_s', 0)):.2f} s"
                if cold
                else ""
            ),
            "hook_text": result.hook_text,
        },
    )  # fmt: skip
    print(f"latest: {episode_reels(desk, episode) / LATEST_FILE}", file=out)
    print(
        f"metrics: {result.metrics} (this episode's row now names this reel: fill in views and the rest "
        "after posting)",
        file=out,
    )
    if result.post is not None:
        print("Post text (copy the caption; the rest is for you):", file=out)
        print(result.post.read_text(encoding="utf-8").rstrip(), file=out)
    print(result.summary(), file=out)
    return result


# --- the episode's reel folder: latest.json, what changed, the automatic reel --------------------

#: The episode folder's record of its current reel (``reels/epNN/latest.json``).


def auto_reel(
    desk: Path,
    episode: int,
    *,
    trigger: str,
    stream: TextIO | None = None,
    force: bool = False,
    server: ReelServer | None = None,
) -> ReelResult | None:
    """The reel made by itself after ``finish`` or an edit that wrote a new deliverable ($0, on the server).

    It writes into ``reels/epNN/`` like ``reel`` and never draws a paid cover.
    It cuts nothing when the episode's finished files are the ones the
    current reel (``latest.json``) was cut from; it reuses the newest
    hand-edited plan while the takes it names are unchanged (else a fresh
    plan, saying what changed); and it names the reel a draft while takes are
    still to finish. A failure is printed (the finish is done), never raised.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    trigger
        ``finish`` or the edit's name (``trim`` …), written in ``latest.json``.
    stream
        Progress output (stdout by default).
    force
        Cut even when nothing changed.
    server
        As :func:`run_reel`.

    Returns
    -------
    ReelResult | None
        The reel, or ``None`` when none was made (unchanged, no spine, or it failed).
    """

    from creation.post.desk import saved_spine

    out = stream or sys.stdout
    desk = desk.expanduser().resolve()
    found = saved_spine(desk, episode)
    if found is None:
        print(
            f"Reel: not made (no saved spine with beats in ep{episode:02d}/api/); "
            f"`reel --desk D --episode {episode}` once it has one",
            file=out,
        )
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="fictora-reel-check-") as tmp:
            srcs = take_sources(
                desk, episode, spine=found[0], scratch=Path(tmp), stream=out
            )
            fingerprint = sources_fingerprint(desk, srcs)
            current = {s.take_id: s.as_json(desk) for s in srcs}
        latest = read_latest(desk, episode)
        reel = desk / str(latest.get("reel") or "")
        if (
            not force
            and latest.get("sources") == fingerprint
            and latest.get("reel")
            and reel.is_file()
        ):
            print(
                f"Reel: unchanged since `{latest['reel']}` (the same finished takes); not cut again",
                file=out,
            )
            return None
        plan_file: Path | None = None
        edited = hand_edited_plan(desk, episode)
        if edited is not None:
            changes = plan_take_changes(edited[1].get("takes") or {}, current)
            if changes:
                print(
                    f"Reel: a fresh plan: the takes changed since the hand-edited plan `{edited[0].name}` "
                    f"({'; '.join(changes)})",
                    file=out,
                )
            else:
                plan_file = edited[0]
                print(
                    f"Reel: reusing the hand-edited plan `{plan_file.name}` (its takes are unchanged)",
                    file=out,
                )
        print(
            f"Reel (after {trigger}; $0, the server's reel engine; --no-reel skips it):",
            file=out,
            flush=True,
        )
        return run_reel(
            desk, episode=episode, plan_file=plan_file, stream=out, made_by=trigger, server=server
        )  # fmt: skip
    except ReelServerUnreachable as exc:
        print(
            f"⚠ {unreachable_message(desk, episode)} ({exc}; the {trigger} is done)",
            file=out,
        )
        return None
    except AUTO_REEL_FAILED as exc:
        print(
            f"⚠ Reel not made ({type(exc).__name__}: {exc}); the {trigger} is done. "
            f"Try again with `reel --desk D --episode {episode}`",
            file=out,
        )
        return None


__all__ = [
    "BedChoice",
    "ReelResult",
    "ReelServerError",
    "auto_reel",
    "legacy_desk",
    "letterbox_finals",
    "make_reel",
    "operator_body",
    "plan_lines",
    "record_reel",
    "reel_bed",
    "run_reel",
    "saved_take_cover",
    "take_cues",
    "take_timing",
]
