"""A character note that changes what they wear: show the server's question, send the answer.

fictora-drama #1156 (The Funeral Guest, 28 Oct 2026): a cast note that changes
a character's outfit carries ``DramaCastNote.outfit_change``. While its
``status`` is ``asked`` the server has not changed the outfit yet and has
written a ``question`` ("… just for episode 7, or from now on?") with two
``options``. The kit prints those words as they are and sends the creator's
answer to ``POST /v1/spines/{id}/cast/{cast_id}/notes/{note_id}/outfit``. The
server owns the reading of the note, the question and the change; the kit keeps
nothing. A server without #1156 sends no ``outfit_change``, and every helper
here then does nothing.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

#: The kit's flag words and the server's answers (``DramaCastNoteOutfitAnswerRequest.answer``).
OUTFIT_ANSWERS: dict[str, str] = {
    "this-episode": "this_episode",
    "from-now-on": "from_now_on",
}

#: The refusal the answer route sends once the question is no longer open.
QUESTION_NOT_OPEN = "outfit_question_not_open"

#: The note route's optional field (#1156); an older server refuses it as an extra field.
EPISODE_FIELD = "episode_ordinal"


def _key(text: str) -> str:
    return " ".join(text.split()).casefold()


def note_on_card(card: Mapping[str, Any], text: str) -> Mapping[str, Any] | None:
    """The newest note on ``card`` with these words (spacing and case ignored), else ``None``."""

    wanted = _key(text)
    found = [
        n
        for n in card.get("creator_notes") or []
        if isinstance(n, Mapping) and _key(str(n.get("text") or "")) == wanted
    ]
    return found[-1] if found else None


def outfit_change(note: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The note's ``outfit_change`` (#1156), or ``None`` (no outfit change, or an older server)."""

    change = (note or {}).get("outfit_change")
    return change if isinstance(change, Mapping) and change.get("status") else None


def episode_field_refused(message: str, body: Any = None) -> bool:
    """A 4xx answer to the note POST that names ``episode_ordinal``: a server without #1156."""

    text = f"{message} {body!r}"
    return bool(re.search(r"HTTP 4\d\d", message)) and EPISODE_FIELD in text


def _flag(answer: str) -> str:
    return next(
        (flag for flag, sent in OUTFIT_ANSWERS.items() if sent == answer), answer
    )


def question_text(name: str, change: Mapping[str, Any], *, rerun: str) -> str:
    """The server's question and its two answers, with the command that sends each one.

    Parameters
    ----------
    name
        The character's name.
    change
        The note's ``outfit_change`` with ``status`` ``asked``.
    rerun
        The same command without the answer flag; `` --outfit <answer>`` is added per option.
    """

    question = str(change.get("question") or "").strip() or (
        f"Should {name} wear {change.get('outfit') or 'this outfit'} just for this episode, or from now on?"
    )
    lines = [
        f"{name}'s note changes what they wear, so the story asks first: {question}",
    ]
    options: Sequence[Any] = change.get("options") or []
    for option in options:
        if not isinstance(option, Mapping) or not option.get("answer"):
            continue
        label = str(option.get("label") or option["answer"])
        lines.append(f"  - {label}: {rerun} --outfit {_flag(str(option['answer']))}")
    if not any(isinstance(o, Mapping) and o.get("answer") for o in options):
        lines.extend(f"  - {flag}: {rerun} --outfit {flag}" for flag in OUTFIT_ANSWERS)
    lines.append(
        "The note is saved on their card; their outfit is not changed yet. "
        "Nothing was drawn or charged. Pick one answer and run that line."
    )
    return "\n".join(lines)


def changed_line(name: str, change: Mapping[str, Any]) -> str | None:
    """One plain line for an outfit note the server has applied, else ``None``."""

    status = change.get("status")
    outfit = str(change.get("outfit") or "the note's outfit")
    if status == "from_now_on":
        return f"{name}'s card now wears {outfit}, from now on: the card picture, boards and takes follow it."
    if status == "episodes":
        ordinals = [
            int(n) for n in change.get("episode_ordinals") or [] if str(n).isdigit()
        ]
        where = (
            f"episode {ordinals[0]}"
            if len(ordinals) == 1
            else "episodes " + ", ".join(str(n) for n in ordinals)
            if ordinals
            else "the episodes the note names"
        )
        variant = change.get("wardrobe_variant")
        named = f' (the outfit "{variant}")' if variant else ""
        return f"{name} wears {outfit}{named} in {where} only; their card keeps their own outfit."
    return None


__all__ = [
    "EPISODE_FIELD",
    "OUTFIT_ANSWERS",
    "QUESTION_NOT_OPEN",
    "changed_line",
    "episode_field_refused",
    "note_on_card",
    "outfit_change",
    "question_text",
]
