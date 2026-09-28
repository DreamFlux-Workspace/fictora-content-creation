"""finish: the whole local chain on a real take (ffmpeg), Fal and the API stubbed."""

from __future__ import annotations

import functools
import io
import json
from pathlib import Path

import pytest
from conftest import make_take, make_tone, needs_ffmpeg
from PIL import Image

from creation import cli_post
from creation.cli_produce import main
from creation.ops.floor import approve_board
from creation.ops.state import load_series
from creation.post.finish import FINISH_INCOMPLETE, run_finish
from creation.post.sfx import SfxCue

#: Kenji at 1-2 s, Aya at 3.2-4 s (the spine's two lines).
TWO_LINES = ((1.0, 2.0, 440), (3.2, 4.0, 880))

FACTS = {
    "job_id": "job_video_scene_1",
    "take_facts": {
        "job_id": "job_video_scene_1",
        "endpoint_id": "minimax/h3-max/reference-to-video",
        "media_kind": "video",
        "reference_image_count": 3,
        "spoken_line_count": 1,
        "shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5, "speaks": True},
            {"shot_index": 2, "start_seconds": 2.5, "end_seconds": 5.0, "speaks": False},
        ],
        "sfx_cues": [
            {"shot_index": 2, "sound": "a door slams", "kind": "event", "start_seconds": 3.0, "duration_seconds": 1.0}
        ],
    },
}


def fake_sfx(calls: list[str]):
    def render(cue: SfxCue, target: Path) -> Path:
        calls.append(cue.sound)
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.8)

    return render


def fake_bed(description: str, seed: int, target: Path) -> Path:
    # Loud enough that the mixed take has no silence left: captions must be timed on the take before the bed.
    return make_tone(target.with_suffix(".wav"), seconds=6.0, freq=220, volume=0.9)


def _board(desk: Path) -> Path:
    board = desk / "ep01" / "boards" / "board-ep01-t1-1-v1.png"
    board.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (192, 336), (200, 150, 110)).save(board)
    approve_board(desk, episode=1, take_id="t1", image=board)
    return board


@needs_ffmpeg
def test_finish_lays_sfx_music_mix_captions_and_mark_as_new_versions(desk: Path) -> None:
    raw = make_take(desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(json.dumps(FACTS))
    _board(desk)
    calls: list[str] = []
    out = io.StringIO()
    result = run_finish(desk, sfx_render=fake_sfx(calls), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, stream=out)  # fmt: skip

    assert result.complete, out.getvalue()
    assert [s.step for s in result.steps] == ["sfx", "bed", "colour", "mix", "captions", "watermark"]
    assert all(s.status == "ran" for s in result.steps), out.getvalue()
    names = sorted(p.name for p in (desk / "ep01" / "takes").glob("*.mp4"))
    assert names == sorted([
        "take-ep01-t1-raw-v1.mp4", "take-ep01-t1-sfx-v1.mp4", "take-ep01-t1-colour-v1.mp4",
        "take-ep01-t1-mix-v1.mp4", "take-ep01-t1-cap-v1.mp4", "take-ep01-t1-sokii-v1.mp4",
    ])  # fmt: skip
    assert result.final.name == "take-ep01-t1-sokii-v1.mp4"
    assert raw.stat().st_size > 0
    assert calls == ["a door slams"]
    assert load_series(desk).bed_path.startswith("shared/beds/show-bed-v1")
    captions = next(s for s in result.steps if s.step == "captions")
    assert "3.2" in captions.detail.split("'Wait for me here.'; ")[1][:5], (
        "Aya's caption starts on her line at 3.2 s, not on the door slam at 3.0 s: timed on the take before post"
    )
    mix = next(s for s in result.steps if s.step == "mix")
    assert "NO MUSIC BED" not in mix.detail
    assert out.getvalue().rstrip().splitlines()[-2] == "Sound: music ✓ · SFX ✓ · mix ✓ · captions ✓"
    notes = (desk / "ep01" / "run-notes.md").read_text()
    assert "Finish summary" in notes and "NOT DONE" not in notes

    again = run_finish(desk, sfx_render=fake_sfx(calls), bed_maker=fake_bed,
                       facts_fetcher=lambda *a: None, stream=io.StringIO())  # fmt: skip
    assert again.final.name == "take-ep01-t1-sokii-v2.mp4"
    assert calls == ["a door slams"], "the cue is cached: a second finish renders nothing"


@needs_ffmpeg
def test_finish_without_sfx_says_not_done_and_the_cli_exits_5(desk: Path, monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    make_take(desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    stubbed = functools.partial(run_finish, bed_maker=fake_bed, facts_fetcher=lambda *a: None)
    monkeypatch.setattr(cli_post, "run_finish", stubbed)

    code = main(["finish", "--desk", str(desk)])

    assert code == FINISH_INCOMPLETE
    err = capsys.readouterr().err
    assert "Sound: music ✓ · SFX ✗ · mix ✓ · captions ✓" in err
    assert "NOT DONE: this take has no SFX" in err
    assert "Finish NOT DONE: no SFX" in (desk / "ep01" / "run-notes.md").read_text()


@needs_ffmpeg
def test_finish_refuses_a_bad_duck_depth_before_any_step(desk: Path) -> None:
    make_take(desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    with pytest.raises(ValueError, match="--duck-db"):
        run_finish(desk, duck_db=45.0, stream=io.StringIO())
    assert [p.name for p in (desk / "ep01" / "takes").glob("*.mp4")] == ["take-ep01-t1-raw-v1.mp4"]


@needs_ffmpeg
def test_finish_fetches_take_facts_when_none_are_saved(desk: Path) -> None:
    make_take(desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    fetched: list[tuple[int, str]] = []

    def fetch(d: Path, episode: int, take_id: str) -> Path:
        fetched.append((episode, take_id))
        path = d / "ep01" / "api" / "take-facts-ep01-t1-v1.json"
        path.write_text(json.dumps(FACTS))
        return path

    result = run_finish(desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=fetch,
                        colour=False, stream=io.StringIO())  # fmt: skip
    assert fetched == [(1, "t1")]
    assert result.complete
