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
from reel_fake_server import FakeReelServer

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


def test_a_line_moved_down_for_a_face_never_sits_on_the_first_caption() -> None:
    """L-20261005-11 (Sweet Racket, Last Call): the face check dropped the line to just above the
    caption band while the first caption was up. Founder, 7 Oct 2026: it stays at the top then; the
    caption keeps its timing. Same rule as the server's ``reel_overlays.decide_hook``."""

    def placed(captions: list[tuple[float, float]], **kw: Any) -> HookOverlay:
        decision = decide(
            _spine(ON), 1, video=Path("x.mp4"), face_in_upper_band=lambda *a: True,
            captions=captions, **kw,
        )  # fmt: skip
        assert decision.overlay is not None and decision.overlay.end_s == 3.0
        return decision.overlay

    up = placed([(0.3, 1.2)])
    assert up.placement == "top" and "caption is on screen" in up.describe()
    assert _pos(overlay_ass(up, width=W, height=H))[0] == "8"
    assert placed([(2.9, 4.0)]).placement == "top"
    # No caption while the line is up: it still clears the face, exactly as before.
    assert placed([]).placement == "lower"
    assert placed([(3.0, 4.0)]).placement == "lower"
    assert placed([], position="lower").placement == "lower"
    # An operator's --hook-line-position lower obeys the same rule.
    assert placed([(0.3, 1.2)], position="lower").placement == "top"
    # The plan JSON keeps its keys.
    assert set(up.as_json()) == set(placed([]).as_json())


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
def test_the_reel_records_the_servers_hook_line_and_sends_the_operators_choices(
    reel_desk: Path,  # noqa: F811
    reel_server: FakeReelServer,
) -> None:
    _set_hook(reel_desk, ON)

    result = run_reel(reel_desk, episode=1, seconds=6.0, stream=io.StringIO())
    run_reel(reel_desk, episode=1, seconds=6.0, stream=io.StringIO(), no_hook_line=True)

    # The spine's pick is the server's to read; the request names only the operator's flags.
    first, second = reel_server.requests
    assert "hook_line" not in first and first["no_hook_line"] is False
    assert second["no_hook_line"] is True
    body = json.loads(result.plan_path.read_text())
    assert body["hook_line"]["mode"] == "hook" and result.hook_text


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


@needs_ffmpeg
def test_finish_keeps_a_lowered_hook_line_off_the_captions_it_burned(
    post_desk: Path,
) -> None:
    """L-20261005-11: finish burns the captions first; a line placed low goes back to the top while
    one of them is on screen, and the drawn ASS says so (an8 under the top strip)."""

    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    _board(post_desk)
    _set_hook(post_desk, ON)

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, stream=io.StringIO(),
                        hook_line_position="lower")  # fmt: skip

    hook = next(s for s in result.steps if s.step == "hook-line")
    assert hook.status == "ran"
    assert "kept at the top: a caption is on screen under it" in hook.detail
    # The hook card's own burn file (the .ass beside the hook video also holds the captions, L-20261006-8).
    drawn = sorted((post_desk / "ep01" / "takes").glob("*-hook-overlay-v*.ass"))[-1]
    assert _pos(drawn.read_text(encoding="utf-8"))[0] == "8"


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


def test_the_default_face_check_asks_the_faces_module_for_the_upper_band(
    monkeypatch,
) -> None:
    from creation.post import faces, hook_overlay

    asked: list[tuple[float, float, float, float]] = []

    def fake(source, start_s=0.0, end_s=None, *, top, bottom, **_):
        asked.append((start_s, end_s, top, bottom))
        return True

    monkeypatch.setattr(faces, "upper_band_face", fake)

    assert hook_overlay.default_face_in_upper_band(Path("x.mp4"), 0.0, 3.0) is True
    assert asked == [(0.0, 3.0, hook_overlay.TOP_STRIP, hook_overlay.CAPTION_BAND[0])]
    monkeypatch.setattr(faces, "upper_band_face", lambda *a, **k: None)
    assert hook_overlay.default_face_in_upper_band(Path("x.mp4"), 0.0, 3.0) is None


class _HookDecided(Exception):
    pass


@needs_ffmpeg
def test_the_local_reel_hands_its_captions_to_the_hook_line(
    reel_desk: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reel's captions, re-timed through the cut, reach the hook decision (L-20261005-11)."""

    seen: dict[str, Any] = {}

    def recording(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise _HookDecided

    monkeypatch.setattr("creation.post.reel.reel_hook", recording)
    _set_hook(reel_desk, ON)
    with pytest.raises(_HookDecided):
        run_reel(
            reel_desk, episode=1, seconds=6.0, stream=io.StringIO(),
            server_refused=RuntimeError("no server in this test"), hook_line_position="lower",
        )  # fmt: skip

    captions = seen["captions"]
    assert captions and all(end > start >= 0.0 for start, end in captions)
    assert captions[0][0] < 3.0, "a caption plays under the hook line's window"
