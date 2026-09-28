"""review: caption boxes measured on the filmed frames against the covered zones (#432). Warns, never blocks."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from conftest import needs_ffmpeg

from creation.cli_produce import main
from creation.post.safe_zones import check_safe_zones, run_review, zones_entered

W, H = 192, 336


def caption_take(path: Path, *, top: float | None, width: float = 0.5, colour: str = "0xFFE500") -> Path:
    """A 2 s gray take with a yellow caption-like box whose top edge sits at ``top`` of the height."""

    path.parent.mkdir(parents=True, exist_ok=True)
    video = ["-vf", f"drawbox=x={round(W * (1 - width) / 2)}:y={round(H * top)}:w={round(W * width)}:h=18:"
             f"color={colour}:t=fill"] if top is not None else []  # fmt: skip
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:d=2:r=24", *video,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )  # fmt: skip
    return path


def test_zones_are_the_covered_strips() -> None:
    assert zones_entered((0.3, 0.84, 0.7, 0.9)) == ["bottom band"]
    assert zones_entered((0.3, 0.02, 0.7, 0.06)) == ["top strip"]
    assert zones_entered((0.9, 0.5, 0.95, 0.55)) == ["right rail"]
    assert zones_entered((0.9, 0.1, 0.95, 0.2)) == [], "the right rail covers only the lower two thirds"
    assert zones_entered((0.25, 0.60, 0.75, 0.66)) == []


@needs_ffmpeg
def test_a_caption_in_the_bottom_band_is_measured_and_warned(tmp_path: Path) -> None:
    take = caption_take(tmp_path / "take-ep01-t1-sokii-v1.mp4", top=0.86)
    before = take.read_bytes()

    report = check_safe_zones(take)

    assert all(frame.zones == ["bottom band"] for frame in report.frames)
    assert report.frames[0].caption is not None and report.frames[0].caption[1] == pytest.approx(0.86, abs=0.01)
    assert any("caption in the bottom 20%" in w for w in report.warnings)
    assert any("outside the house band" in w for w in report.warnings)
    assert report.sheet.is_file() and take.read_bytes() == before, "the take is never changed"
    assert "faces: not measured" in report.warnings[-1]


@needs_ffmpeg
def test_a_caption_in_the_house_band_is_clear_and_a_small_yellow_prop_is_not_a_caption(tmp_path: Path) -> None:
    clear = check_safe_zones(caption_take(tmp_path / "clear.mp4", top=0.60))
    assert all(frame.zones == [] and not frame.outside_band for frame in clear.frames)
    assert [w for w in clear.warnings if w.startswith("!!")] == []

    prop = check_safe_zones(caption_take(tmp_path / "prop.mp4", top=0.90, width=0.03))
    assert all(frame.caption is None for frame in prop.frames)
    assert any("no house caption" in w for w in prop.warnings)


@needs_ffmpeg
def test_review_reads_the_newest_file_for_the_take_saves_a_report_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = tmp_path / "ep01" / "takes"
    caption_take(takes / "take-ep01-t1-raw-v1.mp4", top=None)
    final = caption_take(takes / "take-ep01-t1-sokii-v1.mp4", top=0.02)

    assert main(["review", "--desk", str(tmp_path), "--episode", "1", "--take", "t1"]) == 0
    printed = capsys.readouterr().out
    assert "take-ep01-t1-sokii-v1.mp4" in printed and "caption in the top 8%" in printed
    [saved] = takes.glob("take-ep01-t1-sokii-v1-zones-v*.json")
    assert json.loads(saved.read_text())["faces_measured"] is False

    out = io.StringIO()
    run_review(None, take_file=final, out=out)
    assert "zones-v2.png" in out.getvalue(), "a second review writes a new sheet, never over the first"
