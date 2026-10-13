"""An action's sound lands on the frame the action happens (L-20260930-10, L-20261030-10), new desks only."""

from __future__ import annotations

import io
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
from conftest import SPINE, make_take, make_tone, needs_ffmpeg

from creation.ops.floor import init_series_desk
from creation.production_state import ProductionState, save_production
from creation.post.finish import run_finish
from creation.post.sfx import SfxCue, plan_from_take_facts
from creation.post.sfx_motion import (
    MOTION_SHOT_PEAK_OVER_OTHERS,
    MOTION_SNAP_WINDOW_SECONDS,
    action_moment,
    measure_motion,
    names_an_action,
    names_several_hits,
    opening_hook_names_an_action,
    snap_to_action,
)
from test_post_finish import FACTS, TWO_LINES, fake_bed

FPS = 24


def _trace(
    seconds: float = 5.0, *, base: float = 0.6, spikes: dict[float, float] | None = None
):
    """A steady low motion trace with spikes at the given times."""

    out = []
    for frame in range(1, int(seconds * FPS)):
        t = round(frame / FPS, 3)
        value = base
        for at, height in (spikes or {}).items():
            if abs(t - at) < 0.5 / FPS:
                value = height
        out.append((t, value))
    return tuple(out)


# --- which cues follow the picture ------------------------------------------------------------------


def test_only_one_off_events_that_name_an_action_follow_the_picture() -> None:
    assert names_an_action("a door slams", "event")
    assert names_an_action("his fist punches the table", "event")
    assert names_an_action("a soft rustle", "event", "impact"), (
        "a stated impact always counts"
    )
    assert not names_an_action("a door slams", "sustained"), "a bed never moves"
    assert not names_an_action("a hushed crowd gasp", "event"), (
        "no visible action named"
    )
    assert not names_an_action("a sharp comic shock sting", "event")
    assert names_an_action("a door slams, on the first frame", "event"), (
        "a hook that names an action follows it"
    )


# --- the opening hook (L-20261030-10) ---------------------------------------------------------------


def test_an_opening_hook_that_names_a_physical_action_follows_it() -> None:
    assert opening_hook_names_an_action(
        "a golden crack spreads across the vase, on the first frame"
    )
    assert opening_hook_names_an_action("glass shatters on tile, on the first frame")


def test_a_sting_ambience_cut_in_or_genre_hook_stays_on_the_cut() -> None:
    assert not opening_hook_names_an_action(
        "a short low music sting, one hit, on the first frame"
    )
    assert not opening_hook_names_an_action(
        "a cinematic whoosh and a hit, on the first frame"
    )
    assert not opening_hook_names_an_action(
        "one held breath, close, then a door slams, cutting in hard by half a second, "
        "on the first frame"
    ), "a deliberate cut-in keeps its half second"
    # Picked by genre without looking at the picture: no drawer to follow.
    assert not opening_hook_names_an_action(
        "a file drawer slams shut, on the first frame"
    )
    assert not opening_hook_names_an_action(
        "a can cracks open and fizzes, on the first frame"
    )


def test_the_golden_crack_hook_lands_on_the_cracks_not_on_0s() -> None:
    hook = SfxCue(
        1,
        "a golden crack spreads across the vase, on the first frame",
        "event",
        0.0,
        2.0,
    )
    (moved,), _ = snap_to_action((hook,), {1: (0.0, 5.0)}, _trace(spikes={3.7: 14.0}))
    assert moved.start == pytest.approx(3.7, abs=0.03)


# --- finding the moment -----------------------------------------------------------------------------


def test_a_clear_spike_in_the_window_is_the_moment() -> None:
    peak = action_moment(_trace(spikes={3.5: 12.0}), around=3.0, shot=(2.5, 5.0))
    assert peak.time == pytest.approx(3.5, abs=0.03)


def test_no_spike_keeps_the_time() -> None:
    assert action_moment(_trace(), around=3.0, shot=(2.5, 5.0)).time is None


def test_one_clear_movement_anywhere_in_the_shot_takes_the_cue() -> None:
    """L-20261030-10: a cue more than 1 s from its action stayed put; one dominant movement now takes it."""

    far = 3.0 + MOTION_SNAP_WINDOW_SECONDS + 0.2
    peak = action_moment(_trace(spikes={far: 12.0}), around=3.0, shot=(2.5, 5.0))
    assert peak.time == pytest.approx(far, abs=0.03)
    assert peak.reason == "the shot's one clear movement"


def test_a_far_spike_that_does_not_dominate_the_shot_is_never_taken() -> None:
    far = 3.0 + MOTION_SNAP_WINDOW_SECONDS + 0.2
    # A second movement over half the far one's size, far from the cue as well: the cue keeps its time.
    two = _trace(
        7.0, spikes={far: 12.0, 5.8: 12.0 / MOTION_SHOT_PEAK_OVER_OTHERS + 1.0}
    )
    assert action_moment(two, around=3.0, shot=(2.5, 7.0)).time is None


def test_the_lid_grinds_from_where_the_lid_starts_moving() -> None:
    """L-20261030-10: "the lid grinds" at 9.2 s while the lid moves 5.25-7.0 s."""

    moving = {
        round(5.25 + step / FPS, 3): 8.0 + (step % 5) for step in range(int(1.75 * FPS))
    }
    lid = SfxCue(3, "the stone lid grinds open", "event", 9.2, 2.5)
    (moved,), snaps = snap_to_action(
        (lid,), {3: (4.0, 10.0)}, _trace(10.0, spikes=moving)
    )
    assert moved.start == pytest.approx(5.25, abs=0.06), snaps.one_line()


def test_one_hit_per_movement() -> None:
    slam = SfxCue(2, "a door slams", "event", 3.2, 1.0)
    thud = SfxCue(2, "a body thuds against the door", "event", 4.0, 1.0)
    out, snaps = snap_to_action(
        (slam, thud), {2: (2.5, 5.0)}, _trace(spikes={3.5: 12.0})
    )
    assert out[0].start == pytest.approx(3.5, abs=0.03), (
        "the cue placed nearest the spike"
    )
    assert out[1] == thud and "another cue is on this movement" in snaps.one_line()


def test_a_several_hit_clip_is_never_laid_on_one_movement() -> None:
    knocks = SfxCue(2, "someone knocks three times on the door", "event", 3.0, 1.0)
    (kept,), snaps = snap_to_action(
        (knocks,), {2: (2.5, 5.0)}, _trace(spikes={3.5: 12.0})
    )
    assert kept == knocks and "several hits, not one movement" in snaps.one_line()
    assert names_several_hits("footsteps on gravel") and not names_several_hits(
        "a door slams"
    )


def test_a_spike_outside_the_shot_or_on_a_cut_is_not_the_action() -> None:
    # Window reaches back to 2.2 s, but the shot starts at 2.5 s: the spike at 2.3 s is the previous shot.
    assert (
        action_moment(_trace(spikes={2.3: 12.0}), around=3.0, shot=(2.5, 5.0)).time
        is None
    )
    # A cut inside the shot the plan did not have.
    assert (
        action_moment(
            _trace(spikes={3.4: 30.0}), around=3.0, shot=(2.5, 5.0), cuts=(3.4,)
        ).time
        is None
    )


def test_two_spikes_alike_or_a_weak_one_keep_the_time() -> None:
    alike = _trace(spikes={2.8: 10.0, 3.6: 9.0})
    assert (
        action_moment(alike, around=3.2, shot=(2.5, 5.0)).reason
        == "two motion spikes alike"
    )
    weak = _trace(spikes={3.5: 1.8})
    assert (
        action_moment(weak, around=3.0, shot=(2.5, 5.0)).reason
        == "no clear motion spike"
    )


def test_snap_moves_action_cues_only_and_reports_both() -> None:
    cues = (
        SfxCue(2, "a door slams", "event", 3.0, 1.0),
        SfxCue(2, "a fridge hum", "sustained", 2.5, 2.5),
        SfxCue(2, "a punch lands", "event", 4.6, 0.5),
    )
    out, snaps = snap_to_action(cues, {2: (2.5, 5.0)}, _trace(spikes={3.5: 12.0}))
    assert [c.start for c in out] == [3.5, 2.5, 4.6]
    assert snaps.moved == (("a door slams", 3.0, 3.5),)
    line = snaps.one_line()
    assert "on the filmed action: a door slams 3.00->3.50s" in line
    assert "action cues kept their time: a punch lands 4.60s" in line


def test_take_facts_source_reaches_the_cue() -> None:
    plan = plan_from_take_facts({"sfx_cues": [{"shot_index": 1, "sound": "a rustle", "kind": "event",
                                               "start_seconds": 1.0, "duration_seconds": 1.0, "source": "impact"}]})  # fmt: skip
    assert plan.cues[0].source == "impact"
    assert plan_from_take_facts(FACTS).cues[0].source == "sound_line"


# --- on real pictures -------------------------------------------------------------------------------


def _with_box(take: Path, at: float, out: Path) -> Path:
    """The take with a white box that appears at ``at`` and stays (one frame of motion)."""

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(take), "-vf",
         f"drawbox=x=20:y=60:w=140:h=180:color=white:t=fill:enable='gte(t,{at})'",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)],
        check=True,
    )  # fmt: skip
    return out


@needs_ffmpeg
def test_measure_motion_sees_the_frame_the_box_appears(tmp_path: Path) -> None:
    take = _with_box(make_take(tmp_path / "flat.mp4"), 3.5, tmp_path / "box.mp4")
    trace = measure_motion(take)
    time, value = max(trace, key=lambda s: s[1])
    assert time == pytest.approx(3.5, abs=1 / FPS + 1e-3) and value > 10
    peak = action_moment(trace, around=3.0, shot=(2.5, 5.0))
    assert peak.time == pytest.approx(3.5, abs=1 / FPS + 1e-3)


def _desk(root: Path, day: date, box_at: float | None) -> Path:
    desk = init_series_desk(root, "Motion Test", band="15s", episode_count=1, day=day)
    save_production(desk, ProductionState(session_id="s", prompt="p", preset_id="x", preset_version="1",
                                          spine_id="spine_test"))  # fmt: skip
    api = desk / "ep01" / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "03_spine.json").write_text(json.dumps(SPINE), encoding="utf-8")
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    flat = make_take(root / "flat.mp4", tones=TWO_LINES)
    raw = takes / "take-ep01-t1-raw-v1.mp4"
    if box_at is None:
        raw.write_bytes(flat.read_bytes())
    else:
        _with_box(flat, box_at, raw)
    (api / "take-facts-ep01-t1-v1.json").write_text(json.dumps(FACTS))
    return desk


def _finish(desk: Path, **kw):
    return run_finish(desk, sfx_render=lambda cue, target: make_tone(target, seconds=cue.seconds, freq=300),
                      bed_maker=fake_bed, facts_fetcher=lambda *a: None, cut_meter=lambda _take: (),
                      stream=io.StringIO(), **kw)  # fmt: skip


@needs_ffmpeg
def test_a_new_desk_lays_the_slam_on_the_frame_the_door_moves(tmp_path: Path) -> None:
    desk = _desk(tmp_path, date(2026, 10, 6), box_at=3.5)
    sfx = next(s for s in _finish(desk).steps if s.step == "sfx")
    assert "a door slams @3.50s" in sfx.detail, sfx.detail
    assert "on the filmed action: a door slams 3.00->3.50s" in sfx.detail
    notes = (desk / "ep01" / "run-notes.md").read_text(encoding="utf-8")
    assert "on the filmed action: a door slams 3.00->3.50s" in notes


@needs_ffmpeg
def test_a_new_desk_with_no_motion_keeps_the_planned_time(tmp_path: Path) -> None:
    desk = _desk(tmp_path, date(2026, 10, 6), box_at=None)
    sfx = next(s for s in _finish(desk).steps if s.step == "sfx")
    assert "a door slams @3.00s" in sfx.detail
    assert (
        "action cues kept their time: a door slams 3.00s (no clear motion spike)"
        in sfx.detail
    )


@needs_ffmpeg
def test_a_far_action_alone_in_its_shot_takes_the_cue_on_a_take(tmp_path: Path) -> None:
    desk = _desk(
        tmp_path, date(2026, 10, 6), box_at=3.0 + MOTION_SNAP_WINDOW_SECONDS + 0.4
    )
    sfx = next(s for s in _finish(desk).steps if s.step == "sfx")
    assert "a door slams 3.00->4.4" in sfx.detail, sfx.detail


@needs_ffmpeg
def test_a_failed_trace_keeps_the_time_and_says_so(tmp_path: Path) -> None:
    desk = _desk(tmp_path, date(2026, 10, 6), box_at=3.5)

    def broken(_take: Path):
        raise RuntimeError("ffmpeg motion trace failed")

    sfx = next(s for s in _finish(desk, motion_meter=broken).steps if s.step == "sfx")
    assert "a door slams @3.00s" in sfx.detail
    assert (
        "action cues kept their time (motion not measured: ffmpeg motion trace failed)"
        in sfx.detail
    )


@needs_ffmpeg
def test_a_continuing_desk_lays_exactly_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desk = _desk(tmp_path, date(2026, 10, 5), box_at=3.5)
    traced: list[Path] = []
    commands: list[list[str]] = []
    real = subprocess.run

    def spy(args, *a, **k):
        if (
            isinstance(args, (list, tuple))
            and args
            and "ffmpeg" in Path(str(args[0])).name
        ):
            commands.append([str(x) for x in args])
        return real(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    sfx = next(
        s
        for s in _finish(desk, motion_meter=lambda t: traced.append(t) or ()).steps
        if s.step == "sfx"
    )
    assert traced == [], (
        "a desk made before 6 Oct 2026 never reads the picture for its effects"
    )
    assert "a door slams @3.00s" in sfx.detail and "action" not in sfx.detail, (
        sfx.detail
    )
    lay = next(
        c
        for c in commands
        if any("amix" in x for x in c) and any(x.endswith("-sfx-v1.mp4") for x in c)
    )
    assert any("adelay=3000|3000" in x for x in lay)
    assert not any("scale=64:112" in " ".join(c) for c in commands)
