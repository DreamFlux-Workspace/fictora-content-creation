"""Portrait unchanged by the letterbox deliverable (6 Oct 2026).

HARD RULE from the founder: the 9:16 letterbox file, its title block, the band
captions, phrase chunking, voice-end caption timing and the on-screen italics
check are all gated on a letterbox show filmed 4:3. A portrait show must run
exactly the ffmpeg commands it ran before (filter graphs, captions, timing),
and write the same caption file, on:

* a native take (the plain finish),
* a locked-voice take whose first line carries an ``off_screen`` flag although
  the take facts draw its speaker, and whose second line is long enough to
  chunk differently (the paths item 5, 6 and 7 touch),
* a join of two finished takes.

The golden file was recorded on origin/main (38bcfff) before any letterbox code
existed. Regenerate only on purpose:
``UPDATE_GOLDEN=1 uv run pytest tests/test_letterbox_portrait_unchanged.py``.
"""

from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from conftest import make_take, needs_ffmpeg
from test_hook_overlay_portrait_unchanged import REPO, _recording
from test_post_finish import FACTS, TWO_LINES, _board, fake_bed, fake_sfx
from test_post_join import finished_take, noise_bed

from creation.ops.floor import init_series_desk
from creation.post.finish import run_finish
from creation.post.join import run_join

GOLDEN = Path(__file__).parent / "golden" / "letterbox-portrait-unchanged.json"

#: Kenji's line flagged off screen (stale), although shot 1 draws him; Aya's line long enough to chunk.
LOCKED_LINES = [
    {"line_id": "l1", "cast_id": "cast_kenji", "start_s": 1.0, "end_s": 2.6, "off_screen": True},
    {"line_id": "l2", "cast_id": "cast_aya", "start_s": 3.2, "end_s": 4.6, "off_screen": False},
]  # fmt: skip


def _check(key: str, calls: list[Any]) -> None:
    golden: dict[str, Any] = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden[key] = calls
        GOLDEN.write_text(json.dumps(golden, indent=1) + "\n")
        return
    assert calls == golden[key]


def _locked_spine(desk: Path) -> None:
    api = desk / "ep01" / "api"
    spine = json.loads((api / "03_spine.json").read_text(encoding="utf-8"))
    spine["beats"][0]["dialogue_lines"] = [
        {"line_id": "l1", "cast_id": "cast_kenji", "text": "Wait for me here.", "off_screen": True},
        {"line_id": "l2", "cast_id": "cast_aya",
         "text": "Not tonight, not ever, two things I will never do again."},
    ]  # fmt: skip
    (api / "03_spine.json").write_text(json.dumps(spine), encoding="utf-8")


@needs_ffmpeg
def test_a_portrait_native_finish_runs_the_commands_it_always_ran(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    _board(post_desk)
    calls = _recording(monkeypatch, [post_desk, REPO])

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, stream=io.StringIO())  # fmt: skip

    assert result.complete
    takes = post_desk / "ep01" / "takes"
    captions = (takes / "take-ep01-t1-cap-v1.ass").read_text(encoding="utf-8")
    record = json.loads((takes / "take-ep01-t1-finish-v1.json").read_text())
    _check("finish-native", [*calls, [captions], sorted(record)])


@needs_ffmpeg
def test_a_portrait_locked_voice_finish_runs_the_commands_it_always_ran(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _locked_spine(post_desk)
    facts = copy.deepcopy(FACTS)
    facts["take_facts"]["shots"][0]["people"] = {"count": 1, "named": ["cast_kenji"]}
    facts["take_facts"]["soundtrack"] = {
        "mode": "target_audio", "reason": None, "track_url": "https://x/track.wav",
        "lines": LOCKED_LINES, "native_foley": False,
    }  # fmt: skip
    make_take(
        post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
        tones=((1.0, 2.0, 440), (3.2, 4.0, 880)),
    )
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )
    _board(post_desk)
    calls = _recording(monkeypatch, [post_desk, REPO])

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, cut_meter=lambda _take: (2.5,),
                        stream=io.StringIO())  # fmt: skip

    assert result.complete
    captions = (post_desk / "ep01" / "takes" / "take-ep01-t1-cap-v1.ass").read_text(
        encoding="utf-8"
    )
    _check("finish-locked", [*calls, [captions]])


@needs_ffmpeg
def test_a_portrait_join_runs_the_commands_it_always_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desk = init_series_desk(tmp_path, "Join Test", band="30s", episode_count=1)
    finished_take(desk, 1, "t1", grey=70, tone=0.1)
    finished_take(desk, 1, "t2", grey=90, tone=0.1, box=(1.0, 1.5))
    noise_bed(desk)
    calls = _recording(monkeypatch, [desk, REPO])

    result = run_join(desk, episodes=(1,), stream=io.StringIO())

    assert result.marked is not None
    _check("join", calls)
