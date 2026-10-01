"""The brief's lines against the drafted script: which the writers kept, rewrote, cut or added.

The draft's writers may drop a brief line, move it, or rewrite it. Nothing on
the desk said so, so a creator could approve a script without noticing their
line was gone. :func:`compare_lines` pairs each brief line with a spine line by
speaker and text similarity (deterministic: best pairs first), and
:func:`brief_vs_spine_lines` prints the result after ``step`` drafts.

Locked lines (founder decision, 1 Oct 2026). On *Fated in the Rain* the brief
said "keep these lines exactly"; the draft kept the script text, then the
localization pass rewrote all five performed lines and their subtitles, and this
report said "kept 5, rewritten 0" because it compared the script text only. It
now also compares what is *said* and what is *shown*:

* the spoken line - ``spoken_text`` on a Japanese or Korean show, the script on
  an English one - against the brief's original. A locked line's spoken words
  are the creator's; the server pins them, so a changed one is flagged loudly as
  a bug, with the command that puts it back;
* the subtitle - ``subtitle_text`` against the brief's translation (or the
  server's ``brief_subtitle_text``). A subtitle may adapt; every adaptation is
  listed so the creator sees it.
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

#: The server's lock phrases (``locked_lines._LOCK_PHRASE`` in fictora-drama).
_LOCK_PHRASE = re.compile(
    r"\bkeep\b[^.\n]{0,40}\bexactly\b|\bexactly as written\b|\bverbatim\b|\bword[- ]for[- ]word\b"
    r"|\blocked lines?\b",
    re.IGNORECASE,
)
#: Hangul, kana and CJK ideographs: a line written in the language a Korean or Japanese show is performed in.
_PERFORMED_SCRIPT = re.compile(r"[가-힣ぁ-ゟ゠-ヿ一-鿿]")


@dataclass(frozen=True)
class ScriptLine:
    """One line: who says it and what, with its id on the spine (``None`` for a brief line)."""

    speaker: str
    text: str
    line_id: str | None = None
    alt_text: str = ""
    #: Brief line: its ``Translation`` cell. Spine line: ``subtitle_text``.
    subtitle: str = ""
    #: Spine line: the brief's own subtitle the server kept (``brief_subtitle_text``).
    brief_subtitle: str = ""


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
        translation = (
            row.get("translation") or row.get("english") or row.get("subtitle") or ""
        ).strip()
        if translation.lower() in _EMPTY_CELL:
            translation = ""
        if speaker and original.strip().lower() not in _EMPTY_CELL:
            lines.append(
                ScriptLine(speaker=speaker, text=original.strip(), subtitle=translation)
            )
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

    names = {
        str(c.get("cast_id")): str(c.get("name") or c.get("cast_id"))
        for c in spine.get("cast") or []
        if isinstance(c, Mapping)
    }
    wanted = episode_id_for(spine, episode)
    beats = [
        b
        for b in spine.get("beats") or []
        if isinstance(b, Mapping) and b.get("episode_id") == wanted
    ]
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
                    subtitle=str(line.get("subtitle_text") or "").strip(),
                    brief_subtitle=str(line.get("brief_subtitle_text") or "").strip(),
                )
            )
    return out


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _similar(brief: ScriptLine, spine: ScriptLine) -> float:
    wanted = _norm(brief.text)
    return max(
        SequenceMatcher(None, wanted, _norm(text)).ratio()
        for text in (spine.text, spine.alt_text)
        if text
    )


def _same_speaker(a: str, b: str) -> bool:
    left, right = _norm(a), _norm(b)
    return bool(left and right) and (left == right or left in right or right in left)


def compare_lines(
    brief: Sequence[ScriptLine], spine: Sequence[ScriptLine]
) -> list[LineMatch]:
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
                candidates.append(
                    (ratio + (0.5 if same else 0.0) - 0.001 * abs(i - j), i, j, ratio)
                )
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
    out += [
        LineMatch("added", None, drafted)
        for j, drafted in enumerate(spine)
        if j not in used
    ]
    return out


def brief_locks_lines(brief: str) -> bool:
    """Whether the brief's ``## Lines`` section asks for its lines word for word.

    Parameters
    ----------
    brief
        The brief's markdown.

    Returns
    -------
    bool
        True when a lock phrase ("keep these lines exactly", "verbatim", "word
        for word", "locked lines") sits in the Lines section.
    """

    in_lines = False
    for raw in brief.splitlines():
        text = raw.strip()
        if text.startswith("## "):
            in_lines = text[3:].strip().lower().startswith("lines")
        if in_lines and _LOCK_PHRASE.search(text):
            return True
    return False


def spoken_and_subtitle_lines(
    matches: Sequence[LineMatch], *, locked: bool, desk: Any = "D", episode: int = 1
) -> tuple[list[str], dict[str, int]]:
    """Compare what is said and what is shown with the brief, line by line.

    Parameters
    ----------
    matches
        From :func:`compare_lines`.
    locked
        Whether the brief locks its lines (:func:`brief_locks_lines`).
    desk, episode
        For the command that puts a changed spoken line back.

    Returns
    -------
    tuple[list[str], dict[str, int]]
        Printable lines, and counts: ``locked`` (spoken exactly as the brief),
        ``changed`` (spoken differs: a bug on a locked brief) and ``adapted``
        (subtitle differs from the brief's).
    """

    out: list[str] = []
    count = {"locked": 0, "changed": 0, "adapted": 0}
    for match in matches:
        brief, drafted = match.brief, match.spine
        if brief is None or drafted is None:
            continue
        # The spoken line: spoken_text on a localized show, the script otherwise.
        # A brief written in English for a localized show is performed by the
        # localizer, so only an original in the show's script is compared.
        if drafted.alt_text and _PERFORMED_SCRIPT.search(brief.text):
            said = drafted.alt_text
        elif not drafted.alt_text:
            said = drafted.text
        else:
            said = ""
        if said and match.verdict == "kept" and _norm(said) == _norm(brief.text):
            count["locked"] += 1
        elif said and match.verdict == "kept":
            count["changed"] += 1
            flag = "!! BUG (the server pins a locked line)" if locked else "!!"
            out.append(
                f'  {flag}: the spoken line changed  brief "{brief.text}"  performed "{said}"  '
                f"[{drafted.line_id}]. Put it back: `fictora-produce line --desk {desk} --episode {episode} "
                f'--line {drafted.line_id} --spoken "{brief.text}"`'
            )
        wanted = drafted.brief_subtitle or brief.subtitle
        if wanted and drafted.subtitle and _norm(wanted) != _norm(drafted.subtitle):
            count["adapted"] += 1
            out.append(
                f'  subtitle adapted  [{drafted.line_id}]  brief "{wanted}"  ->  shown "{drafted.subtitle}"'
            )
    return out, count


def brief_vs_spine_lines(
    brief_text: str, spine: Mapping[str, Any], *, episode: int
) -> list[str]:
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
    count = {
        verdict: sum(1 for m in matches if m.verdict == verdict)
        for verdict in ("kept", "rewritten", "cut", "added")
    }
    out = [
        f"brief lines vs the drafted script: brief {len(brief)} -> script {len(drafted)} "
        f"(kept {count['kept']}, rewritten {count['rewritten']}, cut {count['cut']}, added {count['added']})"
    ]
    locked = brief_locks_lines(brief_text)
    said, said_count = spoken_and_subtitle_lines(
        matches, locked=locked, episode=episode
    )
    out.append(
        f"  spoken: {said_count['locked']} {'locked' if locked else 'as written'}"
        f"{', ' + str(said_count['changed']) + ' CHANGED' if said_count['changed'] else ''}; "
        f"subtitles: {said_count['adapted']} adapted"
    )
    for match in matches:
        if match.verdict == "kept" and match.spine:
            out.append(
                f'  kept       {match.spine.speaker}: "{match.spine.text}"  [{match.spine.line_id}]'
            )
        elif match.verdict == "rewritten" and match.brief and match.spine:
            out.append(
                f'  rewritten  brief  {match.brief.speaker}: "{match.brief.text}"'
            )
            out.append(
                f'             script {match.spine.speaker}: "{match.spine.text}"  [{match.spine.line_id}]'
            )
        elif match.verdict == "cut" and match.brief:
            out.append(
                f'  cut        {match.brief.speaker}: "{match.brief.text}" (no line in the script says this)'
            )
        elif match.spine:
            out.append(
                f'  added      {match.spine.speaker}: "{match.spine.text}"  [{match.spine.line_id}] (not in the brief)'
            )
    out += said
    if count["kept"] != len(brief) or count["added"]:
        out.append(
            "  !! The writers changed the brief's lines. Before the script gate: keep theirs, or put yours back with "
            '`fictora-produce line --desk D --episode N --line ID --text "..."` (or `--speaker`). Put a cut line '
            'back with `line --add --beat B --speaker NAME --text "..."` (a voice not in the cast: `--new-voice`); '
            "drop an added one with `line --remove ID`."
        )
    return out


__all__ = [
    "KEPT_RATIO",
    "LineMatch",
    "ScriptLine",
    "brief_locks_lines",
    "brief_vs_spine_lines",
    "spoken_and_subtitle_lines",
    "compare_lines",
    "parse_brief_lines",
    "spine_script_lines",
]
