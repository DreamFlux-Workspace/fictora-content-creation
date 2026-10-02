"""``edit --frame --set`` may set optional visual_brief fields a saved frame leaves out."""

from __future__ import annotations

import pytest

from creation.episode_commands import CommandStopped, _apply


def test_story_signs_can_be_set_on_a_frame_that_has_none() -> None:
    brief = {"story_moment": "Ren hangs the sign back", "location": "doorway"}
    signs = [{"text": "営業中", "where": "the wooden shop sign on the beam"}]

    _apply(brief, "story_signs", signs, label="visual_brief")

    assert brief["story_signs"] == signs


def test_an_unknown_field_is_still_refused() -> None:
    brief = {"story_moment": "x"}

    with pytest.raises(CommandStopped, match="has no field 'story_sign'"):
        _apply(brief, "story_sign", [], label="visual_brief")


def test_optional_fields_are_only_for_the_visual_brief_top_level() -> None:
    blocking = {"pose": "standing"}

    with pytest.raises(CommandStopped, match="has no field 'story_signs'"):
        _apply(blocking, "story_signs", [], label="visual_brief.subject_blocking.0")
