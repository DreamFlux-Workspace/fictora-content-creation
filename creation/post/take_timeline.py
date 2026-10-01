"""Keep a take's facts on the take's own timeline: the server's board-frame hold, and takes it cut.

The server cleans the storyboard frames a Turbo take opens or closes on when it
stores the take. Since fictora-drama #555 it HOLDS them (each board frame is
replaced by the nearest real frame): the clip keeps its length and sound, and
the take facts say so under ``board_frames`` (``timeline_shift_s`` is always 0).
``deboard`` then measures no board frames and writes nothing: no double trim,
and nothing is shifted.

For a few hours before that (fictora-drama #543, 1 Oct 2026) the server CUT
the frames, sound and all, and did not move the take facts. Hanakaze ep 7 v3
lost its first 10 frames and 0.417 s of its locked-voice track, so every
caption and duck laid from its take facts ran 0.42 s late. Such a take carries
no ``board_frames`` record, so the cut is measured instead: on a locked-voice
take (``soundtrack.mode == "target_audio"``) the take's sound IS the dialogue
track at ``soundtrack.track_url``, so cross-correlating the two gives the cut
to the sample. :func:`align_take_facts` writes the next facts version with
every time moved by it (``take_facts_shifted_s`` records how much), so every
command that reads the newest facts (finish, captions, review) agrees with the
clip. A native take from that window cannot be measured; it is reported.

Since fictora-drama #559 the server keeps the provider's untouched take
whenever it holds frames (``board_frames.original``), and it holds only frames
that are surely the board: a run it found but was not sure of is left as
filmed and listed under ``board_frames.unsure`` with the reason. Review says
all of it (:meth:`ServerBoardFrames.lines`), ``review --original`` fetches the
original and checks it (:func:`fetch_original`), and the kit's ``deboard``
never holds an end the server listed as unsure: it asks the producer
(:func:`unsure_head`).

Server contract (``GET /v1/jobs/{id}/take-facts``)::

    board_frames: {"head_frames", "tail_frames", "head_s", "tail_s",
                   "frame_rate", "timeline_shift_s": 0,
                   "original": {"url", "content_sha256", "content_length"},  # #559, when held
                   "unsure": [{"end": "head" | "tail", "frames", "reason"}]}  # #559, when any
                  | null
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import numpy.typing as npt

from creation.post.media import ffmpeg_bin, probe_video
from creation.ops.folder import next_versioned_path

#: The key a kit-shifted facts file records its shift under (seconds the times moved earlier).
SHIFTED_KEY = "take_facts_shifted_s"
#: Sample rate the take and its track are compared at.
_RATE = 8000
#: Farthest a cut is looked for: the server never cut more than 12 frames (0.5 s).
_MAX_LAG_SECONDS = 0.6
#: Seconds of sound compared (enough lines to lock on, cheap).
_COMPARE_SECONDS = 12.0
#: A peak this far under perfect correlation is not the same sound.
_MIN_CORRELATION = 0.6

#: Reads ``url`` into ``dest`` and returns ``dest``.
TrackFetcher = Callable[[str, Path], Path]


@dataclass(frozen=True)
class KeptOriginal:
    """The provider's untouched take the server kept when it held frames (``board_frames.original``)."""

    url: str
    content_sha256: str
    content_length: int | None


@dataclass(frozen=True)
class UnsureRun:
    """Possible board frames at one end the server left as filmed (``board_frames.unsure[]``)."""

    end: str
    frames: int
    reason: str

    @property
    def where(self) -> str:
        """``start`` or ``end`` (the server says ``head`` / ``tail``)."""

        return "start" if self.end == "head" else "end"

    def one_line(self) -> str:
        """``!! possible board frames at the start (9) left as filmed: <reason>``."""

        return (
            f"!! possible board frames at the {self.where} ({self.frames} frame(s)) left as filmed: {self.reason}. "
            f"The kit does not hold them either: look at the {self.where} of the take before deciding"
        )


@dataclass(frozen=True)
class ServerBoardFrames:
    """The board frames the server held at the stored take's ends (``take_facts.board_frames``)."""

    head_frames: int
    tail_frames: int
    original: KeptOriginal | None = None
    unsure: tuple[UnsureRun, ...] = ()
    frame_rate: float = 24.0

    @property
    def held(self) -> int:
        """Frames held at both ends."""

        return self.head_frames + self.tail_frames

    def unsure_at(self, end: str) -> UnsureRun | None:
        """The unsure run at ``head`` or ``tail``, if the server listed one."""

        return next((run for run in self.unsure if run.end == end), None)

    def lines(self) -> list[str]:
        """What the server did at the take's ends, one line each (never "held 0" next to an unsure run).

        Returns
        -------
        list[str]
            The held line (with the kept original, or that none was stored), then
            one ``!!`` line per end the server left as filmed.
        """

        rows: list[str] = []
        if self.held:
            held = (
                f"server held {self.head_frames} start / {self.tail_frames} end frame(s) "
                "(same length and sound; no time moves)"
            )
            if self.original is not None:
                size = (
                    f", {self.original.content_length} bytes"
                    if self.original.content_length
                    else ""
                )
                held += (
                    f" · original kept: {self.original.url} (sha256 {self.original.content_sha256[:12]}…{size}); "
                    "`review --original` downloads it, checks it and writes a side-by-side of the held ends"
                )
            else:
                held += (
                    " · held frames but no original stored (before fictora-drama #559): the provider's take "
                    "cannot be restored"
                )
            rows.append(held)
        elif not self.unsure:
            rows.append("server found no board frames at either end (nothing held)")
        rows += [run.one_line() for run in self.unsure]
        return rows

    def one_line(self) -> str:
        """Every line of :meth:`lines`, joined for a single run-notes line."""

        return "; ".join(self.lines())


def _inner(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    inner = payload.get("take_facts")
    return inner if isinstance(inner, Mapping) else payload


def server_board_frames(payload: Mapping[str, Any] | None) -> ServerBoardFrames | None:
    """The server's board-frame record from saved take facts.

    Parameters
    ----------
    payload
        Saved take facts (``{"take_facts": {...}}`` or the facts alone), or ``None``.

    Returns
    -------
    ServerBoardFrames | None
        ``None`` when the facts carry none (an older server, a clean take, or a
        take stored before the record was kept).
    """

    if payload is None:
        return None
    record = _inner(payload).get("board_frames")
    if not isinstance(record, Mapping):
        return None
    head, tail = record.get("head_frames"), record.get("tail_frames")
    if not isinstance(head, int) or not isinstance(tail, int):
        return None
    kept = record.get("original")
    original = None
    if (
        isinstance(kept, Mapping)
        and isinstance(kept.get("url"), str)
        and isinstance(kept.get("content_sha256"), str)
    ):
        length = kept.get("content_length")
        original = KeptOriginal(
            url=kept["url"],
            content_sha256=kept["content_sha256"],
            content_length=length if isinstance(length, int) else None,
        )
    unsure = tuple(
        UnsureRun(
            end=str(run.get("end")),
            frames=int(run.get("frames") or 0),
            reason=str(run.get("reason") or "no reason given"),
        )
        for run in record.get("unsure") or ()
        if isinstance(run, Mapping) and run.get("end") in ("head", "tail")
    )
    rate = record.get("frame_rate")
    return ServerBoardFrames(
        head_frames=head,
        tail_frames=tail,
        original=original,
        unsure=unsure,
        frame_rate=float(rate) if isinstance(rate, (int, float)) and rate > 0 else 24.0,
    )


def unsure_head(payload: Mapping[str, Any] | None) -> UnsureRun | None:
    """The run at the start the server left as filmed, which ``deboard`` must not hold on its own.

    Parameters
    ----------
    payload
        Saved take facts, or ``None``.

    Returns
    -------
    UnsureRun | None
        ``None`` when the server listed no unsure run at the head (or sent no record).
    """

    record = server_board_frames(payload)
    return record.unsure_at("head") if record is not None else None


def facts_shift_s(payload: Mapping[str, Any] | None) -> float | None:
    """How far :func:`align_take_facts` moved these saved facts, or ``None`` when it did not.

    Parameters
    ----------
    payload
        Saved take facts, or ``None``.

    Returns
    -------
    float | None
        The recorded :data:`SHIFTED_KEY` seconds.
    """

    if payload is None:
        return None
    value = _inner(payload).get(SHIFTED_KEY)
    return float(value) if isinstance(value, (int, float)) else None


def _pcm(path: Path) -> npt.NDArray[np.float64]:
    run = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-t", f"{_COMPARE_SECONDS + _MAX_LAG_SECONDS:.3f}",
         "-ac", "1", "-ar", str(_RATE), "-f", "f32le", "-"],
        capture_output=True, check=False,
    )  # fmt: skip
    if run.returncode != 0:
        raise ValueError(
            f"cannot read the sound of {path.name}: {run.stderr.decode(errors='replace')[-200:]}"
        )
    return np.frombuffer(run.stdout, dtype=np.float32).astype(np.float64)


def measure_track_lag(take: Path, track: Path) -> float | None:
    """How much earlier the take's sound runs than its dialogue track, in seconds.

    Parameters
    ----------
    take
        The stored take (its sound is the track on a locked-voice take).
    track
        The dialogue track WAV the take was filmed to.

    Returns
    -------
    float | None
        Positive when the take's sound starts that far into the track (its
        head was cut); about 0 on a take that kept its timeline. ``None`` when
        the two do not correlate (not the same sound).
    """

    clip, ref = _pcm(take), _pcm(track)
    span = round(_MAX_LAG_SECONDS * _RATE)
    length = min(len(clip), len(ref) - span, round(_COMPARE_SECONDS * _RATE))
    if length <= _RATE:
        return None
    a = clip[:length]
    best_lag, best = 0, -1.0
    norm_a = float(np.linalg.norm(a))
    for lag in range(0, span + 1):
        b = ref[lag : lag + length]
        denom = norm_a * float(np.linalg.norm(b))
        if denom == 0:
            continue
        score = float(np.dot(a, b)) / denom
        if score > best:
            best_lag, best = lag, score
    if best < _MIN_CORRELATION:
        return None
    return best_lag / _RATE


def shift_take_facts(
    payload: Mapping[str, Any],
    seconds: float,
    *,
    until: float | None = None,
    record_as: str = SHIFTED_KEY,
) -> dict[str, Any]:
    """Move every time in the take facts ``seconds`` earlier, clamped at 0.

    Shot windows, ``soundtrack.lines``, line windows and SFX cues (played and
    dropped) all move. A window that ends at or before 0 after the move was in
    the part that was cut and is dropped (its line or cue is not on the clip).

    Parameters
    ----------
    payload
        Saved take facts (``{"take_facts": {...}}`` or the facts alone).
    seconds
        How far the clip's timeline is ahead of the facts' (the cut head).
    until
        The clip's length after the move, when its end was cut too (a take
        cut to its trim handles, :mod:`creation.post.take_handles`): a window
        or cue starting at or after it is dropped, an end past it is clipped,
        and a cue that started before the cut head is dropped (it is not on
        the clip) instead of moved to 0.
    record_as
        The key the shift is recorded under (:data:`SHIFTED_KEY` for a take
        the server cut; the handles keep theirs apart).

    Returns
    -------
    dict[str, Any]
        A new payload in the same shape, with the shift recorded under ``record_as``.
    """

    def move(value: Any) -> Any:
        return (
            round(max(0.0, float(value) - seconds), 3)
            if isinstance(value, (int, float))
            else value
        )

    def windows(items: Any, start: str, end: str | None) -> Any:
        if not isinstance(items, list):
            return items
        kept = []
        for item in items:
            if not isinstance(item, Mapping):
                kept.append(item)
                continue
            moved = dict(item)
            began = item.get(start)
            if until is not None and isinstance(began, (int, float)):
                if float(began) - seconds >= until:
                    continue  # after the cut tail
                if end is None and float(began) < seconds:
                    continue  # a cue in the cut head is not on the clip
            if end is not None and isinstance(item.get(end), (int, float)):
                if float(item[end]) - seconds <= 0:
                    continue
                moved[end] = move(item[end])
                if until is not None:
                    moved[end] = round(min(float(moved[end]), until), 3)
            moved[start] = move(began)
            kept.append(moved)
        return kept

    nested = isinstance(payload.get("take_facts"), Mapping)
    facts = dict(_inner(payload))
    facts["shots"] = windows(facts.get("shots"), "start_seconds", "end_seconds")
    facts["lines"] = windows(facts.get("lines"), "start_seconds", "end_seconds")
    facts["sfx_cues"] = windows(facts.get("sfx_cues"), "start_seconds", None)
    if "sfx_dropped_cues" in facts:
        facts["sfx_dropped_cues"] = windows(
            facts.get("sfx_dropped_cues"), "start_seconds", None
        )
    soundtrack = facts.get("soundtrack")
    if isinstance(soundtrack, Mapping):
        facts["soundtrack"] = {
            **soundtrack,
            "lines": windows(soundtrack.get("lines"), "start_s", "end_s"),
        }
    facts[record_as] = round(seconds, 3)
    if nested:
        return {**payload, "take_facts": facts}
    return facts


@dataclass(frozen=True)
class TimelineCheck:
    """What :func:`align_take_facts` found and did."""

    facts: Path | None
    note: str
    warning: bool = False


def _download(url: str, dest: Path) -> Path:
    from creation.post.audio_service import download

    return download(url, dest)


def align_take_facts(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    take: Path,
    facts_path: Path,
    fetch_track: TrackFetcher = _download,
) -> TimelineCheck:
    """Make sure the take facts' times are the clip's times; write shifted facts when the server cut the head.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1`` ...
    take
        The raw take as stored by the server.
    facts_path
        The newest saved take facts.
    fetch_track
        Reads the dialogue track (tests pass a local copy).

    Returns
    -------
    TimelineCheck
        ``facts`` is the facts file to use (a new version when shifted, else
        ``facts_path``); ``note`` is the run-notes line; ``warning`` marks a
        take whose cut could not be measured.
    """

    payload = json.loads(facts_path.read_text(encoding="utf-8"))
    facts = _inner(payload)
    held = server_board_frames(payload)
    if held is not None:
        return TimelineCheck(facts_path, f"take timeline: {held.one_line()}")
    moved = facts_shift_s(payload)
    if moved is not None:
        return TimelineCheck(
            facts_path, f"take timeline: facts already moved {moved:.3f}s to the clip"
        )
    soundtrack = facts.get("soundtrack")
    track_url = soundtrack.get("track_url") if isinstance(soundtrack, Mapping) else None
    asked = facts.get("duration_seconds")
    clip_seconds = probe_video(take).duration_seconds
    short = isinstance(asked, (int, float)) and clip_seconds < float(asked) - 0.05
    if not isinstance(track_url, str) or soundtrack.get("mode") != "target_audio":
        if short:
            return TimelineCheck(
                facts_path,
                f"!! take timeline: the take is {float(asked) - clip_seconds:.2f}s shorter than filmed and its facts "
                "carry no board-frame record: if the server cut its head board frames (1 Oct 2026, fictora-drama "
                "#543), cue, line and caption times run up to that much late. Check captions by eye.",
                warning=True,
            )
        return TimelineCheck(
            facts_path,
            "take timeline: facts and clip agree (no board-frame record, full length)",
        )
    if not short:
        # A cut take is shorter than it was filmed; a full-length one kept its head.
        return TimelineCheck(
            facts_path,
            "take timeline: facts and clip agree (no board-frame record, full length)",
        )
    with tempfile.TemporaryDirectory(prefix="take-track-") as tmp:
        try:
            track = fetch_track(track_url, Path(tmp) / "track.wav")
        except (httpx.HTTPError, OSError) as exc:
            return TimelineCheck(
                facts_path,
                f"!! take timeline: the take is {float(asked) - clip_seconds:.2f}s shorter than filmed and its "
                f"dialogue track could not be read ({exc}); times not checked: check captions by eye",
                warning=True,
            )
        lag = measure_track_lag(take, track)
    if lag is None:
        return TimelineCheck(
            facts_path,
            "!! take timeline: the take's sound does not match its dialogue track; times not checked",
            warning=True,
        )
    fps = probe_video(take).fps or 24.0
    frames = round(lag * fps)
    if frames < 1:
        return TimelineCheck(
            facts_path,
            f"take timeline: the take's sound sits on its track (lag {lag * 1000:.0f} ms)",
        )
    shift = round(lag, 3)
    shifted = shift_take_facts(payload, shift)
    out = next_versioned_path(
        facts_path.parent, f"take-facts-ep{episode:02d}-{take_id}", ".json"
    )
    out.write_text(
        json.dumps(shifted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return TimelineCheck(
        out,
        f"take timeline: the server cut {frames} head frame(s) with their sound ({shift:.3f}s; fictora-drama #543), "
        f"so every take-facts time moved {shift:.3f}s earlier -> `{out.name}`",
    )


@dataclass(frozen=True)
class OriginalCopy:
    """The provider's original fetched beside the held take, checked, with a side-by-side of the held ends."""

    path: Path
    compare: Path | None

    def lines(self) -> list[str]:
        """What ``review --original`` prints."""

        rows = [
            f"original: `{self.path.name}` (sha256 and length match the server's record)"
        ]
        if self.compare is not None:
            rows.append(
                f"compare: `{self.compare.name}` (left the held take, right the original, at each held end)"
            )
        return rows


def _held_ends_graph(record: ServerBoardFrames, seconds: float) -> str:
    """``select`` expression keeping the held ends plus a quarter second of the real take after each."""

    rate = record.frame_rate or 24.0
    parts = []
    if record.head_frames:
        parts.append(f"lt(t\\,{record.head_frames / rate + 0.25:.3f})")
    if record.tail_frames:
        parts.append(
            f"gte(t\\,{max(0.0, seconds - record.tail_frames / rate - 0.25):.3f})"
        )
    return "+".join(parts) or "lt(t\\,0.5)"


def fetch_original(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    payload: Mapping[str, Any] | None,
    held_take: Path | None,
    fetch: TrackFetcher = _download,
) -> OriginalCopy:
    """Download the original the server kept, check it against its record, and compare it with the held take.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1`` ...
    payload
        The take's saved facts.
    held_take
        The raw take on the desk (the held clip), for the side-by-side; ``None`` skips it.
    fetch
        Reads ``url`` into a path (tests pass a local copy).

    Returns
    -------
    OriginalCopy
        ``epNN/takes/take-epNN-tK-original-vN.mp4`` and the compare clip.

    Raises
    ------
    ValueError
        When the facts name no original (nothing held, or stored before
        fictora-drama #559), or the download's sha256 or length does not match.
    """

    record = server_board_frames(payload)
    if record is None or record.original is None:
        why = (
            "the facts carry no board-frame record"
            if record is None
            else "held frames but no original stored (before fictora-drama #559)"
            if record.held
            else "the server held nothing, so the stored take is the original"
        )
        raise ValueError(f"no original to fetch: {why}")
    takes = desk / f"ep{episode:02d}" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    out = next_versioned_path(takes, f"take-ep{episode:02d}-{take_id}-original", ".mp4")
    fetch(record.original.url, out)
    data = out.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = record.original.content_length
    if digest != record.original.content_sha256 or (
        expected is not None and len(data) != expected
    ):
        out.unlink()
        raise ValueError(
            f"the downloaded original does not match the server's record (sha256 {digest[:12]}…, "
            f"{len(data)} bytes; expected {record.original.content_sha256[:12]}…, {expected} bytes): not kept"
        )
    compare = None
    if held_take is not None and held_take.is_file():
        compare = next_versioned_path(
            takes, f"take-ep{episode:02d}-{take_id}-original-compare", ".mp4"
        )
        keep = _held_ends_graph(record, probe_video(held_take).duration_seconds)
        side = f"select='{keep}',setpts=N/FRAME_RATE/TB,scale=-2:480"
        run = subprocess.run(
            [ffmpeg_bin(), "-v", "error", "-i", str(held_take), "-i", str(out), "-filter_complex",
             f"[0:v]{side}[a];[1:v]{side}[b];[a][b]hstack=inputs=2[v]", "-map", "[v]", "-an",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(compare)],
            capture_output=True, check=False,
        )  # fmt: skip
        if run.returncode != 0:
            raise ValueError(
                f"could not write the side-by-side: {run.stderr.decode(errors='replace')[-300:]}"
            )
    return OriginalCopy(path=out, compare=compare)
