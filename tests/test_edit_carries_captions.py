"""An edit after finish carries the take's captions onto the edited master (Lost & Found canary, 7 Oct 2026).

The canary: after ``finish``, ``blur --take-file …handles-v1.mp4`` wrote record
``take-ep01-t1-finish-v3.json`` whose master ``take-ep01-t1-blur-master-v1.mp4``
had no ``.ass``; the reel (server and local) and the clips then said "t1: no
accepted caption cues (.ass); the reel is uncaptioned".
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import needs_ffmpeg, shot_frames, write_frames

from creation.cli_produce import main
from creation.post.edit_captions import captions_for_record, carry_ass
from creation.post.finish_record import finish_records, write_finish_record
from creation.post.reel import parse_ass_cues, take_sources

BLUE = (40, 60, 200)
YELLOW = (250, 220, 60)
ASS = (
    "[Script Info]\nPlayResX: 1080\n\n[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:00.50,0:00:01.00,House,,0,0,0,,Open the door\n"
    "Dialogue: 0,0:00:01.20,0:00:02.00,House,,0,0,0,,Who is\n"
    "Dialogue: 0,0:00:01.80,0:00:02.20,House,,0,0,0,,there\n"
    "Dialogue: 0,0:00:02.60,0:00:02.90,House,,0,0,0,,It was me\n"
)


def _times(path: Path) -> list[tuple[float, float, str]]:
    return [
        (round(c.start, 2), round(c.end, 2), c.text)
        for c in parse_ass_cues(path.read_text(encoding="utf-8"))
    ]


# --- the re-time, one edit at a time ---------------------------------------------------------------


@pytest.mark.parametrize(
    "edit",
    [
        {"op": "blur", "boxes": [[0, 0, 10, 10]], "from": 0.0, "to": 1.0},
        {"op": "freeze", "at": 1.0, "hold": 0.6},
        {"op": "soften", "cuts": [1.5]},
    ],
)
def test_an_edit_that_keeps_timing_copies_the_cues(tmp_path: Path, edit: dict) -> None:
    source = tmp_path / "m.ass"
    source.write_text(ASS, encoding="utf-8")

    carried = carry_ass(source, tmp_path / "out.ass", [edit])

    assert carried.out is not None and carried.kept == 4 and carried.dropped == 0
    assert _times(carried.out) == _times(source)
    assert "[Script Info]" in carried.out.read_text(), (
        "the styles and header come along"
    )


def test_a_trim_moves_the_cues_after_the_cut_earlier_and_drops_what_was_inside(
    tmp_path: Path,
) -> None:
    source = tmp_path / "m.ass"
    source.write_text(ASS, encoding="utf-8")

    carried = carry_ass(
        source, tmp_path / "out.ass", [{"op": "trim", "frames": [36, 60], "fps": 24.0}]
    )

    assert carried.out is not None
    assert _times(carried.out) == [
        (0.5, 1.0, "Open the door"),
        (1.2, 1.5, "Who is"),  # ran into the cut: clipped to it
        (1.6, 1.9, "It was me"),  # 1.0 s earlier
    ]
    assert carried.dropped == 1 and "fell inside the cut" in carried.line


def test_a_tempo_moves_the_cues_the_way_the_picture_was_sped(tmp_path: Path) -> None:
    source = tmp_path / "m.ass"
    source.write_text(ASS, encoding="utf-8")

    whole = carry_ass(source, tmp_path / "whole.ass", [{"op": "tempo", "factor": 2.0}])
    window = carry_ass(
        source,
        tmp_path / "window.ass",
        [{"op": "tempo", "factor": 2.0, "from": 1.0, "to": 2.0}],
    )

    assert whole.out is not None and _times(whole.out)[0] == (
        0.25,
        0.5,
        "Open the door",
    )
    assert window.out is not None and _times(window.out) == [
        (0.5, 1.0, "Open the door"),
        (1.1, 1.5, "Who is"),
        (1.4, 1.7, "there"),
        (2.1, 2.4, "It was me"),
    ]


def test_an_edit_the_kit_cannot_retime_writes_nothing_and_says_so_loudly(
    tmp_path: Path,
) -> None:
    source = tmp_path / "m.ass"
    source.write_text(ASS, encoding="utf-8")

    carried = carry_ass(source, tmp_path / "out.ass", [{"op": "warp"}])

    assert carried.out is None and not (tmp_path / "out.ass").exists()
    assert (
        carried.line.startswith("!! captions NOT carried")
        and "uncaptioned" in carried.line
    )


# --- the canary desk: a record whose master an older blur wrote without captions --------------------


def _record_desk(desk: Path) -> Path:
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True)
    for name in (
        "handles-prebed-v1",
        "handles-master-v1",
        "handles-v1",
        "blur-master-v1",
        "blur-v1",
    ):
        (takes / f"take-ep01-t1-{name}.mp4").write_bytes(b"x")
    (takes / "take-ep01-t1-handles-master-v1.ass").write_text(ASS, encoding="utf-8")
    write_finish_record(desk, episode=1, take_id="t1", complete=True, pre_bed=takes / "take-ep01-t1-handles-prebed-v1.mp4",
                        master=takes / "take-ep01-t1-handles-master-v1.mp4", final=takes / "take-ep01-t1-handles-v1.mp4",
                        bed=None, bed_db=-16.5, duck_db=None)  # fmt: skip
    write_finish_record(desk, episode=1, take_id="t1", complete=True, pre_bed=takes / "take-ep01-t1-handles-prebed-v1.mp4",
                        master=takes / "take-ep01-t1-blur-master-v1.mp4", final=takes / "take-ep01-t1-blur-v1.mp4",
                        bed=None, bed_db=-16.5, duck_db=None,
                        edits=[{"op": "blur", "boxes": [[280, 690, 300, 160]], "from": 7.0, "to": 11.0,
                                "from_record": "take-ep01-t1-finish-v1.json"}])  # fmt: skip
    return takes


def test_the_reel_finds_the_captions_of_a_master_an_older_blur_wrote_without_them(
    tmp_path: Path,
) -> None:
    takes = _record_desk(tmp_path / "desk")
    out = io.StringIO()

    [source] = take_sources(tmp_path / "desk", 1, stream=out)

    assert source.captions == takes / "take-ep01-t1-blur-master-v1.ass", out.getvalue()
    assert _times(source.captions) == _times(
        takes / "take-ep01-t1-handles-master-v1.ass"
    )
    assert (
        "carried from `take-ep01-t1-handles-master-v1.ass` through the blur"
        in out.getvalue()
    )


def test_a_record_with_no_captions_anywhere_stays_uncaptioned(tmp_path: Path) -> None:
    takes = _record_desk(tmp_path / "desk")
    (takes / "take-ep01-t1-handles-master-v1.ass").unlink()

    record = finish_records(tmp_path / "desk", 1)[-1]

    assert captions_for_record(tmp_path / "desk", record) == (None, [])


# --- the real edits on a finished take ----------------------------------------------------------------


def _finished(post_desk: Path) -> tuple[Path, Path]:
    takes = post_desk / "ep01" / "takes"
    files = {}
    for name in ("colour-v1", "cap-v1", "sokii-v1"):
        files[name] = write_frames(
            takes / f"take-ep01-t1-{name}.mp4",
            shot_frames(36, BLUE) + shot_frames(36, YELLOW, start=36),
        )
    files["cap-v1"].with_suffix(".ass").write_text(ASS, encoding="utf-8")
    write_finish_record(post_desk, episode=1, take_id="t1", complete=True, pre_bed=files["colour-v1"],
                        master=files["cap-v1"], final=files["sokii-v1"], bed=None, bed_db=-16.5, duck_db=None)  # fmt: skip
    return takes, files["sokii-v1"]


@needs_ffmpeg
def test_after_blur_the_reel_finds_the_captions(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes, final = _finished(post_desk)

    assert main(["blur", "--desk", str(post_desk), "--take-file", str(final), "--box", "20,40,40,30",
                 "--from", "0.5", "--to", "2.5", "--no-reel", "--no-clips"]) == 0  # fmt: skip

    said = capsys.readouterr().out
    carried = takes / "take-ep01-t1-blur-master-v1.ass"
    assert carried.is_file(), said
    assert (
        "- Captions: `take-ep01-t1-cap-v1.ass` -> `take-ep01-t1-blur-master-v1.ass`"
        in said
    )
    [source] = take_sources(post_desk, 1, stream=io.StringIO())
    assert source.captions == carried and _times(carried) == _times(
        takes / "take-ep01-t1-cap-v1.ass"
    )


@needs_ffmpeg
def test_after_a_trim_the_cues_are_shifted_like_the_picture(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes, final = _finished(post_desk)

    assert main(["trim", "--desk", str(post_desk), "--take-file", str(final), "--cut", "1.46-2.5",
                 "--no-reel", "--no-clips"]) == 0  # fmt: skip

    said = capsys.readouterr().out
    carried = takes / "take-ep01-t1-trim-master-v1.ass"
    assert carried.is_file(), said
    assert _times(carried) == [
        (0.5, 1.0, "Open the door"),
        (1.2, 1.5, "Who is"),
        (1.6, 1.9, "It was me"),
    ]
    [source] = take_sources(post_desk, 1, stream=io.StringIO())
    assert source.captions == carried
