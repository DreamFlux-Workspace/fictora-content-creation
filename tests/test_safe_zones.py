"""review: burned captions checked against the covered zones (#432) by the server's check. Warns, never blocks.

Since 24 Oct 2026 (fictora-drama #1029) the take check sends the burned caption
file to ``POST /v1/captions/safe-area-check`` and no longer looks for captions
by yellow pixels: that boxed a yellow raincoat and a brass lamp as captions on
the Pawn Ticket paid test. An older server falls back to the kit's own reader.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import needs_ffmpeg

from creation.caption_server import SAFE_AREA_CHECK_ROUTE, server_override
from creation.cli_produce import main
from creation.post.safe_zones import (
    check_safe_zones,
    run_review,
    zones_entered,
)

W, H = 192, 336

ASS_HEAD = f"""[Script Info]
PlayResX: {W}
PlayResY: {H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: House,Arial,11,&H0000E5FF,&H0000E5FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,1,1,2,30,30,{{margin_v}},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,House,,0,0,0,,Not for sale.
"""


def yellow_take(path: Path, *, top: float | None, width: float = 0.5) -> Path:
    """A 2 s gray take with a filled house-yellow box (a raincoat, a lamp) whose top edge sits at ``top``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    video = ["-vf", f"drawbox=x={round(W * (1 - width) / 2)}:y={round(H * top)}:w={round(W * width)}:h=18:"
             "color=0xFFE500:t=fill"] if top is not None else []  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:d=2:r=24", *video,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    return path


def caption_file(take: Path, *, bottom: float) -> Path:
    """The take's burned caption file, one cue whose bottom edge sits at ``bottom`` of the height."""

    path = take.with_suffix(".ass")
    path.write_text(ASS_HEAD.format(margin_v=round(H * (1 - bottom))), encoding="utf-8")
    return path


def test_zones_are_the_covered_strips() -> None:
    assert zones_entered((0.3, 0.84, 0.7, 0.9)) == ["bottom band"]
    assert zones_entered((0.3, 0.02, 0.7, 0.06)) == ["top strip"]
    assert zones_entered((0.9, 0.5, 0.95, 0.55)) == ["right rail"]
    assert zones_entered((0.9, 0.1, 0.95, 0.2)) == [], (
        "the right rail covers only the lower two thirds"
    )
    assert zones_entered((0.25, 0.60, 0.75, 0.66)) == []


@needs_ffmpeg
def test_yellow_scenery_is_never_a_caption(tmp_path: Path) -> None:
    """A yellow raincoat in the rail and a lamp at the top: no caption file, so no caption finding."""

    take = yellow_take(tmp_path / "take-ep01-t1-sokii-v1.mp4", top=0.86, width=0.9)
    before = take.read_bytes()

    report = check_safe_zones(take)

    assert all(frame.caption is None and frame.zones == [] for frame in report.frames)
    assert [w for w in report.warnings if w.startswith("!!")] == []
    assert any(w.startswith("no burned caption file") for w in report.warnings)
    assert report.checked_by is None
    assert report.sheet.is_file() and take.read_bytes() == before, (
        "the take is never changed"
    )
    assert "faces: not measured" in report.warnings[-1]


@needs_ffmpeg
def test_a_clean_caption_file_over_yellow_scenery_is_clear(tmp_path: Path) -> None:
    take = yellow_take(tmp_path / "take-ep01-t1-sokii-v1.mp4", top=0.86, width=0.9)
    caption_file(take, bottom=0.62)

    report = check_safe_zones(take)

    assert report.checked_by == "kit"  # the tests' server is an older one (conftest)
    assert all(frame.zones == [] and not frame.outside_band for frame in report.frames)
    assert all(
        frame.caption is not None and frame.measured_by == "layout"
        for frame in report.frames
    )
    assert [w for w in report.warnings if w.startswith("!!")] == []


@needs_ffmpeg
def test_a_caption_in_the_bottom_band_is_warned_with_its_times(tmp_path: Path) -> None:
    take = yellow_take(tmp_path / "take-ep01-t1-sokii-v1.mp4", top=None)
    caption_file(take, bottom=0.92)

    report = check_safe_zones(take)

    assert all(frame.zones == ["bottom band"] for frame in report.frames)
    assert any(
        "caption in the bottom 20%" in w and "0.00-2.00s 'Not for sale.'" in w
        for w in report.warnings
    ), report.warnings
    assert any("outside the caption band" in w for w in report.warnings)


@needs_ffmpeg
@pytest.mark.real_caption_server
def test_the_server_checks_the_burned_caption_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    take = yellow_take(tmp_path / "take-ep01-t1-sokii-v1.mp4", top=0.86, width=0.9)
    ass = caption_file(take, bottom=0.92)
    sent: list[tuple[str, dict[str, Any]]] = []

    def server(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        sent.append((path, body))
        cue = {"start_ms": 0, "end_ms": 2000, "text": "Not for sale.", "box": [0.3, 0.85, 0.7, 0.92],
               "zones": ["bottom band"], "in_band": False}  # fmt: skip
        findings = [
            {"kind": "zone", "zone": "bottom band", "start_ms": 0, "end_ms": 2000, "text": "Not for sale.",
             "message": "caption in the bottom band at 0.00-2.00s 'Not for sale.'"},
            {"kind": "band", "zone": None, "start_ms": 0, "end_ms": 2000, "text": "Not for sale.",
             "message": "caption outside the caption band (55%-70% of the height) at 0.00-2.00s 'Not for sale.'"},
        ]  # fmt: skip
        return 200, {
            "cues": [cue],
            "findings": findings,
            "clear": False,
            "letterbox": False,
        }

    with server_override(server):
        report = check_safe_zones(take)

    assert sent == [
        (
            SAFE_AREA_CHECK_ROUTE,
            {"ass": ass.read_text(encoding="utf-8"), "letterbox": False},
        )
    ]
    assert report.checked_by == "server"
    assert all(
        f.measured_by == "server" and f.zones == ["bottom band"] and f.outside_band
        for f in report.frames
    )
    found = [w for w in report.warnings if w.startswith("!!")]
    assert found == [
        "!! caption in the bottom 20% (post caption, username, music): the platform covers it "
        f"(server check of `{ass.name}`): 0.00-2.00s 'Not for sale.'",
        f"!! caption outside the caption band (55%-70% of the height) (server check of `{ass.name}`): "
        "0.00-2.00s 'Not for sale.'",
    ]
    assert "NOTE captions" not in capsys.readouterr().err


@needs_ffmpeg
@pytest.mark.real_caption_server
def test_a_server_that_cannot_be_reached_falls_back_to_the_kits_reader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    take = yellow_take(tmp_path / "take-ep01-t1-sokii-v1.mp4", top=None)
    caption_file(take, bottom=0.62)

    def down(path: str, body: dict[str, Any]) -> tuple[int, Any]:
        raise OSError("connection refused")

    with server_override(down):
        report = check_safe_zones(take)

    assert report.checked_by == "kit"
    assert "NOTE captions: the server could not be reached" in capsys.readouterr().err


@needs_ffmpeg
def test_review_reads_the_newest_file_for_the_take_saves_a_report_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = tmp_path / "ep01" / "takes"
    yellow_take(takes / "take-ep01-t1-raw-v1.mp4", top=None)
    final = yellow_take(takes / "take-ep01-t1-sokii-v1.mp4", top=None)
    caption_file(final, bottom=0.07)

    assert (
        main(["review", "--desk", str(tmp_path), "--episode", "1", "--take", "t1"]) == 0
    )
    printed = capsys.readouterr().out
    assert "take-ep01-t1-sokii-v1.mp4" in printed and "caption in the top 8%" in printed
    [saved] = takes.glob("take-ep01-t1-sokii-v1-zones-v*.json")
    saved_json = json.loads(saved.read_text())
    assert saved_json["faces_measured"] is False
    assert saved_json["captions_checked_by"] == "kit"

    out = io.StringIO()
    run_review(None, take_file=final, out=out)
    assert "zones-v2.png" in out.getvalue(), (
        "a second review writes a new sheet, never over the first"
    )
