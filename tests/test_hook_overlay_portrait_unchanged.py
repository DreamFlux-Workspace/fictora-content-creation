"""Portrait unchanged: with no hook line and no letterbox, finish runs the same ffmpeg commands as before.

Founder requirement (5 Oct 2026): the hook-line overlay and the letterbox title bar
may not disturb anything else. With the hook line off (or none on the spine) and a
portrait show, every ffmpeg command finish runs (filter graphs, captions,
timing) must equal the ones recorded on origin/main before the overlay existed
(``tests/golden/portrait-unchanged.json``). Regenerate only on purpose:
``UPDATE_GOLDEN=1 uv run --group test pytest tests/test_hook_overlay_portrait_unchanged.py``.

Re-recorded 25 Oct 2026 (kit #294) for the mark only: 3% in and the approved reels' size
(72/768 of the width), so the test frame's overlay reads ``scale=18:16`` and ``overlay=6:30``
(was ``overlay=23:30``, 12% in at the PNG's own size). Nothing else changed.

Re-recorded 27 Oct 2026 for the mark only: the mark with the word ("E2", ``sokii-mark-word.png``),
230/1080 of the width at full strength, so the overlay reads ``scale=41:16`` and ``aa=1.0``.
The same day it became the app's header logo (no pill, 460x148), so it reads ``scale=41:13``.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from conftest import make_take, needs_ffmpeg
from test_post_finish import FACTS, TWO_LINES, _board, fake_sfx

from creation.post.finish import run_finish

GOLDEN = Path(__file__).parent / "golden" / "portrait-unchanged.json"
REPO = Path(__file__).resolve().parents[1]
_TEMP = re.escape(os.path.realpath(tempfile.gettempdir()))
_MEASURED_GAIN = re.compile(r"volume=[+-]\d+(?:\.\d+)?dB")


def _normalised(argv: list[str], roots: list[Path]) -> list[str]:
    out = []
    for arg in argv[1:]:
        text = str(arg)
        for index, root in enumerate(roots):
            text = text.replace(str(root), f"<root{index}>")
        text = re.sub(_TEMP + r"/[^/:'\s]+", "<tmp>", text)
        text = re.sub(
            r"/(private/)?(var/folders|tmp)/[^:'\s]*?/T/[^/:'\s]+", "<tmp>", text
        )
        # A gain measured from the take's own audio (``volume=+12.1dB``) moves by
        # 0.1 dB between ffmpeg builds (CI vs a laptop). Its presence and place
        # in the graph are what the guard is about, not the measured number.
        text = _MEASURED_GAIN.sub("volume=<dB>", text)
        out.append(text)
    return out


def _recording(monkeypatch: pytest.MonkeyPatch, roots: list[Path]) -> list[list[str]]:
    calls: list[list[str]] = []
    real = subprocess.run

    def spy(args: Any, *a: Any, **k: Any) -> Any:
        if (
            isinstance(args, (list, tuple))
            and args
            and "ffmpeg" in Path(str(args[0])).name
            and "-filters" not in args
        ):
            calls.append(_normalised([str(x) for x in args], roots))
        return real(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    return calls


#: A number that is a length or a point in time: a trim's bounds or length, an ``anullsrc``
#: duration, or a caption timestamp (``0:00:01.00``).
_TIME = re.compile(
    r"(?<=\btrim=)[\d.:]+|(?<=atrim=)[\d.:]+|(?<=start=)[\d.]+|(?<=end=)[\d.]+"
    r"|(?<=duration=)[\d.]+|\b\d+:\d{2}:\d{2}\.\d{2}\b"
)
#: Arguments whose value is a time.
_TIME_FLAGS = {"-t", "-ss", "-to"}
#: How far a time measured from the test media may differ between machines. The takes are encoded
#: on the machine running the test, and another ffmpeg build gives a container a few milliseconds more
#: (a 5 s take probes 5.013 s on CI's ubuntu ffmpeg, 5.000 s on a laptop), which moves every length,
#: trim and seek computed from it. Only times get this slack: a volume, a mix level or any other
#: number must match exactly, and a timing shift of a frame (0.042 s) or more still differs.
TIME_TOLERANCE = 0.03


def _seconds(text: str) -> list[float]:
    """The numbers in a time value; a ``h:mm:ss.cc`` timestamp is one number of seconds."""

    if re.fullmatch(r"\d+:\d{2}:\d{2}\.\d{2}", text):
        h, m, s = text.split(":")
        return [int(h) * 3600 + int(m) * 60 + float(s)]
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]


def _times(text: str, after_time_flag: bool) -> tuple[str, list[float]]:
    """The text with its time values blanked, and those values in seconds."""

    if after_time_flag and re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return "#", [float(text)]
    found: list[float] = []

    def blank(match: re.Match[str]) -> str:
        found.extend(_seconds(match.group()))
        return "#" * match.group().count(":") + "#"

    return _TIME.sub(blank, text), found


def _same(got: Any, want: Any, after_time_flag: bool = False) -> bool:
    """Equal, except times inside strings may differ by :data:`TIME_TOLERANCE`."""

    if isinstance(got, list) and isinstance(want, list):
        return len(got) == len(want) and all(
            _same(
                g,
                w,
                i > 0 and isinstance(want[i - 1], str) and want[i - 1] in _TIME_FLAGS,
            )
            for i, (g, w) in enumerate(zip(got, want))
        )
    if not isinstance(got, str) or not isinstance(want, str):
        return got == want
    got_rest, got_times = _times(got, after_time_flag)
    want_rest, want_times = _times(want, after_time_flag)
    return (
        got_rest == want_rest
        and len(got_times) == len(want_times)
        and all(abs(g - w) <= TIME_TOLERANCE for g, w in zip(got_times, want_times))
    )


def _check(key: str, calls: list[list[str]]) -> None:
    golden: dict[str, Any] = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden[key] = calls
        GOLDEN.write_text(json.dumps(golden, indent=1) + "\n")
        return
    if not _same(calls, golden[key]):
        assert calls == golden[key]  # the exact diff, for the reader


def _without_action_trace(calls: list[list[str]]) -> list[list[str]]:
    """The calls with the new desk's one action-cue motion trace taken out (L-20260930-10).

    The golden is shared with the legacy desk's finish (``test_rules_epoch``), which never reads
    the trace. On a new desk the trace is an analysis pass: it makes no picture or sound, and
    the commands that lay the effects must still match the golden.
    """

    from creation.post.sfx_motion import MOTION_SCALE

    traces = [
        c for c in calls if any(x.startswith(f"scale={MOTION_SCALE},") for x in c)
    ]
    assert len(traces) == 1, "a new desk with an action cue reads the motion trace once"
    return [c for c in calls if c is not traces[0]]


@needs_ffmpeg
def test_finish_without_a_hook_line_runs_the_commands_it_always_ran(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    _board(post_desk)
    calls = _recording(monkeypatch, [post_desk, REPO])

    # Subtle (today's house captions) must burn exactly what the golden recorded; a new show's Bold is new.
    result = run_finish(post_desk, caption_style="subtle", sfx_render=fake_sfx([]),
                        facts_fetcher=lambda *a: None, stream=io.StringIO())  # fmt: skip

    assert result.complete
    captions = (post_desk / "ep01" / "takes" / "take-ep01-t1-cap-v1.ass").read_text(
        encoding="utf-8"
    )
    _check("finish", [*_without_action_trace(calls), [captions]])
