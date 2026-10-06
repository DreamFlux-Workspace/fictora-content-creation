"""Portrait unchanged: with no hook line and no letterbox, finish and reel run the same ffmpeg commands as before.

Founder requirement (5 Oct 2026): the hook-line overlay and the letterbox title bar
may not disturb anything else. With the hook line off (or none on the spine) and a
portrait show, every ffmpeg command finish and reel run (filter graphs, captions,
timing) must equal the ones recorded on origin/main before the overlay existed
(``tests/golden/portrait-unchanged.json``). Regenerate only on purpose:
``UPDATE_GOLDEN=1 uv run --group test pytest tests/test_hook_overlay_portrait_unchanged.py``.
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
from test_post_finish import FACTS, TWO_LINES, _board, fake_bed, fake_sfx
from test_reel import reel_desk  # noqa: F401  (fixture)

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
        ):
            calls.append(_normalised([str(x) for x in args], roots))
        return real(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    return calls


def _check(key: str, calls: list[list[str]]) -> None:
    golden: dict[str, Any] = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden[key] = calls
        GOLDEN.write_text(json.dumps(golden, indent=1) + "\n")
        return
    assert calls == golden[key]


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
    result = run_finish(post_desk, caption_style="subtle", sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, stream=io.StringIO())  # fmt: skip

    assert result.complete
    captions = (post_desk / "ep01" / "takes" / "take-ep01-t1-cap-v1.ass").read_text(
        encoding="utf-8"
    )
    _check("finish", [*calls, [captions]])
