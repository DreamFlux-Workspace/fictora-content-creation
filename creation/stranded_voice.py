"""A voice-only character left with no lines: the guard on line edits and the film preflight.

A character who is only heard (every line off screen, in no frame) has no plate
and no visual brief (fictora-drama #469). When their last line goes, the deployed
server counts them as drawn again and refuses the whole film request with
``422 spine_reuse_invalid: cast.<id>.visual_brief is required``, even for a take
they are not in (Hana inner voice, 2026-09-30). No command and no API field
removes a character, so the kit stops a line edit that would strand one before
anything is sent, and warns before a film when a character is already stranded.

A server fix (unused characters no longer block filming) is on its way in
fictora-drama. The kit cannot tell from the deploy whether it is live, so the
film check warns and the refusal, if it comes, is explained (it charges nothing).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

#: The override flag on ``line`` for a human who truly wants the character left with no lines.
STRAND_FLAG = "--strand-voice"

#: The server's film refusal for a character with no look (``spine_reuse_invalid``).
_REFUSAL = re.compile(
    r"spine_reuse_invalid.*?cast\.([A-Za-z0-9_.-]+?)\.visual_brief is required", re.S
)


def _names(spine: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(card.get("cast_id")): str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    }


def line_counts(
    spine: Mapping[str, Any],
    *,
    removed: Iterable[str] = (),
    speakers: Mapping[str, str] | None = None,
    added: Iterable[str] = (),
) -> dict[str, int]:
    """How many lines each cast member has on the whole story, after an edit.

    Parameters
    ----------
    spine
        Spine JSON before the edit.
    removed
        Line ids the edit drops.
    speakers
        ``line_id`` to its new speaker's cast id, for lines the edit gives to someone else.
    added
        Cast ids of the lines the edit adds (one entry per line).

    Returns
    -------
    dict[str, int]
        ``cast_id`` to its line count (every episode).
    """

    gone = set(removed)
    moved = dict(speakers or {})
    counts: dict[str, int] = {}
    for beat in spine.get("beats") or []:
        if not isinstance(beat, Mapping):
            continue
        for line in beat.get("dialogue_lines") or []:
            if not isinstance(line, Mapping) or str(line.get("line_id")) in gone:
                continue
            who = moved.get(str(line.get("line_id")), str(line.get("cast_id") or ""))
            if who:
                counts[who] = counts.get(who, 0) + 1
    for who in added:
        counts[who] = counts.get(who, 0) + 1
    return counts


def framed_cast(spine: Mapping[str, Any]) -> set[str]:
    """Every cast id any frame names (``cast_refs`` or ``subject_blocking``), on or off frame."""

    named: set[str] = set()
    for frame in spine.get("frames") or []:
        if not isinstance(frame, Mapping):
            continue
        named.update(str(ref) for ref in frame.get("cast_refs") or [] if ref)
        brief = frame.get("visual_brief")
        blocking = brief.get("subject_blocking") if isinstance(brief, Mapping) else None
        for entry in blocking or []:
            if isinstance(entry, Mapping) and entry.get("cast_id"):
                named.add(str(entry["cast_id"]))
    return named


def _no_look(card: Mapping[str, Any]) -> bool:
    return not card.get("visual_brief")


def is_heard_only(card: Mapping[str, Any], framed: set[str]) -> bool:
    """A character who is only heard: the server's ``voice_only`` flag, or no look and in no frame (older server)."""

    if card.get("voice_only") is True:
        return True
    return _no_look(card) and str(card.get("cast_id")) not in framed


def voices_left_without_lines(
    spine: Mapping[str, Any],
    *,
    removed: Iterable[str] = (),
    speakers: Mapping[str, str] | None = None,
    added: Iterable[str] = (),
) -> list[tuple[str, str]]:
    """The heard-only characters an edit would leave with no lines at all.

    Parameters
    ----------
    spine
        Spine JSON before the edit.
    removed, speakers, added
        As :func:`line_counts`.

    Returns
    -------
    list[tuple[str, str]]
        ``(cast_id, name)`` for each one who has lines now and none after.
    """

    before = line_counts(spine)
    after = line_counts(spine, removed=removed, speakers=speakers, added=added)
    framed = framed_cast(spine)
    return [
        (str(card["cast_id"]), str(card.get("name") or card["cast_id"]))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping)
        and card.get("cast_id")
        and before.get(str(card["cast_id"]), 0) > 0
        and after.get(str(card["cast_id"]), 0) == 0
        and is_heard_only(card, framed)
    ]


def strand_refusal(
    stranded: list[tuple[str, str]], *, desk: Any, episode: int, what: str
) -> str:
    """The plain-words stop for a line edit that would leave a heard-only character with no lines.

    Parameters
    ----------
    stranded
        :func:`voices_left_without_lines`.
    desk
        Series desk (for the commands to paste).
    episode
        Episode ordinal.
    what
        The edit in words (``removing line_episode_01_03``).

    Returns
    -------
    str
        What would happen and the three ways on.
    """

    who = ", ".join(f"{name} ({cast_id})" for cast_id, name in stranded)
    name = stranded[0][1]
    return (
        f"nothing was sent: {what} leaves {who} with no lines. They are only heard, so they have no look; "
        "with no lines they stay in the cast with no look (no command removes a character), and the server "
        "may then refuse to film the whole episode (`422 spine_reuse_invalid: cast.<id>.visual_brief is "
        "required`), even a take they are not in. Pick one:\n"
        "  - keep the line (drop --remove, or leave the speaker as it is);\n"
        f"  - give them another line in the same command: `line --desk {desk} --episode {episode} --remove ... "
        f'--add --beat N --speaker "{name}" --text "..." --off-screen`;\n'
        "  - if it was a character's own thought, put it on that character with "
        f'`inner-voice --desk {desk} --episode {episode} --cast NAME --text "..." --at S` '
        f"(no cast place); {name} still stays in the cast, so removing their line still needs {STRAND_FLAG}.\n"
        f"  To do it anyway: add {STRAND_FLAG}. A server fix so unused characters no longer block filming is on "
        "the way; until it is live, filming may be refused."
    )


def strand_override_note(stranded: list[tuple[str, str]]) -> str:
    """The warning printed (and noted) when ``--strand-voice`` lets the edit through."""

    who = ", ".join(f"{name} ({cast_id})" for cast_id, name in stranded)
    return (
        f"!! {STRAND_FLAG}: {who} now has no lines and no look. Until the server fix is live, filming may be "
        "refused (`spine_reuse_invalid … visual_brief is required`); `film` and `step` warn before any spend."
    )


def stranded_cast(spine: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Cast members with no visual brief, no lines anywhere on the story, and in no frame.

    Parameters
    ----------
    spine
        Spine JSON.

    Returns
    -------
    list[tuple[str, str]]
        ``(cast_id, name)`` in cast order.
    """

    counts = line_counts(spine)
    framed = framed_cast(spine)
    return [
        (str(card["cast_id"]), str(card.get("name") or card["cast_id"]))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping)
        and card.get("cast_id")
        and _no_look(card)
        and counts.get(str(card["cast_id"]), 0) == 0
        and str(card["cast_id"]) not in framed
    ]


def stranded_preflight(
    spine: Mapping[str, Any], *, unit: str, desk: Any, episode: int
) -> list[str]:
    """The film preflight lines for a stranded character; empty when there is none.

    The kit cannot tell whether the deployed server has the fix, so this warns
    and never blocks; a refusal that follows charges nothing (:func:`explain_film_refusal`).

    Parameters
    ----------
    spine
        Spine JSON as it is now.
    unit
        ``ep01`` or ``ep01-t2``: what is about to be filmed.
    desk
        Series desk (for the command to paste).
    episode
        Episode ordinal.

    Returns
    -------
    list[str]
        Printable lines in the preflight banner's shape.
    """

    found = stranded_cast(spine)
    if not found:
        return []
    lines = [f"!! PREFLIGHT WARNING — {unit} — this is not blocked, but read it"]
    for cast_id, name in found:
        lines.append(
            f"!! stranded_voice: {name} ({cast_id}) is in the cast with no lines, no look (no visual brief) and in "
            "no frame. The deployed server may count them as drawn and refuse the whole film request "
            f"(`422 spine_reuse_invalid: cast.{cast_id}.visual_brief is required`), even for a take they are not "
            "in; that refusal charges nothing."
        )
    name = found[0][1]
    lines.append(
        f'!! Before filming, give them a line back: `line --desk {desk} --episode {episode} --add --beat N --speaker "{name}" '
        '--text "..." --off-screen`. No command removes a character; a server fix so unused characters no '
        "longer block filming is on the way."
    )
    return lines


def explain_film_refusal(
    message: str, spine: Mapping[str, Any] | None = None
) -> str | None:
    """Plain words for the server's ``spine_reuse_invalid … visual_brief is required`` film refusal.

    Parameters
    ----------
    message
        The error text the kit got.
    spine
        Spine JSON (for the character's name), when at hand.

    Returns
    -------
    str | None
        The explanation, or ``None`` when the message is some other refusal.
    """

    found = _REFUSAL.search(message or "")
    if not found:
        return None
    cast_id = found.group(1)
    name = _names(spine or {}).get(cast_id, cast_id)
    return (
        f"The server refused to film (nothing was filmed or charged): {name} ({cast_id}) has no look (visual "
        "brief). This happens to a character who was only heard and lost their last line: the server then "
        "counts them as drawn and needs a look for the whole request, even a take they are not in. Fix: give "
        f'{name} a line back (`line --add --beat N --speaker "{name}" --text "..." --off-screen`), then film '
        "again. A server fix so unused characters no longer block filming is on the way; do not work around it "
        "with raw HTTP."
    )


__all__ = [
    "STRAND_FLAG",
    "explain_film_refusal",
    "framed_cast",
    "is_heard_only",
    "line_counts",
    "strand_override_note",
    "strand_refusal",
    "stranded_cast",
    "stranded_preflight",
    "voices_left_without_lines",
]
