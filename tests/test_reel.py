"""reel: a social cut from an episode's rendered footage (planner pure; render on tiny synthetic clips)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from conftest import needs_ffmpeg

from creation.captions import Cue
from creation.post import reel as reel_module
from creation.post.finish_record import write_finish_record
from creation.post.reel import (
    cut_sound,
    parse_ass_cues,
    reel_paths,
    run_reel,
    write_new,
)
from creation.post.reel_plan import (
    BeatInput,
    ReelPlan,
    Segment,
    Shot,
    TakeInput,
    check_plan,
    frame_scores,
    beat_spans,
    plan_reel,
    post_text,
    retime_cues,
    viewer_address,
)

FPS = 24.0


def take(
    take_id: str = "t1",
    *,
    seconds: float = 15.0,
    cues: tuple[Cue, ...] = (),
    peak: tuple[float, float] | None = None,
    rows: int = 4,
    cuts: tuple[float, ...] = (),
) -> TakeInput:
    """A measured take: flat low motion, a motion peak in ``peak``, one shot per board row."""

    samples = tuple(round(i / 8, 4) for i in range(int(seconds * 8)))
    motion = tuple(
        (0.5 if peak and peak[0] <= t < peak[1] else 0.05) + 0.001 * (i % 3)
        for i, t in enumerate(samples)
    )
    contrast = tuple(0.2 for _ in samples)
    width = seconds / rows
    shots = tuple(
        Shot(i + 1, round(i * width, 3), round((i + 1) * width, 3), named=1)
        for i in range(rows)
    )
    return TakeInput(
        take_id=take_id, duration=seconds, fps=FPS, shots=shots, cuts=cuts, cues=cues,
        sample_seconds=samples, motion=motion, contrast=contrast,
    )  # fmt: skip


def three_beats(take_id: str = "t1", *, payoff: bool = True) -> list[BeatInput]:
    return [
        BeatInput(1, take_id, 1, 2, "the setup", lines=("plant line",)),
        BeatInput(2, take_id, 3, 3, "the turn", lines=("pivot line",)),
        BeatInput(3, take_id, 4, 4, "the reveal", payoff=payoff, lines=("new fact",)),
    ]


CUES = (
    Cue(0.5, 2.5, "plant line"),
    Cue(8.0, 9.5, "pivot line"),
    Cue(12.0, 13.0, "new fact"),
)


# --- the planner ------------------------------------------------------------------------------


def test_the_plan_opens_on_a_flash_forward_and_ends_on_the_new_fact() -> None:
    plan = plan_reel(2, [take(cues=CUES, peak=(12.0, 13.5))], three_beats())

    roles = [s.role for s in plan.segments]
    assert roles[0] == "cold_open"
    assert roles[-1] == "new_fact"
    assert plan.ends_on_new_fact()
    # The new fact stops on its peak: the action after its last caption runs out at 13.5 s; the tail is cut.
    assert plan.segments[-1].end == pytest.approx(13.5, abs=1 / FPS)
    assert not any(s.role == "new_fact" for s in plan.segments[:-1] if s.end > 13.6)
    assert "ends on the new fact" not in " ".join(plan.warnings)


def test_the_flash_forward_is_the_highest_scoring_window() -> None:
    measured = take(cues=CUES, peak=(9.75, 11.0))
    beats = three_beats(payoff=False)
    plan = plan_reel(2, [measured], beats)

    rows = frame_scores(measured, beat_spans(beats, {"t1": measured}))
    # The new fact is never the cold open: the best frame before it wins.
    best = max((r for r in rows if r[3] != "new_fact"), key=lambda r: r[1])
    cold = plan.segments[0]
    assert cold.role == "cold_open"
    assert plan.strongest is not None
    assert plan.strongest.at == pytest.approx(best[0])
    assert cold.start <= plan.strongest.at < cold.end
    assert 1.0 - 1e-6 <= cold.end - cold.start <= 2.0 + 1e-6
    # Its edges sit off words: never inside a caption.
    for edge in (cold.start, cold.end):
        assert not any(c.start + 0.02 < edge < c.end - 0.02 for c in CUES)


def test_story_outweighs_a_camera_pan_in_the_setup() -> None:
    """A big pan over the setup must not beat the pivot that moves a little (the new fact is never shown first)."""

    measured = take(cues=CUES, peak=(3.0, 5.0))
    plan = plan_reel(2, [measured], three_beats(payoff=True))
    assert plan.strongest is not None
    assert plan.strongest.role == "pivot"


@pytest.mark.parametrize("seconds", [15.0, 10.0, 8.0])
def test_the_plan_respects_seconds_on_a_long_episode(seconds: float) -> None:
    cues_1 = (Cue(0.5, 2.0, "a"), Cue(6.0, 7.0, "b"), Cue(11.0, 12.0, "c"))
    cues_2 = (Cue(1.0, 2.0, "d"), Cue(6.0, 7.5, "e"), Cue(12.0, 13.0, "f"))
    takes = [take("t1", cues=cues_1), take("t2", cues=cues_2, peak=(12.0, 13.0))]
    beats = [
        BeatInput(1, "t1", 1, 2, lines=("a",)),
        BeatInput(2, "t1", 3, 4, lines=("b",)),
        BeatInput(3, "t2", 1, 2, lines=("d",)),
        BeatInput(4, "t2", 3, 3, lines=("e",)),
        BeatInput(5, "t2", 4, 4, payoff=True, lines=("f",)),
    ]
    plan = plan_reel(1, takes, beats, seconds=seconds)

    assert plan.total <= seconds + 0.5
    if plan.total > seconds + 0.05:
        assert any("over --seconds" in w for w in plan.warnings)
    assert plan.ends_on_new_fact()
    assert (
        sum(1 for s in plan.segments if s.role == "pivot") <= 2
    )  # one pivot beat, maybe split
    assert {s.take for s in plan.segments if s.role == "pivot"} == {"t2"}


def test_seconds_outside_6_to_30_is_refused() -> None:
    with pytest.raises(ValueError, match="6-30"):
        plan_reel(1, [take()], three_beats(), seconds=40)


def test_a_line_said_to_the_viewer_never_goes_in() -> None:
    cues = (*CUES, Cue(13.2, 14.4, "What would you do? Comment below!"))
    plan = plan_reel(2, [take(cues=cues, peak=(13.0, 14.5))], three_beats())

    for seg in plan.segments:
        assert not (seg.start < 14.4 and 13.2 < seg.end), seg
    assert any("said to the viewer" in w for w in plan.warnings)


@pytest.mark.parametrize(
    ("text", "viewer"),
    [
        ("What would you do?", True),
        ("Comment below if you saw it", True),
        ("Follow for part 2", True),
        ("Couple?! What?!", False),
        ("Cleaner. Don't move.", False),
        ("Are you serious?", False),
    ],
)
def test_viewer_address(text: str, viewer: bool) -> None:
    assert viewer_address(text) is viewer


def test_a_hand_plan_that_does_not_end_on_the_new_fact_or_runs_long_is_warned_not_stopped() -> (
    None
):
    measured = take(cues=CUES)
    plan = ReelPlan(
        episode=1,
        seconds=10.0,
        segments=[
            Segment("t1", 12.0, 14.0, "new_fact"),
            Segment("t1", 0.0, 9.0, "plant"),
            Segment("t1", 8.5, 9.0, "pivot"),
        ],
    )
    warnings = check_plan(plan, [measured])
    assert any("does not end on the new fact" in w for w in warnings)
    assert any("over --seconds" in w for w in warnings)
    assert any("inside a word at 8.50" in w for w in warnings)


def test_segments_that_run_straight_on_are_not_a_cut_inside_a_word() -> None:
    measured = take(cues=(Cue(6.5, 8.0, "across the beat"),))
    plan = ReelPlan(
        1,
        15.0,
        [Segment("t1", 0.0, 7.0, "plant"), Segment("t1", 7.0, 15.0, "new_fact")],
    )
    assert not any("inside a word" in w for w in check_plan(plan, [measured]))


# --- captions on the reel's timeline ------------------------------------------------------------


def test_captions_are_retimed_through_the_segment_map() -> None:
    cues = {"t1": [Cue(1.0, 2.0, "first"), Cue(10.0, 11.0, "last")]}
    segments = [
        Segment("t1", 10.0, 11.5, "cold_open"),
        Segment("t1", 0.5, 3.0, "plant"),
    ]

    out = retime_cues(segments, cues)

    assert [(c.text, c.start, c.end) for c in out] == [
        ("last", 0.0, 1.0),
        ("first", 2.0, 3.0),
    ]


def test_a_caption_mostly_cut_away_is_dropped_and_a_flicker_cue_loses_the_words_not_played() -> (
    None
):
    cues = {
        "t1": [
            Cue(4.0, 4.5, "Two"),
            Cue(4.5, 5.5, "Two watch,"),
            Cue(5.5, 6.0, "Two watch, one"),
            Cue(8.0, 10.0, "a long whole line"),
        ]
    }
    # Starts on the onset of "one" (its earlier words are cut) and ends 0.3 s into the long line.
    out = retime_cues([Segment("t1", 5.5, 8.3, "plant")], cues)

    assert [c.text for c in out] == ["one"]
    assert out[0].start == 0.0


def test_contiguous_segments_keep_a_flicker_line_whole() -> None:
    cues = {
        "t1": [
            Cue(6.70, 6.88, "Two watch, one"),
            Cue(6.88, 8.31, "Two watch, one cleans."),
        ]
    }
    segments = [Segment("t1", 4.5, 7.08, "plant"), Segment("t1", 7.08, 8.5, "pivot")]

    out = retime_cues(segments, cues)

    assert [c.text for c in out] == ["Two watch, one", "Two watch, one cleans."]
    assert out[1].start == pytest.approx(out[0].end, abs=0.01)


def test_ass_cues_round_trip_with_italic() -> None:
    text = (
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.45,0:00:03.20,House,,0,0,0,,Couple?!\\NWhat?!\n"
        "Dialogue: 0,0:00:09.90,0:00:11.00,Italic,,0,0,0,,{\\i1}Cleaner.\n"
    )
    cues = parse_ass_cues(text)
    assert cues == [
        Cue(1.45, 3.2, "Couple?! What?!", False),
        Cue(9.9, 11.0, "Cleaner.", True),
    ]


# --- sound -------------------------------------------------------------------------------------


def test_cut_sound_keeps_the_length_and_crossfades_without_a_click() -> None:
    rate = reel_module.RATE
    a = np.full((rate * 4, 2), 0.5)
    b = np.full((rate * 4, 2), -0.5)
    runs = [("t1", 24, 48), ("t2", 48, 72)]  # 1 s of each, frames at 24 fps

    out = cut_sound({"t1": a, "t2": b}, runs, FPS)

    assert len(out) == 2 * rate
    step = np.abs(np.diff(out[:, 0]))
    assert step[100 : len(step) - 100].max() < 0.01  # a ramp, never a jump


# --- post text ---------------------------------------------------------------------------------


def test_post_text_carries_the_call_to_action_without_prices_or_model_names() -> None:
    text = post_text(
        series="SCP-173 Blink", episode=2, title="Blinking",
        question="Where will the statue be? Made with Kling for $0.30", genre="horror",
    )  # fmt: skip
    assert text.startswith("SCP-173 Blink · Part 2: Blinking")
    assert "Follow for part 3." in text
    assert "$" not in text and "Kling" not in text
    assert "#horror" in text


# --- never overwrite ---------------------------------------------------------------------------


def test_reel_paths_never_reuse_a_version_of_any_reel_file(tmp_path: Path) -> None:
    first = reel_paths(tmp_path, 6)
    assert first["video"].name == "reel-ep06-v1.mp4"
    write_new(first["plan"], "{}")  # a --plan-only run wrote the plan only

    second = reel_paths(tmp_path, 6)
    assert {p.name for p in second.values()} == {
        "reel-ep06-v2.mp4", "reel-ep06-v2.ass", "reel-plan-ep06-v2.json", "post-ep06-v2.txt",
    }  # fmt: skip
    with pytest.raises(FileExistsError):
        write_new(first["plan"], "{}")
    assert reel_paths(tmp_path, 7)["video"].name == "reel-ep07-v1.mp4"


# --- end to end on a tiny desk -----------------------------------------------------------------


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture
def reel_desk(tmp_path: Path) -> Path:
    """One finished 6 s take (4 shots of different greys, a tone), its captions, a bed and a spine."""

    desk = tmp_path / "desk"
    takes = desk / "ep01" / "takes"
    takes.mkdir(parents=True)
    (desk / "ep01" / "api").mkdir()
    (desk / "shared" / "beds").mkdir(parents=True)
    pieces = [
        f"color=c=0x{g:02x}{g:02x}{g:02x}:s=96x168:d=1.5:r=24"
        for g in (40, 90, 140, 200)
    ]
    inputs = [x for p in pieces for x in ("-f", "lavfi", "-i", p)]
    inputs += ["-f", "lavfi", "-i", "sine=f=440:d=6:sample_rate=48000"]
    _run([*inputs, "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]", "-map", "[v]",
          "-map", "4:a", "-af", "volume=0.3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
          str(takes / "take-ep01-t1-colour-v1.mp4")])  # fmt: skip
    (takes / "take-ep01-t1-cap-v1.ass").write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:00.30,0:00:01.20,House,,0,0,0,,Open the door\n"
        "Dialogue: 0,0:00:03.10,0:00:04.00,House,,0,0,0,,Who is there\n"
        "Dialogue: 0,0:00:04.60,0:00:05.30,House,,0,0,0,,It was me\n",
        encoding="utf-8",
    )
    _run(["-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.2:d=4:r=48000", "-c:a", "pcm_s16le",
          str(desk / "shared" / "beds" / "show-bed-v1.wav")])  # fmt: skip
    pre_bed = takes / "take-ep01-t1-colour-v1.mp4"
    write_finish_record(
        desk, episode=1, take_id="t1", complete=True, pre_bed=pre_bed,
        master=takes / "take-ep01-t1-cap-v1.mp4", final=takes / "take-ep01-t1-sokii-v1.mp4",
        bed=desk / "shared" / "beds" / "show-bed-v1.wav", bed_db=-16.5, duck_db=None,
    )  # fmt: skip
    spine = {
        "title": "Tiny Show",
        "premise_line": "The door was never locked.",
        "microdrama_genre": "mystery",
        "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1, "title": "The Door"}],
        "frames": [
            {"frame_id": f"f{i}", "episode_id": "episode_01", "board_row": i, "storyboard_group_id": "g1"}
            for i in range(1, 5)
        ],
        "beats": [
            {"beat_id": "b1", "episode_id": "episode_01", "ordinal": 1, "frame_id": "f1",
             "dialogue_lines": [{"text": "Open the door"}]},
            {"beat_id": "b2", "episode_id": "episode_01", "ordinal": 2, "frame_id": "f3",
             "dialogue_lines": [{"text": "Who is there"}]},
            {"beat_id": "b3", "episode_id": "episode_01", "ordinal": 3, "frame_id": "f4",
             "satisfaction_type": "mystery_reveal", "dialogue_lines": [{"text": "It was me"}]},
        ],
    }  # fmt: skip
    (desk / "ep01" / "api" / "spine.json").write_text(
        json.dumps(spine), encoding="utf-8"
    )
    facts = {
        "shots": [
            {
                "shot_index": i + 1,
                "start_seconds": i * 1.5,
                "end_seconds": (i + 1) * 1.5,
            }
            for i in range(4)
        ]
    }
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts), encoding="utf-8"
    )
    return desk


def _snapshot(desk: Path) -> dict[str, tuple[int, float]]:
    return {
        str(p.relative_to(desk)): (p.stat().st_size, p.stat().st_mtime)
        for p in desk.rglob("*")
        if p.is_file() and "reels" not in p.parts
    }


@needs_ffmpeg
def test_reel_plans_then_renders_only_new_files_under_reels(reel_desk: Path) -> None:
    before = _snapshot(reel_desk)

    planned = run_reel(reel_desk, episode=1, seconds=6.0, plan_only=True)
    assert planned.video is None
    assert planned.plan_path.name == "reel-plan-ep01-v1.json"
    body = json.loads(planned.plan_path.read_text(encoding="utf-8"))
    assert body["segments"][-1]["role"] == "new_fact"
    assert body["segments"][0]["role"] == "cold_open"

    result = run_reel(reel_desk, episode=1, plan_file=planned.plan_path)

    assert result.video is not None and result.video.name == "reel-ep01-v2.mp4"
    assert result.post is not None and "Follow for part 2." in result.post.read_text(
        encoding="utf-8"
    )
    # The show's premise line is the post's title (founder decision, 5 Oct 2026).
    assert result.post.read_text(encoding="utf-8").startswith(
        "The door was never locked.\nTiny Show"
    )
    assert result.ass is not None
    texts = [c.text for c in parse_ass_cues(result.ass.read_text(encoding="utf-8"))]
    assert texts[-1] == "It was me"
    assert result.seconds == pytest.approx(
        sum(s.end - s.start for s in result.plan.segments), abs=0.1
    )
    assert "LUFS" in result.loudness
    assert (
        _snapshot(reel_desk) == before
    )  # nothing outside reels/ was written or touched
    names = sorted(p.name for p in (reel_desk / "reels").iterdir())
    assert names == [
        "metrics.csv", "post-ep01-v2.txt", "reel-ep01-v2-cover-v1.jpg", "reel-ep01-v2.ass",
        "reel-ep01-v2.mp4", "reel-plan-ep01-v1.json", "reel-plan-ep01-v2.json",
    ]  # fmt: skip


@needs_ffmpeg
def test_review_of_a_reel_never_touches_run_notes_and_does_not_compare_take_cuts(
    reel_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.cli_produce import main

    notes = reel_desk / "ep01" / "run-notes.md"
    notes.write_text("# run notes\n", encoding="utf-8")
    result = run_reel(reel_desk, episode=1, seconds=6.0)
    assert result.video is not None
    capsys.readouterr()

    code = main(
        [
            "review",
            "--desk",
            str(reel_desk),
            "--episode",
            "1",
            "--take-file",
            str(result.video),
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert notes.read_text(encoding="utf-8") == "# run notes\n"
    saved = reel_desk / "reels" / f"{result.video.stem}-review-v1.txt"
    assert saved.is_file() and "Review ep01 t1" in saved.read_text(encoding="utf-8")
    assert "(finished," in out
    assert "not compared (the reel cut and reordered the take)" in out


@needs_ffmpeg
def test_reel_records_the_cold_open_and_ends_with_the_chosen_style(
    reel_desk: Path,
) -> None:
    result = run_reel(
        reel_desk, episode=1, seconds=6.0, ending="freeze-black", detector=None
    )

    body = json.loads(result.plan_path.read_text(encoding="utf-8"))
    assert body["cold_open"]["beat_role"] in {"pivot", "escalation", "plant"}
    assert body["cold_open"]["spoils_ending"] is False
    assert body["cold_open"]["face_source"] == "head_count"
    last = body["last_beat"]
    window = body["cold_open"]["window"]
    assert window[1] <= last["start_s"] + 1e-6, (
        "the cold open never shows the last beat"
    )
    assert set(body["opening"]) >= {"frame0_luma", "first_second_motion", "warnings"}
    assert body["ending"]["style"] == "freeze-black"
    assert result.video is not None
    assert result.seconds == pytest.approx(
        sum(s.end - s.start for s in result.plan.segments) + 0.7, abs=0.1
    )


@pytest.mark.parametrize(
    ("brief", "pov"),
    [
        ("POV: your ex shows up at your wedding", True),
        ("# Brief — Wedding\n\n## Premise\n\npov - you open the door", True),
        ("> **POV —** the landlord is at your door", True),
        ("POV of Mira: she sees the letter", False),
        ("A wedding goes wrong.\nPOV: the bride", False),
        ("", False),
    ],
)
def test_a_pov_episode_is_read_from_how_the_brief_opens(brief: str, pov: bool) -> None:
    from creation.post.reel_plan import brief_is_pov

    assert brief_is_pov(brief) is pov


def test_a_pov_episode_keeps_a_line_said_to_the_camera_but_still_cuts_a_call_to_action() -> (
    None
):
    assert viewer_address("What would you do?", pov=True) is False
    assert viewer_address("Did you see that?", pov=True) is False
    assert viewer_address("Comment below if you saw it", pov=True) is True
    assert viewer_address("Follow for part 2", pov=True) is True
    said = (*CUES, Cue(13.2, 14.4, "What would you do?"))
    plan = plan_reel(2, [take(cues=said, peak=(13.0, 14.5))], three_beats(), pov=True)
    assert not any("said to the viewer" in w for w in plan.warnings)
    assert any(seg.start < 14.4 and 13.2 < seg.end for seg in plan.segments), (
        "the line said to the camera stays in the reel"
    )
    cta = (*CUES, Cue(13.2, 14.4, "Comment below!"))
    plan = plan_reel(2, [take(cues=cta, peak=(13.0, 14.5))], three_beats(), pov=True)
    assert any("said to the viewer" in w for w in plan.warnings)
    for seg in plan.segments:
        assert not (seg.start < 14.4 and 13.2 < seg.end), seg
