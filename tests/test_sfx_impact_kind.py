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

from creation.post.ambience import IMPACT_WORDS, goes_on
from creation.post.sfx import HIT_THEN_TAIL, SfxCue, checked_shape


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


def test_the_impact_words_match_the_server() -> None:
    for word in (
        "explosion",
        "boom",
        "blast",
        "gunshot",
        "crash",
        "bang",
        "slam",
        "impact",
    ):
        assert word in IMPACT_WORDS
