"""System panels in the kit: gating, placement on a take, the ASS layer, the reel, the flag.

Mirrors fictora-drama ``tests/drama_generation/test_system_panels.py``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from creation.post.reel_plan import Segment
from creation.post.system_panels import (
    DEFAULT_LOOK,
    PanelStyle,
    PlacedPanel,
    burn_panels,
    panel_area,
    place_panel_box,
    place_panels,
    reel_panels,
    show_takes_panels,
    system_panels_ass,
    take_panels,
)

_GLASS = {"frame": "glass", "palette": ["#0B1220", "#5FD4FF", "#F4F8FF"]}


def _cue(**overrides: Any) -> dict[str, Any]:
    cue: dict[str, Any] = {"form": "level_up", "lines": ["LEVEL UP", "Level: 1 > 2"], "appear_at_ms": 500,
                           "duration_ms": 2000, "position": "upper"}  # fmt: skip
    cue.update(overrides)
    return cue


def _spine(genre: str = "system_leveling", **extra: Any) -> dict[str, Any]:
    spine: dict[str, Any] = {
        "microdrama_genre": genre,
        "beats_per_storyboard_set": [2, 2],
        "beats": [
            {"beat_id": "b1", "episode_id": "ep01", "ordinal": 1},
            {"beat_id": "b2", "episode_id": "ep01", "ordinal": 2, "system_panels": [_cue()]},
            {"beat_id": "b3", "episode_id": "ep01", "ordinal": 3},
            {"beat_id": "b4", "episode_id": "ep01", "ordinal": 4,
             "system_panels": [_cue(form="quest_log", lines=["QUEST", "Survive the gate"])]},
        ],  # fmt: skip
    }
    spine.update(extra)
    return spine


def _panel(**overrides: Any) -> PlacedPanel:
    values: dict[str, Any] = {"panel_id": "p1", "take": 1, "start_ms": 0, "end_ms": 2000, "form": "status_window",
                              "lines": ("STATUS", "Strength: 12 > 15", "Rank: F"),
                              "style": PanelStyle("glass", ("#0B1220", "#5FD4FF")), "position": "upper"}  # fmt: skip
    values.update(overrides)
    return PlacedPanel(**values)


def test_only_system_and_game_genres_have_panels() -> None:
    for genre in ("system_leveling", "last_human", "isekai", "cultivation", "regression_revenge"):
        assert show_takes_panels({"microdrama_genre": genre})
    assert not show_takes_panels({"microdrama_genre": "urban_romance"})
    assert show_takes_panels({"microdrama_genre": "action_fight", "microdrama_genre_overlays": ["isekai"]})
    assert place_panels(_spine("horror"), "ep01", [10_000, 10_000]) == []


def test_panels_land_on_their_beat_and_on_the_takes_own_timeline() -> None:
    placed = place_panels(_spine(), "ep01", [10_000, 12_000])
    assert [(p.take, p.start_ms, p.form) for p in placed] == [(1, 5_500, "level_up"), (2, 16_500, "quest_log")]
    assert placed[0].style == DEFAULT_LOOK
    on_t2 = take_panels(_spine(), "ep01", 2, [10.0, 12.0])
    assert [(start, end, p.form) for start, end, p in on_t2] == [(6_500, 8_500, "quest_log")]
    # An earlier take with no raw file counts as 15 s; this take's own timing does not move.
    assert take_panels(_spine(), "ep01", 2, [None, 12.0])[0][0] == 6_500


def test_the_look_is_the_cues_then_the_shows() -> None:
    spine = _spine(system_panel_look=_GLASS)
    spine["beats"][3]["system_panels"][0]["style"] = {"frame": "ink_scroll", "palette": ["#EFE3C8", "#7A1E14"]}
    placed = place_panels(spine, "ep01", [10_000, 10_000])
    assert placed[0].style.frame == "glass" and placed[1].style.frame == "ink_scroll"


def test_spam_is_dropped_and_long_dashes_never_drawn() -> None:
    beats = [
        {"beat_id": f"b{n}", "episode_id": "ep01", "ordinal": n,
         "system_panels": [_cue(form=form, lines=["X \u2014 1"], appear_at_ms=0, duration_ms=1200)]}
        for n, form in enumerate(["level_up", "level_up", "alert", "quest_log"], start=1)
    ]  # fmt: skip
    placed = place_panels({**_spine(), "beats": beats, "beats_per_storyboard_set": [4]}, "ep01", [15_000])
    assert [p.form for p in placed] == ["level_up", "alert"]
    assert placed[0].lines == ("X: 1",)


@pytest.mark.parametrize("position", ["upper", "center", "lower", "upper_left", "upper_right"])
def test_panels_stay_out_of_the_covered_zones_and_the_caption_band(position: str) -> None:
    box = place_panel_box(_panel(position=position), width=1080, height=1920, area=panel_area(1080, 1920))
    assert box.y >= 0.08 * 1920 and box.y + box.height <= 0.55 * 1920
    assert box.x >= 0 and box.x + box.width <= 0.88 * 1080


def test_a_letterbox_panel_stays_on_the_picture() -> None:
    box = place_panel_box(_panel(position="lower"), width=1080, height=1920,
                          area=panel_area(1080, 1920, picture=(0, 555, 1080, 810), captions_on_picture=False))  # fmt: skip
    assert 555 <= box.y and box.y + box.height <= 1365


def test_every_frame_draws_its_own_shape_and_types_in() -> None:
    drawings = set()
    for frame in ("glass", "hologram", "neon", "ink_scroll", "parchment", "brush"):
        document = system_panels_ass([(0, 2000, _panel(style=PanelStyle(frame, ("#101820", "#FFB000"))))],
                                     width=1080, height=1920)  # fmt: skip
        assert document is not None and "\\ko" in document and "\\2a&HFF&" in document
        drawings.add(tuple(re.findall(r"\\p1\}([^{]+)\{\\p0\}", document)))
    assert len(drawings) == 6


def test_the_reel_moves_panels_through_the_cut() -> None:
    takes = [("t1", 10.0), ("t2", 12.0)]
    segments = [Segment("t1", 5.0, 8.0, "cold_open"), Segment("t2", 0.0, 3.0, "turn")]
    assert [(s, e, p.form) for s, e, p in reel_panels(_spine(), "ep01", takes, segments)] == [(500, 2500, "level_up")]
    assert reel_panels(_spine(), "ep01", takes, segments, skip_takes=["t1"]) == []
    assert reel_panels(_spine(), "ep01", takes, [Segment("t1", 7.0, 9.0, "cold_open")]) == []


def test_finish_and_reel_take_no_panels() -> None:
    import argparse

    from creation.cli_post import add_post_parsers

    parser = argparse.ArgumentParser()
    add_post_parsers(parser.add_subparsers(dest="command"))
    assert parser.parse_args(["finish", "--desk", "d", "--episode", "1", "--take", "t1", "--no-panels"]).no_panels
    assert parser.parse_args(["reel", "--desk", "d", "--episode", "1", "--no-panels"]).no_panels


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_burn_panels_draws_on_a_take(tmp_path: Path) -> None:
    take = tmp_path / "take.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=360x640:d=2:r=24",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(take)], check=True)  # fmt: skip
    out = burn_panels([(0, 2000, _panel())], take, tmp_path / "p.ass", tmp_path / "out.mp4")
    assert out.is_file() and out.stat().st_size > 0
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.5", "-i", str(out), "-frames:v", "1", str(frame)],
                   check=True)  # fmt: skip
    from PIL import Image

    pixels = Image.open(frame).convert("L").getdata()
    assert max(pixels) > 100, "the panel is on the picture"


def _panel_desk(post_desk: Path, genre: str) -> None:
    import copy
    import json

    from conftest import SPINE, make_take
    from test_finish_inner_voice import FACTS, TWO_LINES

    body = copy.deepcopy(SPINE)
    body["microdrama_genre"] = genre
    episode_id = body["episode_summaries"][0]["episode_id"]
    beats = [b for b in body.get("beats") or [] if b.get("episode_id") == episode_id]
    assert beats, "the shared spine has beats for episode 1"
    beats[0]["system_panels"] = [_cue(appear_at_ms=0)]
    (post_desk / "ep01" / "api" / "03_spine.json").write_text(json.dumps(body), encoding="utf-8")
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(json.dumps(FACTS))
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)


def _run(post_desk: Path, **kwargs: Any):
    import io

    from test_finish_inner_voice import FakeAudio, _bed, _sfx

    from creation.post.finish import run_finish

    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=_sfx, bed_maker=_bed, facts_fetcher=lambda *a: None,
                        voice_audio=FakeAudio(), stream=out, **kwargs)  # fmt: skip
    return result, out.getvalue()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_finish_draws_the_panels_after_the_captions_and_no_panels_skips_them(post_desk: Path) -> None:
    _panel_desk(post_desk, "system_leveling")
    result, text = _run(post_desk)
    assert result.complete, text
    steps = [s.step for s in result.steps]
    assert "system-panels" in steps and steps.index("captions") < steps.index("system-panels")
    assert "level_up" in next(s for s in result.steps if s.step == "system-panels").detail
    assert sorted((post_desk / "ep01" / "takes").glob("take-ep01-t1-panels-v*.ass"))

    again, text = _run(post_desk, no_panels=True)
    assert again.complete, text
    assert "system-panels" not in [s.step for s in again.steps]
    assert "turned off (--no-panels)" in text


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_finish_on_another_genre_draws_no_panel(post_desk: Path) -> None:
    _panel_desk(post_desk, "urban_romance")
    result, text = _run(post_desk)
    assert result.complete, text
    assert "system-panels" not in [s.step for s in result.steps]
    assert "system-panels" not in text
