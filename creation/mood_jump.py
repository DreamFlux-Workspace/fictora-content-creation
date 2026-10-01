"""Board gate: warn when a character's mood jumps between rows with no cause on screen.

Hanakaze Sweets ep 7 "Not For Sale" (board ep07/boards/board-ep07-t1-v1.png,
2026-10-01): rows 1-2 play Mitsu in full ``comic_anger`` ("I don't sell to my
rival!"); row 3, Ren politely asking to learn, played ``eye_twitch`` - a fixed
polite smile with level brows - and the board drew her calm, glancing sideways
with a poised finger, as if thinking "hmm"; row 4 she shouts again. Nothing in
row 3's beat said what changed her mood: the script meant "thrown mid-scold".

This reads the spine's frame expression fields only (``reaction_kind``, whose
face wears it - ``reaction_cast_id`` or the lone subject - and
``expression_cause``); it never looks at the drawing. It is a **warning**:
the board is not refused, and the fix is a free ``edit --frame`` before the
take is paid for.

* Kinds are grouped into moods (:data:`MOOD_FAMILY`, the same grouping as the
  server's ``expression_carry``). Two kinds of one mood are never a jump.
* A row that opens on a kind of another mood than the one the face last
  played, with no ``expression_cause`` on that row, is a jump.
* A shock (``stunned_blank``, ``double_take`` ...) is a reaction to something
  by its nature: a row that opens on one is not flagged, and the next row is
  compared with the mood before the shock.
* Rage, then calm, then rage again is one bounce, reported once.

The server (fictora-drama, ``expression_carry``) carries the mood into the
next row of the board and the take anyway; this warning is what the
operator sees at the gate. A server older than ``expression_cause`` never
sends it, so every jump there is reported.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Kind to mood. Keep in step with fictora-drama ``expression_carry.MOOD_FAMILY``.
#: ``eye_twitch`` is ``composed``: its look is a polite smile with level brows.
MOOD_FAMILY: dict[str, str] = {
    "comic_anger": "angry",
    "anger_flare": "angry",
    "smiling_rage": "angry",
    "eye_twitch": "composed",
    "smile_goes_cold": "composed",
    "deadpan": "composed",
    "stunned_blank": "shocked",
    "shock": "shocked",
    "slow_surprise": "shocked",
    "double_take": "shocked",
    "freeze": "shocked",
    "flinch": "shocked",
    "dramatic_gasp": "shocked",
    "pause_then_outburst": "shocked",
    "laugh": "happy",
    "big_laugh": "happy",
    "happy": "happy",
    "sparkle_delight": "happy",
    "tear_up": "sad",
    "comic_tears": "sad",
    "gloom": "sad",
    "sweat_drop": "deflated",
    "deflated": "deflated",
    "blush_look_away": "flustered",
    "embarrassed": "flustered",
}
#: A mood that is a reaction to something by nature: never itself a jump.
_INTERRUPT = "shocked"


@dataclass(frozen=True)
class MoodJump:
    """One character's mood changing between two rows with no stated cause.

    Attributes
    ----------
    cast_id
        Whose face.
    from_row, to_row
        The row the mood was last played on, and the row that changes it.
    from_kind, to_kind
        The kinds either side of the jump.
    frame_ordinal
        The ``to_row`` frame that opens on the new kind (what ``edit --frame``
        takes).
    """

    cast_id: str
    from_row: int
    to_row: int
    from_kind: str
    to_kind: str
    frame_ordinal: int


def _brief(frame: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = frame.get("visual_brief")
    return raw if isinstance(raw, Mapping) else {}


def _row(frame: Mapping[str, Any]) -> int:
    raw = frame.get("board_row")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return int(frame.get("ordinal") or 0)


def expression_owner(frame: Mapping[str, Any]) -> str | None:
    """Whose face wears the frame's ``reaction_kind``, when the spine says.

    Parameters
    ----------
    frame
        One storyboard frame.

    Returns
    -------
    str | None
        ``reaction_cast_id``, or the lone subject's cast id; ``None`` when the
        frame has no kind or stages several people without naming the face.
    """

    brief = _brief(frame)
    if not brief.get("reaction_kind"):
        return None
    if brief.get("reaction_cast_id"):
        return str(brief["reaction_cast_id"])
    blocking = [
        item
        for item in brief.get("subject_blocking") or []
        if isinstance(item, Mapping)
    ]
    if len(blocking) == 1 and blocking[0].get("cast_id"):
        return str(blocking[0]["cast_id"])
    return None


def mood_jumps(frames: Sequence[Mapping[str, Any]]) -> list[MoodJump]:
    """Find every row where a face's mood changes with no stated cause.

    Parameters
    ----------
    frames
        One board's frames.

    Returns
    -------
    list[MoodJump]
        In board order; empty when every change has a cause or none happens.
    """

    rows: dict[int, list[Mapping[str, Any]]] = {}
    for frame in sorted(
        frames, key=lambda item: (_row(item), int(item.get("ordinal") or 0))
    ):
        rows.setdefault(_row(frame), []).append(frame)
    #: cast_id -> (row, kind) of the last mood played that was not a shock.
    mood: dict[str, tuple[int, str]] = {}
    jumps: list[MoodJump] = []
    for row, row_frames in sorted(rows.items()):
        owned: dict[str, list[tuple[str, bool, int]]] = {}
        for frame in row_frames:
            owner = expression_owner(frame)
            kind = str(_brief(frame).get("reaction_kind") or "")
            if owner is None or kind not in MOOD_FAMILY:
                continue
            caused = bool(str(_brief(frame).get("expression_cause") or "").strip())
            owned.setdefault(owner, []).append(
                (kind, caused, int(frame.get("ordinal") or 0))
            )
        for cast_id, kinds in owned.items():
            opening, _, ordinal = kinds[0]
            caused = any(given for _, given, _ in kinds)
            before = mood.get(cast_id)
            if (
                before is not None
                and not caused
                and MOOD_FAMILY[opening] != _INTERRUPT
                and MOOD_FAMILY[opening] != MOOD_FAMILY[before[1]]
            ):
                jumps.append(
                    MoodJump(cast_id, before[0], row, before[1], opening, ordinal)
                )
            settled = [kind for kind, _, _ in kinds if MOOD_FAMILY[kind] != _INTERRUPT]
            if settled:
                mood[cast_id] = (row, settled[-1])
    return jumps


def _label(kind: str) -> str:
    return kind.replace("_", " ")


def _fix(jump: MoodJump) -> str:
    return (
        f"If row {jump.to_row} is a reaction on top of the {_label(jump.from_kind)}, make it read that way "
        f'(`edit --frame {jump.frame_ordinal} --set subject_blocking.N.pose="…"` or `--set reaction_kind=…`); '
        f"if the beat really changes the mood, put the cause on screen and name it "
        f'(`edit --frame {jump.frame_ordinal} --set expression_cause="…"`). Edits are free; fix it and '
        "redraw before paying for the take (warning only)."
    )


def mood_jump_lines(
    frames: Sequence[Mapping[str, Any]], *, cast_names: Mapping[str, str]
) -> list[str]:
    """The board gate's ``!!`` lines for mood jumps, rows named.

    Parameters
    ----------
    frames
        One board's frames.
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[str]
        One ``!!`` line per jump, or one per bounce (rage, calm, rage again);
        empty when nothing jumps.
    """

    jumps = mood_jumps(frames)
    out: list[str] = []
    skip: set[int] = set()
    for index, jump in enumerate(jumps):
        if index in skip:
            continue
        who = cast_names.get(jump.cast_id, jump.cast_id)
        back = next(
            (
                position
                for position in range(index + 1, len(jumps))
                if jumps[position].cast_id == jump.cast_id
                and jumps[position].from_row == jump.to_row
                and MOOD_FAMILY[jumps[position].to_kind] == MOOD_FAMILY[jump.from_kind]
            ),
            None,
        )
        if back is not None:
            skip.add(back)
            again = jumps[back]
            out.append(
                f"  !! rows {jump.from_row}→{jump.to_row}→{again.to_row}: {who}'s mood bounces "
                f"{_label(jump.from_kind)} → {_label(jump.to_kind)} → {_label(again.to_kind)} with no cause on "
                f"screen: row {jump.to_row} resets the {MOOD_FAMILY[jump.from_kind]} mood between two rows of it. "
                + _fix(jump)
            )
            continue
        out.append(
            f"  !! rows {jump.from_row}→{jump.to_row}: {who}'s expression jumps from {_label(jump.from_kind)} "
            f"({MOOD_FAMILY[jump.from_kind]}) to {_label(jump.to_kind)} ({MOOD_FAMILY[jump.to_kind]}) with no "
            "cause on screen in the beat. " + _fix(jump)
        )
    return out


__all__ = [
    "MOOD_FAMILY",
    "MoodJump",
    "expression_owner",
    "mood_jump_lines",
    "mood_jumps",
]
