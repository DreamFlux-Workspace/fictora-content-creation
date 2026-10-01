"""Give a cast member a look.

Production learnings, 22 Sep to 1 Oct 2026:

- **A look for someone who has none** (L-20261001-23, L-20261001-103). Sam was
  added with ``line --add --new-voice``: a voice card, no visual brief. A later
  episode put him in a frame, and ``redraw-plate --cast Sam`` was refused
  ``cast_visual_brief_missing``; the operator hand-built the ``cast_card``
  cascade. ``cast --desk D --name X --look …`` sends that edit through the
  kit's session: ``PATCH /v1/spines/{id}`` with ``cast[]`` before the script
  gate, the ``cast_card`` cascade edit (``POST …/cascade/preview`` +
  ``…/execute``) after it. The look is written in the shape every cast brief
  has (``DramaCastVisualBrief``); the drawing style (medium, surface, palette,
  reference light and background) is the show's, read from a cast card that
  already has a look.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from creation import episode_commands as ec
from creation.cli_text import TextArgError, text_or_file
from creation.desk_media_urls import drawn_cast_rows
from creation.orchestrate import save_spine_snapshot

#: A look that names no expression, gaze or posture gets these (printed with the change).
DEFAULT_EXPRESSION = "neutral, composed"
DEFAULT_GAZE = "steady, toward the viewer"
DEFAULT_POSTURE = "relaxed, upright"

#: The show's drawing style: taken from a cast card that has a look when the new look leaves it out.
STYLE_FIELDS = (
    "visual_medium",
    "surface_treatment",
    "palette",
    "reference_lighting",
    "reference_background",
)
#: What every look must say about the character themself.
OWN_FIELDS = (
    "age_band",
    "face_anchors",
    "hair_anchors",
    "silhouette",
    "wardrobe_anchors",
)
LIST_FIELDS = frozenset(
    {
        "face_anchors",
        "hair_anchors",
        "wardrobe_anchors",
        "palette",
        "forbidden_elements",
    }
)
#: ``key:`` words a look file may use, and the ``DramaCastVisualBrief`` field each one fills.
LOOK_KEYS: dict[str, str] = {
    "age": "age_band",
    "age_band": "age_band",
    "gender": "gender_presentation",
    "gender_presentation": "gender_presentation",
    "face": "face_anchors",
    "face_anchors": "face_anchors",
    "hair": "hair_anchors",
    "hair_anchors": "hair_anchors",
    "silhouette": "silhouette",
    "build": "silhouette",
    "wardrobe": "wardrobe_anchors",
    "clothes": "wardrobe_anchors",
    "wardrobe_anchors": "wardrobe_anchors",
    "expression": "default_expression",
    "default_expression": "default_expression",
    "gaze": "default_gaze",
    "default_gaze": "default_gaze",
    "posture": "default_posture",
    "default_posture": "default_posture",
    "palette": "palette",
    "never": "forbidden_elements",
    "forbidden": "forbidden_elements",
    "forbidden_elements": "forbidden_elements",
    "medium": "visual_medium",
    "visual_medium": "visual_medium",
    "surface": "surface_treatment",
    "surface_treatment": "surface_treatment",
    "lighting": "reference_lighting",
    "reference_lighting": "reference_lighting",
    "background": "reference_background",
    "reference_background": "reference_background",
}
_DESCRIPTION_KEYS = frozenset({"description", "visual_description", "look"})

LOOK_TEMPLATE = (
    "age: 50s\ngender: male\nface: long face; grey stubble\nhair: short grey crew cut\n"
    "silhouette: tall, stooped\nwardrobe: navy uniform jacket; brass badge\n"
    "[expression: …]\n[gaze: …]\n[posture: …]\n[never: …]\n"
    "One line of who they are and how they look."
)
LOOK_HELP = (
    "How the character looks, as `key: value` lines (age, gender, face, hair, silhouette, wardrobe; optional "
    "expression, gaze, posture, palette, never; lists split on ';'; any other line is the description) or a "
    "JSON cast visual brief. The drawing style is taken from a cast member who has a look. Text, or @FILE."
)


# --- The look ----------------------------------------------------------------------------------


def _one_line(value: Any) -> str:
    return " ".join(str(value).split())


def _as_list(value: Any) -> list[str]:
    items = value if isinstance(value, list) else str(value).split(";")
    return [_one_line(item) for item in items if _one_line(item)]


def parse_look(raw: str, *, flag: str = "--look") -> tuple[str | None, dict[str, Any]]:
    """Read a look: ``key: value`` lines or JSON, into a description and ``DramaCastVisualBrief`` fields.

    Parameters
    ----------
    raw
        What the operator passed (words, ``@FILE`` or a file path).
    flag
        The flag's name, for messages.

    Returns
    -------
    tuple[str | None, dict[str, Any]]
        ``visual_description`` (``None`` when the look gives none) and the brief fields it sets.

    Raises
    ------
    ec.CommandStopped
        The file cannot be read, the JSON is not an object, or a field is unknown.
    """

    try:
        text = text_or_file(raw, flag=flag)
    except TextArgError as exc:
        raise ec.CommandStopped(str(exc)) from None
    if not text:
        raise ec.CommandStopped(f"{flag} is empty. A look reads like:\n{LOOK_TEMPLATE}")
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ec.CommandStopped(f"{flag}: not valid JSON ({exc})") from None
        if not isinstance(data, dict):
            raise ec.CommandStopped(f"{flag}: the JSON must be an object")
        description = data.get("visual_description")
        fields = (
            data.get("visual_brief")
            if isinstance(data.get("visual_brief"), dict)
            else {k: v for k, v in data.items() if k != "visual_description"}
        )
        unknown = sorted(
            k for k in fields if LOOK_KEYS.get(k) != k and k != "wardrobe_variants"
        )
        if unknown:
            raise ec.CommandStopped(
                f"{flag}: not cast visual brief fields: {', '.join(unknown)}"
            )
        brief = {
            key: (_as_list(value) if key in LIST_FIELDS else value)
            for key, value in fields.items()
        }
        return (_one_line(description) if description else None), brief
    brief: dict[str, Any] = {}
    prose: list[str] = []
    for row in text.splitlines():
        key, sep, value = row.partition(":")
        field = LOOK_KEYS.get(key.strip().lower()) if sep else None
        if sep and key.strip().lower() in _DESCRIPTION_KEYS:
            prose.append(value)
        elif field is None:
            prose.append(row)
        elif field in LIST_FIELDS:
            brief[field] = [*brief.get(field, []), *_as_list(value)]
        else:
            brief[field] = _one_line(value)
    description = _one_line(" ".join(prose))
    return (description or None), brief


def _style_source(spine: Mapping[str, Any], cast_id: str) -> Mapping[str, Any]:
    for card in spine.get("cast") or []:
        if (
            isinstance(card, Mapping)
            and card.get("cast_id") != cast_id
            and isinstance(card.get("visual_brief"), Mapping)
        ):
            return card["visual_brief"]
    return {}


def look_patch(
    spine: Mapping[str, Any], card: Mapping[str, Any], raw: str
) -> tuple[dict[str, Any], list[str]]:
    """Build the ``DramaCastCardPatch`` that gives ``card`` the look ``raw`` describes.

    The brief starts from the card's own brief when it has one (a partial look
    edits it), else from the show's drawing style (another cast card's
    ``STYLE_FIELDS``) and the default expression, gaze and posture. A
    description left out is written from the anchors.

    Parameters
    ----------
    spine
        Spine JSON (for the show's drawing style).
    card
        The cast card (``cast_id``, ``name``, maybe ``visual_brief``); it need not be on the spine yet.
    raw
        The ``--look`` value.

    Returns
    -------
    tuple[dict[str, Any], list[str]]
        ``{cast_id, visual_description, visual_brief}`` and one printable line per changed field.

    Raises
    ------
    ec.CommandStopped
        A field the look must give is missing or blank; nothing was sent.
    """

    description, fields = parse_look(raw)
    cast_id = str(card["cast_id"])
    own = card.get("visual_brief")
    if isinstance(own, Mapping):
        brief = copy.deepcopy(dict(own))
    else:
        style = _style_source(spine, cast_id)
        brief = {key: copy.deepcopy(style[key]) for key in STYLE_FIELDS if key in style}
        brief.update(
            {
                "default_expression": DEFAULT_EXPRESSION,
                "default_gaze": DEFAULT_GAZE,
                "default_posture": DEFAULT_POSTURE,
                "forbidden_elements": [],
            }
        )
    brief.update(fields)
    missing = [
        key
        for key in (*OWN_FIELDS, *STYLE_FIELDS)
        if not brief.get(key) or (key in LIST_FIELDS and not _as_list(brief[key]))
    ]
    if missing:
        words = {v: k for k, v in reversed(list(LOOK_KEYS.items()))}
        named = ", ".join(f"{words.get(key, key)} ({key})" for key in missing)
        style_note = (
            " No cast member has a look yet, so the drawing style must be given too (medium, surface, palette, "
            "lighting, background)."
            if any(key in STYLE_FIELDS for key in missing)
            else ""
        )
        raise ec.CommandStopped(
            f"the look for {card.get('name') or cast_id} is missing: {named}.{style_note} Nothing was sent. "
            f"A look reads like:\n{LOOK_TEMPLATE}"
        )
    if not description:
        description = _one_line(
            f"{brief['age_band']}, {brief['silhouette']}; {', '.join(brief['face_anchors'])}; "
            f"{', '.join(brief['hair_anchors'])}; wears {', '.join(brief['wardrobe_anchors'])}"
        )
    patch = {
        "cast_id": cast_id,
        "visual_description": description,
        "visual_brief": brief,
    }
    before = {
        "visual_description": card.get("visual_description"),
        "visual_brief": card.get("visual_brief") or {},
    }
    changed = ec._changes(before, {k: v for k, v in patch.items() if k != "cast_id"})
    return patch, changed


def find_card(spine: Mapping[str, Any], who: str) -> Mapping[str, Any]:
    """The cast card named ``who`` (name or ``cast_id``, any case)."""

    wanted = who.strip().casefold()
    cards = [
        c
        for c in spine.get("cast") or []
        if isinstance(c, Mapping) and c.get("cast_id")
    ]
    for card in cards:
        if wanted in {
            str(card["cast_id"]).casefold(),
            str(card.get("name") or "").casefold(),
        }:
            return card
    names = ", ".join(f"{c.get('name')} ({c['cast_id']})" for c in cards)
    raise ec.CommandStopped(f"no character {who!r} on the story; the cast is: {names}")


def _send_look(
    desk: Path,
    patch: dict[str, Any],
    *,
    episode: int,
    select_regen: bool,
    preview_only: bool,
    out: Any,
) -> tuple[dict[str, Any] | None, bool]:
    """Send one cast card patch: ``PATCH`` with ``cast[]`` before the script gate, the ``cast_card`` cascade after.

    Returns
    -------
    tuple[dict[str, Any] | None, bool]
        The fresh spine (``None`` on a preview) and whether the cascade ran.
    """

    desk, state, run = ec._desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        cascade = spine.get("approval_state") == "approved"
        if preview_only and not cascade:
            print("(preview only: nothing was sent)", file=out)
            return None, False
        try:
            if not cascade:
                try:
                    answer = run.patch(
                        f"/v1/spines/{state.spine_id}",
                        {
                            "spine_version": spine["spine_version"],
                            "patch": {"cast": [patch]},
                        },
                    )
                    ec.say_patch_warnings(spine, answer, out=out)
                except SystemExit as exc:
                    if "cascade_required" not in str(exc.code):
                        raise ec.CommandStopped(str(exc.code)) from None
                    cascade = True
                    print(
                        "(the server asks for a cascade: the script is approved there)",
                        file=out,
                    )
            if cascade:
                ec._run_cascade(
                    desk, run, spine, {}, episode=episode, select_regen=select_regen, preview_only=preview_only,
                    out=out,
                    edit={"scope": "field", "target_type": "cast_card", "target_id": patch["cast_id"], "patch": patch},
                )  # fmt: skip
        except ec.CommandStopped as exc:
            raise ec.CommandStopped(
                ec.explain_refusal(str(exc), spine, episode=episode)
            ) from None
        if preview_only:
            return None, cascade
        return run.spine(state.spine_id or ""), cascade
    finally:
        run.client.close()


def _has_plates(spine: Mapping[str, Any]) -> bool:
    return any(
        isinstance(a, Mapping)
        and a.get("relation_type") == "cast_card"
        and a.get("url")
        for a in spine.get("media_assets") or []
    )


def plate_next_step(
    desk: Path, spine: Mapping[str, Any], card: Mapping[str, Any]
) -> str:
    """What to do after a character gets a look: their plate, or nothing while they are heard only."""

    name = str(card.get("name") or card["cast_id"])
    drawn = {str(row["cast_id"]) for row in drawn_cast_rows(dict(spine))}
    if str(card["cast_id"]) not in drawn:
        return (
            f"{name} is heard only for now: no plate is drawn. When a frame or an on-screen line shows them, draw "
            f'their plate: fictora-produce redraw-plate --desk {desk} --cast "{name}" --note "first drawing from '
            'the new look" ($0.30).'
        )
    if not _has_plates(spine):
        return f"next: {name}'s plate is drawn with the others at the plates step (`fictora-produce step`)."
    return (
        f'next: fictora-produce redraw-plate --desk {desk} --cast "{name}" --note "first drawing from the new '
        f'look" (draws {name} alone, $0.30; show the contact sheet), then redraw the boards {name} is in.'
    )


def run_cast_look(
    desk: Path,
    *,
    name: str,
    look: str,
    select_regen: bool = False,
    preview_only: bool = False,
    next_step: bool = True,
    out: Any = None,
) -> Path | None:
    """Give one cast member a look (``visual_description`` + ``visual_brief``) on the server.

    The change is printed before it is sent. Before the script gate it is a
    ``PATCH /v1/spines/{id}`` with ``cast[]``; after it, the ``cast_card``
    cascade (paid items off unless ``select_regen``). ``preview_only`` prints
    the change (and, after the gate, the cascade) and changes nothing.

    Parameters
    ----------
    desk
        Series desk with a story.
    name
        The character's name or ``cast_id``.
    look
        The look (:func:`parse_look`).
    select_regen, preview_only
        As ``edit``.
    next_step
        Print the next step (their plate).
    out
        Text stream.

    Returns
    -------
    Path | None
        The refreshed ``api/spine.json``; ``None`` on a preview.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    _, state, run = ec._desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    card = find_card(spine, name)
    patch, changed = look_patch(spine, card, look)
    who = str(card.get("name") or card["cast_id"])
    if not changed:
        print(f"{who} already has this look; nothing to send.", file=out)
        return None
    print(f"look for {who} ({card['cast_id']}):", file=out)
    for row in changed:
        print(row, file=out)
    episode = int(card.get("intro_episode_ordinal") or 1)
    fresh, cascade = _send_look(
        desk,
        patch,
        episode=episode,
        select_regen=select_regen,
        preview_only=preview_only,
        out=out,
    )
    if fresh is None:
        return None
    path = save_spine_snapshot(desk, episode, fresh)
    ec._note(
        desk,
        episode,
        f"cast look: {who} ({card['cast_id']}) {'through the cast cascade' if cascade else 'patched'}: "
        + "; ".join(row.strip() for row in changed),
    )
    if next_step:
        print(
            plate_next_step(desk, fresh, find_card(fresh, str(card["cast_id"]))),
            file=out,
        )
    return path


__all__ = [
    "DEFAULT_EXPRESSION",
    "DEFAULT_GAZE",
    "DEFAULT_POSTURE",
    "LOOK_HELP",
    "find_card",
    "look_patch",
    "parse_look",
    "plate_next_step",
    "run_cast_look",
]
