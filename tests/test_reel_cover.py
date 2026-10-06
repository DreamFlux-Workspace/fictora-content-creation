"""Reel part covers, Part wording, posting lanes and the results sheet (founder decision, 6 Oct 2026)."""

from __future__ import annotations

import csv
import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from conftest import needs_ffmpeg
from PIL import Image
from test_reel import reel_desk  # noqa: F401  (fixture)

from creation.post.reel_cover import (
    METRICS_COLUMNS,
    append_metrics_row,
    cover_layout,
    cover_path,
)
from creation.post.reel_plan import (
    genre_family,
    post_operator_notes,
    post_text,
    posting_warnings,
)
from creation.post.safe_zones import caption_mask, zones_entered

# --- the post text: Part wording -----------------------------------------------------------------


def test_post_text_says_part_n_and_follow_for_the_next_part() -> None:
    text = post_text(
        series="Night Shift", episode=3, title="The Jacket",
        premise_line="The night-shift clerk is wearing his jacket.", genre="mystery",
    )  # fmt: skip
    lines = text.splitlines()
    assert lines[0] == "The night-shift clerk is wearing his jacket."
    assert lines[1] == "Night Shift · Part 3: The Jacket"
    assert "Follow for part 4." in lines
    assert "Episode" not in text


def test_the_last_part_says_follow_for_the_next_one() -> None:
    text = post_text(series="Night Shift", episode=3, has_next=False)
    assert "Follow for the next one." in text and "part 4" not in text


def test_operator_notes_name_the_cover_the_sound_and_the_lane() -> None:
    notes = post_operator_notes(
        cover="reel-ep03-v1-cover-v1.jpg", account="@nightshift.drama",
        lane="mystery", posting_slot="18:30 IST",
    )  # fmt: skip
    text = "\n".join(notes)
    assert "Edit cover" in text and "Add from camera roll" in text
    assert "reel-ep03-v1-cover-v1.jpg" in text
    assert "trending sound" in text
    assert "@nightshift.drama" in text and "mystery" in text and "18:30 IST" in text


def test_a_missing_posting_slot_warns_and_never_blocks() -> None:
    assert posting_warnings(account="@a", posting_slot="18:30 IST") == []
    warned = posting_warnings(account="@a", posting_slot=None)
    assert len(warned) == 1 and "@a" in warned[0] and "posting_slot" in warned[0]
    assert "posting.md" in warned[0]


# --- genre families ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "genre",
    [
        "system", "leveling_system", "levelling", "regression", "regressor_revenge",
        "apocalypse", "last_human", "isekai", "cultivation", "hunter", "dungeon_crawl", "awakening",
    ],
)  # fmt: skip
def test_power_fantasy_genres_score_as_action(genre: str) -> None:
    assert genre_family(genre) == "action"


def test_a_lone_last_or_human_is_not_the_last_human() -> None:
    assert genre_family("the_last_dance") == "default"
    assert genre_family("human_drama") == "default"


# --- the cover's layout (pure) -------------------------------------------------------------------


def test_cover_text_stays_out_of_the_covered_zones_and_in_the_grid() -> None:
    layout = cover_layout(series="Night Shift", part=3, width=1080, height=1920)
    assert layout.part_text == "PART 3"
    assert zones_entered(layout.box) == []
    left, top, right, bottom = layout.box
    # Instagram's profile grid shows the middle 3:4 of a 9:16 cover.
    assert top >= 0.125 and bottom <= 0.875
    assert right <= 0.88


def test_cover_text_moves_off_a_face() -> None:
    plain = cover_layout(series="Night Shift", part=3, width=1080, height=1920)
    # A face over where the text would sit.
    left, top, right, bottom = plain.box
    face = (0.3, top, 0.4, bottom - top)
    moved = cover_layout(
        series="Night Shift", part=3, width=1080, height=1920, faces=[face]
    )
    assert moved.placement != plain.placement
    m_left, m_top, m_right, m_bottom = moved.box
    assert m_bottom <= face[1] or m_top >= face[1] + face[3]
    assert zones_entered(moved.box) == []


def test_cover_path_is_versioned_next_to_the_reel(tmp_path: Path) -> None:
    reel = tmp_path / "reel-ep02-v3.mp4"
    first = cover_path(reel)
    assert first.name == "reel-ep02-v3-cover-v1.jpg"
    first.write_bytes(b"x")
    assert cover_path(reel).name == "reel-ep02-v3-cover-v2.jpg"


# --- the results sheet ---------------------------------------------------------------------------


def test_metrics_rows_append_under_one_header(tmp_path: Path) -> None:
    sheet = tmp_path / "metrics.csv"
    row = {"reel_file": "reel-ep01-v1.mp4", "part": "1", "series": "Night Shift"}
    append_metrics_row(sheet, row)
    append_metrics_row(sheet, {**row, "reel_file": "reel-ep01-v2.mp4"})
    rows = list(csv.reader(sheet.open(encoding="utf-8")))
    assert rows[0] == list(METRICS_COLUMNS)
    assert [r[0] for r in rows[1:]] == ["reel-ep01-v1.mp4", "reel-ep01-v2.mp4"]
    for name in (
        "posted_at",
        "views",
        "hold_3s_pct",
        "avg_watch_pct",
        "follows",
        "saves",
        "shares",
        "notes",
    ):
        assert name in METRICS_COLUMNS
        assert rows[1][METRICS_COLUMNS.index(name)] == ""


# --- end to end on the tiny desk -----------------------------------------------------------------


def _streams(video: Path) -> list[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return [line for line in out.stdout.split() if line]


@needs_ffmpeg
def test_every_reel_writes_a_part_cover_a_metrics_row_and_operator_notes(
    reel_desk: Path,  # noqa: F811
) -> None:
    from creation.post.reel import run_reel

    (reel_desk / "production.config.json").write_text(
        json.dumps(
            {"account": "@tiny.show", "lane": "mystery", "posting_slot": "18:30 IST"}
        ),
        encoding="utf-8",
    )
    out = io.StringIO()
    result = run_reel(reel_desk, episode=1, seconds=6.0, stream=out, detector=None)

    assert result.video is not None and result.cover is not None
    assert result.cover.name == "reel-ep01-v1-cover-v1.jpg"
    assert result.cover.parent == result.video.parent
    picture = np.asarray(Image.open(result.cover).convert("RGB"))
    assert picture.shape[:2] == (168, 96)
    # PART N is drawn in the house yellow, outside the covered zones.
    mask = caption_mask(picture)
    assert mask.any()
    ys, xs = np.nonzero(mask)
    height, width = mask.shape
    box = (
        xs.min() / width,
        ys.min() / height,
        (xs.max() + 1) / width,
        (ys.max() + 1) / height,
    )
    assert zones_entered(box) == []
    # The cover never goes inside the video.
    assert sorted(_streams(result.video)) == ["audio", "video"]
    printed = out.getvalue()
    assert f"cover: {result.cover}" in printed
    post = result.post.read_text(encoding="utf-8") if result.post else ""
    caption, _, notes = post.partition("Not part of the caption")
    assert "Tiny Show · Part 1" in caption and "Follow for part 2." in caption
    assert (
        "Edit cover" in notes and result.cover.name in notes and "@tiny.show" in notes
    )
    assert "18:30 IST" in notes and "trending sound" in notes
    rows = list(
        csv.DictReader((reel_desk / "reels" / "metrics.csv").open(encoding="utf-8"))
    )
    assert len(rows) == 1
    row = rows[0]
    assert (row["reel_file"], row["cover_file"]) == (
        result.video.name,
        result.cover.name,
    )
    assert (row["series"], row["part"], row["account"], row["lane"]) == (
        "Tiny Show",
        "1",
        "@tiny.show",
        "mystery",
    )
    assert row["planned_post_slot"] == "18:30 IST"
    assert row["cold_open_role"] and row["cold_open_time"]
    assert row["views"] == "" and row["posted_at"] == ""


@needs_ffmpeg
def test_no_cover_skips_it_and_cover_frame_picks_the_time(reel_desk: Path) -> None:  # noqa: F811
    from creation.post.reel import run_reel

    out = io.StringIO()
    skipped = run_reel(
        reel_desk, episode=1, seconds=6.0, stream=out, detector=None, no_cover=True
    )
    assert skipped.cover is None
    assert not list((reel_desk / "reels").glob("*cover*"))
    assert "--no-cover" in out.getvalue()
    assert "posting_slot" not in out.getvalue()  # no account set: nothing to warn about

    picked = run_reel(
        reel_desk,
        episode=1,
        seconds=6.0,
        stream=io.StringIO(),
        detector=None,
        cover_frame=0.2,
    )
    assert picked.cover is not None and picked.cover.is_file()
    assert picked.cover_note and "--cover-frame" in picked.cover_note


@needs_ffmpeg
def test_an_account_without_a_slot_warns(reel_desk: Path) -> None:  # noqa: F811
    from creation.post.reel import run_reel

    (reel_desk / "production.config.json").write_text(
        json.dumps({"account": "@tiny.show"}), encoding="utf-8"
    )
    out = io.StringIO()
    result = run_reel(reel_desk, episode=1, seconds=6.0, stream=out, detector=None)
    assert result.video is not None
    assert "⚠" in out.getvalue() and "posting_slot" in out.getvalue()


def test_the_cli_takes_the_cover_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation.cli_produce import main

    seen: dict[str, object] = {}

    def fake_reel(desk: Path, **kwargs: object) -> None:
        seen.update(kwargs)

    monkeypatch.setattr("creation.post.reel.run_reel", fake_reel)
    assert (
        main(
            ["reel", "--desk", str(tmp_path), "--episode", "2", "--cover-frame", "1.5"]
        )
        == 0
    )
    assert (seen["no_cover"], seen["cover_frame"]) == (False, 1.5)
    assert main(["reel", "--desk", str(tmp_path), "--episode", "2", "--no-cover"]) == 0
    assert (seen["no_cover"], seen["cover_frame"]) == (True, None)
