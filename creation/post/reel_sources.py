"""Where ``reel`` finds a take's material when the take has no finish record (a desk finished before the kit wrote one).

A desk finished before ``finish`` wrote ``take-epNN-tK-finish-vN.json`` (or one
finished by hand) still has every file the reel needs; nothing says which is
which. :func:`infer_take_source` works it out from the accepted file alone,
reading only what the desk already says, and names every step it took:

1. **Edits after finish.** The accepted file is followed back through the
   ``trim`` / ``tempo`` edits that made it (``edit-chain.jsonl``, else the run
   notes' ``Trim -> …`` / ``Tempo …x`` blocks). An old ``Trim -> `X``` block
   does not name its source: the source is the newest file of the same name
   family (``take-ep01-t1-sokii-trim-v5`` -> ``take-ep01-t1-sokii-vN``) the run
   notes named before the block, and only when its length minus the cut is the
   length the block printed.
2. **The un-marked master** of the finished file: the run notes'
   ``Watermarked -> `F` (the un-marked master is `M`)``, else ``F`` without its
   ``-sokii`` suffix (a hand chain), when the two are the same length.
3. **The picture before the captions**: the newest ``Mix:`` file the run
   notes name before the master's ``Captions … -> `M``` line (else the
   ``-mix-vN`` of a ``-cap-vN`` master, by version number), the same length as
   the master. When that mix laid nothing by hand and the same finish chain
   names its ``Colour match … -> `C``` file, ``C`` is the sound before the bed
   (as a record's ``pre_bed``). When the mix laid hand voices, mutes or cues,
   the file before it lacks them, so the reel cuts the mix itself with its bed
   in (the music cuts with the picture; no second bed goes under).
4. **The same edits on the source**: each trim / tempo is applied again to a
   scratch copy (never a desk file), and the result must be the accepted
   file's length.
5. **Captions** (no ``.ass`` for the master): the take's spine lines, timed on
   the windows the run notes recorded for the master's captions, else on the
   take's words json of the same finish run, built with the kit's caption
   builder (:func:`creation.captions.build_line_cues`) and moved through the
   edits.

When the source cannot be followed, the reel is cut from the accepted file
itself: its burned captions and mark stay as they are (no second captions,
no second mark, no second bed), with a ⚠ saying so. Nothing is guessed
silently: every inferred step is a ⚠ line on the plan.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from creation.captions import (
    CaptionLine,
    Cue,
    Span,
    build_line_cues,
    captions_whole_lines,
    desk_take_count,
    is_english,
    take_caption_lines,
    time_lines,
)
from creation.post.media import probe_video

#: Two files are "the same length" within this many frames.
SAME_LENGTH_FRAMES = 2.0

_HEADING = re.compile(r"^## ")
_TAKE_FILE = re.compile(r"take-ep\d+-t\d+-.*\.mp4$")
_FINISH_CHAIN = re.compile(r"^Finish chain on `([^`]+)`")
_FINAL = re.compile(r"^Final: (.+\.mp4)\s*$")
_WATERMARK = re.compile(
    r"^Watermarked -> `([^`]+\.mp4)` \(the un-marked master is `([^`]+\.mp4)`\)"
)
_CAPTIONS = re.compile(r"^Captions\b.*?-> `([^`]+\.mp4)`(?::\s*(.*))?$")
_WINDOW = re.compile(
    r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)s? ('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")( \(italic\))?"
)
_MIX = re.compile(r"^Mix: (\S+\.mp4):")
_HAND_LAYERS = re.compile(
    r"hand layers: (\d+) voice\(s\), (\d+) mute\(s\), (\d+) cue\(s\)"
)
_CUE_WINDOW = re.compile(r"^- cue window ")
_COLOUR = re.compile(r"^Colour match\b.*?-> `([^`]+\.mp4)`")
_TRIM = re.compile(r"^Trim (?:`([^`]+\.mp4)` )?-> `([^`]+\.mp4)`")
_TRIM_FRAME = re.compile(r"\(frame (\d+),")
_TRIM_REMOVED = re.compile(r"Removed ([\d.]+) s \(([\d.]+)-([\d.]+) s\)")
_TRIM_LENGTH = re.compile(r"New length ([\d.]+) s")
_TEMPO = re.compile(r"^Tempo ([\d.]+)x `([^`]+\.mp4)` -> `([^`]+\.mp4)`")
_VERSION = re.compile(r"-v(\d+)$")


# --- the run notes -------------------------------------------------------------------------------


@dataclass(frozen=True)
class NoteEvent:
    """One thing the run notes say a step wrote, in file order (``order``)."""

    order: int
    kind: str
    out: str
    src: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


def _name(text: str) -> str:
    return Path(text.strip()).name


def parse_run_notes(text: str) -> list[NoteEvent]:
    """The step outputs an episode's ``run-notes.md`` names, in order.

    Parameters
    ----------
    text
        The run notes.

    Returns
    -------
    list[NoteEvent]
        ``finish_chain``, ``final``, ``watermark``, ``captions``, ``mix``, ``colour``,
        ``trim`` and ``tempo`` events (file names only).
    """

    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if _HEADING.match(line):
            blocks.append([])
        else:
            blocks[-1].append(line.strip())
    events: list[NoteEvent] = []
    order = 0

    def add(kind: str, out: str, src: str | None = None, **detail: Any) -> None:
        nonlocal order
        events.append(
            NoteEvent(order, kind, _name(out), _name(src) if src else None, detail)
        )
        order += 1

    for block in blocks:
        body = "\n".join(block)
        for index, line in enumerate(block):
            if m := _FINISH_CHAIN.match(line):
                add("finish_chain", m.group(1))
            elif m := _FINAL.match(line):
                add("final", m.group(1))
            elif m := _WATERMARK.match(line):
                add("watermark", m.group(1), m.group(2))
            elif m := _CAPTIONS.match(line):
                windows = [
                    (
                        float(w.group(1)),
                        float(w.group(2)),
                        str(ast.literal_eval(w.group(3))),
                        bool(w.group(4)),
                    )
                    for w in _WINDOW.finditer(m.group(2) or "")
                ]
                add("captions", m.group(1), windows=windows)
            elif m := _MIX.match(line):
                rest = block[index + 1 :]
                layers = next((h for r in rest if (h := _HAND_LAYERS.search(r))), None)
                add(
                    "mix", m.group(1),
                    voices=int(layers.group(1)) if layers else 0,
                    mutes=int(layers.group(2)) if layers else 0,
                    cues=int(layers.group(3)) if layers else 0,
                    cue_windows=sum(1 for r in rest if _CUE_WINDOW.match(r)),
                )  # fmt: skip
            elif m := _COLOUR.match(line):
                add("colour", m.group(1))
            elif m := _TRIM.match(line):
                frames = [int(f) for f in _TRIM_FRAME.findall(body)[:2]]
                removed = _TRIM_REMOVED.search(body)
                length = _TRIM_LENGTH.search(body)
                add(
                    "trim", m.group(2), m.group(1),
                    frames=frames if len(frames) == 2 else None,
                    cut=[float(removed.group(2)), float(removed.group(3))] if removed else None,
                    removed=float(removed.group(1)) if removed else None,
                    length=float(length.group(1)) if length else None,
                )  # fmt: skip
            elif m := _TEMPO.match(line):
                add("tempo", m.group(3), m.group(2), factor=float(m.group(1)))
    return events


def _notes(takes: Path) -> list[NoteEvent]:
    path = takes.parent / "run-notes.md"
    if not path.is_file():
        return []
    return parse_run_notes(path.read_text(encoding="utf-8"))


def _canonical(name: str) -> str:
    """``take-epNN-tK-…mp4`` inside a delivered name (``Show - take-ep01-t1-…-sokii.mp4``)."""

    match = _TAKE_FILE.search(name)
    return match.group(0) if match else name


def _family(name: str, op: str) -> str:
    """``take-ep01-t1-sokii-trim-v5.mp4`` -> ``take-ep01-t1-sokii`` (the name of what was trimmed)."""

    stem = _VERSION.sub("", Path(_canonical(name)).stem)
    return re.sub(rf"-{op}(?:-cover)?$", "", stem)


def accepted_from_notes(desk: Path, episode: int, take_id: str) -> Path | None:
    """The newest ``-sokii`` file the run notes name as a ``Final:`` or a ``Trim ->`` / ``Tempo`` output.

    Parameters
    ----------
    desk, episode, take_id
        The take.

    Returns
    -------
    Path | None
        The file on the desk, or ``None`` when the notes name none that exists.
    """

    takes = desk / f"ep{episode:02d}" / "takes"
    prefix = f"take-ep{episode:02d}-{take_id}-"
    for event in reversed(_notes(takes)):
        if event.kind in ("final", "trim", "tempo") and "sokii" in event.out:
            if event.out.startswith(prefix) and (takes / event.out).is_file():
                return takes / event.out
    return None


def takes_in_notes(desk: Path, episode: int) -> list[str]:
    """Every ``tK`` the run notes name a ``-sokii`` final or edit for, in take order."""

    takes = desk / f"ep{episode:02d}" / "takes"
    found = {
        m.group(1)
        for e in _notes(takes)
        if e.kind in ("final", "trim", "tempo") and "sokii" in e.out
        if (m := re.match(rf"take-ep{episode:02d}-(t\d+)-", e.out))
    }
    return sorted(found, key=lambda t: int(t[1:]))


# --- the inference -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    """One edit made after finish, to apply again: ``trim`` (``frames`` at ``fps``) or ``tempo`` (``factor``)."""

    op: str
    source: Path
    output: Path
    frames: tuple[int, int] | None = None
    fps: float = 24.0
    factor: float = 1.0

    @property
    def cut(self) -> tuple[float, float]:
        assert self.frames is not None
        return self.frames[0] / self.fps, self.frames[1] / self.fps

    def as_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "op": self.op,
            "source": self.source.name,
            "output": self.output.name,
        }
        if self.op == "trim" and self.frames:
            body.update(
                frames=list(self.frames),
                fps=self.fps,
                cut=[round(t, 3) for t in self.cut],
            )
        if self.op == "tempo":
            body["factor"] = self.factor
        return body


@dataclass
class Inferred:
    """What :func:`infer_take_source` found, and how (every line is shown and kept on the plan)."""

    accepted: Path
    #: The desk file the reel's source is cut from (before the edits are applied again).
    source: Path
    edits: list[Edit] = field(default_factory=list)
    #: The source carries the take's bed (a mix): no second bed goes under.
    bed_in_source: bool = False
    #: The source is the accepted file itself: burned captions and mark kept.
    burned: bool = False
    cues: tuple[Cue, ...] | None = None
    notes: list[str] = field(default_factory=list)

    def as_json(self, desk: Path) -> dict[str, Any]:
        def rel(p: Path) -> str:
            try:
                return str(p.resolve().relative_to(desk.resolve()))
            except ValueError:
                return str(p)

        return {
            "accepted": rel(self.accepted),
            "source": rel(self.source),
            "edits": [e.as_json() for e in self.edits],
            "bed_in_source": self.bed_in_source,
            "burned_captions_kept": self.burned,
            "captions_rebuilt": self.cues is not None,
            "how": list(self.notes),
        }


def _seconds(path: Path) -> tuple[float, float]:
    info = probe_video(path)
    return info.duration_seconds, info.fps or 24.0


def _same_length(a: Path, b: Path) -> bool:
    (da, fps), (db, _) = _seconds(a), _seconds(b)
    return abs(da - db) <= SAME_LENGTH_FRAMES / fps


def _chain_edit(desk: Path, takes: Path, name: str) -> dict[str, Any] | None:
    """The newest ``trim`` / ``tempo`` line of ``edit-chain.jsonl`` whose output is ``name``."""

    chain = takes / "edit-chain.jsonl"
    if not chain.is_file():
        return None
    hit: dict[str, Any] | None = None
    for raw in chain.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("op") in ("trim", "tempo"):
            if Path(str(entry.get("output") or "")).name == name and entry.get(
                "source"
            ):
                hit = entry
    return hit


def _edits_back(desk: Path, takes: Path, accepted: Path, events: Sequence[NoteEvent],
                notes: list[str]) -> tuple[Path, list[Edit]]:  # fmt: skip
    """Follow ``accepted`` back through its trim / tempo edits to the finished file they started from."""

    edits: list[Edit] = []
    current = accepted
    for _ in range(16):
        name = _canonical(current.name)
        entry = _chain_edit(desk, takes, name)
        if entry is not None:
            source = Path(str(entry["source"]))
            source = source if source.is_absolute() else desk / source
            if not source.is_file():
                notes.append(
                    f"⚠ `{name}`: edit-chain.jsonl names `{source.name}` as its source, but it is not on the desk"
                )
                break
            _, fps = _seconds(source)
            if entry["op"] == "trim":
                cut = entry.get("cut") or [0.0, 0.0]
                frames = (round(float(cut[0]) * fps), round(float(cut[1]) * fps))
                edit = Edit("trim", source, current, frames=frames, fps=fps)
            else:
                edit = Edit(
                    "tempo", source, current, factor=float(entry.get("factor") or 1.0)
                )
            notes.append(
                f"⚠ `{name}` is a {edit.op} of `{source.name}` (edit-chain.jsonl)"
            )
            edits.append(edit)
            current = source
            continue
        event = next(
            (
                e
                for e in reversed(events)
                if e.kind in ("trim", "tempo") and e.out == name
            ),
            None,
        )
        if event is None:
            break
        source = takes / event.src if event.src else None
        why = "the run notes' block names it"
        if source is None:
            family = _family(name, event.kind)
            before = [
                e.out for e in events
                if e.order < event.order and e.kind in ("final", "watermark", "trim", "tempo")
                and _VERSION.sub("", Path(e.out).stem) == family and (takes / e.out).is_file()
            ]  # fmt: skip
            if not before:
                notes.append(
                    f"⚠ `{name}`: the run notes' {event.kind} block names no source and no `{family}-vN` "
                    "file comes before it: the edit cannot be followed"
                )
                break
            source = takes / before[-1]
            why = f"its block names no source: the newest `{family}` file the run notes name before it"
        if not source.is_file():
            notes.append(f"⚠ `{name}`: its source `{source.name}` is not on the desk")
            break
        length, fps = _seconds(source)
        if event.kind == "trim":
            frames = event.detail.get("frames")
            cut = event.detail.get("cut")
            if not frames and cut:
                frames = [round(cut[0] * fps), round(cut[1] * fps)]
            if not frames:
                notes.append(
                    f"⚠ `{name}`: the trim block gives no cut: the edit cannot be followed"
                )
                break
            edit = Edit(
                "trim",
                source,
                current,
                frames=(int(frames[0]), int(frames[1])),
                fps=fps,
            )
            expected = event.detail.get("length")
            removed = (edit.frames[1] - edit.frames[0]) / fps  # type: ignore[index]
            if (
                expected is not None
                and abs(length - removed - float(expected)) > SAME_LENGTH_FRAMES / fps
            ):
                notes.append(
                    f"⚠ `{name}`: `{source.name}` is {length:.3f} s; less the {removed:.3f} s cut that is not the "
                    f"{float(expected):.3f} s the trim block printed: not its source, the edit cannot be followed"
                )
                break
            check = (
                f"; {length:.3f} s − {removed:.3f} s = {length - removed:.3f} s, the length the block printed"
                if expected is not None
                else ""
            )
            notes.append(
                f"⚠ `{name}` is a trim of `{source.name}` (run notes; {why}); cut frames "
                f"{edit.frames[0]}-{edit.frames[1]} ({edit.cut[0]:.3f}-{edit.cut[1]:.3f} s){check}"  # type: ignore[index]
            )
        else:
            edit = Edit(
                "tempo",
                source,
                current,
                factor=float(event.detail.get("factor") or 1.0),
            )
            notes.append(
                f"⚠ `{name}` is tempo {edit.factor:g}x of `{source.name}` (run notes)"
            )
        edits.append(edit)
        current = source
    edits.reverse()
    return current, edits


def _master_of(
    takes: Path, finished: Path, events: Sequence[NoteEvent], notes: list[str]
) -> Path | None:
    """The un-marked captioned file the mark went on."""

    name = _canonical(finished.name)
    hit = next(
        (e for e in reversed(events) if e.kind == "watermark" and e.out == name), None
    )
    if hit is not None and hit.src and (takes / hit.src).is_file():
        notes.append(
            f"⚠ un-marked master `{hit.src}` (the run notes' Watermarked line for `{name}`)"
        )
        return takes / hit.src
    candidates = [takes / name] if name != finished.name else []
    if name.endswith("-sokii.mp4"):
        candidates.append(takes / name.replace("-sokii.mp4", ".mp4"))
    for candidate in candidates:
        if (
            candidate.is_file()
            and candidate.resolve() != finished.resolve()
            and _same_length(candidate, finished)
        ):
            if candidate.name.endswith("-sokii.mp4"):
                continue
            notes.append(
                f"⚠ un-marked master `{candidate.name}`: `{finished.name}` without its mark suffix, the same length "
                "(no Watermarked line names it)"
            )
            return candidate
    return None


def _picture_of(takes: Path, master: Path, events: Sequence[NoteEvent], notes: list[str]
                ) -> tuple[Path, NoteEvent | None, NoteEvent | None] | None:  # fmt: skip
    """The mix the master's captions went on (and the run notes' mix / captions events)."""

    captioned = next(
        (e for e in reversed(events) if e.kind == "captions" and e.out == master.name),
        None,
    )
    prefix = re.match(r"take-ep\d+-t\d+-", master.name)
    if captioned is not None:
        mix = next(
            (e for e in reversed(events)
             if e.kind == "mix" and e.order < captioned.order
             and (prefix is None or e.out.startswith(prefix.group(0)))),
            None,
        )  # fmt: skip
        if (
            mix is not None
            and (takes / mix.out).is_file()
            and _same_length(takes / mix.out, master)
        ):
            notes.append(
                f"⚠ picture before the captions: `{mix.out}` (the last Mix line before the Captions line of "
                f"`{master.name}`; the same length)"
            )
            return takes / mix.out, mix, captioned
    by_version = re.sub(r"-cap-v(\d+)\.mp4$", r"-mix-v\1.mp4", master.name)
    if (
        by_version != master.name
        and (takes / by_version).is_file()
        and _same_length(takes / by_version, master)
    ):
        notes.append(
            f"⚠ picture before the captions: `{by_version}` (matched to `{master.name}` by version number only)"
        )
        mix = next(
            (e for e in reversed(events) if e.kind == "mix" and e.out == by_version),
            None,
        )
        return takes / by_version, mix, captioned
    return None


def _colour_before(
    takes: Path, mix: NoteEvent, events: Sequence[NoteEvent]
) -> Path | None:
    """The colour file the same finish chain made just before ``mix`` (the sound before the bed)."""

    chain_start = max(
        (e.order for e in events if e.kind == "finish_chain" and e.order < mix.order),
        default=-1,
    )
    colour = next(
        (
            e
            for e in reversed(events)
            if e.kind == "colour" and chain_start < e.order < mix.order
        ),
        None,
    )
    return (
        takes / colour.out
        if colour is not None and (takes / colour.out).is_file()
        else None
    )


def _words_json(takes: Path, master: Path) -> Path | None:
    """The finish run's transcript: ``take-epNN-tK-words-*-vN.json`` of the master's version (kit names only)."""

    match = re.match(r"(take-ep\d+-t\d+)-cap-v(\d+)\.mp4$", master.name)
    if match is None:
        return None
    found = sorted(takes.glob(f"{match.group(1)}-words-*-v{match.group(2)}.json"))
    return found[-1] if found else None


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def rebuild_cues(
    spine: Mapping[str, Any],
    *,
    desk: Path,
    episode: int,
    take_index: int,
    duration: float,
    windows: Sequence[tuple[float, float, str, bool]] = (),
    marks_italic: bool = False,
    words_json: Path | None = None,
    notes: list[str],
    label: str,
) -> tuple[Cue, ...] | None:
    """The take's captions rebuilt with the kit's builder, on recorded windows or a words json.

    Parameters
    ----------
    spine
        The saved spine.
    desk, episode, take_index
        The take (its own beats' lines).
    duration
        The take's length on the timeline the windows and words are on.
    windows
        ``(start, end, text, italic)`` per line, as the run notes recorded the master's
        captions: the accepted wording and times (the spine may have changed since).
    marks_italic
        The run notes mark italic lines (``(italic)``): a window without the mark is upright.
        Otherwise each line's slant is the spine's.
    words_json
        A transcript of the take on the same timeline.
    notes
        Report lines (appended).
    label
        The file the captions were on (for the report).

    Returns
    -------
    tuple[Cue, ...] | None
        The cues, or ``None`` when they cannot be rebuilt.
    """

    from creation.post.whisper import load_words

    lines, warning = take_caption_lines(
        dict(spine),
        episode,
        take_index=take_index,
        take_count=desk_take_count(desk, episode),
    )
    if warning:
        notes.append(f"⚠ {warning}")
    if not lines:
        notes.append(
            f"⚠ no spine lines for t{take_index}: the reel is uncaptioned there"
        )
        return None
    whole = captions_whole_lines(dict(spine))
    texts = [line.text for line in lines]
    shown: list[CaptionLine] = list(lines)
    if windows and len(windows) == len(lines):
        # The accepted cut's wording and times: what the human approved, even when the spine changed since.
        differ = [
            f"accepted {w[2]!r}, spine now {line.text!r}"
            for w, line in zip(windows, lines)
            if _plain(w[2]) != _plain(line.text)
        ]
        anchors = [Span(w[0], w[1]) for w in windows]
        italic = [
            w[3] if marks_italic else line.italic for w, line in zip(windows, shown)
        ]
        how = (
            f"the wording and {len(windows)} window(s) the run notes recorded for `{label}`"
            f" (slant: {'as recorded' if marks_italic else 'the spine'})"
        )
        if differ:
            how += f"; kept the accepted wording where the spine changed since: {'; '.join(differ)}"
        groups = build_line_cues(
            [w[2] for w in windows], anchors, whole_lines=whole, italic=italic,
            skip=[not is_english(w[2]) for w in windows], fixed_ends=[True] * len(anchors),
        )  # fmt: skip
    elif words_json is not None:
        try:
            timing = time_lines(
                lines, duration=duration, words=load_words(words_json), spans=None
            )
        except ValueError as exc:
            notes.append(
                f"⚠ captions not rebuilt: `{words_json.name}` does not time every line ({exc}); the reel is uncaptioned there"
            )
            return None
        how = f"timed on `{words_json.name}` ({', '.join(timing.methods)})"
        groups = build_line_cues(
            texts, list(timing.anchors), whole_lines=whole, italic=[line.italic for line in shown],
            skip=[not line.english for line in shown], holds=timing.holds, fixed_ends=timing.fixed_ends,
        )  # fmt: skip
    else:
        reason = (
            f"the run notes recorded {len(windows)} caption window(s) for {len(lines)} line(s)"
            if windows
            else "no caption windows in the run notes and no words json of the finish run"
        )
        notes.append(
            f"⚠ captions not rebuilt ({reason}): the reel is uncaptioned there"
        )
        return None
    cues = tuple(sorted((c for g in groups for c in g), key=lambda c: c.start))
    grain = "whole English lines" if whole else "word flicker"
    notes.append(
        f"⚠ captions rebuilt (no .ass for `{label}`): {len(lines)} spine line(s), {grain}, {how}"
    )
    return cues


def shift_cues(cues: Sequence[Cue], edits: Sequence[Edit]) -> tuple[Cue, ...]:
    """Move cues through the edits (oldest first): a trim drops what was inside and pulls the rest earlier.

    Parameters
    ----------
    cues
        On the source's timeline.
    edits
        The trim / tempo edits to the accepted file.

    Returns
    -------
    tuple[Cue, ...]
        On the accepted file's timeline.
    """

    from creation.post.edit import shift_time

    out = list(cues)
    for edit in edits:
        moved: list[Cue] = []
        for cue in out:
            if edit.op == "tempo":
                moved.append(
                    Cue(
                        cue.start / edit.factor,
                        cue.end / edit.factor,
                        cue.text,
                        italic=cue.italic,
                    )
                )
                continue
            a, b = edit.cut
            start, end = shift_time(cue.start, a, b), shift_time(cue.end, a, b)
            if start is None and (end is None or cue.end <= b):
                continue
            start = a if start is None else start
            end = a if end is None else end
            if end - start >= 0.05:
                moved.append(Cue(start, end, cue.text, italic=cue.italic))
        out = moved
    return tuple(out)


def apply_edits(
    source: Path, edits: Sequence[Edit], scratch: Path, take_id: str
) -> Path:
    """The same trims / tempos on ``source``, into scratch files (never on the desk).

    Parameters
    ----------
    source
        The desk file the reel cuts from.
    edits
        Oldest first.
    scratch
        A temporary folder.
    take_id
        For the scratch names.

    Returns
    -------
    Path
        The edited copy (``source`` itself when there are no edits).
    """

    from creation.post.edit import change_tempo, cut_frames

    current = source
    for index, edit in enumerate(edits, start=1):
        out = scratch / f"{take_id}-source-{index}-{edit.op}.mp4"
        if edit.op == "trim":
            assert edit.frames is not None
            rate = probe_video(current).fps or edit.fps
            cut_frames(
                current, out, fps=rate,
                begin=round(edit.frames[0] / edit.fps * rate), stop=round(edit.frames[1] / edit.fps * rate),
            )  # fmt: skip
        else:
            change_tempo(current, out, factor=edit.factor)
        current = out
    return current


def infer_take_source(
    desk: Path,
    episode: int,
    take_id: str,
    accepted: Path,
    *,
    spine: Mapping[str, Any],
    scratch: Path,
) -> tuple[Path, Inferred]:
    """A take's reel source and captions from its accepted file alone (no finish record).

    Parameters
    ----------
    desk, episode, take_id
        The take.
    accepted
        The accepted (finished, marked) file.
    spine
        The saved spine (the captions' lines).
    scratch
        Where edited copies go (a temporary folder; never the desk).

    Returns
    -------
    tuple[Path, Inferred]
        The file to cut (a scratch copy when edits were applied again) and how it was found.
    """

    takes = desk / f"ep{episode:02d}" / "takes"
    events = _notes(takes)
    notes: list[str] = [
        f"⚠ {take_id}: no finish record names `{accepted.name}`; inferred from the desk:"
    ]
    finished, edits = _edits_back(desk, takes, accepted, events, notes)
    master = _master_of(takes, finished, events, notes)
    found = _picture_of(takes, master, events, notes) if master is not None else None
    if found is None:
        why = (
            "no un-marked master"
            if master is None
            else f"no mix file the captions of `{master.name}` went on"
        )
        notes.append(
            f"⚠ the source cannot be followed ({why}): the reel is cut from `{accepted.name}` itself, its burned "
            "captions, mark and bed kept as they are (a cut may fall mid-caption; no second captions, mark or bed)"
        )
        return accepted, Inferred(
            accepted, accepted, [], bed_in_source=True, burned=True, notes=notes
        )
    mix_file, mix, captioned = found
    source, bed_in = mix_file, True
    hand = (
        {
            k: int(mix.detail.get(k) or 0)
            for k in ("voices", "mutes", "cues", "cue_windows")
        }
        if mix is not None
        else None
    )
    colour = _colour_before(takes, mix, events) if mix is not None else None
    if (
        hand is not None
        and not any(hand.values())
        and colour is not None
        and _same_length(colour, mix_file)
    ):
        source, bed_in = colour, False
        notes.append(
            f"⚠ sound before the bed: `{colour.name}` (the Colour match file of the same finish chain; the mix laid "
            "nothing by hand)"
        )
    else:
        if hand is None:
            why = "the run notes do not say what the mix laid"
        elif any(hand.values()):
            why = (
                f"the mix laid {hand['voices']} voice(s), {hand['mutes']} mute(s) and "
                f"{max(hand['cues'], hand['cue_windows'])} cue(s) by hand that the file before it lacks"
            )
        else:
            why = "the finish chain names no colour file before it"
        notes.append(
            f"⚠ the reel cuts `{mix_file.name}` with its bed in ({why}): the music cuts with the picture, "
            "no second bed goes under"
        )
    cut = apply_edits(source, edits, scratch, take_id)
    if edits:
        if not _same_length(cut, accepted):
            notes.append(
                f"⚠ `{source.name}` with the same edit(s) is {_seconds(cut)[0]:.3f} s, not the accepted "
                f"{_seconds(accepted)[0]:.3f} s: the reel is cut from `{accepted.name}` itself, burned captions, "
                "mark and bed kept"
            )
            return accepted, Inferred(
                accepted, accepted, [], bed_in_source=True, burned=True, notes=notes
            )
        notes.append(
            f"⚠ the same {' + '.join(e.op for e in edits)} applied to `{source.name}` in a scratch copy "
            f"({_seconds(cut)[0]:.3f} s, the accepted file's length)"
        )
    match = re.match(r"t(\d+)$", take_id)
    cues = rebuild_cues(
        spine, desk=desk, episode=episode, take_index=int(match.group(1)) if match else 1,
        duration=_seconds(mix_file)[0],
        windows=captioned.detail.get("windows") or () if captioned is not None else (),
        marks_italic=any(
            w[3] for e in events if e.kind == "captions" for w in e.detail.get("windows") or ()
        ),
        words_json=_words_json(takes, master) if master is not None else None,
        notes=notes, label=master.name if master is not None else accepted.name,
    )  # fmt: skip
    if cues is not None and edits:
        cues = shift_cues(cues, edits)
    return cut, Inferred(
        accepted, source, edits, bed_in_source=bed_in, cues=cues, notes=notes
    )


__all__ = [
    "Edit",
    "Inferred",
    "NoteEvent",
    "accepted_from_notes",
    "apply_edits",
    "infer_take_source",
    "parse_run_notes",
    "rebuild_cues",
    "shift_cues",
    "takes_in_notes",
]
