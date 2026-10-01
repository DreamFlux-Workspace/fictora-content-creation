"""Two founder rules of 1 Oct 2026, as the operator kit applies them.

* **Stylised styles only, said out loud.** Fictora draws in its published anime
  and manhwa styles. A brief that asks for photoreal, realistic, live-action or
  a real-film look is never silently converted: the operator is told which
  styles exist, the closest one, and picks.
* **No real people, not even stylised.** A named or recognisable real person is
  never drawn or voiced as a character. Archetypes ("a K-pop idol type", "a
  cold CEO", "a 90s rock star vibe") are fine. The server's writer makes an
  original character with the same vibe; the cast gate shows it before
  anything is drawn.

The rule lives on the server (``fictora-drama``): a draft that breaks it is
answered ``409 brief_needs_creator_choice`` with ``details.notices``. This
module mirrors its cheap deterministic check so the kit pauses *before* it
sends (an older server never raises the 409), and renders the server's notices
as operator text. It is a mirror, not the rule: the server's notice wins.

Look frames and look notes are only *warned* about (:func:`photoreal_look_warning`):
they are an operator calibration tool drawn from words, the board and plate
prompts stay bound to the preset, and a hard block on a false positive
("cinematic lighting") would cost more than it saves.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Notice kinds the server raises and the operator acknowledges.
STYLE_NOT_AVAILABLE = "style_not_available"
REAL_PERSON_NOT_ALLOWED = "real_person_not_allowed"
NOTICE_KINDS: tuple[str, ...] = (STYLE_NOT_AVAILABLE, REAL_PERSON_NOT_ALLOWED)
#: The draft refusal that carries the notices.
BRIEF_NEEDS_CREATOR_CHOICE = "brief_needs_creator_choice"

#: The published stylised presets (``GET /v1/art-style-presets``), id and display
#: name. Used only to word a pause before anything is sent; the server's notice
#: carries its own list.
STYLISED_PRESETS: tuple[tuple[str, str], ...] = (
    ("modern-romance-1", "Clear Morning"),
    ("modern-dark-fantasy", "Cold Gate"),
    ("modern-romance-3", "Golden Hour"),
    ("xianxia", "Jade Ascent"),
    ("slice-of-life", "Meadow Hour"),
    ("modern-romance-2", "Peach Bloom"),
    ("cyberpunk", "Riot Ink"),
    ("modern-noble", "Velvet Sovereign"),
)

_PHOTOREAL = re.compile(
    r"\b(?:"
    r"photo[- ]?real(?:istic|ism)?|hyper[- ]?real(?:istic|ism)?|ultra[- ]?realistic|"
    r"live[- ]action|real actors?|actual actors?|real footage|real[- ]film look|"
    r"real[- ]life (?:look|footage|actors?|people|style)|"
    r"(?:realistic|lifelike|life-like|true-to-life)[- ](?:look(?:ing)?|style|visuals?|art(?:work)?|faces?|"
    r"people|characters?|render(?:ing)?|graphics|animation|cgi|3d|imagery|images?|photos?|film|footage|drawing)|"
    r"looks? real\b|"
    r"(?:not|no|without) (?:anime|cartoon|manhwa|manga|drawn|animated|illustrated)|"
    r"like a real (?:film|movie|photo(?:graph)?|tv show|drama)|"
    r"photograph(?:ic|y)? (?:style|look)|"
    r"(?:shot|filmed) on (?:35 ?mm|film|an? (?:iphone|camera|dslr))|"
    r"cinematic realism|documentary (?:style|footage|look)|dslr|raw photo|8k photo|"
    r"unreal engine|octane render"
    r")",
    re.IGNORECASE,
)

# A capitalised name of one to four tokens ("Park Seo-joon", "Jungkook", "Taylor Swift").
_NAME = r"(?P<name>[A-Z][\w'’-]*(?: [A-Z][\w'’-]*){0,3})"
_NOT_NAMES = frozenset(
    "I A An The He She They We You It His Her Their My Our This That These Those Him Them Me "
    "God Someone Somebody Anyone Everyone Mom Dad Mum Mother Father Grandma Grandpa Sister Brother "
    "Uncle Aunt Nobody".split()
)
_ROLE_WORDS = (
    r"idol|singer|actor|actress|rapper|model|athlete|star|celebrity|influencer|youtuber|streamer|member|"
    r"footballer|player|politician|president"
)
_LIKENESS = (
    rf"(?i:played by|starring|voiced by|modell?ed (?:on|after)|lookalike of|look-alike of|resembl(?:es|ing|e)|"
    rf"look(?:s|ing)?(?: just| exactly| a lot| a bit)? like|sounds? like|the real(?:-life)?|"
    rf"(?:face|likeness|voice) of|(?:an?|the) (?:{_ROLE_WORDS}) like)\s+{_NAME}",
    rf"{_NAME}(?:'s|’s) (?i:face|voice|likeness)\b",
    rf"{_NAME} (?i:look-?alike|doppelg[aä]nger)",
)
_LIKENESS_RE = tuple(re.compile(pattern) for pattern in _LIKENESS)

#: Words in a brief that point at the closest stylised preset, most specific first.
_CLOSEST: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "xianxia",
        re.compile(
            r"\b(?:xianxia|wuxia|cultivat\w*|martial|immortal|murim|ancient china)",
            re.I,
        ),
    ),
    (
        "cyberpunk",
        re.compile(
            r"\b(?:cyberpunk|neon|sci-?fi|future|android|robot|hacker|dystopia\w*)",
            re.I,
        ),
    ),
    (
        "modern-dark-fantasy",
        re.compile(
            r"\b(?:horror|ghost|demon|vampire|thriller|curse\w*|monster|occult|zombie|dark)",
            re.I,
        ),
    ),
    (
        "modern-romance-3",
        re.compile(r"\b(?:k-?drama|romance|romantic|love|wedding|dating)", re.I),
    ),
    (
        "modern-noble",
        re.compile(
            r"\b(?:ceo|chaebol|billionaire|heir\w*|aristocra\w*|noble|royal|palace|mafia)",
            re.I,
        ),
    ),
)
_FALLBACK_PRESET = "slice-of-life"


class BriefNoticePause(RuntimeError):
    """The brief needs the operator's choice before it is drafted. Nothing was spent."""


def find_photoreal_request(text: str) -> str | None:
    """Return the words that ask for a photoreal or live-action look, or ``None``.

    Parameters
    ----------
    text
        A brief, a look-frame description or a look note.

    Returns
    -------
    str | None
        The matched phrase as written.
    """

    found = _PHOTOREAL.search(text or "")
    return found.group(0) if found else None


def find_real_people(text: str) -> tuple[str, ...]:
    """Return the real people a text names as a character's face, voice or casting.

    Only a capitalised name used as a likeness counts ("looks like Park
    Seo-joon", "an idol like Jungkook from BTS", "played by ..."). Pronouns and
    family words are not names, so "looks like her mother" passes, and so does
    every archetype ("a K-pop idol type").

    Parameters
    ----------
    text
        A brief or a cast description.

    Returns
    -------
    tuple[str, ...]
        Names in order of first mention, without repeats.
    """

    hits: list[tuple[int, str]] = []
    for pattern in _LIKENESS_RE:
        for match in pattern.finditer(text or ""):
            name = match.group("name").strip(" '’-")
            if not name or name.split()[0] in _NOT_NAMES:
                continue
            hits.append((match.start("name"), name))
    seen: dict[str, str] = {}
    for _, name in sorted(hits):
        seen.setdefault(name.casefold(), name)
    return tuple(seen.values())


def closest_preset(text: str) -> str:
    """Return the published preset closest to what a brief describes.

    Parameters
    ----------
    text
        The brief.

    Returns
    -------
    str
        A preset id from :data:`STYLISED_PRESETS`.
    """

    for preset_id, pattern in _CLOSEST:
        if pattern.search(text or ""):
            return preset_id
    return _FALLBACK_PRESET


def _label(preset_id: str) -> str:
    return dict(STYLISED_PRESETS).get(preset_id, preset_id)


def brief_notices(text: str) -> list[dict[str, Any]]:
    """Return the notices the server would raise for this brief, in the server's shape.

    Parameters
    ----------
    text
        The brief about to be drafted.

    Returns
    -------
    list[dict[str, Any]]
        Zero, one or two notices (``kind``, ``message``, ``asked_for``, ``styles``,
        ``suggested_preset_id``, ``real_people``).
    """

    notices: list[dict[str, Any]] = []
    asked = find_photoreal_request(text)
    if asked:
        suggested = closest_preset(text)
        notices.append(
            {
                "kind": STYLE_NOT_AVAILABLE,
                "message": (
                    f"Fictora draws in these styles: {', '.join(label for _, label in STYLISED_PRESETS)}. "
                    f"Realistic or live-action isn't available. Pick one to continue; {_label(suggested)} "
                    "is the closest to your idea."
                ),
                "asked_for": asked,
                "styles": [
                    {"preset_id": pid, "label": label}
                    for pid, label in STYLISED_PRESETS
                ],
                "suggested_preset_id": suggested,
                "real_people": [],
            }
        )
    people = find_real_people(text)
    if people:
        named = " and ".join(people)
        notices.append(
            {
                "kind": REAL_PERSON_NOT_ALLOWED,
                "message": (
                    f"We can't draw or voice real people, so {named} won't be in your story. We'll write an "
                    "original character with the same vibe instead, and you'll see them in the cast before "
                    f"anything is drawn. If {named} is someone you made up, just continue."
                ),
                "asked_for": named,
                "styles": [],
                "suggested_preset_id": None,
                "real_people": list(people),
            }
        )
    return notices


def server_notices(body: Any) -> list[dict[str, Any]] | None:
    """Read the notices out of a ``409 brief_needs_creator_choice`` body.

    Parameters
    ----------
    body
        The parsed error envelope.

    Returns
    -------
    list[dict[str, Any]] | None
        The notices, or ``None`` when the body is some other refusal.
    """

    if not isinstance(body, Mapping):
        return None
    error = body.get("error")
    if (
        not isinstance(error, Mapping)
        or error.get("code") != BRIEF_NEEDS_CREATOR_CHOICE
    ):
        return None
    details = error.get("details")
    notices = details.get("notices") if isinstance(details, Mapping) else None
    return [dict(n) for n in notices or [] if isinstance(n, Mapping)]


def unacknowledged(
    notices: Iterable[Mapping[str, Any]], accepted: Iterable[str]
) -> list[Mapping[str, Any]]:
    """Return the notices whose kind the operator has not acknowledged.

    Parameters
    ----------
    notices
        Local or server notices.
    accepted
        Kinds passed with ``--accept-notice``.

    Returns
    -------
    list[Mapping[str, Any]]
        Notices still owed a choice.
    """

    taken = set(accepted)
    return [notice for notice in notices if str(notice.get("kind")) not in taken]


def pause_text(
    notices: Sequence[Mapping[str, Any]], *, desk: str = "<desk>", preset_id: str = ""
) -> str:
    """Word the pause the operator reads, with the commands that continue.

    Parameters
    ----------
    notices
        Notices still owed a choice.
    desk
        Series desk, for the commands to paste.
    preset_id
        The preset the desk is bound to now.

    Returns
    -------
    str
        Plain operator text.
    """

    lines = [
        "PAUSED for the creator: the brief needs a choice before it is drafted (nothing was sent or spent)."
    ]
    for notice in notices:
        kind = str(notice.get("kind"))
        lines.append(f"  - {notice.get('message')}")
        if kind == STYLE_NOT_AVAILABLE:
            styles = notice.get("styles") or []
            listed = ", ".join(
                f"{style.get('preset_id')} ({style.get('label')})"
                for style in styles
                if isinstance(style, Mapping)
            )
            suggested = notice.get("suggested_preset_id")
            if listed:
                lines.append(f"    Presets: {listed}.")
            if suggested:
                lines.append(
                    f"    Closest: {suggested}. Bound now: {preset_id or 'unknown'}."
                )
            lines.append(
                f"    Pick one with the creator: `fictora-produce bind --desk {desk} --prompt <brief> "
                "--preset-id <id>`, then `fictora-produce step --desk "
                f"{desk} --accept-notice {STYLE_NOT_AVAILABLE}`."
            )
        elif kind == REAL_PERSON_NOT_ALLOWED:
            lines.append(
                "    The writer makes an original character with the same vibe (new name, new look); the creator "
                "sees them at the cast gate. To go on: `fictora-produce step --desk "
                f"{desk} --accept-notice {REAL_PERSON_NOT_ALLOWED}` (or take the name out of the brief and bind again)."
            )
    return "\n".join(lines)


def photoreal_look_warning(text: str) -> str | None:
    """Return a warning when a look-frame description or look note asks for a photoreal look.

    A warning, never a block: see the module docstring.

    Parameters
    ----------
    text
        Look-frame description or look note.

    Returns
    -------
    str | None
        Operator warning, or ``None`` when the words stay stylised.
    """

    asked = find_photoreal_request(text)
    if not asked:
        return None
    return (
        f"WARNING: this look asks for {asked!r}. Fictora draws stylised anime/manhwa styles only (founder rule, "
        "1 Oct 2026): a look frame or look note must stay inside the stylised family and read as one of the "
        "presets. Photoreal look frames were used before (Sighted, Last Stream); do not repeat that. "
        "Drawing anyway; rewrite the words if this was not meant."
    )


__all__ = [
    "BRIEF_NEEDS_CREATOR_CHOICE",
    "NOTICE_KINDS",
    "REAL_PERSON_NOT_ALLOWED",
    "STYLE_NOT_AVAILABLE",
    "STYLISED_PRESETS",
    "BriefNoticePause",
    "brief_notices",
    "closest_preset",
    "find_photoreal_request",
    "find_real_people",
    "pause_text",
    "photoreal_look_warning",
    "server_notices",
    "unacknowledged",
]
