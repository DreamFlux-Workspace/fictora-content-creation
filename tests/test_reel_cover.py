"""Reel covers, posting lanes and the results sheet: what the kit keeps of the reel's books.

The cover image and the post caption are made by the server's reel engine
(fictora-drama); the kit downloads the cover beside the reel, writes the
operator's notes under the caption and keeps ``reels/metrics.csv``.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from conftest import needs_ffmpeg
from test_reel import reel_desk  # noqa: F401  (fixture)

from creation.post.reel_cover import (
    METRICS_COLUMNS,
    cover_path,
    record_metrics_row,
)
from creation.post.reel_plan import post_operator_notes, posting_warnings
from reel_fake_server import FakeReelServer


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


def test_cover_path_is_versioned_next_to_the_reel(tmp_path: Path) -> None:
    reel = tmp_path / "reel-ep02-v3.mp4"
    first = cover_path(reel)
    assert first.name == "reel-ep02-v3-cover-v1.jpg"
    first.write_bytes(b"x")
    assert cover_path(reel).name == "reel-ep02-v3-cover-v2.jpg"


# --- the results sheet ---------------------------------------------------------------------------


def test_metrics_keep_one_row_per_part_and_list_what_it_superseded(
    tmp_path: Path,
) -> None:
    sheet = tmp_path / "metrics.csv"
    row = {"reel_file": "reel-ep01-v1.mp4", "part": "1", "series": "Night Shift"}
    record_metrics_row(sheet, row)
    record_metrics_row(sheet, {**row, "reel_file": "reel-ep01-v2.mp4"})
    record_metrics_row(sheet, {**row, "reel_file": "reel-ep02-v1.mp4", "part": "2"})
    rows = list(csv.reader(sheet.open(encoding="utf-8")))
    assert rows[0] == list(METRICS_COLUMNS)
    assert [r[0] for r in rows[1:]] == ["reel-ep01-v2.mp4", "reel-ep02-v1.mp4"]
    assert rows[1][METRICS_COLUMNS.index("superseded")] == "reel-ep01-v1.mp4"
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
    result = run_reel(reel_desk, episode=1, seconds=6.0, stream=out)

    assert result.video is not None and result.cover is not None
    assert result.cover.name == "reel-ep01-v1-cover-v1.jpg"
    assert result.cover.parent == result.video.parent
    # The cover is the server's, downloaded beside the reel (never inside the video).
    assert result.cover.read_bytes().startswith(b"\xff\xd8")
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
def test_no_cover_skips_it_and_cover_frame_picks_the_time(
    reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
) -> None:
    from creation.post.reel import run_reel

    out = io.StringIO()
    skipped = run_reel(reel_desk, episode=1, seconds=6.0, stream=out, no_cover=True)
    assert skipped.cover is None
    assert not list((reel_desk / "reels").glob("*cover*"))
    assert "--no-cover" in out.getvalue()
    assert "posting_slot" not in out.getvalue()  # no account set: nothing to warn about

    picked = run_reel(
        reel_desk,
        episode=1,
        seconds=6.0,
        stream=io.StringIO(),
        cover_frame=0.2,
    )
    assert picked.cover is not None and picked.cover.is_file()
    assert reel_server.requests[0]["no_cover"] is True
    assert reel_server.requests[1]["cover_frame_s"] == 0.2


@needs_ffmpeg
def test_an_account_without_a_slot_warns(reel_desk: Path) -> None:  # noqa: F811
    from creation.post.reel import run_reel

    (reel_desk / "production.config.json").write_text(
        json.dumps({"account": "@tiny.show"}), encoding="utf-8"
    )
    out = io.StringIO()
    result = run_reel(reel_desk, episode=1, seconds=6.0, stream=out)
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
