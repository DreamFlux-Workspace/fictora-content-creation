"""Portrait unchanged by the letterbox review crop (6 Oct 2026).

``review`` on a letterbox show's 9:16 file measures only the picture (the 4:3
take on the canvas, y 555-1365), not the black bands, the title or the band
captions. A portrait take and a portrait reel must run exactly the ffmpeg
commands they ran before. The golden file is recorded on origin/main (8faeaaa)
before any of that code. Regenerate only on purpose:
``UPDATE_GOLDEN=1 uv run pytest tests/test_review_portrait_unchanged.py``.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from conftest import make_take, needs_ffmpeg
from test_hook_overlay_portrait_unchanged import REPO, _recording
from test_letterbox_portrait_unchanged import _same
from test_post_finish import FACTS, TWO_LINES, _board, fake_bed, fake_sfx
from test_reel import reel_desk  # noqa: F401

from creation.post.finish import run_finish
from creation.post.review import review_take

GOLDEN = Path(__file__).parent / "golden" / "review-portrait-unchanged.json"


def _check(key: str, calls: list[Any]) -> None:
    golden: dict[str, Any] = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden[key] = calls
        GOLDEN.write_text(json.dumps(golden, indent=1) + "\n")
        return
    if not _same(calls, golden[key]):
        assert calls == golden[key]  # the exact diff, for the reader


def _no_text(frames: Any, languages: Any) -> str:
    return ""


@needs_ffmpeg
def test_review_of_a_portrait_finished_take_runs_the_commands_it_always_ran(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    _board(post_desk)
    # Subtle (today's house captions) must burn exactly what the golden recorded; a new show's Bold is new.
    result = run_finish(post_desk, caption_style="subtle", sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, stream=io.StringIO())  # fmt: skip
    assert result.complete and result.final is not None
    calls = _recording(monkeypatch, [post_desk, REPO])

    review = review_take(
        post_desk, take_file=result.final, text_ocr=_no_text, face_detector=None
    )

    sections = [[s.name, s.status] for s in review.sections]
    _check("review-finished", [*calls, sections])
