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

Server contract (``GET /v1/jobs/{id}/take-facts``)::

    board_frames: {"head_frames", "tail_frames", "head_s", "tail_s",
                   "frame_rate", "timeline_shift_s": 0} | null
"""

from __future__ import annotations

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
class ServerBoardFrames:
    """The board frames the server held at the stored take's ends (``take_facts.board_frames``)."""

    head_frames: int
    tail_frames: int

    def one_line(self) -> str:
        """``the server held 10 head and 0 tail board frame(s) when it stored the take``."""

        return (
            f"the server held {self.head_frames} head and {self.tail_frames} tail board frame(s) "
            "when it stored the take (same length and sound; no time moves)"
        )


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
    return ServerBoardFrames(head_frames=head, tail_frames=tail)


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


def shift_take_facts(payload: Mapping[str, Any], seconds: float) -> dict[str, Any]:
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

    Returns
    -------
    dict[str, Any]
        A new payload in the same shape, with :data:`SHIFTED_KEY` recorded.
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
            if end is not None and isinstance(item.get(end), (int, float)):
                if float(item[end]) - seconds <= 0:
                    continue
                moved[end] = move(item[end])
            moved[start] = move(item.get(start))
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
    facts[SHIFTED_KEY] = round(seconds, 3)
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
