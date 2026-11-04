"""A Wan shot's take lines carry the server's "Take 1 · shot 2 of 3" (take facts ``shot_label``), as sent.

An H3 take's facts have no ``shot_label``, so its lines read ``t1`` exactly as before.
"""

from __future__ import annotations

from creation.orchestrate import take_soundtrack_lines
from creation.post.take_facts import take_head


def test_a_wan_shot_shows_the_server_label_beside_its_desk_take() -> None:
    facts = {"job_id": "j", "shot_label": "Take 12 · shot 10 of 12"}
    assert take_head("t12", facts) == "t12 (Take 12 · shot 10 of 12)"
    assert (
        take_head("t1", {"take_facts": {"shot_label": "Take 1 · shot 2 of 3"}})
        == "t1 (Take 1 · shot 2 of 3)"
    )


def test_an_h3_take_or_missing_facts_keep_the_plain_take_id() -> None:
    assert take_head("t1", {"job_id": "j", "lane": "minimax-h3"}) == "t1"
    assert take_head("t1", None) == "t1"
    assert take_head("t1", {"shot_label": "  "}) == "t1"


def test_the_label_heads_the_take_lines_it_is_passed_to() -> None:
    facts = {
        "shot_label": "Take 1 · shot 2 of 3",
        "soundtrack": {"mode": "native", "reason": "model_voices"},
    }
    lines = take_soundtrack_lines(take_head("t1", facts), facts)
    assert lines == [
        "t1 (Take 1 · shot 2 of 3) Soundtrack: native (reason: model_voices)"
    ]
