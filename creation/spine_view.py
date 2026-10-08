"""Read a story spine the way the desk needs it: episodes by ordinal, lines per take, the board's shot list.

Pure functions over ``GET /v1/spines/{id}`` JSON; nothing here calls the API.

Episodes are found by ordinal in ``spine.episode_summaries``: episode 1 came
back ``episode_01`` and episode 2 ``ep_02`` in the same story, so no id is built
from the ordinal while the spine can say it.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Collection, Mapping, Sequence

from creation.crowd_lines import beat_rows
from creation.mood_jump import mood_jump_lines
from creation.ops.state import SpokenLine
from creation.shot_plan import plan_lines

_SET_SUFFIX = re.compile(r"_set(\d+)$")


def episode_api_id(ordinal: int) -> str:
    """Return ``episode_NN``: the fallback id when a spine lists no summary for the ordinal."""

    return f"episode_{ordinal:02d}"


def _summary_ordinal(summary: Mapping[str, Any]) -> int | None:
    for key in ("ordinal", "episode_ordinal"):
        value = summary.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def episode_id_for(spine: Mapping[str, Any], ordinal: int) -> str:
    """Return an episode's API id, looked up by ordinal in ``spine.episode_summaries``.

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` JSON.
    ordinal
        Episode ordinal (1-based).

    Returns
    -------
    str
        The summary's ``episode_id``; ``episode_NN`` when the spine lists none for it.
    """

    for summary in spine.get("episode_summaries") or []:
        if (
            isinstance(summary, Mapping)
            and summary.get("episode_id")
            and _summary_ordinal(summary) == ordinal
        ):
            return str(summary["episode_id"])
    return episode_api_id(ordinal)


def episode_summary(spine: Mapping[str, Any], ordinal: int) -> Mapping[str, Any]:
    """Return an episode's summary entry (empty when the spine has none).

    Parameters
    ----------
    spine
        Spine JSON.
    ordinal
        Episode ordinal.

    Returns
    -------
    Mapping[str, Any]
        ``episode_id``, ``title``, ``summary``, ``authoring_state`` ...
    """

    wanted = episode_id_for(spine, ordinal)
    for summary in spine.get("episode_summaries") or []:
        if isinstance(summary, Mapping) and summary.get("episode_id") == wanted:
            return summary
    return {}


def storyboard_set_for_beat(beat_ordinal: int, pattern: Sequence[int]) -> int:
    """Map a beat ordinal to its storyboard set (take) through the spine's ``beats_per_storyboard_set``.

    Parameters
    ----------
    beat_ordinal
        1-based beat ordinal within the episode.
    pattern
        Beats per set, in order.

    Returns
    -------
    int
        1-based set index.

    Raises
    ------
    ValueError
        When the ordinal falls outside the pattern.
    """

    cursor = 1
    for set_index, count in enumerate(pattern, start=1):
        if beat_ordinal < cursor + count:
            if beat_ordinal < 1:
                break
            return set_index
        cursor += count
    raise ValueError(
        f"beat {beat_ordinal} is outside the storyboard pattern {tuple(pattern)}"
    )


def beats_by_take(
    spine: Mapping[str, Any], *, episode: int, take_count: int
) -> list[list[dict[str, Any]]]:
    """Group an episode's beats into its takes (storyboard sets), in order.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    take_count
        Takes on the desk for this episode (the band's count).

    Returns
    -------
    list[list[dict[str, Any]]]
        ``take_count`` lists of beats; a beat past the last set goes to the last take.
    """

    wanted = episode_id_for(spine, episode)
    beats = [
        b
        for b in spine.get("beats") or []
        if isinstance(b, dict) and b.get("episode_id") == wanted
    ]
    beats.sort(key=lambda beat: int(beat.get("ordinal") or 0))
    pattern = tuple(int(n) for n in spine.get("beats_per_storyboard_set") or ())
    grouped: list[list[dict[str, Any]]] = [[] for _ in range(max(1, take_count))]
    for position, beat in enumerate(beats, start=1):
        ordinal = int(beat.get("ordinal") or position)
        if pattern:
            try:
                index = storyboard_set_for_beat(ordinal, pattern)
            except ValueError:
                index = len(grouped)
        else:
            index = (position - 1) * len(grouped) // max(1, len(beats)) + 1
        grouped[min(index, len(grouped)) - 1].append(beat)
    return grouped


def spoken_lines(spine: Mapping[str, Any], beat: Mapping[str, Any]) -> list[SpokenLine]:
    """Return a beat's lines as the take speaks them.

    ``spoken_text`` (the performed line on a Japanese or Korean show) wins over
    ``text``; the subtitle is the translation when the two differ.

    Parameters
    ----------
    spine
        Spine JSON (for cast names).
    beat
        One beat.

    Returns
    -------
    list[SpokenLine]
        The beat's lines, speaker by name.
    """

    names = {
        c.get("cast_id"): c.get("name")
        for c in spine.get("cast") or []
        if isinstance(c, dict)
    }
    lines: list[SpokenLine] = []
    for raw in beat.get("dialogue_lines") or []:
        if not isinstance(raw, dict):
            continue
        spoken = str(raw.get("spoken_text") or raw.get("text") or "").strip()
        if not spoken:
            continue
        speaker = str(
            names.get(raw.get("cast_id"))
            or raw.get("speaker")
            or raw.get("cast_id")
            or "?"
        )
        gloss = str(
            raw.get("subtitle_text") or raw.get("spoken_back_gloss") or ""
        ).strip()
        lines.append(
            SpokenLine(
                speaker=speaker,
                original=spoken,
                translation=gloss if gloss != spoken else "",
            )
        )
    return lines


def dialogue_line_ids(
    spine: Mapping[str, Any], *, episode: int, take_index: int, take_count: int
) -> list[tuple[str, str]]:
    """Return ``(line_id, performed text)`` for one take's approved lines, in order.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    take_index
        1-based take index.
    take_count
        Takes on the episode.

    Returns
    -------
    list[tuple[str, str]]
        Line ids with their text (text is shown to the operator only, never sent anywhere).
    """

    grouped = beats_by_take(spine, episode=episode, take_count=take_count)
    if not 1 <= take_index <= len(grouped):
        return []
    found: list[tuple[str, str]] = []
    for beat in grouped[take_index - 1]:
        for raw in beat.get("dialogue_lines") or []:
            if isinstance(raw, dict) and raw.get("line_id"):
                text = str(raw.get("spoken_text") or raw.get("text") or "").strip()
                if text:
                    found.append((str(raw["line_id"]), text))
    return found


def script_lines(
    spine: Mapping[str, Any], *, episode: int, take_ids: Sequence[str]
) -> list[str]:
    """The script gate: per take, each beat's shot (``motion_intent``) and its lines.

    A beat's background shouts (``crowd_lines``) print in the order they play,
    marked ``[background]``; they are not characters and never count toward a
    take's three lines.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    take_ids
        The episode's take ids.

    Returns
    -------
    list[str]
        Printable lines.
    """

    out: list[str] = []
    grouped = beats_by_take(spine, episode=episode, take_count=len(take_ids))
    for take_id, beats in zip(take_ids, grouped, strict=True):
        out.append(f"ep{episode:02d} {take_id}")
        for beat in beats:
            out.append(
                f"  shot {beat.get('ordinal')}: {beat.get('motion_intent') or '(no shot written)'}"
            )
            out += plan_lines(beat.get("shot_plan"), indent="    ")
            if beat.get("reaction_kind"):
                out.append(f"    expression: {beat['reaction_kind']}")
            character = [
                f"{line.speaker}: {line.original}"
                + (f"  ({line.translation})" if line.translation else "")
                for line in spoken_lines(spine, beat)
            ]
            # Background shouts play beside the beat's line; never characters, never counted below.
            out += [f"    {row}" for row in beat_rows(beat, character)]
        count = sum(len(spoken_lines(spine, beat)) for beat in beats)
        if count > 3:
            out.append(
                f"  !! {take_id} has {count} lines; a 15 s take holds three. Say which to cut"
            )
    return out


# --- Boards ------------------------------------------------------------------------------------


def board_assets(spine: Mapping[str, Any], *, episode: int) -> list[tuple[int, str]]:
    """Return ``(set_index, url)`` for an episode's current boards.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[tuple[int, str]]
        ``media_assets`` with ``relation_type == "episode"`` for this episode, by
        ``_setNN`` on the asset id; stale ones left out.
    """

    wanted = episode_id_for(spine, episode)
    boards: list[tuple[int, str]] = []
    for asset in spine.get("media_assets") or []:
        if not isinstance(asset, Mapping):
            continue
        if (
            asset.get("relation_type") != "episode"
            or asset.get("relation_id") != wanted
        ):
            continue
        if asset.get("stale") or not asset.get("url"):
            continue
        match = _SET_SUFFIX.search(str(asset.get("asset_id") or ""))
        boards.append(
            (int(match.group(1)) if match else len(boards) + 1, str(asset["url"]))
        )
    return sorted(boards)


def frames_by_set(
    spine: Mapping[str, Any], *, episode: int
) -> dict[int, list[dict[str, Any]]]:
    """Group an episode's storyboard frames by board (set), in frame order.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    dict[int, list[dict[str, Any]]]
        Frames by set (``_setNN`` on ``storyboard_group_id``; set 1 when absent).
    """

    wanted = episode_id_for(spine, episode)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for frame in spine.get("frames") or []:
        if not isinstance(frame, dict) or frame.get("episode_id") != wanted:
            continue
        match = _SET_SUFFIX.search(str(frame.get("storyboard_group_id") or ""))
        grouped.setdefault(int(match.group(1)) if match else 1, []).append(frame)
    for frames in grouped.values():
        frames.sort(key=lambda frame: int(frame.get("ordinal") or 0))
    return grouped


def frames_digest(frames: Sequence[Mapping[str, Any]]) -> str:
    """Digest of what a board draws: each frame's id and ``visual_brief``.

    Parameters
    ----------
    frames
        One set's frames.

    Returns
    -------
    str
        Short sha256 hex digest.
    """

    payload = [(frame.get("frame_id"), frame.get("visual_brief")) for frame in frames]
    return _digest(payload)


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


#: Line fields that are words only: changing them does not change what a board draws.
_WORDS_ONLY_LINE_KEYS = frozenset({"text", "spoken_text", "subtitle_text"})

#: What a board's drawing is made from, as :func:`board_inputs` names them (the order they are reported in).
BOARD_INPUT_NAMES: dict[str, str] = {
    "frames": "the frame briefs",
    "beats": "the take's beats (intent, direction, shot plan, who is in them)",
    "look": "the look notes",
    "plates": "the cast plates",
}


def board_inputs(
    spine: Mapping[str, Any], *, episode: int, set_index: int, take_count: int
) -> dict[str, str]:
    """Digest each thing one board is drawn from, to tell a redraw that changes something from a re-roll.

    A redraw draws the take's frames again (the server re-authors them first
    after a beat edit), in the story's look, with the cast's plates. When none of
    these changed since the board was drawn, a redraw draws the same direction
    again: a paid re-roll.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    set_index
        The board (take) number, 1-based.
    take_count
        Takes on the desk for this episode (:func:`beats_by_take`).

    Returns
    -------
    dict[str, str]
        One short digest per key of :data:`BOARD_INPUT_NAMES`.
    """

    frames = frames_by_set(spine, episode=episode).get(set_index, [])
    takes = beats_by_take(spine, episode=episode, take_count=max(take_count, set_index))
    beats = []
    for beat in takes[set_index - 1]:
        drawn = {key: value for key, value in beat.items() if key != "dialogue_lines"}
        drawn["dialogue_lines"] = [
            {k: v for k, v in line.items() if k not in _WORDS_ONLY_LINE_KEYS}
            for line in beat.get("dialogue_lines") or []
            if isinstance(line, Mapping)
        ]
        beats.append(drawn)
    look = [
        (note.get("note_id"), note.get("text"))
        for note in spine.get("look_notes") or []
        if isinstance(note, Mapping)
    ]
    plates = sorted(
        (str(asset.get("relation_id")), str(asset.get("url")))
        for asset in spine.get("media_assets") or []
        if isinstance(asset, Mapping)
        and asset.get("relation_type") == "cast_card"
        and not asset.get("stale")
    )
    return {
        "frames": frames_digest(frames),
        "beats": _digest(beats),
        "look": _digest(look),
        "plates": _digest(plates),
    }


@dataclass(frozen=True)
class ShotRow:
    """One board row as the frames author wrote it (``frames[].visual_brief``)."""

    row: int
    shot_scale: str
    camera_angle: str
    viewpoint: str
    camera_move: str
    cell_roles: tuple[str, ...]
    reaction_kinds: tuple[str, ...]

    def one_line(self) -> str:
        """Return ``row N: size · angle · viewpoint · camera move · cells · reaction``."""

        reaction = ", ".join(self.reaction_kinds) or "none"
        return (
            f"row {self.row}: {self.shot_scale} · {self.camera_angle} · {self.viewpoint} · "
            f"camera {self.camera_move} · cells {', '.join(self.cell_roles) or '?'} · reaction {reaction}"
        )


def _row_number(frame: Mapping[str, Any]) -> int:
    raw = frame.get("board_row")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return int(frame.get("ordinal") or 0)


def _first(briefs: Sequence[Mapping[str, Any]], key: str) -> str:
    return next((str(brief[key]) for brief in briefs if brief.get(key)), "?")


def shot_rows(frames: Sequence[Mapping[str, Any]]) -> list[ShotRow]:
    """Collapse one board's frames into its rows, top to bottom.

    Parameters
    ----------
    frames
        One set's frames.

    Returns
    -------
    list[ShotRow]
        One entry per ``board_row``.
    """

    rows: dict[int, list[Mapping[str, Any]]] = {}
    for frame in frames:
        rows.setdefault(_row_number(frame), []).append(frame)
    out: list[ShotRow] = []
    for row, cells in sorted(rows.items()):
        briefs = [
            cell["visual_brief"]
            if isinstance(cell.get("visual_brief"), Mapping)
            else {}
            for cell in cells
        ]
        moves = [
            str(brief["row_direction"]["camera_move"])
            for brief in briefs
            if isinstance(brief.get("row_direction"), Mapping)
            and brief["row_direction"].get("camera_move")
        ]
        out.append(
            ShotRow(
                row=row,
                shot_scale=_first(briefs, "shot_scale"),
                camera_angle=_first(briefs, "camera_angle"),
                viewpoint=_first(briefs, "viewpoint"),
                camera_move=moves[0] if moves else "?",
                cell_roles=tuple(
                    str(brief.get("cell_role") or "?") for brief in briefs
                ),
                reaction_kinds=tuple(
                    str(brief["reaction_kind"])
                    for brief in briefs
                    if brief.get("reaction_kind")
                ),
            )
        )
    return out


#: A placement phrase that puts a face or prop in a platform-covered zone, checked clause by clause.
_COVERED_PLACEMENT: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bottom band",
        re.compile(
            r"\b(bottom|lower edge|foot of the frame|lower[- ]left corner|lower fifth)\b",
            re.I,
        ),
    ),
    (
        "top strip",
        re.compile(
            r"\b(top edge|upper edge|very top|top of (the )?frame|top[- ](left|right) corner)\b",
            re.I,
        ),
    ),
    (
        "right rail",
        re.compile(r"\b(right edge|far right|lower[- ]right|right margin)\b", re.I),
    ),
)
#: The same, anchored on the frame, for text that is not purely a position (props, the zone note).
_COVERED_FRAME_PLACEMENT: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bottom band",
        re.compile(
            r"\b(bottom (edge|of (the )?frame)|frame bottom|lower edge|lower[- ]left corner)\b",
            re.I,
        ),
    ),
    (
        "top strip",
        re.compile(
            r"\b(top edge|very top|top of (the )?frame|top[- ](left|right) corner)\b",
            re.I,
        ),
    ),
    (
        "right rail",
        re.compile(r"\b(right edge|far right|lower[- ]right corner)\b", re.I),
    ),
)
#: A clause that keeps something OUT of a zone ("bottom fifth clear of faces") is not a placement.
_CLEARS_ZONE = re.compile(
    r"\b(clear|away from|above|out of|avoid|avoids|keep|keeps|never|not|no)\b", re.I
)
_ZONE_WHAT = {
    "bottom band": "the bottom 20% (post caption, username, music)",
    "top strip": "the top 8% (tabs, search, camera)",
    "right rail": "the right 12% of the lower two thirds (like, comment, share)",
}
SAFE_ZONE_BOARD_CHECK = (
    "safe zones: TikTok, Reels and Shorts cover the top 8%, the bottom 20% and the right 12% of the lower two "
    "thirds of every cell. Not measured on the board image (the kit has no face detector): look at the board "
    "and check no face, eyes, mouth or key prop sits there. Written placements are listed to look at, never "
    "flagged; `review` measures the finished take's captions."
)


def covered_placement(text: str, *, frame_anchored: bool = False) -> str | None:
    """Return the covered zone a placement phrase puts something in, or ``None``.

    Parameters
    ----------
    text
        A ``frame_position``, a story object or a ``ui_safe_zone`` note.
    frame_anchored
        Only match phrases that name the frame (for text that is not purely a position).

    Returns
    -------
    str | None
        ``bottom band``, ``top strip`` or ``right rail``; ``None`` when nothing is placed in one.
    """

    patterns = _COVERED_FRAME_PLACEMENT if frame_anchored else _COVERED_PLACEMENT
    for clause in re.split(r"[;,.]", text):
        if _CLEARS_ZONE.search(clause):
            continue
        for zone, pattern in patterns:
            if pattern.search(clause):
                return zone
    return None


def safe_zone_lines(
    frames: Sequence[Mapping[str, Any]], *, cast_names: Mapping[str, str] | None = None
) -> list[str]:
    """List (never flag) faces and props whose written placement could fall in a covered zone.

    The placement words are read, the drawn board is not measured (the kit has no face
    detector), so a line is a cell to look at, not a finding: written placements raised
    three false alarms as ``!!`` lines (Hanakaze #9, SCP-173 Blink #54). ``review``
    measures the finished take's caption box on its frames and burned caption file.

    Parameters
    ----------
    frames
        One set's frames, in board order.
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[str]
        One ``look at`` line per placement in a covered zone, then the human check line.
    """

    names = cast_names or {}
    lines: list[str] = []
    cell_in_row: dict[int, int] = {}
    for frame in frames:
        row = _row_number(frame)
        cell_in_row[row] = cell_in_row.get(row, 0) + 1
        cell = f"row {row} cell {cell_in_row[row]}"
        raw = frame.get("visual_brief")
        brief: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        found: list[tuple[str, str, str]] = []
        for blocking in brief.get("subject_blocking") or []:
            if not isinstance(blocking, Mapping):
                continue
            position = str(blocking.get("frame_position") or "")
            zone = covered_placement(position)
            if zone:
                who = names.get(
                    str(blocking.get("cast_id")),
                    str(blocking.get("cast_id") or "a cast member"),
                )
                found.append((f"{who}'s face", position, zone))
        for item in brief.get("story_objects") or []:
            zone = covered_placement(str(item), frame_anchored=True)
            if zone:
                found.append(("the prop", str(item), zone))
        note = str(brief.get("ui_safe_zone") or "")
        zone = covered_placement(note, frame_anchored=True)
        if zone:
            found.append(("the safe-zone note", note, zone))
        lines += [
            f'  look at {cell}: {what} is written "{text}", which could put it in {_ZONE_WHAT[zone]}. '
            "Written words only, not a finding: if the drawing puts it there, edit the frame "
            "(edit --frame ...) and redraw"
            for what, text, zone in found
        ]
    lines.append(f"  {SAFE_ZONE_BOARD_CHECK}")
    return lines


#: A ``frame_position`` that keeps the subject out of the picture: they are not drawn in that cell.
_OFF_FRAME = re.compile(
    r"\b(off[- ]?(frame|screen|camera)|out of (the )?(frame|shot)|unseen|not visible)\b",
    re.I,
)


def frame_cast(frame: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """Who a frame draws, and who it names but places off-frame.

    Parameters
    ----------
    frame
        One storyboard frame.

    Returns
    -------
    tuple[set[str], set[str]]
        ``(drawn, off_frame)`` cast ids: ``cast_refs`` plus ``subject_blocking``
        entries, less any whose ``frame_position`` puts them off-frame.
    """

    raw = frame.get("visual_brief")
    brief: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    named = {str(cast_id) for cast_id in frame.get("cast_refs") or [] if cast_id}
    off: set[str] = set()
    for blocking in brief.get("subject_blocking") or []:
        if not isinstance(blocking, Mapping) or not blocking.get("cast_id"):
            continue
        cast_id = str(blocking["cast_id"])
        if _OFF_FRAME.search(str(blocking.get("frame_position") or "")):
            off.add(cast_id)
        else:
            named.add(cast_id)
    return named - off, off


def row_speech_lines(
    spine: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    *,
    row: int,
    cast_names: Mapping[str, str],
) -> list[str]:
    """Which line is spoken on one board row, by whom, and a warning when the speaker is not drawn there.

    A beat's lines are anchored to one frame (``beats[].frame_id``), so they are
    spoken on that frame's row. A speaker marked ``off_screen`` is heard, not
    seen, and needs no place in the row.

    Parameters
    ----------
    spine
        Spine JSON (the beats).
    frames
        One board's frames.
    row
        The board row.
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[str]
        ``row N cell K says ...`` per line, then ``!!`` warnings (never blocking).
    """

    cells = [frame for frame in frames if _row_number(frame) == row]
    cell_of = {
        str(frame.get("frame_id")): index
        for index, frame in enumerate(cells, start=1)
        if frame.get("frame_id")
    }
    drawn: set[str] = set()
    off: set[str] = set()
    for frame in cells:
        cell_drawn, cell_off = frame_cast(frame)
        drawn |= cell_drawn
        off |= cell_off
    beats = [
        beat
        for beat in spine.get("beats") or []
        if isinstance(beat, Mapping) and str(beat.get("frame_id") or "") in cell_of
    ]
    beats.sort(key=lambda beat: int(beat.get("ordinal") or 0))
    shown = (
        ", ".join(sorted(cast_names.get(cast_id, cast_id) for cast_id in drawn))
        or "nobody"
    )
    out: list[str] = []
    for beat in beats:
        frame_id = str(beat["frame_id"])
        cell = cell_of[frame_id]
        for line in beat.get("dialogue_lines") or []:
            if not isinstance(line, Mapping):
                continue
            text = str(line.get("spoken_text") or line.get("text") or "").strip()
            if not text:
                continue
            cast_id = str(line.get("cast_id") or "")
            who = cast_names.get(cast_id, cast_id or "?")
            heard = line.get("off_screen") is True
            out.append(
                f'    row {row} cell {cell} says: {who}{" (off-screen)" if heard else ""}: "{text}"'
            )
            if heard or not cast_id:
                continue
            fix = (
                f"Mark the line off-screen (`fictora-produce line --line {line.get('line_id')} --off-screen`), give it to someone "
                "drawn here (`--speaker`), or edit the frame and redraw (warning only)"
            )
            if cast_id not in drawn:
                where = (
                    "is placed off-frame"
                    if cast_id in off
                    else f"is not drawn (the row shows {shown})"
                )
                out.append(
                    f'    !! row {row}: {who} speaks "{_clip(text)}" but {where}. {fix}'
                )
                continue
            own_drawn, _ = frame_cast(
                next(frame for frame in cells if str(frame.get("frame_id")) == frame_id)
            )
            if cast_id not in own_drawn:
                out.append(
                    f'    !! row {row} cell {cell}: {who} speaks "{_clip(text)}" on a cell that does not draw them '
                    f"(they are in another cell of the row). {fix}"
                )
    return out


def heard_line_ids(spine: Mapping[str, Any]) -> set[str]:
    """Line ids the take plays heard off screen, as the server says.

    fictora-drama (founder decision 4) derives it per line: the line is marked
    ``off_screen``, or the frame or beat that carries it keeps its speaker out
    of view (Hanakaze ep 1: Genzō's line had no flag while his frame said "NOT
    VISIBLE"). The spine lists those lines in ``heard_off_screen_line_ids``.
    A server without the field gives the flagged lines only, as before.

    Parameters
    ----------
    spine
        Spine JSON (``GET /v1/spines/{id}``).

    Returns
    -------
    set[str]
        The server's list, plus every line marked ``off_screen``.
    """

    listed = {str(line_id) for line_id in spine.get("heard_off_screen_line_ids") or []}
    flagged = {
        str(line.get("line_id"))
        for beat in spine.get("beats") or []
        if isinstance(beat, Mapping)
        for line in beat.get("dialogue_lines") or []
        if isinstance(line, Mapping) and line.get("off_screen") is True
    }
    return listed | flagged


def heard_not_seen(
    beats: Sequence[Mapping[str, Any]], *, heard_line_ids: Collection[str] = ()
) -> set[str]:
    """Cast ids one take only hears: an off-screen line here and nothing that shows them.

    The server's rule (``off_screen_staging``): someone with a line heard off
    screen in the take, and no on-screen line, no vocalization, and not the
    motion subject of a beat that does not carry their off-screen line.

    Parameters
    ----------
    beats
        The take's beats (spine JSON).
    heard_line_ids
        :func:`heard_line_ids` of the spine; a line counts as heard when it is
        here or marked ``off_screen``.

    Returns
    -------
    set[str]
        Cast ids that must not be on any frame of the take's board.
    """

    heard: set[str] = set()
    seen: set[str] = set()
    for beat in beats:
        off: set[str] = set()
        for line in beat.get("dialogue_lines") or []:
            if not isinstance(line, Mapping) or not line.get("cast_id"):
                continue
            is_heard = (
                line.get("off_screen") is True
                or str(line.get("line_id")) in heard_line_ids
            )
            (off if is_heard else seen).add(str(line["cast_id"]))
        heard |= off
        vocal = beat.get("vocalization")
        if isinstance(vocal, Mapping) and vocal.get("cast_id"):
            seen.add(str(vocal["cast_id"]))
        motion = beat.get("motion_direction")
        subject = (
            str(motion.get("subject_cast_id") or "")
            if isinstance(motion, Mapping)
            else ""
        )
        if subject and subject not in off:
            seen.add(subject)
    return heard - seen


_FACELESS = re.compile(
    r"\b(?:face|head)\b[^.;]{0,40}?\b(?:outside|out of|not visible|hidden|cropped)\b|\bheadless\b|\bfaceless\b"
    r"|\bshoulders? to (?:the )?waist\b",
    re.I,
)


def _shows_a_face(frame: Mapping[str, Any]) -> bool:
    raw = frame.get("visual_brief")
    brief: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    if str(brief.get("cell_role") or "") in ("insert", "cover"):
        return False
    if re.search(
        r"\b(?:insert|macro|detail)\b", str(brief.get("shot_scale") or ""), re.I
    ):
        return False
    staged = [b for b in brief.get("subject_blocking") or [] if isinstance(b, Mapping)]
    return any(
        not any(
            _FACELESS.search(str(b.get(field) or ""))
            for field in ("frame_position", "pose", "gaze")
        )
        for b in staged
    )


def off_screen_speaker_lines(
    spine: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    take_beats: Sequence[Mapping[str, Any]],
    *,
    cast_names: Mapping[str, str],
) -> list[str]:
    """Warn when a speaker the take only hears is on the board, or their line plays on no face.

    Hanakaze Sweets ep 3: Genzō (heard through the ceiling) was listed on a cell
    written about Mitsu, so the board drew him and his plate went to the image
    model; his line's row was two headless inserts. Warning only: the server
    takes him off the frames when it draws.

    Parameters
    ----------
    spine
        Spine JSON.
    frames
        One board's frames.
    take_beats
        That take's beats.
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[str]
        ``!!`` lines, empty when nothing is wrong.
    """

    heard_ids = heard_line_ids(spine)
    hidden = heard_not_seen(take_beats, heard_line_ids=heard_ids)
    if not hidden:
        return []
    out: list[str] = []
    rows = {frame.get("frame_id"): _row_number(frame) for frame in frames}
    for cast_id in sorted(hidden):
        who = cast_names.get(cast_id, cast_id)
        listed = [
            frame
            for frame in frames
            if cast_id in {str(c) for c in frame.get("cast_refs") or []}
            or any(
                isinstance(b, Mapping) and str(b.get("cast_id") or "") == cast_id
                for b in (frame.get("visual_brief") or {}).get("subject_blocking") or []
            )
        ]
        for frame in listed:
            out.append(
                f"  !! {who} is off-screen in this take (heard, not seen) but is listed on "
                f"{frame.get('frame_id')} (row {_row_number(frame)}): the board may draw them and their plate is "
                f"sent. Edit that frame's cast to the character it shows, then redraw (warning only)."
            )
    for beat in take_beats:
        unseen = [
            str(line.get("cast_id"))
            for line in beat.get("dialogue_lines") or []
            if isinstance(line, Mapping)
            and (
                line.get("off_screen") is True or str(line.get("line_id")) in heard_ids
            )
            and str(line.get("cast_id")) in hidden
        ]
        anchor = str(beat.get("frame_id") or "")
        if not unseen or anchor not in rows:
            continue
        row = rows[anchor]
        cells = [frame for frame in frames if _row_number(frame) == row]
        if cells and not any(_shows_a_face(frame) for frame in cells):
            out.append(
                f"  !! row {row} carries {cast_names.get(unseen[0], unseen[0])}'s off-screen line but shows no face "
                "(only inserts or cropped bodies): the line lands on nobody. Put the listener's reaction on that row "
                "(warning only)."
            )
    return out


def _plain_text(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def _name_patterns(cast_names: Mapping[str, str]) -> dict[str, list[re.Pattern[str]]]:
    """Per cast member, the name parts only they carry ("Ren", not the shared "Hanakaze")."""

    words = {
        cast_id: [
            w
            for w in re.findall(r"[\w'-]+", _plain_text(name))
            if w.casefold() not in {"the", "a", "an"}
        ]
        for cast_id, name in cast_names.items()
    }
    patterns: dict[str, list[re.Pattern[str]]] = {}
    for cast_id, own in words.items():
        shared = {
            w.casefold() for other, ws in words.items() if other != cast_id for w in ws
        }
        found = (
            [re.compile(rf"(?<![\w-]){re.escape(' '.join(own))}(?![\w-])", re.I)]
            if own
            else []
        )
        for word in own:
            if len(own) == 1 or len(word) < 3 or word.casefold() in shared:
                continue
            flags = re.I if len(word) >= 4 else 0
            found.append(
                re.compile(
                    rf"(?<![\w-]){re.escape(word if flags else word.capitalize())}(?![\w-])",
                    flags,
                )
            )
        patterns[cast_id] = found
    return patterns


@dataclass(frozen=True)
class NamedCastMismatch:
    """One frame whose words name ``missing`` but whose cast list draws ``unnamed`` instead."""

    frame: Mapping[str, Any]
    missing: tuple[str, ...]
    unnamed: tuple[str, ...]

    def line(self, cast_names: Mapping[str, str]) -> str:
        """The shot list's ``!!`` line."""

        return (
            f"  !! {self.frame.get('frame_id')} (row {_row_number(self.frame)}) names "
            f"{', '.join(cast_names.get(c, c) for c in self.missing)} but lists "
            f"{', '.join(cast_names.get(c, c) for c in self.unnamed)}: the board draws whom the cast list names. "
            "Edit that frame's cast to the character it describes, then redraw (warning only)."
        )


def named_cast_mismatches(
    frames: Sequence[Mapping[str, Any]],
    take_beats: Sequence[Mapping[str, Any]] = (),
    *,
    cast_names: Mapping[str, str],
) -> list[NamedCastMismatch]:
    """Frames whose words name one cast member but whose cast list has another it never names.

    Hanakaze Sweets ep 4: frames 4, 6 and 8 said "Catches Ren lowering his
    phone" and "Ren and pastry counter background right" but listed Genzō, and
    the board drew an old man in a brown robe. The board draws whom the cast
    list names. Read from ``beat_label``, ``beat_prompt``, ``story_moment`` and
    ``depth_order``; ``forbidden_elements`` ("no visible Genzō") never count.
    Someone with a line on a beat anchored to the frame belongs on its list
    whether or not its words name them.

    Parameters
    ----------
    frames
        One board's frames.
    take_beats
        That take's beats.
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[NamedCastMismatch]
        One per frame that lists someone other than whom it names; empty when none.
    """

    patterns = _name_patterns(cast_names)
    speakers: dict[str, set[str]] = {}
    for beat in take_beats:
        speakers.setdefault(str(beat.get("frame_id") or ""), set()).update(
            str(line.get("cast_id"))
            for line in beat.get("dialogue_lines") or []
            if isinstance(line, Mapping)
        )
    out: list[NamedCastMismatch] = []
    for frame in frames:
        brief = frame.get("visual_brief") or {}
        text = _plain_text(
            " ".join(
                str(value)
                for value in (
                    frame.get("beat_label"),
                    frame.get("beat_prompt"),
                    brief.get("story_moment"),
                    brief.get("depth_order"),
                )
                if value
            )
        )
        named = {
            cast_id
            for cast_id, found in patterns.items()
            if any(p.search(text) for p in found)
        }
        drawn, _off_frame = frame_cast(frame)
        missing = [c for c in cast_names if c in named - drawn]
        speaking = speakers.get(str(frame.get("frame_id") or ""), set())
        unnamed = [c for c in cast_names if c in drawn - named - speaking]
        if missing and unnamed:
            out.append(NamedCastMismatch(frame, tuple(missing), tuple(unnamed)))
    return out


def named_cast_mismatch_lines(
    frames: Sequence[Mapping[str, Any]],
    take_beats: Sequence[Mapping[str, Any]] = (),
    *,
    cast_names: Mapping[str, str],
) -> list[str]:
    """Warn when a frame's words name one cast member but its cast list has another it never names.

    The shot list's lines for :func:`named_cast_mismatches` (the board gate).
    Before a board is drawn the same check stops the paid draw
    (:func:`named_cast_stop`).

    Parameters
    ----------
    frames
        One board's frames.
    take_beats
        That take's beats.
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[str]
        ``!!`` lines, empty when every frame lists whom it names.
    """

    return [
        found.line(cast_names)
        for found in named_cast_mismatches(frames, take_beats, cast_names=cast_names)
    ]


def spine_cast_names(spine: Mapping[str, Any]) -> dict[str, str]:
    """``cast_id`` to display name from the spine's cast cards."""

    return {
        str(card.get("cast_id")): str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    }


def episode_named_cast_mismatches(
    spine: Mapping[str, Any], *, episode: int, sets: Sequence[int] | None = None
) -> dict[int, list[NamedCastMismatch]]:
    """:func:`named_cast_mismatches` for every board of an episode (or only ``sets``), as the shot list reads them.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    sets
        Only these boards (default every board on the episode).

    Returns
    -------
    dict[int, list[NamedCastMismatch]]
        Board index to its mismatches; boards with none are left out.
    """

    cast_names = spine_cast_names(spine)
    boards = frames_by_set(spine, episode=episode)
    planned = beats_by_take(spine, episode=episode, take_count=max(boards, default=1))
    found: dict[int, list[NamedCastMismatch]] = {}
    for set_index, frames in sorted(boards.items()):
        if sets is not None and set_index not in sets:
            continue
        take_beats = planned[set_index - 1] if set_index <= len(planned) else []
        hits = named_cast_mismatches(frames, take_beats, cast_names=cast_names)
        if hits:
            found[set_index] = hits
    return found


def _named_cast_fix(
    found: NamedCastMismatch,
    *,
    desk: str,
    episode: int,
    cast_names: Mapping[str, str],
) -> list[str]:
    """The commands that fix one mismatch: swap who is staged, or (when the list is right) fix the words."""

    frame_id = str(found.frame.get("frame_id"))
    where = f"fictora-produce edit --desk {desk} --episode {episode} --frame {frame_id}"
    raw = found.frame.get("visual_brief")
    brief: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    blocking = [
        str(entry.get("cast_id")) if isinstance(entry, Mapping) else ""
        for entry in brief.get("subject_blocking") or []
    ]
    lines: list[str] = []
    if (
        len(found.missing) == 1
        and len(found.unnamed) == 1
        and found.unnamed[0] in blocking
    ):
        index = blocking.index(found.unnamed[0])
        lines.append(
            f"    {where} --set subject_blocking.{index}.cast_id={found.missing[0]}   "
            f"(stage {cast_names.get(found.missing[0], found.missing[0])} where "
            f"{cast_names.get(found.unnamed[0], found.unnamed[0])} stands; cast_refs follows)"
        )
    else:
        lines.append(
            f'    {where} --set \'subject_blocking=[{{"cast_id": "…", "frame_position": "…", "pose": "…", '
            '"gaze": "…", "interaction": "…"}, …]\'   (every person in the shot, one entry each)'
        )
    lines.append(
        f'    or, when the listed cast is right: {where} --set story_moment="…"   (words that name whom it draws)'
    )
    return lines


def named_cast_stop(
    spine: Mapping[str, Any],
    *,
    episode: int,
    desk: str,
    sets: Sequence[int] | None = None,
    action: str = "drawing the boards",
    heads_up: bool = False,
) -> str | None:
    """The stop before a paid board draw when a frame names one character but lists another; ``None`` when clean.

    The same check the shot list prints after the draw
    (:func:`named_cast_mismatches`), run on the frames before any board is
    paid for: a leftover name otherwise costs a redraw.

    Parameters
    ----------
    spine
        Spine JSON (the frames as they stand).
    episode
        Episode ordinal.
    desk
        The desk, named in the commands.
    sets
        Only these boards (default every board on the episode).
    action
        What was stopped, for the first line.
    heads_up
        Said ahead of the paid step (after the draft, at the script yes): nothing is stopped yet.

    Returns
    -------
    str | None
        The message naming each take, frame, row and name with the command to fix it.
    """

    found = episode_named_cast_mismatches(spine, episode=episode, sets=sets)
    if not found:
        return None
    cast_names = spine_cast_names(spine)
    why = (
        "A frame's words name one character but its cast list has another, and the board draws whom the "
        "list names (a redraw costs $0.30 per board)."
    )
    lines = [
        f"!! Before the boards: {why} `step` stops before drawing them until each frame is fixed (free edits):"
        if heads_up
        else f"!! Stopped before {action}: nothing was sent or paid. {why} Fix each frame, then run the "
        "same command again:"
    ]
    for set_index, hits in found.items():
        for hit in hits:
            frame = hit.frame
            beat = (
                f', beat "{frame.get("beat_label")}"' if frame.get("beat_label") else ""
            )
            lines.append(
                f"  !! ep{episode:02d} t{set_index} {frame.get('frame_id')} (row {_row_number(frame)}{beat}) names "
                f"{', '.join(cast_names.get(c, c) for c in hit.missing)} but lists "
                f"{', '.join(cast_names.get(c, c) for c in hit.unnamed)}"
            )
            lines += _named_cast_fix(
                hit, desk=desk, episode=episode, cast_names=cast_names
            )
    lines.append(
        "  After the script gate, run each edit with --preview first and show the human the before/after."
    )
    return "\n".join(lines)


_WHOSE = r"(?:(?:his|her|their|its|the|one's|my|your|a)\s+)?(?:own\s+)?"
_MOUTH_WORD = r"(?:mouth|lips)\b"
_COVERING_THING = (
    r"(?:hands?|palms?|fingers?|fingertips?|fists?|knuckles?|wrists?|sleeves?|arms?|fan|napkin|towel|tissue|"
    r"handkerchief|cloth|cup|teacup|mug|glass|bottle|straw|phone|mask|scarf|book|menu|paper|tray|chopsticks?|"
    r"spoon|fork|food|mochi|snack)"
)
#: Staging that hides a mouth: kept in step with the server's
#: ``speaking_mouth_free.MOUTH_HIDDEN_PATTERN`` (which rewrites it at compile).
MOUTH_HIDDEN = re.compile(
    "|".join(
        (
            r"\b(?:cover(?:s|ed|ing)?|hid(?:e|es|ing|den)|muffl(?:e|es|ed|ing)|block(?:s|ed|ing)?|"
            r"obscur(?:e|es|ed|ing)|conceal(?:s|ed|ing)?|stifl(?:e|es|ed|ing)|smother(?:s|ed|ing)?)\b"
            rf"[^,;.]{{0,24}}\b{_MOUTH_WORD}",
            rf"\b{_MOUTH_WORD}\s+(?:(?:still|half|fully|partly)\s+)?(?:covered|hidden|muffled|blocked|obscured|concealed)\b",
            rf"\b{_COVERING_THING}\b[^,;.]{{0,28}}?\b(?:over|on|onto|to|against|across|at|near|by|beside|"
            rf"in\s+front\s+of|covering|up\s+to|under)\s+{_WHOSE}{_MOUTH_WORD}",
            r"\b(?:wip(?:e|es|ed|ing)|clear(?:s|ed|ing)?|dab(?:s|bed|bing)?|rub(?:s|bed|bing)?)\s+"
            rf"(?:[a-z]+\s+(?:from|off)\s+)?{_WHOSE}(?:mouth|lips|chin)\b",
            rf"\b{_MOUTH_WORD}\s+(?:clearing|wiping|wiped|cleared|dabbed)\b",
            rf"\bbehind\s+{_WHOSE}(?:hands?|palms?|fingers?|fan|sleeves?|cup|teacup|mug|phone|menu|napkin|"
            r"tissue|handkerchief|scarf|mask)\b",
            rf"\b{_MOUTH_WORD}\s+(?:full|stuffed|crammed)\b",
            r"\bhand-over-(?:the-)?mouth\b",
        )
    ),
    re.I,
)
#: Expression kinds whose drawn marks put a hand over the mouth.
_MOUTH_COVERING_KINDS = frozenset({"dramatic_gasp"})


def _speaking_cells(
    frames: Sequence[Mapping[str, Any]], take_beats: Sequence[Mapping[str, Any]]
) -> dict[str, set[str]]:
    """Frame id to the on-screen speakers it stages: the anchor's whole row."""

    rows = {str(frame.get("frame_id") or ""): _row_number(frame) for frame in frames}
    out: dict[str, set[str]] = {}
    for beat in take_beats:
        anchor = str(beat.get("frame_id") or "")
        if anchor not in rows:
            continue
        speakers = {
            str(line.get("cast_id"))
            for line in beat.get("dialogue_lines") or []
            if isinstance(line, Mapping)
            and str(line.get("text") or "").strip()
            and line.get("off_screen") is not True
        }
        for frame in frames:
            if _row_number(frame) != rows[anchor]:
                continue
            drawn, _off = frame_cast(frame)
            if staged := speakers & drawn:
                out.setdefault(str(frame.get("frame_id") or ""), set()).update(staged)
    return out


def speaking_mouth_hidden_lines(
    frames: Sequence[Mapping[str, Any]],
    take_beats: Sequence[Mapping[str, Any]],
    *,
    cast_names: Mapping[str, str],
) -> list[str]:
    """Warn when a cell a line plays over hides the speaker's mouth.

    Hanakaze Sweets ep 6: frame 01 carried Mitsu's shrieked line, its pose said
    "mouth clearing after the spray" and it played ``dramatic_gasp`` ("one hand
    over the mouth"); the board drew her hand clamped over her mouth on both
    cells of the row. Reads the speaker's ``pose`` and ``interaction``, the
    cell's ``story_moment`` and its row direction. The server rewrites these at
    compile; this is a warning so the gate catches a board drawn before that.

    Parameters
    ----------
    frames
        One board's frames.
    take_beats
        That take's beats.
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[str]
        ``!!`` lines, empty when every speaking mouth is free.
    """

    speaking = _speaking_cells(frames, take_beats)
    out: list[str] = []
    for frame in frames:
        frame_id = str(frame.get("frame_id") or "")
        speakers = speaking.get(frame_id)
        if not speakers:
            continue
        raw = frame.get("visual_brief")
        brief: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        texts = [str(brief.get("story_moment") or "")]
        for blocking in brief.get("subject_blocking") or []:
            if (
                isinstance(blocking, Mapping)
                and str(blocking.get("cast_id") or "") in speakers
            ):
                texts += [
                    str(blocking.get("pose") or ""),
                    str(blocking.get("interaction") or ""),
                ]
        direction = brief.get("row_direction")
        if isinstance(direction, Mapping):
            texts += [
                str(direction.get("starts") or ""),
                str(direction.get("ends") or ""),
            ]
            texts += [str(step) for step in direction.get("chain") or []]
        hits = [m.group(0) for text in texts if (m := MOUTH_HIDDEN.search(text))]
        kind = str(brief.get("reaction_kind") or "")
        if kind in _MOUTH_COVERING_KINDS:
            hits.append(f"expression {kind} (hand over the mouth)")
        if hits:
            who = ", ".join(cast_names.get(c, c) for c in sorted(speakers))
            out.append(
                f"  !! {frame_id} (row {_row_number(frame)}) carries {who}'s line but its staging hides the mouth "
                f"({'; '.join(dict.fromkeys(hits))}): the take cannot show the words. Look for a hand or cup at "
                "the mouth on the board; edit the pose to an open gesture and redraw (warning only)."
            )
    return out


#: A shot size close enough that the face is the picture.
_CLOSE_SCALE = re.compile(r"\bclose\b", re.IGNORECASE)


def _emotional_reasons(
    cells: Sequence[Mapping[str, Any]], anchored: Sequence[Mapping[str, Any]]
) -> list[str]:
    reasons: list[str] = []
    for beat in anchored:
        direction = beat.get("motion_direction")
        end_state = (
            str(direction.get("end_state") or "").strip()
            if isinstance(direction, Mapping)
            else ""
        )
        if end_state:
            reasons.append(f"beat {beat.get('ordinal')} ends on {end_state[:60]!r}")
        payoff = str(beat.get("satisfaction_type") or "").strip()
        if payoff and payoff != "none":
            reasons.append(f"beat {beat.get('ordinal')} pays off a {payoff}")
    for cell in cells:
        raw = cell.get("visual_brief")
        brief: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        if not brief.get("subject_blocking"):
            continue  # no face on the cell
        if str(brief.get("cell_role") or "") == "reaction":
            reasons.append(f"{cell.get('frame_id')} is a reaction cell")
        elif _CLOSE_SCALE.search(str(brief.get("shot_scale") or "")):
            reasons.append(
                f"{cell.get('frame_id')} is a {brief.get('shot_scale')} on a face"
            )
    return list(dict.fromkeys(reasons))


def missing_expression_lines(
    frames: Sequence[Mapping[str, Any]],
    take_beats: Sequence[Mapping[str, Any]],
    *,
    episode: int,
) -> list[str]:
    """Warn when an emotional row has no expression picked (``reaction_kind``).

    The take prints a row's expression kind as a picture of the face the
    video model keeps; a row without one plays its emotion from the motion
    text alone, which the provider's rewrite thins or drops (Hanakaze ep 5,
    2026-10-01). A row is an emotional moment when a beat anchored on it ends
    on a stated ``end_state`` or pays off a ``satisfaction_type``, or a cell
    with a staged face is a ``reaction`` cell or a close-up. Reads the spine
    fields only. A warning: the gate still takes a yes.

    Parameters
    ----------
    frames
        One board's frames.
    take_beats
        That take's beats.
    episode
        Episode ordinal, for the suggested command.

    Returns
    -------
    list[str]
        ``!!`` lines naming the row and the beat to give an expression; empty
        when every emotional row wears one.
    """

    rows: dict[int, list[Mapping[str, Any]]] = {}
    for frame in frames:
        rows.setdefault(_row_number(frame), []).append(frame)
    beats = sorted(take_beats, key=lambda beat: int(beat.get("ordinal") or 0))
    anchor_row = {
        str(beat.get("frame_id") or ""): beat for beat in beats if beat.get("frame_id")
    }
    out: list[str] = []
    owner: Mapping[str, Any] | None = beats[0] if beats else None
    for row, cells in sorted(rows.items()):
        anchored = [
            anchor_row[str(cell.get("frame_id") or "")]
            for cell in cells
            if str(cell.get("frame_id") or "") in anchor_row
        ]
        if anchored:
            owner = anchored[0]
        briefs = [cell.get("visual_brief") for cell in cells]
        if any(
            isinstance(brief, Mapping) and brief.get("reaction_kind")
            for brief in briefs
        ):
            continue
        if any(beat.get("reaction_kind") for beat in anchored):
            continue  # asked for and not worn: the shot list already says so
        reasons = _emotional_reasons(cells, anchored)
        if not reasons or owner is None:
            continue
        beat_ordinal = owner.get("ordinal")
        out.append(
            f"  !! row {row}: an emotional moment ({'; '.join(reasons)}) with no expression picked; the take "
            "plays it from the motion text alone. Give it one (warning only): "
            f"`edit --episode {episode} --beat {beat_ordinal} --expression KIND` "
            "(`expressions` lists them), then redraw the board."
        )
    return out


def _is_insert_cell(frame: Mapping[str, Any]) -> bool:
    raw = frame.get("visual_brief")
    brief: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    return str(brief.get("cell_role") or "") in ("insert", "cover") or bool(
        re.search(r"\binsert\b", str(brief.get("shot_scale") or ""), re.I)
    )


def insert_run_lines(frames: Sequence[Mapping[str, Any]]) -> list[str]:
    """Warn when a take has two or more consecutive insert cells.

    Hanakaze Sweets ep 6: row 2 (frames 03 and 04) was two countertop inserts,
    about 3.5 s with no face straight after the hook. The server turns the
    second into a reaction close-up at compile on a dialogue take; this is a
    warning so the gate catches a board drawn before that.

    Parameters
    ----------
    frames
        One board's frames, in board order.

    Returns
    -------
    list[str]
        One ``!!`` line per run of two or more insert (or cover) cells.
    """

    ordered = sorted(frames, key=lambda frame: int(frame.get("ordinal") or 0))
    runs: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    for frame in ordered:
        if _is_insert_cell(frame):
            current.append(frame)
            continue
        if len(current) >= 2:
            runs.append(current)
        current = []
    if len(current) >= 2:
        runs.append(current)
    return [
        f"  !! {len(run)} insert cells in a row ({', '.join(str(f.get('frame_id')) for f in run)}; rows "
        f"{', '.join(str(r) for r in dict.fromkeys(_row_number(f) for f in run))}): about "
        f"{len(run) * 1.75:.1f} s with no face. Keep one insert and make the other a reaction close-up "
        "(warning only)."
        for run in runs
    ]


#: Body postures a pose names plainly. A pose that names exactly one of them has that posture.
_POSTURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "standing",
        re.compile(r"\b(?:stand(?:s|ing)?|upright|on (?:his|her|their) feet)\b", re.I),
    ),
    (
        "kneeling",
        re.compile(r"\b(?:kneel(?:s|ing)?|knelt|on (?:his|her|their) knees)\b", re.I),
    ),
    ("sitting", re.compile(r"\b(?:sit(?:s|ting)?|seated|sat)\b", re.I)),
    (
        "lying",
        re.compile(
            r"\b(?:lying|lies|face[- ]down|face[- ]up|prone|supine|sprawled)\b", re.I
        ),
    ),
    (
        "crouching",
        re.compile(r"\b(?:crouch(?:es|ing|ed)?|squat(?:s|ting)?|hunkered)\b", re.I),
    ),
)


def pose_posture(pose: str) -> str | None:
    """The one body posture a pose names (``standing``, ``kneeling`` ...), or ``None``.

    ``None`` when the pose names no posture or more than one (``rises from kneeling
    to standing``): only a plain posture counts, so a pose change is never guessed.
    """

    found = {name for name, pattern in _POSTURES if pattern.search(pose)}
    return found.pop() if len(found) == 1 else None


def _postures(frame: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """``cast_id -> (posture, pose)`` for each drawn cast member whose pose names one posture."""

    drawn, _ = frame_cast(frame)
    raw = frame.get("visual_brief")
    brief: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    out: dict[str, tuple[str, str]] = {}
    for blocking in brief.get("subject_blocking") or []:
        if not isinstance(blocking, Mapping):
            continue
        cast_id = str(blocking.get("cast_id") or "")
        pose = str(blocking.get("pose") or "")
        posture = pose_posture(pose)
        if cast_id in drawn and posture:
            out[cast_id] = (posture, pose)
    return out


def pose_change_at_cut_lines(
    frames: Sequence[Mapping[str, Any]], *, cast_names: Mapping[str, str]
) -> list[str]:
    """Warn when one character is drawn in the cells either side of a cut in plainly different postures.

    Turbo films from the board picture alone: the same person drawn in two
    panels (kneeling at the end of row 3, standing at the start of row 4) can
    come out as two people in one shot. The cells either side of a cut are the
    last cell of one row and the first cell of the next. Only postures a pose
    names in plain words are compared (:func:`pose_posture`); anything else is
    not guessed at.

    Parameters
    ----------
    frames
        One board's frames, in board order.
    cast_names
        ``cast_id`` to display name.

    Returns
    -------
    list[str]
        One ``!!`` line per character and cut; empty when nothing reads that way.
    """

    by_row: dict[int, list[Mapping[str, Any]]] = {}
    for frame in frames:
        by_row.setdefault(_row_number(frame), []).append(frame)
    rows = sorted(by_row)
    out: list[str] = []
    for above, below in zip(rows, rows[1:], strict=False):
        last, first = by_row[above][-1], by_row[below][0]
        before, after = _postures(last), _postures(first)
        for cast_id in sorted(set(before) & set(after)):
            (was, _), (now, _) = before[cast_id], after[cast_id]
            if was == now:
                continue
            who = cast_names.get(cast_id, cast_id)
            out.append(
                f"  !! rows {above} and {below}: {who} is {was} in row {above} cell {len(by_row[above])} and {now} "
                f"in row {below} cell 1, right at the cut. Turbo sees the whole board and can draw {who} twice in "
                f"one shot: count the people at that cut in the take (warning only)."
            )
    return out


#: The one redraw note the board gate offers for a take that may show someone twice.
EDGE_REDRAW_NOTE = "keep each character on one side within a shot"


def edge_duplicate_risk_lines(
    exposure: Mapping[str, Any] | None,
    *,
    desk: str,
    episode: int,
    sets: Collection[int] | None = None,
) -> list[str]:
    """The board gate's one line per take whose shots may show a character twice.

    Sweet Racket, 6 Oct 2026 (L-20261006-9): the video model drew a character
    twice, facing himself from opposite edges, because the cells of one shot
    put him left in one and right in the next. The server (fictora-drama
    ``board_edge_risks``) keeps a new show's people on one side before it
    draws, and returns what is left on the board exposure route, only for
    boards still awaiting approval. Founder, 8 Oct 2026: calm and rare. One
    ``!!`` line per take naming the shots, then its redraw command once; a
    stated walk across (``moves``) is one quiet information line, never
    ``!!``. Warnings only. A server without the field prints nothing.

    Parameters
    ----------
    exposure
        ``GET .../boards/exposure`` JSON, or ``None`` when it was unavailable.
    desk
        Series desk (for the printed command).
    episode
        Episode ordinal.
    sets
        Only these boards (default every board in the response).

    Returns
    -------
    list[str]
        Printable lines; empty when no board has a risk.
    """

    lines: list[str] = []
    for board in (exposure or {}).get("boards") or []:
        if not isinstance(board, Mapping):
            continue
        set_index = int(board.get("set_index") or 1)
        if sets is not None and set_index not in sets:
            continue
        risks = [
            risk
            for risk in board.get("edge_duplicate_risks") or []
            if isinstance(risk, Mapping) and risk.get("who") and risk.get("shot_index")
        ]
        twice: dict[int, list[str]] = {}
        walks: dict[int, list[str]] = {}
        for risk in risks:
            bucket = walks if risk.get("moves") else twice
            names = bucket.setdefault(int(risk["shot_index"]), [])
            if str(risk["who"]) not in names:
                names.append(str(risk["who"]))
        if twice:
            count = len(twice)
            shots = "; ".join(
                f"shot {shot}: {', '.join(names)}"
                for shot, names in sorted(twice.items())
            )
            lines.append(
                f"  !! t{set_index} board: {count} shot{'s' if count > 1 else ''} may show a character twice "
                f'({shots}). Fix: redraw t{set_index} with "{EDGE_REDRAW_NOTE}" (warning only)'
            )
            lines.append(
                f"     fictora-produce redraw-board --desk {desk} --episode {episode} --take t{set_index} "
                f'--note "{EDGE_REDRAW_NOTE}"'
            )
        if walks:
            shots = "; ".join(
                f"shot {shot}: {', '.join(names)}"
                for shot, names in sorted(walks.items())
            )
            lines.append(
                f"  t{set_index} board: a character walks across the frame ({shots}); "
                "watch that shot in the take. Information only."
            )
    return lines


def _clip(text: str, size: int = 60) -> str:
    return text if len(text) <= size else text[: size - 1] + "…"


def shot_list_lines(
    spine: Mapping[str, Any], *, episode: int, sets: Sequence[int] | None = None
) -> list[str]:
    """The board gate's shot list: one line per row with who says what on it, repeated sizes, safe zones.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    sets
        Only these boards (default every board on the episode).

    Returns
    -------
    list[str]
        Printable lines; empty when the spine has no frames for the episode.
    """

    # Imported here: touch_and_side reads frames with this module's helpers.
    from creation.touch_and_side import spine_cards, unowned_touch_lines

    lines: list[str] = []
    cast_names = spine_cast_names(spine)
    boards = frames_by_set(spine, episode=episode)
    planned = beats_by_take(spine, episode=episode, take_count=max(boards, default=1))
    for set_index, frames in sorted(boards.items()):
        if sets is not None and set_index not in sets:
            continue
        rows = shot_rows(frames)
        lines.append(f"ep{episode:02d} t{set_index} board, row by row:")
        take_beats = planned[set_index - 1] if set_index <= len(planned) else []
        for beat in take_beats:
            if beat.get("shot_plan"):
                lines.append(
                    f"  beat {beat.get('ordinal')} asks for (its first row is shot 1):"
                )
                lines += plan_lines(beat.get("shot_plan"), indent="    ")
            if beat.get("reaction_kind"):
                lines.append(
                    f"  beat {beat.get('ordinal')} asks for expression {beat['reaction_kind']} "
                    "(its anchor row wears it)"
                )
        worn = {kind for row in rows for kind in row.reaction_kinds}
        for beat in take_beats:
            kind = beat.get("reaction_kind")
            if kind and str(kind) not in worn:
                lines.append(
                    f"  !! beat {beat.get('ordinal')} asks for expression {kind} and no row on this board wears it: "
                    f"the board was drawn before the request, or its row stages nobody. "
                    f"`redraw-board --episode {episode} --take t{set_index} --cause '...'`"
                )
        for row in rows:
            lines.append(f"  {row.one_line()}")
            lines += row_speech_lines(spine, frames, row=row.row, cast_names=cast_names)
        for above, below in zip(rows, rows[1:], strict=False):
            if (above.shot_scale, above.camera_angle) == (
                below.shot_scale,
                below.camera_angle,
            ):
                lines.append(
                    f"  !! rows {above.row} and {below.row} share size and angle "
                    f"({above.shot_scale}, {above.camera_angle}): the cut will not read as a new shot"
                )
        lines += pose_change_at_cut_lines(frames, cast_names=cast_names)
        lines += mood_jump_lines(frames, cast_names=cast_names)
        lines += off_screen_speaker_lines(
            spine, frames, take_beats, cast_names=cast_names
        )
        lines += safe_zone_lines(frames, cast_names=cast_names)
        lines += named_cast_mismatch_lines(frames, take_beats, cast_names=cast_names)
        lines += speaking_mouth_hidden_lines(frames, take_beats, cast_names=cast_names)
        lines += unowned_touch_lines(
            frames, cast_names=cast_names, cards=spine_cards(spine)
        )
        lines += missing_expression_lines(frames, take_beats, episode=episode)
        lines += insert_run_lines(frames)
    return lines


__all__ = [
    "BOARD_INPUT_NAMES",
    "SAFE_ZONE_BOARD_CHECK",
    "ShotRow",
    "beats_by_take",
    "board_assets",
    "board_inputs",
    "covered_placement",
    "dialogue_line_ids",
    "edge_duplicate_risk_lines",
    "episode_api_id",
    "episode_id_for",
    "episode_summary",
    "frame_cast",
    "frames_by_set",
    "frames_digest",
    "heard_not_seen",
    "insert_run_lines",
    "missing_expression_lines",
    "NamedCastMismatch",
    "episode_named_cast_mismatches",
    "named_cast_mismatch_lines",
    "named_cast_mismatches",
    "named_cast_stop",
    "spine_cast_names",
    "off_screen_speaker_lines",
    "pose_change_at_cut_lines",
    "pose_posture",
    "row_speech_lines",
    "safe_zone_lines",
    "speaking_mouth_hidden_lines",
    "script_lines",
    "shot_list_lines",
    "shot_rows",
    "spoken_lines",
    "storyboard_set_for_beat",
]
