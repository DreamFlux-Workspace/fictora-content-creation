"""Which take a local edit came from, so later steps can trust the raw take's timeline.

Every local edit (``deboard``, ``freeze``, ``soften``, ``trim``, ``tempo``, and
each step ``finish`` writes) appends one line to
``epNN/takes/edit-chain.jsonl``: ``{op, source, output}`` (paths relative to
the desk). :func:`raw_take_behind` walks that chain back from any file to the
raw take it was made from and says whether every edit on the way kept the
sound timeline, i.e. a word the server heard at 3.20 s on the raw take is still
at 3.20 s on the file.

``freeze``, ``soften``, ``deboard`` and ``colour`` keep it (same length, same
frame count, sound copied or untouched). ``trim`` and ``tempo`` move it; the
hand ``voice`` step (``--voice`` / ``--mute``) and the sound steps (``sfx``,
``cues``, ``mix``) change the speech or the sound, so a transcript of the raw
take no longer describes the file.

Desks edited before the chain file existed are read from their run notes: the
``freeze``, ``soften`` and ``deboard`` commands have always written a line
naming the file they read and the file they wrote.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Edits that keep the length, the frame count and the sound: the raw take's word times still hold.
KEEPS_TIMELINE = frozenset({"freeze", "soften", "deboard", "colour"})
#: Why each other known edit breaks the raw take's word times.
BREAKS_TIMELINE = {
    "trim": "trim cuts time out, so every word after the cut moved",
    "tempo": "tempo changes the speed, so every word moved",
    "voice": "the hand voice step (--voice/--mute) changed the speech",
    "sfx": "the sound effects step changed the sound",
    "cues": "the hand cues step changed the sound",
    "mix": "the mix put the bed under the take",
    "captions": "the captions step burned lines on the take",
    "watermark": "the mark went on the take",
}
#: The chain file, one per episode.
CHAIN_FILE = "edit-chain.jsonl"
#: A chain longer than this is refused (a loop in the records).
MAX_DEPTH = 32

_RAW = re.compile(r"^take-ep\d+-t\d+-raw-v\d+\.mp4$")
#: Run-note lines of desks edited before the chain file: ``(op, pattern)`` naming ``src`` and ``out``.
_NOTE_LINES = (
    (
        "freeze",
        re.compile(r"^`(?P<src>[^`]+\.mp4)`: Freeze at .*?-> `(?P<out>[^`]+\.mp4)`"),
    ),
    (
        "soften",
        re.compile(r"^Softened .*? in `(?P<src>[^`]+\.mp4)` -> `(?P<out>[^`]+\.mp4)`"),
    ),
    (
        "deboard",
        re.compile(
            r"^Deboard `(?P<src>[^`]+\.mp4)` against `[^`]+`: (?P<out>\S+\.mp4): replaced"
        ),
    ),
)


def _stored(desk: Path, path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(desk.expanduser().resolve()))
    except ValueError:
        return str(resolved)


def _episode_takes(path: Path) -> Path:
    return path.expanduser().resolve().parent


def record_edit(
    desk: Path, *, op: str, source: Path, output: Path, **detail: Any
) -> Path:
    """Append one edit to the episode's chain file (next to ``output``).

    Parameters
    ----------
    desk
        Series desk.
    op
        ``freeze``, ``soften``, ``deboard``, ``colour``, ``trim``, ``tempo``, or a ``finish`` step name.
    source
        The file the edit read.
    output
        The file it wrote.
    **detail
        Anything else worth keeping (``at``, ``hold``, ``factor``, ...).

    Returns
    -------
    Path
        The chain file.
    """

    chain = _episode_takes(output) / CHAIN_FILE
    entry = {
        "op": op,
        "source": _stored(desk, source),
        "output": _stored(desk, output),
        **detail,
    }
    with chain.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return chain


def _chain_entries(desk: Path, takes: Path) -> dict[str, tuple[str, Path]]:
    """``output file name -> (op, source path)`` from the chain file, newest wins."""

    found: dict[str, tuple[str, Path]] = {}
    chain = takes / CHAIN_FILE
    if not chain.is_file():
        return found
    for raw in chain.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (
            not isinstance(entry, dict)
            or not entry.get("output")
            or not entry.get("source")
        ):
            continue
        source = Path(str(entry["source"]))
        output = Path(str(entry["output"]))
        found[output.name] = (
            str(entry.get("op") or "?"),
            source if source.is_absolute() else desk / source,
        )
    return found


def _note_entries(takes: Path) -> dict[str, tuple[str, Path]]:
    """``output file name -> (op, source path)`` from the episode's run notes (desks older than the chain)."""

    found: dict[str, tuple[str, Path]] = {}
    notes = takes.parent / "run-notes.md"
    if not notes.is_file():
        return found
    for line in notes.read_text(encoding="utf-8").splitlines():
        for op, pattern in _NOTE_LINES:
            match = pattern.match(line.strip())
            if match:
                found[Path(match["out"]).name] = (op, takes / Path(match["src"]).name)
    return found


@dataclass(frozen=True)
class Lineage:
    """How a file came from its raw take.

    Parameters
    ----------
    raw
        The raw take at the root, or ``None`` when the chain could not be followed to one.
    steps
        ``(op, file)`` from the raw take forward.
    keeps_timeline
        Every step kept the sound timeline (the raw take's word times hold on the file).
    reason
        Why not, when ``keeps_timeline`` is false.
    """

    raw: Path | None
    steps: tuple[tuple[str, Path], ...]
    keeps_timeline: bool
    reason: str = ""

    def chain_text(self) -> str:
        """``raw-v1 -> freeze -> freeze`` for a report line."""

        if self.raw is None:
            return ""
        return " -> ".join([self.raw.name, *(op for op, _ in self.steps)])


def raw_take_behind(desk: Path, file: Path) -> Lineage:
    """Walk the edit chain back from ``file`` to the raw take it was made from.

    Parameters
    ----------
    desk
        Series desk.
    file
        A take file on the desk (a raw take is its own root).

    Returns
    -------
    Lineage
        The raw take, the edits on the way and whether they all kept the sound timeline.
    """

    desk = desk.expanduser().resolve()
    current = file.expanduser().resolve()
    takes = current.parent
    chain = _chain_entries(desk, takes)
    notes: dict[str, tuple[str, Path]] | None = None
    steps: list[tuple[str, Path]] = []
    breaks = ""
    for _ in range(MAX_DEPTH):
        if _RAW.match(current.name):
            steps.reverse()
            return Lineage(current, tuple(steps), not breaks, breaks)
        hit = chain.get(current.name)
        if hit is None:
            notes = _note_entries(takes) if notes is None else notes
            hit = notes.get(current.name)
        if hit is None:
            return Lineage(
                None,
                (),
                False,
                f"no record of how `{current.name}` was made (edit-chain.jsonl or run notes)",
            )
        op, source = hit
        if op not in KEEPS_TIMELINE and not breaks:
            breaks = BREAKS_TIMELINE.get(
                op, f"`{op}` is not an edit known to keep the sound timeline"
            )
            breaks = f"`{current.name}` came from `{source.name}` by {op}: {breaks}"
        steps.append((op, current))
        current = source.expanduser().resolve()
    return Lineage(
        None, (), False, f"the edit chain behind `{file.name}` loops or is too long"
    )
