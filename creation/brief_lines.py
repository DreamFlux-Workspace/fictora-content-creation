"""The brief's lines against the drafted script: which the writers kept, rewrote, cut or added.

The draft's writers may drop a brief line, move it, or rewrite it. Nothing on
the desk said so, so a creator could approve a script without noticing their
line was gone. :func:`compare_lines` pairs each brief line with a spine line by
speaker and text similarity (deterministic: best pairs first), and
:func:`brief_vs_spine_lines` prints the result after ``step`` drafts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence

from creation.spine_view import episode_id_for

#: At or above this, a line is the brief's line (punctuation and case aside).
KEPT_RATIO = 0.97
#: Same speaker and at least this similar: the writers' rewrite of the brief line.
REWRITE_RATIO = 0.35
#: A different speaker needs this much of the text to count as the same line, given to someone else.
RESPEAKER_RATIO = 0.8

_EMPTY_CELL = {"", "-", "—", "–", "n/a"}


@dataclass(frozen=True)
class ScriptLine:
    """One line: who says it and what, with its id on the spine (``None`` for a brief line)."""

    speaker: str
    text: str
    line_id: str | None = None
    alt_text: str = ""


@dataclass(frozen=True)
class LineMatch:
    """One row of the comparison: ``kept``, ``rewritten``, ``cut`` (brief only) or ``added`` (spine only)."""

    verdict: str
    brief: ScriptLine | None
    spine: ScriptLine | None


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _speaker_name(cell: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*", " ", cell).strip()


def parse_brief_lines(brief: str) -> list[ScriptLine]:
    """Read the lines tables under the brief's ``## Lines`` heading.

    Parameters
    ----------
    brief
        The brief's markdown (the premise sent on the draft).

    Returns
    -------
    list[ScriptLine]
        Each table row with a speaker and an original line, in order. Empty when
        the brief has no lines table (the writers wrote every line).
    """

    lines: list[ScriptLine] = []
    in_lines = False
    header: list[str] | None = None
    for raw in brief.splitlines():
        text = raw.strip()
        if text.startswith("## "):
            in_lines = text[3:].strip().lower().startswith("lines")
            header = None
            continue
        if not in_lines or not text.startswith("|"):
            header = None if not text.startswith("|") else header
            continue
        cells = _cells(text)
        if header is None:
            header = [cell.lower() for cell in cells]
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        row = dict(zip(header, cells, strict=False))
        speaker = _speaker_name(row.get("speaker", ""))
        original = row.get("original") or row.get("line") or row.get("text") or ""
        if speaker and original.strip().lower() not in _EMPTY_CELL:
            lines.append(ScriptLine(speaker=speaker, text=original.strip()))
    return lines


def spine_script_lines(spine: Mapping[str, Any], *, episode: int) -> list[ScriptLine]:
    """An episode's lines on the spine, beat by beat, speaker by name.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[ScriptLine]
        ``text`` is the English script; ``alt_text`` the performed line on a JA/KO show.
    """

    names = {str(c.get("cast_id")): str(c.get("name") or c.get("cast_id")) for c in spine.get("cast") or [] if isinstance(c, Mapping)}
    wanted = episode_id_for(spine, episode)
    beats = [b for b in spine.get("beats") or [] if isinstance(b, Mapping) and b.get("episode_id") == wanted]
    beats.sort(key=lambda beat: int(beat.get("ordinal") or 0))
    out: list[ScriptLine] = []
    for beat in beats:
        for line in beat.get("dialogue_lines") or []:
            if not isinstance(line, Mapping):
                continue
            text = str(line.get("text") or line.get("spoken_text") or "").strip()
            if not text:
                continue
            cast_id = str(line.get("cast_id") or "")
            out.append(
                ScriptLine(
                    speaker=names.get(cast_id, cast_id or "?"),
                    text=text,
                    line_id=str(line.get("line_id") or "") or None,
                    alt_text=str(line.get("spoken_text") or "").strip(),
                )
            )
    return out


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _similar(brief: ScriptLine, spine: ScriptLine) -> float:
    wanted = _norm(brief.text)
    return max(SequenceMatcher(None, wanted, _norm(text)).ratio() for text in (spine.text, spine.alt_text) if text)


def _same_speaker(a: str, b: str) -> bool:
    left, right = _norm(a), _norm(b)
    return bool(left and right) and (left == right or left in right or right in left)


def compare_lines(brief: Sequence[ScriptLine], spine: Sequence[ScriptLine]) -> list[LineMatch]:
    """Pair brief lines with spine lines: best pairs first, each line used once.

    Parameters
    ----------
    brief
        The brief's lines.
    spine
        The drafted lines.

    Returns
    -------
    list[LineMatch]
        Brief lines in brief order (kept, rewritten or cut), then spine lines no
        brief line matched (added).
    """

    candidates: list[tuple[float, int, int, float]] = []
    for i, wanted in enumerate(brief):
        for j, drafted in enumerate(spine):
            ratio = _similar(wanted, drafted)
            same = _same_speaker(wanted.speaker, drafted.speaker)
            if (same and ratio >= REWRITE_RATIO) or ratio >= RESPEAKER_RATIO:
                candidates.append((ratio + (0.5 if same else 0.0) - 0.001 * abs(i - j), i, j, ratio))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    paired: dict[int, tuple[int, float]] = {}
    used: set[int] = set()
    for _, i, j, ratio in candidates:
        if i in paired or j in used:
            continue
        paired[i] = (j, ratio)
        used.add(j)
    out: list[LineMatch] = []
    for i, wanted in enumerate(brief):
        if i not in paired:
            out.append(LineMatch("cut", wanted, None))
            continue
        j, ratio = paired[i]
        drafted = spine[j]
        kept = ratio >= KEPT_RATIO and _same_speaker(wanted.speaker, drafted.speaker)
        out.append(LineMatch("kept" if kept else "rewritten", wanted, drafted))
    out += [LineMatch("added", None, drafted) for j, drafted in enumerate(spine) if j not in used]
    return out


def brief_vs_spine_lines(brief_text: str, spine: Mapping[str, Any], *, episode: int) -> list[str]:
    """Print the brief's lines against the drafted script, for the creator to see right after the draft.

    Parameters
    ----------
    brief_text
        The brief's markdown.
    spine
        The drafted spine.
    episode
        Episode ordinal.

    Returns
    -------
    list[str]
        Printable lines; empty when the brief has no lines table.
    """

    brief = parse_brief_lines(brief_text)
    if not brief:
        return []
    drafted = spine_script_lines(spine, episode=episode)
    matches = compare_lines(brief, drafted)
    count = {verdict: sum(1 for m in matches if m.verdict == verdict) for verdict in ("kept", "rewritten", "cut", "added")}
    out = [
        f"brief lines vs the drafted script: brief {len(brief)} -> script {len(drafted)} "
        f"(kept {count['kept']}, rewritten {count['rewritten']}, cut {count['cut']}, added {count['added']})"
    ]
    for match in matches:
        if match.verdict == "kept" and match.spine:
            out.append(f'  kept       {match.spine.speaker}: "{match.spine.text}"  [{match.spine.line_id}]')
        elif match.verdict == "rewritten" and match.brief and match.spine:
            out.append(f'  rewritten  brief  {match.brief.speaker}: "{match.brief.text}"')
            out.append(f'             script {match.spine.speaker}: "{match.spine.text}"  [{match.spine.line_id}]')
        elif match.verdict == "cut" and match.brief:
            out.append(f'  cut        {match.brief.speaker}: "{match.brief.text}" (no line in the script says this)')
        elif match.spine:
            out.append(f'  added      {match.spine.speaker}: "{match.spine.text}"  [{match.spine.line_id}] (not in the brief)')
    if count["kept"] != len(brief) or count["added"]:
        out.append(
            "  !! The writers changed the brief's lines. Before the script gate: keep theirs, or put yours back with "
            "`fictora-produce line --desk D --episode N --line ID --text \"...\"` (or `--speaker`). The API cannot "
            "add a line back: a cut line needs a new draft, or its words given to a kept line."
        )
    return out


__all__ = [
    "KEPT_RATIO",
    "LineMatch",
    "ScriptLine",
    "brief_vs_spine_lines",
    "compare_lines",
    "parse_brief_lines",
    "spine_script_lines",
]
