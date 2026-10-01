"""Ask whether a narrator-named character is heard only, and keep the answer.

Founder decision 5 (2026-10-01). A cast member named like a narrator
("Narrator", "Storyteller", "Voice-over", "VO" / "V.O.", "Inner voice") can be
either a voice heard over the picture or a character the story shows. The
name does not say which, so the kit never decides from it: when such a
character first appears (after the draft, after ``author``, at the cast
plates or the boards, or with ``line --add --new-voice``) it asks the operator

    NAME — heard only, never seen? [y/N]

and saves the answer on the desk (``narrator-answers.json``), so it is asked
once per character.

* **Yes** puts them on the server's voice-only route in one ``PATCH``: every
  line of theirs is marked ``off_screen``, their card's ``visual_brief`` is
  cleared and ``heard_only`` is saved on the card, which holds even before
  they have a line, so no plate is ever drawn. Against a server without
  ``heard_only`` the lines are sent alone and a character with no line yet
  keeps their brief, with a warning.
* **No** keeps them an ordinary drawn character. Nothing is sent.

A run with nobody to ask (no terminal, or ``CI`` set) does not guess. It stops
with the exact flags to pass: ``--narrator-heard-only NAME`` or
``--narrator-on-screen NAME``.

The question is about a *name*, and only that. A person the story hears
off screen (a grandfather through the ceiling, a caller) is never asked and
never turned into a narrator; a character the server already says is
voice-only is not asked either (the answer is already saved there).

The name list mirrors the dry-voice labels the server's take printers know
(fictora-drama ``inner_voice.py``, #524: inner voice, voice-over, narration,
V.O., narrator) plus "storyteller" and a bare "VO".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Words that name a narrator-type voice, case-insensitive.
_NARRATOR_WORDS = re.compile(
    r"(?i)\b(?:narrator|narration|storyteller|story[- ]teller|inner[- ]voice|voice[- ]?over)\b"
)
#: "VO" / "V.O." in capitals only, so a surname such as "Vo" is not a narrator.
_VO_MARK = re.compile(r"(?<![A-Za-z])V\.?\s?O\.?(?![A-Za-z])")

ANSWERS_FILE = "narrator-answers.json"


class NarratorQuestionOpen(RuntimeError):
    """A narrator-named character has no saved answer and there is nobody to ask."""


@dataclass(frozen=True)
class NarratorAnswer:
    """One saved answer.

    Parameters
    ----------
    cast_id
        The character's cast id.
    name
        Their name when asked.
    heard_only
        ``True``: heard only, never seen (voice-only). ``False``: drawn.
    """

    cast_id: str
    name: str
    heard_only: bool


def named_like_narrator(name: str) -> bool:
    """Return whether a cast name reads like a narrator, voice-over or inner voice.

    Parameters
    ----------
    name
        Cast display name.

    Returns
    -------
    bool
        ``True`` for "Narrator", "The Storyteller", "Voice-over", "Hana (inner
        voice)", "VO", "V.O."; ``False`` for an ordinary name.
    """

    return bool(_NARRATOR_WORDS.search(name) or _VO_MARK.search(name))


def add_narrator_answer_args(parser: argparse.ArgumentParser) -> None:
    """Add ``--narrator-heard-only NAME`` and ``--narrator-on-screen NAME`` (repeatable) to a command.

    Parameters
    ----------
    parser
        The command's parser.
    """

    parser.add_argument(
        "--narrator-heard-only",
        action="append",
        default=[],
        metavar="NAME",
        help="A character named like a narrator is heard only, never seen: their lines go off screen and no "
        "plate is drawn. The human's answer; the kit never guesses from the name.",
    )
    parser.add_argument(
        "--narrator-on-screen",
        action="append",
        default=[],
        metavar="NAME",
        help="A character named like a narrator is an ordinary drawn character. The human's answer.",
    )


def interactive_ask() -> Callable[[str], str] | None:
    """Return ``input`` when a person can answer, else ``None``.

    Returns
    -------
    Callable[[str], str] | None
        ``input`` on a terminal outside CI; ``None`` when stdin is not a
        terminal or ``CI`` is set.
    """

    if os.environ.get("CI") or not sys.stdin.isatty():
        return None
    return input


def load_answers(desk: Path) -> dict[str, NarratorAnswer]:
    """Return the saved answers by cast id.

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    dict[str, NarratorAnswer]
        Empty when nothing was asked yet.
    """

    path = desk / ANSWERS_FILE
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(cast_id): NarratorAnswer(
            cast_id=str(cast_id),
            name=str(row.get("name") or cast_id),
            heard_only=bool(row.get("heard_only")),
        )
        for cast_id, row in (raw.get("characters") or {}).items()
        if isinstance(row, Mapping)
    }


def save_answer(desk: Path, answer: NarratorAnswer) -> None:
    """Save one answer on the desk.

    Parameters
    ----------
    desk
        Series desk.
    answer
        The answer to keep.
    """

    path = desk / ANSWERS_FILE
    raw: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    )
    characters = dict(raw.get("characters") or {})
    characters[answer.cast_id] = {
        "name": answer.name,
        "heard_only": answer.heard_only,
        "answered_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    raw["characters"] = characters
    path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _cards(spine: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [card for card in spine.get("cast") or [] if isinstance(card, Mapping)]


def open_questions(
    spine: Mapping[str, Any], answers: Mapping[str, NarratorAnswer]
) -> list[Mapping[str, Any]]:
    """Return the narrator-named characters with no answer yet, in cast order.

    Parameters
    ----------
    spine
        The story (``GET /v1/spines/{id}``).
    answers
        From :func:`load_answers`.

    Returns
    -------
    list[Mapping[str, Any]]
        Cast cards named like a narrator, not answered, and not already
        voice-only on the server (``voice_only`` true).
    """

    return [
        card
        for card in _cards(spine)
        if named_like_narrator(str(card.get("name") or ""))
        and str(card.get("cast_id")) not in answers
        and card.get("voice_only") is not True
        and card.get("heard_only") is not True
    ]


def _find(spine: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    wanted = ref.strip().casefold()
    for card in _cards(spine):
        if wanted in {
            str(card.get("cast_id") or "").casefold(),
            str(card.get("name") or "").strip().casefold(),
        }:
            return card
    names = ", ".join(str(card.get("name")) for card in _cards(spine)) or "nobody"
    raise ValueError(f"{ref!r} is not in the cast ({names})")


def question(name: str) -> str:
    """Return the question asked for one character.

    Parameters
    ----------
    name
        Their name.

    Returns
    -------
    str
        ``NAME — heard only, never seen? [y/N] ``.
    """

    return f"{name} — heard only, never seen? [y/N] "


def stop_message(names: Sequence[str], *, rerun: str) -> str:
    """Return the message a run with nobody to ask stops with.

    Parameters
    ----------
    names
        The unanswered characters.
    rerun
        The command to run again with a flag, e.g. ``fictora-produce step --desk D``.

    Returns
    -------
    str
        The question, why the kit will not guess, and both flags per name.
    """

    rows = "\n".join(
        f"  {rerun} --narrator-heard-only {name!r}   (heard only: their lines go off screen, no plate)\n"
        f"  {rerun} --narrator-on-screen {name!r}   (an ordinary drawn character)"
        for name in names
    )
    who = ", ".join(names)
    return (
        f"Stopped, nothing drawn: {who} is named like a narrator. Heard only, never seen? The name does not say, "
        "and nobody is here to answer, so the kit will not guess. Ask the human, then run one of:\n"
        + rows
    )


def _lines_of(spine: Mapping[str, Any], cast_id: str) -> list[Mapping[str, Any]]:
    return [
        line
        for beat in spine.get("beats") or []
        if isinstance(beat, Mapping)
        for line in beat.get("dialogue_lines") or []
        if isinstance(line, Mapping) and line.get("cast_id") == cast_id
    ]


def heard_only_patch(
    spine: Mapping[str, Any], cast_id: str, *, saved_on_card: bool = True
) -> dict[str, Any] | None:
    """Return the story patch that puts one character on the voice-only route.

    Parameters
    ----------
    spine
        The story.
    cast_id
        The character the operator said is heard only.
    saved_on_card
        Send ``heard_only: true`` on the card (fictora-drama founder decision
        5 follow-up): the server then never draws them, even before they have
        a line. ``False`` for a server without the field.

    Returns
    -------
    dict[str, Any] | None
        ``dialogue_lines`` marking every line of theirs ``off_screen`` that is
        not yet, and a ``cast`` entry clearing their ``visual_brief`` and saving
        ``heard_only``; ``None`` when there is nothing to send.
    """

    lines = [
        line for line in _lines_of(spine, cast_id) if line.get("off_screen") is not True
    ]
    patch: dict[str, Any] = {}
    if lines:
        patch["dialogue_lines"] = [
            {"line_id": str(line["line_id"]), "off_screen": True} for line in lines
        ]
    card = next((c for c in _cards(spine) if c.get("cast_id") == cast_id), {})
    entry: dict[str, Any] = {"cast_id": cast_id}
    if card.get("visual_brief") is not None and (
        saved_on_card or _lines_of(spine, cast_id)
    ):
        # Without the saved answer, a character with no line is still drawn,
        # so their brief is kept for that drawing.
        entry["visual_brief"] = None
    if saved_on_card and card.get("heard_only") is not True:
        entry["heard_only"] = True
    if len(entry) > 1:
        patch["cast"] = [entry]
    return patch or None


def settle_narrators(
    desk: Path,
    run: Any,
    spine: Mapping[str, Any],
    *,
    heard_only: Sequence[str] = (),
    on_screen: Sequence[str] = (),
    ask: Callable[[str], str] | None,
    rerun: str,
    out: Any = None,
) -> list[NarratorAnswer]:
    """Answer every narrator-named character once, and put a heard-only one on the voice-only route.

    Flags answer first; anyone still open is asked through ``ask``. With no
    ``ask`` and someone still open, nothing is sent and the run stops.

    Parameters
    ----------
    desk
        Series desk (answers are saved in ``narrator-answers.json``).
    run
        API session with ``patch(path, body)``.
    spine
        The story as the server has it now.
    heard_only, on_screen
        Names or cast ids from ``--narrator-heard-only`` / ``--narrator-on-screen``.
    ask
        Prompt function (``input``), or ``None`` when nobody can answer.
    rerun
        The command named in the stop message.
    out
        Text stream.

    Returns
    -------
    list[NarratorAnswer]
        The answers given in this call.

    Raises
    ------
    NarratorQuestionOpen
        A narrator-named character is unanswered and ``ask`` is ``None``.
    ValueError
        A flag names nobody in the cast, or the same character both ways.
    """

    out = out or sys.stdout
    answers = load_answers(desk)
    given: list[NarratorAnswer] = []
    flagged: dict[str, NarratorAnswer] = {}
    for refs, heard in ((heard_only, True), (on_screen, False)):
        for ref in refs:
            card = _find(spine, ref)
            cast_id = str(card["cast_id"])
            earlier = flagged.get(cast_id)
            if earlier is not None and earlier.heard_only is not heard:
                raise ValueError(
                    f"{card.get('name')} cannot be both heard only and on screen"
                )
            flagged[cast_id] = NarratorAnswer(
                cast_id, str(card.get("name") or cast_id), heard
            )
    given.extend(flagged.values())
    for card in open_questions(spine, {**answers, **flagged}):
        if ask is None:
            continue
        name = str(card.get("name") or card.get("cast_id"))
        reply = ask(question(name)).strip().casefold()
        given.append(NarratorAnswer(str(card["cast_id"]), name, reply in {"y", "yes"}))
    still_open = open_questions(spine, {**answers, **{a.cast_id: a for a in given}})
    if still_open:
        raise NarratorQuestionOpen(
            stop_message(
                [str(card.get("name") or card.get("cast_id")) for card in still_open],
                rerun=rerun,
            )
        )
    for answer in given:
        if answer.heard_only:
            _put_on_voice_only_route(run, spine, answer, out=out)
        else:
            print(
                f"[cast] {answer.name}: on screen, an ordinary drawn character. Saved; not asked again.",
                file=out,
            )
        save_answer(desk, answer)
    return given


def _send(run: Any, spine: Mapping[str, Any], patch: dict[str, Any]) -> None:
    run.patch(
        f"/v1/spines/{spine['spine_id']}",
        {"spine_version": spine["spine_version"], "patch": patch},
    )


def _put_on_voice_only_route(
    run: Any, spine: Mapping[str, Any], answer: NarratorAnswer, *, out: Any
) -> None:
    patch = heard_only_patch(spine, answer.cast_id)
    saved = True
    try:
        if patch is not None:
            try:
                _send(run, spine, patch)
            except SystemExit as exc:
                if "heard_only" not in str(exc.code) or "cascade_required" in str(
                    exc.code
                ):
                    raise
                # A server before founder decision 5's follow-up: no
                # ``heard_only`` field. Send the lines alone, as before.
                saved = False
                patch = heard_only_patch(spine, answer.cast_id, saved_on_card=False)
                if patch is not None:
                    _send(run, spine, patch)
    except SystemExit as exc:
        if "cascade_required" not in str(exc.code):
            raise
        line_ids = [
            entry["line_id"] for entry in (patch or {}).get("dialogue_lines") or []
        ]
        steps = (
            "; ".join(f"`line --line {line_id} --off-screen`" for line_id in line_ids)
            or "none"
        )
        raise NarratorQuestionOpen(
            f"{answer.name}: heard only, but the script is already approved, so the server needs the cascade "
            f"for it. Mark their lines off screen with the kit's line edit: {steps}. Then run this again."
        ) from exc
    if not saved and not _lines_of(spine, answer.cast_id):
        print(
            f"!! {answer.name}: heard only, saved on the desk. This server does not keep the answer on the card "
            "and they have no line yet, so it still draws them until one of their lines is marked off screen "
            "(`line --line ID --off-screen`).",
            file=out,
        )
        return
    count = len((patch or {}).get("dialogue_lines") or [])
    print(
        f"[cast] {answer.name}: heard only, never seen. {count} line(s) marked off screen"
        + ("; kept on their card" if saved else "")
        + ". No plate is drawn. Saved; not asked again.",
        file=out,
    )


__all__ = [
    "ANSWERS_FILE",
    "add_narrator_answer_args",
    "NarratorAnswer",
    "NarratorQuestionOpen",
    "heard_only_patch",
    "interactive_ask",
    "load_answers",
    "named_like_narrator",
    "open_questions",
    "question",
    "save_answer",
    "settle_narrators",
    "stop_message",
]
