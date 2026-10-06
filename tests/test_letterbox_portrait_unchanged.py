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

The golden file is recorded on origin/main before any letterbox code (now
ae52d98, which carries #131's join gain-match change). Numbers measured from
the test media may differ by a few milliseconds between ffmpeg builds
(:data:`NUMBER_TOLERANCE`); everything else must match exactly. Regenerate only on purpose:
``UPDATE_GOLDEN=1 uv run pytest tests/test_letterbox_portrait_unchanged.py``.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
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


#: A number in an ffmpeg argument or a caption file.
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
#: How far a number measured from the test media may differ between machines. The takes are encoded
#: on the machine running the test, and another ffmpeg build gives a container a few milliseconds more
#: (a 5 s take probes 5.013 s on CI's ubuntu ffmpeg, 5.000 s on a laptop), which moves every length,
#: trim and seek computed from it. Anything the letterbox work could change on a portrait show (a
#: step, a filter, an argument, a caption's place, colour, chunk or italics, a timing shift of a
#: frame or more) still differs.
NUMBER_TOLERANCE = 0.03


def _same(got: Any, want: Any) -> bool:
    """Equal, except numbers inside strings may differ by :data:`NUMBER_TOLERANCE`."""

    if isinstance(got, list) and isinstance(want, list):
        return len(got) == len(want) and all(_same(g, w) for g, w in zip(got, want))
    if not isinstance(got, str) or not isinstance(want, str):
        return got == want
    if _NUMBER.sub("#", got) != _NUMBER.sub("#", want):
        return False
    return all(
        abs(float(g) - float(w)) <= NUMBER_TOLERANCE
        for g, w in zip(_NUMBER.findall(got), _NUMBER.findall(want))
    )


def _check(key: str, calls: list[Any]) -> None:
    golden: dict[str, Any] = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden[key] = calls
        GOLDEN.write_text(json.dumps(golden, indent=1) + "\n")
        return
    if not _same(calls, golden[key]):
        assert calls == golden[key]  # the exact diff, for the reader


def test_the_comparison_tolerates_media_timing_only() -> None:
    assert _same(["atrim=0:5.013", "-ss", "1.999"], ["atrim=0:5.000", "-ss", "2.000"])
    assert not _same(["atrim=0:5.200"], ["atrim=0:5.000"]), (
        "a real timing shift is caught"
    )
    assert not _same(["fps=8.0,scale=96:168"], ["fps=8.0,scale=96:169,crop=1:1"])
    assert not _same(["Dialogue: Italic,,Wait"], ["Dialogue: House,,Wait"])
    assert not _same([["-i", "a"]], [["-i", "a"], ["-i", "b"]]), (
        "an extra command is caught"
    )


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

    # Subtle (today's house captions) must burn exactly what the golden recorded; a new show's Bold is new.
    result = run_finish(post_desk, caption_style="subtle", sfx_render=fake_sfx([]), bed_maker=fake_bed,
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

    # Subtle (today's house captions) must burn exactly what the golden recorded; a new show's Bold is new.
    result = run_finish(post_desk, caption_style="subtle", sfx_render=fake_sfx([]), bed_maker=fake_bed,
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
