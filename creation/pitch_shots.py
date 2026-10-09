"""The episode's shots by take, in plain words: the same reading as the app's pitch card (fictora-drama, 9 Oct 2026).

The server builds the creator app's per-episode pitch card from the writer's own
shot plan (fictora-drama ``drama_generation.pitch_card``): one row per shot,
grouped by take, each with a short camera tag ("Close-up · high angle"), the
scene, the spoken line apart from the scene, and the expression as a short stage
direction ("Mina stares, stunned"), never a code. The kit cannot import the
server, so it mirrors the reading here (as it mirrors ``LINE_DELIVERIES``) and
``tests/data/pitch_card_parity.json``, copied from the server's
``tests/drama_generation/fixtures/pitch_card_parity.json``, holds both to the
same card. Change the reading in the server first, regenerate the fixture
there, copy it here, then mirror the change.

Kit desks keep the kit's own pitch card (``creation.pitch_card``: throughline,
world rules, emotional beats…) and its gate. This only adds the shots to what
``pitch`` prints when the desk has a story, so the producer sees the episode the
way an app creator does.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

#: Where the parts of a camera tag are joined (server ``CAMERA_TAG_JOIN``).
CAMERA_TAG_JOIN = " · "
#: Longest scene a card shows (server ``SCENE_MAX_CHARS``).
SCENE_MAX_CHARS = 240

#: Every expression code as a short stage direction (server ``EXPRESSION_DIRECTIONS``).
EXPRESSION_DIRECTIONS: Mapping[str, str] = {
    "laugh": "{name} laughs",
    "slow_surprise": "{name}'s eyes slowly widen",
    "stunned_blank": "{name} stares, stunned",
    "sweat_drop": "{name} sweats it out",
    "pause_then_outburst": "a pause, then {name} bursts out",
    "dramatic_gasp": "{name} gasps",
    "double_take": "{name} does a double take",
    "freeze": "{name} goes still, eyes wide",
    "flinch": "{name} flinches",
    "anger_flare": "{name}'s anger flares",
    "eye_twitch": "{name}'s eye twitches behind a polite smile",
    "blush_look_away": "{name} blushes and looks away",
    "sparkle_delight": "{name} lights up",
    "smile_goes_cold": "{name}'s smile goes cold",
    "tear_up": "{name}'s eyes fill with tears",
    "happy": "{name} beams",
    "big_laugh": "{name} bursts out laughing",
    "comic_tears": "{name} wails, tears streaming",
    "comic_anger": "{name} fumes",
    "smiling_rage": "{name} smiles through the rage",
    "shock": "{name}'s jaw drops",
    "gloom": "a gloom cloud settles over {name}",
    "embarrassed": "{name} goes red",
    "deadpan": "{name} keeps a flat face",
    "deflated": "{name} deflates",
}

#: Shot sizes a writer opens a row with, longest first (server ``SHOT_SIZES``).
SHOT_SIZES: tuple[tuple[str, str], ...] = (
    ("extreme close-up", "Extreme close-up"),
    ("extreme close up", "Extreme close-up"),
    ("medium close-up", "Medium close-up"),
    ("medium close up", "Medium close-up"),
    ("medium two-shot", "Two-shot"),
    ("medium two shot", "Two-shot"),
    ("wide establishing shot", "Wide"),
    ("establishing shot", "Wide"),
    ("over-the-shoulder", "Over the shoulder"),
    ("over-shoulder", "Over the shoulder"),
    ("over the shoulder", "Over the shoulder"),
    ("over his shoulder", "Over his shoulder"),
    ("over her shoulder", "Over her shoulder"),
    ("medium wide", "Medium wide"),
    ("medium shot", "Medium"),
    ("close-up", "Close-up"),
    ("close up", "Close-up"),
    ("two-shot", "Two-shot"),
    ("two shot", "Two-shot"),
    ("wide shot", "Wide"),
    ("full shot", "Wide"),
    ("long shot", "Wide"),
    ("insert", "Insert"),
    ("pov", "Point of view"),
    ("medium", "Medium"),
    ("wide", "Wide"),
    ("widens", "Wide"),
)
#: Angles worth a tag (server ``ANGLES``).
ANGLES: tuple[tuple[str, str], ...] = (
    ("low angle", "low angle"),
    ("high angle", "high angle"),
    ("top-down", "from above"),
    ("overhead", "from above"),
    ("bird's-eye", "from above"),
    ("dutch angle", "tilted"),
    ("side on", "side on"),
    ("profile", "side on"),
    ("eye level", "eye level"),
)
_CAMERA_FILLER = re.compile(
    r"^(?:(?:locked|static|handheld|slow|tight|loose|steady|single|continuous|shot|cut to|then|and)\b[\s,:-]*)+",
    re.IGNORECASE,
)
_ROW_SPLIT = re.compile(r"\bRow\s+(\d)\b[\s:,.-]*", re.IGNORECASE)
_SHOT_SPLIT = re.compile(r"\bSHOT\s+(\d)\b[\s:,.-]*(?:—|-)?\s*", re.IGNORECASE)
_EXPRESSION_TAIL = re.compile(
    r"\s*\bExpression:\s*(?:(?P<face>[^.;:\n]+?)\s*(?:—|–|\s-\s)\s*)?(?P<kind>[a-z_]+)\b\.?",
    re.IGNORECASE,
)
_SPACES = re.compile(r"\s+")
_CRAFT_PHRASES = re.compile(
    r"^(?:to (?:reveal|show)\s+)|^(?:with )?(?:both|all|their) faces (?:readable|visible|in frame)[\s:,;-]*"
    r"|\b(?:blank or pictorial|blank|pictorial) (?=(?:\w+ )?signs?\b)"
    r"|,?\s*\blip[- ]sync(?:ed)?\b[^,.;]*",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip(" \t\n,;:-—")


def _plain(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = _clean(_CRAFT_PHRASES.sub("", text))
    return text


def _sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _short(text: str, limit: int = SCENE_MAX_CHARS) -> str:
    text = _clean(text)
    if len(text) <= limit:
        return text
    window = text[:limit]
    for stop in (". ", "! ", "? ", "; ", ", "):
        cut = window.rfind(stop)
        if cut >= limit // 3:
            return (
                window[: cut + 1].rstrip(",; ")
                if stop[0] in ".!?"
                else window[:cut].rstrip(",; ") + "…"
            )
    return window.rsplit(" ", 1)[0].rstrip(",;: ") + "…"


def _size_tag(text: str) -> tuple[str | None, str]:
    rest = _CAMERA_FILLER.sub("", text.strip())
    lowered = rest.lower()
    for words, tag in SHOT_SIZES:
        if lowered.startswith(words) and (
            len(lowered) == len(words) or not lowered[len(words)].isalpha()
        ):
            remainder = rest[len(words) :]
            if words == "widens":
                return tag, _sentence_case(_plain(remainder))
            remainder = _CAMERA_FILLER.sub(
                "", _clean(re.sub(r"^\s*(?:shot)?\s*(?:on|of)?\b", "", remainder))
            )
            return tag, remainder
    return None, rest


def _angle_tag(text: str) -> str | None:
    lowered = text.lower()
    return next((tag for words, tag in ANGLES if words in lowered), None)


def camera_tag(size: str | None, angle: str | None = None) -> str:
    """Join a size and an angle into a short tag: ``"Close-up · low angle"``.

    Parameters
    ----------
    size
        The shot size in the writer's words, or ``None``.
    angle
        The angle in the writer's words, or ``None``.

    Returns
    -------
    str
        The tag; "Medium" when the writer named no size.
    """

    tag, _rest = _size_tag(size or "")
    if tag is None:
        tag = _sentence_case(_clean(size or "")) or "Medium"
    angle_words = _angle_tag(angle or "") or (_clean(angle).lower() if angle else None)
    if angle_words and angle_words not in tag.lower():
        return f"{tag}{CAMERA_TAG_JOIN}{angle_words}"
    return tag


def _scene_text(intent: str) -> str:
    _size, rest = _size_tag(_EXPRESSION_TAIL.sub(" ", intent))
    return _short(_sentence_case(_plain(rest)))


def _intent_rows(intent: str) -> list[str]:
    body = _EXPRESSION_TAIL.sub(" ", intent)
    for marker in (_ROW_SPLIT, _SHOT_SPLIT):
        parts = marker.split(body)
        if len(parts) >= 3:
            lead = _clean(parts[0])
            rows = [_clean(parts[index + 1]) for index in range(1, len(parts), 2)]
            if lead and rows:
                rows[0] = _clean(f"{lead} {rows[0]}")
            return [row for row in rows if row] or [_clean(body)]
    return [_clean(body)]


def _row_from_text(text: str, *, speaking: bool) -> tuple[str, str]:
    size, rest = _size_tag(text)
    angle = _angle_tag(text.split(".", 1)[0])
    if size is None:
        size = "Medium close-up" if speaking else "Medium"
    tag = (
        f"{size}{CAMERA_TAG_JOIN}{angle}"
        if angle and angle not in size.lower()
        else size
    )
    scene = (
        _short(_sentence_case(_plain(rest)))
        or _short(_sentence_case(_plain(text)))
        or tag
    )
    return tag, scene


def _beat_rows(beat: Mapping[str, Any]) -> list[tuple[str, str]]:
    speaking = bool(beat.get("dialogue_lines"))
    intent = str(beat.get("motion_intent") or "")
    texts = _intent_rows(intent)
    plan = [shot for shot in beat.get("shot_plan") or [] if isinstance(shot, Mapping)]
    if plan:
        rows: list[tuple[str, str]] = []
        for index, shot in enumerate(plan):
            subject = str(shot.get("subject") or "").strip()
            if index < len(texts) and len(texts) == len(plan):
                _size, scene = _size_tag(texts[index])
                scene = _short(_sentence_case(_plain(scene))) or _sentence_case(subject)
            elif index == 0:
                scene = _scene_text(intent)
            else:
                scene = _sentence_case(_clean(subject))
            rows.append(
                (camera_tag(shot.get("size"), shot.get("angle")), scene or subject)
            )
        return rows
    return [
        _row_from_text(text, speaking=speaking and index == 0)
        for index, text in enumerate(texts)
        if text
    ]


def _short_name(card: Mapping[str, Any]) -> str:
    """The server's ``cast[].short_name`` (fictora-drama #734), else the full name: the kit never guesses."""

    return str(card.get("short_name") or card.get("name"))


def _cast_names(spine: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(card.get("cast_id")): _short_name(card)
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id") and card.get("name")
    }


def _full_to_short(spine: Mapping[str, Any]) -> list[tuple[str, str]]:
    pairs = [
        (str(card.get("name")), _short_name(card))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("name")
    ]
    return sorted(
        ((full, short) for full, short in pairs if full != short),
        key=lambda pair: -len(pair[0]),
    )


def _shorten_names(text: str, pairs: Sequence[tuple[str, str]]) -> str:
    for full, short in pairs:
        text = re.sub(rf"\b{re.escape(full)}\b", short, text)
    return text


def _beat_line(
    beat: Mapping[str, Any], names: Mapping[str, str]
) -> dict[str, str] | None:
    lines = [
        line for line in beat.get("dialogue_lines") or [] if isinstance(line, Mapping)
    ]
    if not lines:
        return None
    line = lines[0]
    words = _clean(str(line.get("subtitle_text") or line.get("text") or ""))
    speaker = names.get(str(line.get("cast_id") or ""), "")
    if not words or not speaker:
        return None
    return {"speaker": speaker, "text": words}


def _anchor_reaction(
    spine: Mapping[str, Any], beat: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    if beat.get("frame_id"):
        for frame in spine.get("frames") or []:
            if not isinstance(frame, Mapping) or frame.get("frame_id") != beat.get(
                "frame_id"
            ):
                continue
            brief = frame.get("visual_brief")
            if isinstance(brief, Mapping) and brief.get("reaction_kind"):
                return str(brief["reaction_kind"]), brief.get("reaction_cast_id")
    return None, None


def beat_expression(
    spine: Mapping[str, Any],
    beat: Mapping[str, Any],
    names: Mapping[str, str] | None = None,
) -> str | None:
    """The beat's expression as a short stage direction, or ``None`` (server ``beat_expression``).

    Parameters
    ----------
    spine
        The desk's story (``api/spine.json``).
    beat
        One of its beats.
    names
        ``cast_id`` to name, when the caller already built it.

    Returns
    -------
    str | None
        For example "Mina stares, stunned"; ``None`` when the beat names no face.
    """

    names = names if names is not None else _cast_names(spine)
    frame_kind, frame_face = _anchor_reaction(spine, beat)
    intent = str(beat.get("motion_intent") or "")
    tag = _EXPRESSION_TAIL.search(intent)
    kind = beat.get("reaction_kind") or frame_kind
    tag_face = _clean(tag.group("face")) if tag and tag.group("face") else None
    if tag_face:
        tag_face = _shorten_names(tag_face, _full_to_short(spine))
    if kind is None and tag is not None:
        kind = tag.group("kind").lower()
    if kind not in EXPRESSION_DIRECTIONS:
        return None
    direction = beat.get("motion_direction")
    subject = (
        direction.get("subject_cast_id") if isinstance(direction, Mapping) else None
    )
    lines = [
        line for line in beat.get("dialogue_lines") or [] if isinstance(line, Mapping)
    ]
    face = (
        (names.get(str(frame_face)) if frame_face else None)
        or tag_face
        or (names.get(str(subject)) if subject else None)
        or (names.get(str(lines[0].get("cast_id") or "")) if lines else None)
    )
    if not face:
        return None
    return _sentence_case(EXPRESSION_DIRECTIONS[str(kind)].format(name=face))


def _take_groups(
    beats: Sequence[Mapping[str, Any]], pattern: Sequence[int]
) -> list[list[Mapping[str, Any]]]:
    groups: list[list[Mapping[str, Any]]] = []
    cursor = 0
    for count in pattern:
        if cursor >= len(beats):
            break
        groups.append(list(beats[cursor : cursor + int(count)]))
        cursor += int(count)
    if cursor < len(beats):
        if groups:
            groups[-1].extend(beats[cursor:])
        else:
            groups.append(list(beats[cursor:]))
    return groups


def pitch_takes(spine: Mapping[str, Any], episode_id: str) -> list[dict[str, Any]]:
    """The episode's shots, grouped by take, as the app's pitch card shows them.

    Parameters
    ----------
    spine
        The desk's story (``api/spine.json``).
    episode_id
        The episode.

    Returns
    -------
    list[dict[str, Any]]
        ``{"take": n, "shots": [{"shot", "camera", "scene", "line", "expression",
        "opens", "ends_on"}]}`` per take; empty while the episode has no beats.
    """

    beats = sorted(
        (
            beat
            for beat in spine.get("beats") or []
            if isinstance(beat, Mapping) and beat.get("episode_id") == episode_id
        ),
        key=lambda beat: int(beat.get("ordinal") or 0),
    )
    if not beats:
        return []
    names = _cast_names(spine)
    pairs = _full_to_short(spine)
    takes: list[dict[str, Any]] = []
    number = 0
    pattern = spine.get("beats_per_storyboard_set") or []
    for take_index, group in enumerate(_take_groups(beats, pattern), start=1):
        shots: list[dict[str, Any]] = []
        for beat in group:
            rows = _beat_rows(beat) or [
                _row_from_text(
                    str(beat.get("motion_intent") or ""),
                    speaking=bool(beat.get("dialogue_lines")),
                )
            ]
            for row_index, (camera, scene) in enumerate(rows):
                number += 1
                first = row_index == 0
                shots.append(
                    {
                        "shot": number,
                        "camera": camera[:80],
                        "scene": _shorten_names(scene, pairs)[:400],
                        "line": _beat_line(beat, names) if first else None,
                        "expression": beat_expression(spine, beat, names)
                        if first
                        else None,
                        "opens": False,
                        "ends_on": False,
                    }
                )
        takes.append({"take": take_index, "shots": shots})
    takes[0]["shots"][0]["opens"] = True
    takes[-1]["shots"][-1]["ends_on"] = True
    return takes


def pitch_digest(takes: Sequence[Mapping[str, Any]], world_rules: Sequence[str]) -> str:
    """The identity of a card's content, as the server computes it (``sha256:<hex>``).

    Parameters
    ----------
    takes
        The card's takes (:func:`pitch_takes`).
    world_rules
        The card's world rules.

    Returns
    -------
    str
        Changes whenever anything the card shows changes.
    """

    payload = {"takes": list(takes), "world_rules": list(world_rules)}
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def shot_lines(takes: Sequence[Mapping[str, Any]], *, indent: str = "  ") -> list[str]:
    """The shots as ``pitch`` prints them: one block per take, one line per shot.

    Parameters
    ----------
    takes
        The card's takes (:func:`pitch_takes`).
    indent
        Leading spaces.

    Returns
    -------
    list[str]
        Printable lines; empty when there are no takes.
    """

    if not takes:
        return []
    count = sum(len(take["shots"]) for take in takes)
    out = [f"{indent}Shots, by take ({count}; what an app creator approves):"]
    for take in takes:
        out.append(f"{indent}  Take {take['take']}")
        for shot in take["shots"]:
            flag = (
                " [opens]"
                if shot["opens"]
                else (" [ends on]" if shot["ends_on"] else "")
            )
            out.append(
                f"{indent}    {shot['shot']}. {shot['camera']}{flag}: {shot['scene']}"
            )
            if shot["line"]:
                out.append(
                    f'{indent}       {shot["line"]["speaker"]}: "{shot["line"]["text"]}"'
                )
            if shot["expression"]:
                out.append(f"{indent}       ({shot['expression']})")
    return out


__all__ = [
    "ANGLES",
    "CAMERA_TAG_JOIN",
    "EXPRESSION_DIRECTIONS",
    "SCENE_MAX_CHARS",
    "SHOT_SIZES",
    "beat_expression",
    "camera_tag",
    "pitch_digest",
    "pitch_takes",
    "shot_lines",
]
