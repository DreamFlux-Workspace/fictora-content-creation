"""Captions per take: a take of a 30 s / 60 s episode is captioned with its own beats' lines only."""

from __future__ import annotations

import io
import json
import re
from datetime import date
from pathlib import Path

import pytest

from conftest import SPINE, make_take, make_tone, needs_ffmpeg

from creation.captions import (
    caption_take,
    episode_caption_lines,
    take_caption_lines,
    take_index_from_name,
)
from creation.ops.floor import init_series_desk
from creation.production_state import ProductionState, save_production
from creation.post.finish import run_finish


def _line(line_id: str, text: str, cast: str = "cast_kenji") -> dict[str, str]:
    return {"line_id": line_id, "cast_id": cast, "text": text}


def two_take_spine(*, pattern: list[int] | None = None) -> dict:
    """Episode 1 in two takes of two beats: t1 says A1, A2; t2 says B1, B2."""

    spine = json.loads(json.dumps(SPINE))
    spine["beats"] = [
        {"episode_id": "episode_01", "ordinal": 1, "dialogue_lines": [_line("a1", "Wait for me here.")]},
        {"episode_id": "episode_01", "ordinal": 2, "dialogue_lines": [_line("a2", "Not tonight.", "cast_aya")]},
        {"episode_id": "episode_01", "ordinal": 3, "dialogue_lines": [_line("b1", "Then tomorrow.")]},
        {"episode_id": "episode_01", "ordinal": 4, "dialogue_lines": [_line("b2", "Maybe never.", "cast_aya")]},
    ]  # fmt: skip
    if pattern is not None:
        spine["beats_per_storyboard_set"] = pattern
    return spine


def _texts(lines) -> list[str]:  # noqa: ANN001
    return [line.text for line in lines]


def test_each_take_gets_only_the_lines_of_its_own_beats() -> None:
    spine = {"spine": two_take_spine(pattern=[2, 2])}
    first, warn1 = take_caption_lines(spine, 1, take_index=1, take_count=2)
    second, warn2 = take_caption_lines(spine, 1, take_index=2, take_count=2)
    assert _texts(first) == ["Wait for me here.", "Not tonight."]
    assert _texts(second) == ["Then tomorrow.", "Maybe never."]
    assert warn1 == warn2 == ""
    # Without a desk count the spine's own pattern is the split.
    assert _texts(take_caption_lines(spine, 1, take_index=2, take_count=None)[0]) == [
        "Then tomorrow.",
        "Maybe never.",
    ]


def test_a_one_take_episode_still_gets_every_line() -> None:
    spine = two_take_spine(pattern=[4])
    lines, warning = take_caption_lines(spine, 1, take_index=1, take_count=1)
    assert _texts(lines) == _texts(episode_caption_lines(spine, 1))
    assert len(lines) == 4 and warning == ""
    # An old one-take desk with no pattern is not a gap either.
    bare = two_take_spine()
    assert take_caption_lines(bare, 1, take_index=1, take_count=1) == (
        episode_caption_lines(bare, 1),
        "",
    )


@pytest.mark.parametrize(
    ("pattern", "count", "why"),
    [
        (None, 2, "the spine has no beats_per_storyboard_set"),
        ([4], 2, "does not match the 2 take(s) on the desk"),
        (None, None, "the desk names no takes"),
    ],
)
def test_no_split_falls_back_to_every_line_with_a_warning(
    pattern: list[int] | None, count: int | None, why: str
) -> None:
    spine = two_take_spine(pattern=pattern)
    lines, warning = take_caption_lines(spine, 1, take_index=2, take_count=count)
    assert len(lines) == 4, "fallback keeps the old behaviour: every line"
    assert warning.startswith("TAKE LINES: ep01 t2 is captioned with every line")
    assert why in warning


def test_the_whole_episode_still_gets_every_line() -> None:
    assert len(episode_caption_lines(two_take_spine(pattern=[2, 2]), 1)) == 4


@pytest.mark.parametrize(
    ("name", "index"),
    [
        ("take-ep01-t2-raw-v1.mp4", 2),
        ("take-ep03-t4-mix-v2.mp4", 4),
        ("take-ep01-t1.mp4", 1),
        ("ep01-joined-v1.mp4", None),
        ("take-ep01-joined-v1.mp4", None),
    ],
)
def test_take_index_from_name(name: str, index: int | None) -> None:
    assert take_index_from_name(Path(name)) == index


# --- end to end: finish and caption on a two-take desk ----------------------------------------


def _dialogue(ass: Path) -> list[str]:
    return [
        row.rsplit(",,", 1)[-1]
        for row in ass.read_text(encoding="utf-8").splitlines()
        if row.startswith("Dialogue:")
    ]


def _words(rows: list[str]) -> set[str]:
    return {w.lower() for row in rows for w in re.findall(r"[A-Za-z']+", row)}


@pytest.fixture
def two_take_desk(tmp_path: Path) -> Path:
    """A 30 s desk (t1, t2) with the two-take spine and both raw takes."""

    desk = init_series_desk(
        tmp_path, "Two Takes", band="30s", episode_count=1, day=date(2026, 10, 1)
    )
    save_production(
        desk,
        ProductionState(
            session_id="content-ops-test",
            prompt="p",
            preset_id="x",
            preset_version="1",
            spine_id="spine_test",
        ),
    )
    api = desk / "ep01" / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "03_spine.json").write_text(
        json.dumps(two_take_spine(pattern=[2, 2])), encoding="utf-8"
    )
    return desk


def _make_takes(desk: Path) -> None:
    takes = desk / "ep01" / "takes"
    for take_id in ("t1", "t2"):
        make_take(takes / f"take-ep01-{take_id}-raw-v1.mp4",
                  tones=((1.0, 2.0, 440), (3.2, 4.0, 880)))  # fmt: skip


@needs_ffmpeg
def test_finish_captions_take_two_with_take_two_lines_only(two_take_desk: Path) -> None:
    _make_takes(two_take_desk)
    result = run_finish(
        two_take_desk,
        take_id="t2",
        sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
        bed_maker=lambda spine, music, target: make_tone(
            target.with_suffix(".wav"), seconds=6.0, freq=220
        ),
        facts_fetcher=lambda *a: None,
        stream=io.StringIO(),
    )
    captions = next(s for s in result.steps if s.step == "captions")
    assert captions.status == "ran"
    assert "TAKE LINES" not in captions.detail
    ass = next((two_take_desk / "ep01" / "takes").glob("take-ep01-t2-cap-v*.ass"))
    words = _words(_dialogue(ass))
    assert {"then", "tomorrow", "maybe", "never"} <= words
    assert not words & {"wait", "here", "tonight"}, "t2 must not carry t1's lines"


@needs_ffmpeg
def test_caption_take_one_and_two_and_a_joined_file(two_take_desk: Path) -> None:
    _make_takes(two_take_desk)
    takes = two_take_desk / "ep01" / "takes"
    first = caption_take(two_take_desk)  # default: newest t1 raw, take 1
    second = caption_take(
        two_take_desk, take=takes / "take-ep01-t2-raw-v1.mp4", take_index=2
    )
    assert first.lines == ("Wait for me here.", "Not tonight.")
    assert second.lines == ("Then tomorrow.", "Maybe never.")
    assert first.take_lines_warning == second.take_lines_warning == ""
    # A whole-episode file (no take index) keeps every line of the episode.
    joined = make_take(takes / "ep01-joined-v1.mp4", seconds=8.0,
                       tones=((0.5, 1.5, 440), (2.5, 3.5, 880), (4.5, 5.5, 440), (6.5, 7.5, 880)))  # fmt: skip
    whole = caption_take(two_take_desk, take=joined)
    assert len(whole.lines) == 4 and whole.take_lines_warning == ""


@needs_ffmpeg
def test_caption_take_warns_when_the_split_is_missing(
    two_take_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (two_take_desk / "ep01" / "api" / "03_spine.json").write_text(
        json.dumps(two_take_spine()), encoding="utf-8"
    )
    takes = two_take_desk / "ep01" / "takes"
    make_take(takes / "take-ep01-t2-raw-v1.mp4", seconds=8.0,
              tones=((0.5, 1.5, 440), (2.5, 3.5, 880), (4.5, 5.5, 440), (6.5, 7.5, 880)))  # fmt: skip
    result = caption_take(
        two_take_desk, take=takes / "take-ep01-t2-raw-v1.mp4", take_index=2
    )
    assert len(result.lines) == 4
    assert "the spine has no beats_per_storyboard_set" in result.take_lines_warning
    assert "WARNING TAKE LINES: ep01 t2" in capsys.readouterr().err
