"""An edit after ``finish`` carries the take's captions (``.ass``) onto the edited master (canary 7 Oct 2026).

``reel`` (server and local) and ``clips`` find a take's captions as the
``.ass`` beside the finish record's ``master``. An edit (``blur``, ``freeze``,
``soften``, ``trim``, ``tempo``, the take handles) writes a new master and a
new record, so the new master needs its ``.ass`` too: the Lost & Found canary's
``blur`` wrote ``take-ep01-t1-blur-master-v1.mp4`` and no ``.ass``, and the
reel said "t1: no accepted caption cues (.ass); the reel is uncaptioned".

- Edits that keep timing (``blur``, ``freeze`` (the hold is laid over the
  picture, nothing is inserted), ``soften``): the same cues, copied.
- Edits that change timing: the cues move the way the picture did. ``trim``
  removes ``frames[0]..frames[1]`` (cues after it move earlier, cues inside it
  go, cues across an edge are clipped to it); ``tempo`` divides the time in its
  window by the factor (and moves what follows); ``handles`` keeps
  ``start_s..end_s`` (times minus ``start_s``).
- An edit the kit cannot re-time, or a caption file it cannot read, writes
  nothing and says so with ``!!``: never a silently uncaptioned take.

:func:`captions_for_record` also walks a record back through its
``from_record`` chain when its master has no ``.ass`` (a desk edited before
this fix) and writes the re-timed file beside the master, so the reel finds it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Edits that leave every frame where it was: the captions are copied as they are.
SAME_TIMING = frozenset({"blur", "freeze", "soften"})
#: Shorter than this after a re-time, a cue is dropped (as :func:`creation.post.reel_sources.shift_cues`).
MIN_CUE_SECONDS = 0.05

_ASS_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")

Span = Callable[[float, float], "tuple[float, float] | None"]


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


def _number(edit: Mapping[str, Any], key: str) -> float | None:
    value = edit.get(key)
    return float(value) if isinstance(value, int | float) else None


def span_map(edit: Mapping[str, Any]) -> tuple[Span | None, str]:
    """How one edit moves a cue ``(start, end)``: the mapper (``None`` drops the cue), and what it does.

    Parameters
    ----------
    edit
        One finish-record edit (``{"op": "trim", "frames": [a, b], "fps": 24.0, ...}``).

    Returns
    -------
    tuple[Span | None, str]
        The mapper, or ``None`` when the kit cannot re-time this edit (the reason is the text).
    """

    op = str(edit.get("op") or "")
    if op in SAME_TIMING:
        return (
            lambda a, b: (a, b)
        ), "same times (the edit keeps every frame where it was)"
    if op == "trim":
        frames, fps = edit.get("frames"), _number(edit, "fps")
        if isinstance(frames, list | tuple) and len(frames) == 2 and fps:
            cut_a, cut_b = float(frames[0]) / fps, float(frames[1]) / fps
        elif isinstance(edit.get("cut"), list | tuple) and len(edit["cut"]) == 2:
            cut_a, cut_b = float(edit["cut"][0]), float(edit["cut"][1])
        else:
            return None, "the trim record names no cut"
        removed = cut_b - cut_a

        def trim(a: float, b: float) -> tuple[float, float] | None:
            def at(t: float) -> float:
                return t if t < cut_a else (cut_a if t < cut_b else t - removed)

            return at(a), at(b)

        return (
            trim,
            f"moved for the cut {cut_a:.3f}-{cut_b:.3f} s ({removed:.3f} s earlier after it)",
        )
    if op == "tempo":
        factor = _number(edit, "factor")
        if not factor or factor <= 0:
            return None, "the tempo record names no factor"
        start, end = _number(edit, "from"), _number(edit, "to")
        if start is None or end is None:

            def whole(a: float, b: float) -> tuple[float, float]:
                return a / factor, b / factor

            return whole, f"every time / {factor:g} (tempo {factor:g}x)"
        gained = (end - start) - (end - start) / factor

        def window(a: float, b: float) -> tuple[float, float]:
            def at(t: float) -> float:
                if t <= start:
                    return t
                if t <= end:
                    return start + (t - start) / factor
                return t - gained

            return at(a), at(b)

        return (
            window,
            f"times in {start:g}-{end:g} s / {factor:g}, later ones {gained:.3f} s earlier",
        )
    if op == "handles":
        start, end = _number(edit, "start_s"), _number(edit, "end_s")
        if start is None or end is None:
            return None, "the handles record names no window"
        length = end - start

        def keep(a: float, b: float) -> tuple[float, float]:
            return max(0.0, a - start), min(length, b - start)

        return (
            keep,
            f"times minus {start:.3f} s, outside {start:.3f}-{end:.3f} s dropped",
        )
    return None, f"the kit does not know how `{op or '?'}` moves the picture"


@dataclass(frozen=True)
class CaptionCarry:
    """What :func:`carry_ass` wrote (``out`` ``None``: nothing; ``line`` says why, ``!!``)."""

    out: Path | None
    line: str
    kept: int = 0
    dropped: int = 0


def carry_ass(
    source: Path, out: Path, edits: Sequence[Mapping[str, Any]]
) -> CaptionCarry:
    """Write ``source``'s captions through ``edits`` (oldest first) into ``out``; never overwrites.

    Parameters
    ----------
    source
        The ``.ass`` beside the master before the edits.
    out
        The ``.ass`` beside the edited master.
    edits
        The edits between the two masters.

    Returns
    -------
    CaptionCarry
        The file and the line to print; ``out`` ``None`` (and a ``!!`` line) when it could not be re-timed.
    """

    maps: list[Span] = []
    hows: list[str] = []
    for edit in edits:
        mapper, how = span_map(edit)
        if mapper is None:
            return CaptionCarry(
                None,
                f"!! captions NOT carried from `{source.name}`: {how}; the edited take has no .ass, so its reel "
                "and clips are uncaptioned. Run finish again on the take, then make the edit again.",
            )
        maps.append(mapper)
        hows.append(how)
    if out.exists():
        return CaptionCarry(
            out, f"- Captions: `{out.name}` is already beside the edited master (kept)"
        )
    kept_lines: list[str] = []
    kept = dropped = 0
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            kept_lines.append(line)
            continue
        head, rest = line.split(":", 1)
        parts = rest.split(",", 3)
        begin = _ass_seconds(parts[1]) if len(parts) > 3 else None
        end = _ass_seconds(parts[2]) if len(parts) > 3 else None
        if begin is None or end is None:
            return CaptionCarry(
                None,
                f"!! captions NOT carried from `{source.name}`: a cue's times cannot be read ({line[:60]!r}); "
                "the edited take has no .ass, so its reel and clips are uncaptioned",
            )
        span: tuple[float, float] | None = (begin, end)
        for mapper in maps:
            span = mapper(*span) if span is not None else None
        if span is None or span[1] - span[0] < MIN_CUE_SECONDS:
            dropped += 1
            continue
        parts[1], parts[2] = _ass_time(span[0]), _ass_time(span[1])
        kept_lines.append(f"{head}:{','.join(parts)}")
        kept += 1
    out.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
    gone = f"; {dropped} cue(s) fell inside the cut and are gone" if dropped else ""
    return CaptionCarry(
        out,
        f"- Captions: `{source.name}` -> `{out.name}` ({'; '.join(hows)}; {kept} cue(s){gone})",
        kept,
        dropped,
    )


def _record_at(record: Any, name: str) -> Any:
    from creation.post.finish_record import _load

    if record.path is None:
        return None
    path = record.path.parent / name
    return _load(path) if path.is_file() else None


def captions_for_record(
    desk: Path, record: Any, *, depth: int = 0
) -> tuple[Path | None, list[str]]:
    """The ``.ass`` of a record's master; on an older desk, carried from the record it was edited from.

    A record whose master has no ``.ass`` but whose last edit names the record
    it came from (``from_record``) gets that record's captions re-timed by the
    edit and written beside its master (a new file; nothing overwritten).

    Parameters
    ----------
    desk
        Series desk.
    record
        A :class:`creation.post.finish_record.FinishRecord`.

    Returns
    -------
    tuple[Path | None, list[str]]
        The captions (``None``: none found) and a line per step taken.
    """

    master = record.resolve(desk, "master")
    if master is None:
        return None, []
    ass = master.with_suffix(".ass")
    if ass.is_file():
        return ass, []
    if not record.edits or depth > 32:
        return None, []
    last = record.edits[-1]
    before = (
        _record_at(record, str(last.get("from_record") or ""))
        if last.get("from_record")
        else None
    )
    if before is None:
        return None, []
    found, notes = captions_for_record(desk, before, depth=depth + 1)
    if found is None:
        return None, notes
    carried = carry_ass(found, ass, [last])
    if carried.out is None:
        return None, [*notes, carried.line]
    return carried.out, [
        *notes,
        f"⚠ {record.take_id}: the master `{master.name}` had no captions file (an edit made before the kit carried "
        f"them); carried from `{found.name}` through the {last.get('op')}: `{ass.name}` ({carried.kept} cue(s))",
    ]
