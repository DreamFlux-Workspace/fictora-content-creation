"""Desks made on or after 6 Oct 2026: the reel is cut by the server's reel engine.

Older desks keep the local reel exactly as it was (``tests/test_reel.py`` and
the other reel tests run on such desks). Here the server is
:class:`reel_fake_server.FakeReelServer` (``conftest.reel_server``).
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import needs_ffmpeg
from test_reel import _snapshot
from test_reel import reel_desk as legacy_reel_desk  # noqa: F401  (fixture)

from creation.post.letterbox import pad_to_canvas
from creation.post.media import probe_video
from creation.post.reel import auto_reel, run_reel
from creation.post.reel_via_server import legacy_desk
from creation.post.safe_zones import letterbox_file
from creation.post.finish_record import write_finish_record
from reel_fake_server import CAPTION, FakeReelServer

CLIPS = {
    "coordinator_job_id": "video-1",
    "clips": [
        {
            "job_id": "take-1",
            "episode_id": "",
            "set_index": 1,
            "url": "https://media.test/take-1.mp4",
        }
    ],
}


def _make_new(desk: Path) -> Path:
    """Date the desk 6 Oct 2026 (series.json day) and give it the take's video job."""

    series = desk / "series.json"
    body = json.loads(series.read_text(encoding="utf-8")) if series.is_file() else {}
    series.write_text(json.dumps({**body, "day": "2026-10-06"}), encoding="utf-8")
    (desk / "ep01" / "api" / "17_raw_scene_clips.json").write_text(
        json.dumps(CLIPS), encoding="utf-8"
    )
    return desk


@pytest.fixture
def reel_desk(legacy_reel_desk: Path) -> Path:  # noqa: F811
    """test_reel's tiny desk, made on the rules epoch: its reel goes to the server."""

    return _make_new(legacy_reel_desk)


# --- which desks -------------------------------------------------------------------------------


def test_desks_before_the_epoch_keep_the_local_reel(tmp_path: Path) -> None:
    old = tmp_path / "2026-09-28-closing-time"
    old.mkdir()
    new = tmp_path / "2026-10-06-night-shift"
    new.mkdir()
    dated = tmp_path / "desk"
    dated.mkdir()
    (dated / "series.json").write_text(
        json.dumps({"day": "2026-10-05"}), encoding="utf-8"
    )

    assert legacy_desk(old) is True
    assert legacy_desk(new) is False
    assert legacy_desk(dated) is True


@needs_ffmpeg
def test_a_legacy_desk_never_calls_the_server(
    legacy_reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
) -> None:
    from creation.rules_epoch import run_rules_epoch

    run_rules_epoch(legacy_reel_desk, set_to="legacy", out=io.StringIO())
    result = run_reel(
        legacy_reel_desk, episode=1, seconds=6.0, stream=io.StringIO(), no_cover=True
    )

    assert (
        result.video is not None
        and reel_server.requests == []
        and reel_server.uploads == []
    )
    assert result.ass is not None  # the local renderer's captions file, as before


# --- the server is sent what only the desk knows ---------------------------------------------


@needs_ffmpeg
def test_the_server_gets_the_takes_cues_shots_bed_and_the_desks_choices(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    run_reel(
        reel_desk, episode=1, seconds=12.0, ending="freeze-black", hook_line="She never left",
        hook_line_position="lower", caption_style="plain",
    )  # fmt: skip

    assert reel_server.jobs == [("video-1", "episode_01")]
    body = reel_server.requests[0]
    assert (body["seconds"], body["ending"], body["hook_line"], body["hook_line_position"]) == (
        12.0, "freeze-black", "She never left", "lower",
    )  # fmt: skip
    operator = body["operator"]
    take = operator["takes"][0]
    assert take["take_id"] == "t1" and take["video_url"].startswith(
        "https://assets.test/uploads/"
    )
    assert [c["text"] for c in take["cues"]] == [
        "Open the door",
        "Who is there",
        "It was me",
    ]
    assert take["cues"][0]["start_s"] == pytest.approx(0.3)
    assert [s["index"] for s in take["shots"]] == [1, 2, 3, 4]
    assert take["captions_burned"] is False and take["marked"] is False
    # The picture before the bed and captions, and the harness bed, are what was uploaded.
    assert [(kind, path.name) for kind, path in reel_server.uploads] == [
        ("video", "take-ep01-t1-colour-v1.mp4"), ("audio", "show-bed-v1.wav"),
    ]  # fmt: skip
    assert operator["bed_url"].endswith(".wav") and len(operator["bed_sha256"]) == 64
    assert (
        operator["bed_db"] == 1.6 and operator["music_in_take"] is False
    )  # measured up to -27 LUFS
    assert operator["caption_style"] == "plain" and operator["pov"] is False


@needs_ffmpeg
def test_the_reel_cover_post_and_plan_land_in_the_episode_folder_and_nothing_else_is_touched(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    before = _snapshot(reel_desk)

    result = run_reel(reel_desk, episode=1, seconds=6.0)

    assert result.video is not None and result.video.name == "reel-ep01-v1.mp4"
    assert result.cover is not None and result.cover.name == "reel-ep01-v1-cover-v1.jpg"
    post = result.post.read_text(encoding="utf-8") if result.post else ""
    # The caption is the server's; the operator's notes go under the divider.
    assert post.startswith(CAPTION)
    assert "Not part of the caption" in post and "Edit cover" in post
    plan = json.loads(result.plan_path.read_text(encoding="utf-8"))
    assert plan["takes"]["t1"]["source"] == "ep01/takes/take-ep01-t1-colour-v1.mp4"
    assert plan["take_windows"]["t1"]["duration_s"] == 6.0
    assert plan["server"]["job_id"] == "video-1"
    assert result.loudness == "-18.0 LUFS"
    assert (
        _snapshot(reel_desk) == before
    )  # nothing outside reels/ was written or touched
    names = sorted(p.name for p in (reel_desk / "reels" / "ep01").iterdir())
    assert names == [
        "latest.json", "post-ep01-v1.txt", "reel-ep01-v1-cover-v1.jpg", "reel-ep01-v1.mp4",
        "reel-plan-ep01-v1.json",
    ]  # fmt: skip


@needs_ffmpeg
def test_a_plan_only_run_keeps_the_plan_and_a_hand_edited_plan_is_sent_back(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    planned = run_reel(reel_desk, episode=1, seconds=6.0, plan_only=True)
    assert planned.video is None
    body = json.loads(planned.plan_path.read_text(encoding="utf-8"))
    body["segments"] = body["segments"][-1:]
    planned.plan_path.write_text(json.dumps(body), encoding="utf-8")

    result = run_reel(reel_desk, episode=1, plan_file=planned.plan_path)

    sent = reel_server.requests[-1]["plan"]
    assert [s["role"] for s in sent["segments"]] == ["new_fact"]
    assert result.video is not None and result.video.name == "reel-ep01-v2.mp4"
    assert (
        json.loads(result.plan_path.read_text(encoding="utf-8"))["edited_from"]
        == planned.plan_path.name
    )


@needs_ffmpeg
def test_a_server_that_does_not_answer_gets_the_reel_cut_locally(
    reel_desk: Path, reel_server: FakeReelServer, capsys: pytest.CaptureFixture[str]
) -> None:
    """Since 7 Oct 2026 a silent server no longer means no reel: the local engine cuts it (same rules)."""

    from creation.cli_produce import main

    reel_server.unreachable = True

    code = main(["reel", "--desk", str(reel_desk), "--episode", "1"])

    assert code == 0
    said = capsys.readouterr().out
    assert (
        "Reel made locally (the server reel engine did not answer: ConnectError); same rules"
        in said
    )
    assert (reel_desk / "reels" / "ep01" / "reel-ep01-v1.mp4").is_file()


@needs_ffmpeg
def test_a_refusal_is_the_servers_words(
    reel_desk: Path, reel_server: FakeReelServer, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.cli_produce import main

    reel_server.refuse = "episode_reel_invalid: plan: segments name t9"

    assert main(["reel", "--desk", str(reel_desk), "--episode", "1"]) == 1
    assert "episode_reel_invalid: plan: segments name t9" in capsys.readouterr().err


def test_a_pov_brief_on_the_desk_is_sent_as_pov(tmp_path: Path) -> None:
    from creation.post.reel import episode_is_pov

    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "brief-v1.md").write_text(
        "POV: the landlord is at your door\n", encoding="utf-8"
    )

    assert episode_is_pov(tmp_path, {}) is True
    assert episode_is_pov(tmp_path, {"scene_prompt_normalized": "A wedding."}) is False


def test_operator_body_carries_only_what_is_set() -> None:
    from creation.post.reel_via_server import BedChoice, operator_body

    body = operator_body(
        takes=[{"take_id": "t1"}], bed=BedChoice(None, -60.0, None, True, "no bed"), bed_upload=None,
        still_upload=("https://assets.test/still.jpg", "a" * 64), caption_style="house", pov=False,
        seconds=15.0, ending=None, hook_line=None, no_hook_line=True, hook_line_position=None,
        no_cover=False, cover_frame=None, plan=None,
    )  # fmt: skip

    assert body["ending"] == "hard" and body["no_hook_line"] is True
    assert (
        "hook_line" not in body and "plan" not in body and "cover_frame_s" not in body
    )
    assert body["operator"]["bed_db"] == -40.0  # the server's floor
    assert (
        "bed_url" not in body["operator"] and body["operator"]["music_in_take"] is True
    )
    assert body["operator"]["cover_still_url"] == "https://assets.test/still.jpg"


# --- the automatic reel ----------------------------------------------------------------------


def _rows(desk: Path) -> list[dict[str, str]]:
    return list(csv.DictReader((desk / "reels" / "metrics.csv").open(encoding="utf-8")))


def _latest(desk: Path, episode: int = 1) -> dict[str, Any]:
    return json.loads(
        (desk / "reels" / f"ep{episode:02d}" / "latest.json").read_text(
            encoding="utf-8"
        )
    )


@needs_ffmpeg
def test_auto_reel_writes_the_episode_folder_latest_json_and_one_metrics_row(
    reel_desk: Path,  # noqa: F811
) -> None:
    out = io.StringIO()
    result = auto_reel(reel_desk, 1, trigger="finish", stream=out)

    assert result is not None and result.video is not None and result.cover is not None
    folder = reel_desk / "reels" / "ep01"
    assert result.video.parent == folder and result.cover.parent == folder
    assert result.post is not None and result.post.parent == folder
    latest = _latest(reel_desk)
    assert latest["reel"] == "reels/ep01/reel-ep01-v1.mp4"
    assert latest["cover"] == f"reels/ep01/{result.cover.name}"
    assert latest["post"] == "reels/ep01/post-ep01-v1.txt"
    assert latest["draft"] is False and latest["made_by"] == "finish"
    assert len(_rows(reel_desk)) == 1

    # Nothing changed: the next finish says so in one line and cuts nothing.
    again = io.StringIO()
    assert auto_reel(reel_desk, 1, trigger="finish", stream=again) is None
    assert "unchanged" in again.getvalue()
    assert len(again.getvalue().strip().splitlines()) == 1
    assert sorted(p.name for p in folder.glob("reel-ep01-v*.mp4")) == [
        "reel-ep01-v1.mp4"
    ]

    # The finished take changes: a new reel, still one row for the episode, the old reel superseded.
    pre_bed = reel_desk / "ep01" / "takes" / "take-ep01-t1-colour-v1.mp4"
    stat = pre_bed.stat()
    os.utime(pre_bed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    third = auto_reel(reel_desk, 1, trigger="trim", stream=io.StringIO())
    assert (
        third is not None
        and third.video is not None
        and third.video.name == "reel-ep01-v2.mp4"
    )
    rows = _rows(reel_desk)
    assert len(rows) == 1
    assert rows[0]["reel_file"] == "reel-ep01-v2.mp4"
    assert "reel-ep01-v1.mp4" in rows[0]["superseded"]
    assert _latest(reel_desk)["made_by"] == "trim"


@needs_ffmpeg
def test_a_hand_edited_plan_is_reused_while_its_takes_are_unchanged(
    reel_desk: Path,  # noqa: F811
) -> None:
    first = auto_reel(reel_desk, 1, trigger="finish", stream=io.StringIO())
    assert first is not None
    body = json.loads(first.plan_path.read_text(encoding="utf-8"))
    body["segments"] = body["segments"][1:]  # the human drops the flash-forward
    body["notes"].append("hand edit: no flash-forward")
    first.plan_path.write_text(json.dumps(body), encoding="utf-8")
    pre_bed = reel_desk / "ep01" / "takes" / "take-ep01-t1-colour-v1.mp4"
    stat = pre_bed.stat()
    os.utime(pre_bed, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    out = io.StringIO()
    second = auto_reel(reel_desk, 1, trigger="finish", stream=out)

    assert second is not None
    assert [s["role"] for s in second.segments] == [s["role"] for s in body["segments"]]
    assert f"reusing the hand-edited plan `{first.plan_path.name}`" in out.getvalue()
    made = json.loads(second.plan_path.read_text(encoding="utf-8"))
    assert made["edited_from"] == first.plan_path.name


# --- letterbox shows ---------------------------------------------------------------------------

GREYS = (120, 160, 200, 230)


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture
def letterbox_reel_desk(tmp_path: Path) -> Path:
    """One finished letterbox take: its 4:3 pre-bed source, a letterbox caption file, a bed and a spine."""

    desk = tmp_path / "desk"
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True)
    (desk / "ep01" / "api").mkdir()
    (desk / "shared" / "beds").mkdir(parents=True)
    (desk / "series.json").write_text("{}", encoding="utf-8")
    pieces = [f"color=c=0x{g:02x}{g:02x}{g:02x}:s=192x144:d=1.5:r=24" for g in GREYS]
    inputs = [x for p in pieces for x in ("-f", "lavfi", "-i", p)]
    inputs += ["-f", "lavfi", "-i", "sine=f=440:d=6:sample_rate=48000"]
    _run([*inputs, "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]", "-map", "[v]",
          "-map", "4:a", "-af", "volume=0.3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
          str(takes / "take-ep01-t1-colour-v1.mp4")])  # fmt: skip
    # The accepted master's captions: a letterbox file's, placed in the band (finish wrote them on the canvas).
    (takes / "take-ep01-t1-cap-v1.ass").write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:00.30,0:00:01.20,House,,0,0,0,,{\\an8\\pos(540,1407)}Open the door\n"
        "Dialogue: 0,0:00:03.10,0:00:04.00,House,,0,0,0,,{\\an8\\pos(540,1407)}Who is there\n"
        "Dialogue: 0,0:00:04.60,0:00:05.30,House,,0,0,0,,{\\an8\\pos(540,1407)}It was me\n",
        encoding="utf-8",
    )
    _run(["-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.2:d=4:r=48000", "-c:a", "pcm_s16le",
          str(desk / "shared" / "beds" / "show-bed-v1.wav")])  # fmt: skip
    write_finish_record(
        desk, episode=1, take_id="t1", complete=True,
        pre_bed=takes / "take-ep01-t1-colour-v1.mp4",
        master=takes / "take-ep01-t1-cap-v1.mp4", final=takes / "take-ep01-t1-sokii-v1.mp4",
        bed=desk / "shared" / "beds" / "show-bed-v1.wav", bed_db=-16.5, duck_db=None,
        letterbox={"letterbox": True, "caption_colour": "white"},
    )  # fmt: skip
    spine = {
        "title": "Three Payments Late",
        "delivery_format": "letterbox",
        "premise_line": "The door was never locked.",
        "microdrama_genre": "mystery",
        "episode_summaries": [
            {"episode_id": "episode_01", "ordinal": 1, "title": "The Door",
             "hook_line_selected": {"kind": "drafted", "text": "She paid the third time"}},
        ],
        "frames": [
            {"frame_id": f"f{i}", "episode_id": "episode_01", "board_row": i, "storyboard_group_id": "g1"}
            for i in range(1, 5)
        ],
        "beats": [
            {"beat_id": "b1", "episode_id": "episode_01", "ordinal": 1, "frame_id": "f1",
             "dialogue_lines": [{"text": "Open the door"}]},
            {"beat_id": "b2", "episode_id": "episode_01", "ordinal": 2, "frame_id": "f3",
             "dialogue_lines": [{"text": "Who is there"}]},
            {"beat_id": "b3", "episode_id": "episode_01", "ordinal": 3, "frame_id": "f4",
             "satisfaction_type": "mystery_reveal", "dialogue_lines": [{"text": "It was me"}]},
        ],
    }  # fmt: skip
    (desk / "ep01" / "api" / "spine.json").write_text(
        json.dumps(spine), encoding="utf-8"
    )
    facts = {
        "shots": [
            {
                "shot_index": i + 1,
                "start_seconds": i * 1.5,
                "end_seconds": (i + 1) * 1.5,
            }
            for i in range(4)
        ]
    }
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts), encoding="utf-8"
    )
    # finish's letterbox final: the 4:3 take on the 9:16 canvas (captions, mark and title burned on it).
    pad_to_canvas(
        takes / "take-ep01-t1-colour-v1.mp4", takes / "take-ep01-t1-sokii-v1.mp4"
    )
    (desk / "ep01" / "api" / "17_raw_scene_clips.json").write_text(
        json.dumps(
            {
                "coordinator_job_id": "video-1",
                "clips": [{"job_id": "take-1", "episode_id": "", "set_index": 1}],
            }
        ),
        encoding="utf-8",
    )
    return _make_new(desk)


def test_a_letterbox_reel_is_cut_from_the_9_16_letterbox_file(
    letterbox_reel_desk: Path, reel_server: FakeReelServer
) -> None:
    out = io.StringIO()
    result = run_reel(
        letterbox_reel_desk, episode=1, seconds=6.0, stream=out, no_cover=True
    )

    # The final (on the canvas) went up, not the 4:3 picture; the server keeps its bands and text.
    assert [path.name for kind, path in reel_server.uploads] == [
        "take-ep01-t1-sokii-v1.mp4"
    ]
    operator = reel_server.requests[0]["operator"]
    take = operator["takes"][0]
    assert take["captions_burned"] is True and take["marked"] is True
    assert operator["bands_in_source"] is True and operator["music_in_take"] is True
    assert "bed_url" not in operator
    assert result.video is not None
    assert (probe_video(result.video).width, probe_video(result.video).height) == (
        1080,
        1920,
    )
    # The plan says what kind of file it is, so review and the safe zones read it as letterbox.
    plan = json.loads(result.plan_path.read_text(encoding="utf-8"))
    assert plan.get("letterbox") is True and plan["caption_colour"] == "white"
    assert letterbox_file(result.video)
    assert "[letterbox]" in out.getvalue()


def test_the_title_block_is_the_finished_files_and_the_flags_say_so(
    letterbox_reel_desk: Path, reel_server: FakeReelServer
) -> None:
    out = io.StringIO()
    run_reel(
        letterbox_reel_desk,
        episode=1,
        seconds=6.0,
        stream=out,
        no_cover=True,
        no_hook_line=True,
    )

    assert "the title block is part of each take's letterbox file" in out.getvalue()


def test_a_letterbox_take_without_its_letterbox_final_is_cut_as_portrait_with_a_warning(
    letterbox_reel_desk: Path, reel_server: FakeReelServer
) -> None:
    (letterbox_reel_desk / "ep01" / "takes" / "take-ep01-t1-sokii-v1.mp4").unlink()
    out = io.StringIO()

    run_reel(letterbox_reel_desk, episode=1, seconds=6.0, stream=out, no_cover=True)

    assert [path.name for _, path in reel_server.uploads][
        0
    ] == "take-ep01-t1-colour-v1.mp4"
    assert "has no 9:16 letterbox file" in out.getvalue()


@needs_ffmpeg
def test_reel_captions_go_to_the_server_without_a_dash(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    cap = reel_desk / "ep01" / "takes" / "take-ep01-t1-cap-v1.ass"
    cap.write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:00.30,0:00:01.20,House,,0,0,0,,Open the door—\n"
        "Dialogue: 0,0:00:03.10,0:00:04.00,House,,0,0,0,,Who — is there\n"
        "Dialogue: 0,0:00:04.60,0:00:05.30,House,,0,0,0,,It was me–\n",
        encoding="utf-8",
    )
    run_reel(reel_desk, episode=1, seconds=6.0, stream=io.StringIO())

    texts = [c["text"] for c in reel_server.requests[0]["operator"]["takes"][0]["cues"]]
    assert not any(ch in t for t in texts for ch in "—–")
    assert texts[-1] == "It was me…"


def _spine_emphasis(desk: Path, words: dict[str, str]) -> None:
    """Mark the writer's emphasis word on the desk spine's lines (``{line text: word}``)."""

    path = desk / "ep01" / "api" / "spine.json"
    spine = json.loads(path.read_text(encoding="utf-8"))
    body = spine.get("spine", spine)
    for beat in body.get("beats") or []:
        for line in beat.get("dialogue_lines") or []:
            if line.get("text") in words:
                line["emphasis_word"] = words[line["text"]]
    path.write_text(json.dumps(spine), encoding="utf-8")


@needs_ffmpeg
def test_a_bold_show_is_captioned_bold_on_the_server_with_the_writers_emphasis_words(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    _spine_emphasis(reel_desk, {"Open the door": "Open"})
    out = io.StringIO()
    run_reel(reel_desk, episode=1, seconds=6.0, stream=out, caption_style="bold")

    operator = reel_server.requests[0]["operator"]
    assert operator["caption_style"] == "bold"
    cues = operator["takes"][0]["cues"]
    assert cues[0]["emphasis_word"] == "Open"
    # A line the writer did not mark sends none: the server's Bold fallback picks, as the local reel did.
    assert "emphasis_word" not in cues[1] and "emphasis_word" not in cues[2]
    assert "does not take Bold" not in out.getvalue()


@needs_ffmpeg
def test_a_bold_cuts_yellow_word_goes_to_the_server(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    from creation.caption_bold import BOLD_COLOUR, EMPHASIS_COLOUR

    _spine_emphasis(reel_desk, {"Who is there": "Who"})
    yellow, white = f"{{\\c{EMPHASIS_COLOUR}&}}", f"{{\\c{BOLD_COLOUR}&}}"
    (reel_desk / "ep01" / "takes" / "take-ep01-t1-cap-v1.ass").write_text(
        "[Events]\n"
        f"Dialogue: 0,0:00:00.30,0:00:01.20,Bold,,0,0,0,,Open the {yellow}door{white}\n"
        "Dialogue: 0,0:00:03.10,0:00:04.00,Bold,,0,0,0,,Who is there\n",
        encoding="utf-8",
    )
    run_reel(
        reel_desk, episode=1, seconds=6.0, stream=io.StringIO(), caption_style="bold"
    )

    cues = reel_server.requests[0]["operator"]["takes"][0]["cues"]
    # The accepted cut's yellow word first; else the spine's word for the line.
    assert [c.get("emphasis_word") for c in cues] == ["door", "Who"]


@needs_ffmpeg
def test_the_desks_show_style_goes_to_the_server_and_subtle_is_house(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    config = reel_desk / "production.config.json"
    body = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    config.write_text(json.dumps({**body, "caption_style": "bold"}), encoding="utf-8")
    run_reel(reel_desk, episode=1, seconds=6.0, stream=io.StringIO())
    config.write_text(json.dumps({**body, "caption_style": "subtle"}), encoding="utf-8")
    run_reel(reel_desk, episode=1, seconds=6.0, stream=io.StringIO())

    assert [r["operator"]["caption_style"] for r in reel_server.requests] == [
        "bold",
        "house",
    ]


@needs_ffmpeg
def test_watermark_y_and_no_panels_go_to_the_server(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    out = io.StringIO()
    run_reel(
        reel_desk, episode=1, seconds=6.0, stream=out, watermark_y=220, no_panels=True
    )
    run_reel(reel_desk, episode=1, seconds=6.0, stream=io.StringIO())

    marked, plain = (r["operator"] for r in reel_server.requests)
    assert marked["watermark_y"] == 220 and marked["no_panels"] is True
    # Unset, they are left out: the server's defaults (the mark where /export puts it, the spine's panels).
    assert "watermark_y" not in plain and "no_panels" not in plain
    assert "the server puts the mark where /export does" not in out.getvalue()


@needs_ffmpeg
def test_review_of_a_reel_never_touches_run_notes_and_does_not_compare_take_cuts(
    reel_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.cli_produce import main

    notes = reel_desk / "ep01" / "run-notes.md"
    notes.write_text("# run notes\n", encoding="utf-8")
    result = run_reel(reel_desk, episode=1, seconds=6.0, stream=io.StringIO())
    assert result.video is not None
    capsys.readouterr()

    code = main(
        [
            "review",
            "--desk",
            str(reel_desk),
            "--episode",
            "1",
            "--take-file",
            str(result.video),
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert notes.read_text(encoding="utf-8") == "# run notes\n"
    saved = reel_desk / "reels" / "ep01" / f"{result.video.stem}-review-v1.txt"
    assert saved.is_file() and "Review ep01 t1" in saved.read_text(encoding="utf-8")
    assert "(finished," in out
    assert "not compared (the reel cut and reordered the take)" in out
