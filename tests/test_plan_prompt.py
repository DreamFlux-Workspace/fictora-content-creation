"""Tests for plan prompt normalization."""

from __future__ import annotations

from creation.plan_prompt import ensure_plan_prompt


def test_ensure_plan_prompt_appends_cast_floor_once() -> None:
    raw = "One elder at a sealed door."
    once = ensure_plan_prompt(raw)
    twice = ensure_plan_prompt(once)
    assert once == twice
    assert "at least two distinct named characters" in once
    assert raw in once


def test_ensure_plan_prompt_preserves_existing_marker() -> None:
    marked = "Premise. [fictora:season-bible-cast-min=2] already set."
    assert ensure_plan_prompt(marked) == marked


def test_cast_floor_never_asks_for_a_drawn_narrator() -> None:
    """Sighted ep 1 (L-20261001-2 / -4): the floor made a one-creature show's narrator a drawn cast member.

    The old directive asked every member for a full visual_brief, so the second
    member the minimum forced in was a narrator with a face, and the film job
    later drew his plate. The second member is now a voice heard and never seen
    or a second drawn character the story needs - never a narrator invented to
    reach two.
    """

    text = ensure_plan_prompt("A blind lighthouse keeper and the thing in the fog.")
    directive = text.split("[fictora:season-bible-cast-min=2]", 1)[1]
    assert "each with full visual_brief" not in directive
    assert "never shown on screen; heard only as a voice" in directive
    assert "off_screen" in directive
    assert "the story actually needs" in directive
    assert "narrator" in directive.casefold()
    assert "never invent a narrator" in directive.casefold()


def test_an_undrafted_prompt_carrying_the_old_directive_gets_the_new_one() -> None:
    """A desk created before this change re-steps its draft with the new directive, once."""

    old = (
        "Premise.\n\n[fictora:season-bible-cast-min=2] "
        "The season bible cast array must include at least two distinct named characters, "
        "each with full visual_brief and voice_brief. "
        "Episode 1 may show only one person on screen; the second may be voice-only, "
        "off-screen, or introduced later."
    )
    updated = ensure_plan_prompt(old)
    assert updated.startswith("Premise.")
    assert "each with full visual_brief" not in updated
    assert updated.count("[fictora:season-bible-cast-min=2]") == 1
    assert ensure_plan_prompt(updated) == updated
