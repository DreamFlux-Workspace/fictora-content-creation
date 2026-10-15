"""The kit shows the server's take length and reason; it never works one out itself."""

from __future__ import annotations

from creation.spine_view import script_lines, take_length_line

_SPINE = {
    "episode_summaries": [{"episode_id": "ep01", "ordinal": 1}],
    "beats_per_storyboard_set": [3, 3],
    "beats": [
        {
            "episode_id": "ep01",
            "ordinal": index,
            "motion_intent": f"shot {index}",
            "dialogue_lines": [],
        }
        for index in range(1, 7)
    ],
    "take_lengths": [
        {
            "episode_id": "ep01",
            "take_index": 2,
            "seconds": 9,
            "needed_seconds": 8.4,
            "longest_seconds": 15,
            "shortest_seconds": 5,
            "reason": "sized to the scene: about 5 s of lines at their natural pace, "
            "the moves around them and a short beat at the end",
        }
    ],
}


def test_a_take_the_server_sized_prints_its_length_and_reason() -> None:
    assert take_length_line(_SPINE, episode=1, take_index=2).startswith(
        "films 9 s (sized to the scene"
    )
    # A server that still starts its reason with the seconds is not repeated.
    older = {
        **_SPINE,
        "take_lengths": [
            {**_SPINE["take_lengths"][0], "reason": "9 s, sized to the scene"}
        ],
    }
    assert (
        take_length_line(older, episode=1, take_index=2)
        == "films 9 s (sized to the scene)"
    )
    assert take_length_line(_SPINE, episode=1, take_index=1) == ""
    assert take_length_line({}, episode=1, take_index=1) == ""


def test_the_script_view_shows_it_under_its_take() -> None:
    lines = script_lines(_SPINE, episode=1, take_ids=["t1", "t2"])

    header = lines.index("ep01 t2")
    assert lines[header + 1].startswith("  films 9 s")
    assert not any(line.startswith("  films") for line in lines[:header])


def test_the_spend_log_books_the_seconds_the_take_filmed() -> None:
    from creation.orchestrate import _filmed_seconds

    assert _filmed_seconds({"duration_seconds": 9}, 15) == 9
    assert _filmed_seconds({}, 15) == 15
    assert _filmed_seconds(None, 10) == 10
