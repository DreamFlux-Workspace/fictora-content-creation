"""finish's room-tone heard check reads only a transcript of the take being finished (Hanakaze ep1, option C live run).

The run checked "lines heard" on take v3 against `take-ep01-t1-v2-words-ja-v1.json`, the
transcript of the take it replaced, and printed "3 of 3 line(s) heard" next to
"!! line_episode_01_03: not heard".
"""

from __future__ import annotations

import os
from pathlib import Path

from creation.post.review import take_words
from creation.post.soundtrack import SoundLine, heard_summary


def _touch(path: Path, *, at: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"words": []}' if path.suffix == ".json" else "x")
    os.utime(path, (at, at))
    return path


def test_a_transcript_named_for_an_older_take_version_is_not_used(
    tmp_path: Path,
) -> None:
    takes = tmp_path / "ep01" / "takes"
    _touch(takes / "take-ep01-t1-raw-v2.mp4", at=100)
    old = _touch(takes / "take-ep01-t1-v2-words-ja-v1.json", at=200)
    v3 = _touch(takes / "take-ep01-t1-raw-v3.mp4", at=300)

    words, why = take_words(tmp_path, 1, "t1", v3)

    assert words is None
    assert old.name in why and "v2" in why and "raw-v3" in why
    assert take_words(tmp_path, 1, "t1", takes / "take-ep01-t1-raw-v2.mp4")[0] == old


def test_a_transcript_without_a_version_counts_only_when_made_after_the_take(
    tmp_path: Path,
) -> None:
    takes = tmp_path / "ep01" / "takes"
    _touch(takes / "take-ep01-t1-review-words-v1.json", at=100)
    v3 = _touch(takes / "take-ep01-t1-raw-v3.mp4", at=200)

    assert take_words(tmp_path, 1, "t1", v3)[0] is None
    fresh = _touch(takes / "take-ep01-t1-review-words-v2.json", at=300)
    assert take_words(tmp_path, 1, "t1", v3)[0] == fresh


def test_a_transcript_named_for_this_version_is_used(tmp_path: Path) -> None:
    takes = tmp_path / "ep01" / "takes"
    v3 = _touch(takes / "take-ep01-t1-raw-v3.mp4", at=300)
    mine = _touch(takes / "take-ep01-t1-raw-v3-words-ja-v1.json", at=100)

    assert take_words(tmp_path, 1, "t1", v3)[0] == mine


LINES = (
    SoundLine("l1", "cast_a", 1.0, 2.0),
    SoundLine("l2", "cast_b", 3.0, 4.0),
    SoundLine("l3", "cast_a", 5.0, 6.0),
)


def test_the_summary_counts_a_line_the_transcript_missed_as_not_heard() -> None:
    text = heard_summary(
        LINES,
        silent=[],
        problems=["l3: not heard in `take-ep01-t1-raw-v3-words-v1.json`"],
        words=Path("take-ep01-t1-raw-v3-words-v1.json"),
        skipped="",
    )

    assert text.startswith("2 of 3 line(s) heard in their window")
    assert "3 of 3" not in text


def test_the_summary_says_when_the_transcript_check_was_skipped() -> None:
    text = heard_summary(
        LINES,
        silent=[],
        problems=[],
        words=None,
        skipped="no transcript of take-ep01-t1-raw-v3.mp4",
    )

    assert text.startswith("3 of 3 line(s) have voice in their window (levels only)")
    assert "transcript check skipped: no transcript of take-ep01-t1-raw-v3.mp4" in text
