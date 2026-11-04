"""A story whose video model makes its own sound effects (Wan) takes no sound notes (fictora-drama ``wan_sound_notes``).

The kit reads the server's ``video_lane_unsupported`` off the spine and never
decides by lane id: ``sound-note`` stops before sending, the hands-on warning
and the "older than the sound notes" printouts stay quiet, and ``--sfx-adjust``
says why. A spine without the field (H3, an older server) works as before.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from creation import episode_commands as ec
from creation.cli_post import laptop_only_refusal
from creation.harness_rules import hands_on_sound_lines
from creation.lane_features import (
    SFX_ADJUST_OFF_HOME,
    desk_sound_notes_supported,
    sound_notes_supported,
)
from creation.post.take_facts import stale_facts_reason
from fake_api import FakeApi

NOTES = "/v1/spines/sp1/sound-notes"
WAN_UNSUPPORTED = ["sound_notes", "sfx_remix"]


def _wan(api: FakeApi) -> None:
    api.spine_doc["video_lane"] = "wan-3.0"
    api.spine_doc["video_lane_unsupported"] = list(WAN_UNSUPPORTED)


def test_only_the_servers_list_turns_sound_notes_off() -> None:
    assert sound_notes_supported(None)
    assert sound_notes_supported(
        {"video_lane": "wan-3.0"}
    )  # an older server: no list, as before
    assert sound_notes_supported({"video_lane_unsupported": []})
    assert not sound_notes_supported({"video_lane_unsupported": WAN_UNSUPPORTED})


def test_sound_note_on_a_wan_story_stops_before_sending(
    desk: Path, api: FakeApi
) -> None:
    _wan(api)

    with pytest.raises(ec.CommandStopped, match="makes its own sound effects"):
        ec.run_sound_note(desk, text="no purring", out=io.StringIO())
    with pytest.raises(ec.CommandStopped, match="Nothing was saved or charged"):
        ec.run_sound_note(desk, out=io.StringIO())

    assert api.posted(NOTES) == []


def test_sound_note_on_an_h3_story_still_lists(desk: Path, api: FakeApi) -> None:
    out = io.StringIO()
    assert ec.run_sound_note(desk, out=out) == []
    assert "no sound notes on this story" in out.getvalue()


def _episode_spine(unsupported: list[str] | None) -> dict:
    spine = {
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
        "beats": [
            {
                "beat_id": "b1",
                "episode_id": "episode_01",
                "motion_intent": "She slams the door and pours the tea.",
                "dialogue_lines": [],
            }
        ],
        "sound_notes": [],
    }
    if unsupported is not None:
        spine["video_lane_unsupported"] = unsupported
    return spine


def test_the_hands_on_warning_is_quiet_on_a_wan_story() -> None:
    h3 = hands_on_sound_lines(_episode_spine(None), episode=1)
    wan = hands_on_sound_lines(_episode_spine(WAN_UNSUPPORTED), episode=1)

    assert h3 and h3[0].startswith("Hands-on actions with no sound note")
    assert wan == []


def test_saved_facts_are_never_older_than_a_wan_storys_notes() -> None:
    note = {"note_id": "n1", "text": "no purring"}
    h3 = {"sound_notes": [note]}
    wan = {"sound_notes": [note], "video_lane_unsupported": WAN_UNSUPPORTED}

    assert stale_facts_reason({}, h3, episode=1, take_id="t1")
    assert stale_facts_reason({}, wan, episode=1, take_id="t1") is None


def test_sfx_adjust_on_a_wan_story_says_why_and_h3_points_at_sound_note() -> None:
    h3 = laptop_only_refusal("finish", ["--sfx-adjust"])
    wan = laptop_only_refusal("finish", ["--sfx-adjust"], sound_notes=False)

    assert "sound-note --desk D" in h3
    assert SFX_ADJUST_OFF_HOME in wan and "sound-note" not in wan


def test_the_desk_reads_the_saved_spine(desk: Path) -> None:
    api_dir = desk / "ep01" / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    spine = _episode_spine(WAN_UNSUPPORTED)
    (api_dir / "05_spine.json").write_text(json.dumps(spine), encoding="utf-8")

    assert desk_sound_notes_supported(desk, 1) is False
    assert (
        desk_sound_notes_supported(desk, 2) is True
    )  # no spine saved for that episode
