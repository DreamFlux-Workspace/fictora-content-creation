"""The server's take-facts ``warnings`` (fictora-drama #584) reach ``review`` and ``finish``."""

from __future__ import annotations

from creation.post.review import take_warnings_section
from creation.post.take_facts import take_warning_lines


def test_each_warning_prints_with_its_shot_and_message_and_absent_prints_nothing() -> (
    None
):
    facts = {"take_facts": {
        "shots": [{"shot_index": 2, "start_seconds": 3.0, "end_seconds": 6.5}],
        "warnings": [{"code": "edge_duplicate_risk", "shot_index": 2, "who": "", "cast_id": "cast_hana",
                      "moves": "walks left to right",
                      "message": "Hana may appear twice, at the left and right edges"}],
    }}  # fmt: skip

    lines = take_warning_lines(facts, {"cast_hana": "Hana"})

    assert lines[0] == (
        "!! shot 2 (3.00-6.50s): Hana may appear twice, at the left and right edges "
        "[edge_duplicate_risk, Hana]"
    )
    assert (
        lines[1]
        .strip()
        .startswith("Check shot 2 (3.00-6.50s) at full size before finishing")
    )
    section = take_warnings_section(facts, {"cast_hana": "Hana"})
    assert section is not None and section.status == "⚠"
    assert section.details[0].startswith("shot 2 (3.00-6.50s): Hana may appear twice")
    assert take_warning_lines({"take_facts": {"shots": []}}) == []
    assert take_warnings_section({"take_facts": {}}, {}) is None


def test_a_change_that_must_hold_lists_its_shots_and_says_how_to_fix_it() -> None:
    # fictora-drama object_state (Iron Pigeon ep 1, L-20261009-14): a head that flew off came back on.
    facts = {"take_facts": {
        "shots": [{"shot_index": 3, "start_seconds": 6.0, "end_seconds": 8.0},
                  {"shot_index": 4, "start_seconds": 8.0, "end_seconds": 11.0}],
        "warnings": [{"code": "state_change_check", "shot_index": 3,
                      "who": "Duke Aldric's head off the body for good", "cast_id": "cast_duke",
                      "message": "Shots 3 and 4 come after a change that must hold (Duke Aldric's head off "
                                 "the body for good); check none of them shows it undone."}],
    }}  # fmt: skip

    lines = take_warning_lines(facts, {"cast_duke": "Duke Aldric"})

    assert lines[0] == (
        "!! shot 3 (6.00-8.00s): Shots 3 and 4 come after a change that must hold (Duke Aldric's head off "
        "the body for good); check none of them shows it undone. "
        "[state_change_check, Duke Aldric's head off the body for good]"
    )
    assert len(lines) == 2
    assert "look for it undone (a head back on" in lines[1]
    assert "re-film the take once" in lines[1]
    section = take_warnings_section(facts, {"cast_duke": "Duke Aldric"})
    assert section is not None and len(section.details) == 2
    # An edge warning alone keeps its old advice, word for word.
    edge = {"take_facts": {"shots": [], "warnings": [{"code": "edge_duplicate_risk", "shot_index": 1,
                                                      "who": "Hana", "message": "m"}]}}  # fmt: skip
    assert "state_change_check" not in take_warning_lines(edge)[-1]
