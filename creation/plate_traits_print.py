"""Say at the cast gate what each approved sheet shows and where it disagrees with the card.

fictora-drama reads each new story's approved plate once, in the background
right after the cast is approved (founder decisions, 8 Oct 2026), and saves
what the picture visibly shows on the card as ``observed_traits``: skin tone,
face shape, hair, eye colour, marks, build and outfit colours. The takes use
those words where the card says nothing (Three Payments Late's Dez: his card
never says "pale"), and where the card and the approved sheet disagree on a
visible trait the takes follow the sheet (each conflict says which side the
takes use, ``takes_use``).

The approve call returns before the read lands, so the kit waits briefly
(:func:`wait_for_plate_traits`, at most :data:`WAIT_SECONDS`, reading the story
only) and then prints what was saved (:func:`plate_traits_text`). A read still
running is said plainly; nothing is ever held for it. No cost.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

#: Most the plates yes waits for the server's sheet read to land.
WAIT_SECONDS = 20.0
#: Pause between two reads of the story while waiting.
POLL_SECONDS = 2.0

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


def _approved_plate_ids(spine: Mapping[str, Any]) -> set[str]:
    return {
        str(asset.get("relation_id"))
        for asset in spine.get("media_assets") or ()
        if asset.get("relation_type") == "cast_card"
        and asset.get("url")
        and not asset.get("stale")
        and asset.get("review_state") == "approved"
    }


def plate_traits_pending(spine: Mapping[str, Any] | None) -> list[str]:
    """Return the names of characters whose approved sheet has no saved traits yet.

    Parameters
    ----------
    spine
        The story.

    Returns
    -------
    list[str]
        Names, in cast order; empty when every approved sheet was read.
    """

    if not spine:
        return []
    approved = _approved_plate_ids(spine)
    return [
        str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or ()
        if str(card.get("cast_id")) in approved
        and not isinstance(card.get("observed_traits"), Mapping)
    ]


def wait_for_plate_traits(
    fetch: Callable[[], Mapping[str, Any]],
    spine: Mapping[str, Any],
    *,
    wait_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """Read the story again until every approved sheet has saved traits, or the wait runs out.

    Parameters
    ----------
    fetch
        Reads the story (``GET /v1/spines/{id}``); never sends anything.
    spine
        The story the approve call returned.
    wait_seconds
        Most to wait; :data:`WAIT_SECONDS` when ``None``.
    sleep, clock
        Seams for tests.

    Returns
    -------
    Mapping[str, Any]
        The newest story read.
    """

    limit = WAIT_SECONDS if wait_seconds is None else wait_seconds
    deadline = clock() + limit
    current = spine
    while plate_traits_pending(current) and clock() < deadline:
        sleep(POLL_SECONDS)
        try:
            current = fetch()
        except Exception:  # noqa: BLE001 - a read failing only ends the wait
            break
    return current


def plate_traits_text(
    spine: Mapping[str, Any] | None, *, wait_note: bool = False
) -> str:
    """Return the gate lines for every card whose approved sheet was read, or ``""``.

    Parameters
    ----------
    spine
        The story after the approval (and :func:`wait_for_plate_traits`).
    wait_note
        Say which approved sheets have no traits yet even when none has any
        (a new desk, after a wait). Off for a story the server never reads.

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
            card_says = conflict.get("card_says")
            sheet_shows = conflict.get("plate_shows")
            if conflict.get("takes_use") == "sheet":
                disagreements.append(
                    f'Info: {name}\'s card says "{card_says}", the approved sheet shows "{sheet_shows}" '
                    f"({trait}): takes follow the sheet; update the card text if the sheet is wrong."
                )
            else:
                disagreements.append(
                    f"!! {name}'s card and sheet disagree on {trait}: the card says "
                    f'"{card_says}", the sheet shows "{sheet_shows}". '
                    "Takes keep the card's words. Show the human: if the card is right, redraw the "
                    f'sheet (`redraw-plate --cast "{name}" --note "..."`, $0.30); if the sheet is right, '
                    "note it in run-notes: the takes still say the card's words."
                )
    pending = plate_traits_pending(spine)
    waiting = (
        [
            f"Sheet read not back yet for {', '.join(pending)} (running on the server, or nothing usable "
            "seen): takes use those traits once saved; nothing waits for it."
        ]
        if pending and (lines or disagreements or wait_note)
        else []
    )
    if not lines and not disagreements and not waiting:
        return ""
    head = (
        "Saved on the cards from the approved sheets (one read each, server side). "
        "Takes add what a card leaves out and follow the sheet where they disagree."
    )
    return "\n".join([head, *lines, *disagreements, *waiting])


__all__ = [
    "POLL_SECONDS",
    "WAIT_SECONDS",
    "plate_traits_pending",
    "plate_traits_text",
    "wait_for_plate_traits",
]
