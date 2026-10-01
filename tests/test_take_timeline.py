"""Take facts stay on the take's own timeline: the server's board-frame hold, and takes it cut (#543).

Hanakaze ep 7 v3: the server cut 10 head board frames and 0.417 s of the
locked-voice track, and the take facts kept the filmed windows, so captions and
ducking ran 0.42 s late. The server now holds board frames (``board_frames`` in
the facts, nothing moves) and ``deboard`` finds nothing to do on such a take; a
take stored while the server cut is measured against its track and re-timed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import board_array, needs_ffmpeg, shot_frames, write_frames
from PIL import Image

from creation.post.deboard import deboard, measure_board_leak
from creation.post.media import count_frames
from creation.post.take_timeline import (
    SHIFTED_KEY,
    align_take_facts,
    facts_shift_s,
    measure_track_lag,
    server_board_frames,
    shift_take_facts,
)

BLUE = (60, 90, 220)
#: The ep 7 v3 cut: 10 frames at 24 fps.
EP7_CUT = 10 / 24
WINDOWS = ((0.5, 1.4, 440), (2.2, 2.9, 660), (3.6, 4.4, 520))


def _facts(*, board_frames: dict | None = None) -> dict:
    return {
        "take_facts": {
            "job_id": "job_video_scene_83ef142d7840c481d81a90a8",
            "duration_seconds": 5,
            "soundtrack": {
                "mode": "target_audio",
                "reason": None,
                "track_url": "https://assets.test/tracks/ep07-t3.wav",
                "lines": [
                    {
                        "line_id": f"l{i}",
                        "cast_id": "hana",
                        "start_s": a,
                        "end_s": b,
                        "off_screen": False,
                    }
                    for i, (a, b, _f) in enumerate(WINDOWS, start=1)
                ],
                "native_foley": False,
            },
            "shots": [
                {
                    "shot_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "speaks": True,
                },
                {
                    "shot_index": 2,
                    "start_seconds": 2.0,
                    "end_seconds": 5.0,
                    "speaks": True,
                },
            ],
            "lines": [
                {
                    "line_id": "l1",
                    "count": 1,
                    "shot_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                }
            ],
            "sfx_cues": [
                {
                    "shot_index": 2,
                    "sound": "door",
                    "start_seconds": 3.0,
                    "duration_seconds": 0.5,
                }
            ],
            "board_frames": board_frames,
        }
    }


def _save(desk: Path, payload: dict) -> Path:
    api = desk / "ep07" / "api"
    api.mkdir(parents=True, exist_ok=True)
    path = api / "take-facts-ep07-t3-v1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _filmed(tmp_path: Path) -> tuple[Path, Path]:
    """A 5 s take on the track's timeline, and the track itself (the take's sound)."""

    take = write_frames(tmp_path / "filmed.mp4", shot_frames(120, BLUE), tones=WINDOWS)
    track = tmp_path / "track.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(take),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            str(track),
        ],
        check=True,
    )
    return take, track


def _cut_head(take: Path, out: Path, seconds: float) -> Path:
    """What #543 stored: picture and sound both start ``seconds`` in."""

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{seconds:.6f}", "-i", str(take),
         "-c:v", "libx264", "-c:a", "aac", str(out)],
        check=True,
    )  # fmt: skip
    return out


def _local(track: Path):  # type: ignore[no-untyped-def]
    def fetch(url: str, dest: Path) -> Path:
        assert url == "https://assets.test/tracks/ep07-t3.wav"
        shutil.copyfile(track, dest)
        return dest

    return fetch


@needs_ffmpeg
def test_a_take_the_server_cut_gets_its_facts_moved_to_the_clip(tmp_path: Path) -> None:
    filmed, track = _filmed(tmp_path)
    stored = _cut_head(filmed, tmp_path / "stored.mp4", EP7_CUT)
    facts_path = _save(tmp_path, _facts())

    checked = align_take_facts(
        tmp_path,
        episode=7,
        take_id="t3",
        take=stored,
        facts_path=facts_path,
        fetch_track=_local(track),
    )

    assert checked.facts is not None and checked.facts != facts_path
    assert checked.facts.name == "take-facts-ep07-t3-v2.json"
    assert "10 head frame(s)" in checked.note
    moved = json.loads(checked.facts.read_text(encoding="utf-8"))["take_facts"]
    assert moved[SHIFTED_KEY] == pytest.approx(EP7_CUT, abs=0.002)
    for line, (start, end, _f) in zip(
        moved["soundtrack"]["lines"], WINDOWS, strict=True
    ):
        assert line["start_s"] == pytest.approx(start - EP7_CUT, abs=0.002)
        assert line["end_s"] == pytest.approx(end - EP7_CUT, abs=0.002)
    assert moved["shots"][0]["start_seconds"] == 0.0
    assert moved["shots"][1]["start_seconds"] == pytest.approx(2.0 - EP7_CUT, abs=0.002)
    assert moved["sfx_cues"][0]["start_seconds"] == pytest.approx(
        3.0 - EP7_CUT, abs=0.002
    )
    # The original stays as it was: versions are never overwritten.
    assert (
        SHIFTED_KEY
        not in json.loads(facts_path.read_text(encoding="utf-8"))["take_facts"]
    )


@needs_ffmpeg
def test_a_take_on_its_own_timeline_is_left_alone(tmp_path: Path) -> None:
    filmed, track = _filmed(tmp_path)
    facts_path = _save(tmp_path, _facts())

    def never(url: str, dest: Path) -> Path:
        raise AssertionError("a full-length take is not measured")

    checked = align_take_facts(
        tmp_path,
        episode=7,
        take_id="t3",
        take=filmed,
        facts_path=facts_path,
        fetch_track=never,
    )

    assert checked.facts == facts_path
    assert "full length" in checked.note
    assert measure_track_lag(filmed, track) == pytest.approx(0.0, abs=0.002)


@needs_ffmpeg
def test_a_short_take_whose_track_cannot_be_read_says_so(tmp_path: Path) -> None:
    filmed, _track = _filmed(tmp_path)
    stored = _cut_head(filmed, tmp_path / "stored.mp4", EP7_CUT)
    facts_path = _save(tmp_path, _facts())

    def offline(url: str, dest: Path) -> Path:
        raise OSError("offline")

    checked = align_take_facts(
        tmp_path,
        episode=7,
        take_id="t3",
        take=stored,
        facts_path=facts_path,
        fetch_track=offline,
    )

    assert checked.facts == facts_path and checked.warning
    assert checked.note.startswith("!! take timeline")


def test_a_take_the_server_held_is_never_shifted_or_measured(tmp_path: Path) -> None:
    record = {"head_frames": 10, "tail_frames": 0, "head_s": 0.417, "tail_s": 0.0, "frame_rate": 24.0,
              "timeline_shift_s": 0.0}  # fmt: skip
    facts_path = _save(tmp_path, _facts(board_frames=record))

    def never(url: str, dest: Path) -> Path:
        raise AssertionError("a held take is not measured")

    checked = align_take_facts(
        tmp_path,
        episode=7,
        take_id="t3",
        take=tmp_path / "unused.mp4",
        facts_path=facts_path,
        fetch_track=never,
    )

    assert checked.facts == facts_path
    assert "server held 10 start / 0 end" in checked.note
    held = server_board_frames(json.loads(facts_path.read_text(encoding="utf-8")))
    assert held is not None and (held.head_frames, held.tail_frames) == (10, 0)


def test_facts_moved_once_are_not_moved_again(tmp_path: Path) -> None:
    once = shift_take_facts(_facts(), EP7_CUT)
    path = _save(tmp_path, once)

    checked = align_take_facts(
        tmp_path, episode=7, take_id="t3", take=tmp_path / "x.mp4", facts_path=path
    )

    assert checked.facts == path
    assert facts_shift_s(once) == pytest.approx(0.417)


def test_a_window_inside_the_cut_is_dropped_and_starts_clamp_at_zero() -> None:
    payload = _facts()
    payload["take_facts"]["sfx_cues"].append(
        {"shot_index": 1, "sound": "tap", "start_seconds": 0.1}
    )
    payload["take_facts"]["shots"].insert(
        0, {"shot_index": 0, "start_seconds": 0.0, "end_seconds": 0.3}
    )

    moved = shift_take_facts(payload, EP7_CUT)["take_facts"]

    assert [shot["shot_index"] for shot in moved["shots"]] == [1, 2]
    assert moved["shots"][0]["start_seconds"] == 0.0
    assert moved["sfx_cues"][1]["start_seconds"] == 0.0


@needs_ffmpeg
def test_deboard_finds_nothing_on_a_take_the_server_already_held(
    tmp_path: Path,
) -> None:
    """No double trim: the kit's deboard holds like the server does, so a second pass writes nothing."""

    board = tmp_path / "board.png"
    Image.fromarray(board_array()).save(board)
    raw = write_frames(
        tmp_path / "raw.mp4", [board_array()] * 10 + shot_frames(62, BLUE)
    )
    held = deboard(raw, board, tmp_path / "held.mp4")
    assert held.output is not None and held.removed == 10

    again = deboard(held.output, board, tmp_path / "again.mp4")

    assert again.output is None and again.removed == 0
    assert not (tmp_path / "again.mp4").exists()
    assert count_frames(held.output) == count_frames(raw)
    assert measure_board_leak(held.output, board).frames == 0
