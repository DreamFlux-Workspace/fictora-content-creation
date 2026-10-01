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


# --- a stretched first word on a locked-voice take (Hanakaze ep 7) ------------------------------------


def _words(tmp_path: Path, words: list[tuple[str, float, float]]) -> Path:
    import json

    path = tmp_path / "take-ep07-t1-raw-v1-words-v1.json"
    path.write_text(
        json.dumps({"words": [{"word": w, "start": a, "end": b} for w, a, b in words]})
    )
    return path


def _levels(*voiced: tuple[float, float], seconds: float = 10.0):
    """A dialogue track: voice (-20 dB) in ``voiced`` spans, digital silence elsewhere, 0.1 s windows."""

    def meter(_take: Path, *, window_seconds: float) -> tuple[float, ...]:
        count = int(seconds / window_seconds)
        return tuple(
            -20.0 if any(a <= i * window_seconds < b for a, b in voiced) else -120.0
            for i in range(count)
        )

    return meter


STRETCHED = [
    ("Wait", 4.35, 6.3),
    ("for", 6.4, 6.6),
    ("me", 6.6, 6.8),
    ("here", 6.8, 7.2),
]


def test_a_stretched_first_word_is_heard_where_the_tracks_own_voice_starts(
    tmp_path: Path,
) -> None:
    from creation.post.soundtrack import Soundtrack, misplaced_lines

    track = Soundtrack(
        mode="target_audio",
        lines=(SoundLine("line_episode_07_02", "cast_a", 6.0, 7.5),),
    )
    words = _words(tmp_path, STRETCHED)
    texts = {"line_episode_07_02": "Wait for me here."}

    assert misplaced_lines(track, words, texts) == [
        "line_episode_07_02: heard at 4.35s, its window is 6.00-7.50s"
    ], "Whisper's word start alone is the false alarm"
    assert misplaced_lines(track, words, texts, take=tmp_path / "t.mp4",
                           measure=_levels((6.1, 7.3))) == []  # fmt: skip


def test_a_line_heard_wholly_elsewhere_or_over_a_silent_window_is_still_named(
    tmp_path: Path,
) -> None:
    from creation.post.soundtrack import Soundtrack, misplaced_lines

    track = Soundtrack(
        mode="target_audio", lines=(SoundLine("l2", "cast_a", 6.0, 7.5),)
    )
    texts = {"l2": "Wait for me here."}
    early = _words(
        tmp_path,
        [("Wait", 1.0, 1.2), ("for", 1.2, 1.4), ("me", 1.4, 1.6), ("here", 1.6, 1.9)],
    )

    assert misplaced_lines(track, early, texts, take=tmp_path / "t.mp4", measure=_levels((1.0, 1.9), (6.1, 7.3))) == [
        "l2: heard at 1.00s, its window is 6.00-7.50s"
    ], "the words never reach the window"  # fmt: skip
    assert misplaced_lines(track, _words(tmp_path, STRETCHED), texts, take=tmp_path / "t.mp4",
                           measure=_levels((4.3, 5.0))) == [
        "l2: heard at 4.35s, its window is 6.00-7.50s"
    ], "no voice in the window: the stretched start stands"  # fmt: skip
