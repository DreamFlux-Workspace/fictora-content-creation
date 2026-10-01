"""Option C: a take whose sound is the show's locked voices (take facts ``soundtrack.mode == "target_audio"``).

The server sends the take's lines, rendered in the locked voices, to the video
model as its audio; the take comes back with exactly that dialogue track:
voices in the line windows, digital silence between them, no ambience. finish
must then lay room tone, the bed ducked in each line window and the effects on
the measured cuts, time captions on the line windows, never touch the voices,
and STOP rather than ship a voice-only take. Older servers and native takes
finish exactly as before.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pytest
from conftest import make_take, needs_ffmpeg

from creation.captions import Span, time_lines
from creation.orchestrate import take_soundtrack_lines
from creation.post.finish import AMBIENCE_STEP, ROOM_TONE_STEP, run_finish
from creation.post.media import measure_rms_windows
from creation.post.review import soundtrack_section
from creation.post.sfx import filmed_shot_windows, planned_shots
from creation.post.soundtrack import (
    TARGET_AUDIO_CUT_WINDOW_SECONDS,
    soundtrack_from,
    soundtrack_lines,
    unheard_lines,
)
from test_post_finish import FACTS, TWO_LINES, fake_bed, fake_sfx

#: The dialogue track's lines, where TWO_LINES puts the voices (Kenji 1-2 s, Aya 3.2-4 s).
LINES = [
    {"line_id": "l1", "cast_id": "cast_kenji", "start_s": 1.0, "end_s": 2.0, "off_screen": False},
    {"line_id": "l2", "cast_id": "cast_aya", "start_s": 3.2, "end_s": 4.0, "off_screen": False},
]  # fmt: skip


def facts_with(soundtrack: dict | None) -> dict:
    """``FACTS`` (two shots, a door slam at 3.0 s in shot 2) with a ``soundtrack`` (``None``: an older server)."""

    facts = copy.deepcopy(FACTS)
    if soundtrack is not None:
        facts["take_facts"]["soundtrack"] = soundtrack
    return facts


TARGET = {"mode": "target_audio", "reason": None, "track_url": "https://x/track.wav",
          "lines": LINES, "native_foley": False}  # fmt: skip
NATIVE = {
    "mode": "native",
    "reason": "lane is r2v",
    "track_url": None,
    "lines": [],
    "native_foley": True,
}


def _desk_take(post_desk: Path, facts: dict) -> Path:
    raw = make_take(
        post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES
    )
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )
    return raw


def no_transcript(*_: object) -> Path:
    raise AssertionError(
        "a locked-voice take is captioned from its line windows, never transcribed"
    )


# --- reading the contract -------------------------------------------------------------------------------------


def test_the_soundtrack_mode_is_read_and_an_older_server_reads_as_native() -> None:
    locked = soundtrack_from(facts_with(TARGET))
    assert locked.target_audio and locked.sent
    assert [(line.line_id, line.start, line.end) for line in locked.lines] == [
        ("l1", 1.0, 2.0),
        ("l2", 3.2, 4.0),
    ]
    assert (
        "locked voices" in locked.one_line()
        and "no native ambience" in locked.one_line()
    )

    old = soundtrack_from(facts_with(None))
    assert not old.target_audio and not old.sent
    assert "older server" in old.one_line()

    native = soundtrack_from(facts_with(NATIVE))
    assert not native.target_audio and native.sent
    assert native.one_line() == "Soundtrack: native (reason: lane is r2v)"

    odd = soundtrack_from(facts_with({"mode": "dubbed", "lines": LINES}))
    assert not odd.target_audio and "unknown soundtrack mode" in odd.one_line()
    broken = soundtrack_from(
        facts_with(
            {
                "mode": "target_audio",
                "lines": [{"line_id": "x", "start_s": 2, "end_s": 1}],
            }
        )
    )
    assert broken.target_audio and broken.lines == ()


def test_the_take_review_film_step_and_take_facts_show_the_soundtrack() -> None:
    names = {"cast_kenji": "Kenji", "cast_aya": "Aya"}
    rows = soundtrack_lines(facts_with(TARGET), names)
    assert rows[0].startswith("Soundtrack: locked voices, 2 line(s)")
    assert rows[1:] == ["  l1 (Kenji) 1.00-2.00s", "  l2 (Aya) 3.20-4.00s"]
    section = soundtrack_section(facts_with(TARGET), names)
    assert section.name == "Soundtrack" and "do not revoice" in " ".join(
        section.details
    )
    assert take_soundtrack_lines("t1", facts_with(TARGET))[0].startswith(
        "t1 Soundtrack: locked voices"
    )
    assert take_soundtrack_lines("t1", facts_with(NATIVE)) == [
        "t1 Soundtrack: native (reason: lane is r2v)"
    ]
    assert take_soundtrack_lines("t1", facts_with(None)) == [], (
        "an older server: the film step prints nothing new"
    )


def test_a_locked_voice_take_snaps_a_cut_more_than_1_s_off_the_plan_and_prints_the_measured_cuts() -> (
    None
):
    shots = planned_shots(facts_with(TARGET))
    native = filmed_shot_windows(shots, (4.1,), duration=5.0)
    assert native.moved == () and native.kept == (2.5,), (
        "native takes keep today's 1 s snap"
    )
    assert "measured cuts" not in native.one_line()

    wide = filmed_shot_windows(
        shots,
        (4.1,),
        duration=5.0,
        window=TARGET_AUDIO_CUT_WINDOW_SECONDS,
        show_measured=True,
    )
    assert wide.moved == ((2.5, 4.1),)
    assert wide.one_line().startswith(
        "measured cuts: 4.10s; shot changes on the filmed cuts: 2.50->4.10s"
    )


def test_captions_are_timed_on_known_line_windows_without_detecting_speech() -> None:
    from creation.captions import CaptionLine

    lines = [CaptionLine("l1", "Wait for me here."), CaptionLine("l2", "Not tonight.")]

    def spans() -> list[Span]:
        raise AssertionError("known windows need no speech detection")

    timing = time_lines(
        lines, duration=5.0, spans=spans, known=[Span(1.0, 2.0), Span(3.2, 4.0)]
    )
    assert timing.anchors == (Span(1.0, 2.0), Span(3.2, 4.0))
    assert timing.methods == ("lines", "lines")


# --- finish on a locked-voice take -----------------------------------------------------------------------------


@needs_ffmpeg
def test_finish_lays_room_tone_bed_ducked_in_line_windows_effects_on_measured_cuts_and_line_captions(
    post_desk: Path,
) -> None:
    raw = _desk_take(post_desk, facts_with(TARGET))
    assert unheard_lines(raw, soundtrack_from(facts_with(TARGET))) == []
    out = io.StringIO()
    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
        transcriber=no_transcript, cut_meter=lambda _take: (3.9,), stream=out,
    )  # fmt: skip

    log = out.getvalue()
    assert result.complete, log
    assert [s.step for s in result.steps] == [
        "deboard", "sfx", AMBIENCE_STEP, ROOM_TONE_STEP, "bed", "colour", "mix", "captions", "watermark",
        "thumbnail",
    ]  # fmt: skip
    # The fixture spine names no location: no ambience cue, room tone is the fallback (test_target_audio_ambience).
    assert next(s for s in result.steps if s.step == AMBIENCE_STEP).status == "skipped"
    detail = {s.step: s.detail for s in result.steps}
    # (c) the shot change planned at 2.5 s follows the cut measured 1.4 s later, and the cuts are printed.
    assert "measured cuts: 3.90s" in detail["sfx"] and "2.50->3.90s" in detail["sfx"]
    assert "a door slams @4.12s" in detail["sfx"], (
        "the slam keeps its fraction of shot 2 as filmed"
    )
    assert (
        "effects duck under the 2 line window(s) of the dialogue track" in detail["sfx"]
    )
    # (b) the bed ducks exactly in the dialogue track's line windows.
    assert (
        "ducking 12 dB in 2 voice window(s) (the take's line windows)" in detail["mix"]
    )
    # (d) room tone: the gap between the lines is no longer digital silence; both lines heard where the facts say.
    assert (
        "2 of 2 line(s) have voice in their window (levels only); transcript check skipped"
        in detail[ROOM_TONE_STEP]
    ), "no transcript of this take on the desk: the count says levels only"
    toned = next(s.output for s in result.steps if s.step == ROOM_TONE_STEP)
    gap = measure_rms_windows(toned, window_seconds=0.1)[
        24:30
    ]  # 2.4-3.0 s, between the lines
    assert all(-70.0 < level < -40.0 for level in gap), gap
    assert min(measure_rms_windows(raw, window_seconds=0.1)[24:30]) < -100, (
        "the raw gap is digital silence"
    )
    # (e) captions from the line windows, no transcript.
    assert "1.00-" in detail["captions"] and "(lines)" in detail["captions"]
    assert "3.20-" in detail["captions"]
    # Shown in the summary.
    assert "Soundtrack: locked voices, 2 line(s)" in log
    assert "Sound: music ✓ · SFX ✓ · mix ✓ · room tone ✓ · captions ✓" in log
    record = json.loads(
        next(
            (post_desk / "ep01" / "takes").glob("take-ep01-t1-finish-v*.json")
        ).read_text()
    )
    assert record["duck_db"] == 12.0, (
        "join re-lays the bed at the depth this take was finished at"
    )
    assert Path(record["pre_bed"]).name.startswith("take-ep01-t1-room-tone-"), (
        "join's bed goes over the room tone"
    )


@needs_ffmpeg
def test_finish_stops_loudly_without_a_bed_and_never_ships_a_voice_only_take(
    post_desk: Path,
) -> None:
    _desk_take(post_desk, facts_with(TARGET))

    def no_bed(*_: object) -> Path:
        raise RuntimeError("no bed pinned and the server could not make one")

    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=no_bed, facts_fetcher=lambda *a: None,
                        cut_meter=lambda _take: (), stream=out)  # fmt: skip

    assert not result.complete and "music" in result.sound_missing
    assert "no music bed" in result.stopped
    assert [s.step for s in result.steps] == [
        "deboard", "sfx", AMBIENCE_STEP, ROOM_TONE_STEP, "bed",
    ]  # fmt: skip
    assert "!! STOPPED" in out.getvalue()
    assert not list((post_desk / "ep01" / "takes").glob("*-sokii-*.mp4")), (
        "nothing deliverable is written"
    )
    assert not list((post_desk / "ep01" / "takes").glob("*-mix-*.mp4"))
    assert "STOPPED" in (post_desk / "ep01" / "run-notes.md").read_text()
    assert result.as_json()["stopped"]


@needs_ffmpeg
def test_finish_never_mutes_or_replaces_the_locked_voices_without_the_flag(
    post_desk: Path,
) -> None:
    _desk_take(post_desk, facts_with(TARGET))
    for kwargs in ({"mutes": ((1.0, 2.0),)},):
        with pytest.raises(ValueError, match="target_audio.*--over-locked-voices"):
            run_finish(
                post_desk, facts_fetcher=lambda *a: None, stream=io.StringIO(), **kwargs
            )
    revoiced = make_take(
        post_desk / "ep01" / "takes" / "take-ep01-t1-revoice-v1.mp4", tones=TWO_LINES
    )
    with pytest.raises(ValueError, match="revoice / voice-fx file"):
        run_finish(
            post_desk,
            take_file=revoiced,
            facts_fetcher=lambda *a: None,
            stream=io.StringIO(),
        )
    assert sorted(p.name for p in (post_desk / "ep01" / "takes").glob("*.mp4")) == [
        "take-ep01-t1-raw-v1.mp4", "take-ep01-t1-revoice-v1.mp4",
    ], "refused before any step ran"  # fmt: skip


def test_revoice_refuses_a_locked_voice_take_before_anything_is_spent(
    post_desk: Path,
) -> None:
    from creation.post.voice import run_revoice

    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts_with(TARGET))
    )
    with pytest.raises(ValueError, match="revoice of ep01 t1 .*target_audio"):
        run_revoice(
            post_desk, cast="Kenji", take_file=Path("unused.mp4"), out=io.StringIO()
        )


# --- native takes and older servers: exactly as before --------------------------------------------------------


def _finish_report(post_desk: Path, facts: dict) -> list[tuple[str, str, str]]:
    for path in (post_desk / "ep01" / "takes").glob("*"):
        path.unlink()
    for path in (post_desk / "ep01" / "api").glob("take-facts-*"):
        path.unlink()
    _desk_take(post_desk, facts)
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
                        cut_meter=lambda _take: (3.9,), colour=False, stream=io.StringIO())  # fmt: skip
    assert result.complete and not result.locked_voices and not result.stopped
    # The second run finds the bed the first one made and pinned: the same file either way.
    return [(s.step, s.status, s.detail.replace("-v2", "-v1").replace("made bed", "pinned bed"))
            for s in result.steps]  # fmt: skip


@needs_ffmpeg
def test_native_and_older_server_takes_finish_exactly_as_before(
    post_desk: Path,
) -> None:
    old = _finish_report(post_desk, facts_with(None))
    native = _finish_report(post_desk, facts_with(NATIVE))

    assert native == old, (
        "a native take finishes exactly like one from a server that sends no soundtrack"
    )
    steps = {step: detail for step, _status, detail in old}
    assert list(steps) == [
        "deboard",
        "sfx",
        "bed",
        "colour",
        "mix",
        "captions",
        "watermark",
        "thumbnail",
    ]
    assert (
        "no cut within 1 s, kept as planned: 2.50s" in steps["sfx"]
        and "measured cuts" not in steps["sfx"]
    )
    assert "a door slams @3.00s" in steps["sfx"]
    assert "sidechain compressor" in steps["mix"]
    assert "(lines)" not in steps["captions"] and "(speech)" in steps["captions"]
