"""Line-fit part 2 (fictora-drama #967): "No, give it two takes" and each episode's own takes.

The kit prints the server's choice word for word, sends ``two_takes`` back, and
groups an episode's beats into the takes the server films
(``episode_take_patterns``), falling back to the show's pattern on an older
server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from creation import line_fit
from creation.cli_produce import main
from creation.ops.floor import init_series_desk
from creation.ops.state import (
    episode_beat_pattern,
    load_series,
    sync_take_slots_to_spine,
)
from creation.spine_view import beats_by_take
from fake_api import FakeApi
from test_line_fit_command import ROUTE, SHA, VERSION, _run, _serve, proposal

LABEL = "No, give it two takes"
TEXT = "This adds one take to film."
UNAVAILABLE = (
    "This episode can no longer take another take; trim or edit the lines instead."
)


def two_takes_proposal(pid: str = "lf_1", **changes: Any) -> dict[str, Any]:
    """A ``needs_two_takes`` proposal that offers the third choice, as the server sends it."""

    base = proposal(
        pid,
        outcome="needs_two_takes",
        fitted_lines=[],
        trimmed_lines=[{"slot": "beat_3", "speaker": "Hana", "text": "Every night.", "words": 2, "changed": True}],
        fits_rules=False,
        extra_take={"after_take": 2, "beats": 3},
        question="Trim it to fit one take?",
        choices=[
            {"action": "line_fit_trim", "label": "Yes, trim to one take", "text": "Trim the lines.", "proposal_id": pid},
            {"action": "line_fit_two_takes", "label": LABEL, "text": TEXT, "proposal_id": pid},
            {"action": "line_fit_edit", "label": "Edit", "text": "Change the lines yourself.", "proposal_id": pid},
        ],
    )  # fmt: skip
    base.update(changes)
    return base


# --- the card and the choice -----------------------------------------------------------------------


def test_the_card_prints_the_two_takes_choice_word_for_word_and_nothing_of_its_own(
    desk: Path, api: FakeApi
) -> None:
    _serve(api, two_takes_proposal())

    text = _run(desk, proposal_id="lf_1")

    assert f"{LABEL}: {TEXT}" in text
    # The server's words only: no kit sentence about the take it adds.
    assert "Adds a take" not in text and "beats)" not in text
    assert (
        f"fictora-produce line-fit --desk {desk} --proposal lf_1 --choose two-takes"
        in text
    )


def test_the_card_prints_where_the_new_take_plays_in_the_servers_words(
    desk: Path, api: FakeApi
) -> None:
    said = "This adds one take to film, right after take 2."
    served = two_takes_proposal()
    served["choices"][1]["text"] = said
    _serve(api, served)

    text = _run(desk, proposal_id="lf_1")

    assert f"{LABEL}: {said}" in text
    assert text.count("after take 2") == 1


def test_choose_two_takes_sends_two_takes_and_follows_the_redraft(
    desk: Path, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation import episode_commands as ec

    _serve(
        api,
        two_takes_proposal(
            source="episode_author", episode_ordinal=2, spine_version=None
        ),
    )
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        200,
        {"proposal_id": "lf_1", "status": "redrafting", "job_id": "job_ep2"},
    )
    api.jobs["job_ep2"] = {"job_id": "job_ep2", "status": "completed"}
    monkeypatch.setattr(ec, "_show_authored", lambda *a, **k: None)

    text = _run(desk, choose="two-takes")

    assert api.posted(f"{ROUTE}/lf_1/choose") == [
        {"choice": "two_takes", "source_sha256": SHA}
    ]
    assert "Server: redrafting (job job_ep2)." in text
    assert api.polled == [("job_ep2", False)]


def test_choose_two_takes_on_a_brief_sends_the_story_version_and_saves_the_served_story(
    desk: Path, api: FakeApi
) -> None:
    _serve(api, two_takes_proposal(source="scene_prompt"))
    api.spine_doc = {
        **api.spine_doc,
        "spine_version": VERSION,
        "episode_take_patterns": [
            {"episode_id": "episode_01", "episode_ordinal": 1, "beats_per_storyboard_set": [3, 3, 3],
             "extra_take_count": 1},
        ],
    }  # fmt: skip
    bare = {k: v for k, v in api.spine_doc.items() if k != "episode_take_patterns"}
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        200,
        {"proposal_id": "lf_1", "status": "applied", "spine": bare},
    )

    text = _run(desk, proposal_id="lf_1", choose="two-takes")

    assert api.posted(f"{ROUTE}/lf_1/choose") == [
        {"choice": "two_takes", "source_sha256": SHA, "spine_version": VERSION}
    ]
    assert "Server: applied (lf_1)." in text
    # The desk keeps the story as GET serves it: with each episode's own takes.
    assert "episode_take_patterns" in (desk / "api" / "spine.json").read_text(
        encoding="utf-8"
    )


def test_needs_choice_after_two_takes_shows_the_next_card(
    desk: Path, api: FakeApi
) -> None:
    _serve(api, two_takes_proposal(source="scene_prompt"))
    nxt = two_takes_proposal("lf_2", message="Take 4 still holds too much.")
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        200,
        {"proposal_id": "lf_1", "status": "needs_choice", "next_proposal": nxt},
    )

    text = _run(desk, proposal_id="lf_1", choose="two-takes")

    assert "Server: needs_choice (another moment still does not fit)." in text
    assert "Take 4 still holds too much." in text
    assert "--proposal lf_2 --choose two-takes" in text


def test_a_409_unavailable_shows_the_servers_words_and_points_at_trim_or_edit(
    desk: Path, api: FakeApi
) -> None:
    _serve(api, two_takes_proposal())
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        409,
        {"error": {"code": "line_fit_two_takes_unavailable", "message": UNAVAILABLE}},
    )

    with pytest.raises(line_fit.LineFitStopped) as stopped:
        _run(desk, proposal_id="lf_1", choose="two-takes")

    assert UNAVAILABLE in str(stopped.value)
    assert f"fictora-produce line-fit --desk {desk} --proposal lf_1" in str(
        stopped.value
    )


def test_two_takes_on_a_proposal_that_does_not_offer_it_sends_nothing(
    desk: Path, api: FakeApi
) -> None:
    """An older server (or the switch off) offers only trim and edit: refuse before sending."""

    _serve(api, proposal(outcome="needs_two_takes"))

    with pytest.raises(line_fit.LineFitStopped, match="does not offer two takes"):
        _run(desk, proposal_id="lf_1", choose="two-takes")
    assert not [call for call in api.calls if call[0] == "POST"]


def test_the_cli_accepts_choose_two_takes(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(api, two_takes_proposal())
    api.routes[("POST", f"{ROUTE}/lf_1/choose")] = (
        200,
        {"proposal_id": "lf_1", "status": "applied"},
    )

    assert (
        main(
            [
                "line-fit",
                "--desk",
                str(desk),
                "--proposal",
                "lf_1",
                "--choose",
                "two-takes",
            ]
        )
        == 0
    )
    assert api.posted(f"{ROUTE}/lf_1/choose")[0]["choice"] == "two_takes"


# --- each episode's own takes ----------------------------------------------------------------------


def _spine(**extra: Any) -> dict[str, Any]:
    beats = [
        {"beat_id": f"b{n}", "episode_id": "ep01", "ordinal": n} for n in range(1, 10)
    ]
    beats += [
        {"beat_id": f"c{n}", "episode_id": "ep02", "ordinal": n} for n in range(1, 7)
    ]
    return {
        "beats_per_storyboard_set": [3, 3],
        "episode_summaries": [
            {"episode_id": "ep01", "ordinal": 1},
            {"episode_id": "ep02", "ordinal": 2},
        ],
        "beats": beats,
        **extra,
    }


PATTERNS = [
    {
        "episode_id": "ep01",
        "episode_ordinal": 1,
        "beats_per_storyboard_set": [3, 3, 3],
        "extra_take_count": 1,
    },
    {
        "episode_id": "ep02",
        "episode_ordinal": 2,
        "beats_per_storyboard_set": [3, 3],
        "extra_take_count": 0,
    },
]


def test_an_episode_reads_its_own_takes_from_the_server() -> None:
    spine = _spine(episode_take_patterns=PATTERNS)

    assert episode_beat_pattern(spine, episode_ordinal=1) == (3, 3, 3)
    assert episode_beat_pattern(spine, episode_id="ep01") == (3, 3, 3)
    assert episode_beat_pattern({"spine": spine}, episode_ordinal=1) == (3, 3, 3)
    assert episode_beat_pattern(spine, episode_ordinal=2) == (3, 3)


def test_an_older_server_without_the_list_falls_back_to_the_show_pattern() -> None:
    spine = _spine()

    assert episode_beat_pattern(spine, episode_ordinal=1) == (3, 3)
    assert episode_beat_pattern(
        _spine(episode_take_patterns=PATTERNS), episode_ordinal=3
    ) == (3, 3)
    assert episode_beat_pattern({}, episode_ordinal=1) == ()


def test_beats_split_into_the_episodes_own_takes() -> None:
    own = [
        {
            "episode_id": "ep01",
            "episode_ordinal": 1,
            "beats_per_storyboard_set": [3, 2, 4],
            "extra_take_count": 1,
        }
    ]
    grouped = beats_by_take(_spine(episode_take_patterns=own), episode=1, take_count=3)

    assert [[b["beat_id"] for b in take] for take in grouped] == [
        ["b1", "b2", "b3"],
        ["b4", "b5"],
        ["b6", "b7", "b8", "b9"],
    ]


def test_an_episode_given_two_takes_gets_one_more_take_slot(tmp_path: Path) -> None:
    desk = init_series_desk(tmp_path, "Two Takes", band="30s", episode_count=2)
    series = load_series(desk)
    assert [len(e.takes) for e in series.episodes] == [2, 2]

    notes = sync_take_slots_to_spine(series, _spine(episode_take_patterns=PATTERNS))

    assert [t.take_id for t in series.episodes[0].takes] == ["t1", "t2", "t3"]
    assert [t.take_id for t in series.episodes[1].takes] == ["t1", "t2"]
    assert notes == ["ep01: the story films 3 takes; added t3–t3."]


def test_take_captions_read_the_episodes_own_takes() -> None:
    from creation.captions import take_caption_lines

    spine = _spine(episode_take_patterns=PATTERNS)

    _, warning = take_caption_lines(spine, 1, take_index=3, take_count=3)

    assert warning == ""
