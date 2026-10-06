"""TikTok clips of a new desk's episode: 2-3 clips by the server's reel engine (``mode: clips``), $0.

The server is :class:`reel_fake_server.FakeReelServer` (``conftest.reel_server``).
Desks made before 6 Oct 2026 get no clips at all.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_reel import reel_desk as legacy_reel_desk  # noqa: F401  (fixture)
from test_reel_via_server import _make_new

from creation.post.clips_via_server import (
    TIKTOK_NOTE,
    ClipsAsk,
    auto_clips,
    episode_clips,
    run_clips,
)
from creation.post.reel import auto_reel
from creation.post.reel_cover import METRICS_COLUMNS, record_metrics_row
from creation.post.reel_plan import OPERATOR_DIVIDER
from reel_fake_server import FakeReelServer


@pytest.fixture
def reel_desk(legacy_reel_desk: Path) -> Path:  # noqa: F811
    """test_reel's tiny desk, made on the rules epoch: its reel and clips go to the server."""

    return _make_new(legacy_reel_desk)


def _metrics(desk: Path) -> list[dict[str, str]]:
    with (desk / "reels" / "metrics.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


# --- the request ---------------------------------------------------------------------------------


def test_clips_ask_the_reel_route_in_clip_mode_with_the_desks_takes(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    run_clips(reel_desk, episode=1, count=3, seconds=12.0, stream=io.StringIO())

    body = reel_server.requests[-1]
    assert (body["mode"], body["clip_count"], body["clip_seconds"]) == (
        "clips",
        3,
        12.0,
    )
    assert "seconds" not in body and "plan" not in body
    assert body["operator"]["takes"], "the same operator takes as the reel"
    assert "cover_still_url" not in body["operator"], "each clip draws its own cover"


def test_the_servers_defaults_are_used_when_nothing_is_asked(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    run_clips(reel_desk, episode=1, stream=io.StringIO())

    body = reel_server.requests[-1]
    assert (
        body["mode"] == "clips"
        and "clip_count" not in body
        and "clip_seconds" not in body
    )


@pytest.mark.parametrize(
    ("count", "seconds"), [(0, None), (4, None), (None, 9.0), (None, 25.0)]
)
def test_out_of_range_clips_are_refused_before_anything_is_sent(
    count: int | None, seconds: float | None
) -> None:
    with pytest.raises(ValueError):
        ClipsAsk(count, seconds)


# --- the files -----------------------------------------------------------------------------------


def test_each_clip_lands_with_its_cover_and_post_text(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    result = run_clips(reel_desk, episode=1, count=3, stream=io.StringIO())

    folder = episode_clips(reel_desk, 1)
    assert folder == reel_desk / "reels" / "ep01" / "clips"
    names = sorted(p.name for p in folder.iterdir())
    for k in (1, 2, 3):
        assert f"clip-ep01-{k}-v1.mp4" in names
        assert f"clip-ep01-{k}-v1-cover.jpg" in names
        assert f"post-clip-ep01-{k}-v1.txt" in names
    post = (folder / "post-clip-ep01-2-v1.txt").read_text(encoding="utf-8")
    caption, notes = post.split(OPERATOR_DIVIDER)
    assert "clip 2" in caption and "Follow for part 2." in caption
    assert TIKTOK_NOTE in notes and "clip-ep01-2-v1-cover.jpg" in notes
    assert "Instagram demotes" in notes
    latest = json.loads((folder / "latest.json").read_text(encoding="utf-8"))
    assert [c["index"] for c in latest["clips"]] == [1, 2, 3]
    assert latest["sources"] == result.sources and latest["made_by"] == "reel"


def test_a_second_set_is_a_new_version_and_never_overwrites(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    run_clips(reel_desk, episode=1, stream=io.StringIO())
    second = run_clips(reel_desk, episode=1, stream=io.StringIO())

    assert second.version == 2
    assert (episode_clips(reel_desk, 1) / "clip-ep01-1-v1.mp4").is_file()
    assert (episode_clips(reel_desk, 1) / "clip-ep01-1-v2.mp4").is_file()


def test_metrics_gets_one_row_per_clip_beside_the_reels_row(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    auto_reel(reel_desk, 1, trigger="finish", stream=io.StringIO())
    run_clips(reel_desk, episode=1, count=2, stream=io.StringIO())
    run_clips(reel_desk, episode=1, count=2, stream=io.StringIO())

    rows = _metrics(reel_desk)
    assert [(r["kind"], r["clip"]) for r in rows] == [
        ("reel", ""),
        ("clip", "1"),
        ("clip", "2"),
    ]
    # A re-cut before posting updates each clip's own row; the old file is superseded.
    assert (
        rows[1]["reel_file"] == "clip-ep01-1-v2.mp4"
        and rows[1]["superseded"] == "clip-ep01-1-v1.mp4"
    )
    assert rows[0]["reel_file"].startswith("reel-ep01")


def test_a_sheet_with_no_clip_keeps_its_columns(tmp_path: Path) -> None:
    sheet = tmp_path / "metrics.csv"

    record_metrics_row(sheet, {"reel_file": "reel-ep01-v1.mp4", "part": 1})

    assert sheet.read_text(encoding="utf-8").splitlines()[0].split(",") == list(
        METRICS_COLUMNS
    )


# --- automatic after finish ----------------------------------------------------------------------


def test_clips_follow_a_complete_finish_and_are_not_cut_again_when_nothing_changed(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    out = io.StringIO()
    first = auto_clips(reel_desk, 1, trigger="finish", stream=out)
    again = io.StringIO()
    second = auto_clips(reel_desk, 1, trigger="finish", stream=again)

    assert first is not None and len(first.clips) == 2
    assert "--no-clips skips them" in out.getvalue()
    assert second is None and "unchanged" in again.getvalue()
    assert (
        json.loads((episode_clips(reel_desk, 1) / "latest.json").read_text())["made_by"]
        == "finish"
    )


def test_a_legacy_desk_gets_no_clips_at_all(
    legacy_reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
) -> None:
    series = legacy_reel_desk / "series.json"
    body = json.loads(series.read_text(encoding="utf-8")) if series.is_file() else {}
    series.write_text(json.dumps({**body, "day": "2026-09-28"}), encoding="utf-8")
    out = io.StringIO()

    assert auto_clips(legacy_reel_desk, 1, trigger="finish", stream=out) is None
    with pytest.raises(ValueError, match="older"):
        run_clips(legacy_reel_desk, episode=1, stream=io.StringIO())

    assert out.getvalue() == "", "a legacy finish prints nothing new"
    assert reel_server.requests == []
    assert not episode_clips(legacy_reel_desk, 1).exists()


def test_an_unreachable_server_never_fails_the_finish(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    reel_server.unreachable = True
    out = io.StringIO()

    assert auto_clips(reel_desk, 1, trigger="finish", stream=out) is None

    said = out.getvalue()
    assert "Clips not made: the server didn't answer" in said and "--clips 3" in said
    assert "the finish is done" in said


def test_a_server_without_clip_mode_is_a_clear_failure(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    reel_server.no_clip_mode = True
    out = io.StringIO()

    assert auto_clips(reel_desk, 1, trigger="finish", stream=out) is None
    assert "no clips" in out.getvalue() and "the finish is done" in out.getvalue()


# --- the CLI -------------------------------------------------------------------------------------


def test_finish_makes_clips_unless_no_clips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from creation import cli_post
    from creation.cli_produce import main

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli_post,
        "run_finish",
        lambda *_a, **_k: SimpleNamespace(complete=True, final="f", episode=1),
    )
    monkeypatch.setattr("creation.post.reel.auto_reel", lambda *a, **k: None)
    monkeypatch.setattr(
        "creation.post.clips_via_server.auto_clips",
        lambda desk, episode, **k: calls.append(k),
    )

    assert main(["finish", "--desk", str(tmp_path)]) == 0
    assert [c["trigger"] for c in calls] == ["finish"]
    assert main(["finish", "--desk", str(tmp_path), "--no-clips"]) == 0
    assert main(["finish", "--desk", str(tmp_path), "--no-reel"]) == 0
    assert len(calls) == 2, "--no-reel does not skip the clips; --no-clips does"


def test_reel_clips_n_recuts_the_clips_by_hand(
    reel_desk: Path, reel_server: FakeReelServer
) -> None:
    from creation.cli_produce import main

    assert (
        main(["reel", "--desk", str(reel_desk), "--episode", "1", "--clips", "3"]) == 0
    )

    assert reel_server.requests[-1]["mode"] == "clips"
    assert (episode_clips(reel_desk, 1) / "clip-ep01-3-v1.mp4").is_file()
