"""An explosion is a hit, not a held sound (L-20261009-9): the kit's one copy of the server's rule.

"An explosion with a rumbling boom" carried "rumbling", a sustained word, so the
server planned it sustained and the shape checks refused a good render as
"sustained sound collapses". The server's ``sfx_layer.sfx_kind`` now lets
impact words beat sustained words; the kit's one copy (:mod:`creation.post.sound_words`, read
by ``ambience.goes_on``, ``sfx.checked_shape`` / ``sfx.shape_problem`` and the impact step's
offline fallback) follows the same words, synced with fictora-drama #1118 (27 Oct 2026):
doubled-letter forms ("slapping"), a one-off with the room after it, drips that come and go.
"""

from __future__ import annotations

import pytest

from creation.post import impact_post, sound_words
from creation.post.ambience import BLAST_STEMS, HARD_HIT_STEMS, goes_on, impact_weight
from creation.post.impact_post import impact_style
from creation.post.sfx import HIT_THEN_TAIL, SfxCue, checked_shape, shape_problem
from creation.post.sfx_motion import names_an_action, names_several_hits


@pytest.mark.parametrize(
    "sound",
    [
        "an explosion with a rumbling boom",
        "a distant blast, the ground rumbling",
        "a gunshot echoes off the hum of the street",
        "a crash of thunder",
    ],
)
def test_impact_words_beat_sustained_words(sound: str) -> None:
    assert not goes_on(sound), "a hit is never looped as the room"


def test_a_sound_that_goes_on_on_purpose_stays_a_bed() -> None:
    assert goes_on("distant explosions throughout")
    assert goes_on("an explosion, then a low rumble of falling rubble")
    for sound in ("thunder rumbling far off", "rain on the window", "crowd murmur"):
        assert goes_on(sound), sound


def test_an_explosion_then_a_bed_is_checked_as_a_hit_then_its_tail() -> None:
    cue = SfxCue(
        1, "an explosion, then a low rumble of falling rubble", "sustained", 0.0, 4.0
    )
    assert checked_shape(cue) == HIT_THEN_TAIL


#: Pinned here and in fictora-drama tests/drama_generation/test_sfx_impact_kind.py
#: (``impact_words``): change both together.
PINNED_BLASTS = {
    "explosion",
    "explode",
    "blast",
    "boom",
    "gunshot",
    "detonation",
    "detonate",
    "kaboom",
}
PINNED_HARD_HITS = {
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


def test_the_lists_match_the_server() -> None:
    assert BLAST_STEMS == PINNED_BLASTS and HARD_HIT_STEMS == PINNED_HARD_HITS


def test_the_kit_keeps_one_copy_of_the_lists() -> None:
    assert impact_post.BLAST_STEMS is sound_words.BLAST_STEMS
    assert impact_post.HIT_STEMS is sound_words.HARD_HIT_STEMS
    assert (
        BLAST_STEMS is sound_words.BLAST_STEMS
        and HARD_HIT_STEMS is sound_words.HARD_HIT_STEMS
    )


def test_a_blast_is_an_explosion_and_a_bang_is_a_hit() -> None:
    assert impact_weight("a blast") == "blast"
    assert impact_weight("the tank explodes") == "blast"
    assert impact_weight("a door bangs shut") == "hit"
    assert impact_weight("three cane bangs on the floor") == "hit"
    assert impact_weight("a soft knock") is None


def test_a_blast_follows_its_action_and_several_bangs_stay_several() -> None:
    assert names_an_action("a blast", "event")
    assert names_several_hits("three cane bangs on the floor")
    assert not names_several_hits("a door bangs shut")


# --- synced with fictora-drama #1118 (Every Use Costs a Finger ep 1, 26-27 Oct 2026) ---------------

#: A slap: loud at once, then gone.
_SLAP = (-8.0, -30.0, -60.0, -60.0, -60.0, -60.0)
#: Drips over a faint room: bursts with the room between them, never held near the loudest drip.
_DRIPS = (-20.0, -44.0, -46.0, -21.0, -45.0, -47.0, -22.0, -44.0)


@pytest.mark.parametrize(
    "word",
    ["slapping", "slapped", "slaps", "slammed", "slamming", "kicking", "smacked"],
)
def test_every_form_of_a_hit_is_a_hit(word: str) -> None:
    assert impact_weight(f"the glove {word} the tile") == "hit"
    if (
        word != "slamming"
    ):  # "slamming" is several slams to the impact step: no single punch-in
        assert impact_style(f"the glove {word} the tile", "event") == "hit"


@pytest.mark.parametrize(
    "sound",
    [
        "the glove slapping into shallow water, the drain hum under it",
        "water splashing across the tile, the fluorescent buzz above",
        "metal trays clattering, the strip light humming",
        "a sharp gasp; the fridge hum behind her",
    ],
)
def test_a_one_off_sound_with_the_room_after_it_is_never_looped(sound: str) -> None:
    assert sound_words.one_off_with_room_after(sound)
    assert not goes_on(sound), "one event: the ambience bed already lays the room"


@pytest.mark.parametrize(
    "sound",
    [
        "the fluorescent hum, a chair creak",
        "a car door slams, then the engine hums",
        "distant knocks throughout, the wind rising",
        "rain on the window",
        "dripping water and the fluorescent buzz",
    ],
)
def test_a_held_sound_stays_held(sound: str) -> None:
    assert goes_on(sound)


def test_drips_are_heard_in_bursts_not_held_at_their_loudest() -> None:
    sound = "dripping water and the fluorescent buzz"
    for legacy in (True, False):
        assert shape_problem("sustained", _DRIPS, sound=sound, legacy=legacy) is None
        assert (
            shape_problem(
                "sustained", _DRIPS, sound="the fluorescent buzz", legacy=legacy
            )
            is not None
        ), "a steady hum still must hold"
    assert shape_problem("sustained", _DRIPS, legacy=False) is not None, (
        "what the drips were refused for"
    )


def test_drips_that_go_silent_are_still_refused() -> None:
    gone = (-20.0, -60.0, -62.0, -65.0, -70.0, -70.0)
    assert (
        shape_problem("sustained", gone, sound="water dripping off the trays")
        is not None
    )
