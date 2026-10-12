"""Salvage before re-filming: the free edits to try on a flawed take before a paid re-film.

Gallery Heiress review (Mihir, 12 Oct 2026): takes were re-filmed (paid) or
scratched when a free edit on this laptop would have saved them:

- a character drawn twice for a moment: "speed up or cut that shot short so
  that we can make it usable"; "if she was only there for half a second we
  could have reconsidered" (``trim --cut``, ``tempo --from --to``);
- "cut the part where the male MC looked at himself" (``trim --cut``, or the
  handles ``trim --start/--end`` when it is at an end);
- "scenes being out of order can be made in order ... for free" (``join
  --take-file`` in the right order for takes);
- "instead of the cut you could ... zoom into the stranger's face as he enters
  the doorway" (``zoom``: a punch-in hides a jump cut or crops a stray element);
- "take 1 raw is decent at best, could be repurposed for some other scene"
  (``keep-take``: a decent rejected take goes on the desk's reuse list).

Only a flaw no free edit can hide goes straight to a re-film: a smear or melt
on a face (the blood-looking smear across a face in the same review), the
wrong character or identity for the whole take, a spoken line missing or wrong
on a model-voice take (founder decision, 8 Oct 2026: a laid line breaks lip
sync).

:func:`free_fix_lines` is printed wherever the kit suggests a re-film;
:func:`refilm_cause_lines` is printed when ``film`` prices a re-film, naming the
free edit a cause like "drawn twice" or "out of order" points to. Nothing here
blocks: the human still decides, after hearing the free options first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Rule of thumb: a flaw on screen this long or shorter is trimmed (or sped past), never re-filmed.
SHORT_FLAW_SECONDS = 0.5
#: The desk's list of decent rejected takes, kept for reuse later in the series.
REUSE_FILE = Path("shared") / "reuse-takes.jsonl"


@dataclass(frozen=True)
class FreeFix:
    """One free edit that can save a flawed take."""

    flaw: str
    edit: str
    command: str


#: The free edits, in the order to try them. ``{desk}``, ``{episode}``, ``{take}`` and ``{file}`` are filled in.
FREE_FIXES: tuple[FreeFix, ...] = (
    FreeFix(
        "a short mistake mid-take (on screen about 0.5 s or less: a double, a glance, a glitch)",
        "cut it out on the shot change",
        "fictora-produce trim --desk {desk} --take-file {file} --cut A-B",
    ),
    FreeFix(
        "a mistake at the start or the end of the take",
        "move the take's start or end past it (no file is cut)",
        "fictora-produce trim --desk {desk} --episode {episode} --take {take} --start S --end E --preview",
    ),
    FreeFix(
        "a shot that holds the mistake too long",
        "speed up only that shot",
        "fictora-produce tempo --desk {desk} --take-file {file} --factor 1.5 --from S --to S",
    ),
    FreeFix(
        "a jump cut, or a stray element at the edge of the frame",
        "punch in on the shot (before finish)",
        "fictora-produce zoom --desk {desk} --episode {episode} --take {take} --factor 1.2 --center X,Y --from S --to S",
    ),
    FreeFix(
        "takes or episodes in the wrong order",
        "join them in the right order",
        "fictora-produce join --desk {desk} --take-file F2 --take-file F1",
    ),
)

#: Words in a flaw or a re-film cause that no free edit fixes.
_REFILM_ONLY = re.compile(
    r"\b(smear\w*|melt\w*|blood|morph\w*|distort\w* face|face (?:is )?(?:wrong|broken|garbled)|wrong (?:character|"
    r"person|identity|face)|identity|throughout|whole take|every shot|missing line|wrong words|lip ?sync|"
    r"voices? of episodes)\b",
    re.IGNORECASE,
)
#: ``(pattern, the free edit it points to)``: flaws the Gallery Heiress review saved for free.
_FREE_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(twice|double\w*|duplicat\w*|two of|clone\w*)\b", re.IGNORECASE),
        "a person drawn twice for a moment: cut that stretch (`trim --cut`) or speed past it (`tempo --from --to`); "
        "a double only in the edge of the frame: `zoom` away from it",
    ),
    (
        re.compile(r"\b(out of order|wrong order|swapped)\b", re.IGNORECASE),
        "takes in the wrong order: `join --take-file` them in the right order",
    ),
    (
        re.compile(
            r"\b(jump ?cut|jumps|pops?|cut (?:is )?(?:jarring|hard))\b", re.IGNORECASE
        ),
        "a jump cut: `zoom` (punch in) on the shot after it, or `soften` the cut",
    ),
    (
        re.compile(
            r"\b(stray|extra (?:person|man|woman|hand|arm)|in the (?:background|corner|edge))\b",
            re.IGNORECASE,
        ),
        "a stray element at the edge of the frame: `zoom` away from it while it is there",
    ),
    (
        re.compile(
            r"\b(look\w* at (?:him|her|them)self|glance\w*|half a second|brief\w*|split second|"
            r"flash\w*|glitch\w*|too long|slow|lingers?)\b",
            re.IGNORECASE,
        ),
        "a short or slow stretch: cut it (`trim --cut`, about 0.5 s or less is always a cut) or speed it up "
        "(`tempo --from --to`)",
    ),
)


def needs_refilm(flaw: str) -> bool:
    """Whether a flaw is one no free edit can fix (a smear on a face, the wrong person throughout).

    Parameters
    ----------
    flaw
        The flaw or the re-film cause, in plain words.

    Returns
    -------
    bool
        ``True`` when the words name a flaw only a re-film fixes.
    """

    return bool(_REFILM_ONLY.search(flaw or ""))


def free_fix_hints(flaw: str) -> list[str]:
    """The free edits the words of a flaw (or a re-film cause) point to.

    Parameters
    ----------
    flaw
        The flaw or the re-film cause.

    Returns
    -------
    list[str]
        One line per matching free edit; empty when none matches or the flaw
        is one only a re-film fixes (:func:`needs_refilm`).
    """

    if needs_refilm(flaw):
        return []
    return [hint for pattern, hint in _FREE_HINTS if pattern.search(flaw or "")]


def free_fix_lines(
    desk: Path | str = "D",
    episode: int | str = "N",
    take_id: str = "tK",
    *,
    take_file: Path | str | None = None,
    indent: str = "   ",
) -> list[str]:
    """The free-edits-first block printed wherever the kit suggests a re-film.

    Parameters
    ----------
    desk, episode, take_id
        The take (placeholders when unknown).
    take_file
        The file the trim / tempo commands edit; default a placeholder.
    indent
        Prefix of each line.

    Returns
    -------
    list[str]
        The rule of thumb, each free edit with its command, what goes straight
        to a re-film, and the reuse note.
    """

    fill = {
        "desk": desk,
        "episode": episode,
        "take": take_id,
        "file": take_file or "<the finished take>",
    }
    lines = [
        f"{indent}Before a re-film, try the free edits (salvage first; on screen about "
        f"{SHORT_FLAW_SECONDS:g} s or less is a cut, never a re-film):"
    ]
    lines += [
        f"{indent}  - {fix.flaw}: {fix.edit} (`{fix.command.format(**fill)}`)"
        for fix in FREE_FIXES
    ]
    lines.append(
        f"{indent}  Re-film only what no free edit hides: a smear or melt on a face, the wrong person for the "
        "whole take, a spoken line missing or wrong (model voices)."
    )
    lines.append(
        f"{indent}  A decent take you still re-film goes on the reuse list: "
        f'`fictora-produce keep-take --desk {desk} --episode {episode} --take {take_id} --note "..."`'
    )
    return lines


def refilm_cause_lines(cause: str | None) -> list[str]:
    """What ``film`` prints when it prices a re-film: the free edit the cause points to, if any.

    Parameters
    ----------
    cause
        The written re-film cause.

    Returns
    -------
    list[str]
        ``!!`` lines naming the free edit to try first when the cause reads like
        a flaw a free edit fixes; a one-line reminder otherwise; nothing when
        the cause names a flaw only a re-film fixes.
    """

    text = " ".join((cause or "").split())
    if not text or needs_refilm(text):
        return []
    hints = free_fix_hints(text)
    if not hints:
        return [
            "Salvage first: could a free edit save the take (trim, speed up a shot, zoom, reorder)? "
            "`review` lists them; re-film only when none does."
        ]
    return [
        f"!! salvage first: '{text}' reads like a flaw a free edit may fix. Try before paying:",
        *(f"   - {hint}" for hint in hints),
        "   Re-film only if the human has seen the free edit and it does not hold up.",
    ]


def _reuse_path(desk: Path) -> Path:
    return desk / REUSE_FILE


def keep_for_reuse(
    desk: Path,
    *,
    episode: int,
    take_id: str,
    note: str,
    take_file: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Add a decent rejected take to the desk's reuse list (``shared/reuse-takes.jsonl``).

    Parameters
    ----------
    desk
        Series desk.
    episode, take_id
        The take.
    note
        What is good in it and where it could go ("the doorway wide works as a
        flashback in a later episode").
    take_file
        The file kept; default the take's newest raw file, when one is on the desk.
    now
        Time stamp (tests).

    Returns
    -------
    dict[str, Any]
        The entry written.

    Raises
    ------
    ValueError
        When ``note`` is empty.
    FileNotFoundError
        When ``take_file`` is given and missing.
    """

    text = " ".join((note or "").split())
    if not text:
        raise ValueError(
            'keep-take needs --note "what is good in it and where it could go"'
        )
    if take_file is not None:
        take_file = take_file.expanduser().resolve()
        if not take_file.is_file():
            raise FileNotFoundError(f"take not found: {take_file}")
    else:
        takes = desk / f"ep{episode:02d}" / "takes"
        raws = (
            sorted(takes.glob(f"take-ep{episode:02d}-{take_id}-raw-v*.mp4"))
            if takes.is_dir()
            else []
        )
        take_file = raws[-1] if raws else None
    file_text = None
    if take_file is not None:
        try:
            file_text = str(take_file.relative_to(desk.resolve()))
        except ValueError:
            file_text = str(take_file)
    entry = {
        "episode": episode,
        "take": take_id,
        "file": file_text,
        "note": text,
        "kept_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
    }
    path = _reuse_path(desk)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def reuse_entries(desk: Path) -> list[Mapping[str, Any]]:
    """The desk's reuse list, oldest first (empty when there is none).

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    list[Mapping[str, Any]]
        One entry per kept take.
    """

    path = _reuse_path(desk)
    if not path.is_file():
        return []
    entries: list[Mapping[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, Mapping):
            entries.append(loaded)
    return entries


def reuse_lines(desk: Path) -> list[str]:
    """The reuse list as printed by ``keep-take --list`` (and before planning a new episode).

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    list[str]
        A heading and one line per kept take.
    """

    entries = reuse_entries(desk)
    if not entries:
        return ["No takes kept for reuse on this desk yet."]
    lines = [f"Takes kept for reuse ({len(entries)}):"]
    for entry in entries:
        where = f"ep{int(entry.get('episode') or 0):02d} {entry.get('take') or '?'}"
        file_text = f" `{entry['file']}`" if entry.get("file") else ""
        lines.append(f"  - {where}{file_text}: {entry.get('note') or ''}")
    return lines


__all__ = [
    "FREE_FIXES",
    "REUSE_FILE",
    "SHORT_FLAW_SECONDS",
    "FreeFix",
    "free_fix_hints",
    "free_fix_lines",
    "keep_for_reuse",
    "needs_refilm",
    "refilm_cause_lines",
    "reuse_entries",
    "reuse_lines",
]
