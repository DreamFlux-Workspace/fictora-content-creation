"""join fixes a loud seam itself before refusing (desks created on/after 6 Oct 2026; real ffmpeg, tiny clips).

The takes carry their music in their own sound (``music_in_take``), so the joined
master is the takes' own sound, gained and limited: the seam is exactly the
step between a steady room on one side and a near-silent take on the other.
"""

from __future__ import annotations

import functools
import io
import json
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from conftest import needs_ffmpeg

from creation import cli_post
from creation.cli_produce import main
from creation.ops.floor import init_series_desk
from creation.post import join as join_module
from creation.post import seam_fix
from creation.post.finish_record import write_finish_record
from creation.post.join import JOIN_NOT_DONE, decode_stereo, run_join
from creation.post.watermark import watermark
from creation.rules_epoch import is_legacy, run_rules_epoch

SIZE = (192, 336)


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def take_clip(path: Path, *, seconds: float, level: float, silent_head: float = 0.0,
              cut_at: float | None = None, seed: int = 1) -> Path:  # fmt: skip
    """Grey picture (a hard cut to light grey at ``cut_at``); steady pink noise at ``level`` after ``silent_head``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        "null"
        if cut_at is None
        else f"drawbox=x=0:y=0:w=iw:h=ih:color=0xc8c8c8:t=fill:enable='gte(t,{cut_at})'"
    )
    af = f"volume={level}" + (
        f",volume=0:enable='lt(t,{silent_head})'" if silent_head else ""
    )
    _run(["-f", "lavfi", "-i", f"color=c=0x282828:s={SIZE[0]}x{SIZE[1]}:d={seconds}:r=24",
          "-f", "lavfi", "-i", f"anoisesrc=c=pink:a=1:d={seconds}:r=48000:seed={seed}",
          "-vf", vf, "-af", af, "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", str(path)])  # fmt: skip
    return path


def finished(desk: Path, take_id: str, **clip: float) -> None:
    """A finished take whose music is in its own sound (no bed across the join)."""

    takes = desk / "ep01" / "takes"
    base = f"take-ep01-{take_id}"
    pre_bed = take_clip(takes / f"{base}-colour-v1.mp4", **clip)  # type: ignore[arg-type]
    master = take_clip(takes / f"{base}-cap-v1.mp4", **clip)  # type: ignore[arg-type]
    final = watermark(master, takes / f"{base}-sokii-v1.mp4")
    write_finish_record(desk, episode=1, take_id=take_id, complete=True, pre_bed=pre_bed, master=master,
                        final=final, bed=None, bed_db=-16.5, duck_db=None, music_in_take=True)  # fmt: skip


def ambience(desk: Path, *, level: float = 0.05, seconds: float = 6.0) -> Path:
    """The episode's location ambience on the desk (what ``finish`` saves for locked-voice takes)."""

    folder = desk / "ep01" / "ambience"
    folder.mkdir(parents=True, exist_ok=True)
    cue = folder / "ambience-rain.wav"
    _run(["-f", "lavfi", "-i", f"anoisesrc=c=brown:a=1:d={seconds}:r=48000:seed=7", "-af",
          f"volume={level}", "-c:a", "pcm_s16le", str(cue)])  # fmt: skip
    (folder / "ambience-ep01.json").write_text(
        json.dumps({"description": "steady rain", "location": "street", "file": cue.name, "seconds": seconds})
    )  # fmt: skip
    return cue


@pytest.fixture
def desk(tmp_path: Path) -> Path:
    """A new desk (created today: the current rules)."""

    return init_series_desk(tmp_path, "Seam Fix", band="30s", episode_count=1)


@pytest.fixture
def legacy_desk(tmp_path: Path) -> Path:
    """A desk created before 6 Oct 2026."""

    old = init_series_desk(
        tmp_path / "old", "Seam Fix", band="30s", episode_count=1, day=date(2026, 10, 1)
    )
    run_rules_epoch(old, set_to="legacy", out=io.StringIO())
    assert is_legacy(old)
    return old


def _join(desk: Path, **kwargs: object):
    out = io.StringIO()
    result = run_join(desk, episodes=(1,), gain_match=False, stream=out, **kwargs)
    return result, out.getvalue()


def _sound_outside(master: Path, spans: list[tuple[float, float]]) -> np.ndarray:
    samples = decode_stereo(master)
    keep = np.ones(len(samples), dtype=bool)
    for a, b in spans:
        keep[int(a * 48000) : int(b * 48000)] = False
    return samples[keep]


# --- pure helpers ------------------------------------------------------------------------------


def test_bed_gain_fills_the_gap_to_3_db_under_the_loud_side_and_never_past_the_ceiling() -> (
    None
):
    gain, target = seam_fix.bed_gain(-60.0, -30.0, -40.0)
    assert target == -33.0
    assert abs((-40.0 + gain) - (-33.0)) < 0.1, (
        "a near-silent side: the bed is the target level"
    )
    assert seam_fix.bed_gain(-31.0, -30.0, -40.0) is None, (
        "already within 3 dB: nothing to lay"
    )
    gain, _ = seam_fix.bed_gain(-60.0, -10.0, -40.0)
    assert -40.0 + gain <= seam_fix.SEAM_BED_MAX_DB + 1e-9, (
        "never louder than the ceiling"
    )
    gain, _ = seam_fix.bed_gain(-120.0, -30.0, -80.0)
    assert gain <= seam_fix.SEAM_BED_MAX_BOOST_DB


def test_a_bed_only_touches_its_window_on_the_quiet_side() -> None:
    sound = np.zeros((48000 * 8, 2))
    cue = seam_fix.SteadyCue(seam_fix.room_tone(4.0), "test")
    after = seam_fix.SeamBed(0, 4.0, True, cue, gain_db=10.0)
    laid = seam_fix.lay_beds(sound, [after])
    touched = np.flatnonzero(np.abs(laid).max(axis=1) > 0)
    assert touched[0] >= 4 * 48000 and touched[-1] < (4 + 2.5) * 48000
    assert after.span == (4.0, 6.5)
    before = seam_fix.SeamBed(0, 4.0, False, cue, gain_db=10.0)
    laid = seam_fix.lay_beds(sound, [before])
    touched = np.flatnonzero(np.abs(laid).max(axis=1) > 0)
    assert touched[0] >= 1.5 * 48000 and touched[-1] < 4 * 48000
    # Short ramps, never a pop: the first sample at the cut is near zero.
    assert abs(seam_fix.lay_beds(sound, [after])[4 * 48000, 0]) < 1e-3


def test_steady_cues_prefer_this_episode_then_the_series(tmp_path: Path) -> None:
    desk = tmp_path
    for episode in (1, 2):
        folder = desk / f"ep{episode:02d}" / "ambience"
        folder.mkdir(parents=True)
        (folder / f"amb-{episode}.mp3").write_bytes(b"x")
        (folder / f"ambience-ep{episode:02d}.json").write_text(
            json.dumps({"description": "d", "file": f"amb-{episode}.mp3", "seconds": 5})
        )
    sfx = desk / "ep02" / "sfx"
    sfx.mkdir()
    (sfx / "cue-rain-v1.mp3").write_bytes(b"x")
    (sfx / "cue-rain-v1.json").write_text(json.dumps({"kind": "sustained"}))
    (sfx / "cue-door-slam-v1.mp3").write_bytes(b"x")
    (sfx / "cue-door-slam-v1.json").write_text(json.dumps({"kind": "event"}))
    (desk / "sfx").mkdir()
    (desk / "sfx" / "rain-street.mp3").write_bytes(b"x")
    (desk / "sfx" / "knife-board.mp3").write_bytes(b"x")

    found = [(p.name, why) for p, why in seam_fix.steady_cue_files(desk, [2])]

    assert [name for name, _ in found] == [
        "amb-2.mp3",
        "cue-rain-v1.mp3",
        "amb-1.mp3",
        "rain-street.mp3",
    ]
    assert found[0][1].startswith("this episode's (ep02) location ambience")
    assert found[2][1].startswith("the series' (ep01)")


def test_a_silent_head_is_trimmed_on_the_last_filmed_cut_inside_it_and_never_into_speech() -> (
    None
):
    sound = np.zeros((48000 * 5, 2))
    sound[int(1.5 * 48000) :] = 0.05
    plan = seam_fix.plan_silent_trim(
        part_index=1,
        label="ep01 t2",
        sound=sound,
        seconds=5.0,
        speech=[],
        cuts=[0.5, 1.5, 3.0],
        head=True,
    )
    assert isinstance(plan, seam_fix.Trim) and plan.frame == 36 and plan.seconds == 1.5
    spoken = seam_fix.plan_silent_trim(
        part_index=1, label="ep01 t2", sound=sound, seconds=5.0, speech=[(1.0, 2.0)],
        cuts=[0.5, 1.5], head=True,
    )  # fmt: skip
    assert isinstance(spoken, seam_fix.Trim) and spoken.seconds == 0.5, (
        "the cut stays clear of the line"
    )
    none = seam_fix.plan_silent_trim(
        part_index=1,
        label="ep01 t2",
        sound=sound,
        seconds=5.0,
        speech=[],
        cuts=[3.0],
        head=True,
    )
    assert isinstance(none, str) and "no filmed cut" in none
    tail = np.zeros((48000 * 5, 2))
    tail[: int(3.5 * 48000)] = 0.05
    plan = seam_fix.plan_silent_trim(
        part_index=0,
        label="ep01 t1",
        sound=tail,
        seconds=5.0,
        speech=[],
        cuts=[1.0, 3.5, 4.5],
        head=False,
    )
    assert (
        isinstance(plan, seam_fix.Trim)
        and not plan.head
        and plan.frame == 84
        and plan.seconds == 1.5
    )


# --- join ---------------------------------------------------------------------------------------


@needs_ffmpeg
def test_a_quiet_then_loud_seam_passes_after_a_steady_bed_with_the_step_reported(
    desk: Path,
) -> None:
    finished(desk, "t1", seconds=4.0, level=0.05)
    finished(desk, "t2", seconds=4.0, level=0.001, seed=2)
    ambience(desk)

    result, printed = _join(desk)

    fix = result.seam_fix
    assert fix is not None and fix.fixed, printed
    assert fix.before[0] < -10, fix.before
    assert result.complete and result.marked is not None
    assert abs(result.seam_steps_db[0]) <= 5 and abs(result.seam_steps_db[0]) < abs(
        fix.before[0]
    )
    assert "this episode's (ep01) location ambience `ambience-rain.wav`" in fix.laid[0]
    assert "quiet side (after the cut)" in fix.laid[0] and not fix.trimmed
    assert f"{fix.before[0]:+.1f} -> {result.seam_steps_db[0]:+.1f} dB" in printed
    notes = (desk / "ep01" / "run-notes.md").read_text()
    assert "SEAM FIXED by join" in notes and "ambience-rain.wav" in notes
    report = result.as_json()
    assert report["seam_fix"]["fixed"] and report["seam_fix"]["laid"]
    # Nothing away from the seam window changed: the join mixed again from the same sound at the same
    # gain, the bed added before the one limiter. Before the window the samples are the same ones.
    plain, _ = _join(desk, seam_fix=False)
    head = int(3.85 * 48000)
    assert np.array_equal(
        decode_stereo(result.master)[:head], decode_stereo(plain.master)[:head]
    )
    away = [(3.9, 6.6)]
    a, b = _sound_outside(result.master, away), _sound_outside(plain.master, away)
    n = min(len(a), len(b))
    diff = a[:n] - b[:n]
    assert 10 * np.log10(np.mean(diff**2) / np.mean(b[:n] ** 2)) < -30, (
        "the sound away from the seam is the join as it was"
    )


@needs_ffmpeg
def test_a_silent_head_is_trimmed_on_a_filmed_cut_and_the_seam_passes(
    desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    finished(desk, "t1", seconds=4.0, level=0.05)
    finished(desk, "t2", seconds=4.0, level=0.05, silent_head=1.5, cut_at=1.5, seed=2)
    # A bed this quiet cannot close the seam (Fated ep 3: louder rain got it to -6.1): the trim must.
    monkeypatch.setattr(seam_fix, "SEAM_BED_MAX_DB", -90.0)

    result, printed = _join(desk)

    fix = result.seam_fix
    assert fix is not None and fix.fixed, printed
    assert result.complete and abs(result.seam_steps_db[0]) <= 5
    assert len(fix.trimmed) == 1 and "ep01 t2's silent head" in fix.trimmed[0], (
        fix.trimmed
    )
    assert "frame 36" in fix.trimmed[0]
    assert abs(join_module.count_frames(result.master) / 24 - 6.5) < 0.05, (
        "1.5 s of silent head is gone"
    )
    assert result.seams == [4.0]


@needs_ffmpeg
def test_a_seam_that_cannot_be_fixed_still_refuses_and_lists_what_was_tried(
    desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    finished(desk, "t1", seconds=4.0, level=0.05)
    finished(
        desk, "t2", seconds=4.0, level=0.05, silent_head=1.5, seed=2
    )  # no filmed cut to trim on
    monkeypatch.setattr(seam_fix, "SEAM_BED_MAX_DB", -90.0)
    monkeypatch.setattr(
        cli_post, "run_join", functools.partial(run_join, stream=io.StringIO())
    )

    code = main(
        ["join", "--desk", str(desk), "--episode", "1", "--no-gain-match", "--json"]
    )

    report = json.loads(capsys.readouterr().out)
    assert (
        code == JOIN_NOT_DONE
        and report["complete"] is False
        and report["marked"] is None
    )
    fix = report["seam_fix"]
    assert fix["fixed"] is False and not fix["laid"] and not fix["trimmed"]
    tried = " | ".join(fix["tried"])
    assert "no filmed cut inside it" in tried, tried
    assert "room tone" in tried or "laid" in tried or "not laid" in tried, tried
    assert abs(report["seams"][0]["step_db"]) > 5, "the master as first joined is kept"
    notes = (desk / "ep01" / "run-notes.md").read_text()
    assert "SEAM FIX TRIED" in notes and "STOPPED: seam at 4.00s" in notes
    assert not list((desk / "ep01" / "takes").glob("episode-ep01-join-sokii-*.mp4"))


@needs_ffmpeg
def test_a_legacy_desk_and_no_seam_fix_measure_and_refuse_exactly_as_before(
    desk: Path,
    legacy_desk: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for each in (desk, legacy_desk):
        finished(each, "t1", seconds=4.0, level=0.05)
        finished(each, "t2", seconds=4.0, level=0.001, seed=2)
        ambience(each)
    called: list[str] = []

    def no_fix(*args: object, **kwargs: object) -> None:
        called.append("fix")
        raise AssertionError(
            "no seam fix on a legacy desk or with --no-seam-fix / --accept-seam"
        )

    monkeypatch.setattr(join_module, "_fix_seams", no_fix)
    monkeypatch.setattr(
        cli_post, "run_join", functools.partial(run_join, stream=io.StringIO())
    )

    old, _ = _join(legacy_desk)
    off, _ = _join(desk, seam_fix=False)
    accepted, _ = _join(
        desk, accept_seam="the room changes on purpose", accepted_by="T"
    )
    code = main(
        [
            "join",
            "--desk",
            str(desk),
            "--episode",
            "1",
            "--no-gain-match",
            "--no-seam-fix",
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert called == []
    for result in (old, off):
        assert not result.complete and result.marked is None and result.seam_fix is None
        assert abs(result.seam_steps_db[0]) > 10
        assert "seam_fix" not in result.as_json()
        assert not any("SEAM FIX" in line for line in result.summary_lines())
    assert old.seam_steps_db == off.seam_steps_db
    a, b = decode_stereo(old.master), decode_stereo(off.master)
    assert a.shape == b.shape and np.array_equal(a, b), (
        "the legacy join is the join as it always was"
    )
    assert (
        accepted.complete
        and accepted.accepted is not None
        and accepted.seam_fix is None
    )
    assert code == JOIN_NOT_DONE and "seam_fix" not in report


def _rgb(video: Path, index: int) -> tuple[int, int, int]:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{index}),scale=1:1",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    return raw[0], raw[1], raw[2]


@needs_ffmpeg
def test_a_seam_fixed_join_still_gets_its_cover_on_the_first_frame(
    desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    finished(desk, "t1", seconds=4.0, level=0.05)
    finished(desk, "t2", seconds=4.0, level=0.05, silent_head=1.5, cut_at=1.5, seed=2)
    monkeypatch.setattr(
        seam_fix, "SEAM_BED_MAX_DB", -90.0
    )  # the trim (a full re-join) does the work
    covers = desk / "reels" / "ep01"
    covers.mkdir(parents=True)
    _run(["-f", "lavfi", "-i", "color=c=red:s=1080x1920", "-frames:v", "1",
          str(covers / "reel-ep01-v1-cover-v1.jpg")])  # fmt: skip

    result, printed = _join(desk)

    assert (
        result.seam_fix is not None
        and result.seam_fix.fixed
        and result.seam_fix.trimmed
    ), printed
    assert result.complete and result.marked is not None
    red, _, blue = _rgb(result.marked, 0)
    assert red > 200 and blue < 60, "the cover is the seam-fixed join's first frame"
    red, green, blue = _rgb(result.marked, 1)
    assert red < 120 and green < 120, "only frame 0 is the cover"
    assert any(
        "cover frame: reel-ep01-v1-cover-v1.jpg" in note for note in result.notes
    ), result.notes
    assert join_module.count_frames(result.marked) == join_module.count_frames(
        result.master
    )
