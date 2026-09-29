"""Hand-placed sound: finish --mute / --voice / --cue, and the cue / voice-line commands.

Real ffmpeg on tiny synthetic takes and tones; the Drama API and its audio routes are faked (no network).
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    SPINE,
    board_array,
    make_take,
    make_tone,
    needs_ffmpeg,
    shot_frames,
    write_frames,
)
from PIL import Image

from creation.cli_produce import main
from creation.ops.floor import approve_board
from creation.ops.state import load_series
from creation.post import finish as finish_mod
from creation.post import handmade
from creation.post.finish import run_finish
from creation.post.hand import (
    HandPlan,
    Placed,
    check_hand_plan,
    lay_cues,
    lay_voice,
    parse_placed,
    parse_range,
    silent_cues,
)
from creation.post.media import measure_rms_windows, media_duration
from creation.post.sfx import SfxCue
from creation.post.voice import _unit as revoice_key

WINDOW = 0.1
#: Kenji at 1-2 s, Aya at 3.2-4 s, as in the finish tests.
TWO_LINES = ((1.0, 2.0, 440), (3.2, 4.0, 880))
FACTS = {
    "take_facts": {
        "job_id": "job_video_scene_1",
        "shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5, "speaks": True},
            {
                "shot_index": 2,
                "start_seconds": 2.5,
                "end_seconds": 5.0,
                "speaks": False,
            },
        ],
        "sfx_cues": [
            {
                "shot_index": 2,
                "sound": "a door slams",
                "kind": "event",
                "start_seconds": 3.0,
                "duration_seconds": 1.0,
            }
        ],
    }
}


def rms(
    path: Path, start: float, end: float
) -> float:  # an ffmpeg test sine plays at about -27 dB
    """Loudest 0.1 s window between ``start`` and ``end`` (dB)."""

    levels = measure_rms_windows(path, window_seconds=WINDOW)
    return max(levels[round(start / WINDOW) : round(end / WINDOW)])


def quiet_take(path: Path) -> Path:
    """5 s take whose own sound is one short low tone at 0.2-0.4 s, silence elsewhere."""

    return make_take(path, tones=((0.2, 0.4, 150),))


# ================================================================================================
# parsing and the up-front checks
# ================================================================================================


def test_specs_parse_and_bad_ones_say_the_form() -> None:
    assert parse_range("6.9-8.3") == (6.9, 8.3)
    assert parse_placed("sfx/cue-sting-v1.mp3@4.2@-12", flag="--cue") == Placed(
        Path("sfx/cue-sting-v1.mp3"), 4.2, -12.0
    )
    assert parse_placed("line.mp3@5.2", flag="--voice").gain_db is None
    for bad in ("8.3-6.9", "6.9", "a-b"):
        with pytest.raises(ValueError, match="A-B"):
            parse_range(bad)
    for bad in ("line.mp3", "line.mp3@x", "line.mp3@1@2@3", "line.mp3@1@-90"):
        with pytest.raises(ValueError, match="--voice"):
            parse_placed(bad, flag="--voice")


@needs_ffmpeg
def test_a_silent_cue_or_voice_fails_loud_before_anything_runs(tmp_path: Path) -> None:
    silent = make_tone(tmp_path / "silent.wav", seconds=1.0, volume=0.0)
    with pytest.raises(ValueError, match="silent"):
        check_hand_plan(5.0, cues=(Placed(silent, 1.0),))
    with pytest.raises(ValueError, match="silent"):
        check_hand_plan(5.0, voices=(Placed(silent, 1.0),))
    with pytest.raises(FileNotFoundError):
        check_hand_plan(5.0, cues=(Placed(tmp_path / "missing.mp3", 1.0),))


@needs_ffmpeg
def test_placements_outside_the_take_are_refused(tmp_path: Path) -> None:
    tone = make_tone(tmp_path / "tone.wav", seconds=1.0, volume=0.5)
    with pytest.raises(ValueError, match="outside"):
        check_hand_plan(5.0, cues=(Placed(tone, 5.2),))
    with pytest.raises(ValueError, match="no room"):
        check_hand_plan(5.0, cues=(Placed(tone, 4.7),))
    with pytest.raises(ValueError, match="runs past the end"):
        check_hand_plan(5.0, voices=(Placed(tone, 4.5),))
    with pytest.raises(ValueError, match="past the end"):
        check_hand_plan(5.0, mutes=((5.5, 6.0),))
    assert check_hand_plan(5.0, mutes=((4.0, 9.0),)).mutes == ((4.0, 5.0),), (
        "a mute is clamped to the take"
    )


# ================================================================================================
# mute, voice, cue on a take
# ================================================================================================


@needs_ffmpeg
def test_the_mute_window_is_silent_and_the_take_outside_it_is_not(
    tmp_path: Path,
) -> None:
    take = make_take(tmp_path / "take.mp4", tones=((0.0, 5.0, 440),))
    plan = check_hand_plan(5.0, mutes=((2.0, 3.0),))

    out = lay_voice(take, tmp_path / "voice.mp4", plan)

    assert rms(out, 2.0, 3.0) < -80, "inside the mute window the take is silent"
    assert rms(out, 1.0, 1.9) > -35 and rms(out, 3.1, 4.0) > -35, (
        "outside it the take plays on"
    )
    assert abs(media_duration(out) - media_duration(take)) < 0.05


@needs_ffmpeg
def test_a_voice_line_lands_at_its_time(tmp_path: Path) -> None:
    take = quiet_take(tmp_path / "take.mp4")
    line = make_tone(tmp_path / "line.wav", seconds=0.5, freq=660, volume=0.3)
    plan = check_hand_plan(5.0, voices=(Placed(line, 3.0),))
    assert plan.voice_windows == ((3.0, 3.5),)

    out = lay_voice(take, tmp_path / "voice.mp4", plan)

    assert rms(out, 3.0, 3.5) > -30, "the line plays at 3.0 s"
    assert rms(out, 1.0, 2.9) < -60 and rms(out, 3.7, 4.8) < -60, "and nowhere else"
    with pytest.raises(FileExistsError):
        lay_voice(take, out, plan)


@needs_ffmpeg
def test_a_cue_lands_at_its_time_at_the_house_level(tmp_path: Path) -> None:
    take = quiet_take(tmp_path / "take.mp4")
    cue = make_tone(tmp_path / "cue.wav", seconds=0.6, freq=300, volume=0.5)
    plan = check_hand_plan(5.0, cues=(Placed(cue, 1.5),))

    out = lay_cues(take, tmp_path / "cues.mp4", plan)

    at = rms(out, 1.6, 1.9)
    assert rms(out, 0.8, 1.4) < -60 and rms(out, 2.3, 4.8) < -60, (
        "the cue is only where it was placed"
    )
    raw = max(measure_rms_windows(cue, window_seconds=WINDOW))
    assert -10 < at - raw < -6, f"house level is -8 dB under the file: {at - raw:.1f}"


@needs_ffmpeg
def test_a_cue_drops_10_db_while_someone_speaks(tmp_path: Path) -> None:
    take = quiet_take(tmp_path / "take.mp4")
    cue = make_tone(tmp_path / "cue.wav", seconds=2.0, freq=300, volume=0.5)
    plan = check_hand_plan(5.0, cues=(Placed(cue, 1.0),))

    out = lay_cues(take, tmp_path / "cues.mp4", plan, speech=((2.0, 4.0),))

    assert rms(out, 1.2, 1.9) - rms(out, 2.1, 2.6) == pytest.approx(10.0, abs=1.5)


@needs_ffmpeg
def test_a_cue_is_clamped_to_end_before_the_take_does(tmp_path: Path) -> None:
    take = quiet_take(tmp_path / "take.mp4")
    cue = make_tone(tmp_path / "long.wav", seconds=3.0, freq=300, volume=0.5)
    plan = check_hand_plan(5.0, cues=(Placed(cue, 4.0),))
    assert plan.cues[0][1] == pytest.approx(0.85), (
        "a 3 s cue at 4.0 s on a 5 s take is laid for 0.85 s"
    )

    out = lay_cues(take, tmp_path / "cues.mp4", plan)

    assert rms(out, 4.1, 4.5) > -40
    assert abs(media_duration(out) - media_duration(take)) < 0.05, (
        "the cue never makes the take longer"
    )


@needs_ffmpeg
def test_a_cue_that_comes_out_silent_in_the_layer_fails_loud(tmp_path: Path) -> None:
    layer = make_tone(tmp_path / "layer.wav", seconds=5.0, volume=0.0)
    cue = Placed(tmp_path / "cue.wav", 1.0)
    assert silent_cues(layer, HandPlan(cues=((cue, 0.5),))) == [
        "cue.wav @1.00s auto came out SILENT in the cue layer"
    ]
    loud = make_tone(tmp_path / "loud.wav", seconds=5.0, volume=0.5)
    assert silent_cues(loud, HandPlan(cues=((cue, 0.5),))) == []


# ================================================================================================
# finish with hand layers
# ================================================================================================


def _fake_sfx(cue: SfxCue, target: Path) -> Path:
    return make_tone(target, seconds=cue.seconds, freq=300, volume=0.8)


def _fake_bed(spine: dict, music: str | None, target: Path) -> Path:
    return make_tone(target.with_suffix(".wav"), seconds=6.0, freq=220, volume=0.9)


#: Board frames at the head: 10 frames at 24 fps is 0.42 s, far more than any window below can absorb.
BOARD_FRAMES = 10


@needs_ffmpeg
def test_finish_lays_mute_voice_and_cue_at_filmed_times_with_no_shift_after_deboard(
    post_desk: Path,
) -> None:
    takes = post_desk / "ep01" / "takes"
    frames = [board_array()] * BOARD_FRAMES + shot_frames(
        120 - BOARD_FRAMES, (40, 60, 200)
    )
    write_frames(takes / "take-ep01-t1-raw-v1.mp4", frames, tones=TWO_LINES)
    board = post_desk / "ep01" / "boards" / "board-ep01-t1-1-v1.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(board_array()).save(board)
    approve_board(post_desk, episode=1, take_id="t1", image=board)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    line = make_tone(
        post_desk / "ep01" / "voices" / "voice-ep01-aya-v1.mp3",
        seconds=0.4,
        freq=660,
        volume=0.3,
    )
    sting = make_tone(
        post_desk / "ep01" / "sfx" / "cue-sting-v1.mp3",
        seconds=0.3,
        freq=1200,
        volume=0.6,
    )
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=_fake_sfx, bed_maker=_fake_bed, facts_fetcher=lambda *a: None, colour=False,
        mutes=((3.2, 4.0),), voices=(Placed(line, 2.5),), cues=(Placed(sting, 0.6),), stream=out,
    )  # fmt: skip

    assert result.complete, out.getvalue()
    steps = [s.step for s in result.steps]
    assert steps[:4] == ["deboard", "voice", "sfx", "cues"], steps
    deboarded = result.steps[0].output
    assert deboarded is not None, (
        "the take opened on board frames, so deboard wrote a file"
    )
    voiced = takes / "take-ep01-t1-voice-v1.mp4"
    # Times are on the take as filmed: deboard kept the timeline, so a shift by the 0.42 s of board frames
    # would put Aya's mute at 2.78-3.58 s (her line audible again at 3.6-3.9 s) and the dry line at 2.08 s.
    assert rms(voiced, 3.3, 3.9) < -80, "Aya's line is muted at its filmed time"
    assert rms(voiced, 1.1, 1.9) > -35, "Kenji's line is left as filmed"
    assert rms(voiced, 2.55, 2.85) > -30, "the dry line lands at 2.5 s"
    assert rms(voiced, 2.2, 2.45) < -60, "and not before it"
    cued = takes / "take-ep01-t1-cues-v1.mp4"
    # -8 dB house level and a further -10 dB inside the speaking shot (0-2.5 s): quiet, but there, at 0.6 s.
    assert rms(cued, 0.65, 0.85) > -55 and rms(cued, 0.2, 0.55) < -70, (
        "the cue lands at 0.6 s"
    )
    assert (
        result.sound_line()
        == "Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓ · hand voice ✓ · hand cues ✓"
    )
    notes = (post_desk / "ep01" / "run-notes.md").read_text()
    assert (
        "Hand voice" in notes and "muted 3.20-4.00s" in notes and "Hand cues" in notes
    )
    # The finish record names the hand-laid line, so review counts it as on the take.
    from creation.post.finish_record import latest_finish_record

    record = latest_finish_record(post_desk, 1, "t1")
    assert record is not None
    assert [(v["file"], v["start"]) for v in record.hand_voices] == [
        ("voice-ep01-aya-v1.mp3", 2.5)
    ]


@needs_ffmpeg
def test_the_bed_ducks_under_a_hand_voice_line_like_speech(post_desk: Path) -> None:
    quiet_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    line = make_tone(
        post_desk / "ep01" / "voices" / "voice-ep01-aya-v1.mp3",
        seconds=0.5,
        freq=660,
        volume=0.3,
    )

    result = run_finish(post_desk, bed_maker=_fake_bed, facts_fetcher=lambda *a: None, colour=False, duck_db=12.0,
                        voices=(Placed(line, 3.0),), stream=io.StringIO())  # fmt: skip

    mix = next(s for s in result.steps if s.step == "mix")
    assert mix.status == "ran", mix.detail
    assert "12 dB in 2 voice window(s)" in mix.detail, (
        "the take's own sound at 0.2 s and the dry line at 3.0 s"
    )


@needs_ffmpeg
def test_finish_refuses_a_silent_hand_cue_before_any_step(post_desk: Path) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    silent = make_tone(
        post_desk / "ep01" / "sfx" / "cue-hush-v1.mp3", seconds=1.0, volume=0.0
    )

    with pytest.raises(ValueError, match="silent"):
        run_finish(post_desk, cues=(Placed(silent, 1.0),), stream=io.StringIO())
    assert [p.name for p in (post_desk / "ep01" / "takes").glob("*.mp4")] == [
        "take-ep01-t1-raw-v1.mp4"
    ]


@needs_ffmpeg
def test_a_hand_step_that_fails_makes_the_take_not_done(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    sting = make_tone(
        post_desk / "ep01" / "sfx" / "cue-sting-v1.mp3", seconds=0.5, volume=0.5
    )

    def broken(*_: Any, **__: Any) -> Path:
        raise RuntimeError("cue came out SILENT in the cue layer")

    monkeypatch.setattr(finish_mod, "lay_cues", broken)
    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=_fake_sfx, bed_maker=_fake_bed, facts_fetcher=lambda *a: None,
                        cues=(Placed(sting, 1.0),), stream=out)  # fmt: skip

    assert not result.complete
    assert result.sound_missing == ("cues",)
    assert "hand cues ✗" in result.sound_line()
    assert "NOT DONE" in out.getvalue()


@needs_ffmpeg
def test_finish_cli_takes_the_hand_flags_and_refuses_a_bad_one(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation import cli_post

    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        cli_post, "run_finish", lambda desk, **kwargs: seen.update(kwargs) or _Done()
    )
    code = main(["finish", "--desk", str(post_desk), "--mute", "6.9-8.3", "--mute", "1-2",
                 "--voice", "a.mp3@5.2", "--cue", "b.mp3@4.2@-12"])  # fmt: skip
    assert code == 0
    assert seen["mutes"] == ((6.9, 8.3), (1.0, 2.0))
    assert seen["voices"] == (Placed(Path("a.mp3"), 5.2),)
    assert seen["cues"] == (Placed(Path("b.mp3"), 4.2, -12.0),)

    assert main(["finish", "--desk", str(post_desk), "--mute", "8-6"]) == 2
    assert "A-B" in capsys.readouterr().err


class _Done:
    complete = True
    final = Path("final.mp4")


# ================================================================================================
# cue and voice-line
# ================================================================================================


class FakeAudio:
    """The server's operator audio routes: records every request, answers with stored URLs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def sfx_cue(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("cue", kwargs))
        return {"audio_url": "https://media.test/cue.mp3", "kind": "event", "shape_problem": None,
                "cached": False, "cost_usd": 0.003}  # fmt: skip

    def voice_line(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("line", kwargs))
        return {"audio_url": "https://media.test/line.mp3", "seconds": 0.4, "provider_voice": "Roger",
                "reading": {"checked": True, "read_right": True, "match": 1.0}, "cost_usd": 0.002}  # fmt: skip


def _downloads(monkeypatch: pytest.MonkeyPatch, *, volume: float = 0.5) -> list[str]:
    fetched: list[str] = []

    def fake_download(url: str, dest: Path) -> Path:
        fetched.append(url)
        return make_tone(dest, seconds=1.0, freq=300, volume=volume)

    monkeypatch.setattr(handmade, "download", fake_download)
    return fetched


@needs_ffmpeg
def test_cue_saves_a_versioned_file_prints_its_shape_and_books_its_cost(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched = _downloads(monkeypatch)
    audio = FakeAudio()
    out = io.StringIO()

    first = handmade.run_cue(post_desk, episode=1, description="A descending comic brass sting", seconds=1.0,
                             audio=audio, out=out)  # fmt: skip
    second = handmade.run_cue(post_desk, episode=1, description="A descending comic brass sting", seconds=1.0,
                              audio=audio, out=io.StringIO())  # fmt: skip

    assert first == post_desk / "ep01" / "sfx" / "cue-a-descending-comic-brass-v1.mp3"
    assert second.name == "cue-a-descending-comic-brass-v2.mp3", "never overwrites"
    assert fetched == ["https://media.test/cue.mp3"] * 2
    (_, one), (_, two) = audio.calls
    assert one["sound"] == "A descending comic brass sting" and one["seconds"] == 1.0
    assert one["spine_id"] == "spine_test" and one["key"] == two["key"], (
        "a re-run replays the same key"
    )
    printed = out.getvalue()
    assert "RMS per 0.5 s: 0.0s" in printed and "shape (event): ok" in printed
    assert "$" not in printed, "cost never reaches printed output"
    assert load_series(post_desk).spend_usd == pytest.approx(0.006), (
        "each render is booked on the ledger"
    )
    assert {entry.unit for entry in load_series(post_desk).spend_log} == {
        "cue:a-descending-comic-brass"
    }  # same words as the file name
    sidecar = json.loads(first.with_suffix(".json").read_text())
    assert (
        sidecar["shape_problem"] is None
        and sidecar["kind"] == "event"
        and len(sidecar["rms_db"]) == 2
    )


@needs_ffmpeg
def test_cue_flags_a_silent_render_and_refuses_a_bad_length_before_calling(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _downloads(monkeypatch, volume=0.0)
    audio = FakeAudio()
    out = io.StringIO()
    handmade.run_cue(
        post_desk, episode=1, description="a door", seconds=1.0, audio=audio, out=out
    )
    assert "!! shape (event): silent" in out.getvalue()

    with pytest.raises(ValueError, match="--seconds"):
        handmade.run_cue(
            post_desk, episode=1, description="a door", seconds=30.0, audio=audio
        )
    with pytest.raises(ValueError, match="--description"):
        handmade.run_cue(post_desk, episode=1, description="  ", audio=audio)
    assert len(audio.calls) == 1


class FakeSpineApi:
    def __init__(self, spine: dict[str, Any]) -> None:
        self.spine_body = spine
        self.client = self

    def close(self) -> None:
        pass

    def spine(self, spine_id: str) -> dict[str, Any]:
        return {"spine": copy.deepcopy(self.spine_body)}

    def save(self, name: str, payload: Any) -> None:
        pass


@needs_ffmpeg
def test_voice_line_renders_in_the_locked_voice_saves_it_and_books_it(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        handmade, "open_api", lambda desk, episode: FakeSpineApi(copy.deepcopy(SPINE))
    )
    fetched = _downloads(monkeypatch)
    audio = FakeAudio()
    out = io.StringIO()

    path = handmade.run_voice_line(
        post_desk, cast="Kenji", text="Wait for me here.", audio=audio, out=out
    )

    assert path == post_desk / "ep01" / "voices" / "voice-ep01-kenji-v1.mp3"
    assert fetched == ["https://media.test/line.mp3"]
    ((route, call),) = audio.calls
    assert (
        route == "line"
        and call["cast_id"] == "cast_kenji"
        and call["text"] == "Wait for me here."
    )
    assert call["language"] == "en" and call["spoken_text"] is None
    assert call["key"] == revoice_key(
        "voice-ep01-kenji",
        {"text": "Wait for me here.", "voice": "Roger", "language": "en"},
    ), "the same key revoice sends for this line: never paid twice"
    assert load_series(post_desk).spend_usd == pytest.approx(0.002)
    assert [entry.unit for entry in load_series(post_desk).spend_log] == [
        "voice-line:kenji"
    ]
    assert json.loads(path.with_suffix(".json").read_text())["voice"] == "Roger"
    assert "--voice" in out.getvalue() and "$" not in out.getvalue()


def test_voice_line_refuses_a_character_with_no_locked_voice(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spine = copy.deepcopy(SPINE)
    spine["cast"][0]["voice_brief"] = {}
    monkeypatch.setattr(handmade, "open_api", lambda desk, episode: FakeSpineApi(spine))
    audio = FakeAudio()
    with pytest.raises(ValueError, match="no locked voice"):
        handmade.run_voice_line(post_desk, cast="Kenji", text="Hi.", audio=audio)
    assert audio.calls == []


@needs_ffmpeg
def test_cue_and_voice_line_commands_run_from_the_cli(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = FakeAudio()
    monkeypatch.setattr(handmade, "DramaApiAudio", lambda desk, episode: audio)
    monkeypatch.setattr(
        handmade, "open_api", lambda desk, episode: FakeSpineApi(copy.deepcopy(SPINE))
    )
    _downloads(monkeypatch)

    assert (
        main(
            [
                "cue",
                "--desk",
                str(post_desk),
                "--episode",
                "1",
                "--description",
                "a cup set down",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "voice-line",
                "--desk",
                str(post_desk),
                "--cast",
                "cast_aya",
                "--text",
                "Not tonight.",
            ]
        )
        == 0
    )
    assert [route for route, _ in audio.calls] == ["cue", "line"]
    assert audio.calls[0][1]["seconds"] == 1.5
    assert (post_desk / "ep01" / "sfx" / "cue-a-cup-set-down-v1.mp3").is_file()
    assert (post_desk / "ep01" / "voices" / "voice-ep01-aya-v1.mp3").is_file()


# ================================================================================================
# a hand cue on an auto cue of the same sound
# ================================================================================================


def test_a_hand_cue_on_an_auto_cue_of_the_same_sound_says_which_to_drop() -> None:
    from creation.post.sfx import duplicate_cue_warnings

    laid = (("a door slams", 1.80), ("a door slams", 4.80), ("low hum", 5.0))
    warnings = duplicate_cue_warnings((("a heavy steel door slam", 5.25),), laid)

    assert len(warnings) == 1, warnings
    assert "@4.80s" in warnings[0] and '--sfx-adjust "door=drop"' in warnings[0]
    # Far apart, or a different sound: no warning.
    assert duplicate_cue_warnings((("a heavy door slam", 3.2),), laid) == []
    assert duplicate_cue_warnings((("a glass shatters", 4.9),), laid) == []


@needs_ffmpeg
def test_finish_warns_when_a_hand_cue_doubles_an_auto_cue(post_desk: Path) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    slam = make_tone(
        post_desk / "ep01" / "sfx" / "cue-a-heavy-door-slam-v1.mp3",
        seconds=0.4,
        freq=900,
        volume=0.6,
    )
    slam.with_suffix(".json").write_text(
        json.dumps({"description": "a heavy door slam"})
    )
    out = io.StringIO()

    result = run_finish(post_desk, sfx_render=_fake_sfx, bed_maker=_fake_bed, facts_fetcher=lambda *a: None,
                        colour=False, cues=(Placed(slam, 3.4),), stream=out)  # fmt: skip

    cues = next(s for s in result.steps if s.step == "cues")
    assert '--sfx-adjust "door=drop"' in cues.detail, cues.detail
    assert '--sfx-adjust "door=drop"' in out.getvalue()

    from creation.post.sfx import parse_adjustment

    again = run_finish(post_desk, sfx_render=_fake_sfx, bed_maker=_fake_bed, facts_fetcher=lambda *a: None,
                       colour=False, cues=(Placed(slam, 3.4),), sfx_adjust=(parse_adjustment("door=drop"),),
                       stream=io.StringIO())  # fmt: skip
    assert "!!" not in next(s for s in again.steps if s.step == "cues").detail
