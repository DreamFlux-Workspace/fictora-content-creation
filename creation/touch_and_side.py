"""Free checks before a paid draw: a touch with no owner, a one-sided feature with no side.

Three Payments Late (2026-10-05 and 10-06) paid two redraws ($0.30 each) for
faults the words never ruled out:

* 10-06 t1 v1, row 2: on a reverse angle, a black gripping hand came in from
  behind Noor, attached to no one. The frame said "Dez clamps her wrist" and
  never said which of his hands, where his body was, or which side of the
  frame his arm came from.
* 10-05: Noor's single chrome forearm came back on both arms; the card said
  "chrome forearm" in places without the side.

These are warnings, never stops: the server (fictora-drama
``frame_drawing_rules`` / ``cast_drawing_anchors``) writes the owner line, the
side and the bans itself when it draws. The free edit makes them exact, and on
a server without that change it is the only thing that does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from creation.spine_view import _row_number, frames_by_set, spine_cast_names

#: Verbs that put a hand on someone or something.
_CONTACT = re.compile(
    r"\b(?:grab|grabs|grabbed|grabbing|grip|grips|gripped|gripping|clamp|clamps|clamped|clamping|"
    r"clutch(?:es|ed|ing)?|seiz(?:e|es|ed|ing)|hold|holds|holding|held|touch(?:es|ed|ing)?|"
    r"pull(?:s|ed|ing)?|yank(?:s|ed|ing)?|tug(?:s|ged|ging)?|squeez(?:e|es|ed|ing)|pin(?:s|ned|ning)?|"
    r"restrain(?:s|ed|ing)?|shov(?:e|es|ed|ing)|wrench(?:es|ed|ing)?|drag(?:s|ged|ging)?|"
    r"snatch(?:es|ed|ing)?)\b",
    re.IGNORECASE,
)
#: What follows a contact verb when nothing is touched ("holds still", "pulls back").
_NOT_A_TOUCH = re.compile(
    r"\s+(?:still|steady|firm|back|out|off|away|apart|breath|(?:her|his|their)\s+(?:breath|gaze|ground|nerve)|"
    r"gaze|eye\s+contact|position|silence|focus|composure)\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(
    r"\b(?:without|never|not|no\s+longer)\s+(?:\w+\s+)?$", re.IGNORECASE
)
_CLAUSE = re.compile(r"[.;,]|\s(?:while|as|then|before|after|until)\s", re.IGNORECASE)
#: The toucher's hand with its side: "right hand", "matte-black left fist".
_HAND_SIDE = re.compile(
    r"\b(?:left|right)\s+(?:[\w-]+\s+)?(?:hands?|arms?|forearms?|fists?|fingers|palms?)\b",
    re.IGNORECASE,
)
#: Where the arm comes from: a frame side, or the arm joined to the body.
_ENTRY_SIDE = re.compile(
    r"\b(?:frame|screen)[\s-](?:left|right)\b|\b(?:left|right)\s+(?:of\s+)?(?:the\s+)?frame\b|"
    r"\b(?:connected|attached|joined|joining)\b[^.;]{0,60}\b(?:shoulder|body)\b",
    re.IGNORECASE,
)


#: A body part or clothes of the other person: "her wrist", "his collar".
_ON_SOMEONE = re.compile(
    r"\b(her|his|their|him|them)\s+(?:[\w-]+\s+)?(?:wrist|arm|forearm|hand|collar|shoulder|throat|neck|sleeve|"
    r"jacket|coat)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UnownedTouch:
    """One frame where someone's hand touches someone, with no stated hand or side."""

    set_index: int
    frame: Mapping[str, Any]
    #: Index of the toucher's ``subject_blocking`` entry.
    index: int
    who: str
    words: str
    missing: tuple[str, ...]


def _brief(frame: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = frame.get("visual_brief")
    return raw if isinstance(raw, Mapping) else {}


def _sex(card: Mapping[str, Any] | None) -> str | None:
    brief = card.get("visual_brief") if isinstance(card, Mapping) else None
    return (
        str(brief.get("gender_presentation"))
        if isinstance(brief, Mapping) and brief.get("gender_presentation")
        else None
    )


def _first(name: str) -> str:
    return name.strip().split()[0] if name.strip() else name


def _touch_clauses(text: str) -> list[tuple[str, str]]:
    """Each clause that puts a hand on something, split at its verb: ``(before, clause)``."""

    found: list[tuple[str, str]] = []
    for clause in _CLAUSE.split(text or ""):
        for verb in _CONTACT.finditer(clause):
            before, after = clause[: verb.start()], clause[verb.end() :]
            if _NEGATED.search(before) or _NOT_A_TOUCH.match(after):
                continue
            found.append((before, clause.strip()))
            break
    return found


def _named_last(text: str, names: Sequence[str]) -> str | None:
    """The name said last in ``text`` (the subject nearest a verb), or ``None``."""

    best: tuple[int, str] | None = None
    for name in names:
        for match in re.finditer(
            rf"\b{re.escape(_first(name))}\b", text, re.IGNORECASE
        ):
            if best is None or match.start() > best[0]:
                best = (match.start(), name)
    return best[1] if best else None


def unowned_touches(
    frames: Sequence[Mapping[str, Any]],
    *,
    set_index: int,
    cast_names: Mapping[str, str],
    cards: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[UnownedTouch]:
    """Frames where a staged person touches someone else with no stated hand or arm side.

    Parameters
    ----------
    frames
        One board's frames.
    set_index
        The board (take) number.
    cast_names
        ``cast_id`` to display name.
    cards
        Cast cards by ``cast_id``, to read "her wrist" as the toucher's own or
        someone else's from the card's ``gender_presentation``.

    Returns
    -------
    list[UnownedTouch]
        One entry per toucher whose pose, interaction or the story moment's
        clause about them names a contact verb toward another person in the
        frame, when their words do not say which hand (``right hand``) or
        where it comes from (``frame right``, ``joined to his shoulder``).
        Frames with one person are left alone.
    """

    cards = cards or {}
    found: list[UnownedTouch] = []
    for frame in frames:
        brief = _brief(frame)
        blocking = [
            entry
            for entry in brief.get("subject_blocking") or []
            if isinstance(entry, Mapping)
        ]
        if len(blocking) < 2:
            continue
        names = [
            cast_names.get(str(entry.get("cast_id")), str(entry.get("cast_id")))
            for entry in blocking
        ]
        moment = str(brief.get("story_moment") or "")
        sexes = [_sex(cards.get(str(entry.get("cast_id")))) for entry in blocking]
        for index, entry in enumerate(blocking):
            who = names[index]
            own_word = {"female": "her", "male": "his"}.get(sexes[index])
            others = [_first(name) for i, name in enumerate(names) if i != index]
            own = "; ".join(
                str(entry.get(key) or "") for key in ("pose", "interaction")
            )
            clauses = [
                clause
                for before, clause in _touch_clauses(own)
                if _named_last(before, names) in (None, who)
            ]
            # A story-moment clause is theirs when their name is the last one before the verb.
            clauses += [
                clause
                for before, clause in _touch_clauses(moment)
                if _named_last(before, names) == who
            ]
            touching = [
                clause
                for clause in clauses
                if any(
                    re.search(rf"\b{re.escape(other)}\b", clause, re.IGNORECASE)
                    for other in others
                )
                or any(
                    match.group(1).casefold() != own_word
                    for match in _ON_SOMEONE.finditer(clause)
                )
            ]
            if not touching:
                continue
            said = f"{own} {moment} {entry.get('frame_position') or ''}"
            missing = tuple(
                label
                for label, pattern in (
                    ("which hand", _HAND_SIDE),
                    ("which side of the frame it comes from", _ENTRY_SIDE),
                )
                if pattern.search(said) is None
            )
            if missing:
                found.append(
                    UnownedTouch(
                        set_index=set_index,
                        frame=frame,
                        index=index,
                        who=who,
                        words=touching[0],
                        missing=missing,
                    )
                )
    return found


def unowned_touch_lines(
    frames: Sequence[Mapping[str, Any]],
    *,
    cast_names: Mapping[str, str],
    cards: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """The board gate's shot-list lines for :func:`unowned_touches` on one board.

    Parameters
    ----------
    frames
        One board's frames.
    cast_names
        ``cast_id`` to display name.
    cards
        Cast cards by ``cast_id``.

    Returns
    -------
    list[str]
        One ``!!`` line per toucher: look at that row for a hand attached to no
        one. Empty when every touch says whose hand and from where.
    """

    return [
        f'  !! row {_row_number(hit.frame)}: {hit.who} "{hit.words}" does not say {" or ".join(hit.missing)}: '
        "look for a hand or arm attached to no one (warning only)"
        for hit in unowned_touches(
            frames, set_index=1, cast_names=cast_names, cards=cards
        )
    ]


def _cards(spine: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(card.get("cast_id")): card
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    }


def spine_cards(spine: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Cast cards by ``cast_id``.

    Parameters
    ----------
    spine
        Spine JSON.

    Returns
    -------
    dict[str, Mapping[str, Any]]
        Every card that has a ``cast_id``.
    """

    return _cards(spine)


def touch_owner_heads_up(
    spine: Mapping[str, Any],
    *,
    episode: int,
    desk: str,
    sets: Sequence[int] | None = None,
) -> str | None:
    """The free heads-up before a paid board draw when a touch names no hand or side.

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

    Returns
    -------
    str | None
        ``!!`` lines naming each take, frame, row and toucher with the edit that
        fixes it; ``None`` when every touch says whose hand and from where.
        Warning only: nothing is stopped.
    """

    cast_names = spine_cast_names(spine)
    cards = _cards(spine)
    findings: list[UnownedTouch] = []
    for set_index, frames in sorted(frames_by_set(spine, episode=episode).items()):
        if sets is not None and set_index not in sets:
            continue
        findings += unowned_touches(
            frames, set_index=set_index, cast_names=cast_names, cards=cards
        )
    if not findings:
        return None
    lines = [
        "!! Before the boards (warning only, nothing stopped): a touch that does not say whose hand it is and "
        "where it comes from can be drawn as a hand attached to no one (Three Payments Late t1, a $0.30 "
        "redraw). Say which hand, where the person's body is, and the side of the frame the arm comes from "
        "(free edit):"
    ]
    for hit in findings:
        frame_id = str(hit.frame.get("frame_id"))
        lines.append(
            f'  !! ep{episode:02d} t{hit.set_index} {frame_id} (row {_row_number(hit.frame)}): {hit.who} "{hit.words}" '
            f"does not say {' or '.join(hit.missing)}"
        )
        lines.append(
            f"    fictora-produce edit --desk {desk} --episode {episode} --frame {frame_id} "
            f"--set subject_blocking.{hit.index}.interaction=\"…; {_first(hit.who)}'s right hand reaches from "
            f'frame right, the arm joined to the shoulder"'
        )
    lines.append(
        "  The server adds an owner line when it draws (fictora-drama); the edit makes it exact."
    )
    return "\n".join(lines)


#: A feature that sits on one side of the body.
_FEATURE = re.compile(
    r"\b(?:prosthetic|cybernetic|chrome|metal|metallic|robotic|bionic|mechanical|scar|scars|scarred|burn|"
    r"burned|tattoo|tattoos|tattooed|eye[\s-]?patch|earring|piercing|bandage|bandaged|cast|sling|missing|"
    r"amputated)\b",
    re.IGNORECASE,
)
_PAIRED_PART = re.compile(
    r"\b(?:arm|arms|forearm|forearms|hand|hands|wrist|wrists|leg|legs|knee|knees|foot|feet|eye|eyes|ear|ears|"
    r"cheek|cheeks|brow|brows|eyebrow|eyebrows|shoulder|shoulders|temple|temples)\b",
    re.IGNORECASE,
)
#: A feature that names its own part: an eyepatch is on an eye, an earring in an ear.
_IMPLIED_PART = re.compile(
    r"\b(?:eye[\s-]?patch|single\s+earring|one\s+earring)\b", re.IGNORECASE
)
_SIDE = re.compile(r"\b(?:left|right)\b", re.IGNORECASE)
_BOTH = re.compile(r"\b(?:both|two|pair|no|not|never|without)\b", re.IGNORECASE)
_CARD_CLAUSE = re.compile(
    r"[,;]|\band\b|\bwhile\b|\bbut\b|\bcontrasts?\s+with\b", re.IGNORECASE
)


def one_sided_side_lines(spine: Mapping[str, Any]) -> list[str]:
    """Warn when a cast card puts a one-sided feature on a body part without naming the side.

    Parameters
    ----------
    spine
        Spine JSON.

    Returns
    -------
    list[str]
        One line per card clause like "chrome forearm" or "a scar across his
        cheek" with no left or right: the plate can draw it on either side, or
        both (Three Payments Late 10-05: two chrome arms, a $0.30 redraw).
        Empty when every such feature names its side.
    """

    lines: list[str] = []
    for card in spine.get("cast") or []:
        if not isinstance(card, Mapping):
            continue
        brief = card.get("visual_brief")
        if not isinstance(brief, Mapping):
            continue
        texts: list[str] = []
        for key in ("face_anchors", "hair_anchors", "wardrobe_anchors"):
            texts += [str(item) for item in brief.get(key) or [] if item]
        texts.append(str(brief.get("silhouette") or ""))
        name = str(card.get("name") or card.get("cast_id") or "the character")
        for text in texts:
            for clause in _CARD_CLAUSE.split(text):
                if (
                    _FEATURE.search(clause) is None
                    or _SIDE.search(clause)
                    or _BOTH.search(clause)
                ):
                    continue
                if (
                    _PAIRED_PART.search(clause) is None
                    and _IMPLIED_PART.search(clause) is None
                ):
                    continue
                lines.append(
                    f'!! {name}: "{clause.strip()}" names no side. Write the character\'s own side on the card '
                    'before the plate ("her own left forearm"), or it can be drawn on the wrong side or '
                    "on both (warning only)."
                )
    return list(dict.fromkeys(lines))


__all__ = [
    "UnownedTouch",
    "spine_cards",
    "unowned_touch_lines",
    "one_sided_side_lines",
    "touch_owner_heads_up",
    "unowned_touches",
]
