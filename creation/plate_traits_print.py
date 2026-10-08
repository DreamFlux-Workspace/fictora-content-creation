"""Say at the cast gate what each approved sheet shows and where it disagrees with the card.

fictora-drama reads each new story's approved plate once when the cast is
approved (founder decision, 8 Oct 2026) and saves what the picture visibly
shows on the card as ``observed_traits``: skin tone, face shape, hair, eye
colour, marks, build and outfit colours. The takes use those words only where
the card says nothing (Three Payments Late's Dez: his card never says "pale").

Where the card does speak and the picture shows something else, the card's
words stay in every take and the server records the disagreement in
``observed_traits.conflicts``. This module prints both at the plates gate, so
the human can settle a disagreement with one card edit or a redraw. It reads
only the story the approve call returned: no request, no cost.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: How each trait is named in the gate text.
_TRAIT_WORDS: Mapping[str, str] = {
    "skin_tone": "skin tone",
    "face_shape": "face shape",
    "hair_colour": "hair colour",
    "hair_length": "hair length",
    "eye_colour": "eye colour",
    "build": "build",
}


def _seen_words(traits: Mapping[str, Any]) -> list[str]:
    words: list[str] = []
    skin = str(traits.get("skin_tone") or "")
    if skin:
        words.append(skin if "skin" in skin else f"{skin} skin")
    face = str(traits.get("face_shape") or "")
    if face:
        words.append(face)
    hair = " ".join(
        str(traits.get(key) or "")
        for key in ("hair_length", "hair_colour", "hair_style")
    ).split()
    if hair:
        said = " ".join(dict.fromkeys(hair))
        words.append(said if "hair" in said or "bald" in said else f"{said} hair")
    eyes = str(traits.get("eye_colour") or "")
    if eyes:
        words.append(eyes if "eye" in eyes else f"{eyes} eyes")
    words.extend(str(mark) for mark in traits.get("distinctive_marks") or () if mark)
    build = str(traits.get("build") or "")
    if build:
        words.append(build)
    colours = [str(colour) for colour in traits.get("outfit_colours") or () if colour]
    if colours:
        words.append(f"wears {', '.join(colours)}")
    return words


def plate_traits_text(spine: Mapping[str, Any] | None) -> str:
    """Return the gate lines for every card whose approved sheet was read, or ``""``.

    Parameters
    ----------
    spine
        The story as the approve call returned it.

    Returns
    -------
    str
        One line per read sheet (what it shows), then one line per
        disagreement with the card. Empty when no sheet was read (a story from
        before 6 Oct 2026, or a server without the read).
    """

    if not spine:
        return ""
    lines: list[str] = []
    disagreements: list[str] = []
    cast: Sequence[Mapping[str, Any]] = spine.get("cast") or ()
    for card in cast:
        traits = card.get("observed_traits")
        if not isinstance(traits, Mapping):
            continue
        name = str(card.get("name") or card.get("cast_id") or "a character")
        seen = _seen_words(traits)
        if seen:
            lines.append(f"{name}'s approved sheet shows: {'; '.join(seen)}.")
        for conflict in traits.get("conflicts") or ():
            trait = _TRAIT_WORDS.get(
                str(conflict.get("trait")), str(conflict.get("trait"))
            )
            disagreements.append(
                f"!! {name}'s card and sheet disagree on {trait}: the card says "
                f'"{conflict.get("card_says")}", the sheet shows "{conflict.get("plate_shows")}". '
                "Takes keep the card's words. Show the human: if the card is right, redraw the "
                f'sheet (`redraw-plate --cast "{name}" --note "..."`, $0.30); if the sheet is right, '
                "note it in run-notes: the takes still say the card's words."
            )
    if not lines and not disagreements:
        return ""
    head = (
        "Saved on the cards from the approved sheets (one read each, server side). "
        "Takes add only what a card leaves out."
    )
    return "\n".join([head, *lines, *disagreements])


__all__ = ["plate_traits_text"]
