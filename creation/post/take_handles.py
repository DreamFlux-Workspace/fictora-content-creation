"""A take's trim handles (fictora-drama #563): where it starts and ends when it plays.

The server never cuts a stored take. It keeps two handles on it, ``start_s``
and ``end_s``, seconds on the take as filmed (the take-facts timeline), and
cuts picture and sound at them only when it joins the episode. The automatic
handles (``source: auto``) sit just past the board frames it held; a creator
moves them (``source: creator``) with
``PUT /v1/video-generations/{job_id}/takes/{take_index}/trim``, and a reset
puts them back. Take facts carry ``trim`` and ``playable_window``; every other
time in the facts stays on the filmed timeline, so a consumer that plays the
window subtracts ``trim.start_s`` and drops what falls outside it.

The kit does the same as the server's join:

- ``trim --start S --end E`` (or one end alone) / ``--reset`` sets the handles (preview first,
  ``--preview`` sends nothing), then saves the take facts again so ``finish``
  reads the new ``trim`` (:func:`run_take_trim`).
- ``finish`` lays every effect, line duck and caption on the take as filmed,
  as before, and cuts at the handles LAST, on the three files of its finish
  record (the take before the bed, the un-marked master, the marked file),
  with the burned captions' ``.ass`` moved by ``-start_s``
  (:func:`apply_take_handles`). The new record carries the edit
  ``{"op": "handles", "start_s", "end_s", ...}``, so ``join`` joins the take
  at its handles like the server does.
- Whatever reads take-facts times against such a file (``join``'s seam
  speech, ``review``, ``reel``) moves them by ``-start_s`` first
  (:func:`facts_on_handled_file`).

A server older than #563 sends no ``trim``: nothing is cut and nothing moves.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from creation.ops.folder import next_versioned_path
from creation.post.media import (
    count_frames,
    keep_cover_args,
    probe_video,
    run_ffmpeg,
    video_streams,
)
from creation.post.take_timeline import server_board_frames, shift_take_facts

#: The edit a finish record carries once its files were cut to the handles.
HANDLES_OP = "handles"
#: The key handle-moved facts record their shift under (kept apart from a server cut's).
HANDLES_SHIFT_KEY = "take_facts_handles_start_s"
#: The server's shortest take (``TAKE_HANDLES_MIN_SECONDS``).
MIN_SECONDS = 1.0
#: The rate handles land on when the facts name none (the delivery rate).
DEFAULT_RATE = 24.0

#: What to do about each named refusal of the trim route.
TRIM_REFUSAL_FIXES: dict[str, str] = {
    "take_trim_out_of_range": "keep 0 <= start < end <= the take's length (seconds on the take as filmed)",
    "take_trim_too_short": f"a take plays at least {MIN_SECONDS:g} s: move the handles apart",
    "take_trim_not_frame_aligned": "put each handle on a frame (the message names the two nearest frame times)",
    "take_not_ready": "the take is not delivered yet, or its length is unknown: wait for the take, then trim",
    "take_not_found": "the desk's take is not on that video job: `take-facts --refresh`, or check the take id",
    "video_generation_not_found": (
        "the server does not know the desk's video job for this session (another session filmed it?)"
    ),
}


@dataclass(frozen=True)
class TakeHandles:
    """The handles as the server reports them (take facts ``trim`` + ``playable_window``, or the PUT answer)."""

    start_s: float
    end_s: float
    source: str
    take_duration_s: float | None = None
    frame_rate: float = DEFAULT_RATE
    auto_start_s: float | None = None
    auto_end_s: float | None = None
    media_url: str | None = None
    is_original: bool | None = None

    @property
    def duration_s(self) -> float:
        """Seconds that play."""

        return round(self.end_s - self.start_s, 3)

    def one_line(self) -> str:
        """``0.417-14.833 s (auto) of 15.000 s``."""

        total = (
            f" of {self.take_duration_s:.3f} s"
            if self.take_duration_s is not None
            else ""
        )
        return f"{self.start_s:.3f}-{self.end_s:.3f} s ({self.source}){total}"


def _inner(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    inner = payload.get("take_facts")
    return inner if isinstance(inner, Mapping) else payload


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def handles_from(
    trim: Any, window: Any, *, frame_rate: float | None = None
) -> TakeHandles | None:
    """Read the handles from a ``trim`` and a ``playable_window`` object.

    Parameters
    ----------
    trim
        ``take_facts.trim`` (or the PUT answer's ``trim``).
    window
        ``take_facts.playable_window`` (or the PUT answer's), may be ``None``.
    frame_rate
        The rate to use when the window names none.

    Returns
    -------
    TakeHandles | None
        ``None`` when ``trim`` is missing or unreadable (a server older than #563).
    """

    if not isinstance(trim, Mapping):
        return None
    start, end = _number(trim.get("start_s")), _number(trim.get("end_s"))
    if start is None or end is None or end <= start:
        return None
    window = window if isinstance(window, Mapping) else {}
    rate = _number(window.get("frame_rate")) or frame_rate or DEFAULT_RATE
    original = window.get("is_original")
    return TakeHandles(
        start_s=start,
        end_s=end,
        source=str(trim.get("source") or "auto"),
        take_duration_s=_number(window.get("take_duration_s")),
        frame_rate=rate,
        auto_start_s=_number(window.get("auto_start_s")),
        auto_end_s=_number(window.get("auto_end_s")),
        media_url=str(window["media_url"]) if window.get("media_url") else None,
        is_original=original if isinstance(original, bool) else None,
    )


def handles_from_facts(payload: Mapping[str, Any] | None) -> TakeHandles | None:
    """The take's handles from its saved facts, or ``None`` (no ``trim``: an older server or older facts).

    Parameters
    ----------
    payload
        Saved take facts (``{"take_facts": {...}}`` or the facts alone), or ``None``.

    Returns
    -------
    TakeHandles | None
        The handles.
    """

    if payload is None:
        return None
    facts = _inner(payload)
    record = server_board_frames(payload)
    return handles_from(
        facts.get("trim"),
        facts.get("playable_window"),
        frame_rate=record.frame_rate if record is not None else None,
    )


def shift_for_handles(
    payload: Mapping[str, Any], start_s: float, end_s: float
) -> dict[str, Any]:
    """The facts on a file cut to ``[start_s, end_s)``: every time minus ``start_s``, outside dropped.

    Parameters
    ----------
    payload
        Saved take facts.
    start_s, end_s
        The handles (seconds on the take as filmed).

    Returns
    -------
    dict[str, Any]
        A new payload (:func:`creation.post.take_timeline.shift_take_facts`).
    """

    return shift_take_facts(
        payload, start_s, until=round(end_s - start_s, 3), record_as=HANDLES_SHIFT_KEY
    )


def handles_edit(edits: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The ``handles`` edit among a finish record's edits, if the record's files were cut to the handles."""

    return next((edit for edit in edits if edit.get("op") == HANDLES_OP), None)


def facts_on_handled_file(
    payload: Mapping[str, Any] | None, edits: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """Saved take facts as they lie on a file whose record was cut to the handles (else unchanged).

    Parameters
    ----------
    payload
        Saved take facts, or ``None``.
    edits
        The finish record's ``edits``.

    Returns
    -------
    Mapping[str, Any] | None
        Facts moved by ``-start_s`` (outside the window dropped) when a
        ``handles`` edit is there; ``payload`` itself otherwise.
    """

    edit = handles_edit(edits)
    if payload is None or edit is None:
        return payload
    start, end = _number(edit.get("start_s")), _number(edit.get("end_s"))
    if start is None or end is None:
        return payload
    return shift_for_handles(payload, start, end)


def windows_on_handled_file(
    windows: Sequence[tuple[float, float]], edits: Sequence[Mapping[str, Any]]
) -> list[tuple[float, float]]:
    """Move ``(start, end)`` windows on the take as filmed onto a file cut to its handles.

    Parameters
    ----------
    windows
        Seconds on the take as filmed.
    edits
        The finish record's ``edits``.

    Returns
    -------
    list[tuple[float, float]]
        Windows minus ``start_s``, clipped to the window, the ones outside dropped.
    """

    edit = handles_edit(edits)
    if edit is None:
        return list(windows)
    start, end = float(edit["start_s"]), float(edit["end_s"])
    length = end - start
    moved = []
    for a, b in windows:
        a2, b2 = max(0.0, a - start), min(length, b - start)
        if b2 > a2:
            moved.append((round(a2, 3), round(b2, 3)))
    return moved


# --- Frames and captions ---------------------------------------------------------------------------


def handle_frames(handles: TakeHandles, *, fps: float, frames: int) -> tuple[int, int]:
    """The first frame kept and the first frame after the window, on a file of ``frames`` at ``fps``."""

    first = max(0, round(handles.start_s * fps))
    stop = min(frames, round(handles.end_s * fps))
    return first, max(first + 1, stop)


def keep_frames(take: Path, out: Path, *, first: int, stop: int, fps: float) -> Path:
    """Keep frames ``first`` to ``stop - 1`` and the sound under exactly them, in a new file.

    Parameters
    ----------
    take
        Video (never overwritten).
    out
        New file.
    first, stop
        First frame kept and first frame dropped after the window, at ``fps``.
    fps
        The rate the frame numbers count at.

    Returns
    -------
    Path
        ``out``.

    Raises
    ------
    FileExistsError
        When ``out`` exists.
    """

    if out.exists():
        raise FileExistsError(f"{out} exists; the handle cut never overwrites")
    info = probe_video(take)
    picture, cover = video_streams(take)
    begin_s, stop_s = first / fps, stop / fps
    video = ["-map", f"0:v:{picture}",
             "-filter:v:0", f"select='between(n\\,{first}\\,{stop - 1})',setpts=N/FRAME_RATE/TB",
             "-r:v:0", f"{fps:g}", "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p"]  # fmt: skip
    audio = (
        ["-map", "0:a:0", "-af", f"atrim=start={begin_s:.6f}:end={stop_s:.6f},asetpts=N/SR/TB",
         "-c:a", "aac", "-b:a", "192k"]
        if info.has_audio
        else ["-an"]
    )  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(take), *video, *audio, *keep_cover_args(cover), str(out)])
    return out


_ASS_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")


def _ass_seconds(text: str) -> float | None:
    match = _ASS_TIME.match(text.strip())
    if not match:
        return None
    hours, minutes, seconds, cents = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + cents / 100


def _ass_time(seconds: float) -> str:
    cents = round(max(0.0, seconds) * 100)
    hours, rest = divmod(cents, 360000)
    minutes, rest = divmod(rest, 6000)
    return f"{hours}:{minutes:02d}:{rest // 100:02d}.{rest % 100:02d}"


def shift_ass(source: Path, out: Path, *, start_s: float, end_s: float) -> Path:
    """Move a burned caption file onto the cut window: times minus ``start_s``, outside dropped, edges clipped.

    Parameters
    ----------
    source
        The ``.ass`` the captions step wrote beside the master.
    out
        The new ``.ass`` (beside the cut master).
    start_s, end_s
        The handles.

    Returns
    -------
    Path
        ``out``.
    """

    length = end_s - start_s
    kept: list[str] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            kept.append(line)
            continue
        head, rest = line.split(":", 1)
        parts = rest.split(",", 3)
        begin = _ass_seconds(parts[1]) if len(parts) > 3 else None
        end = _ass_seconds(parts[2]) if len(parts) > 3 else None
        if begin is None or end is None:
            kept.append(line)
            continue
        a, b = max(0.0, begin - start_s), min(length, end - start_s)
        if b <= a:
            continue
        parts[1], parts[2] = _ass_time(a), _ass_time(b)
        kept.append(f"{head}:{','.join(parts)}")
    out.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return out


@dataclass(frozen=True)
class HandleCut:
    """What :func:`apply_take_handles` did."""

    final: Path | None
    record: Path | None
    note: str


def apply_take_handles(
    desk: Path,
    *,
    record_path: Path,
    handles: TakeHandles,
    held_head_s: float | None = None,
    kept_original: bool = False,
) -> HandleCut:
    """Cut a finished take's record files to its handles, last, the way the server's join does.

    The take before the bed, the un-marked master and the marked file are each
    cut to the same frames, picture and sound together; the master's ``.ass``
    is moved with them; a new finish record names the three with the edit
    ``{"op": "handles", "start_s", "end_s", "source", "frames", "fps"}``.

    Parameters
    ----------
    desk
        Series desk.
    record_path
        The finish record ``finish`` just wrote.
    handles
        The take's handles (seconds on the take as filmed).
    held_head_s
        Seconds the server held at the start, to warn when a creator start
        reaches into them.
    kept_original
        The server kept the original (so it plays real frames where the kit's raw take has held copies).

    Returns
    -------
    HandleCut
        The cut final and its record (``None`` when the window is the whole take), and the line to print.
    """

    from creation.post.edit_commands import RecordCarry
    from creation.post.finish_record import _load
    from creation.post.lineage import record_edit

    record = _load(record_path)
    if record is None:
        return HandleCut(
            None,
            None,
            f"!! trim handles not applied: `{record_path.name}` is not a finish record",
        )
    final = record.resolve(desk, "final")
    if final is None or not final.is_file():
        return HandleCut(
            None, None, "!! trim handles not applied: the finished file is missing"
        )
    fps = probe_video(final).fps or handles.frame_rate
    total = count_frames(final)
    first, stop = handle_frames(handles, fps=fps, frames=total)
    if first == 0 and stop >= total:
        return HandleCut(
            None,
            None,
            f"trim handles {handles.one_line()}: the whole take plays; nothing cut",
        )
    takes = final.parent
    base = f"take-ep{record.episode:02d}-{record.take_id}"
    covered = "-cover" if video_streams(final)[1] is not None else ""

    def cut(take: Path, out: Path) -> Path:
        rate = probe_video(take).fps or fps
        return keep_frames(
            take,
            out,
            first=round(first / fps * rate),
            stop=round(stop / fps * rate),
            fps=rate,
        )

    carry = RecordCarry(desk, record, final, HANDLES_OP)
    carry.edit_companions(cut)
    output = cut(
        final, next_versioned_path(takes, f"{base}-{HANDLES_OP}{covered}", ".mp4")
    )
    start_s, end_s = round(first / fps, 3), round(stop / fps, 3)
    record_edit(
        desk, op=HANDLES_OP, source=final, output=output, start_s=start_s, end_s=end_s
    )
    edit = {"op": HANDLES_OP, "start_s": start_s, "end_s": end_s, "source": handles.source,
            "frames": [first, stop], "fps": fps}  # fmt: skip
    new_record = carry.write(output, edit)
    # The master's .ass moves with the cut (RecordCarry.write: creation.post.edit_captions).
    note = (
        f"trim handles {handles.one_line()}: kept frames {first}-{stop - 1} of {total} "
        f"({start_s:.3f}-{end_s:.3f} s, picture and sound together) on the take before the bed, the master "
        f"and the marked file -> `{output.name}`"
        + (f"; record `{new_record.name}` (what `join` reads)" if new_record else "")
    )
    if carry.caption_line and "!!" in carry.caption_line:
        note += "\n" + carry.caption_line
    if (
        handles.source == "creator"
        and held_head_s
        and handles.start_s < held_head_s - 1e-3
        and kept_original
    ):
        note += (
            f"\n!! the creator's start ({handles.start_s:.3f} s) reaches into the {held_head_s:.3f} s the server "
            "held: the server plays the original's frames there, this cut shows the held copies. If those frames "
            "matter, `review --original` and finish --take-file the original"
        )
    return HandleCut(output, new_record, note)


# --- trim --start/--end / --reset: the server's handles -------------------------------------------


@dataclass(frozen=True)
class TakeCoordinate:
    """Where the trim route finds a take: its video job, its 1-based index there, and its own job."""

    video_job_id: str
    take_index: int
    take_job_id: str


def take_coordinate(desk: Path, episode: int, take_id: str) -> TakeCoordinate | None:
    """The video job and take index of the desk's newest clip of ``take_id`` (from its clip records).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1`` ...

    Returns
    -------
    TakeCoordinate | None
        ``None`` when the desk has no filmed clip for the take.
    """

    from creation.harness.raw_video import raw_clips_records
    from creation.post.desk import take_clip

    clip = take_clip(desk, episode, take_id)
    job = str(clip.get("job_id") or "") if clip else ""
    if not job:
        return None
    for path in raw_clips_records(desk / f"ep{episode:02d}" / "api"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        ids = [
            str(item.get("job_id"))
            for item in raw.get("clips") or []
            if isinstance(item, Mapping)
        ]
        coordinator = raw.get("coordinator_job_id")
        if job in ids and coordinator:
            return TakeCoordinate(str(coordinator), ids.index(job) + 1, job)
    return None


def _refusal_code(text: str) -> str | None:
    found = re.search(r"\b(take_[a-z_]+|video_generation_not_found)\b", text)
    return found.group(1) if found else None


def trim_refusal(text: str) -> str:
    """A refusal of the trim route in the operator's words (the server's text, then the fix).

    Parameters
    ----------
    text
        The server's error text.

    Returns
    -------
    str
        ``text`` plus a ``fix:`` line for a named code; an unknown route says the server predates #563.
    """

    code = _refusal_code(text)
    if code in TRIM_REFUSAL_FIXES:
        return f"{text}\n  fix: {TRIM_REFUSAL_FIXES[code]}"
    if re.search(r"HTTP (404|405)\b", text):
        return (
            f"{text}\n  this server has no trim handles (fictora-drama #563 is not deployed): nothing changed; "
            "the take plays whole"
        )
    return text


def _take_length(
    handles: TakeHandles | None, payload: Mapping[str, Any] | None, raw: Path | None
) -> float | None:
    if handles is not None and handles.take_duration_s is not None:
        return handles.take_duration_s
    if raw is not None and raw.is_file():
        return probe_video(raw).duration_seconds
    return None


def _snap(seconds: float, rate: float) -> float:
    return round(round(seconds * rate) / rate, 3)


def preview_lines(
    payload: Mapping[str, Any] | None,
    *,
    now: TakeHandles | None,
    start_s: float,
    end_s: float,
    rate: float,
    length: float | None,
) -> list[str]:
    """What a trim would change: the handles before and after, what each end loses, and what falls outside."""

    rows = [
        f"  now:  {now.one_line()}"
        if now
        else "  now:  not in the saved take facts (older facts or server)"
    ]
    plays = f"plays {end_s - start_s:.3f} s" + (f" of {length:.3f} s" if length else "")
    rows.append(f"  new:  {start_s:.3f}-{end_s:.3f} s (creator) -> {plays}")
    tail = (
        f"{length - end_s:.3f} s ({round((length - end_s) * rate)} frame(s))"
        if length
        else "?"
    )
    rows.append(
        f"  cut when played or joined: {start_s:.3f} s ({round(start_s * rate)} frame(s)) at the start, {tail} at the end"
    )
    facts = _inner(payload) if payload else {}
    soundtrack = (
        facts.get("soundtrack") if isinstance(facts.get("soundtrack"), Mapping) else {}
    )
    spans = [
        (
            str(line.get("line_id") or "line"),
            _number(line.get("start_s")),
            _number(line.get("end_s")),
        )
        for line in soundtrack.get("lines") or []
        if isinstance(line, Mapping)
    ] or [
        (
            str(line.get("line_id") or "line"),
            _number(line.get("start_seconds")),
            _number(line.get("end_seconds")),
        )
        for line in facts.get("lines") or []
        if isinstance(line, Mapping)
    ]
    for name, a, b in spans:
        if a is None or b is None:
            continue
        if b <= start_s or a >= end_s:
            rows.append(
                f"  !! line {name} ({a:.2f}-{b:.2f} s) is outside the window: it will not play"
            )
        elif a < start_s or b > end_s:
            rows.append(
                f"  !! line {name} ({a:.2f}-{b:.2f} s) is cut by the window: part of it will not play"
            )
    for cue in facts.get("sfx_cues") or []:
        at = _number(cue.get("start_seconds")) if isinstance(cue, Mapping) else None
        if at is not None and not start_s <= at < end_s:
            rows.append(
                f"  !! effect '{cue.get('sound')}' at {at:.2f} s is outside the window: it will not play"
            )
    record = server_board_frames(payload)
    if record is not None and record.head_frames and record.original is not None:
        held = record.head_frames / record.frame_rate
        if start_s < held - 1e-3:
            rows.append(
                f"  the start reaches into the {held:.3f} s the server held: it plays the original's frames there "
                "(the kit's raw take has held copies: `review --original` fetches the original)"
            )
    return rows


def run_take_trim(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    start_s: float | None,
    end_s: float | None,
    reset: bool,
    preview: bool = False,
    out: TextIO,
) -> int:
    """``trim --start S --end E`` / ``--reset``: set the take's handles on the server, preview first.

    Prints the change, then sends ``PUT /v1/video-generations/{job}/takes/{index}/trim``
    (unless ``preview``), saves the take facts again so ``finish`` cuts at the
    new handles, and ends on ``Applied``, ``Refused: <reason>`` or
    ``Not applied`` (``--preview``).

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    take_id
        ``t1`` ...
    start_s, end_s
        The handles, seconds on the take as filmed (either or both; neither with
        ``reset``). The one not given stays where the take's handles are now.
    reset
        Back to the automatic handles.
    preview
        Print the change; send nothing.
    out
        Text stream.

    Returns
    -------
    int
        ``0`` applied or previewed; ``2`` refused.
    """

    from creation import episode_commands as ec
    from creation.harness.raw_video import fetch_take_facts
    from creation.ops.notes import append_run_note
    from creation.post.desk import latest_raw_take, saved_spine
    from creation.post.sfx import saved_take_facts
    from creation.post.take_facts import save_take_facts

    def refused(reason: str) -> int:
        out.flush()
        print(f"Stopped: {reason}", file=sys.stderr)
        print(f"Refused: {ec.refusal_reason(reason)}", file=sys.stderr)
        return 2

    desk = desk.expanduser().resolve()
    label = f"ep{episode:02d} {take_id}"
    if reset == (start_s is not None or end_s is not None):
        return refused("give --start and/or --end, or --reset alone")
    where = take_coordinate(desk, episode, take_id)
    if where is None:
        return refused(f"{label}: no filmed clip on the desk (no video job to trim)")
    facts_path = saved_take_facts(desk, episode, take_id)
    payload = json.loads(facts_path.read_text(encoding="utf-8")) if facts_path else None
    now = handles_from_facts(payload)
    record = server_board_frames(payload)
    rate = now.frame_rate if now else record.frame_rate if record else DEFAULT_RATE
    try:
        raw: Path | None = latest_raw_take(desk, episode, take_id)
    except FileNotFoundError:
        raw = None
    length = _take_length(now, payload, raw)
    print(
        f"Trim {label} (take {where.take_index} of video job {where.video_job_id}):",
        file=out,
    )
    if reset:
        auto = (
            f"{now.auto_start_s:.3f}-{now.auto_end_s:.3f} s"
            if now and now.auto_start_s is not None and now.auto_end_s is not None
            else "just past the board frames the server held"
        )
        print(
            f"  now:  {now.one_line() if now else 'not in the saved take facts'}",
            file=out,
        )
        print(f"  new:  the automatic handles ({auto})", file=out)
        body: dict[str, Any] = {"reset": True}
    else:
        # One end alone moves only that end: the other stays where the take's handles are now
        # (L-20261006-14), or the take's own start / end when the take facts carry no handles.
        if start_s is None:
            start_s = now.start_s if now is not None else 0.0
            print(f"  --start not given: the start stays at {start_s:.3f} s", file=out)
        if end_s is None:
            end_s = now.end_s if now is not None else length
            if end_s is None:
                return refused(
                    f"{label}: the take's length is unknown (no take facts, no raw take on the desk); "
                    "give --end too"
                )
            print(f"  --end not given: the end stays at {end_s:.3f} s", file=out)
        start, end = _snap(start_s, rate), _snap(end_s, rate)
        if length is not None and abs(end_s - length) < 0.5 / rate:
            end = round(length, 3)
        for asked, landed in ((start_s, start), (end_s, end)):
            if abs(asked - landed) > 0.0006:
                print(
                    f"  {asked:g} s is between frames at {rate:g} fps: sent as {landed:.3f} s",
                    file=out,
                )
        if not 0 <= start < end:
            return refused(
                f"{label}: the start must be at or after 0 s and before the end ({start}-{end} s)"
            )
        if length is not None and end > length + 0.0006:
            return refused(
                f"{label}: the end {end:.3f} s is past the take's length {length:.3f} s"
            )
        if end - start < MIN_SECONDS - 1e-6:
            return refused(
                f"{label}: the window is {end - start:.3f} s; a take plays at least {MIN_SECONDS:g} s"
            )
        for row in preview_lines(
            payload, now=now, start_s=start, end_s=end, rate=rate, length=length
        ):
            print(row, file=out)
        body = {"start_s": start, "end_s": end}
    if preview:
        print("Not applied: --preview showed the trim; nothing was sent", file=out)
        return 0
    _desk, state, run = ec._desk_session(desk)
    try:
        try:
            answer = run.put(
                f"/v1/video-generations/{where.video_job_id}/takes/{where.take_index}/trim",
                body,
                idempotency_key=f"{run.prefix}-trim-ep{episode:02d}-{take_id}-{uuid.uuid4().hex[:12]}",
            )
        except SystemExit as exc:
            text = exc.code if isinstance(exc.code, str) else str(exc.code)
            return refused(trim_refusal(text))
        stored = handles_from(answer.get("trim"), answer.get("playable_window"))
        if stored is None:
            return refused(
                "the server answered without the handles (an older deploy?): check `take-facts --refresh`"
            )
        changed = bool(answer.get("changed", True))
        print(
            f"  stored: {stored.one_line()}"
            + ("" if changed else " (already stored: nothing written)")
            + (
                f"; plays {'the original' if stored.is_original else 'the stored clip'}"
                if stored.is_original is not None
                else ""
            ),
            file=out,
        )
        facts_note = ""
        try:
            facts = fetch_take_facts(run, where.take_job_id, spine_id=state.spine_id)
        except SystemExit as exc:
            facts, facts_note = None, f" ({exc})"
        if facts is None:
            print(
                f"  !! take facts not saved again{facts_note}: run `take-facts --refresh` before finish",
                file=out,
            )
        else:
            found = saved_spine(desk, episode)
            saved = save_take_facts(desk, episode=episode, take_id=take_id, facts=facts,
                                    spine=found[0] if found else None)  # fmt: skip
            print(
                f"  take facts: `{saved.name}` (finish cuts at these handles)", file=out
            )
    finally:
        run.client.close()
    run_dir = desk / f"ep{episode:02d}"
    if (run_dir / "run-notes.md").is_file():
        append_run_note(
            run_dir, f"Trim handles {label}: {stored.one_line()} (changed: {changed})"
        )
    print("Applied", file=out)
    return 0


__all__ = [
    "HANDLES_OP",
    "HandleCut",
    "TakeCoordinate",
    "TakeHandles",
    "apply_take_handles",
    "facts_on_handled_file",
    "handle_frames",
    "handles_from",
    "handles_from_facts",
    "keep_frames",
    "preview_lines",
    "run_take_trim",
    "shift_ass",
    "shift_for_handles",
    "take_coordinate",
    "trim_refusal",
    "windows_on_handled_file",
]
