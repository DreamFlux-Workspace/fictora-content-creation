"""Readable episode file names (founder, 14 Oct 2026): ``<show>_epNN_<draft|final>_vN_<date>.mp4``."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from creation.cli_ops import main as ops_main
from creation.ops.floor import init_series_desk
from creation.ops.state import load_series
from creation.post import episode_name
from creation.post.episode_name import (
    episode_files,
    episode_number,
    mark_final,
    next_episode_path,
    series_slug,
    slugify,
)
from creation.post.review import take_kind

DAY = date(2026, 10, 14)


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    return init_series_desk(tmp_path, "Back From The Sea!", band="30s", episode_count=2)


def _touch(path: Path, body: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def test_the_slug_is_the_title_then_the_folder_then_series(
    tmp_path: Path, desk: Path
) -> None:
    assert slugify("  Back From The Sea! ") == "back-from-the-sea"
    assert slugify("A" * 30 + " " + "b" * 30) == "a" * 30 + "-" + "b" * 9
    assert len(slugify("word " * 20)) <= 40 and not slugify("word " * 20).endswith("-")
    assert series_slug(desk) == "back-from-the-sea"

    no_title = _touch(
        tmp_path / "My Desk" / "series.json", json.dumps({"title": "  "}).encode()
    )
    assert series_slug(no_title.parent) == "my-desk"
    assert series_slug(tmp_path / "!!!") == "series"


def test_a_new_desk_starts_at_v1_and_never_reuses_a_name(desk: Path) -> None:
    takes = desk / "ep01" / "takes"
    first = next_episode_path(takes, desk, 1, day=DAY)
    assert first.name == "back-from-the-sea_ep01_draft_v1_2026-10-14.mp4"
    _touch(first)
    assert (
        next_episode_path(takes, desk, 1, day=DAY).name
        == "back-from-the-sea_ep01_draft_v2_2026-10-14.mp4"
    )
    assert next_episode_path(takes, desk, 2, day=DAY).name.startswith(
        "back-from-the-sea_ep02_draft_v1_"
    )
    with pytest.raises(ValueError, match="stage"):
        next_episode_path(takes, desk, 1, "publish", day=DAY)


def test_an_old_desk_keeps_its_files_and_the_count_carries_on(desk: Path) -> None:
    takes = desk / "ep01" / "takes"
    old = [
        _touch(takes / "episode-ep01-join-sokii-v1.mp4", b"one"),
        _touch(takes / "episode-ep01-join-sokii-v2.mp4", b"two"),
    ]
    # An un-marked master is a working file, not counted.
    _touch(takes / "episode-ep01-join-v7.mp4")

    assert episode_files(takes, 1) == old, "old names are still found"
    nxt = next_episode_path(takes, desk, 1, day=DAY)
    assert nxt.name == "back-from-the-sea_ep01_draft_v3_2026-10-14.mp4"
    assert [p.read_bytes() for p in old] == [b"one", b"two"], (
        "nothing renamed or touched"
    )
    assert episode_number("episode-ep01-join-sokii-v2.mp4") == 1
    assert episode_number(nxt.name) == 1
    assert episode_number("take-ep01-t1-sokii-v1.mp4") is None


def test_mark_final_copies_to_a_new_final_name_and_leaves_the_draft(
    desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(episode_name, "today", lambda: DAY)
    takes = desk / "ep01" / "takes"
    assert mark_final(desk, 1) is None, "no episode file yet"
    draft = _touch(next_episode_path(takes, desk, 1), b"draft")

    final = mark_final(desk, 1)

    assert (
        final is not None
        and final.name == "back-from-the-sea_ep01_final_v2_2026-10-14.mp4"
    )
    assert final.read_bytes() == b"draft" and draft.read_bytes() == b"draft"
    assert mark_final(desk, 1, final) == final, "a final file is not copied again"
    again = mark_final(desk, 1, draft)
    assert again is not None and again.name.endswith("_final_v3_2026-10-14.mp4"), (
        "never overwrites"
    )


def test_mark_final_copies_an_old_desk_file_too(
    desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(episode_name, "today", lambda: DAY)
    old = _touch(desk / "ep01" / "takes" / "episode-ep01-join-sokii-v4.mp4", b"old")

    final = mark_final(desk, 1)

    assert (
        final is not None
        and final.name == "back-from-the-sea_ep01_final_v5_2026-10-14.mp4"
    )
    assert old.read_bytes() == b"old"


def test_approve_post_marks_the_episode_final(
    desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(episode_name, "today", lambda: DAY)
    takes = desk / "ep01" / "takes"
    draft = _touch(next_episode_path(takes, desk, 1), b"draft")

    code = ops_main(
        [
            "approve",
            "--desk",
            str(desk),
            "--gate",
            "post",
            "--episode",
            "1",
            "--path",
            str(draft),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    final = takes / "back-from-the-sea_ep01_final_v2_2026-10-14.mp4"
    assert final.is_file() and f"Final file: {final}" in out
    assert load_series(desk).episodes[0].post.status == "approved"
    notes = desk / "ep01" / "run-notes.md"
    if notes.is_file():
        assert final.name in notes.read_text(encoding="utf-8")


def test_approve_post_with_nothing_joined_still_records_the_yes(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        ops_main(["approve", "--desk", str(desk), "--gate", "post", "--episode", "2"])
        == 0
    )
    assert "No final copy made" in capsys.readouterr().out
    assert load_series(desk).episodes[1].post.status == "approved"


def test_review_reads_a_readable_name_as_finished_whatever_the_title_says() -> None:
    assert (
        take_kind(Path("back-from-the-sea_ep01_draft_v3_2026-10-14.mp4")) == "finished"
    )
    assert take_kind(Path("reel-life_ep01_final_v1_2026-10-14.mp4")) == "finished"
    assert take_kind(Path("episode-ep01-join-sokii-v1.mp4")) == "finished"
    assert take_kind(Path("take-ep01-t1-v1.mp4")) == "raw"
