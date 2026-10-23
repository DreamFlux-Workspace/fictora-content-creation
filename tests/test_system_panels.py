"""System panels in the kit: the server places and draws them, the kit asks and burns.

The placement and drawing rules live in fictora-drama (``system-panels`` route,
``tests/drama_generation/test_system_panel_layer.py``); here: the gate, what
the kit sends, how it reads the answer, the older-server stop, and the burn.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from creation.post import system_panels as sp
from creation.post.system_panels import (
    OLDER_SERVER,
    PanelLayer,
    SystemPanelsUnavailable,
    burn_layer,
    layer_from_answer,
    show_takes_panels,
    take_layer,
)

#: A white box from 0 to 2 s on a 360x640 frame: what the server's layer looks like to the burn.
BOX_ASS = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 360\nPlayResY: 640\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Panel,Arial,40,&H00FFFFFF,&HFF000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:00.00,0:00:02.00,Panel,,0,0,0,,{\\pos(40,60)\\p1}m 0 0 l 280 0 280 200 0 200{\\p0}\n"
)


def _cue(**overrides: Any) -> dict[str, Any]:
    cue: dict[str, Any] = {"form": "level_up", "lines": ["LEVEL UP", "Level: 1 > 2"], "appear_at_ms": 500,
                           "duration_ms": 2000, "position": "upper"}  # fmt: skip
    cue.update(overrides)
    return cue


def _answer(start_ms: int = 0, end_ms: int = 2000) -> dict[str, Any]:
    return {
        "schema_version": "fictora.drama-system-panels-response.v1",
        "spine_id": "spine_test",
        "episode_id": "ep01",
        "takes_panels": True,
        "panels": [
            {
                "panel_id": "b1-panel1",
                "take": 1,
                "start_ms": 500,
                "end_ms": 2500,
                "form": "level_up",
                "lines": ["LEVEL UP", "Level: 1 > 2"],
                "style": {"frame": "glass", "palette": ["#0B1220"]},
                "position": "upper",
            },
        ],  # fmt: skip
        "drawn": [{"panel_id": "b1-panel1", "start_ms": start_ms, "end_ms": end_ms}],
        "ass": BOX_ASS,
    }


class FakeApi:
    """Answers the system-panels route as told and remembers what was sent."""

    def __init__(self, status: int = 200, answer: Any = None) -> None:
        self.status = status
        self.answer = _answer() if answer is None and status == 200 else answer
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]:
        self.sent.append((path, body))
        return self.status, self.answer


def _desk(tmp_path: Path) -> Path:
    from creation.production_state import ProductionState, save_production

    save_production(
        tmp_path,
        ProductionState(
            session_id="s",
            prompt="p",
            preset_id="x",
            preset_version="1",
            spine_id="spine_test",
        ),
    )
    return tmp_path


def test_only_system_and_game_genres_ask_for_panels() -> None:
    for genre in (
        "system_leveling",
        "last_human",
        "isekai",
        "cultivation",
        "regression_revenge",
    ):
        assert show_takes_panels({"microdrama_genre": genre})
    assert not show_takes_panels({"microdrama_genre": "urban_romance"})
    assert show_takes_panels(
        {"microdrama_genre": "action_fight", "microdrama_genre_overlays": ["isekai"]}
    )
    assert not show_takes_panels(None)


def test_a_take_sends_its_lengths_and_reads_the_servers_layer(tmp_path: Path) -> None:
    api = FakeApi()
    layer = take_layer(_desk(tmp_path), 1, "ep01", 2, [None, 12.0], width=1080, height=1920,
                       picture=(0, 555, 1080, 810), api=api)  # fmt: skip
    path, body = api.sent[0]
    assert path == "/v1/spines/spine_test/episodes/ep01/system-panels"
    # An earlier take with no raw file counts as 15 s.
    assert body == {"take_ms": [15000, 12000], "width": 1080, "height": 1920, "take": 2,
                    "picture": [0, 555, 1080, 810]}  # fmt: skip
    assert layer and len(layer) == 1 and layer.ass == BOX_ASS
    assert (
        layer.describe()
        == "1 panel(s): level_up (glass, upper) 0.00-2.00 s: LEVEL UP / Level: 1 > 2"
    )
    # Every take of the episode is sent, so the server reads each beat's take from the pattern;
    # takes after this one count as 15 s and never move its panels.
    take_layer(_desk(tmp_path), 1, "ep01", 2, [10.0, 12.0], width=1080, height=1920,
               take_count=4, api=api)  # fmt: skip
    assert api.sent[1][1]["take_ms"] == [10000, 12000, 15000, 15000]


def test_nothing_drawn_reads_as_no_panels() -> None:
    answer = {**_answer(), "drawn": [], "ass": None}
    assert not layer_from_answer(answer)


@pytest.mark.parametrize(
    "status,answer",
    [(404, {"detail": "Not Found"}), (405, {"detail": "Method Not Allowed"})],
)
def test_an_older_server_stops_with_the_fix(
    tmp_path: Path, status: int, answer: Any
) -> None:
    with pytest.raises(SystemPanelsUnavailable) as stopped:
        take_layer(
            _desk(tmp_path),
            1,
            "ep01",
            1,
            [10.0],
            width=1080,
            height=1920,
            api=FakeApi(status, answer),
        )
    assert str(stopped.value) == OLDER_SERVER.format(command="finish")
    assert str(stopped.value) == (
        "this Drama API cannot draw system panels yet; finish with --no-panels until it is updated"
    )


def test_a_named_refusal_is_not_an_older_server(tmp_path: Path) -> None:
    refused = FakeApi(
        404,
        {
            "error": {
                "code": "episode_not_found",
                "message": "Episode is not available: ep01",
            }
        },
    )
    with pytest.raises(SystemPanelsUnavailable) as stopped:
        take_layer(
            _desk(tmp_path), 1, "ep01", 1, [10.0], width=1080, height=1920, api=refused
        )
    assert "Episode is not available" in str(stopped.value) and "--no-panels" in str(
        stopped.value
    )


def test_finish_takes_no_panels() -> None:
    import argparse

    from creation.cli_post import add_post_parsers

    parser = argparse.ArgumentParser()
    add_post_parsers(parser.add_subparsers(dest="command"))
    assert parser.parse_args(
        ["finish", "--desk", "d", "--episode", "1", "--take", "t1", "--no-panels"]
    ).no_panels


def _make_box_take(path: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=360x640:d=2:r=24",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)  # fmt: skip


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_burn_layer_draws_the_servers_layer_on_a_take(tmp_path: Path) -> None:
    take = tmp_path / "take.mp4"
    _make_box_take(take)
    out = burn_layer(
        layer_from_answer(_answer()), take, tmp_path / "p.ass", tmp_path / "out.mp4"
    )
    assert out.is_file() and (tmp_path / "p.ass").read_text(encoding="utf-8") == BOX_ASS
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.5", "-i", str(out), "-frames:v", "1", str(frame)],
                   check=True)  # fmt: skip
    from PIL import Image

    assert max(Image.open(frame).convert("L").getdata()) > 100, (
        "the panel is on the picture"
    )
    with pytest.raises(ValueError):
        burn_layer(PanelLayer(ass=None), take, tmp_path / "q.ass", tmp_path / "q.mp4")


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
    (post_desk / "ep01" / "api" / "03_spine.json").write_text(
        json.dumps(body), encoding="utf-8"
    )
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)


def serve_panels(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> FakeApi:
    """Answer the desk's system-panels calls from ``api`` (finish opens its own session)."""

    class _Session(FakeApi):
        class client:  # noqa: N801 - mirrors DramaApiRunSession.client
            @staticmethod
            def close() -> None:
                return None

    session = _Session(api.status, api.answer)
    session.sent = api.sent
    monkeypatch.setattr("creation.post.desk.open_api", lambda desk, episode: session)
    return api


def _run(post_desk: Path, **kwargs: Any):
    import io

    from test_finish_inner_voice import FakeAudio, _sfx

    from creation.post.finish import run_finish

    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=_sfx, facts_fetcher=lambda *a: None,
                        voice_audio=FakeAudio(), stream=out, **kwargs)  # fmt: skip
    return result, out.getvalue()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_finish_burns_the_servers_panels_after_the_captions_and_no_panels_skips_them(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _panel_desk(post_desk, "system_leveling")
    api = serve_panels(monkeypatch, FakeApi())
    result, text = _run(post_desk)
    assert result.complete, text
    steps = [s.step for s in result.steps]
    assert "system-panels" in steps and steps.index("captions") < steps.index(
        "system-panels"
    )
    assert (
        "level_up" in next(s for s in result.steps if s.step == "system-panels").detail
    )
    overlays = sorted(
        (post_desk / "ep01" / "takes").glob("take-ep01-t1-panels-overlay-v*.ass")
    )
    assert overlays and overlays[-1].read_text(encoding="utf-8") == BOX_ASS
    assert api.sent and api.sent[0][1]["take"] == 1

    asked = len(api.sent)
    again, text = _run(post_desk, no_panels=True)
    assert again.complete, text
    assert "system-panels" not in [s.step for s in again.steps]
    assert "turned off (--no-panels)" in text
    assert len(api.sent) == asked, "--no-panels never asks the server"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_finish_on_an_older_server_stops_before_any_work(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _panel_desk(post_desk, "system_leveling")
    serve_panels(monkeypatch, FakeApi(404, {"detail": "Not Found"}))
    with pytest.raises(
        SystemPanelsUnavailable, match="finish with --no-panels until it is updated"
    ):
        _run(post_desk)
    assert not list((post_desk / "ep01" / "takes").glob("*panels*"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_finish_on_another_genre_never_asks(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _panel_desk(post_desk, "urban_romance")
    api = serve_panels(monkeypatch, FakeApi(404, {"detail": "Not Found"}))
    result, text = _run(post_desk)
    assert result.complete, text
    assert (
        "system-panels" not in [s.step for s in result.steps]
        and "system-panels" not in text
    )
    assert api.sent == []


def test_the_kit_keeps_no_copy_of_the_rules() -> None:
    """Placement and drawing are the server's (server-heavy rule): none of the old mirror is left."""

    for gone in (
        "place_panels",
        "take_panels",
        "reel_panels",
        "reel_layer",
        "system_panels_ass",
        "panel_area",
        "burn_panels",
    ):
        assert not hasattr(sp, gone), gone
