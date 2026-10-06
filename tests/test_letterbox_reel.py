"""``reel`` on a letterbox show: the same 9:16 layout as finish and join (6 Oct 2026).

A letterbox show's reel must come out like the episode's own file: the
1080x1920 black canvas, the picture at y 555-1365, the title block above it,
captions in the band under it and the Sokii mark in the top band. The server's
reel engine cuts it from each take's letterbox final (the file ``finish``
made on the canvas), keeping its bands, captions and mark, as it cuts the
app's letterbox delivery.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from conftest import needs_ffmpeg

from creation.post.finish_record import write_finish_record
from creation.post.letterbox import pad_to_canvas
from creation.post.media import probe_video
from creation.post.reel import run_reel
from creation.post.safe_zones import letterbox_file
from reel_fake_server import FakeReelServer

pytestmark = needs_ffmpeg

#: Bright picture greys, so the black bands read clearly against it.
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
    return desk


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


def test_review_reads_a_letterbox_reel_s_picture_only(
    letterbox_reel_desk: Path, reel_server: FakeReelServer
) -> None:
    from creation.post.review import review_take

    result = run_reel(
        letterbox_reel_desk, episode=1, seconds=6.0, stream=io.StringIO(), no_cover=True
    )
    assert result.video is not None

    review = review_take(
        letterbox_reel_desk, take_file=result.video, face_detector=None,
        text_ocr=lambda frames, languages: "",
    )  # fmt: skip

    frames = next(s for s in review.sections if s.name == "Frames")
    assert frames.data.get("region") == [0, 555, 1080, 810]
    opening = next(s for s in review.sections if s.name == "Opening")
    assert opening.data["opening"]["frame0_luma"] > 0.3, (
        "the picture's brightness, not the bands'"
    )
