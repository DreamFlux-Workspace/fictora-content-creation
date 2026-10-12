"""Salvage before re-filming: free edits first (zoom punch-in, trim, tempo, reorder), a reuse list, and the
re-film price naming the free edit (Gallery Heiress review, 12 Oct 2026)."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import needs_ffmpeg, shot_frames, write_frames

from creation.cli_produce import main
from creation.post.edit import BlurBox, punch_in, zoom_crop
from creation.post.finish_record import write_finish_record
from creation.post.lineage import raw_take_behind
from creation.post.media import count_frames, decode_frames
from creation.post.salvage import (
    free_fix_hints,
    free_fix_lines,
    keep_for_reuse,
    needs_refilm,
    refilm_cause_lines,
    reuse_entries,
    reuse_lines,
)

W, H = 96, 168


def _quadrant_frames(count: int = 48) -> list[Any]:
    """A white square in the top-left corner on a blue gradient: what a punch-in pushes out of frame."""

    frames = shot_frames(count, (40, 60, 200))
    for frame in frames:
        frame[0:16, 0:16] = 255
    return frames


def _frames(path: Path) -> np.ndarray:
    return decode_frames(path, width=W, height=H)


def _pcm(path: Path) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip


# --- zoom: the punch-in -----------------------------------------------------------------------------------------


def test_zoom_crop_keeps_the_frame_shape_and_slides_inside_at_an_edge() -> None:
    middle = zoom_crop(1080, 1920, 1.2)
    assert (middle.w, middle.h) == (900, 1600)
    assert (middle.x, middle.y) == (90, 160), "default: the middle of the frame"
    corner = zoom_crop(1080, 1920, 1.2, (10, 10))
    assert (corner.x, corner.y) == (0, 0), (
        "a centre near the edge slides the window inward"
    )
    far = zoom_crop(1080, 1920, 2.0, (1079, 1919))
    assert far == BlurBox(540, 960, 540, 960)
    for bad in (1.0, 0.8, 2.5):
        with pytest.raises(ValueError, match="punch-in"):
            zoom_crop(1080, 1920, bad)
    with pytest.raises(ValueError, match="outside the 1080x1920 frame"):
        zoom_crop(1080, 1920, 1.2, (1200, 10))


@needs_ffmpeg
def test_punch_in_crops_out_the_corner_only_inside_the_window_and_copies_the_sound(
    tmp_path: Path,
) -> None:
    take = write_frames(tmp_path / "take.mp4", _quadrant_frames(), tone=440)
    out = punch_in(
        take, tmp_path / "zoom.mp4", factor=1.5, center=(60, 110), start=1.0, end=1.5
    )

    assert out.crop == zoom_crop(W, H, 1.5, (60, 110)) and not out.whole_take
    assert count_frames(out.output) == count_frames(take), "same length"
    assert _pcm(out.output) == _pcm(take), "the sound is copied untouched"
    before, after = _frames(take), _frames(out.output)
    corner = (slice(2, 12), slice(2, 12))
    assert before[30][corner].mean() > 230, "(the corner square is there)"
    assert after[30][corner].mean() < 200, (
        "inside the window the punch-in crops the corner out"
    )
    for index in (0, 10, 40, 47):
        assert np.abs(after[index] - before[index]).mean() < 2, (
            f"frame {index} (outside the window) unchanged"
        )


@needs_ffmpeg
def test_punch_in_refuses_half_a_window_and_never_overwrites(tmp_path: Path) -> None:
    take = write_frames(tmp_path / "take.mp4", _quadrant_frames(24), tone=440)
    with pytest.raises(ValueError, match="go together"):
        punch_in(take, tmp_path / "a.mp4", start=0.2)
    with pytest.raises(ValueError, match="past the end"):
        punch_in(take, tmp_path / "a.mp4", start=5.0, end=6.0)
    whole = punch_in(take, tmp_path / "a.mp4")
    assert whole.whole_take and whole.factor == 1.15
    with pytest.raises(FileExistsError):
        punch_in(take, tmp_path / "a.mp4")


@needs_ffmpeg
def test_zoom_command_writes_the_file_and_the_chain_and_keeps_the_raw_timeline(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    raw = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _quadrant_frames(), tone=440)

    assert main(["zoom", "--desk", str(post_desk), "--factor", "1.3", "--center", "48,84",
                 "--from", "0.5", "--to", "1.5"]) == 0  # fmt: skip

    said = capsys.readouterr().out
    out = takes / "take-ep01-t1-zoom-v1.mp4"
    assert out.is_file() and "punched in 1.3x" in said and "finish --desk" in said, said
    chain = [
        json.loads(line)
        for line in (takes / "edit-chain.jsonl").read_text().splitlines()
    ]
    assert (
        chain[-1]["op"] == "zoom"
        and chain[-1]["from"] == 0.5
        and chain[-1]["to"] == 1.5
    )
    lineage = raw_take_behind(post_desk, out)
    assert lineage.raw == raw and lineage.keeps_timeline, (
        "finish --take-file lays everything at the filmed times"
    )


@needs_ffmpeg
def test_zoom_refuses_a_finished_file_whose_captions_and_mark_would_zoom_too(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    master = write_frames(
        takes / "take-ep01-t1-cap-v1.mp4", _quadrant_frames(24), tone=440
    )
    final = write_frames(
        takes / "take-ep01-t1-sokii-v1.mp4", _quadrant_frames(24), tone=440
    )
    write_finish_record(post_desk, episode=1, take_id="t1", complete=True, pre_bed=None, master=master,
                        final=final, bed=None, bed_db=-16.5, duck_db=None)  # fmt: skip

    assert main(["zoom", "--desk", str(post_desk), "--take-file", str(final)]) == 2
    assert "finished file" in capsys.readouterr().err
    assert not list(takes.glob("*zoom*"))


# --- the free-fix rules -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flaw", "edit"),
    [
        ("same person rendered twice at shot 3", "trim --cut"),
        ("the male MC looked at himself for half a second", "trim --cut"),
        ("scenes out of order", "join --take-file"),
        ("jump cut as the stranger enters the doorway", "zoom"),
        ("a stray hand in the corner", "zoom"),
    ],
)
def test_flaws_the_review_saved_for_free_point_to_a_free_edit(
    flaw: str, edit: str
) -> None:
    hints = free_fix_hints(flaw)
    assert hints and any(edit in hint for hint in hints), hints
    assert not needs_refilm(flaw)


@pytest.mark.parametrize(
    "flaw",
    [
        "a blood-looking smear across her face",
        "face melts in shot 2",
        "wrong character for the whole take",
    ],
)
def test_a_smear_on_a_face_or_the_wrong_person_throughout_goes_straight_to_a_refilm(
    flaw: str,
) -> None:
    assert needs_refilm(flaw)
    assert free_fix_hints(flaw) == [] and refilm_cause_lines(flaw) == []


def test_the_free_fix_block_names_every_free_edit_the_short_flaw_rule_and_the_reuse_list() -> (
    None
):
    text = "\n".join(free_fix_lines("D", 2, "t1", take_file="F.mp4"))
    for command in ("trim --desk D --take-file F.mp4 --cut", "trim --desk D --episode 2 --take t1 --start",
                    "tempo --desk D --take-file F.mp4", "zoom --desk D --episode 2 --take t1", "join --desk D",
                    "keep-take --desk D --episode 2 --take t1"):  # fmt: skip
        assert command in text, command
    assert "0.5 s or less is a cut, never a re-film" in text
    assert "smear or melt on a face" in text
    for word in ("$", "model", "fal", "seedance", "h3"):
        assert word not in text.lower().replace("model voices", ""), word


# --- the re-film price names the free edit first -----------------------------------------------------------------


def test_refilm_price_names_the_free_edit_when_the_cause_reads_like_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = refilm_cause_lines("same person rendered twice at shot 2")
    assert lines[0].startswith("!! salvage first") and any(
        "trim --cut" in line for line in lines
    )
    plain = refilm_cause_lines(
        "the ring leaves her hand before the line: move the drop after 'Not for me'"
    )
    assert len(plain) == 1 and plain[0].startswith("Salvage first")


# --- the reuse list ---------------------------------------------------------------------------------------------


def test_keep_take_adds_a_decent_rejected_take_to_the_reuse_list(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    (takes / "take-ep01-t1-raw-v1.mp4").write_bytes(b"mp4")
    (takes / "take-ep01-t1-raw-v2.mp4").write_bytes(b"mp4")

    assert main(["keep-take", "--desk", str(post_desk), "--episode", "1", "--take", "t1",
                 "--note", "decent doorway wide; could be a flashback later"]) == 0  # fmt: skip
    said = capsys.readouterr().out
    assert "Kept for reuse: ep01 t1 `ep01/takes/take-ep01-t1-raw-v2.mp4`" in said, said
    assert (
        reuse_entries(post_desk)[0]["note"]
        == "decent doorway wide; could be a flashback later"
    )

    assert main(["keep-take", "--desk", str(post_desk), "--list"]) == 0
    assert "Takes kept for reuse (1):" in capsys.readouterr().out


def test_keep_take_needs_a_note_and_an_empty_desk_says_so(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--note"):
        keep_for_reuse(tmp_path, episode=1, take_id="t1", note="  ")
    assert reuse_lines(tmp_path) == ["No takes kept for reuse on this desk yet."]
    entry = keep_for_reuse(tmp_path, episode=3, take_id="t2", note="good reaction shot",
                           now=datetime(2026, 10, 12, tzinfo=UTC))  # fmt: skip
    assert entry == {"episode": 3, "take": "t2", "file": None, "note": "good reaction shot",
                     "kept_at": "2026-10-12T00:00:00+00:00"}  # fmt: skip
