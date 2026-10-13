"""Big impact beats punched up on the frame their effect plays (L-20261009-9, L-20260929-24).

The kit's ``finish`` runs the server's pass (fictora-drama ``impact_post``) with the same numbers:
a white flash (0.9 -> 0 over 0.32 s) and a decaying shake on a blast, a punch-in and a small shake on
a hard hit; on measured motion where the effect is heard, at most two per take, never mid-line.
"""

from __future__ import annotations

import io
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from conftest import make_tone, needs_ffmpeg

from creation.post.finish import run_finish
from creation.post.impact_post import (
    BLAST_STEMS,
    HIT_STEMS,
    FLASH_PEAK,
    FLASH_SECONDS,
    MAX_MOMENTS_PER_TAKE,
    PUNCH_ZOOM,
    ImpactMoment,
    ImpactPlan,
    apply_impacts,
    impact_style,
    moment_move,
    plan_impacts,
)
from creation.post.lineage import KEEPS_TIMELINE
from creation.post.sfx import SfxCue
from test_post_finish import fake_bed
from test_sfx_motion import _desk

FPS = 24
WINDOWS = {1: (0.0, 2.0), 2: (2.0, 4.0)}


def _cue(
    sound: str, start: float = 2.5, *, shot: int = 2, kind: str = "event"
) -> SfxCue:
    return SfxCue(shot_index=shot, sound=sound, kind=kind, start=start, seconds=1.0)


def _trace(
    seconds: float = 4.0, *, base: float = 0.6, spikes: dict[float, float] | None = None
):
    out = []
    for frame in range(1, int(seconds * FPS)):
        t = round(frame / FPS, 3)
        value = base
        for at, height in (spikes or {}).items():
            if abs(t - at) < 0.5 / FPS:
                value = height
        out.append((t, value))
    return tuple(out)


# --- which beats: the server's words ---------------------------------------------------------------


def test_explosions_are_blasts_hard_hits_are_hits_and_soft_or_denied_hits_get_nothing() -> (
    None
):
    """The same sounds the server's tests classify (fictora-drama ``test_impact_post.py``)."""

    for blast in ("a gas tank explodes", "a deep boom rolls", "a single gunshot"):
        assert impact_style(blast, "event") == "blast", blast
    for hit in (
        "the door slams shut",
        "she slaps him hard",
        "a fist punches the table",
    ):
        assert impact_style(hit, "event") == "hit", hit
    for soft in (
        "a knock at the door",
        "a soft thud",
        "a dry stone crack",
        "rain on the window",
    ):
        assert impact_style(soft, "event") is None, soft
    assert impact_style("no explosion, only silence", "event") is None
    assert impact_style("a door slams with a thud", "event") == "hit", (
        "a hard hit with a softer sound"
    )
    assert impact_style("a gas tank explodes", "sustained") is None
    assert impact_style("a door slams on the first frame", "event") is None


#: Real-footage check of fictora-drama #764 (13 Oct): only a true explosion flashes; a bang is a hit at
#: most; several hits get nothing. The same table as the server's tests (test_impact_post.py there).
STYLE_CASES = (
    ("a gas tank explodes", "blast"),
    ("a blast", "blast"),
    ("a blast rips through the hangar", "blast"),
    ("the bomb's detonation", "blast"),
    ("kaboom", "blast"),
    ("a deep boom rolls", "blast"),
    ("a door bangs shut", "hit"),
    ("a single bang on the table", "hit"),
    ("a door slams with a boom", "blast"),
    ("a single gunshot", "blast"),
    ("a knock, then the door slams", "hit"),
    ("three cane bangs", None),
    ("the cane bangs three times", None),
    ("he knocks twice", None),
    ("distant explosions", None),
    ("a flurry of punches", None),
    ("a blast of cold air", None),
    ("music blasts from the radio", None),
    ("no explosion, only silence", None),
    ("a soft thud", None),
)


def test_the_word_lists_are_the_servers() -> None:
    """The server's ``impact_words`` lists (fictora-drama #767; kit copy in ``ambience``, #219)."""

    assert BLAST_STEMS == {
        "explosion", "explode", "blast", "boom", "gunshot", "detonation", "detonate", "kaboom",
    }  # fmt: skip
    assert HIT_STEMS == {
        "slam", "bang", "crash", "smash", "shatter", "punch", "kick", "slap", "smack", "whack", "thwack",
    }  # fmt: skip


@pytest.mark.parametrize(("sound", "style"), STYLE_CASES)
def test_only_true_explosions_flash_a_bang_is_a_hit_and_several_hits_get_nothing(
    sound: str, style: str | None
) -> None:
    assert impact_style(sound, "event") == style, sound


# --- where -------------------------------------------------------------------------------------------


def test_a_blast_lands_on_the_measured_spike_where_its_effect_plays() -> None:
    plan = plan_impacts(
        [_cue("a gas tank explodes", 3.0)], WINDOWS, _trace(spikes={3.0: 12.0})
    )

    assert [(m.time, m.style) for m in plan.moments] == [(3.0, "blast")]


def test_no_spike_or_a_spike_away_from_the_effect_gets_nothing() -> None:
    assert (
        plan_impacts([_cue("a gas tank explodes", 3.0)], WINDOWS, _trace()).moments
        == ()
    )
    away = plan_impacts(
        [_cue("a gas tank explodes", 2.5)], WINDOWS, _trace(spikes={3.0: 12.0})
    )
    assert away.moments == ()
    assert "not where the effect plays" in away.skipped[0]


def test_never_mid_line() -> None:
    plan = plan_impacts(
        [_cue("the door slams shut", 3.0)],
        WINDOWS,
        _trace(spikes={3.0: 12.0}),
        speech=((2.6, 3.4),),
    )

    assert plan.moments == () and "mid-line" in plan.skipped[0]


def test_at_most_two_per_take_blasts_first() -> None:
    cues = [
        _cue("the door slams shut", 0.75, shot=1),
        _cue("a gas tank explodes", 3.0),
        _cue("a cup smashes", 5.0, shot=3),
    ]
    trace = _trace(6.0, spikes={0.75: 9.0, 3.0: 12.0, 5.0: 8.0})

    plan = plan_impacts(cues, {**WINDOWS, 3: (4.0, 6.0)}, trace)

    assert len(plan.moments) == MAX_MOMENTS_PER_TAKE == 2
    assert [(m.time, m.style) for m in plan.moments] == [(0.75, "hit"), (3.0, "blast")]
    assert plan.skipped == ("a cup smashes (already 2 on this take)",)


# --- what --------------------------------------------------------------------------------------------


def test_the_blast_frame_flashes_at_0_9_and_the_hit_frame_punches_in() -> None:
    blast = ImpactMoment(1.0, 1.5, "blast", "boom")
    on = moment_move(blast, 1.0)
    assert on is not None and on.flash == pytest.approx(FLASH_PEAK) and on.zoom > 1.0
    later = moment_move(blast, 1.0 + FLASH_SECONDS)
    assert later is not None and later.flash == 0.0
    assert moment_move(blast, 0.99) is None and moment_move(blast, 1.5) is None
    hit = moment_move(ImpactMoment(1.0, 1.8, "hit", "slam"), 1.0)
    assert (
        hit is not None and hit.zoom == pytest.approx(PUNCH_ZOOM) and hit.flash == 0.0
    )


def test_the_shake_never_shows_the_frame_edge() -> None:
    w, h = 1080, 1920
    for moment in (
        ImpactMoment(0.0, 0.5, "blast", "b"),
        ImpactMoment(0.0, 0.8, "hit", "h"),
    ):
        for frame in range(int(moment.until * FPS)):
            move = moment_move(moment, frame / FPS)
            assert move is not None
            m = move.matrix(w, h)
            for x, y in ((0, 0), (w, 0), (0, h), (w, h)):
                sx, sy = m @ np.array([x, y, 1.0])
                assert -0.5 <= sx <= w + 0.5 and -0.5 <= sy <= h + 0.5


def test_an_impact_keeps_the_sound_timeline() -> None:
    assert "impact" in KEEPS_TIMELINE


# --- rendered, frame by frame ------------------------------------------------------------------------


def _take(path: Path, *, box_at: float, seconds: float = 3.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=gray:s=180x320:r={FPS}:d={seconds},"
               f"drawbox=x=60:y=110:w=60:h=60:color=white:t=fill:enable='gte(t,{box_at})'",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )  # fmt: skip
    return path


def _frames(path: Path) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 320, 180).astype(np.float64)


@needs_ffmpeg
def test_rendered_blast_and_hit_on_the_impact_frame(tmp_path: Path) -> None:
    take = _take(tmp_path / "t.mp4", box_at=1.0)
    hit_at = round(1.0 * FPS)
    before = _frames(take)

    blast = apply_impacts(
        take, ImpactPlan((ImpactMoment(1.0, 1.5, "blast", "boom"),)), tmp_path / "b.mp4"
    )
    after = _frames(blast.output)
    assert after.shape == before.shape
    assert before[hit_at].mean() < 150 and after[hit_at].mean() > 0.85 * 255
    for index in (hit_at - 1, hit_at + round(0.55 * FPS), len(before) - 1):
        assert np.abs(after[index] - before[index]).mean() < 2.0, index

    hit = apply_impacts(
        take, ImpactPlan((ImpactMoment(1.0, 1.8, "hit", "slam"),)), tmp_path / "h.mp4"
    )
    after = _frames(hit.output)
    assert (after[hit_at] > 200).sum() > 1.12 * (before[hit_at] > 200).sum(), (
        "the box is punched in"
    )
    assert np.abs(after[hit_at - 1] - before[hit_at - 1]).mean() < 2.0


# --- in finish ---------------------------------------------------------------------------------------


def _finish(desk: Path, **kw):
    return run_finish(desk, sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
                      bed_maker=fake_bed, facts_fetcher=lambda *a: None, cut_meter=lambda _take: (),
                      stream=io.StringIO(), **kw)  # fmt: skip


@needs_ffmpeg
def test_a_new_desk_punches_in_on_the_slam_where_the_door_moves(tmp_path: Path) -> None:
    desk = _desk(tmp_path, date(2026, 10, 6), box_at=3.5)
    result = _finish(desk)
    impact = next(s for s in result.steps if s.step == "impact")
    assert impact.status == "ran", impact.detail
    assert "a door slams: punch-in + shake at 3.50s" in impact.detail
    assert (
        impact.output is not None and impact.output.name == "take-ep01-t1-impact-v1.mp4"
    )
    mix = next(s for s in result.steps if s.step == "mix")
    assert mix.status == "ran"


@needs_ffmpeg
def test_no_motion_no_impact_and_the_switches(tmp_path: Path) -> None:
    still = _desk(tmp_path / "a", date(2026, 10, 6), box_at=None)
    impact = next(s for s in _finish(still).steps if s.step == "impact")
    assert impact.status == "skipped" and "no clear motion spike" in impact.detail

    off = _desk(tmp_path / "b", date(2026, 10, 6), box_at=3.5)
    impact = next(s for s in _finish(off, impact=False).steps if s.step == "impact")
    assert (impact.status, impact.detail) == ("skipped", "--no-impact")

    legacy = _desk(tmp_path / "c", date(2026, 10, 5), box_at=3.5)
    impact = next(s for s in _finish(legacy).steps if s.step == "impact")
    assert impact.status == "skipped" and "before 6 Oct 2026" in impact.detail
