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
