"""Join finished takes into one file: an episode's 30 s / 60 s cut, or a series cut across episodes.

Free: ffmpeg on this laptop, no paid call (the bed is the one already pinned).

What ``join`` does, in order:

1. **Parts.** For ``--episode N`` the newest complete finish record of every
   take, in take order; ``--episodes`` does that per episode for a series cut;
   ``--take-file`` names finished takes in the order given. A take needs a
   finish record (``take-epNN-tK-finish-vN.json``, written by ``finish``): it
   names the take before the bed (``pre_bed``) and the un-marked captioned
   picture (``master``). A file edited after ``finish`` (``trim``, ``tempo``)
   has none and is refused.
2. **Checks, before anything is written.** Every picture 24 fps and the same
   size; the pre-bed take the same length as its picture (one frame of slack).
3. **One bed.** Each take's pre-bed sound (voice, effects, hand cues) is joined
   with its captioned picture, never the finished sound, so no take's own bed
   is stitched in (sound joined on the samples, so every part starts exactly
   on its picture). Each part gets one gain to the parts' median loudness (no
   loudnorm). The show's bed is looped seamlessly (silent head and tail cut,
   1 s equal-power crossfade at each loop point) to cover the whole join, then
   mixed under it by :func:`creation.post.mix.mix_take`: ducked under the
   voice, a measured gain to about -18 LUFS, ONE limiter.
4. **Seams.** Takes of one episode are filmed to hand off on the same frame, so
   they meet on a straight cut; episodes meet on a 0.25 s dissolve (picture
   ``xfade`` and sound ``acrossfade``). ``--dissolve S`` sets every seam.
5. **Captions** are the ones ``finish`` burned on each take, timed on that
   take's own voice; they move with their picture, so they land on the joined
   timeline by construction (a caption inside a dissolve fades with it).
6. **Measure.** Frames / seconds of the joined file must be 24; the room level
   either side of each seam (median of 0.1 s RMS over 2 s) must step under
   5 dB. A bigger step stops the join: the un-marked master is kept to listen
   to, nothing is marked, and the CLI exits 5.
7. **Mark once** on the joined master (the parts are un-marked); the master
   stays beside the marked file.

Outputs never overwrite: ``epNN/takes/episode-epNN-join-vN.mp4`` (master) and
``episode-epNN-join-sokii-vN.mp4``; a series cut goes to ``shared/cuts/``.
"""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from creation.ops.folder import next_versioned_path
from creation.ops.notes import append_run_note
from creation.ops.state import episode_by_ordinal, load_series
from creation.post.bed import DEFAULT_BED_DB, pinned_bed
from creation.post.finish_record import (
    FinishRecord,
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
from creation.post.watermark import watermark

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

    @property
    def label(self) -> str:
        """``ep01 t2``."""

        return f"ep{self.episode:02d} {self.take_id}"


@dataclass
class JoinResult:
    """What the join wrote and measured."""

    parts: list[JoinPart]
    master: Path
    marked: Path | None
    bed: Path
    gains_db: list[float]
    dissolves: list[float]
    seams: list[float]
    seam_steps_db: list[float]
    fps: float
    loudness: str
    mix_line: str
    notes: list[str] = field(default_factory=list)

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
        """Marked, and every seam steps under 5 dB."""

        return self.marked is not None and not self.loud_seams

    def summary_lines(self) -> list[str]:
        """Operator lines."""

        seams = ", ".join(
            f"{s:.2f}s {'cut' if d == 0 else f'{d:.2f}s dissolve'} {step:+.1f} dB"
            for s, d, step in zip(
                self.seams, self.dissolves, self.seam_steps_db, strict=True
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
            f"Bed: one bed across the join, `{self.bed.name}`; {self.mix_line}",
            f"Seams: {seams}",
            f"Frames / seconds: {self.fps:.2f}; loudness {self.loudness}",
        ]
        lines += [f"- {note}" for note in self.notes]
        return lines

    def as_json(self) -> dict[str, Any]:
        """JSON report."""

        return {
            "master": str(self.master),
            "marked": str(self.marked) if self.marked else None,
            "complete": self.complete,
            "parts": [{"episode": p.episode, "take_id": p.take_id, "picture": str(p.picture),
                       "pre_bed": str(p.pre_bed)} for p in self.parts],
            "gains_db": self.gains_db,
            "seams": [{"at": s, "dissolve": d, "step_db": step}
                      for s, d, step in zip(self.seams, self.dissolves, self.seam_steps_db, strict=True)],
            "fps": self.fps,
            "loudness": self.loudness,
        }  # fmt: skip


# --- parts --------------------------------------------------------------------------------------


def _take_order(take_id: str) -> int:
    digits = take_id.lstrip("t")
    return int(digits) if digits.isdigit() else 0


def _part(desk: Path, record: FinishRecord, *, asked: Path | None = None) -> JoinPart:
    name = asked.name if asked else f"ep{record.episode:02d} {record.take_id}"
    if not record.complete or not record.pre_bed:
        raise ValueError(
            f"{name}: its finish record `{record.path.name if record.path else '?'}` is NOT DONE "
            "(no music, SFX or mix); finish the take before joining it"
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
            missing.append(take_id)
            continue
        parts.append(_part(desk, record))
    if missing:
        raise ValueError(
            f"ep{episode:02d}: no finished {', '.join(missing)} (no complete take-ep{episode:02d}-tK-finish-vN.json); "
            "run `fictora-produce finish` on every take first, or name the takes with --take-file"
        )
    return parts


def file_parts(desk: Path, files: tuple[Path, ...]) -> list[JoinPart]:
    """Parts for explicit finished files, in the order given.

    Raises
    ------
    ValueError
        When a file has no finish record naming it.
    """

    parts = []
    for file in files:
        if not file.expanduser().is_file():
            raise FileNotFoundError(f"take not found: {file}")
        record = record_for_file(desk, file)
        if record is None:
            raise ValueError(
                f"{file.name}: no finish record names this file, so its sound before the bed is unknown and "
                "one bed cannot go across the join. Pass a file `finish` made (the -sokii final or the "
                "un-marked -cap file). A take edited after finish (trim, tempo) cannot be joined yet: "
                "run finish on the edited raw take instead."
            )
        parts.append(_part(desk, record, asked=file))
    return parts


# --- checks -------------------------------------------------------------------------------------


def video_seconds(path: Path) -> float:
    """Length of the first video stream (not the container, which counts the audio's priming)."""

    result = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
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


def match_gains(parts: list[JoinPart]) -> list[float]:
    """One gain per part to the parts' median integrated loudness (silent parts get 0)."""

    levels = [measure_loudness(part.pre_bed) for part in parts]
    heard = [level for level in levels if math.isfinite(level)]
    if not heard:
        return [0.0] * len(parts)
    target = statistics.median(heard)
    return [
        round(target - level, 1) if math.isfinite(level) else 0.0 for level in levels
    ]


def decode_stereo(path: Path) -> np.ndarray:
    """A file's sound as float samples, stereo at 48 kHz, shape ``(samples, 2)``.

    Raises
    ------
    MediaToolError
        When ffmpeg cannot decode it.
    """

    decoded = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", "2", "-ar", str(BED_RATE), "-"],
        capture_output=True, check=False,
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


def seam_loudness_steps(
    video: Path, seams: list[float], *, window: float = SEAM_WINDOW_SECONDS
) -> list[float]:
    """Room-level step (after minus before) across each seam.

    The level either side is the median of 0.1 s RMS windows over ``window``
    seconds, so a line starting right after the seam does not read as a jump;
    the bed and the room do. The bed's fade in (first 1 s) and fade out (last
    1.5 s) are left out.
    """

    levels = np.array(measure_rms_windows(video, window_seconds=0.1))
    span = int(round(window / 0.1))
    # The bed's own fade in and fade out are not a seam: leave them out of both sides.
    first = int(math.ceil(BED_FADE_IN_SECONDS / 0.1))
    last = len(levels) - int(math.ceil(BED_FADE_OUT_SECONDS / 0.1))
    steps: list[float] = []
    for seam in seams:
        index = int(seam / 0.1)
        before = levels[max(first, index - span) : index]
        after = levels[index : min(last, index + span)]
        if before.size == 0 or after.size == 0:
            steps.append(0.0)
            continue
        steps.append(round(float(np.median(after)) - float(np.median(before)), 1))
    return steps


def join_sound(
    parts: list[JoinPart],
    lengths: list[float],
    gains: list[float],
    dissolves: list[float],
) -> np.ndarray:
    """Each part's pre-bed sound, cut or padded to its picture, gained, then butted or crossfaded (linear).

    On the samples, so every part starts exactly where its picture does (ffmpeg's
    ``acrossfade`` / ``concat`` chain lost a part's sound at random in testing).
    """

    joined = np.zeros((0, 2))
    for index, (part, seconds, gain) in enumerate(
        zip(parts, lengths, gains, strict=True)
    ):
        size = int(round(seconds * BED_RATE))
        sound = decode_stereo(part.pre_bed)[:size]
        sound = np.pad(sound, ((0, size - len(sound)), (0, 0))) * 10 ** (gain / 20)
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
) -> list[float]:
    """Captioned pictures + pre-bed sound, gain-matched, cut or dissolved; no bed, no limiter (float sound)."""

    first = probe_video(parts[0].picture)
    inputs: list[str] = []
    graph: list[str] = []
    for index, (part, seconds) in enumerate(zip(parts, lengths, strict=True)):
        inputs += ["-i", str(part.picture)]
        graph.append(
            f"[{index}:v]settb=AVTB,fps={HOUSE_FPS:g},scale={first.width}:{first.height},setsar=1,"
            f"format=yuv420p,trim=duration={seconds:.4f},setpts=PTS-STARTPTS,fps={HOUSE_FPS:g}[v{index}]"
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
        join_sound(parts, lengths, gains, dissolves),
        out.with_name(f"{out.stem}-sound.wav"),
    )
    run_ffmpeg(
        [*inputs, "-i", str(sound), "-filter_complex", ";".join(graph), "-map", video, "-map", f"{len(parts)}:a",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-r", f"{HOUSE_FPS:g}",
         "-c:a", "pcm_f32le", str(out)]
    )  # fmt: skip
    return seams


# --- the command --------------------------------------------------------------------------------


def _agreed(values: list[Any], default: Any) -> Any:
    distinct = {json.dumps(v) for v in values}
    return values[0] if len(distinct) == 1 else default


def run_join(
    desk: Path,
    *,
    episodes: tuple[int, ...] = (),
    take_files: tuple[Path, ...] = (),
    dissolve: float | None = None,
    bed: Path | None = None,
    bed_db: float | None = None,
    duck_db: float | None = None,
    gain_match: bool = True,
    watermark_y: int | None = None,
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
    dissolve
        Seconds at every seam; default a cut between takes, 0.25 s between episodes.
    bed
        Bed file; default the bed pinned on the desk, else the one the takes were finished with.
    bed_db, duck_db
        Bed level and exact duck depth; default what the takes were finished with.
    gain_match
        Gain every part to the parts' median loudness first.
    watermark_y
        Mark top offset override.
    stream
        Progress output (stderr by default).

    Returns
    -------
    JoinResult
        Master, marked file (``None`` when a seam steps too far), seams and levels.

    Raises
    ------
    ValueError
        When the parts cannot be joined (unfinished, mixed size or rate, one part, no bed).
    FileNotFoundError
        When a named file is gone.
    MediaToolError
        When the joined file is not 24 frames a second.
    """

    out = stream or sys.stderr
    desk = desk.expanduser().resolve()
    if bool(episodes) == bool(take_files):
        raise ValueError(
            "join needs --episode / --episodes, or --take-file (one of them)"
        )
    parts = (
        file_parts(desk, take_files)
        if take_files
        else [p for n in episodes for p in episode_parts(desk, n)]
    )
    lengths = check_parts(parts)
    dissolves = seam_dissolves(parts, dissolve)
    records = [part.record for part in parts]
    duck_db = (
        duck_db if duck_db is not None else _agreed([r.duck_db for r in records], None)
    )
    check_duck_db(duck_db)
    bed_db = (
        bed_db
        if bed_db is not None
        else _agreed([r.bed_db for r in records], DEFAULT_BED_DB)
    )
    recorded_bed = _agreed([r.bed for r in records], None)
    bed = (bed.expanduser().resolve() if bed else None) or pinned_bed(desk)
    if bed is None and recorded_bed:
        bed = records[0].resolve(desk, "bed")
    if bed is None or not bed.is_file():
        raise ValueError(
            "no bed to lay across the join: pin the show's bed (`fictora-produce set-bed --desk D --path F`) "
            "or pass --bed F. join never makes one (it is free)."
        )

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

    print(
        f"Joining {len(parts)} take(s): {', '.join(p.label for p in parts)} (one bed, free)",
        file=out,
        flush=True,
    )
    gains = match_gains(parts) if gain_match else [0.0] * len(parts)
    total = sum(lengths) - sum(dissolves)
    master = next_versioned_path(folder, stem, ".mp4")
    with tempfile.TemporaryDirectory() as scratch:
        bedless = Path(scratch) / "joined-no-bed.mkv"
        seams = _join_bedless(parts, lengths, gains, dissolves, bedless)
        looped = loop_bed(bed, total + 1.0, Path(scratch) / "bed-looped.wav")
        mixed = mix_take(bedless, master, bed=looped, bed_db=bed_db, duck_db=duck_db)
    fps = assert_house_fps(master)
    steps = seam_loudness_steps(master, seams)
    result = JoinResult(
        parts=parts, master=master, marked=None, bed=bed, gains_db=gains, dissolves=dissolves, seams=seams,
        seam_steps_db=steps, fps=round(fps, 3), loudness=f"{mixed.mix_lufs:.1f} LUFS",
        mix_line=mixed.one_line(),
    )  # fmt: skip
    if result.loud_seams:
        for seam, step in result.loud_seams:
            result.notes.append(
                f"STOPPED: seam at {seam:.2f}s steps {step:+.1f} dB (over {SEAM_STEP_DB:.0f}, audible)"
            )
    else:
        marked_stem = f"{stem}-sokii"
        result.marked = watermark(
            master, next_versioned_path(folder, marked_stem, ".mp4"), y=watermark_y
        )
    summary = result.summary_lines()
    for run_dir in run_dirs:
        append_run_note(run_dir, "Join\n" + "\n".join(summary))
    for line in summary:
        print(line, file=out)
    if not result.complete:
        print(
            "Stopped: NOT DONE: a seam steps more than 5 dB. Nothing was marked; listen to the master. "
            "Fix: join again with gain matching on (the default), or re-finish the loud take with a lower level.",
            file=out,
        )
        return result
    print(
        "Next: watch the joined file through every seam and say Use it or Change this.",
        file=out,
    )
    return result
