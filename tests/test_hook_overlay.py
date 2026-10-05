"""The on-screen hook line and the letterbox title bar (founder decisions, 5 Oct 2026)."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import make_take, needs_ffmpeg
from test_post_finish import FACTS, TWO_LINES, _board, fake_bed, fake_sfx
from test_reel import reel_desk  # noqa: F401  (fixture)

from creation.captions import CAPTION_BAND, text_width
from creation.cli_produce import main
from creation.post import hook_overlay as hk
from creation.post.finish import run_finish
from creation.post.hook_overlay import (
    HookOverlay,
    decide,
    delivery_format,
    hook_end,
    overlay_ass,
)
from creation.post.reel import run_reel

W, H = 1080, 1920


def _spine(
    hook: dict[str, Any] | None = None, *, first_line: str | None = None, **top: Any
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "episode_id": "episode_01",
        "ordinal": 1,
        "title": "The Door",
    }
    if hook is not None:
        summary["hook_line_selected"] = hook
    if first_line is not None:
        summary["first_line"] = first_line
    return {
        "title": "Tiny Show",
        "episode_summaries": [summary],
        "beats": [
            {
                "episode_id": "episode_01",
                "ordinal": 1,
                "dialogue_lines": [{"text": "Open the door!"}],
            }
        ],
        **top,
    }


ON = {"kind": "option", "index": 0, "text": "Never open the third door"}


def _pos(ass: str) -> tuple[str, int, int]:
    match = re.search(r"\\an(\d)\\pos\((\d+),(\d+)\)", ass)
    assert match, ass
    return match.group(1), int(match.group(2)), int(match.group(3))


# --- what is drawn, and when nothing is ----------------------------------------------------------


def test_the_selected_hook_line_is_drawn_top_below_the_ui_strip_and_out_of_the_rail() -> (
    None
):
    decision = decide(_spine(ON), 1, face_in_upper_band=None)
    assert decision.overlay is not None and decision.overlay.placement == "top"

    ass = overlay_ass(decision.overlay, width=W, height=H)
    anchor, x, y = _pos(ass)

    assert anchor == "8" and y >= hk.TOP_STRIP * H
    widest = max(
        text_width(line, 88) for line in ass.rsplit("}", 1)[1].strip().split("\\N")
    )
    assert x + widest / 2 <= hk.RIGHT_RAIL_X * W, (
        "the line never runs into the right-hand rail"
    )
    assert "\\fad(120,250)" in ass and "0:00:03.00" in ass
    margin_l, margin_r = (
        int(v) for v in re.search(r"Style: Hook,.*,8,(\d+),(\d+),0,1", ass).groups()
    )
    assert margin_r >= (1 - hk.RIGHT_RAIL_X) * W, (
        "the wrap box itself stops short of the rail"
    )
    assert x == margin_l + (W - margin_l - margin_r) // 2


def test_a_face_in_the_upper_band_moves_it_just_above_the_captions() -> None:
    decision = decide(
        _spine(ON), 1, video=Path("x.mp4"), face_in_upper_band=lambda *a: True
    )
    assert decision.overlay is not None and decision.overlay.placement == "lower"

    anchor, _, y = _pos(overlay_ass(decision.overlay, width=W, height=H))

    assert anchor == "2" and y < CAPTION_BAND[0] * H


def test_an_unknown_face_keeps_it_at_the_top() -> None:
    decision = decide(
        _spine(ON), 1, video=Path("x.mp4"), face_in_upper_band=lambda *a: None
    )
    assert decision.overlay is not None and decision.overlay.placement == "top"


def test_it_leaves_on_the_first_cut_after_about_three_seconds_else_fades() -> None:
    assert hook_end([1.2, 3.4, 3.9]) == (3.4, True)
    assert hook_end([1.2, 5.0]) == (3.0, False)
    ass = overlay_ass(
        HookOverlay("hook", "A line", "top", 0.0, 3.4, on_cut=True), width=W, height=H
    )
    assert "\\fad(120,0)" in ass


def test_it_is_skipped_when_it_says_the_first_spoken_line() -> None:
    decision = decide(
        _spine({"kind": "custom", "text": "open the DOOR"}), 1, face_in_upper_band=None
    )

    assert decision.overlay is None and "first spoken line" in (decision.skipped or "")


def test_off_none_and_operator_flags() -> None:
    assert decide(_spine({"kind": "off"}), 1).overlay is None
    assert decide(_spine(None), 1).overlay is None
    assert decide(_spine(ON), 1, off=True).skipped == "turned off (--no-hook-line)"
    forced = decide(
        _spine(None),
        1,
        override="  She lies   tonight ",
        position="lower",
        face_in_upper_band=None,
    )
    assert forced.overlay is not None
    assert (forced.overlay.text, forced.overlay.placement, forced.overlay.source) == (
        "She lies tonight",
        "lower",
        "--hook-line",
    )


# --- letterbox title bar ----------------------------------------------------------------------------


def test_delivery_format_is_portrait_unless_the_spine_says_letterbox() -> None:
    assert delivery_format(_spine()) == "portrait"
    assert delivery_format(_spine(delivery_format="portrait")) == "portrait"
    assert delivery_format(_spine(delivery_format="letterbox")) == "letterbox"
    assert delivery_format(_spine(show={"delivery_format": "letterbox"})) == "letterbox"


def test_a_letterbox_show_gets_a_solid_title_bar_for_the_whole_video() -> None:
    decision = decide(_spine(ON, delivery_format="letterbox"), 1, size=(W, H))
    overlay = decision.overlay
    assert overlay is not None and overlay.mode == "title_bar" and overlay.end_s is None
    assert (overlay.title, overlay.text) == ("Tiny Show", ON["text"])

    ass = overlay_ass(overlay, width=W, height=H, duration=12.0)

    assert "\\p1}m 0 0 l 1080 0" in ass, "a solid band across the full width"
    assert "0:00:12.00" in ass
    band_top = int(re.search(r"\\an7\\pos\(0,(\d+)\)", ass).group(1))
    assert band_top >= hk.TOP_STRIP * H


def test_a_creator_title_line_wins_on_the_bar() -> None:
    spine = _spine(ON, delivery_format="letterbox")
    spine["episode_summaries"][0]["title_line"] = "Episode 1: The Door"
    assert decide(spine, 1, size=(W, H)).overlay.text == "Episode 1: The Door"  # type: ignore[union-attr]


def test_a_real_four_three_take_defers_to_the_servers_letterbox() -> None:
    decision = decide(_spine(ON, delivery_format="letterbox"), 1, size=(1440, 1080))
    assert decision.overlay is None and "4:3" in (decision.skipped or "")


# --- end to end: reel plan JSON, reel render, finish step ---------------------------------------


def _set_hook(desk: Path, hook: dict[str, Any]) -> None:
    path = next((desk / "ep01" / "api").glob("*spine*.json"))
    spine = json.loads(path.read_text())
    spine["episode_summaries"][0]["hook_line_selected"] = hook
    path.write_text(json.dumps(spine))


@needs_ffmpeg
def test_the_reel_records_and_burns_the_hook_line(reel_desk: Path) -> None:  # noqa: F811
    _set_hook(reel_desk, ON)

    result = run_reel(reel_desk, episode=1, seconds=6.0, stream=io.StringIO())

    body = json.loads(result.plan_path.read_text())
    assert (
        body["hook_line"]["text"] == ON["text"] and body["hook_line"]["mode"] == "hook"
    )
    assert (
        body["hook_line"]["placement"] == "top" and body["hook_line"]["start_s"] == 0.0
    )
    assert (reel_desk / "reels" / "reel-ep01-v1-hook.ass").is_file()
    assert any("hook line" in line for line in result.lines)


@needs_ffmpeg
def test_no_hook_line_records_why_in_the_plan(reel_desk: Path) -> None:  # noqa: F811
    _set_hook(reel_desk, ON)

    result = run_reel(
        reel_desk, episode=1, seconds=6.0, plan_only=True, no_hook_line=True
    )

    assert (
        json.loads(result.plan_path.read_text())["hook_line"]["skipped"]
        == "turned off (--no-hook-line)"
    )


@needs_ffmpeg
def test_finish_burns_the_hook_line_on_the_first_take_before_the_mark(
    post_desk: Path,
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    _board(post_desk)
    _set_hook(post_desk, ON)

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, stream=io.StringIO())  # fmt: skip

    steps = [s.step for s in result.steps]
    assert steps.index("captions") < steps.index("hook-line") < steps.index("watermark")
    hook = next(s for s in result.steps if s.step == "hook-line")
    assert hook.status == "ran" and "2.5" in hook.detail, (
        "it leaves on the take's cut at 2.5 s"
    )


def test_the_cli_takes_the_hook_line_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_reel(desk: Path, **kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr("creation.post.reel.run_reel", fake_reel)

    assert main(["reel", "--desk", str(tmp_path), "--episode", "2", "--hook-line", "She lies",
                 "--hook-line-position", "lower"]) == 0  # fmt: skip
    assert (seen["hook_line"], seen["no_hook_line"], seen["hook_line_position"]) == (
        "She lies",
        False,
        "lower",
    )
    with pytest.raises(SystemExit):
        main(
            [
                "reel",
                "--desk",
                str(tmp_path),
                "--episode",
                "2",
                "--hook-line",
                "x",
                "--no-hook-line",
            ]
        )
