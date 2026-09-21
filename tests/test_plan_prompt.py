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
