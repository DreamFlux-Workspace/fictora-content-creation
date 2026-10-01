"""An `invalid_patch` refusal's hint follows what the server named (#498), not one fixed guess."""

from __future__ import annotations

from creation import episode_commands as ec
from fake_api import spine_fixture

ID_HINT = "an id is not on the story"

NAMED = (
    "HTTP 400 PATCH https://x/v1/spines/sp1: invalid_patch: The story spine patch is invalid: "
    "frames.0.visual_brief: reaction_cast_id needs a reaction_kind (details "
    '{"validation_errors": [{"loc": ["body", "patch", "frames", 0, "visual_brief"], '
    '"msg": "reaction_cast_id needs a reaction_kind on the same frame", "type": "value_error"}]})'
)
UNKNOWN_IDS = (
    "HTTP 400 PATCH https://x/v1/spines/sp1: invalid_patch: The story spine patch is invalid. (details "
    '{"unknown_frame_cast_ids": ["cast_nobody"]})'
)
BARE = "HTTP 400 PATCH https://x/v1/spines/sp1: invalid_patch: The story spine patch is invalid."


def test_a_named_rule_is_the_hint_not_the_id_guess() -> None:
    text = ec.explain_refusal(NAMED, spine_fixture(), episode=1)

    assert ID_HINT not in text
    assert (
        "the server named: patch.frames.0.visual_brief: reaction_cast_id needs a reaction_kind on the same frame"
        in text
    )
    assert "reaction_cast_id: needs a reaction_kind on the same frame" in text


def test_an_unknown_id_keeps_the_id_hint() -> None:
    text = ec.explain_refusal(UNKNOWN_IDS, spine_fixture(), episode=1)

    assert ID_HINT in text and "cast_nobody" in text


def test_a_bare_refusal_says_the_server_named_nothing_and_offers_the_id_hint_as_a_guess() -> (
    None
):
    text = ec.explain_refusal(BARE, spine_fixture(), episode=1)

    assert "the server named no field" in text
    assert "One common cause: an id is not on the story" in text
