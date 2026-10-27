"""The kit's ONE copy of the server's sound words (fictora-drama ``impact_words`` and ``sfx_layer``).

The server decides what a planned effect is: every take-facts cue carries the server's ``kind``
(``event`` / ``sustained``) and ``impact`` (``blast`` / ``hit``), every ``POST
/v1/spines/{id}/sfx-cues`` answer carries its ``kind`` and ``shape_problem``, and the impact step
asks ``POST /v1/jobs/{take_job}/impact-plan``. The kit reads those answers first, always.

These words stay on the kit only for what the server is never asked about:

- a beat's ``motion_direction.sound_cue`` that the ambience loop may quote (:mod:`creation.post.ambience`):
  the server classes Sound lines, never these;
- the kit's own re-check of a rendered or cached cue file (:func:`creation.post.sfx.shape_problem`),
  which runs with no server call (a cached file on the desk) and adds the kit's hold rule;
- the impact step's offline fallback when ``impact-plan`` cannot be reached, and saved take facts
  older than the server's ``impact`` field (:mod:`creation.post.impact_post`, :mod:`creation.post.sfx_motion`).

Before 27 Oct 2026 the kit kept two copies (``ambience`` and ``impact_post``); now there is this one,
synced word for word with fictora-drama #1118 (Every Use Costs a Finger ep 1, 26-27 Oct 2026):

- a stem's last letter doubles before -ed / -ing ("slapped", "slapping" are a slap);
- a one-off sound that only names the room after it ("the glove slapping into shallow water, the
  drain hum under it") is one event, never a held sound (:func:`one_off_with_room_after`);
- drips and ticks come and go (:func:`names_intermittent_sound`): checked as heard at all, not as
  held near their loudest.

The stem lists are pinned in both repos' tests (kit ``tests/test_sfx_impact_kind.py``, server
``tests/drama_generation/test_sfx_impact_kind.py``): change both together.
"""

from __future__ import annotations

import re
from typing import Literal

#: Things that go off (the server's ``impact_words.BLAST_STEMS``).
BLAST_STEMS = frozenset(
    {
        "explosion",
        "explode",
        "blast",
        "boom",
        "gunshot",
        "detonation",
        "detonate",
        "kaboom",
    }
)
#: A body or an object striking hard; a door that bangs shut is a hit, never an explosion
#: (the server's ``impact_words.HARD_HIT_STEMS``).
HARD_HIT_STEMS = frozenset(
    {
        "slam",
        "bang",
        "crash",
        "smash",
        "shatter",
        "punch",
        "kick",
        "slap",
        "smack",
        "whack",
        "thwack",
    }
)


def forms(stems: frozenset[str]) -> re.Pattern[str]:
    """Every stem with its endings: explode/explodes/exploded/exploding, slap/slaps/slapped/slapping.

    A stem's last letter may double before -ed and -ing (the server's ``impact_words._forms``, #1118):
    "the glove slapping into shallow water" once read as no hit at all.
    """

    parts = [
        f"{stem[:-1]}(?:e|es|ed|ing)"
        if stem.endswith("e")
        else f"{stem}(?:s|es|ed|ing|{stem[-1]}ed|{stem[-1]}ing)?"
        for stem in sorted(stems)
    ]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)


BLAST = forms(BLAST_STEMS)
HARD_HIT = forms(HARD_HIT_STEMS)
#: Softer hits that also happen once, beside the blasts and hard hits.
SOFT_IMPACT = re.compile(
    r"\b(?:thud|thuds|clang|clangs|impact|impacts|shot rings out)\b", re.IGNORECASE
)

#: Sounds that go on rather than happen (the server's ``sfx_layer._SUSTAINED``).
GOES_ON = re.compile(
    r"\b(?:wind|winds|breeze|rain|raining|drizzle|downpour|storm|thunder|hum|humming|hums|drone|droning|"
    r"groan|groans|groaning|rumble|rumbling|ambience|ambient|murmur|murmuring|buzz|buzzing|"
    r"static|waves|surf|purr|purrs|purring|whir|whirring|crackle|crackling|sizzle|sizzling|simmer|simmering|"
    r"ticking|steady|constant|continuous|throughout|ongoing|all the while|keeps going|carries on)\b",
    re.IGNORECASE,
)
#: Who or where makes a sound, not its shape: alone they mean the room goes on.
SOURCE_ONLY = re.compile(r"\b(?:crowd|crowds|traffic)\b", re.IGNORECASE)
#: Sounds that happen once (the server's ``sfx_layer._ONE_OFF``).
ONE_OFF = re.compile(
    r"\b(?:gasp|gasps|gasped|sigh|sighs|sighed|bite|bites|crunch|crunches|breath|exhale|exhales|inhale|gulp|"
    r"cough|coughs|clap|claps|clink|clinks|snap|snaps|pop|pops|slam|slams|thud|thuds|bang|bangs|knock|knocks|"
    r"crack|cracks|click|shout|shouts|yelp|yelps|laugh|laughs|burst|horn|honk|honks|screech|screeches|"
    r"explosion|explosions|explodes|exploded|boom|booms|blast|blasts|gunshot|gunshots|crash|crashes|"
    r"impact|impacts|smash|smashes|shatter|shatters|detonation|detonates|kaboom)\b",
    re.IGNORECASE,
)
#: Wet and loose-object sounds that happen once: a splash, a slosh, trays clattering (``sfx_layer._WET_ONE_OFF``).
WET_ONE_OFF = re.compile(
    r"\b(?:splash\w*|splosh\w*|slosh\w*|splat\w*|plop\w*|plunk\w*|clatter\w*|thunk\w*)\b",
    re.IGNORECASE,
)
#: Where a Sound line's first sound ends and anything after it begins (``sfx_layer._FIRST_CLAUSE_END``).
FIRST_CLAUSE_END = re.compile(r"[,;]|\s(?:-|—|–)\s")
#: Sounds that come and go by nature (a drip, a tick): heard in bursts, never held at one level.
INTERMITTENT = re.compile(
    r"\b(?:drip|drips|dripping|dripped|tick|ticks|ticking|tap|taps|tapping|plink\w*|plip\w*)\b",
    re.IGNORECASE,
)
#: Words that say a sound goes on whatever it is ("distant explosions throughout"): it stays sustained.
GOES_ON_ON_PURPOSE = re.compile(
    r"\b(?:throughout|all the while|the whole time|keeps going|carries on|continues|continuous|constant|"
    r"steady|ongoing|under everything)\b",
    re.IGNORECASE,
)
#: What puts a following sound after a hit ("a door slams, then the engine idles"; ``sfx_layer._THEN``).
THEN = re.compile(
    r"\b(then|followed by|before|into|and then|giving way to)\b|,\s*then\b",
    re.IGNORECASE,
)
#: The hit words of a hit-then-bed cue (the server's ``sfx_layer._HIT_WORDS``, read by ``hit_then_tail``).
HIT_THEN_TAIL_WORDS = re.compile(
    r"\b(slam|slams|slammed|bang|bangs|crash|crashes|thud|thuds|clang|clangs|knock|knocks|"
    r"smash|smashes|shut|shuts|snap|snaps|hit|hits|crack|cracks|pop|pops|click|clicks|clinks?|"
    r"explosion|explosions|explodes|exploded|boom|booms|blast|blasts|gunshot|gunshots|impact|impacts|"
    r"shatter|shatters|detonation|detonates|kaboom)\b",
    re.IGNORECASE,
)


def impact_weight(words: str) -> Literal["blast", "hit"] | None:
    """``blast`` (something goes off), ``hit`` (a hard hit) or ``None``: the server's ``impact_words.impact_weight``."""

    if BLAST.search(words):
        return "blast"
    if HARD_HIT.search(words):
        return "hit"
    return None


def names_an_impact(sound: str) -> bool:
    """Whether a sound names a blast, a hard hit, a thud or a clang (the server's ``sfx_layer.names_an_impact``)."""

    return impact_weight(sound) is not None or bool(SOFT_IMPACT.search(sound))


def one_off_with_room_after(sound: str) -> bool:
    """Whether a sound opens on a one-off sound and only names the room after it (``sfx_layer``, #1118).

    "the glove slapping into shallow water, the drain hum under it" is one slap: the hum is the room.
    A hit then a bed ("slams, then the engine idles") and words that say it goes on ("throughout") are not.
    """

    end = FIRST_CLAUSE_END.search(sound)
    if (
        end is None
        or THEN.search(sound, end.start())
        or GOES_ON_ON_PURPOSE.search(sound)
    ):
        return False
    first = sound[: end.start()]
    if GOES_ON.search(first):
        return False
    return bool(
        names_an_impact(first) or ONE_OFF.search(first) or WET_ONE_OFF.search(first)
    )


def names_intermittent_sound(sound: str) -> bool:
    """Whether a sound comes and goes by nature (dripping water, a clock ticking): ``sfx_layer``, #1118."""

    return bool(INTERMITTENT.search(sound))


def sound_kind(sound: str) -> Literal["event", "sustained"]:
    """``sustained`` for a sound that goes on, else ``event``: the server's ``sfx_layer.sfx_kind``.

    Only for words the server never classed (a beat's ``sound_cue``); a take-facts cue's ``kind`` is the server's.
    """

    if names_an_impact(sound) and not GOES_ON_ON_PURPOSE.search(sound):
        then = THEN.search(sound)
        if not (then and GOES_ON.search(sound[then.end() :])):
            return "event"
    if one_off_with_room_after(sound):
        return "event"
    if GOES_ON.search(sound):
        return "sustained"
    if SOURCE_ONLY.search(sound) and not ONE_OFF.search(sound):
        return "sustained"
    return "event"


__all__ = [
    "BLAST_STEMS",
    "HARD_HIT_STEMS",
    "forms",
    "impact_weight",
    "names_an_impact",
    "names_intermittent_sound",
    "one_off_with_room_after",
    "sound_kind",
]
