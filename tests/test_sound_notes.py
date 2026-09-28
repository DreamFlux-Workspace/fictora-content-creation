"""Sound notes and refreshed take facts: a note that adds a sound reaches an already-filmed take.

``sound-note`` saves the note on the server (scoped to one take when it adds a
sound) and prints the server's named refusal plainly. ``take-facts --refresh``
reads the take's facts again as a new version and says what moved in the SFX
plan. ``finish`` says when its saved facts are older than the sound notes.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest
from conftest import SPINE, make_take, needs_ffmpeg

from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.post.finish import run_finish
from creation.post.take_facts import (
    SOUND_NOTES_KEY,
    level_notes,
    save_take_facts,
    sfx_plan_changes,
    stale_facts_reason,
)
from fake_api import FakeApi

NOTES = "/v1/spines/sp1/sound-notes"
ADD_NOTE = {
    "note_id": "sound_note_1",
    "text": "add a dry stone crack at the end",
    "episode_id": "episode_01",
    "take": 1,
    "shot": 2,
}
DOOR = {
    "shot_index": 2,
    "sound": "a door slams",
    "kind": "event",
    "start_seconds": 3.0,
    "duration_seconds": 1.0,
    "source": "sound_line",
}
CRACK = {
    "shot_index": 2,
    "sound": "a dry stone crack",
    "kind": "event",
    "start_seconds": 4.2,
    "duration_seconds": 0.8,
    "source": "note",
}


def facts(*cues: dict) -> dict:
    """A take's facts as ``GET /v1/jobs/{id}/take-facts`` answers them (``take_facts`` inner)."""

    return {
        "job_id": "job_take_1",
        "endpoint_id": "fal-ai/minimax/hailuo-03/image-to-video",
        "media_kind": "video",
        "reference_image_count": 1,
        "spoken_line_count": 1,
        "shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5, "speaks": True},
            {"shot_index": 2, "start_seconds": 2.5, "end_seconds": 5.0, "speaks": False},
        ],
        "sfx_cues": list(cues),
    }  # fmt: skip


def _answer_note(api: FakeApi, note: dict) -> None:
    """The server appends the note (with the scope it resolved) and bumps the version."""

    def post(_method: str, _path: str, body: dict | None) -> dict:
        api.spine_doc["sound_notes"] = [*api.spine_doc.get("sound_notes", []), note]
        api.spine_doc["spine_version"] = "v6"
        return {"spine": api.spine_doc}

    api.routes[("POST", NOTES)] = post


def _raw_clips(desk: Path) -> None:
    (desk / "ep01" / "api").mkdir(parents=True, exist_ok=True)
    (desk / "ep01" / "api" / "17_raw_scene_clips.json").write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "episode_id": "episode_01",
                        "set_index": 1,
                        "job_id": "job_take_1",
                        "url": "https://r2.example/t1.mp4",
                    }
                ]
            }
        )
    )


# --- sound-note ------------------------------------------------------------------------------------


def test_an_added_sound_is_sent_with_its_take_and_row_and_the_spine_is_saved_again(
    desk: Path, api: FakeApi
) -> None:
    _answer_note(api, ADD_NOTE)
    out = io.StringIO()

    notes = ec.run_sound_note(
        desk, text="add a dry stone crack at the end", episode=1, take_id="t1", row=4, out=out
    )  # fmt: skip

    assert api.posted(NOTES) == [
        {
            "spine_version": "v5",
            "text": "add a dry stone crack at the end",
            "take": 1,
            "episode_id": "episode_01",
            "row": 4,
        }
    ]
    assert [note["note_id"] for note in notes] == ["sound_note_1"]
    saved = json.loads((desk / "api" / "spine.json").read_text())
    assert saved["spine_version"] == "v6" and saved["sound_notes"] == [ADD_NOTE]
    assert json.loads((desk / "ep01" / "api" / "spine.json").read_text()) == saved
    text = out.getvalue()
    assert "[adds to ep01 t1 shot 2]" in text
    assert "take-facts --desk" in text and "--take t1 --refresh" in text


def test_a_drop_note_carries_no_scope_and_says_finish_needs_sfx_adjust(
    desk: Path, api: FakeApi
) -> None:
    _answer_note(api, {"note_id": "sound_note_2", "text": "no purring"})
    out = io.StringIO()

    ec.run_sound_note(desk, text="no purring", out=out)

    assert api.posted(NOTES) == [{"spine_version": "v5", "text": "no purring"}]
    assert "[every take: drop/level]" in out.getvalue()
    assert "--sfx-adjust" in out.getvalue()


def test_the_servers_named_refusal_is_printed_plainly_with_the_fix(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    api.routes[("POST", NOTES)] = SystemExit(
        "HTTP 422 POST https://api.example/v1/spines/sp1/sound-notes: sound_note_needs_take: "
        "A note that adds a sound lands on one take. Say which, for example "
        '"add a crack at the end of episode 1 take 2" (a shot or a row is optional). [request r1]'
    )

    code = produce_main(["sound-note", "--desk", str(desk), "add a crack at the end"])

    err = capsys.readouterr().err
    assert code == 2
    assert "the server refused the sound note, nothing was saved" in err
    assert "sound_note_needs_take: A note that adds a sound lands on one take" in err
    assert "pass --take tK" in err
    assert not (desk / "api" / "spine.json").exists(), "a refused note re-saves nothing"


def test_a_shot_or_row_without_a_take_is_stopped_before_any_call(
    desk: Path, api: FakeApi
) -> None:
    with pytest.raises(ec.CommandStopped, match="pass --take tK too"):
        ec.run_sound_note(desk, text="add a crack", row=3, out=io.StringIO())
    with pytest.raises(ec.CommandStopped, match="not both"):
        ec.run_sound_note(
            desk, text="add a crack", take_id="t1", shot=1, row=3, out=io.StringIO()
        )
    assert api.calls == []


def test_a_note_is_removed_by_its_number(desk: Path, api: FakeApi) -> None:
    api.spine_doc["sound_notes"] = [
        {"note_id": "sn_a", "text": "no purring"},
        ADD_NOTE,
    ]

    def delete(_method: str, _path: str, _body: dict | None) -> dict:
        api.spine_doc["sound_notes"] = api.spine_doc["sound_notes"][:1]
        return {}

    api.routes[("DELETE", f"{NOTES}/sound_note_1")] = delete

    left = ec.run_sound_note(desk, remove="2", out=io.StringIO())

    assert [(m, p) for m, p, _, _ in api.calls if m == "DELETE"] == [
        ("DELETE", f"{NOTES}/sound_note_1")
    ]
    assert [note["note_id"] for note in left] == ["sn_a"]


# --- take-facts --refresh --------------------------------------------------------------------------


def test_refresh_saves_a_new_version_keeps_the_old_one_and_prints_the_new_cue(
    desk: Path, api: FakeApi
) -> None:
    _raw_clips(desk)
    old = desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json"
    old.write_text(json.dumps(facts(DOOR)))
    before = old.read_text()
    api.spine_doc["sound_notes"] = [ADD_NOTE]
    api.routes[("GET", "/v1/jobs/job_take_1/take-facts")] = {
        "take_facts": facts(DOOR, CRACK)
    }
    out = io.StringIO()

    path = ec.run_take_facts(desk, episode=1, take_id="t1", refresh=True, out=out)

    assert path.name == "take-facts-ep01-t1-v2.json"
    assert old.read_text() == before, "the old facts are never overwritten"
    got = [p for m, p, _, _ in api.calls if m == "GET" and "take-facts" in p]
    assert got == ["/v1/jobs/job_take_1/take-facts?spine_id=sp1"]
    saved = json.loads(path.read_text())
    assert saved["sfx_cues"] == [DOOR, CRACK]
    assert saved[SOUND_NOTES_KEY] == [
        {"note_id": "sound_note_1", "text": ADD_NOTE["text"], "shot": 2}
    ]
    text = out.getvalue()
    assert "+ shot 2 at 4.20s for 0.80s: a dry stone crack (event, note)" in text
    assert "door" not in text.split("SFX plan changes:")[1].split("finish")[0]
    assert stale_facts_reason(saved, api.spine_doc, episode=1, take_id="t1") is None


def test_refresh_with_no_take_job_on_the_desk_stops_and_calls_nothing(
    desk: Path, api: FakeApi
) -> None:
    with pytest.raises(ec.CommandStopped, match="names no job for this take"):
        ec.run_take_facts(
            desk, episode=1, take_id="t1", refresh=True, out=io.StringIO()
        )
    assert api.calls == []


def test_a_refused_refresh_names_the_status_and_keeps_the_saved_facts(
    desk: Path, api: FakeApi
) -> None:
    _raw_clips(desk)
    api.routes[("GET", "/v1/jobs/job_take_1/take-facts")] = SystemExit(
        "take_facts_video_only"
    )
    with pytest.raises(ec.CommandStopped, match="HTTP 409.*take_facts_video_only"):
        ec.run_take_facts(
            desk, episode=1, take_id="t1", refresh=True, out=io.StringIO()
        )
    assert not list((desk / "ep01" / "api").glob("take-facts-*.json"))


# --- staleness -------------------------------------------------------------------------------------


def test_facts_saved_before_an_add_note_are_stale_and_only_for_that_take(
    tmp_path: Path,
) -> None:
    spine = copy.deepcopy(SPINE)
    unstamped = {"take_facts": facts(DOOR)}
    assert stale_facts_reason(unstamped, spine, episode=1, take_id="t1") is None

    spine["sound_notes"] = [ADD_NOTE, {"note_id": "sn_x", "text": "no purring"}]
    reason = stale_facts_reason(unstamped, spine, episode=1, take_id="t1")
    assert reason and "1 sound note(s) added" in reason and "dry stone crack" in reason
    assert stale_facts_reason(unstamped, spine, episode=1, take_id="t2") is None, (
        "a note on take 1 says nothing about take 2"
    )

    path = save_take_facts(
        tmp_path, episode=1, take_id="t1", facts=facts(DOOR, CRACK), spine=spine
    )
    stamped = json.loads(path.read_text())
    assert stale_facts_reason(stamped, spine, episode=1, take_id="t1") is None
    spine["sound_notes"] = spine["sound_notes"][1:]
    assert "1 removed since" in (
        stale_facts_reason(stamped, spine, episode=1, take_id="t1") or ""
    )
    assert level_notes(spine) == ["no purring"]


def test_plan_changes_list_added_and_dropped_cues() -> None:
    assert sfx_plan_changes(facts(DOOR), facts(DOOR)) == []
    assert sfx_plan_changes(facts(DOOR), facts(CRACK)) == [
        "+ shot 2 at 4.20s for 0.80s: a dry stone crack (event, note)",
        "- shot 2 at 3.00s for 1.00s: a door slams (event, sound line)",
    ]


@needs_ffmpeg
def test_finish_says_its_facts_are_older_than_the_sound_notes_and_points_to_refresh(
    post_desk: Path,
) -> None:
    from test_post_finish import TWO_LINES, fake_bed, fake_sfx

    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts(DOOR))
    )
    spine = copy.deepcopy(SPINE)
    spine["sound_notes"] = [ADD_NOTE]
    (post_desk / "ep01" / "api" / "03_spine.json").write_text(json.dumps(spine))
    out = io.StringIO()

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, colour=False, stream=out)  # fmt: skip

    text = out.getvalue()
    assert "older than the story's sound notes" in text, text
    assert "take-facts --desk" in text and "--take t1 --refresh" in text
    sfx = next(s for s in result.steps if s.step == "sfx")
    assert "older than the sound notes" in sfx.detail
    assert (
        "older than the story's sound notes"
        in (post_desk / "ep01" / "run-notes.md").read_text()
    )
