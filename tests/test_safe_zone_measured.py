"""``review`` measures the caption box from the burned caption file, with times (Hanakaze #9, Blink #54).

The board's safe-zone lines read written placements only and raised three false alarms.
A finding now needs a measured box: the house caption by colour on sampled frames, and
every cue of the burned ``.ass`` laid out on the frame (a white ``plain`` caption too).
Faces are not measured (no face detector in the kit) and the report says so.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import needs_ffmpeg

from creation.captions import CAPTION_BAND, Cue, build_ass
from creation.post.safe_zones import (
    burned_caption_file,
    caption_layout,
    check_safe_zones,
)
from creation.spine_view import safe_zone_lines

W, H = 1080, 1920

HAND_ASS = """[Script Info]
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Plain,Arial,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,2,2,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.20,0:00:00.90,Plain,,0,0,0,,Blinking!
Dialogue: 0,0:00:01.00,0:00:01.80,Plain,,0,0,0,,{\\an8\\pos(540,40)}Not tonight.
"""


def test_the_kits_own_caption_layout_sits_in_the_house_band(tmp_path: Path) -> None:
    ass = build_ass(
        [Cue(0.0, 1.0, "Wait for me here, Hana. Not tonight.")], width=W, height=H
    )
    path = tmp_path / "take-ep01-t1-cap-v1.ass"
    path.write_text(ass, encoding="utf-8")
    (cue,) = caption_layout(path, width=W, height=H)
    low, high = CAPTION_BAND
    assert cue.zones == []
    assert low <= cue.box[1] < cue.box[3] <= high
    assert cue.box[3] == pytest.approx(0.62, abs=0.01)
    assert 0.0 < cue.box[0] < 0.5 < cue.box[2] < 1.0


def test_a_hand_made_caption_low_or_pinned_to_the_top_is_found_in_its_zone(
    tmp_path: Path,
) -> None:
    path = tmp_path / "take-ep01-t1-cap-v1.ass"
    path.write_text(HAND_ASS, encoding="utf-8")

    low, top = caption_layout(path, width=W, height=H)

    assert low.zones == ["bottom band"] and low.box[3] == pytest.approx(
        1 - 60 / H, abs=1e-3
    )
    assert (low.start, low.end, low.text) == (0.2, 0.9, "Blinking!")
    assert top.zones == ["top strip"] and top.box[1] == pytest.approx(40 / H, abs=1e-3)


def test_the_burned_caption_file_is_found_beside_any_file_of_the_take(
    tmp_path: Path,
) -> None:
    for name in ("take-ep01-t1-cap-v1.ass", "take-ep01-t1-cap-v2.ass"):
        (tmp_path / name).write_text("", encoding="utf-8")
    final = tmp_path / "take-ep01-t1-sokii-v1.mp4"
    assert burned_caption_file(final) == tmp_path / "take-ep01-t1-cap-v2.ass"
    assert burned_caption_file(tmp_path / "other.mp4") is None


@needs_ffmpeg
def test_review_reports_a_white_caption_in_a_covered_zone_with_its_times(
    tmp_path: Path,
) -> None:
    take = tmp_path / "take-ep01-t1-cap-v1.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=270x480:d=2:r=24",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(take)],
        check=True,
    )  # fmt: skip
    take.with_suffix(".ass").write_text(HAND_ASS, encoding="utf-8")

    report = check_safe_zones(take, count=4)

    found = [w for w in report.warnings if w.startswith("!! caption cue(s)")]
    assert any("bottom 20%" in w and "0.20-0.90s 'Blinking!'" in w for w in found), (
        found
    )
    assert any("top 8%" in w and "1.00-1.80s 'Not tonight.'" in w for w in found), found
    first = report.frames[0]  # 0.25 s: no yellow, the layout's cue is showing
    assert first.measured_by == "layout" and first.zones == ["bottom band"]
    assert report.as_json()["caption_layout"].endswith(".ass")
    assert any(
        w.startswith("faces: not measured (no face detector") for w in report.warnings
    )


def test_a_written_placement_alone_is_no_longer_flagged_on_the_board() -> None:
    frame = {"frame_id": "f1", "board_row": 1, "visual_brief": {
        "subject_blocking": [{"cast_id": "cast_hana", "frame_position": "bottom edge, left"}]}}  # fmt: skip

    lines = safe_zone_lines([frame], cast_names={"cast_hana": "Hana"})

    assert not any(line.lstrip().startswith("!!") for line in lines)
    assert 'Hana\'s face is written "bottom edge, left"' in lines[0]
