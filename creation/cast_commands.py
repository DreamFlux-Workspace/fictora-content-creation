"""Give a cast member a look, put a new character on screen in one step, and pin lines on a mis-labelled show.

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
- **A new character on screen in one command** (L-20261001-19). The server
  only creates a cast card through ``add_voice_only_cast``, and a frame may not
  stage someone the spine *before* the patch only hears
  (``spine_frame_cast_edits``: "put one of their lines on screen in another
  patch first"). So ``line --add --new-character`` runs four edits in order,
  each through the same PATCH-or-cascade path as ``line``: add the voice with
  its line (off screen), give them a look, put the line on screen with them as
  the beat's motion subject, then stage them on the beat's frame
  (``subject_blocking`` and ``cast_refs`` together). Everything the kit can
  check is checked before the first edit. Each step is skipped when the story
  already has it, so a run stopped half way is finished by running the same
  command again; the stop names what was done and how to undo it.
- **A show voiced in Japanese but drafted en-US** (L-20261001-21, -28). The
  server refuses ``spoken_text`` on an en-US show. ``language --desk D --spoken
  ja`` changes the show's language (:mod:`creation.story_setup`, fictora-drama
  #582); until then ``line --spoken`` with ``--language ja|ko`` (or performed
  words that are not English) records the declared language and the pinned line
  on the desk (``shared/spoken-language.json``), sends nothing, and names the
  ``language`` command. ``language`` prints the recorded lines as the
  ``line --spoken`` commands that send them.
- **An edited look keeps the bans and the description** (L-20261005-13 Last
  Call, L-20261006-13 Not Home). ``never:`` replaced the card's whole
  ``forbidden_elements`` (the bans the story was drafted with went), and a
  description line replaced the card's own. ``never:`` now adds to the list
  (case-insensitive dedupe, order kept), ``never-remove:`` takes a ban off by
  name, and a description line is added to the card's own description unless
  ``--replace-description`` is given. The change prints what was kept, added
  and removed before anything is sent.
- **A guest who has left** (Noodle24, 8 Oct 2026). A guest-per-arc show hit
  the old cap of four characters a story. A series now holds up to 50, and
  ``cast-exit --desk D --cast NAME --after-episode N`` marks someone as gone
  (``exit_episode_ordinal`` on their card) so the next episode's writers see
  one line for them instead of their whole card; ``--clear`` brings them back.
  Same PATCH-or-cascade path as ``cast --look``, paid items always off.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from creation import episode_commands as ec
from creation.captions import is_english
from creation.cli_text import TextArgError, text_or_file
from creation.desk_media_urls import drawn_cast_rows
from creation.orchestrate import save_spine_snapshot
from creation.spine_view import episode_id_for

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
#: ``key:`` words (and the JSON key) that take bans off the card's never-draw list by name.
NEVER_REMOVE = "never_remove"
_NEVER_REMOVE_KEYS = frozenset({"never-remove", "never_remove", "unban"})

LOOK_TEMPLATE = (
    "age: 50s\ngender: male\nface: long face; grey stubble\nhair: short grey crew cut\n"
    "silhouette: tall, stooped\nwardrobe: navy uniform jacket; brass badge\n"
    "[expression: …]\n[gaze: …]\n[posture: …]\n[never: … (added to the card's bans)]\n"
    "[never-remove: … (a ban to take off, by its words)]\n"
    "One line of who they are and how they look."
)
LOOK_HELP = (
    "How the character looks, as `key: value` lines (age, gender, face, hair, silhouette, wardrobe; optional "
    "expression, gaze, posture, palette, never; lists split on ';'; any other line is the description) or a "
    "JSON cast visual brief. `never:` adds to the card's bans and `never-remove:` takes one off; a description "
    "line is added to the card's own description (--replace-description replaces it). The drawing style is "
    "taken from a cast member who has a look. Text, or @FILE."
)
STAGING_HELP = (
    "With --new-character on a drawn beat: how they stand in the beat's frame, as JSON or `key=value; …` "
    "(frame_position, pose, gaze, interaction)."
)
STAGING_FIELDS = ("frame_position", "pose", "gaze", "interaction")

#: ``--language`` values and the spine codes they mean.
LANGUAGES = {
    "ja": "ja-JP",
    "ja-jp": "ja-JP",
    "ko": "ko-KR",
    "ko-kr": "ko-KR",
    "en": "en-US",
    "en-us": "en-US",
}
LANGUAGE_RECORD = Path("shared") / "spoken-language.json"


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
        ``visual_description`` (``None`` when the look gives none) and the brief fields it sets, plus
        :data:`NEVER_REMOVE` (the bans to take off) when the look names any.

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
            dict(data["visual_brief"])
            if isinstance(data.get("visual_brief"), dict)
            else {k: v for k, v in data.items() if k != "visual_description"}
        )
        if NEVER_REMOVE in data and NEVER_REMOVE not in fields:
            fields[NEVER_REMOVE] = data[NEVER_REMOVE]
        unknown = sorted(
            k
            for k in fields
            if LOOK_KEYS.get(k) != k and k not in {"wardrobe_variants", NEVER_REMOVE}
        )
        if unknown:
            raise ec.CommandStopped(
                f"{flag}: not cast visual brief fields: {', '.join(unknown)}"
            )
        brief = {
            key: (
                _as_list(value) if key in LIST_FIELDS or key == NEVER_REMOVE else value
            )
            for key, value in fields.items()
        }
        return (_one_line(description) if description else None), _with_gender_word(
            brief, flag=flag
        )
    brief: dict[str, Any] = {}
    prose: list[str] = []
    for row in text.splitlines():
        key, sep, value = row.partition(":")
        field = LOOK_KEYS.get(key.strip().lower()) if sep else None
        if sep and key.strip().lower() in _DESCRIPTION_KEYS:
            prose.append(value)
        elif sep and key.strip().lower() in _NEVER_REMOVE_KEYS:
            brief[NEVER_REMOVE] = [*brief.get(NEVER_REMOVE, []), *_as_list(value)]
        elif field is None:
            prose.append(row)
        elif field in LIST_FIELDS:
            brief[field] = [*brief.get(field, []), *_as_list(value)]
        else:
            brief[field] = _one_line(value)
    description = _one_line(" ".join(prose))
    return (description or None), _with_gender_word(brief, flag=flag)


#: Everyday words for the server's two ``gender_presentation`` values.
GENDER_WORDS = {
    "female": "female", "woman": "female", "girl": "female", "f": "female", "she": "female",
    "male": "male", "man": "male", "boy": "male", "m": "male", "he": "male",
}  # fmt: skip


def _with_gender_word(brief: dict[str, Any], *, flag: str) -> dict[str, Any]:
    """Read ``gender: woman`` as the server's ``female`` (it takes only ``female`` or ``male``).

    The canary of 7 Oct 2026 sent ``gender: woman``: the server refused the
    whole look, and a plate was redrawn from the unchanged card ($0.60).
    Anything else stops here, before anything is sent.
    """

    value = brief.get("gender_presentation")
    if value is None:
        return brief
    word = str(value).strip().lower()
    if word not in GENDER_WORDS:
        raise ec.CommandStopped(
            f"{flag}: gender is {value!r}; the server takes female or male "
            "(woman, man, girl and boy work too). Nothing was sent."
        )
    return {**brief, "gender_presentation": GENDER_WORDS[word]}


def _style_source(spine: Mapping[str, Any], cast_id: str) -> Mapping[str, Any]:
    for card in spine.get("cast") or []:
        if (
            isinstance(card, Mapping)
            and card.get("cast_id") != cast_id
            and isinstance(card.get("visual_brief"), Mapping)
        ):
            return card["visual_brief"]
    return {}


_AGE_DIGITS = re.compile(r"\b(1[89]|[2-7]\d|80)\b")


def _description_with_age(description: str, before: Any, after: Any) -> str:
    """The card's description, its stated age moved to the new ``age_band``'s digits when the age changed."""

    old = _AGE_DIGITS.search(str(before or ""))
    new = _AGE_DIGITS.search(str(after or ""))
    if new is None or str(before or "") == str(after or ""):
        return description
    stated = _AGE_DIGITS.search(description)
    if stated is None or stated.group(0) == new.group(0):
        return description
    if old is not None and stated.group(0) != old.group(0):
        return description  # the number in the prose is not the old age: leave it
    return description[: stated.start()] + new.group(0) + description[stated.end() :]


def _ban_key(ban: str) -> str:
    return _one_line(ban).casefold()


def _merged_bans(
    who: str, current: list[str], added: list[str], removed: list[str]
) -> tuple[list[str], list[str]]:
    """The card's bans with ``added`` appended (no repeats, order kept) and ``removed`` taken off.

    Returns
    -------
    tuple[list[str], list[str]]
        The new list and the printable detail rows (kept / added / removed).

    Raises
    ------
    ec.CommandStopped
        A ban to remove is not on the card, or the look both adds and removes the same ban.
    """

    both = sorted({_ban_key(b) for b in added} & {_ban_key(b) for b in removed})
    if both:
        raise ec.CommandStopped(
            f"the look for {who} both adds and removes: {', '.join(both)}. Nothing was sent."
        )
    present = {_ban_key(b) for b in current}
    missing = [b for b in removed if _ban_key(b) not in present]
    if missing:
        has = "; ".join(current) or "no bans"
        raise ec.CommandStopped(
            f"never-remove: {'; '.join(missing)} is not on {who}'s never-draw list (it has: {has}). "
            "Write the ban as the card has it. Nothing was sent."
        )
    gone = {_ban_key(b) for b in removed}
    bans = [b for b in current if _ban_key(b) not in gone]
    seen = {_ban_key(b) for b in bans}
    new_bans: list[str] = []
    for ban in added:
        if _ban_key(ban) not in seen:
            seen.add(_ban_key(ban))
            new_bans.append(ban)
    rows = []
    if new_bans:
        rows.append(f"    never draw, added: {'; '.join(new_bans)}")
    taken = [b for b in current if _ban_key(b) in gone]
    if taken:
        rows.append(f"    never draw, removed (never-remove): {'; '.join(taken)}")
    kept = [b for b in current if _ban_key(b) not in gone]
    if kept and (new_bans or taken):
        rows.append(f"    never draw, kept: {'; '.join(kept)}")
    return [*bans, *new_bans], rows


def _added_description(kept: str, new: str) -> str:
    """``kept`` with ``new`` added after it; nothing of ``kept`` is lost."""

    def bare(text: str) -> str:
        return _one_line(text).rstrip(" .!?").casefold()

    if bare(new) in bare(kept):
        return kept
    if bare(kept) in bare(new):
        return new  # the new words already carry the old ones: an edit that only adds
    joiner = " " if kept.rstrip().endswith((".", "!", "?")) else ". "
    return f"{kept.rstrip()}{joiner}{new}"


def look_patch(
    spine: Mapping[str, Any],
    card: Mapping[str, Any],
    raw: str,
    *,
    replace_description: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Build the ``DramaCastCardPatch`` that gives ``card`` the look ``raw`` describes.

    The brief starts from the card's own brief when it has one (a partial look
    edits it), else from the show's drawing style (another cast card's
    ``STYLE_FIELDS``) and the default expression, gaze and posture. A
    description left out keeps the card's own (its stated age moved to a new
    ``age``), and is written from the anchors only for a card with none.

    Nothing the card already has is dropped without being asked
    (L-20261005-13, L-20261006-13): ``never:`` adds to the card's
    ``forbidden_elements`` (no repeats, order kept), ``never-remove:`` takes a
    ban off by its words, and a description given for a card that has a look
    is added to the card's own description unless ``replace_description``.

    Parameters
    ----------
    spine
        Spine JSON (for the show's drawing style).
    card
        The cast card (``cast_id``, ``name``, maybe ``visual_brief``); it need not be on the spine yet.
    raw
        The ``--look`` value.
    replace_description
        ``--replace-description``: the look's description replaces the card's own.

    Returns
    -------
    tuple[dict[str, Any], list[str]]
        ``{cast_id, visual_description, visual_brief}`` and one printable line per changed field (with
        indented detail rows: the bans kept, added and removed, the description before and after).

    Raises
    ------
    ec.CommandStopped
        A field the look must give is missing or blank, a ban to remove is not on the card, or
        ``replace_description`` without a description; nothing was sent.
    """

    description, fields = parse_look(raw)
    removed = list(fields.pop(NEVER_REMOVE, []))
    added = fields.pop("forbidden_elements", None)
    cast_id = str(card["cast_id"])
    who = str(card.get("name") or cast_id)
    own = card.get("visual_brief")
    has_look = isinstance(own, Mapping)
    if replace_description and not description:
        raise ec.CommandStopped(
            f"--replace-description needs the new description for {who} (a look line that is not "
            "`key: value`). Nothing was sent."
        )
    if has_look:
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
    ban_rows: list[str] = []
    if added is not None or removed:
        brief["forbidden_elements"], ban_rows = _merged_bans(
            who,
            _as_list(brief.get("forbidden_elements") or []),
            list(added or []),
            removed,
        )
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
            f"the look for {who} is missing: {named}.{style_note} Nothing was sent. "
            f"A look reads like:\n{LOOK_TEMPLATE}"
        )
    kept = str(card.get("visual_description") or "").strip()
    description_note = ""
    if has_look and kept and not replace_description:
        # A partial look changes only the fields it names (canary 7 Oct: `age: 28` alone rewrote
        # Mina's description and dropped her ethnicity and hair). The description stays the card's;
        # a new age in digits replaces the one it states, and new words are added after it.
        aged = _description_with_age(kept, own.get("age_band"), brief.get("age_band"))
        if description:
            description = _added_description(aged, description)
            description_note = "    (the card's description is kept and the new words added; --replace-description replaces it)"
        else:
            description = aged
    elif replace_description and has_look and kept:
        description_note = (
            "    (--replace-description: the card's description is replaced)"
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
    changed: list[str] = []
    for row in ec._changes(before, {k: v for k, v in patch.items() if k != "cast_id"}):
        changed.append(row)
        if row.startswith("  visual_brief.forbidden_elements:"):
            changed += ban_rows
        elif row.startswith("  visual_description:") and kept:
            changed += [f"    was: {kept}", f"    now: {description}"]
            if description_note:
                changed.append(description_note)
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


def _send_cast_patch(
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
    verdict: bool = True,
    replace_description: bool = False,
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
    verdict
        End on the ``Applied`` / ``Not applied`` line ``edit`` and ``line`` end on (L-20261001-25); off when a
        caller prints its own.
    replace_description
        ``--replace-description``: the look's description replaces the card's own instead of being added to it.
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
    patch, changed = look_patch(
        spine, card, look, replace_description=replace_description
    )
    who = str(card.get("name") or card["cast_id"])
    if not changed:
        print(f"{who} already has this look; nothing to send.", file=out)
        return None
    print(f"look for {who} ({card['cast_id']}):", file=out)
    for row in changed:
        print(row, file=out)
    episode = int(card.get("intro_episode_ordinal") or 1)
    fresh, cascade = _send_cast_patch(
        desk,
        patch,
        episode=episode,
        select_regen=select_regen,
        preview_only=preview_only,
        out=out,
    )
    items = ec.change_items(changed)
    if fresh is None:
        if verdict:
            for row in ec.edit_verdict(items, preview=True):
                print(row, file=out)
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
    if verdict:
        for row in ec.edit_verdict(items):
            print(row, file=out)
    return path


# --- A new character on screen -----------------------------------------------------------------


def parse_staging(raw: str) -> dict[str, str]:
    """Read ``--staging``: a JSON object or ``key=value; …`` with frame_position, pose, gaze, interaction."""

    text = raw.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ec.CommandStopped(f"--staging: not valid JSON ({exc})") from None
        if not isinstance(data, dict):
            raise ec.CommandStopped("--staging: the JSON must be an object")
    else:
        data = {}
        for part in text.split(";"):
            key, sep, value = part.partition("=")
            if not sep:
                raise ec.CommandStopped(f"--staging: {part.strip()!r} is not key=value")
            data[key.strip()] = value
    allowed = {*STAGING_FIELDS, "wardrobe_variant"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ec.CommandStopped(
            f"--staging: unknown field(s) {', '.join(unknown)}; it takes {', '.join(STAGING_FIELDS)}"
        )
    entry = {key: _one_line(value) for key, value in data.items() if _one_line(value)}
    missing = [key for key in STAGING_FIELDS if key not in entry]
    if missing:
        raise ec.CommandStopped(
            f"--staging needs {', '.join(missing)} (how they stand in the frame); nothing was sent"
        )
    return entry


def _beat_line(
    spine: Mapping[str, Any], beat_id: str, cast_id: str
) -> Mapping[str, Any] | None:
    for beat in spine.get("beats") or []:
        if isinstance(beat, Mapping) and beat.get("beat_id") == beat_id:
            for line in beat.get("dialogue_lines") or []:
                if isinstance(line, Mapping) and line.get("cast_id") == cast_id:
                    return line
    return None


def _beat(spine: Mapping[str, Any], beat_id: str) -> Mapping[str, Any]:
    return next(
        b
        for b in spine.get("beats") or []
        if isinstance(b, Mapping) and b.get("beat_id") == beat_id
    )


def _frame(spine: Mapping[str, Any], frame_id: str) -> Mapping[str, Any] | None:
    return next(
        (
            f
            for f in spine.get("frames") or []
            if isinstance(f, Mapping) and f.get("frame_id") == frame_id
        ),
        None,
    )


def _staged(frame: Mapping[str, Any], cast_id: str) -> bool:
    blocking = (frame.get("visual_brief") or {}).get("subject_blocking") or []
    return cast_id in (frame.get("cast_refs") or []) or any(
        isinstance(b, Mapping) and b.get("cast_id") == cast_id for b in blocking
    )


def run_new_character(
    desk: Path,
    *,
    episode: int,
    beat: str | None,
    text: str | None,
    name: str,
    role: str | None,
    voice_description: str | None,
    provider_voice: str | None = None,
    look: str | None,
    staging: str | None = None,
    spoken: str | None = None,
    subtitle: str | None = None,
    select_regen: bool = False,
    preview_only: bool = False,
    out: Any = None,
) -> Path | None:
    """Add a character who is seen: their card, look, on-screen line and place in the beat's frame.

    The server makes a new card only through ``add_voice_only_cast`` and lets
    no frame stage someone the stored spine only hears, so four edits run in
    order, each a ``PATCH`` before the script gate or a cascade after it:

    1. ``add_voice_only_cast`` + ``add_dialogue_lines`` (the line off screen);
    2. the look (:func:`run_cast_look`'s cast card patch);
    3. ``dialogue_lines[].off_screen: false`` with the beat's
       ``motion_direction.subject_cast_id`` set to them (a speaking beat moves
       its speaker);
    4. on a beat with a frame, ``frames[]`` with the whole brief, a
       ``subject_blocking`` entry for them and ``cast_refs`` to match.

    The look, the staging and the beat are checked before the first edit. A
    step the story already has is skipped, so the same command finishes a run
    that stopped; a stop names what was done and the undo.

    Parameters
    ----------
    desk
        Series desk with a story.
    episode
        Episode ordinal.
    beat, text, spoken, subtitle
        The silent beat that gets their line, and the line.
    name, role, voice_description, provider_voice
        The new character, as ``--new-voice``.
    look
        Their look (:func:`parse_look`); required.
    staging
        How they stand in the beat's frame (:func:`parse_staging`); required when the beat has a frame.
    select_regen, preview_only
        As ``line``. A preview prints the plan and sends nothing.
    out
        Text stream.

    Returns
    -------
    Path | None
        The refreshed ``api/spine.json``; ``None`` on a preview.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    if look is None:
        raise ec.CommandStopped(
            "--new-character needs --look (how they look; they are drawn). Nothing was sent. "
            "For someone only heard, use --new-voice."
        )
    if beat is None or text is None:
        raise ec.CommandStopped('--new-character needs --add --beat N and --text "..."')
    if role is None or voice_description is None:
        raise ec.CommandStopped(
            '--new-character needs --role "..." and --voice-description "..." (how they sound)'
        )
    _, state, run = ec._desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    found = ec._find(
        spine, spine.get("beats") or [], "beat_id", beat, episode=episode, kind="beat"
    )
    beat_id = str(found["beat_id"])
    cast_id = ec.voice_cast_id(name)
    existing = next(
        (c for c in spine.get("cast") or []
         if isinstance(c, Mapping) and str(c.get("name") or "").strip().casefold() == name.strip().casefold()),
        None,
    )  # fmt: skip
    if existing is not None:
        cast_id = str(existing["cast_id"])
        if _beat_line(spine, beat_id, cast_id) is None:
            raise ec.CommandStopped(
                f"{name!r} is already in the cast ({cast_id}): give them the line with --speaker"
            )
    else:
        others = [
            ln for ln in found.get("dialogue_lines") or [] if isinstance(ln, Mapping)
        ]
        if others:
            raise ec.CommandStopped(
                f"beat {found.get('ordinal')} already speaks ({', '.join(str(ln.get('line_id')) for ln in others)}): "
                "a beat holds one line. Remove it first (`line --remove N`) or pick a silent beat. Nothing was sent."
            )
    card = existing or {"cast_id": cast_id, "name": name}
    look_patch(spine, card, look)  # checked now, sent at step 2
    frame_id = str(found.get("frame_id") or "")
    frame = _frame(spine, frame_id) if frame_id else None
    entry: dict[str, str] | None = None
    if frame is not None:
        if staging is None:
            raise ec.CommandStopped(
                f"beat {found.get('ordinal')} is drawn on {frame_id}: say how {name} stands in it with --staging "
                '\'{"frame_position": "…", "pose": "…", "gaze": "…", "interaction": "…"}\'. Nothing was sent.'
            )
        entry = {"cast_id": cast_id, **parse_staging(staging)}
    elif staging is not None:
        print(
            f"(beat {found.get('ordinal')} has no frame yet: --staging is not used; the frames author stages {name})",
            file=out,
        )
    plan = [
        f"add {name} ({cast_id}) with the line on beat {found.get('ordinal')} ({beat_id}), off screen for now",
        f"give {name} a look",
        f"put the line on screen and make {name} the beat's motion subject",
    ]
    if entry is not None:
        plan.append(
            f"stage {name} in {frame_id} ({entry['frame_position']}; {entry['pose']})"
        )
    steps = ["voice and line", "look", "line on screen", f"staged in {frame_id}"][
        : len(plan)
    ]
    print(f"ep{episode:02d} new character {name}, in {len(plan)} edits:", file=out)
    for number, step in enumerate(plan, start=1):
        print(f"  {number}. {step}", file=out)
    if preview_only:
        print(
            "(preview only: nothing was sent; each edit after the script gate prints its own cascade when run)",
            file=out,
        )
        for row in ec.edit_verdict(steps, preview=True):
            print(row, file=out)
        return None

    done: list[str] = []
    line_id = ""
    cascade = False

    def stopped(exc: ec.CommandStopped, step: int) -> ec.CommandStopped:
        rows = [
            f"{exc}",
            "",
            f"line --new-character stopped at edit {step} of {len(plan)}.",
        ]
        if done:
            rows += [f"  done: {done[0]}", *(f"        {item}" for item in done[1:])]
        rows += [
            f"  not done: {step}. {plan[step - 1]}",
            *(
                f"            {n}. {plan[n - 1]}"
                for n in range(step + 1, len(plan) + 1)
            ),
        ]
        rows.append(
            "  To finish: run the same command again (edits already on the story are skipped)."
        )
        if line_id:
            rows.append(
                f"  To undo: fictora-produce line --desk {desk} --episode {episode} --remove {line_id} "
                f"({name}'s card stays; with no line and no frame they are not drawn)."
            )
        return ec.EditRefused("\n".join(rows), items=steps[step - 1 :])

    # 1. the card and the line, heard.
    current = spine
    if _beat_line(current, beat_id, cast_id) is None:

        def add(spine_now: Mapping[str, Any]) -> tuple[dict[str, Any], list[str], str]:
            patch, changed = ec.build_line_add_remove_patch(
                spine_now, episode=episode, add=True, beat=beat_id, text=text, spoken=spoken, subtitle=subtitle,
                new_voice=name, role=role, voice_description=voice_description, provider_voice=provider_voice,
            )  # fmt: skip
            return (
                patch,
                changed,
                f"new character {name} (1/{len(plan)}: card and line)",
            )

        _, _, current, cascade, _, _ = ec._send_story_edit(
            desk,
            episode=episode,
            build=add,
            select_regen=select_regen,
            preview_only=False,
            out=out,
        )
    line = _beat_line(current, beat_id, cast_id)
    line_id = str((line or {}).get("line_id") or "")
    done.append(f"1. added {name} ({cast_id}) with the line {line_id or '?'}")

    # 2. the look.
    try:
        card_now = find_card(current, cast_id)
        patch, changed = look_patch(current, card_now, look)
        if changed:
            print(f"look for {name} ({cast_id}) (2/{len(plan)}):", file=out)
            for row in changed:
                print(row, file=out)
            fresh, ran = _send_cast_patch(
                desk,
                patch,
                episode=episode,
                select_regen=select_regen,
                preview_only=False,
                out=out,
            )
            current, cascade = fresh or current, cascade or ran
    except ec.CommandStopped as exc:
        raise stopped(exc, 2) from None
    done.append(f"2. gave {name} a look")

    # 3. on screen, moving.
    try:
        motion = _beat(current, beat_id).get("motion_direction") or {}
        line = _beat_line(current, beat_id, cast_id) or {}
        if (
            line.get("off_screen") is not False
            or motion.get("subject_cast_id") != cast_id
        ):

            def on_screen(
                spine_now: Mapping[str, Any],
            ) -> tuple[dict[str, Any], list[str], str]:
                direction = copy.deepcopy(
                    dict(_beat(spine_now, beat_id).get("motion_direction") or {})
                )
                before = direction.get("subject_cast_id")
                direction["subject_cast_id"] = cast_id
                names = ec._cast_names(spine_now)
                return (
                    {
                        "dialogue_lines": [{"line_id": line_id, "off_screen": False}],
                        "beats": [{"beat_id": beat_id, "motion_direction": direction}],
                    },
                    [
                        f"  {line_id}: off screen  ->  on screen",
                        f"  motion subject of {beat_id}: {names.get(str(before), before or 'none')}  ->  {name}",
                    ],
                    f"new character {name} (3/{len(plan)}: on screen)",
                )

            _, _, current, ran, _, _ = ec._send_story_edit(
                desk,
                episode=episode,
                build=on_screen,
                select_regen=select_regen,
                preview_only=False,
                out=out,
            )
            cascade = cascade or ran
    except ec.CommandStopped as exc:
        raise stopped(exc, 3) from None
    done.append(f"3. put {line_id} on screen with {name} moving")

    # 4. in the frame.
    if entry is not None:
        try:
            frame_now = _frame(current, frame_id)
            if frame_now is not None and not _staged(frame_now, cast_id):

                def stage(
                    spine_now: Mapping[str, Any],
                ) -> tuple[dict[str, Any], list[str], str]:
                    target = _frame(spine_now, frame_id) or {}
                    blocking = list(
                        (target.get("visual_brief") or {}).get("subject_blocking") or []
                    )
                    patch, changed = ec._frame_patch(
                        spine_now, target, [("subject_blocking", [*blocking, entry])]
                    )
                    return (
                        patch,
                        changed,
                        f"new character {name} (4/{len(plan)}: staged in {frame_id})",
                    )

                _, _, current, ran, _, _ = ec._send_story_edit(
                    desk,
                    episode=episode,
                    build=stage,
                    select_regen=select_regen,
                    preview_only=False,
                    out=out,
                )
                cascade = cascade or ran
        except ec.CommandStopped as exc:
            raise stopped(exc, 4) from None

    where = f", staged in {frame_id}" if entry is not None else ""
    print(
        f"{name} is on screen: {line_id} on beat {found.get('ordinal')}{where}.",
        file=out,
    )
    ec._after_line_edit(
        desk, current, episode=episode, line_id=line_id, after_gate=cascade, relocalized=False, new_line=True,
        out=out,
    )  # fmt: skip
    ec._note(
        desk, episode, f"line: new character {name} ({cast_id}): " + "; ".join(plan)
    )
    if provider_voice is None:
        print(
            ec.new_voice_pick_warning(
                desk, name, picked=ec._card_voice(current, cast_id)
            ),
            file=out,
        )
    print(plate_next_step(desk, current, find_card(current, cast_id)), file=out)
    for row in ec.edit_verdict(steps):
        print(row, file=out)
    return desk / "api" / "spine.json"


# --- A character who has left the story -------------------------------------------------------


def exit_card(spine: Mapping[str, Any], who: str) -> Mapping[str, Any]:
    """The one cast card ``who`` names: its cast id, or its name when no other card shares it.

    Raises
    ------
    CommandStopped
        No card has that name or id, or two or more cards share the name.
    """

    wanted = who.strip().casefold()
    cards = [
        c
        for c in spine.get("cast") or []
        if isinstance(c, Mapping) and c.get("cast_id")
    ]
    by_id = [c for c in cards if str(c["cast_id"]).casefold() == wanted]
    if by_id:
        return by_id[0]
    named = [c for c in cards if str(c.get("name") or "").strip().casefold() == wanted]
    if len(named) == 1:
        return named[0]
    if named:
        ids = ", ".join(str(c["cast_id"]) for c in named)
        raise ec.CommandStopped(
            f"{len(named)} characters are called {who!r} ({ids}); nothing was sent. "
            "Run again with the cast id of the one you mean: --cast <id>"
        )
    listed = ", ".join(f"{c.get('name')} ({c['cast_id']})" for c in cards) or "none"
    raise ec.CommandStopped(
        f"no character {who!r} on the story; nothing was sent. The cast is: {listed}"
    )


def _speaks_after(spine: Mapping[str, Any], cast_id: str, last: int) -> list[int]:
    """Episodes after ``last`` where the stored story still gives ``cast_id`` a line."""

    ordinal = {
        str(e.get("episode_id")): int(e.get("ordinal") or 0)
        for e in spine.get("episode_summaries") or []
        if isinstance(e, Mapping)
    }
    later: set[int] = set()
    for beat in spine.get("beats") or []:
        if not isinstance(beat, Mapping):
            continue
        number = ordinal.get(str(beat.get("episode_id")), 0)
        if number > last and any(
            isinstance(line, Mapping) and line.get("cast_id") == cast_id
            for line in beat.get("dialogue_lines") or []
        ):
            later.add(number)
    return sorted(later)


def run_cast_exit(
    desk: Path,
    *,
    cast: str,
    after_episode: int | None = None,
    clear: bool = False,
    out: Any = None,
) -> Path | None:
    """Mark a character as gone after an episode, or bring them back. Spends nothing.

    A series holds up to 50 characters, and every character still in the story
    is written out in full for the writers of the next episode. A guest who has
    left (they died, moved away, their arc ended) is better marked as gone: the
    writers then see one line for them ("gone after episode 5") and do not
    bring them back by accident. ``clear`` puts them back in the story.

    The change is the card's ``exit_episode_ordinal``: ``PATCH /v1/spines/{id}``
    with ``cast[]`` before the script gate, the ``cast_card`` cascade after it
    (paid items always off). It changes no picture and no voice.

    Parameters
    ----------
    desk
        Series desk with a story.
    cast
        The character's name or cast id.
    after_episode
        The last episode they are in.
    clear
        Bring them back (clear the mark).
    out
        Text stream.

    Returns
    -------
    Path | None
        The refreshed ``api/spine.json``; ``None`` when there was nothing to send.

    Raises
    ------
    CommandStopped
        An unknown or shared name, an episode before the character first appears,
        or a server refusal. Nothing is sent before these checks pass.
    """

    if (after_episode is None) == (not clear):
        raise ec.CommandStopped(
            "pass --after-episode N (the last episode they are in) or --clear (bring them back)"
        )
    if after_episode is not None and after_episode < 1:
        raise ec.CommandStopped("--after-episode is an episode number: 1 or more")
    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    _, state, run = ec._desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    card = exit_card(spine, cast)
    cast_id = str(card["cast_id"])
    who = str(card.get("name") or cast_id)
    intro = int(card.get("intro_episode_ordinal") or 1)
    before = card.get("exit_episode_ordinal")
    if after_episode is not None and after_episode < intro:
        raise ec.CommandStopped(
            f"{who} first appears in episode {intro}, so they cannot leave after episode {after_episode}; "
            f"nothing was sent. The earliest is --after-episode {intro}"
        )
    if clear and before is None:
        print(f"{who} is not marked as leaving; nothing to send.", file=out)
        return None
    if after_episode is not None and before == after_episode:
        print(
            f"{who} is already marked as leaving after episode {after_episode}; nothing to send.",
            file=out,
        )
        return None
    patch = {"cast_id": cast_id, "exit_episode_ordinal": after_episode}
    fresh, cascade = _send_cast_patch(
        desk, patch, episode=intro, select_regen=False, preview_only=False, out=out
    )
    if (
        fresh is None
    ):  # only a preview returns no story, and this command never previews
        return None
    path = save_spine_snapshot(desk, intro, fresh)
    kept = exit_card(fresh, cast_id).get("exit_episode_ordinal")
    if kept != after_episode:
        print(
            f"!! the server answered but its story does not show the change for {who} "
            f"(it shows {kept if kept is not None else 'no leaving episode'}): it may be an older deploy. "
            "Nothing was charged; run the command again or ask engineering.",
            file=out,
        )
        return path
    if after_episode is not None:
        print(
            f"{who} is marked as leaving after episode {after_episode}. Writers will see one line for them "
            f"from episode {after_episode + 1}; run with --clear to bring them back.",
            file=out,
        )
        later = _speaks_after(fresh, cast_id, after_episode)
        if later:
            listed = ", ".join(str(n) for n in later)
            word = "episodes" if len(later) > 1 else "episode"
            print(
                f"note: {who} still has lines in {word} {listed}. Give those lines to someone else "
                f"(`line --desk {desk} --episode N`), or mark them as leaving later.",
                file=out,
            )
        what = f"marked as leaving after episode {after_episode}"
    else:
        print(
            f"{who} is back in the story (was leaving after episode {before}). Writers see them in full again.",
            file=out,
        )
        what = f"back in the story (was leaving after episode {before})"
    ec._note(
        desk,
        intro,
        f"cast-exit: {who} ({cast_id}) {what}"
        + (" through the cast cascade" if cascade else ""),
    )
    return path


# --- The spoken language -----------------------------------------------------------------------


def language_code(value: str) -> str:
    """``ja`` / ``ja-JP`` -> ``ja-JP``; refuses anything but Japanese, Korean or English."""

    code = LANGUAGES.get(value.strip().lower())
    if code is None:
        raise ec.CommandStopped(f"--language takes ja, ko or en, not {value!r}")
    return code


def _record_path(desk: Path) -> Path:
    return desk.expanduser().resolve() / LANGUAGE_RECORD


def declared_language(desk: Path) -> str | None:
    """The spoken language the operator declared on this desk (``shared/spoken-language.json``), if any."""

    path = _record_path(desk)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(
            f"(cannot read {path}: {exc}; treated as no declared language)",
            file=sys.stderr,
        )
        return None
    value = record.get("declared") if isinstance(record, dict) else None
    return str(value) if value else None


def english_show_pin(
    desk: Path,
    *,
    episode: int,
    line: str | None,
    beat: str | None,
    spoken: str,
    subtitle: str | None,
    language: str | None,
) -> None:
    """Handle ``line --spoken`` before anything is sent, when the server holds the show as English.

    The server refuses ``spoken_text`` on an en-US show; ``language --spoken``
    changes the show's language first. A show performed in Japanese or Korean but drafted en-US
    (Kuchisake-onna) is told so here: with ``--language ja|ko``, a language
    already declared on the desk, or performed words that are not English, the
    pinned line and the declared language are recorded on the desk and nothing
    is sent; otherwise the stop names ``--language``. A show the server holds
    in another language returns at once and the edit goes ahead.

    Raises
    ------
    ec.CommandStopped
        Always on an en-US show (recorded, or refused with the flag to pass).
    """

    _, state, run = ec._desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    server = ec._spoken_language(spine)
    if server != "en-US":
        return
    wanted = language_code(language) if language else declared_language(desk)
    if wanted == "en-US" or (wanted is None and is_english(spoken)):
        raise ec.CommandStopped(
            "--spoken pins the performed line of a Japanese or Korean show, and the server holds this show as "
            "en-US: use --text. If the show is really performed in another language, change it first with "
            f"`fictora-produce language --desk {desk} --spoken ja` (or ko), then send this line again; "
            "or pass --language ja (or ko) to record the line on the desk until then."
        )
    code = wanted or ("ko-KR" if any("가" <= ch <= "힣" for ch in spoken) else "ja-JP")
    line_id = (
        ec.resolve_line_id(spine, line, episode=episode) if line is not None else None
    )
    path = _record_path(desk)
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            record = loaded if isinstance(loaded, dict) else {}
        except ValueError as exc:
            print(f"(cannot read {path}: {exc}; writing it again)", file=sys.stderr)
    pin: dict[str, Any] = {"episode": episode}
    if line_id is not None:
        pin["line_id"] = line_id
    else:
        beat_row = ec._find(
            spine,
            spine.get("beats") or [],
            "beat_id",
            str(beat),
            episode=episode,
            kind="beat",
        )
        pin["beat_id"] = beat_row["beat_id"]
    pin.update(
        {
            "spoken": spoken,
            "subtitle": subtitle or "",
            "at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    record.update(
        {
            "declared": code,
            "server": server,
            "spine_id": state.spine_id,
            "episode_id": episode_id_for(spine, episode),
            "pinned_lines": [*(record.get("pinned_lines") or []), pin],
        }
    )
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ec._note(
        desk,
        episode,
        f"line: {code} performed line recorded on the desk, not sent (server holds the show as {server}): "
        f"{line_id or pin.get('beat_id')} {spoken!r}"
        + (f" / {subtitle!r}" if subtitle else ""),
    )
    raise ec.CommandStopped(
        f"recorded on the desk, NOT sent: {line_id or pin.get('beat_id')} performed {spoken!r}"
        + (f", subtitle {subtitle!r}" if subtitle else "")
        + f" ({code}; {path}). The server holds this show as {server} and refuses a pinned performed line on an "
        f"English show. Change the show's language with `fictora-produce language --desk {desk} --spoken "
        f"{code[:2]}` (it shows what it sets aside first, and prints this line's command to send it again); "
        "against a server without that route the take speaks the English --text."
    )


__all__ = [
    "DEFAULT_EXPRESSION",
    "DEFAULT_GAZE",
    "DEFAULT_POSTURE",
    "LOOK_HELP",
    "STAGING_HELP",
    "declared_language",
    "english_show_pin",
    "exit_card",
    "find_card",
    "language_code",
    "NEVER_REMOVE",
    "look_patch",
    "parse_look",
    "parse_staging",
    "plate_next_step",
    "run_cast_exit",
    "run_cast_look",
    "run_new_character",
]
