"""Join finished takes into one file: an episode's 30 s / 60 s cut, or a series cut across episodes.

Free: ffmpeg on this laptop, no paid call (the bed is the one already pinned).

What ``join`` does, in order:

1. **Parts.** For ``--episode N`` the newest complete finish record of every
   take, in take order; ``--episodes`` does that per episode for a series cut;
   ``--take-file`` names finished takes in the order given. A take needs a
   finish record (``take-epNN-tK-finish-vN.json``, written by ``finish``): it
   names the take before the bed (``pre_bed``) and the un-marked captioned
   picture (``master``). A take edited after ``finish`` (``trim``, ``tempo``,
   ``freeze``, ``soften`` on a file the record names) has its own record, whose
   pre-bed take and master were edited the same way, so it joins like any
   finished take (the newest record is the edited one). A file no record names
   is refused, unless ``--from-record R`` says which recorded file it was made
   from (a re-captioned copy): it then joins as that take, with its own
   picture and the record's sound before the bed, once it has R's size, frame
   count and sound (L-20261001-9). The stand-in is said in the run notes.
2. **Checks, before anything is written.** Every picture 24 fps and the same
   size; the pre-bed take the same length as its picture (one frame of slack).
3. **One bed.** Each take's pre-bed sound (voice, effects, hand cues) is joined
   with its captioned picture, never the finished sound, so no take's own bed
   is stitched in (sound joined on the samples, so every part starts exactly
   on its picture). Each part gets one gain to the parts' median loudness (no
   loudnorm). The show's bed is looped seamlessly (silent head and tail cut,
   1 s equal-power crossfade at each loop point) to cover the whole join, then
   mixed under it by :func:`creation.post.mix.mix_take`: ducked under the
   voice, a measured gain to about -18 LUFS, ONE limiter. The bed is the
   harness's (the harness bed pinned on the desk, else the one the takes were
   finished with, :func:`creation.post.bed.harness_bed`); there is no flag to
   pass a file of your own. When every take already carries the harness's music
   in its own soundtrack (finish record ``music_in_take``) no bed is laid, only
   the gain and limiter; a join that mixes such takes with bedded ones is refused.
4. **Seams.** Takes of one episode are filmed to hand off on the same frame, so
   they meet on a straight cut; episodes meet on a 0.25 s dissolve (picture
   ``xfade`` and sound ``acrossfade``). ``--dissolve S`` sets every seam.
5. **Captions** are the ones ``finish`` burned on each take, timed on that
   take's own voice; they move with their picture, so they land on the joined
   timeline by construction (a caption inside a dissolve fades with it).
6. **Measure.** Frames / seconds of the joined file must be 24; the room level
   either side of each seam (median of 0.1 s RMS over 2 s) must step under
   5 dB. Known speech (the take's saved words, else its take-facts line
   windows) is left out of both sides, so a line starting on the cut is not a
   room jump; a side that is speech end to end is measured as it is. Both
   sides' levels are printed. A bigger step stops the join: the un-marked
   master is kept to listen to, nothing is marked, and the CLI exits 5, unless
   a human passes ``--accept-seam "why" --accepted-by NAME`` (recorded in the
   run notes). On a desk created on or after 6 Oct 2026 ``join`` first fixes
   the seam itself, free (:mod:`creation.post.seam_fix`: a steady bed under the
   quiet side, then a silent-head trim on a filmed cut) and stops only when that
   is not enough, listing what it tried; ``--no-seam-fix`` turns it off. An
   older desk measures and refuses exactly as before.
7. **Mark once** on the joined master (the parts are un-marked); the master
   stays beside the marked file.

A letterbox show (every part's finish record says ``letterbox``: its master
is the 9:16 canvas, captioned in the band under the picture) is joined the
same way, and step 7 puts the Sokii mark in the top band and the title block
above the picture (:func:`creation.post.letterbox.mark_and_title`: the setup
line white, the hook line yellow; ``--hook-line`` / ``--no-hook-line``) on the
joined file once. The opening and tail checks read the picture only. The
captions are the ones each take was finished with (``--caption-colour`` on
``finish``).

Outputs never overwrite: ``epNN/takes/episode-epNN-join-vN.mp4`` (master) and
``episode-epNN-join-sokii-vN.mp4``; a series cut goes to ``shared/cuts/``.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.ops.state import episode_by_ordinal, load_series
from creation.post.bed import (
    MUSIC_IS_HARNESS,
    bed_level,
    chosen_record_level,
    harness_bed,
    pinned_bed,
)
from creation.post.finish_record import (
    FinishRecord,
    finish_records,
    latest_finish_record,
    record_for_file,
)
from creation.post.media import (
    MediaToolError,
    count_frames,
    ffmpeg_bin,
    ffprobe_bin,
    measure_loudness,
    measure_rms_windows,
    media_duration,
    probe_video,
    run_ffmpeg,
)
from creation.post.mix import (
    BED_FADE_IN_SECONDS,
    BED_FADE_OUT_SECONDS,
    check_duck_db,
    mix_take,
)
from creation.post.seam_fix import (
    Joined,
    SeamBed,
    SeamFixReport,
    SteadyCue,
    Trim,
    find_steady_cue,
    fix_seams,
    lay_beds,
    plan_silent_trim,
)
from creation.post.seam_fix import keep as keep_seam_fix
from creation.post.watermark import watermark
from creation.rules_epoch import continuing_fix, is_legacy
from creation.harness_rules import (
    gain_match_note,
    inner_voice_cues,
    seam_fix_line,
    seam_fix_tried_line,
    thought_short_of_cut,
)

HOUSE_FPS = 24.0
#: How far a file's rate (r_frame_rate, and counted frames / seconds on the join) may be off 24.
FPS_TOLERANCE = 0.2
#: Takes of one episode hand off on the same frame: a straight cut.
TAKE_DISSOLVE_SECONDS = 0.0
#: Episodes in a series cut meet on a short dissolve.
EPISODE_DISSOLVE_SECONDS = 0.25
#: A room-level step across a seam larger than this is audible.
SEAM_STEP_DB = 5.0
#: Seconds measured either side of a seam.
SEAM_WINDOW_SECONDS = 2.0
#: Where two EPISODES meet in a series cut, the last seconds of the one and the first seconds of the
#: next are levelled toward each other (L-20260930-12, Noodle24: a quiet music ending into a food
#: opening stepped 5-9 dB). Each side moves half the step, held over the seam window and ramped (in dB)
#: back to the take's own level over the ramp, so the rest of each episode is untouched.
EPISODE_SEAM_HOLD_SECONDS = 2.0
EPISODE_SEAM_RAMP_SECONDS = 2.0
#: Steps this small are left as they are.
EPISODE_SEAM_LEVEL_FLOOR_DB = 1.0
#: The most an episode seam is levelled (both sides together); a bigger step is levelled this far and
#: then still stops the join (a real loud seam).
EPISODE_SEAM_LEVEL_MAX_DB = 10.0
#: Speech is left out of the room level with this much margin either side (word times are rough,
#: and the bed's duck ramps back up after a line).
SPEECH_PAD_SECONDS = 0.3
#: A side needs at least this many 0.1 s windows without speech to be measured on them alone;
#: the search reaches out this far for them before falling back to every window.
SEAM_QUIET_WINDOWS = 5
SEAM_SEARCH_SECONDS = 6.0
#: Loop points in the bed are crossfaded over this long (at most a quarter of the bed).
BED_LOOP_CROSSFADE_SECONDS = 1.0
#: A bed's head and tail quieter than this are cut before it is looped.
BED_SILENCE_DB = -50
BED_RATE = 48000
#: ``join`` exit code: the master was written but a seam steps too far; nothing marked.
JOIN_NOT_DONE = 5


@dataclass(frozen=True)
class JoinPart:
    """One take in the join: its captioned un-marked picture and its sound before the bed."""

    episode: int
    take_id: str
    picture: Path
    pre_bed: Path
    record: FinishRecord
    #: Set when the picture is a copy made from a recorded file (``--from-record``): what it stands in for.
    stands_in: str | None = None

    @property
    def label(self) -> str:
        """``ep01 t2``."""

        return f"ep{self.episode:02d} {self.take_id}"


@dataclass(frozen=True)
class SeamLevel:
    """The room level either side of one seam (median of 0.1 s RMS, dB).

    ``*_speech_out`` is true when known speech (the take's words or line
    windows) was left out of that side; ``*_all`` is true when that side is
    speech end to end, so every window was measured (a quiet tail against
    wall-to-wall speech still reads as the jump it is).
    """

    before_db: float
    after_db: float
    before_speech_out: bool = False
    after_speech_out: bool = False
    before_all: bool = False
    after_all: bool = False

    @property
    def step_db(self) -> float:
        """After minus before."""

        return round(self.after_db - self.before_db, 1)

    def text(self) -> str:
        """``before -42.0 dB, after -31.5 dB (speech left out)``."""

        def side(name: str, level: float, out: bool, every: bool) -> str:
            how = (
                " speech left out"
                if out
                else (" all speech, measured as is" if every else "")
            )
            return f"{name} {level:.1f} dB{how}"

        return (
            side("before", self.before_db, self.before_speech_out, self.before_all)
            + ", "
            + side("after", self.after_db, self.after_speech_out, self.after_all)
        )

    def as_json(self) -> dict[str, Any]:
        """Both sides' levels for the JSON report."""

        return {
            "before_db": self.before_db,
            "after_db": self.after_db,
            "before_speech_left_out": self.before_speech_out,
            "after_speech_left_out": self.after_speech_out,
        }


@dataclass
class JoinResult:
    """What the join wrote and measured."""

    parts: list[JoinPart]
    master: Path
    marked: Path | None
    #: ``None`` when every take carries the harness's music in its own soundtrack.
    bed: Path | None
    gains_db: list[float]
    dissolves: list[float]
    seams: list[float]
    seam_steps_db: list[float]
    fps: float
    loudness: str
    mix_line: str
    notes: list[str] = field(default_factory=list)
    #: Each seam's room level either side, and how it was measured (default: unknown).
    seam_levels: list[SeamLevel] = field(default_factory=list)
    #: ``{"by": ..., "why": ..., "seams": [...]}`` when a human accepted loud seams with ``--accept-seam``.
    accepted: dict[str, Any] | None = None
    #: The bed's level across the join (:func:`creation.post.bed.bed_level`).
    bed_db: float | None = None
    #: What ``join`` laid / trimmed on its loud seams (new desks; ``None`` when it tried nothing).
    seam_fix: SeamFixReport | None = None

    @property
    def loud_seams(self) -> list[tuple[float, float]]:
        """``(seam seconds, step dB)`` for every seam that steps more than 5 dB."""

        return [
            (s, d)
            for s, d in zip(self.seams, self.seam_steps_db, strict=True)
            if abs(d) > SEAM_STEP_DB
        ]

    @property
    def complete(self) -> bool:
        """Marked, and every seam steps under 5 dB (or a human accepted the loud ones)."""

        return self.marked is not None and (
            not self.loud_seams or self.accepted is not None
        )

    def summary_lines(self) -> list[str]:
        """Operator lines."""

        levels = self.seam_levels or [None] * len(self.seams)
        seams = ", ".join(
            f"{s:.2f}s {'cut' if d == 0 else f'{d:.2f}s dissolve'} {step:+.1f} dB"
            + (f" ({level.text()})" if level is not None else "")
            for s, d, step, level in zip(
                self.seams, self.dissolves, self.seam_steps_db, levels, strict=True
            )
        )
        lines = [
            f"Joined: {self.marked or self.master}",
            f"Master (un-marked): {self.master}",
            "Parts: "
            + ", ".join(
                f"{p.label} `{p.picture.name}` {g:+.1f} dB"
                for p, g in zip(self.parts, self.gains_db)
            ),
            (
                f"Bed: one bed across the join, `{self.bed.name}`; {self.mix_line}"
                if self.bed is not None
                else f"Bed: none, every take carries the harness's music in its own soundtrack; {self.mix_line}"
            ),
            f"Seams: {seams}",
            f"Frames / seconds: {self.fps:.2f}; loudness {self.loudness}",
        ]
        if self.seam_fix is not None:
            lines += self.seam_fix.lines(SEAM_STEP_DB)
        if self.accepted is not None:
            lines.append(
                f"Seam accepted by {self.accepted['by']}: {self.accepted['why']} "
                f"(seams {', '.join(f'{at:.2f}s {step:+.1f} dB' for at, step in self.loud_seams)})"
            )
        lines += [f"- {note}" for note in self.notes]
        return lines

    def as_json(self) -> dict[str, Any]:
        """JSON report."""

        levels = self.seam_levels
        return {
            "master": str(self.master),
            "marked": str(self.marked) if self.marked else None,
            "complete": self.complete,
            "parts": [{"episode": p.episode, "take_id": p.take_id, "picture": str(p.picture),
                       "pre_bed": str(p.pre_bed)} for p in self.parts],
            "gains_db": self.gains_db,
            "seams": [{"at": s, "dissolve": d, "step_db": step,
                       **(levels[i].as_json() if i < len(levels) else {})}
                      for i, (s, d, step) in enumerate(zip(self.seams, self.dissolves, self.seam_steps_db,
                                                           strict=True))],
            "fps": self.fps,
            "loudness": self.loudness,
            "bed_db": self.bed_db,
            "accepted": self.accepted,
            **({"seam_fix": self.seam_fix.as_json()} if self.seam_fix is not None else {}),
        }  # fmt: skip


# --- parts --------------------------------------------------------------------------------------


def _take_order(take_id: str) -> int:
    digits = take_id.lstrip("t")
    return int(digits) if digits.isdigit() else 0


def not_done_reason(record: FinishRecord) -> str:
    """What a NOT DONE finish record lacks, in the words ``finish`` used.

    The record names it (``missing``); an older record does not, and the reason
    is then read from what it holds (no take before the bed: the mix never ran),
    else the operator is pointed at that finish's ``Sound:`` line.
    """

    if record.missing:
        return "no " + ", ".join(record.missing)
    if not record.pre_bed:
        return "no mix (the finish stopped before it)"
    return (
        "the record does not say what is missing (an older finish); read the `Sound:` line of that "
        "finish in the episode's run-notes.md"
    )


def _part(desk: Path, record: FinishRecord, *, asked: Path | None = None) -> JoinPart:
    name = asked.name if asked else f"ep{record.episode:02d} {record.take_id}"
    if not record.complete or not record.pre_bed:
        raise ValueError(
            f"{name}: its finish record `{record.path.name if record.path else '?'}` is NOT DONE "
            f"({not_done_reason(record)}); finish the take again before joining it"
        )
    picture = record.resolve(desk, "master")
    pre_bed = record.resolve(desk, "pre_bed")
    if picture is None or not picture.is_file():
        raise FileNotFoundError(
            f"{name}: the captioned picture its finish record names is gone: {picture}"
        )
    if pre_bed is None or not pre_bed.is_file():
        raise FileNotFoundError(
            f"{name}: the take before the bed its finish record names is gone: {pre_bed}"
        )
    return JoinPart(record.episode, record.take_id, picture, pre_bed, record)


def episode_parts(desk: Path, episode: int) -> list[JoinPart]:
    """The newest finished file of every take of one episode, in take order.

    Raises
    ------
    ValueError
        When a take on the desk has no complete finish record.
    """

    try:
        desk_takes = [
            take.take_id
            for take in episode_by_ordinal(load_series(desk), episode).takes
        ]
    except (FileNotFoundError, ValueError, KeyError):
        desk_takes = []
    takes_dir = desk / f"ep{episode:02d}" / "takes"
    recorded = {
        path.name.split("-")[2]
        for path in takes_dir.glob(f"take-ep{episode:02d}-t*-finish-v*.json")
    }
    parts: list[JoinPart] = []
    missing: list[str] = []
    for take_id in sorted(set(desk_takes) | recorded, key=_take_order):
        record = latest_finish_record(desk, episode, take_id)
        if record is None:
            tried = [r for r in finish_records(desk, episode) if r.take_id == take_id]
            missing.append(
                f"{take_id} (its newest finish `{tried[-1].path.name if tried[-1].path else '?'}` is "
                f"not done: {not_done_reason(tried[-1])})"
                if tried
                else f"{take_id} (never finished)"
            )
            continue
        parts.append(_part(desk, record))
    if missing:
        raise ValueError(
            f"ep{episode:02d}: no finished {', '.join(missing)}. Fix what is missing and run "
            "`fictora-produce finish` on that take again, or name the takes with --take-file"
        )
    return parts


#: A copy's sound may differ from the recorded file's by this much (median over 0.1 s windows) and
#: still be the same sound: a stream copy is 0 dB, an AAC re-encode a fraction of a dB.
COPY_SOUND_MEDIAN_DB = 1.0
#: ... and by this much in its worst tenth of windows.
COPY_SOUND_P90_DB = 3.0
#: Windows quieter than this on both files are silence and not compared.
COPY_SOUND_FLOOR_DB = -60.0


def _sound_matches(copy: Path, source: Path) -> tuple[bool, str]:
    a = list(measure_rms_windows(copy, window_seconds=0.1))
    b = list(measure_rms_windows(source, window_seconds=0.1))
    if abs(len(a) - len(b)) > 1:
        return False, f"{len(a) / 10:.1f}s of sound against {len(b) / 10:.1f}s"
    pairs = [abs(x - y) for x, y in zip(a, b) if max(x, y) > COPY_SOUND_FLOOR_DB]
    if not pairs:
        return True, "both silent"
    median = statistics.median(pairs)
    p90 = sorted(pairs)[int(0.9 * (len(pairs) - 1))]
    words = (
        f"level differs by {median:.1f} dB median, {p90:.1f} dB at the 90th percentile"
    )
    return median <= COPY_SOUND_MEDIAN_DB and p90 <= COPY_SOUND_P90_DB, words


def _stand_in(desk: Path, file: Path, source: Path) -> JoinPart:
    """A copy of a recorded file (``--from-record``) as that take's part, once it is the same take."""

    asked = file.expanduser().resolve()
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"--from-record: not found: {source}")
    record = record_for_file(desk, source)
    if record is None:
        raise ValueError(
            f"--from-record {source.name}: no finish record names that file either. Name the finished "
            "file the copy was made from (the take's -cap master)."
        )
    which = next(
        name
        for name in ("final", "master", "pre_bed")
        if (p := record.resolve(desk, name)) is not None and p.resolve() == source
    )
    if which == "final":
        raise ValueError(
            f"--from-record {source.name} is the marked final: a copy of it carries the mark, and join "
            f"marks the joined file once. Make the copy from the un-marked master "
            f"`{Path(record.master).name}` and pass that with --from-record."
        )
    base = _part(desk, record, asked=asked)
    copy_info, source_info = probe_video(asked), probe_video(source)
    problems = []
    if (copy_info.width, copy_info.height) != (source_info.width, source_info.height):
        problems.append(
            f"it is {copy_info.width}x{copy_info.height}, the recorded file {source_info.width}x{source_info.height}"
        )
    frames, source_frames = count_frames(asked), count_frames(source)
    if abs(frames - source_frames) > 1:
        problems.append(
            f"not the same length: {frames} frames against the recorded file's {source_frames}"
        )
    if not copy_info.has_audio:
        problems.append("it has no sound")
    else:
        same, words = _sound_matches(asked, source)
        if not same:
            problems.append(f"its sound is not the recorded file's ({words})")
    if problems:
        raise ValueError(
            f"{asked.name} cannot stand in for {source.name}: {'; '.join(problems)}. --from-record takes "
            "a copy of a recorded file with only the picture changed (re-captioned): same size, same "
            "frames, same sound."
        )
    _ok, words = _sound_matches(asked, source)
    note = (
        f"{asked.name} stands in for {source.name} (ep{record.episode:02d} {record.take_id}, "
        f"record {record.path.name if record.path else '?'}): same size, {frames} frames, sound {words}; "
        "its picture is joined, with the record's sound before the bed"
    )
    return JoinPart(
        record.episode, record.take_id, asked, base.pre_bed, record, stands_in=note
    )


def file_parts(
    desk: Path, files: tuple[Path, ...], from_records: tuple[Path, ...] = ()
) -> list[JoinPart]:
    """Parts for explicit finished files, in the order given.

    Parameters
    ----------
    desk
        Series desk.
    files
        ``--take-file`` paths in order.
    from_records
        ``--from-record`` paths: the Nth names the recorded file the Nth ``--take-file`` that no
        record names was copied from (a re-captioned copy).

    Raises
    ------
    ValueError
        When a file has no finish record naming it and no ``--from-record`` for it, or a copy is
        not the same take as the recorded file it names.
    """

    pending = list(from_records)
    parts = []
    for file in files:
        if not file.expanduser().is_file():
            raise FileNotFoundError(f"take not found: {file}")
        record = record_for_file(desk, file)
        if record is None and pending:
            parts.append(_stand_in(desk, file, pending.pop(0)))
            continue
        if record is None:
            raise ValueError(
                f"{file.name}: no finish record names this file, so its sound before the bed is unknown and "
                "one bed cannot go across the join. Pass a file `finish` made (the -sokii final or the "
                "un-marked -cap file), or the output of trim / tempo / freeze / soften run on one of them "
                "(those carry the record). A copy of a recorded file with only the picture changed (a "
                "re-caption) joins with --from-record <the recorded file it was made from>. A file edited "
                "from a file no record names (a raw take, a file edited by hand), or edited while a file "
                "its record names was gone, has none: run finish on the take again, then edit the new "
                "finished file."
            )
        parts.append(_part(desk, record, asked=file))
    if pending:
        raise ValueError(
            f"--from-record {', '.join(p.name for p in pending)}: every --take-file is already one a finish "
            "record names, so there is nothing for it to stand in for"
        )
    return parts


# --- checks -------------------------------------------------------------------------------------


def video_seconds(path: Path) -> float:
    """Length of the first video stream (not the container, which counts the audio's priming)."""

    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
         "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    try:
        return float(json.loads(result.stdout)["streams"][0]["duration"])
    except (KeyError, IndexError, ValueError, TypeError):
        return probe_video(path).duration_seconds


def assert_house_fps(path: Path) -> float:
    """Counted frames / video seconds of ``path``; refuse anything off 24.

    A stream-copy join once froze a second half and a naive re-encode ran at
    half rate; both reported the right length. Counting frames catches both.

    Raises
    ------
    MediaToolError
        When the measured rate is off 24 by more than 0.2.
    """

    seconds = video_seconds(path)
    frames = count_frames(path)
    fps = frames / seconds if seconds > 0 else 0.0
    if abs(fps - HOUSE_FPS) > FPS_TOLERANCE:
        raise MediaToolError(
            f"{path.name}: {frames} frames / {seconds:.2f}s = {fps:.2f} fps, not 24"
        )
    return fps


def check_parts(parts: list[JoinPart]) -> list[float]:
    """Refuse mixed sizes, anything not 24 fps, or a pre-bed take off its picture's length.

    Returns
    -------
    list[float]
        Each part's length in seconds (frames / 24).

    Raises
    ------
    ValueError
        With every offending file named.
    """

    if len(parts) < 2:
        raise ValueError(
            f"nothing to join: {len(parts)} finished take(s) found; a join needs two or more"
        )
    infos = [probe_video(part.picture) for part in parts]
    problems: list[str] = []
    listing = "; ".join(
        f"{p.label} {i.width}x{i.height} {i.fps:.2f} fps" for p, i in zip(parts, infos)
    )
    off_rate = [
        p.label for p, i in zip(parts, infos) if abs(i.fps - HOUSE_FPS) > FPS_TOLERANCE
    ]
    if off_rate:
        problems.append(f"not 24 fps: {', '.join(off_rate)}")
    if len({(i.width, i.height) for i in infos}) > 1:
        problems.append("mixed resolutions")
    if problems:
        raise ValueError(
            f"join refused ({'; '.join(problems)}): {listing}. Every take must be 24 fps at one size."
        )
    lengths = []
    for part in parts:
        frames = count_frames(part.picture)
        seconds = frames / HOUSE_FPS
        sound = media_duration(part.pre_bed)
        if not probe_video(part.pre_bed).has_audio:
            raise ValueError(
                f"{part.label}: the take before the bed `{part.pre_bed.name}` has no sound"
            )
        if abs(sound - seconds) > 1.0 / HOUSE_FPS + 0.05:
            raise ValueError(
                f"{part.label}: the take before the bed `{part.pre_bed.name}` runs {sound:.2f}s but its picture "
                f"`{part.picture.name}` runs {seconds:.2f}s; they must share one timeline"
            )
        lengths.append(seconds)
    return lengths


def seam_dissolves(parts: list[JoinPart], dissolve: float | None) -> list[float]:
    """Dissolve per seam: a cut between takes of one episode, 0.25 s between episodes, or ``dissolve`` everywhere."""

    if dissolve is not None and dissolve < 0:
        raise ValueError(f"--dissolve must be 0 or more; got {dissolve}")
    return [
        dissolve if dissolve is not None
        else (TAKE_DISSOLVE_SECONDS if a.episode == b.episode else EPISODE_DISSOLVE_SECONDS)
        for a, b in zip(parts, parts[1:])
    ]  # fmt: skip


# --- sound --------------------------------------------------------------------------------------


#: Momentary (400 ms) loudness below this is silence, as in EBU R128's absolute gate.
BODY_ABSOLUTE_GATE_LUFS = -70.0
#: Momentary loudness this far below the take's gated mean is silence too (R128's relative gate).
BODY_RELATIVE_GATE_LU = 10.0
#: The relative gate's mean is read over this quietest share of the moments.
BODY_GATE_SHARE = 0.75
#: A moment this far above the take's median moment is a peak (a jumpscare, a scream, a slam),
#: not the take's level: the gain match leaves it out (L-20261006-6).
BODY_PEAK_LU = 8.0


def momentary_loudness(path: Path) -> list[float]:
    """A file's momentary loudness (LUFS, 400 ms windows every 100 ms), from ffmpeg's ``ebur128`` log.

    Raises
    ------
    MediaToolError
        When ffmpeg fails.
    """

    result = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128",
         "-f", "null", "-"],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    if result.returncode != 0:
        raise MediaToolError(
            f"loudness scan failed on {path.name}: {result.stderr.strip()[-300:]}"
        )
    return [float(v) for v in re.findall(r"\bM:\s*(-?[0-9.]+)", result.stderr)]


def _energy_mean(levels: list[float]) -> float:
    return 10 * math.log10(sum(10 ** (v / 10) for v in levels) / len(levels))


def body_loudness(momentary: list[float]) -> float:
    """A take's loudness without its short loud peaks (LUFS); ``-inf`` for silence.

    The R128 gates first (absolute -70 LUFS, then 10 LU under the mean of the
    quietest :data:`BODY_GATE_SHARE` of the gated moments),
    then every moment more than :data:`BODY_PEAK_LU` above the median moment
    is left out, so one loud jumpscare does not read as the whole take being
    loud. With no peak it is the take's integrated loudness (within the 100 ms
    step).

    Parameters
    ----------
    momentary
        Momentary loudness values (:func:`momentary_loudness`).

    Returns
    -------
    float
        The energy mean of the moments kept.
    """

    gated = [v for v in momentary if v > BODY_ABSOLUTE_GATE_LUFS]
    if not gated:
        return -math.inf
    # The relative gate's mean leaves out the loudest quarter: a jumpscare would lift it over the body.
    quiet = sorted(gated)[: max(1, math.ceil(len(gated) * BODY_GATE_SHARE))]
    floor = _energy_mean(quiet) - BODY_RELATIVE_GATE_LU
    gated = [v for v in gated if v > floor] or gated
    ceiling = statistics.median(gated) + BODY_PEAK_LU
    return _energy_mean([v for v in gated if v <= ceiling] or gated)


def match_gains(parts: list[JoinPart], *, desk: Path) -> list[float]:
    """One gain per part to the parts' median loudness (silent parts get 0).

    A legacy desk (made before 6 Oct 2026) matches whole-take integrated
    loudness, as it always did. A new desk matches each take's
    :func:`body_loudness`, so a loud jumpscare in one take no longer ducks
    the whole take (L-20261006-6).
    """

    if is_legacy(desk):
        levels = [measure_loudness(part.pre_bed) for part in parts]
    else:
        levels = [body_loudness(momentary_loudness(part.pre_bed)) for part in parts]
    heard = [level for level in levels if math.isfinite(level)]
    if not heard:
        return [0.0] * len(parts)
    target = statistics.median(heard)
    return [
        round(target - level, 1) if math.isfinite(level) else 0.0 for level in levels
    ]


#: ``finish`` lands every take here; parts all this close already match, and matching them again
#: only moves a quiet tail against a spoken open (Three Payments Late: a seam widened 5.0 -> 7.4 dB).
FINISHED_LUFS = -18.0
FINISHED_LUFS_TOLERANCE = 0.5


def finished_levels(desk: Path, parts: list[JoinPart]) -> list[float | None]:
    """Each part's finished loudness (its record's ``final``), or ``None`` when it cannot be measured."""

    levels: list[float | None] = []
    for part in parts:
        final = part.record.resolve(desk, "final")
        try:
            level = (
                measure_loudness(final)
                if final is not None and final.is_file()
                else None
            )
        except MediaToolError:
            level = None
        levels.append(level if level is not None and math.isfinite(level) else None)
    return levels


def gain_match_needed(levels: list[float | None]) -> tuple[bool, str]:
    """Whether ``join`` should gain-match by default, and why.

    Parameters
    ----------
    levels
        Each part's finished loudness (LUFS); ``None`` when not measured.

    Returns
    -------
    tuple[bool, str]
        False when every part finished within ±0.5 LU of -18 LUFS (they
        already match), else True; and the reason in words.
    """

    shown = ", ".join("not measured" if v is None else f"{v:.1f}" for v in levels)
    if levels and all(
        v is not None and abs(v - FINISHED_LUFS) <= FINISHED_LUFS_TOLERANCE + 1e-9
        for v in levels
    ):
        return False, (
            f"every take finished within ±{FINISHED_LUFS_TOLERANCE:g} LU of {FINISHED_LUFS:g} LUFS ({shown})"
        )
    return (
        True,
        f"the takes finished at {shown} LUFS, not all within ±{FINISHED_LUFS_TOLERANCE:g} LU of {FINISHED_LUFS:g}",
    )


def decode_stereo(path: Path) -> np.ndarray:
    """A file's sound as float samples, stereo at 48 kHz, shape ``(samples, 2)``.

    Raises
    ------
    MediaToolError
        When ffmpeg cannot decode it.
    """

    decoded = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", "2", "-ar", str(BED_RATE), "-"],
        stdin=subprocess.DEVNULL, capture_output=True, check=False,
    )  # fmt: skip
    if decoded.returncode != 0:
        raise MediaToolError(
            f"could not decode the sound of {path.name}: {decoded.stderr.decode(errors='replace')[-300:]}"
        )
    return (
        np.frombuffer(decoded.stdout, dtype=np.float32)
        .reshape(-1, 2)
        .astype(np.float64)
    )


def write_wav(samples: np.ndarray, out: Path) -> Path:
    """Write float stereo samples as a 32-bit float WAV (no clipping before the one limiter).

    Raises
    ------
    MediaToolError
        When ffmpeg cannot write it.
    """

    written = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-y", "-f", "f32le", "-ar", str(BED_RATE), "-ac", "2", "-i", "-",
         "-c:a", "pcm_f32le", str(out)],
        input=samples.astype(np.float32).tobytes(), capture_output=True, check=False,
    )  # fmt: skip
    if written.returncode != 0:
        raise MediaToolError(
            f"could not write {out.name}: {written.stderr.decode(errors='replace')[-300:]}"
        )
    return out


def loop_bed(
    bed: Path,
    seconds: float,
    out: Path,
    *,
    crossfade: float = BED_LOOP_CROSSFADE_SECONDS,
) -> Path:
    """Loop ``bed`` seamlessly to at least ``seconds``: silent head and tail cut, equal-power crossfades.

    A generated bed is about 30 s and usually ends on a decay; looping it as it
    is (``-stream_loop``) leaves a dip or a gap at every loop point. Done on the
    samples (numpy): ffmpeg's ``acrossfade`` chained over several inputs
    dropped copies at random in testing, so no sound is joined with it.

    Raises
    ------
    ValueError
        When the bed is silent.
    MediaToolError
        When the bed cannot be decoded, or the loop comes out short.
    """

    samples = decode_stereo(bed)
    loud = np.flatnonzero(np.abs(samples).max(axis=1) > 10 ** (BED_SILENCE_DB / 20))
    if loud.size == 0 or (loud[-1] - loud[0]) < 0.1 * BED_RATE:
        raise ValueError(f"the bed `{bed.name}` is silent")
    body = samples[loud[0] : loud[-1] + 1]
    overlap = int(min(crossfade, len(body) / BED_RATE / 4) * BED_RATE)
    ramp = (np.arange(overlap) + 0.5)[:, None] / max(overlap, 1) * (math.pi / 2)
    fade_out, fade_in = np.cos(ramp), np.sin(ramp)
    need = int(math.ceil(seconds * BED_RATE))
    looped = body
    while len(looped) < need:
        seam = looped[len(looped) - overlap :] * fade_out + body[:overlap] * fade_in
        looped = np.concatenate([looped[: len(looped) - overlap], seam, body[overlap:]])
    write_wav(looped[:need], out)
    covered = media_duration(out)
    if covered < seconds - 0.05:
        raise MediaToolError(
            f"the looped bed covers {covered:.2f}s of {seconds:.2f}s; the tail would lose its music"
        )
    return out


def _in_speech(t: float, speech: list[tuple[float, float]]) -> bool:
    return any(a - SPEECH_PAD_SECONDS <= t < b + SPEECH_PAD_SECONDS for a, b in speech)


def _side(
    levels: np.ndarray,
    indices: range,
    reach: range,
    speech: list[tuple[float, float]],
) -> tuple[float, bool, bool] | None:
    """Median level over ``indices`` with speech left out (``(level, speech_out, all_speech)``).

    When fewer than :data:`SEAM_QUIET_WINDOWS` windows of ``indices`` are free
    of speech, the search walks on through ``reach`` (further from the seam)
    for more; when there are still too few, every window of ``indices`` is
    measured as it is.
    """

    every = [i for i in indices if 0 <= i < len(levels)]
    if not every:
        return None
    if not speech:
        return float(np.median(levels[every])), False, False
    quiet = [i for i in every if not _in_speech(i * 0.1 + 0.05, speech)]
    for i in reach:
        if len(quiet) >= SEAM_QUIET_WINDOWS:
            break
        if 0 <= i < len(levels) and not _in_speech(i * 0.1 + 0.05, speech):
            quiet.append(i)
    if len(quiet) >= SEAM_QUIET_WINDOWS:
        return float(np.median(levels[quiet])), set(quiet) != set(every), False
    return float(np.median(levels[every])), False, True


def seam_levels(
    video: Path,
    seams: list[float],
    *,
    speech: list[tuple[float, float]] | None = None,
    window: float = SEAM_WINDOW_SECONDS,
) -> list[SeamLevel]:
    """Room level either side of each seam, speech left out.

    The level either side is the median of 0.1 s RMS windows over ``window``
    seconds. Known speech (``speech``, joined-timeline seconds from the takes'
    words or line windows) is left out of both sides, so a line that starts
    right after the cut does not read as a room jump (L-20260930-9). When a
    side is speech end to end, the search reaches up to
    :data:`SEAM_SEARCH_SECONDS` from the seam for pauses; failing that, the
    whole side is measured as it is, so a dead-quiet tail against speech is
    still caught. The bed's fade in (first 1 s) and its end click guard are
    left out.
    """

    levels = np.array(measure_rms_windows(video, window_seconds=0.1))
    span = int(round(window / 0.1))
    reach = int(round(SEAM_SEARCH_SECONDS / 0.1))
    # The bed's own fade in and fade out are not a seam: leave them out of both sides.
    first = int(math.ceil(BED_FADE_IN_SECONDS / 0.1))
    last = len(levels) - int(math.ceil(BED_FADE_OUT_SECONDS / 0.1))
    spoken = sorted(speech or [])
    found: list[SeamLevel] = []
    for seam in seams:
        index = int(seam / 0.1)
        before = _side(
            levels,
            range(max(first, index - span), index),
            range(index - span - 1, max(first, index - reach) - 1, -1),
            spoken,
        )
        after = _side(
            levels,
            range(index, min(last, index + span)),
            range(index + span, min(last, index + reach)),
            spoken,
        )
        if before is None or after is None:
            found.append(SeamLevel(0.0, 0.0))
            continue
        found.append(
            SeamLevel(
                before_db=round(before[0], 1),
                after_db=round(after[0], 1),
                before_speech_out=before[1],
                after_speech_out=after[1],
                before_all=before[2],
                after_all=after[2],
            )  # fmt: skip
        )
    return found


def seam_loudness_steps(
    video: Path,
    seams: list[float],
    *,
    speech: list[tuple[float, float]] | None = None,
    window: float = SEAM_WINDOW_SECONDS,
) -> list[float]:
    """Room-level step (after minus before) across each seam; see :func:`seam_levels`."""

    return [
        level.step_db
        for level in seam_levels(video, seams, speech=speech, window=window)
    ]


def windows_through_edits(
    windows: list[tuple[float, float]], edits: Sequence[Mapping[str, Any]]
) -> tuple[list[tuple[float, float]], list[str]]:
    """Move speech windows on the take as filmed through a finish record's edits, oldest first.

    The same re-timing the captions get (:func:`creation.post.edit_captions.span_map`):
    the trim handles keep ``start_s..end_s``, a ``trim`` removes its cut (later
    words move earlier, words inside it go), a ``tempo`` divides the time in its
    window by the factor; ``freeze``, ``soften`` and ``blur`` keep every time.

    Parameters
    ----------
    windows
        ``(start, end)`` seconds on the take as filmed.
    edits
        The finish record's ``edits``; every one must be one :func:`span_map` can re-time.

    Returns
    -------
    tuple[list[tuple[float, float]], list[str]]
        The windows on the edited file (empty ones dropped), and one note per
        edit that moved them (``"to the trim handles"``, ``"through trim …"``).
    """

    from creation.post.edit_captions import SAME_TIMING, span_map

    moved = list(windows)
    how: list[str] = []
    for edit in edits:
        op = str(edit.get("op") or "")
        if op in SAME_TIMING:
            continue
        mapper, why = span_map(edit)
        if mapper is None:
            raise ValueError(f"cannot re-time `{op}`: {why}")
        after: list[tuple[float, float]] = []
        for a, b in moved:
            span = mapper(a, b)
            if span is not None and span[1] > span[0]:
                after.append((round(span[0], 3), round(span[1], 3)))
        moved = after
        how.append(
            "to the trim handles" if op == "handles" else f"through {op} ({why})"
        )
    return moved, how


def part_speech(
    desk: Path, part: JoinPart, seconds: float
) -> tuple[list[tuple[float, float]], str]:
    """Where this take speaks on its own timeline, from the kit's saved words or the take's line windows.

    Returns
    -------
    tuple[list[tuple[float, float]], str]
        Speech windows (seconds into the part) and where they came from, or
        ``[]`` and why none (no transcript or facts saved, or an edit after
        finish moved the sound timeline).
    """

    from creation.post.edit_captions import span_map

    # Every edit after finish (the trim handles, trim, tempo, freeze, soften, blur) is replayed on the
    # words below, in the order it was made (L-20261008-10). Only an edit the kit cannot re-time stops it.
    unknown: list[str] = []
    for edit in part.record.edits:
        mapper, why = span_map(edit)
        if mapper is None:
            unknown.append(f"{edit.get('op') or '?'} ({why})")
    if unknown:
        return (
            [],
            f"{part.label}: speech not left out (edited after finish: {', '.join(unknown)})",
        )
    from creation.post.review import saved_words
    from creation.post.sfx import saved_take_facts
    from creation.post.take_text import line_windows
    from creation.post.whisper import load_words

    windows: list[tuple[float, float]] = []
    source = ""
    words = saved_words(desk, part.episode, part.take_id)
    if words is not None:
        try:
            windows = [
                (w.start, max(w.end, w.start + 0.1))
                for w in load_words(words)
                if w.text
            ]
            source = f"words `{words.name}`"
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
            windows = []
    if not windows:
        facts = saved_take_facts(desk, part.episode, part.take_id)
        if facts is not None:
            try:
                payload = json.loads(facts.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                payload = None
            windows = line_windows(payload if isinstance(payload, dict) else None)
            source = f"line windows `{facts.name}`"
    moved, how = windows_through_edits(windows, part.record.edits)
    if how:
        source += " moved " + "; ".join(how)
    windows = [(a, min(b, seconds)) for a, b in moved if 0 <= a < seconds]
    if not windows:
        return [], f"{part.label}: speech not left out (no saved words or take facts)"
    return windows, f"{part.label}: speech from {source}"


def _window_levels(sound: np.ndarray) -> np.ndarray:
    """RMS level (dB, first channel, as :func:`creation.post.media.measure_rms_windows`) per 0.1 s window."""

    step = int(round(0.1 * BED_RATE))
    count = len(sound) // step
    if count == 0:
        return np.zeros(0)
    mono = sound[: count * step, 0].reshape(count, step)
    rms = np.sqrt(np.mean(mono**2, axis=1))
    with np.errstate(divide="ignore"):
        levels = 20 * np.log10(rms)
    return np.maximum(np.nan_to_num(levels, nan=-120.0, neginf=-120.0), -120.0)


def _edge_level(
    sound: np.ndarray, speech: list[tuple[float, float]], *, tail: bool
) -> float | None:
    """Room level of a part's last (``tail``) or first seconds, speech left out as at a seam."""

    levels = _window_levels(sound)
    n = len(levels)
    span = int(round(SEAM_WINDOW_SECONDS / 0.1))
    reach = int(round(SEAM_SEARCH_SECONDS / 0.1))
    if tail:
        side = _side(levels, range(max(0, n - span), n),
                     range(n - span - 1, max(0, n - reach) - 1, -1), sorted(speech))  # fmt: skip
    else:
        side = _side(
            levels, range(0, min(n, span)), range(span, min(n, reach)), sorted(speech)
        )
    return None if side is None else side[0]


@dataclass(frozen=True)
class SeamRide:
    """How far the end of one episode and the start of the next are levelled toward each other (dB)."""

    index: int
    tail_db: float
    head_db: float
    step_db: float

    def text(self, parts: list[JoinPart]) -> str:
        """One line for the run notes."""

        a, b = parts[self.index], parts[self.index + 1]
        left = self.step_db - (self.head_db - self.tail_db)
        rest = (
            f"; {left:+.1f} dB left (levelled at most {EPISODE_SEAM_LEVEL_MAX_DB:g} dB)"
            if abs(left) > 0.05
            else ""
        )
        return (
            f"episode seam {a.label} | {b.label} stepped {self.step_db:+.1f} dB: levelled, "
            f"{a.label}'s last {EPISODE_SEAM_HOLD_SECONDS + EPISODE_SEAM_RAMP_SECONDS:g} s {self.tail_db:+.1f} dB, "
            f"{b.label}'s first {EPISODE_SEAM_HOLD_SECONDS + EPISODE_SEAM_RAMP_SECONDS:g} s {self.head_db:+.1f} dB"
            f"{rest}"
        )


def episode_seam_rides(
    parts: list[JoinPart],
    sounds: list[np.ndarray],
    speech: list[list[tuple[float, float]]],
) -> list[SeamRide]:
    """The level ride for each seam where two episodes meet (L-20260930-12).

    Each part's room level is read on its own sound (the 2 s at the edge,
    speech left out, reaching up to 6 s for pauses, as :func:`seam_levels`);
    the step is split between the two sides, capped at
    :data:`EPISODE_SEAM_LEVEL_MAX_DB`. Takes of one episode are never ridden.

    Parameters
    ----------
    parts
        The parts in order.
    sounds
        Each part's sound as it goes into the join (gained, trimmed).
    speech
        Each part's speech windows, seconds on that sound.

    Returns
    -------
    list[SeamRide]
        One per episode seam that steps more than :data:`EPISODE_SEAM_LEVEL_FLOOR_DB`.
    """

    rides: list[SeamRide] = []
    for index in range(len(parts) - 1):
        if parts[index].episode == parts[index + 1].episode:
            continue
        before = _edge_level(sounds[index], speech[index], tail=True)
        after = _edge_level(sounds[index + 1], speech[index + 1], tail=False)
        if before is None or after is None or min(before, after) <= -119.0:
            continue
        step = after - before
        if abs(step) <= EPISODE_SEAM_LEVEL_FLOOR_DB:
            continue
        levelled = max(-EPISODE_SEAM_LEVEL_MAX_DB, min(EPISODE_SEAM_LEVEL_MAX_DB, step))
        rides.append(
            SeamRide(
                index, round(levelled / 2, 2), round(-levelled / 2, 2), round(step, 1)
            )
        )
    return rides


def _ride(sound: np.ndarray, db: float, *, tail: bool) -> np.ndarray:
    """``sound`` with ``db`` held over its edge and ramped (in dB) back to 0 over the ramp before it."""

    hold = int(round(EPISODE_SEAM_HOLD_SECONDS * BED_RATE))
    ramp = int(round(EPISODE_SEAM_RAMP_SECONDS * BED_RATE))
    n = len(sound)
    if not db or not n:
        return sound
    hold, ramp = min(hold, n // 2), min(ramp, max(0, n // 2 - min(hold, n // 2)))
    curve = np.zeros(n)
    edge = np.concatenate(
        [np.linspace(0.0, db, ramp, endpoint=False), np.full(hold, db)]
    )
    if tail:
        curve[n - len(edge) :] = edge
    else:
        curve[: len(edge)] = edge[::-1]
    return sound * (10 ** (curve / 20))[:, None]


def join_sound(
    parts: list[JoinPart],
    lengths: list[float],
    gains: list[float],
    dissolves: list[float],
    heads: list[float] | None = None,
    speech: list[list[tuple[float, float]]] | None = None,
    rides_out: list[SeamRide] | None = None,
) -> np.ndarray:
    """Each part's pre-bed sound, cut or padded to its picture, gained, then butted or crossfaded (linear).

    On the samples, so every part starts exactly where its picture does (ffmpeg's
    ``acrossfade`` / ``concat`` chain lost a part's sound at random in testing).
    ``heads``: seconds cut off each part's start (a silent-head trim; ``lengths``
    are then the parts' lengths after the cut). Where two episodes meet, the
    end of one and the start of the next are levelled toward each other
    (:func:`episode_seam_rides`; ``speech``: each part's speech windows on its
    kept sound, left out of the level); the rides are appended to ``rides_out``.
    """

    sounds: list[np.ndarray] = []
    for index, (part, seconds, gain) in enumerate(
        zip(parts, lengths, gains, strict=True)
    ):
        size = int(round(seconds * BED_RATE))
        skip = int(round(heads[index] * BED_RATE)) if heads else 0
        sound = decode_stereo(part.pre_bed)[skip : skip + size]
        sounds.append(
            np.pad(sound, ((0, size - len(sound)), (0, 0))) * 10 ** (gain / 20)
        )
    rides = episode_seam_rides(parts, sounds, speech or [[] for _ in parts])
    for ride in rides:
        sounds[ride.index] = _ride(sounds[ride.index], ride.tail_db, tail=True)
        sounds[ride.index + 1] = _ride(sounds[ride.index + 1], ride.head_db, tail=False)
    if rides_out is not None:
        rides_out.extend(rides)
    joined = np.zeros((0, 2))
    for index, sound in enumerate(sounds):
        overlap = int(round(dissolves[index - 1] * BED_RATE)) if index else 0
        if overlap:
            ramp = ((np.arange(overlap) + 0.5) / overlap)[:, None]
            seam = joined[len(joined) - overlap :] * (1 - ramp) + sound[:overlap] * ramp
            joined = np.concatenate(
                [joined[: len(joined) - overlap], seam, sound[overlap:]]
            )
        else:
            joined = np.concatenate([joined, sound])
    return joined


def _join_bedless(
    parts: list[JoinPart],
    lengths: list[float],
    gains: list[float],
    dissolves: list[float],
    out: Path,
    heads: list[float] | None = None,
    speech: list[list[tuple[float, float]]] | None = None,
    rides_out: list[SeamRide] | None = None,
) -> list[float]:
    """Captioned pictures + pre-bed sound, gain-matched, cut or dissolved; no bed, no limiter (float sound).

    ``heads``: seconds cut off each part's start (a silent-head trim on a filmed cut).
    ``speech`` / ``rides_out``: episode seams are levelled (:func:`join_sound`).
    """

    first = probe_video(parts[0].picture)
    inputs: list[str] = []
    graph: list[str] = []
    for index, (part, seconds) in enumerate(zip(parts, lengths, strict=True)):
        inputs += ["-i", str(part.picture)]
        head = heads[index] if heads else 0.0
        window = (
            f"trim=start={head:.4f}:duration={seconds:.4f}"
            if head > 0
            else f"trim=duration={seconds:.4f}"
        )
        graph.append(
            f"[{index}:v]settb=AVTB,fps={HOUSE_FPS:g},scale={first.width}:{first.height},setsar=1,"
            f"format=yuv420p,{window},setpts=PTS-STARTPTS,fps={HOUSE_FPS:g}[v{index}]"
        )
    video, elapsed = "[v0]", lengths[0]
    seams: list[float] = []
    for index in range(1, len(parts)):
        dissolve = dissolves[index - 1]
        if dissolve > 0:
            offset = elapsed - dissolve
            seams.append(round(offset + dissolve / 2, 3))
            graph.append(
                f"{video}[v{index}]xfade=transition=fade:duration={dissolve}:offset={offset:.4f}[vx{index}]"
            )
            elapsed += lengths[index] - dissolve
        else:
            seams.append(round(elapsed, 3))
            # concat and setpts forget the frame rate; xfade needs it (and one time base) on both inputs.
            graph.append(
                f"{video}[v{index}]concat=n=2:v=1:a=0,fps={HOUSE_FPS:g}[vx{index}]"
            )
            elapsed += lengths[index]
        video = f"[vx{index}]"
    sound = write_wav(
        join_sound(parts, lengths, gains, dissolves, heads, speech, rides_out),
        out.with_name(f"{out.stem}-sound.wav"),
    )
    run_ffmpeg(
        [*inputs, "-i", str(sound), "-filter_complex", ";".join(graph), "-map", video, "-map", f"{len(parts)}:a",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-r", f"{HOUSE_FPS:g}",
         "-c:a", "pcm_f32le", str(out)]
    )  # fmt: skip
    return seams


# --- the command --------------------------------------------------------------------------------


def edge_notes(
    desk: Path,
    master: Path,
    parts: list[JoinPart],
    *,
    speech: list[tuple[float, float]],
    letterbox: bool = False,
) -> list[str]:
    """⚠ lines for how the joined file opens (its first episode's first take) and ends (a settled tail).

    Warnings only (:mod:`creation.post.opening`): a dark, static or faceless
    first second, and more than 0.3 s settled after the last line or action.
    The last line is the end of the last known speech on the joined timeline.

    Parameters
    ----------
    desk
        Series desk.
    master
        The joined master.
    parts
        The parts in order.
    speech
        Speech windows on the joined timeline.
    letterbox
        The joined file is a letterbox show's 9:16 canvas: only the picture is read.

    Returns
    -------
    list[str]
        ``⚠ ...`` lines (empty when nothing is out of line).
    """

    from creation.post.faces import detector_for
    from creation.post.opening import (
        measure_opening,
        measure_tail,
        opening_context,
        tail_warning,
    )

    crop: tuple[int, int, int, int] | None = None
    if letterbox:
        from creation.post.delivery_geometry import layout

        pic = layout().picture
        crop = (pic.x, pic.y, pic.width, pic.height)
    first = parts[0]
    silent, head_face = opening_context(desk, first.episode, first.take_id)
    reading = measure_opening(
        master, detector=detector_for(desk, first.episode), head_count_face=head_face,
        silent_open=silent, where=f"ep{first.episode:02d} (joined)", crop=crop,
    )  # fmt: skip
    lines = [f"⚠ {w}" for w in reading.warnings]
    tail = measure_tail(
        master, last_mark=max((b for _, b in speech), default=None), crop=crop
    )
    line = tail_warning(tail, what="the joined episode")
    if line:
        lines.append(f"⚠ {line}")
    return lines


def letterbox_join(parts: list[JoinPart]) -> bool:
    """True when every part is a letterbox show's 9:16 take (its finish record says so).

    Raises
    ------
    ValueError
        When letterbox takes are mixed with others (they would not share one layout).
    """

    flags = [part.record.letterbox for part in parts]
    if any(flags) and not all(flags):
        raise ValueError(
            "join refused: "
            + ", ".join(p.label for p, f in zip(parts, flags) if f)
            + " are letterbox 9:16 takes and "
            + ", ".join(p.label for p, f in zip(parts, flags) if not f)
            + " are not. Finish every take again so they share one layout."
        )
    return bool(flags) and all(flags)


def _letterbox_title(
    desk: Path,
    parts: list[JoinPart],
    *,
    hook_line: str | None,
    off: bool,
    caption_colour: str | None,
) -> tuple[Any, str]:
    """The joined letterbox file's title block (its first episode's), and a note for the report."""

    from creation.post.desk import saved_spine
    from creation.post.letterbox import title_block

    episode = parts[0].episode
    found = saved_spine(desk, episode)
    title, why = title_block(
        found[0] if found else None, episode, desk=desk, override=hook_line, off=off
    )
    colours = sorted({str(p.record.caption_colour or "yellow") for p in parts})
    if caption_colour and colours != [caption_colour.strip().casefold()]:
        raise ValueError(
            f"--caption-colour {caption_colour}: the takes' captions are burned in {', '.join(colours)} "
            "under the picture at finish. Run `finish --caption-colour "
            f"{caption_colour}` on each take, then join again."
        )
    episodes = sorted({p.episode for p in parts})
    note = why
    if len(episodes) > 1 and title is not None:
        note = f"letterbox series cut: the title block is ep{episode:02d}'s for the whole cut"
    return title, note


def _agreed(values: list[Any], default: Any) -> Any:
    distinct = {json.dumps(v) for v in values}
    return values[0] if len(distinct) == 1 else default


def _join_cover(desk: Path, episodes: tuple[int, ...]) -> tuple[Path | None, str]:
    """The cover for the joined file's first frame (the episode's newest reel cover), and its note.

    The first frame is the preview Discord, WhatsApp and the phones show
    (:mod:`creation.post.cover_frame`). A series cut takes its first episode's cover.
    """

    from creation.post.cover_frame import episode_cover

    if not episodes:
        return (
            None,
            "cover frame: none (no episode named; the first frame is the picture's)",
        )
    cover = episode_cover(desk, episodes[0])
    if cover is None:
        return None, (
            f"cover frame: none yet for episode {episodes[0]} (no reel cover on the desk): make the reel "
            "(`reel`), then join again for the cover as the first frame"
        )
    return (
        cover,
        f"cover frame: {cover.name} is the first frame (the preview Discord and phones show)",
    )


def _part_sound(part: JoinPart, gain: float, seconds: float) -> np.ndarray:
    size = int(round(seconds * BED_RATE))
    sound = decode_stereo(part.pre_bed)[:size]
    return np.pad(sound, ((0, size - len(sound)), (0, 0))) * 10 ** (gain / 20)


def _fix_seams(
    desk: Path,
    parts: list[JoinPart],
    lengths: list[float],
    gains: list[float],
    build: Any,
    render: Any,
    work: Path,
    joined: Joined,
    *,
    trim: bool = True,
) -> tuple[Joined, SeamFixReport]:
    """Run :func:`creation.post.seam_fix.fix_seams` on this join (its cue search, trims and re-join).

    ``trim=False``: the steady bed only, no silent-head trim (a desk created before 6 Oct 2026).
    """

    from creation.post.edit import measure_cuts

    passed_over: list[str] = []
    cues: dict[tuple[int, bool], SteadyCue] = {}

    def own(
        index: int, base: Joined
    ) -> tuple[str, np.ndarray, list[tuple[float, float]]]:
        part, head = parts[index], base.heads[index]
        seconds = lengths[index] - head - base.tails[index]
        windows, _ = part_speech(desk, part, lengths[index])
        sound = _part_sound(part, gains[index], lengths[index])[
            int(round(head * BED_RATE)) :
        ]
        sound = sound[: int(round(seconds * BED_RATE))]
        return part.label, sound, [(a - head, b - head) for a, b in windows if b > head]

    def pick_cue(base: Joined, index: int, quiet_after: bool) -> SteadyCue:
        quiet, loud = (index + 1, index) if quiet_after else (index, index + 1)
        if (index, quiet_after) not in cues:
            cues[(index, quiet_after)] = find_steady_cue(
                desk, [parts[quiet].episode, parts[loud].episode],
                [own(loud, base), own(quiet, base)], decode=decode_stereo, tried=passed_over,
            )  # fmt: skip
        return cues[(index, quiet_after)]

    def plan_trim(index: int, head: bool) -> Trim | str:
        part = parts[index]
        crop = None
        if part.record.letterbox:
            from creation.post.delivery_geometry import layout

            pic = layout().picture
            crop = (pic.x, pic.y, pic.width, pic.height)
        try:
            cuts = measure_cuts(part.picture, crop=crop)
        except RuntimeError as exc:
            return f"{part.label}: its filmed cuts could not be read ({exc})"
        windows, _ = part_speech(desk, part, lengths[index])
        return plan_silent_trim(
            part_index=index, label=part.label,
            sound=_part_sound(part, gains[index], lengths[index]), seconds=lengths[index],
            speech=windows, cuts=cuts, head=head, fps=HOUSE_FPS,
        )  # fmt: skip

    def rejoin(heads: list[float], tails: list[float], target: Path) -> Joined:
        seams, mixed, _, speech, notes, sources = build(
            heads, tails, target, work / "trim"
        )
        return Joined(
            target, seams, speech, seam_levels(target, seams, speech=speech), heads, tails, mixed,
            notes, sources,
        )  # fmt: skip

    scratch = work / "seam-fix"
    scratch.mkdir(parents=True, exist_ok=True)
    kept, report = fix_seams(
        joined, parts=parts, threshold=SEAM_STEP_DB, pick_cue=pick_cue, plan_trim=plan_trim,
        rejoin=rejoin, measure=lambda path, seams, speech: seam_levels(path, seams, speech=speech),
        render=render, scratch=scratch, trim=trim,
    )  # fmt: skip
    report.tried = passed_over + report.tried
    return kept, report


def run_join(
    desk: Path,
    *,
    episodes: tuple[int, ...] = (),
    take_files: tuple[Path, ...] = (),
    from_records: tuple[Path, ...] = (),
    dissolve: float | None = None,
    bed_db: float | None = None,
    duck_db: float | None = None,
    gain_match: bool | None = None,
    watermark_y: int | None = None,
    accept_seam: str | None = None,
    accepted_by: str | None = None,
    seam_fix: bool = True,
    ending: str = "hard",
    hook_line: str | None = None,
    no_hook_line: bool = False,
    caption_colour: str | None = None,
    stream: TextIO | None = None,
) -> JoinResult:
    """Join finished takes with one bed across them, and mark the joined file once.

    Parameters
    ----------
    desk
        Series desk.
    episodes
        One episode (its takes in order) or several (a series cut).
    take_files
        Finished takes in order, instead of ``episodes``.
    from_records
        With ``take_files``: the recorded file each unrecorded take file was copied from (:func:`file_parts`).
    dissolve
        Seconds at every seam; default a cut between takes, 0.25 s between episodes.
    bed_db
        ``--bed-db``. Default: the desk's ``series.json`` ``bed_db``, else the level
        every take was finished at when one was chosen, else measured from the bed
        (:func:`creation.post.bed.bed_level`).
    duck_db
        Exact duck depth; default what the takes were finished with.
    gain_match
        Gain every part to the parts' median loudness first. ``None`` (the
        default) matches unless every part finished within ±0.5 LU of -18 LUFS
        (:func:`gain_match_needed`); ``True`` forces it (``--gain-match``),
        ``False`` keeps each take's level (``--no-gain-match``).
    watermark_y
        Mark top offset override.
    accept_seam
        Why a human accepts every seam that steps over 5 dB (``--accept-seam``):
        the join is marked anyway, and who and why go in the run notes.
    accepted_by
        Who accepted (required with ``accept_seam``).
    seam_fix
        On a desk created on or after 6 Oct 2026, a seam over 5 dB is first fixed
        by ``join`` itself, free (:mod:`creation.post.seam_fix`: a steady bed on the
        quiet side, then a silent-head trim on a filmed cut); ``False``
        (``--no-seam-fix``) measures and refuses as before. Not tried with
        ``accept_seam`` or on an older desk.
    ending
        ``hard`` (default): the episode ends on its last frame, the bed stops
        with it. ``freeze-black``: the marked file holds its last frame, then
        cuts to black (:func:`creation.post.ending.apply_ending`); the master
        stays as joined.
    hook_line, no_hook_line
        A letterbox join's title block: ``--hook-line TEXT`` for its yellow
        line, ``--no-hook-line`` for none. Refused on a portrait join (its
        hook line is the first take's, burned at ``finish``).
    caption_colour
        ``--caption-colour``: a letterbox join's captions are the ones each
        take was finished with; a colour other than theirs is refused with
        what to run instead (``finish --caption-colour`` on each take).
    stream
        Progress output (stderr by default).

    Returns
    -------
    JoinResult
        Master, marked file (``None`` when a seam steps too far), seams and levels.

    Raises
    ------
    ValueError
        When the parts cannot be joined (unfinished, mixed size or rate, one part, no harness bed, or
        takes carrying their own music mixed with bedded ones).
    FileNotFoundError
        When a named file is gone.
    MediaToolError
        When the joined file is not 24 frames a second.
    """

    from creation.post.ending import apply_ending, check_ending

    check_ending(ending)
    out = stream or sys.stderr
    desk = desk.expanduser().resolve()
    accept_seam = (accept_seam or "").strip() or None
    accepted_by = (accepted_by or "").strip() or None
    if accept_seam and not accepted_by:
        raise ValueError(
            "--accept-seam needs --accepted-by NAME: the run notes record who accepted the seam and why"
        )
    if accepted_by and not accept_seam:
        raise ValueError('--accepted-by goes with --accept-seam "why"')
    if bool(episodes) == bool(take_files):
        raise ValueError(
            "join needs --episode / --episodes, or --take-file (one of them)"
        )
    if from_records and not take_files:
        raise ValueError("--from-record goes with --take-file")
    parts = (
        file_parts(desk, take_files, from_records)
        if take_files
        else [p for n in episodes for p in episode_parts(desk, n)]
    )
    lengths = check_parts(parts)
    letterbox = letterbox_join(parts)
    title = None
    title_note = ""
    if not letterbox and (hook_line or no_hook_line or caption_colour):
        raise ValueError(
            "--hook-line, --no-hook-line and --caption-colour on join are for a letterbox show's 9:16 takes; "
            "a portrait join keeps the hook line and captions each take was finished with"
        )
    if letterbox:
        title, title_note = _letterbox_title(
            desk, parts, hook_line=hook_line, off=no_hook_line,
            caption_colour=caption_colour,
        )  # fmt: skip
    dissolves = seam_dissolves(parts, dissolve)
    records = [part.record for part in parts]
    duck_db = (
        duck_db if duck_db is not None else _agreed([r.duck_db for r in records], None)
    )
    check_duck_db(duck_db)
    in_take = [part.label for part in parts if part.record.music_in_take]
    bed: Path | None = None
    level = None
    if in_take and len(in_take) != len(parts):
        bedded = ", ".join(
            part.label for part in parts if not part.record.music_in_take
        )
        raise ValueError(
            f"{', '.join(in_take)} carry the harness's music in their own soundtrack and {bedded} were finished "
            "with the show's bed: one bed across the join would double the music on the first. Join them "
            "separately, or re-finish so every take has its music the same way."
        )
    if not in_take:
        recorded_bed = _agreed([r.bed for r in records], None)
        bed = pinned_bed(desk)
        if bed is None and recorded_bed:
            found = records[0].resolve(desk, "bed")
            bed = found if found is not None and harness_bed(desk, found) else None
        if bed is None or not bed.is_file():
            raise ValueError(
                "no harness bed to lay across the join: `finish` a take first (it finds or has the server make "
                f"the show's bed and pins it). join never makes one (it is free). {MUSIC_IS_HARNESS}"
            )
        level = bed_level(
            desk,
            bed,
            flag=bed_db,
            recorded=chosen_record_level(
                [(r.bed_db, r.bed_db_source) for r in records]
            ),
        )
        bed_db = level.db

    episode_set = sorted({part.episode for part in parts})
    if len(episode_set) == 1:
        folder = desk / f"ep{episode_set[0]:02d}" / "takes"
        stem = f"episode-ep{episode_set[0]:02d}-join"
    else:
        folder = desk / "shared" / "cuts"
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"series-ep{episode_set[0]:02d}-ep{episode_set[-1]:02d}-join"
    run_dirs = [
        desk / f"ep{n:02d}"
        for n in episode_set
        if (desk / f"ep{n:02d}" / "run-notes.md").is_file()
    ]
    if accept_seam and not run_dirs:
        raise ValueError(
            "--accept-seam is recorded in the episode's run-notes.md, and none is on the desk; "
            "init the episode desk first"
        )

    print(
        f"Joining {len(parts)} take(s): {', '.join(p.label for p in parts)} "
        f"({'one bed' if bed is not None else 'music in each take'}, free)",
        file=out,
        flush=True,
    )
    if level is not None:
        print(f"Bed level: {level.one_line()}", file=out, flush=True)
    skip_note = None
    if gain_match is None:
        gain_match, why = gain_match_needed(finished_levels(desk, parts))
        if not gain_match:
            skip_note = (
                f"gain matching skipped: {why}, so they already match "
                "(--gain-match forces it)"
            )
            print(skip_note, file=out, flush=True)
    gains = match_gains(parts, desk=desk) if gain_match else [0.0] * len(parts)
    gain_note = gain_match_note(gains) if gain_match else skip_note
    master = next_versioned_path(folder, stem, ".mp4")
    zeros = [0.0] * len(parts)

    def part_windows(
        index: int, heads: list[float], tails: list[float]
    ) -> list[tuple[float, float]]:
        """Part ``index``'s speech on its kept sound (after a silent-head trim)."""

        windows, _ = part_speech(desk, parts[index], lengths[index])
        head = heads[index]
        length = lengths[index] - head - tails[index]
        return [
            (max(0.0, a - head), min(b - head, length))
            for a, b in windows
            if a - head < length and b - head > 0
        ]

    def joined_speech(
        heads: list[float], tails: list[float]
    ) -> tuple[list[tuple[float, float]], list[str]]:
        kept = [n - h - t for n, h, t in zip(lengths, heads, tails, strict=True)]
        speech: list[tuple[float, float]] = []
        notes: list[str] = []
        start = 0.0
        for index, (part, seconds) in enumerate(zip(parts, lengths, strict=True)):
            if index:
                start += kept[index - 1] - dissolves[index - 1]
            windows, note = part_speech(desk, part, seconds)
            head, length = heads[index], kept[index]
            if head or tails[index]:
                windows = [
                    (max(0.0, a - head), min(b - head, length))
                    for a, b in windows
                    if a - head < length and b - head > 0
                ]
            speech += [(start + a, start + b) for a, b in windows]
            notes.append(note)
            short = thought_short_of_cut(
                inner_voice_cues(part.record.path),
                duration=seconds - tails[index],
                label=part.label,
                last_take=index == len(parts) - 1,
            )
            if short:
                notes.append(short)
        return speech, notes

    def build(
        heads: list[float], tails: list[float], target: Path, work: Path
    ) -> tuple[
        list[float],
        Any,
        float,
        list[tuple[float, float]],
        list[str],
        tuple[Path, Path | None],
    ]:
        kept = [n - h - t for n, h, t in zip(lengths, heads, tails, strict=True)]
        total = sum(kept) - sum(dissolves)
        work.mkdir(parents=True, exist_ok=True)
        bedless = work / "joined-no-bed.mkv"
        rides: list[SeamRide] = []
        seams = _join_bedless(
            parts, kept, gains, dissolves, bedless, heads,
            speech=[part_windows(i, heads, tails) for i in range(len(parts))]
            if len(episode_set) > 1
            else None,
            rides_out=rides,
        )  # fmt: skip
        looped = (
            loop_bed(bed, total + 1.0, work / "bed-looped.wav")
            if bed is not None
            else None
        )
        mixed = mix_take(
            bedless,
            target,
            bed=looped,
            bed_db=bed_db if bed_db is not None else -16.5,
            duck_db=duck_db if looped is not None else None,
            music_in_take=looped is None,
        )
        fps = assert_house_fps(target)
        speech, notes = joined_speech(heads, tails)
        notes += [ride.text(parts) for ride in rides]
        return seams, mixed, fps, speech, notes, (bedless, looped)

    def render(base: Joined, beds: list[SeamBed], target: Path) -> Path:
        """``base`` mixed again from its own sound before the mix, at its take gain, with the seam beds."""

        bedless, looped = base.sources
        count = int(round(media_duration(bedless) * BED_RATE))
        layer = write_wav(
            lay_beds(np.zeros((count, 2)), beds),
            target.with_name(f"{target.stem}-layer.wav"),
        )
        mix_take(
            bedless, target, bed=looped, bed_db=bed_db if bed_db is not None else -16.5,
            duck_db=duck_db if looped is not None else None, music_in_take=looped is None,
            seam_layer=layer, gain_db=base.mixed.gain_db,
        )  # fmt: skip
        layer.unlink(missing_ok=True)
        return target

    work = Path(tempfile.mkdtemp(prefix="fictora-join-"))
    try:
        seams, mixed, fps, speech, speech_notes, sources = build(
            zeros, zeros, master, work
        )
        opening_notes = edge_notes(
            desk, master, parts, speech=speech, letterbox=letterbox
        )
        levels = seam_levels(master, seams, speech=speech)
        fix_report = None
        loudness_now: str | None = None
        if (
            seam_fix
            and not accept_seam
            and any(abs(lv.step_db) > SEAM_STEP_DB for lv in levels)
            and continuing_fix(desk, "seam_bed")
        ):
            # The trim cuts picture: new desks only (founder decision, 7 Oct 2026).
            trim = not is_legacy(desk)
            print(
                "A seam steps over 5 dB: trying a steady bed under it and a silent-head trim (free)"
                if trim
                else "A seam steps over 5 dB: trying a steady bed under it (free)",
                file=out,
                flush=True,
            )
            joined, fix_report = _fix_seams(
                desk, parts, lengths, gains, build, render, work,
                Joined(master, seams, speech, levels, zeros, zeros, mixed, speech_notes, sources),
                trim=trim,
            )  # fmt: skip
            if fix_report.fixed:
                keep_seam_fix(joined, master)
                mixed, speech_notes = joined.mixed, joined.notes
                fps = assert_house_fps(master)
                seams, speech, levels = joined.seams, joined.speech, joined.levels
                loudness_now = f"{measure_loudness(master):.1f} LUFS"
                if fix_report.trimmed:
                    opening_notes = edge_notes(
                        desk, master, parts, speech=speech, letterbox=letterbox
                    )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if gain_note:
        speech_notes.append(gain_note)
    speech_notes += opening_notes
    if title_note:
        speech_notes.append(title_note)
    result = JoinResult(
        parts=parts, master=master, marked=None, bed=bed, gains_db=gains, dissolves=dissolves, seams=seams,
        seam_steps_db=[level.step_db for level in levels], fps=round(fps, 3),
        loudness=loudness_now or f"{mixed.mix_lufs:.1f} LUFS", mix_line=mixed.one_line(), seam_levels=levels,
        bed_db=bed_db, seam_fix=fix_report,
    )  # fmt: skip
    if level is not None:
        result.notes.append(f"bed level {level.one_line()}")
    result.notes += [f"SUBSTITUTED: {p.stands_in}" for p in parts if p.stands_in]
    result.notes += speech_notes
    if result.loud_seams and accept_seam:
        result.accepted = {
            "by": accepted_by,
            "why": accept_seam,
            "seams": [{"at": at, "step_db": step} for at, step in result.loud_seams],
        }
        for seam, step in result.loud_seams:
            result.notes.append(
                f"ACCEPTED: seam at {seam:.2f}s steps {step:+.1f} dB (over {SEAM_STEP_DB:.0f}); "
                f"accepted by {accepted_by}: {accept_seam}"
            )
    elif accept_seam:
        result.notes.append(
            f"--accept-seam not needed: every seam steps under {SEAM_STEP_DB:.0f} dB"
        )
    if result.loud_seams and not result.accepted:
        for seam, step in result.loud_seams:
            result.notes.append(
                f"STOPPED: seam at {seam:.2f}s steps {step:+.1f} dB (over {SEAM_STEP_DB:.0f}, audible)"
            )
    else:
        marked_stem = f"{stem}-sokii"
        target = next_versioned_path(folder, marked_stem, ".mp4")
        cover, cover_note = _join_cover(desk, episodes)

        def mark(video: Path, out: Path) -> Path:
            # The cover goes on the first frame in the mark's own encode. A cover that fails
            # never stops the join: the mark is made again without it, with a ⚠ line.
            nonlocal cover, cover_note
            try:
                return _mark(video, out, cover=cover)
            except MediaToolError as exc:
                if cover is None:
                    raise
                out.unlink(missing_ok=True)
                cover_note = (
                    f"⚠ cover frame skipped: {type(exc).__name__}: {str(exc)[-200:]}"
                )
                cover = None
                return _mark(video, out, cover=None)

        def _mark(video: Path, out: Path, *, cover: Path | None) -> Path:
            if not letterbox:
                return watermark(video, out, y=watermark_y, cover=cover)
            from creation.post.letterbox import mark_and_title

            done, fitted = mark_and_title(
                video, out, title=title,
                ass_path=next_versioned_path(folder, f"{stem}-title", ".ass"), cover=cover,
            )  # fmt: skip
            result.notes.append(
                "letterbox: Sokii mark in the top band; "
                + (
                    f"{title.describe()} at {fitted.size} px"
                    + (f"; {fitted.note}" if fitted.note else "")
                    if title is not None and fitted is not None
                    else "no title block"
                )
            )
            return done

        if ending == "hard":
            result.marked = mark(master, target)
        else:
            with tempfile.TemporaryDirectory() as scratch:
                marked = mark(master, Path(scratch) / "marked.mp4")
                result.marked = apply_ending(marked, target, style=ending)
            result.notes.append(
                f"ending {ending}: the marked file holds its last frame, then cuts to black "
                "(the master stays as joined)"
            )
        result.notes.append(cover_note)
    summary = result.summary_lines()
    for run_dir in run_dirs:
        append_run_note(run_dir, "Join\n" + "\n".join(summary))
    for line in summary:
        print(line, file=out)
    if not result.complete:
        print(
            "Stopped: NOT DONE: a seam steps more than 5 dB. Nothing was marked; listen to the master. "
            "Fix: join again with gain matching on (the default), or re-finish the loud take with a lower level. "
            "If the master sounds right through the seam (the step is a line starting on the cut, not the "
            'room), join again with --accept-seam "why" --accepted-by NAME; both go in the run notes. '
            + (
                seam_fix_tried_line()
                if result.seam_fix is not None
                else seam_fix_line()
            ),
            file=out,
        )
        return result
    print(
        "Next: watch the joined file through every seam and say Use it or Change this.",
        file=out,
    )
    return result
