"""An explosion is a hit, not a held sound (L-20261009-9): the kit's copy of the server's rule.

"An explosion with a rumbling boom" carried "rumbling", a sustained word, so the
server planned it sustained and the shape checks refused a good render as
"sustained sound collapses". The server's ``sfx_layer.sfx_kind`` now lets
impact words beat sustained words; the kit's own copies (``ambience.goes_on``,
which picks the beats a room loop carries, and the hit words of
``sfx.checked_shape``) follow the same words.
"""

from __future__ import annotations

import pytest

from creation.post.ambience import BLAST_STEMS, HARD_HIT_STEMS, goes_on, impact_weight
from creation.post.sfx import HIT_THEN_TAIL, SfxCue, checked_shape
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
