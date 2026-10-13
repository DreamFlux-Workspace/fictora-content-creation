"""The quiet operator note when a filmed take seems to end on an earlier shot (L-20261009-35).

Last Call ep 2: the punchline shot played at 8-10.7 s, then the take cut back to
an earlier shot at the cafe for 4.3 s. The server's take prompt now says each
shot plays once, in order, and the clip ends on its last shot; ``finish`` prints
this note beside the filmed cuts as the operator's safety net. It never blocks.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from conftest import make_take, needs_ffmpeg
from test_post_finish import FACTS, TWO_LINES, fake_bed, fake_sfx

from creation.post.finish import run_finish
from creation.post.sfx import PlannedShot, filmed_shot_windows
from creation.post.take_facts import ended_on_earlier_shot_note

_PLANNED = (
    PlannedShot(1, 0.0, 4.0),
    PlannedShot(2, 4.0, 8.0),
    PlannedShot(3, 8.0, 15.0),
)


def _note(cuts: tuple[float, ...], *, duration: float = 15.0) -> str | None:
    filmed = filmed_shot_windows(_PLANNED, cuts, duration=duration)
    return ended_on_earlier_shot_note(filmed, cuts, duration=duration)


def test_a_cut_back_inside_the_last_shot_is_named() -> None:
    note = _note((4.1, 8.0, 10.7))
    assert note is not None
    assert "10.70s" in note and "shot 3" in note and "4.30s more" in note
    assert "trim --end" in note


def test_a_take_that_ends_on_its_last_shot_says_nothing() -> None:
    assert _note((4.1, 8.0)) is None
    assert _note((3.9, 7.6)) is None
    assert _note((4.1, 8.0, 14.6)) is None, (
        "a flash in the last frames is a seam, not a shot"
    )


def test_a_late_change_into_the_last_shot_is_not_a_cut_back() -> None:
    assert _note((4.1, 9.3)) is None
    assert _note((4.1, 9.3, 12.0)) is not None


def test_a_one_shot_plan_has_nothing_to_check() -> None:
    plan = (PlannedShot(1, 0.0, 15.0),)
    filmed = filmed_shot_windows(plan, (7.0,), duration=15.0)
    assert ended_on_earlier_shot_note(filmed, (7.0,), duration=15.0) is None


def _finish(post_desk: Path, cuts: tuple[float, ...]) -> str:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    out = io.StringIO()
    run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
        cut_meter=lambda _take: cuts, thumbnail=False, stream=out,
    )  # fmt: skip
    return out.getvalue()


@needs_ffmpeg
def test_finish_prints_the_note_and_still_finishes(post_desk: Path) -> None:
    log = _finish(post_desk, (2.5, 3.8))
    assert "[sfx] !! take may end on an earlier shot: it cuts again at 3.80s" in log
    notes = (post_desk / "ep01" / "run-notes.md").read_text()
    assert "take may end on an earlier shot" in notes


@needs_ffmpeg
def test_finish_says_nothing_when_the_take_ends_on_its_last_shot(
    post_desk: Path,
) -> None:
    assert "earlier shot" not in _finish(post_desk, (2.5,))
