"""reel on a desk with no finish record: the source, the edits after finish and the captions are worked out."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import needs_ffmpeg

from creation.captions import Cue
from creation.post.edit import cut_frames
from creation.post.reel import parse_ass_cues, run_reel
from creation.post.reel_sources import (
    Edit,
    accepted_from_notes,
    infer_take_source,
    parse_run_notes,
    rebuild_cues,
    shift_cues,
)

SPINE = {
    "title": "Tiny Show",
    "microdrama_genre": "mystery",
    "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1, "title": "The Door"}],
    "frames": [
        {"frame_id": f"f{i}", "episode_id": "episode_01", "board_row": i, "storyboard_group_id": "g1"}
        for i in range(1, 5)
    ],
    "beats": [
        {"beat_id": "b1", "episode_id": "episode_01", "ordinal": 1, "frame_id": "f1",
         "dialogue_lines": [{"line_id": "l1", "text": "Open the door"}]},
        {"beat_id": "b2", "episode_id": "episode_01", "ordinal": 2, "frame_id": "f3",
         "dialogue_lines": [{"line_id": "l2", "text": "Who is there"}]},
        {"beat_id": "b3", "episode_id": "episode_01", "ordinal": 3, "frame_id": "f4",
         "satisfaction_type": "mystery_reveal", "dialogue_lines": [{"line_id": "l3", "text": "It was me"}]},
    ],
}  # fmt: skip

T = "take-ep01-t1"


def notes_text(
    takes: Path,
    *,
    hand: str = "1 voice(s), 1 mute(s), 0 cue(s)",
    new_length: str = "5.500",
) -> str:
    """Run notes as the kit wrote them before finish records (no source on the Trim line)."""

    return f"""# run notes

## 2026-09-26 19:26 UTC

Finish chain on `{T}-v1.mp4` (board: `board-ep01-t1-v1.png`)


## 2026-09-26 19:26 UTC

Colour match to the approved board `board-ep01-t1-v1.png`: `{T}-sfx-v1.mp4` -> `{T}-colour-v1.mp4`
- LUT `{T}-colour-v1.cube`, strength 1


## 2026-09-26 19:26 UTC

Mix: {T}-mix-v1.mp4: -17.5 LUFS, LRA 9.3, TP -1.0 dBTP; ducking 6.9 dB under the voice (24 windows), 0.0 dB outside
- auto take gain -6.9 dB: take at -12.2 LUFS, mix lands at -17.5 LUFS (target -18, band -20 to -15)
- hand layers: {hand}


## 2026-09-26 19:27 UTC

Captions (en from spine.json; lines from the desk) -> `{T}-cap-v1.mp4`: 0.30-1.20 'Open the door'; 3.10-3.90 'Who is there'; 4.60-5.30 "It was me"


## 2026-09-26 19:27 UTC

Watermarked -> `{T}-sokii-v1.mp4` (the un-marked master is `{T}-cap-v1.mp4`)


## 2026-09-26 19:27 UTC

Finish summary
Final: {takes / f"{T}-sokii-v1.mp4"}


## 2026-09-26 19:27 UTC

Trim -> `{takes / f"{T}-sokii-trim-v1.mp4"}`
- cut starts: 2.000 s -> 2.000 s (frame 48, shot change)
- cut ends:   2.500 s -> 2.500 s (frame 60, shot change)
- Removed 0.500 s (2.000-2.500 s). Cues, voice lines and captions at or after 2.500 s on the old file move 0.500 s earlier; anything inside the cut is gone. New length {new_length} s.
"""


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def make_desk(tmp_path: Path, **notes: str) -> Path:
    """An old desk: colour, mix, cap, sokii and a trim of the sokii file, run notes, no finish record, no .ass."""

    desk = tmp_path / "old-desk"
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True)
    (desk / "ep01" / "api").mkdir()
    pieces = [
        f"color=c=0x{g:02x}{g:02x}{g:02x}:s=96x168:d=1.5:r=24"
        for g in (40, 90, 140, 200)
    ]
    inputs = [x for p in pieces for x in ("-f", "lavfi", "-i", p)]
    inputs += ["-f", "lavfi", "-i", "sine=f=440:d=6:sample_rate=48000"]
    colour = takes / f"{T}-colour-v1.mp4"
    _run([*inputs, "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]", "-map", "[v]",
          "-map", "4:a", "-af", "volume=0.3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
          str(colour)])  # fmt: skip
    for step in ("mix", "cap", "sokii"):
        shutil.copyfile(colour, takes / f"{T}-{step}-v1.mp4")
    cut_frames(
        takes / f"{T}-sokii-v1.mp4",
        takes / f"{T}-sokii-trim-v1.mp4",
        begin=48,
        stop=60,
        fps=24.0,
    )
    (desk / "ep01" / "run-notes.md").write_text(
        notes_text(takes, **notes), encoding="utf-8"
    )
    (desk / "ep01" / "api" / "spine.json").write_text(
        json.dumps(SPINE), encoding="utf-8"
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
    return desk


def _snapshot(desk: Path) -> dict[str, tuple[int, float]]:
    return {
        str(p.relative_to(desk)): (p.stat().st_size, p.stat().st_mtime)
        for p in desk.rglob("*")
        if p.is_file() and "reels" not in p.parts
    }


# --- the run notes, no ffmpeg ------------------------------------------------------------------------


def test_old_run_notes_are_read_step_by_step(tmp_path: Path) -> None:
    events = parse_run_notes(notes_text(tmp_path))

    kinds = [e.kind for e in events]
    assert kinds == [
        "finish_chain",
        "colour",
        "mix",
        "captions",
        "watermark",
        "final",
        "trim",
    ]
    mix = events[2]
    assert (mix.detail["voices"], mix.detail["mutes"]) == (1, 1)
    windows = events[3].detail["windows"]
    assert [w[2] for w in windows] == ["Open the door", "Who is there", "It was me"]
    trim = events[-1]
    assert trim.src is None and trim.out == f"{T}-sokii-trim-v1.mp4"
    assert trim.detail["frames"] == [48, 60] and trim.detail["length"] == 5.5


def test_with_no_take_file_the_newest_sokii_file_the_notes_name_is_the_accepted_cut(
    tmp_path: Path,
) -> None:
    takes = tmp_path / "ep01" / "takes"
    takes.mkdir(parents=True)
    (tmp_path / "ep01" / "run-notes.md").write_text(notes_text(takes), encoding="utf-8")
    for name in (f"{T}-sokii-v1.mp4", f"{T}-sokii-trim-v1.mp4"):
        (takes / name).write_bytes(b"")

    assert accepted_from_notes(tmp_path, 1, "t1") == takes / f"{T}-sokii-trim-v1.mp4"
    (takes / f"{T}-sokii-trim-v1.mp4").unlink()
    assert accepted_from_notes(tmp_path, 1, "t1") == takes / f"{T}-sokii-v1.mp4"


def test_captions_are_rebuilt_on_the_accepted_wording_and_windows(
    tmp_path: Path,
) -> None:
    spine = json.loads(json.dumps(SPINE))
    spine["beats"][1]["dialogue_lines"][0]["text"] = (
        "Who is it"  # the spine changed after the cut was accepted
    )
    notes: list[str] = []
    windows = [
        (0.3, 1.2, "Open the door", False),
        (3.1, 3.9, "Who is there", False),
        (4.6, 5.3, "It was me", False),
    ]

    cues = rebuild_cues(spine, desk=tmp_path, episode=1, take_index=1, duration=6.0,
                        windows=windows, notes=notes, label="cap-v1.mp4")  # fmt: skip

    assert cues is not None
    assert cues[0].start == pytest.approx(0.3) and cues[-1].end == pytest.approx(5.3)
    assert any(c.text.endswith("Who is there") for c in cues)
    assert not any("Who is it" in c.text for c in cues)
    assert any("accepted 'Who is there', spine now 'Who is it'" in n for n in notes)
    # No speech timestamps: the words are spread over the windows, and the plan says so.
    assert "caption timing is ESTIMATED" in notes[-1]


def test_captions_are_rebuilt_on_the_words_json_when_the_notes_recorded_no_windows(
    tmp_path: Path,
) -> None:
    words = tmp_path / "words.json"
    chunks = [(0.3, 0.6, "Open"), (0.6, 0.8, "the"), (0.8, 1.2, "door"), (3.1, 3.4, "Who"), (3.4, 3.6, "is"),
              (3.6, 3.9, "there"), (4.6, 4.8, "It"), (4.8, 5.0, "was"), (5.0, 5.3, "me")]  # fmt: skip
    words.write_text(
        json.dumps(
            {"chunks": [{"timestamp": [a, b], "text": f" {t}"} for a, b, t in chunks]}
        )
    )
    notes: list[str] = []

    cues = rebuild_cues(SPINE, desk=tmp_path, episode=1, take_index=1, duration=6.0,
                        words_json=words, notes=notes, label="cap-v1.mp4")  # fmt: skip

    assert cues is not None and cues[0].start == pytest.approx(0.3)
    assert (
        "speech timestamps `words.json`; line 1: speech timestamps, line 2: speech timestamps"
        in notes[-1]
    )
    assert [c.start for c in cues if c.text in ("Who", "Who is", "Who is there")] == [
        3.1,
        3.4,
        3.6,
    ]
    # Neither windows nor words: nothing is made up.
    assert rebuild_cues(SPINE, desk=tmp_path, episode=1, take_index=1, duration=6.0,
                        notes=notes, label="x") is None  # fmt: skip
    assert "captions not rebuilt" in notes[-1]


def test_cues_follow_a_trim_and_a_tempo() -> None:
    trim = Edit("trim", Path("a.mp4"), Path("b.mp4"), frames=(48, 60), fps=24.0)
    cues = (
        Cue(0.3, 1.2, "a"),
        Cue(2.1, 2.4, "inside"),
        Cue(1.8, 2.3, "into"),
        Cue(3.1, 3.9, "b"),
    )

    moved = shift_cues(cues, [trim])

    assert [c.text for c in moved] == ["a", "into", "b"]
    assert moved[1].end == pytest.approx(2.0)  # ran into the cut: ends at it
    assert moved[2].start == pytest.approx(2.6)
    slowed = shift_cues(
        moved, [Edit("tempo", Path("b.mp4"), Path("c.mp4"), factor=0.5)]
    )
    assert slowed[2].start == pytest.approx(5.2)


# --- on real files -----------------------------------------------------------------------------------


@needs_ffmpeg
def test_the_trim_is_followed_and_applied_again_to_the_mix(tmp_path: Path) -> None:
    desk = make_desk(tmp_path)
    takes = desk / "ep01" / "takes"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    before = _snapshot(desk)

    cut, how = infer_take_source(
        desk, 1, "t1", takes / f"{T}-sokii-trim-v1.mp4", spine=SPINE, scratch=scratch
    )

    assert how.source == takes / f"{T}-mix-v1.mp4"
    assert cut.parent == scratch  # the edited copy is never on the desk
    assert [e.as_json()["frames"] for e in how.edits] == [[48, 60]]
    assert how.bed_in_source and not how.burned
    text = "\n".join(how.notes)
    assert (
        f"is a trim of `{T}-sokii-v1.mp4`" in text
        and "6.000 s − 0.500 s = 5.500 s" in text
    )
    assert "1 voice(s), 1 mute(s)" in text
    assert how.cues is not None
    there = next(c for c in how.cues if c.text.endswith("there"))
    assert there.end == pytest.approx(
        3.4, abs=0.01
    )  # 3.90 on the finish timeline, 0.5 s cut before it
    assert _snapshot(desk) == before


@needs_ffmpeg
def test_a_trim_whose_length_does_not_add_up_is_not_followed(tmp_path: Path) -> None:
    desk = make_desk(tmp_path, new_length="4.000")
    takes = desk / "ep01" / "takes"
    accepted = takes / f"{T}-sokii-trim-v1.mp4"

    cut, how = infer_take_source(desk, 1, "t1", accepted, spine=SPINE, scratch=tmp_path)

    assert "not its source, the edit cannot be followed" in "\n".join(how.notes)
    # The finished file behind it is unknown, so the accepted file itself is cut, burned captions kept.
    assert cut == accepted and how.burned and how.cues is None


@needs_ffmpeg
def test_colour_is_the_sound_before_the_bed_when_the_mix_laid_nothing_by_hand(
    tmp_path: Path,
) -> None:
    desk = make_desk(tmp_path, hand="0 voice(s), 0 mute(s), 0 cue(s)")
    takes = desk / "ep01" / "takes"

    _, how = infer_take_source(
        desk, 1, "t1", takes / f"{T}-sokii-trim-v1.mp4", spine=SPINE, scratch=tmp_path
    )

    assert how.source == takes / f"{T}-colour-v1.mp4"
    assert not how.bed_in_source


@needs_ffmpeg
def test_a_desk_with_no_finish_record_reels_from_the_accepted_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    desk = make_desk(tmp_path)
    before = _snapshot(desk)

    result = run_reel(
        desk,
        episode=1,
        seconds=6.0,
        take_files=[str(desk / "ep01" / "takes" / f"{T}-sokii-trim-v1.mp4")],
    )

    out = capsys.readouterr().out
    assert "no finish record names" in out and f"is a trim of `{T}-sokii-v1.mp4`" in out
    assert result.video is not None and result.video.is_file()
    body = json.loads(result.plan_path.read_text(encoding="utf-8"))
    inferred = body["takes"]["t1"]["inferred"]
    assert body["takes"]["t1"]["source"] == f"ep01/takes/{T}-mix-v1.mp4"
    assert inferred["edits"][0]["frames"] == [48, 60] and inferred["bed_in_source"]
    assert result.ass is not None
    texts = [c.text for c in parse_ass_cues(result.ass.read_text(encoding="utf-8"))]
    assert texts[-1].endswith("me")
    assert any("no bed laid" in line for line in result.lines)
    assert _snapshot(desk) == before  # nothing outside reels/ was written or touched

    # The plan re-renders: the inferred take is inferred again from its accepted file.
    again = run_reel(desk, episode=1, plan_file=result.plan_path)
    assert again.video is not None and again.video.name == "reel-ep01-v2.mp4"
