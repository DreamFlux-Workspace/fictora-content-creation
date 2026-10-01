"""``fictora-produce reel``: a social reel cut from an episode's already-rendered footage ($0, local).

No new video, no server call: the cut is made on this laptop with ffmpeg from
files the desk already has. The plan (what to cut, and why) comes from
:mod:`creation.post.reel_plan`; this module gathers its inputs from the desk,
writes the plan, and renders it.

Sources, per take (the newest finish record, or the one naming ``--take-file``):

- **Picture and sound before the bed**: the record's ``pre_bed`` (colour-matched,
  uncaptioned, unmarked; voice, effects and hand cues), or ``--source``. Cutting
  the finished file would cut burned captions mid-word, so it is never used.
- **Captions**: the cues of the accepted cut's ``.ass`` (the record master's, or
  ``--captions``): the words and times the human approved. They are moved onto
  the reel through the segment map and burned again in the kit's house style
  (:func:`creation.captions.build_ass`), so a caption never rides on a
  reordered picture it does not belong to. English shows keep their word
  flicker; other spoken languages their whole English lines.
- **Bed**: the record's bed (else the desk's pinned bed), laid ONCE under the
  whole reel like ``join`` (looped seamlessly, ducked under the voice, measured
  gain to about -18 LUFS, one limiter), so the music runs on across every cut.

Render: each run of picture is cut on the frame grid; the sound before the bed
is cut with short equal-power crossfades centred on each cut (no clicks); the
bed goes under; captions; the Sokii mark top left (:func:`creation.post.watermark.watermark`,
as ``finish`` applies it). A ``patches`` entry in the plan (a ``blur`` box made
on the accepted file after finish) is applied to the source first.

Outputs, never overwritten, all in ``<desk>/reels/`` (nothing else on the desk
is touched: no run note, no edit chain line): ``reel-epNN-vN.mp4`` (+ ``.ass``),
``reel-plan-epNN-vN.json`` and ``post-epNN-vN.txt`` (the suggested post text:
the call to action lives there, never on screen).
"""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from creation.captions import (
    Cue,
    build_ass,
    burn_ass,
    captions_whole_lines,
    find_ffmpeg,
    resolve_caption_style,
)
from creation.post.finish_record import FinishRecord, finish_records, record_for_file
from creation.post.media import decode_frames, probe_video, run_ffmpeg
from creation.post.reel_plan import (
    DEFAULT_SECONDS,
    BeatInput,
    ReelPlan,
    Segment,
    Shot,
    TakeInput,
    check_plan,
    plan_json,
    plan_reel,
    post_text,
    reel_seconds,
    retime_cues,
    segment_map,
    segments_from_json,
    strongest_from_json,
)

#: The reel's folder on the desk.
REELS_DIR = "reels"
#: Crossfade at each cut in the sound before the bed (centred on the cut, so the length holds).
CROSSFADE_SECONDS = 0.04
#: Fade on the reel's first and last samples.
EDGE_FADE_SECONDS = 0.02
#: The motion / contrast measurement (the review's frame size and rate).
MEASURE_SIZE = (96, 168)
MEASURE_FPS = 8.0
RATE = 48000

_TAKE_NAME = re.compile(r"take-ep(\d+)-(t\d+)")


# --- desk inputs --------------------------------------------------------------------------------


@dataclass
class TakeSource:
    """Where one take's reel material comes from."""

    take_id: str
    source: Path
    captions: Path | None
    record: FinishRecord | None
    accepted: Path | None
    patches: list[dict[str, Any]] = field(default_factory=list)

    def as_json(self, desk: Path) -> dict[str, Any]:
        def rel(p: Path | None) -> str | None:
            if p is None:
                return None
            try:
                return str(p.resolve().relative_to(desk))
            except ValueError:
                return str(p)

        return {
            "source": rel(self.source),
            "captions": rel(self.captions),
            "accepted": rel(self.accepted),
            "record": rel(self.record.path)
            if self.record and self.record.path
            else None,
        }


def parse_ass_cues(text: str) -> list[Cue]:
    """The caption events of an ASS file as cues (style ``Italic`` -> italic; override tags dropped).

    Parameters
    ----------
    text
        The ``.ass`` file text.

    Returns
    -------
    list[Cue]
        Every ``Dialogue`` event, in file order.
    """

    def seconds(stamp: str) -> float:
        h, m, s = stamp.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    cues: list[Cue] = []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields_ = line.split(":", 1)[1].split(",", 9)
        if len(fields_) < 10:
            continue
        body = re.sub(r"\{[^}]*\}", "", fields_[9])
        body = body.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        cues.append(
            Cue(
                seconds(fields_[1]),
                seconds(fields_[2]),
                body,
                italic=fields_[3].strip().lower() == "italic",
            )
        )
    return cues


def _take_of(path: Path) -> tuple[int, str] | None:
    match = _TAKE_NAME.search(path.name)
    return (int(match.group(1)), match.group(2)) if match else None


def _by_take(values: Sequence[str], flag: str) -> dict[str, Path]:
    """``tK=FILE`` or ``FILE`` named ``take-epNN-tK-…`` -> ``{tK: FILE}``."""

    found: dict[str, Path] = {}
    for raw in values:
        take, sep, path = raw.partition("=")
        if sep and re.fullmatch(r"t\d+", take):
            file = Path(path).expanduser().resolve()
        else:
            file = Path(raw).expanduser().resolve()
            named = _take_of(file)
            if named is None:
                raise ValueError(
                    f"{flag} {raw}: say which take (tK=FILE), the name does not carry take-epNN-tK"
                )
            take = named[1]
        if not file.is_file():
            raise FileNotFoundError(f"{flag}: {file} not found")
        found[take] = file
    return found


def take_sources(
    desk: Path,
    episode: int,
    *,
    take_files: Sequence[str] = (),
    sources: Sequence[str] = (),
    captions: Sequence[str] = (),
) -> list[TakeSource]:
    """Each finished take's pre-caption source and caption cues, in take order.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_files
        The accepted finished file per take (picks its finish record); default the newest record.
    sources
        ``tK=FILE`` overrides for the pre-caption source.
    captions
        ``tK=FILE.ass`` overrides for the accepted captions.

    Returns
    -------
    list[TakeSource]
        One per take with a finish record (or a ``--source``).

    Raises
    ------
    ValueError
        When the episode has no finished take, or a take has no pre-caption file.
    """

    accepted = _by_take(take_files, "--take-file")
    given = _by_take(sources, "--source")
    cue_files = _by_take(captions, "--captions")
    records: dict[str, FinishRecord] = {}
    for record in finish_records(desk, episode):
        records[record.take_id] = record  # newest version wins
    for take, file in accepted.items():
        record = record_for_file(desk, file)
        if record is not None:
            records[take] = record
    take_ids = sorted(set(records) | set(given), key=lambda t: int(t[1:]))
    if not take_ids:
        raise ValueError(
            f"ep{episode:02d} has no finished take (no take-ep{episode:02d}-tK-finish-vN.json): run finish first"
        )
    out: list[TakeSource] = []
    for take in take_ids:
        record = records.get(take)
        source = given.get(take) or (
            record.resolve(desk, "pre_bed") if record else None
        )
        if source is None or not source.is_file():
            raise ValueError(
                f"{take}: no picture before the captions (the finish record's pre_bed is missing); "
                f"pass --source {take}=FILE"
            )
        ass = cue_files.get(take)
        if ass is None and record is not None:
            master = record.resolve(desk, "master")
            if master is not None and master.with_suffix(".ass").is_file():
                ass = master.with_suffix(".ass")
        final = accepted.get(take) or (
            record.resolve(desk, "final") if record else None
        )
        patches = [
            {"take": take, **edit}
            for edit in (record.edits if record else ())
            if edit.get("op") == "blur"
        ]
        out.append(TakeSource(take, source, ass, record, final, patches))
    return out


def _named_counts(facts: Mapping[str, Any]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for shot in facts.get("shots") or []:
        people = shot.get("people") or {}
        try:
            counts[int(shot["shot_index"])] = len(people.get("named") or [])
        except (KeyError, TypeError, ValueError):
            continue
    return counts


def measure_take(desk: Path, episode: int, source: TakeSource) -> TakeInput:
    """Measure one take for the planner: cuts, 8 fps motion and contrast, shots as filmed, captions.

    Parameters
    ----------
    desk, episode
        Where the take facts are.
    source
        The take's source files.

    Returns
    -------
    TakeInput
        Everything :func:`creation.post.reel_plan.plan_reel` reads.
    """

    from creation.post.edit import measure_cuts
    from creation.post.sfx import filmed_shot_windows, planned_shots, saved_take_facts

    info = probe_video(source.source)
    cuts = measure_cuts(source.source)
    width, height = MEASURE_SIZE
    frames = (
        decode_frames(source.source, width=width, height=height, fps=MEASURE_FPS)
        / 255.0
    )
    gray = frames.mean(axis=3) if len(frames) else frames
    motion = (
        [0.0, *np.sqrt(((gray[1:] - gray[:-1]) ** 2).mean(axis=(1, 2))).tolist()]
        if len(gray) > 1
        else [0.0] * len(gray)
    )
    if len(motion) > 1:
        motion[0] = motion[1]
    contrast = gray.std(axis=(1, 2)).tolist() if len(gray) else []
    samples = tuple(round(i / MEASURE_FPS, 4) for i in range(len(gray)))
    shots: tuple[Shot, ...] = ()
    events: list[float] = []
    facts_path = saved_take_facts(desk, episode, source.take_id)
    if facts_path is not None:
        payload = json.loads(facts_path.read_text(encoding="utf-8"))
        facts = payload.get("take_facts", payload)
        planned = planned_shots(payload)
        if planned:
            filmed = filmed_shot_windows(planned, cuts, duration=info.duration_seconds)
            named = _named_counts(facts)
            shots = tuple(
                Shot(p.index, *filmed.windows[p.index], named=named.get(p.index, 0))
                for p in planned
            )
            for cue in facts.get("sfx_cues") or []:
                if cue.get("kind") != "event":
                    continue
                try:
                    t, index = float(cue["start_seconds"]), int(cue["shot_index"])
                except (KeyError, TypeError, ValueError):
                    continue
                plan = next((p for p in planned if p.index == index), None)
                if plan is None or plan.end <= plan.start:
                    events.append(t)
                    continue
                a, b = filmed.windows[index]
                events.append(a + (t - plan.start) / (plan.end - plan.start) * (b - a))
    cues = (
        tuple(parse_ass_cues(source.captions.read_text(encoding="utf-8")))
        if source.captions is not None
        else ()
    )
    return TakeInput(
        take_id=source.take_id, duration=info.duration_seconds, fps=info.fps or 24.0,
        shots=shots, cuts=tuple(cuts), cues=cues, sample_seconds=samples,
        motion=tuple(motion), contrast=tuple(contrast), events=tuple(events),
    )  # fmt: skip


def episode_beats(
    spine: Mapping[str, Any], episode: int, take_ids: Sequence[str]
) -> list[BeatInput]:
    """The episode's beats with their take and board rows, from the saved spine.

    Parameters
    ----------
    spine
        The spine (bare).
    episode
        Episode ordinal.
    take_ids
        The finished takes in order (the beats are split over them like the board).

    Returns
    -------
    list[BeatInput]
        In order.
    """

    from creation.spine_view import beats_by_take

    grouped = beats_by_take(spine, episode=episode, take_count=max(1, len(take_ids)))
    frames = {
        f.get("frame_id"): f for f in spine.get("frames") or [] if isinstance(f, dict)
    }

    def row(frame: Mapping[str, Any] | None) -> int:
        try:
            return int((frame or {}).get("board_row") or 0)
        except (TypeError, ValueError):
            return 0

    beats: list[BeatInput] = []
    for take_index, members in enumerate(grouped):
        take_id = take_ids[take_index] if take_index < len(take_ids) else take_ids[-1]
        firsts = [row(frames.get(b.get("frame_id"))) for b in members]
        groups = {
            (frames.get(b.get("frame_id")) or {}).get("storyboard_group_id")
            for b in members
        } - {None}
        rows_in_take = max(
            [row(f) for f in frames.values() if f.get("storyboard_group_id") in groups]
            or [max(firsts or [0])]
        )
        for index, beat in enumerate(members):
            first = firsts[index]
            later = [r for r in firsts[index + 1 :] if r]
            last = (later[0] - 1) if later else rows_in_take
            lines = tuple(
                str(line.get("subtitle_text") or line.get("text") or "").strip()
                for line in beat.get("dialogue_lines") or []
                if isinstance(line, dict)
                and (line.get("subtitle_text") or line.get("text"))
            )
            kind = str(beat.get("satisfaction_type") or "none").lower()
            beats.append(
                BeatInput(
                    ordinal=int(beat.get("ordinal") or index + 1),
                    take_id=take_id,
                    first_row=first,
                    last_row=max(first, last),
                    label=str(beat.get("motion_intent") or "")[:80],
                    payoff=kind not in ("", "none"),
                    lines=lines,
                )  # fmt: skip
            )
    return beats


# --- naming ------------------------------------------------------------------------------------


def reel_paths(desk: Path, episode: int) -> dict[str, Path]:
    """The next free ``vN`` for the reel's four files, the same N for all (never an existing file).

    Parameters
    ----------
    desk
        Series desk (``reels/`` is made when missing).
    episode
        Episode ordinal.

    Returns
    -------
    dict[str, Path]
        ``video``, ``ass``, ``plan``, ``post``.
    """

    folder = desk / REELS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stems = (
        f"reel-ep{episode:02d}",
        f"reel-plan-ep{episode:02d}",
        f"post-ep{episode:02d}",
    )
    used = {0}
    for path in folder.iterdir():
        for stem in stems:
            match = re.match(rf"^{re.escape(stem)}-v(\d+)(\.|-)", path.name)
            if match:
                used.add(int(match.group(1)))
    n = max(used) + 1
    paths = {
        "video": folder / f"reel-ep{episode:02d}-v{n}.mp4",
        "ass": folder / f"reel-ep{episode:02d}-v{n}.ass",
        "plan": folder / f"reel-plan-ep{episode:02d}-v{n}.json",
        "post": folder / f"post-ep{episode:02d}-v{n}.txt",
    }
    for path in paths.values():
        if path.exists():
            raise FileExistsError(f"{path} exists; the reel never overwrites")
    return paths


def write_new(path: Path, text: str) -> Path:
    """Write ``text`` to a file that must not exist yet (``x`` mode)."""

    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    return path


# --- render ------------------------------------------------------------------------------------


def _runs(segments: Sequence[Segment], fps: float) -> list[tuple[str, int, int]]:
    """Segments as frame runs, contiguous pieces of one take merged: ``(take, first, end)``."""

    runs: list[tuple[str, int, int]] = []
    for seg in segments:
        a, b = round(seg.start * fps), round(seg.end * fps)
        if b <= a:
            continue
        if runs and runs[-1][0] == seg.take and runs[-1][2] == a:
            runs[-1] = (seg.take, runs[-1][1], b)
        else:
            runs.append((seg.take, a, b))
    return runs


def cut_sound(
    sounds: Mapping[str, np.ndarray], runs: Sequence[tuple[str, int, int]], fps: float
) -> np.ndarray:
    """The runs' sound joined with equal-power crossfades centred on each cut (the length is kept).

    Each run is read with a half-crossfade handle either side from its own take
    (silence past the take's ends), so the cut sits mid-fade and the reel is
    exactly as long as its frames.

    Parameters
    ----------
    sounds
        Each take's sound before the bed, ``(samples, 2)`` at 48 kHz.
    runs
        ``(take, first frame, end frame)``.
    fps
        Frame rate.

    Returns
    -------
    numpy.ndarray
        The reel's sound before the bed.
    """

    half = int(round(CROSSFADE_SECONDS / 2 * RATE))
    total = sum(int(round((b - a) / fps * RATE)) for _, a, b in runs)
    out = np.zeros((total + 2 * half, 2))
    cursor = half
    for index, (take, a, b) in enumerate(runs):
        sound = sounds[take]
        start, length = int(round(a / fps * RATE)), int(round((b - a) / fps * RATE))
        lo, hi = start - half, start + length + half
        piece = np.zeros((hi - lo, 2))
        src_lo, src_hi = max(0, lo), min(len(sound), hi)
        if src_hi > src_lo:
            piece[src_lo - lo : src_hi - lo] = sound[src_lo:src_hi]
        ramp = (np.arange(2 * half) + 0.5) / max(1, 2 * half) * (math.pi / 2)
        if index:
            piece[: 2 * half] *= np.sin(ramp)[:, None]
        else:
            piece[:half] = 0.0
        if index < len(runs) - 1:
            piece[len(piece) - 2 * half :] *= np.cos(ramp)[:, None]
        else:
            piece[len(piece) - half :] = 0.0
        out[cursor - half : cursor - half + len(piece)] += piece
        cursor += length
    sound = out[half : half + total]
    edge = int(EDGE_FADE_SECONDS * RATE)
    if edge and len(sound) > 2 * edge:
        sound[:edge] *= np.linspace(0, 1, edge)[:, None]
        sound[-edge:] *= np.linspace(1, 0, edge)[:, None]
    return sound


def _patched(
    source: TakeSource, scratch: Path, patches: Sequence[Mapping[str, Any]]
) -> Path:
    """The source with the plan's blur patches for its take applied (a scratch file), else itself."""

    from creation.post.edit import BLUR_SIGMA, blur_boxes, parse_box

    picture = source.source
    for index, patch in enumerate(
        p for p in patches if p.get("take") == source.take_id
    ):
        if patch.get("op", "blur") != "blur":
            raise ValueError(f"patch {patch}: only op blur is supported")
        boxes = tuple(parse_box(str(b)) if not isinstance(b, list) else parse_box(",".join(map(str, b)))
                      for b in patch.get("boxes") or [])  # fmt: skip
        out = scratch / f"{source.take_id}-patch-{index + 1}.mp4"
        blur_boxes(
            picture, out, boxes,
            start=float(patch.get("from", patch.get("start", 0.0))),
            end=float(patch.get("to", patch.get("end", 0.0))),
            strength=float(patch.get("strength") or BLUR_SIGMA),
            feather=int(patch.get("feather") or 0),
        )  # fmt: skip
        picture = out
    return picture


@dataclass
class ReelResult:
    """What ``reel`` wrote and how it went."""

    plan: ReelPlan
    plan_path: Path
    video: Path | None = None
    ass: Path | None = None
    post: Path | None = None
    seconds: float = 0.0
    loudness: str = ""
    captions: str = ""
    lines: list[str] = field(default_factory=list)

    def summary(self) -> str:
        order = " → ".join(
            f"{s.role}({s.take} {s.start:.1f}-{s.end:.1f})" for s in self.plan.segments
        )
        if self.video is None:
            return f"Plan only: {self.plan_path} ({len(self.plan.segments)} segments, {self.plan.total:.2f} s): {order}"
        return f"Reel {self.video.name}: {self.seconds:.2f} s, {self.loudness}, {self.captions}; {order}"


def render_reel(
    desk: Path,
    *,
    episode: int,
    plan: ReelPlan,
    sources: Sequence[TakeSource],
    takes: Sequence[TakeInput],
    patches: Sequence[Mapping[str, Any]],
    paths: Mapping[str, Path],
    caption_style: str,
    whole_lines: bool,
    watermark_y: int | None = None,
) -> tuple[float, str, str, list[str]]:
    """Cut the plan from the sources into ``paths['video']`` (and its ``.ass``).

    Returns
    -------
    tuple[float, str, str, list[str]]
        Seconds, loudness line, captions line, report lines.

    Raises
    ------
    ValueError
        When a segment names a take with no source, or the sources differ in size.
    """

    from creation.post.bed import bed_level, chosen_record_level, pinned_bed
    from creation.post.join import decode_stereo, loop_bed, write_wav
    from creation.post.mix import mix_take
    from creation.post.watermark import watermark

    by_take = {s.take_id: s for s in sources}
    for seg in plan.segments:
        if seg.take not in by_take:
            raise ValueError(
                f"segment {seg.role} names {seg.take}, which has no source"
            )
    infos = {t: probe_video(s.source) for t, s in by_take.items()}
    sizes = {(i.width, i.height) for i in infos.values()}
    if len(sizes) != 1:
        raise ValueError(
            f"the takes differ in size ({sorted(sizes)}); a reel needs one frame size"
        )
    width, height = sizes.pop()
    fps = next(iter(infos.values())).fps or 24.0
    runs = _runs(plan.segments, fps)
    report: list[str] = []
    record = next((s.record for s in sources if s.record is not None), None)
    bed = (
        record.resolve(desk, "bed") if record and record.bed else None
    ) or pinned_bed(desk)
    level = bed_level(
        desk,
        bed,
        flag=None,
        recorded=chosen_record_level([(record.bed_db, record.bed_db_source)])
        if record
        else None,
    )
    bed_db = level.db
    duck_db = record.duck_db if record else None
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        pictures = {t: _patched(s, scratch, patches) for t, s in by_take.items()}
        used = sorted({take for take, _, _ in runs}, key=lambda t: int(t[1:]))
        inputs: list[str] = []
        for take in used:
            inputs += ["-i", str(pictures[take])]
        graph = [
            f"[{used.index(take)}:v]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,"
            f"fps={fps:g},format=yuv420p[v{i}]"
            for i, (take, a, b) in enumerate(runs)
        ]
        graph.append(
            "".join(f"[v{i}]" for i in range(len(runs)))
            + f"concat=n={len(runs)}:v=1:a=0[v]"
        )
        sound = cut_sound(
            {t: decode_stereo(by_take[t].source) for t in used}, runs, fps
        )
        wav = write_wav(sound, scratch / "reel-sound.wav")
        bedless = scratch / "reel-no-bed.mkv"
        run_ffmpeg(
            [*inputs, "-i", str(wav), "-filter_complex", ";".join(graph), "-map", "[v]", "-map", f"{len(used)}:a",
             "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-r", f"{fps:g}", "-c:a", "pcm_f32le", str(bedless)]
        )  # fmt: skip
        mixed = scratch / "reel-mix.mp4"
        total = reel_seconds(plan.segments, fps)
        if bed is not None and bed.is_file():
            looped = loop_bed(bed, total + 1.0, scratch / "bed-looped.wav")
            mix = mix_take(bedless, mixed, bed=looped, bed_db=bed_db, duck_db=duck_db)
            report.append(
                f"bed `{bed.name}` once under the whole reel at {level.one_line()}; {mix.one_line()}"
            )
        else:
            mix = mix_take(bedless, mixed, bed=None, bed_db=bed_db, duck_db=duck_db)
            report.append(
                f"!! no bed on the record or the desk: the reel has no music; {mix.one_line()}"
            )
        loudness = f"{mix.mix_lufs:.1f} LUFS"
        cues = retime_cues(plan.segments, {t.take_id: t.cues for t in takes}, fps)
        grain = "whole English lines" if whole_lines else "word flicker"
        if caption_style == "none":
            captioned, captions_line = mixed, "captions none (desk style)"
        elif not cues:
            captioned, captions_line = (
                mixed,
                "!! no captions (no accepted caption cues found)",
            )
        else:
            write_new(
                paths["ass"],
                build_ass(cues, width=width, height=height, style=caption_style),
            )
            captioned = scratch / "reel-cap.mp4"
            burn_ass(find_ffmpeg()[0], mixed, paths["ass"], captioned)
            captions_line = (
                f"{len(cues)} caption cue(s), {grain}, re-timed through the segment map"
            )
        watermark(captioned, paths["video"], y=watermark_y)
    seconds = probe_video(paths["video"]).duration_seconds
    report.append(f"captions: {captions_line}")
    report.append("Sokii mark top left (as finish applies it)")
    return seconds, loudness, captions_line, report


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
    watermark_y: int | None = None,
    stream: TextIO | None = None,
) -> ReelResult:
    """Plan (and unless ``plan_only``, render) the episode's reel. Writes only under ``<desk>/reels/``.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    seconds
        Target length, 6-30 s (a hand-edited plan keeps its own unless given).
    plan_only
        Write and print the plan; render nothing.
    plan_file
        Render this (hand-edited) plan instead of planning.
    take_files, sources, captions
        Which accepted cut, pre-caption source and caption cues per take (see :func:`take_sources`).
    caption_style
        ``house`` / ``plain`` / ``none``; default the desk's.
    watermark_y
        Mark top offset override (never into the top 8%).
    stream
        Progress output (stdout by default).

    Returns
    -------
    ReelResult
        The plan and what was written.
    """

    from creation.post.desk import saved_spine
    from creation.spine_view import episode_summary

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
        given_sources = [
            f"{t}={desk / v['source']}"
            for t, v in (body.get("takes") or {}).items()
            if v.get("source")
        ]
        given_captions = [
            f"{t}={desk / v['captions']}"
            for t, v in (body.get("takes") or {}).items()
            if v.get("captions")
        ]
        sources = [*given_sources, *sources]
        captions = [*given_captions, *captions]
    srcs = take_sources(
        desk, episode, take_files=take_files, sources=sources, captions=captions
    )
    print(
        "Reel from rendered footage (local, $0, no server call): "
        + "; ".join(
            f"{s.take_id} {s.source.name} + captions {s.captions.name if s.captions else 'none'}"
            for s in srcs
        ),
        file=out,
        flush=True,
    )
    takes = [measure_take(desk, episode, s) for s in srcs]
    whole = captions_whole_lines(spine)
    if body is None:
        beats = episode_beats(spine, episode, [s.take_id for s in srcs])
        plan = plan_reel(episode, takes, beats, seconds=seconds)
        patches = [p for s in srcs for p in s.patches]
    else:
        plan = ReelPlan(
            episode=episode,
            seconds=float(body.get("seconds") or seconds),
            segments=segments_from_json(body),
            notes=[f"hand-edited plan `{plan_file.name}`"],
            strongest=strongest_from_json(body.get("strongest")),
        )
        plan.warnings += check_plan(plan, takes)
        patches = list(body.get("patches") or [])
    for s in srcs:
        if s.record is not None and not s.record.complete:
            plan.warnings.append(
                f"{s.take_id}: its finish record `{s.record.path.name if s.record.path else '?'}` is not complete "
                "(a sound part was missing at finish): check the accepted cut is this one"
            )
        if s.captions is None:
            plan.warnings.append(
                f"{s.take_id}: no accepted caption cues (.ass); the reel is uncaptioned there"
            )
        takes_dir = s.source.parent
        for variant in sorted(
            takes_dir.glob(f"take-ep{episode:02d}-{s.take_id}-*blur*.mp4")
        ):
            if not any(p.get("take") == s.take_id for p in patches):
                plan.warnings.append(
                    f"{s.take_id}: `{variant.name}` is a blurred variant made after finish; its patch is not on the "
                    "pre-caption source: add it to the plan's patches (--plan-only, edit, --plan FILE)"
                )
    paths = reel_paths(desk, episode)
    extra = {
        "language": "ja/ko/other: whole English lines" if whole else "en: word flicker",
        "edited_from": plan_file.name if plan_file else None,
        "patches": patches,
    }
    payload = plan_json(
        plan, takes={s.take_id: s.as_json(desk) for s in srcs}, extra=extra
    )
    write_new(paths["plan"], json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    for line in plan.lines():
        print(line, file=out)
    result = ReelResult(plan=plan, plan_path=paths["plan"])
    if plan_only:
        print(
            f"Plan: {paths['plan']} (edit it, then: reel --desk D --episode {episode} --plan FILE)",
            file=out,
        )
        return result
    style, style_note = resolve_caption_style(desk, caption_style)
    secs, loud, cap_line, report = render_reel(
        desk, episode=episode, plan=plan, sources=srcs, takes=takes, patches=patches,
        paths=paths, caption_style=style, whole_lines=whole, watermark_y=watermark_y,
    )  # fmt: skip
    summary = episode_summary(spine, episode)
    write_new(
        paths["post"],
        post_text(
            series=str(spine.get("title") or desk.name),
            episode=episode,
            title=str(summary.get("title") or ""),
            question=str(summary.get("hook_question") or ""),
            genre=str(spine.get("microdrama_genre") or ""),
        ),  # fmt: skip
    )
    result.video, result.post, result.seconds, result.loudness, result.captions = (
        paths["video"], paths["post"], secs, loud, cap_line,
    )  # fmt: skip
    result.ass = paths["ass"] if paths["ass"].exists() else None
    result.lines = report + ([style_note] if style_note else [])
    for line in result.lines:
        print(f"- {line}", file=out)
    for key in ("video", "ass", "plan", "post"):
        if paths[key].exists():
            print(f"{key}: {paths[key]}", file=out)
    print(result.summary(), file=out)
    return result


__all__ = [
    "REELS_DIR",
    "ReelResult",
    "TakeSource",
    "cut_sound",
    "episode_beats",
    "measure_take",
    "parse_ass_cues",
    "reel_paths",
    "render_reel",
    "run_reel",
    "segment_map",
    "take_sources",
    "write_new",
]
