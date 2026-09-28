"""join: finished takes into one file with one bed across it (real ffmpeg on tiny synthetic clips; no API)."""

from __future__ import annotations

import functools
import hashlib
import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from conftest import make_take, needs_ffmpeg

from creation import cli_post
from creation.cli_produce import main
from creation.ops.floor import init_series_desk
from creation.post import join as join_module
from creation.post.bed import pin_bed
from creation.post.finish import run_finish
from creation.post.finish_record import write_finish_record
from creation.post.join import JOIN_NOT_DONE, assert_house_fps, run_join
from creation.post.media import MediaToolError, decode_frames, measure_rms_windows
from creation.post.watermark import mark_position, watermark

SIZE = (192, 336)
#: The stand-in for a burned caption: a white box across the caption band.
BOX = (20, 200, 152, 30)
MARK_BOX = (*mark_position(*SIZE), 72, 63)


def _run(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def clip(path: Path, *, seconds: float, grey: int, fps: int = 24, size: tuple[int, int] = SIZE,
         tone: float = 0.0, box: tuple[float, float] | None = None, own_bed: bool = False) -> Path:  # fmt: skip
    """A flat grey picture; a steady 440 Hz tone at ``tone`` (0 = silence); a white caption box in ``box`` seconds.

    ``own_bed`` adds what finish leaves on a take: a loud bed that fades in over 1 s from the take's start.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    colour = f"0x{grey:02x}{grey:02x}{grey:02x}"
    video = f"color=c={colour}:s={size[0]}x{size[1]}:d={seconds}:r={fps}"
    vf = "null" if box is None else (
        f"drawbox=x={BOX[0]}:y={BOX[1]}:w={BOX[2]}:h={BOX[3]}:color=white:t=fill:enable='between(t,{box[0]},{box[1]})'"
    )  # fmt: skip
    audio = (
        f"sine=f=440:d={seconds}:sample_rate=48000"
        if tone
        else f"anullsrc=r=48000:cl=mono:d={seconds}"
    )
    af = f"volume={tone}" if tone else "anull"
    inputs = ["-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio]
    graph = [f"[0:v]{vf}[v]", f"[1:a]{af}[t]"]
    if own_bed:
        inputs += ["-f", "lavfi", "-i", f"anoisesrc=c=white:a=0.4:d={seconds}:r=48000"]
        graph += ["[2:a]afade=t=in:st=0:d=1[b]", "[t][b]amix=inputs=2:normalize=0[a]"]
    else:
        graph.append("[t]anull[a]")
    _run([*inputs, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]", "-t", str(seconds),
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)])  # fmt: skip
    return path


def finished_take(desk: Path, episode: int, take_id: str, *, seconds: float = 2.5, grey: int = 70,
                  tone: float = 0.0, box: tuple[float, float] | None = None, fps: int = 24,
                  size: tuple[int, int] = SIZE) -> dict[str, Path]:  # fmt: skip
    """What finish leaves: the take before the bed, the captioned mix (own bed baked in), the marked final, a record."""

    takes = desk / f"ep{episode:02d}" / "takes"
    base = f"take-ep{episode:02d}-{take_id}"
    pre_bed = clip(
        takes / f"{base}-colour-v1.mp4",
        seconds=seconds,
        grey=grey,
        tone=tone,
        fps=fps,
        size=size,
    )
    master = clip(takes / f"{base}-cap-v1.mp4", seconds=seconds, grey=grey, tone=tone, box=box, fps=fps,
                  size=size, own_bed=True)  # fmt: skip
    final = watermark(master, takes / f"{base}-sokii-v1.mp4")
    record = write_finish_record(desk, episode=episode, take_id=take_id, complete=True, pre_bed=pre_bed,
                                 master=master, final=final, bed=None, bed_db=-16.5, duck_db=None)  # fmt: skip
    return {"pre_bed": pre_bed, "master": master, "final": final, "record": record}


def noise_bed(desk: Path, *, seconds: float = 1.5, tail: float = 0.3) -> Path:
    """A short generated-style bed: steady noise that ends on ``tail`` seconds of silence."""

    path = desk / "shared" / "beds" / "show-bed-v1.wav"
    _run(["-f", "lavfi", "-i", f"anoisesrc=c=white:a=0.3:d={seconds}:r=48000", "-af",
          f"apad=pad_dur={tail}", "-c:a", "pcm_s16le", str(path)])  # fmt: skip
    return pin_bed(desk, path)


def frame_at(video: Path, seconds: float) -> np.ndarray:
    frames = decode_frames(video, width=SIZE[0], height=SIZE[1], fps=24)
    return frames[min(len(frames) - 1, int(round(seconds * 24)))]


def region(frame: np.ndarray, box: tuple[int, int, int, int]) -> float:
    x, y, w, h = box
    return float(frame[y : y + h, x : x + w].mean())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def join_desk(tmp_path: Path) -> Path:
    """A 30s desk with two episodes (takes t1, t2 each)."""

    return init_series_desk(tmp_path, "Join Test", band="30s", episode_count=2)


@needs_ffmpeg
def test_an_episode_joins_on_one_continuous_bed_with_captions_and_one_mark(
    join_desk: Path,
) -> None:
    one = finished_take(join_desk, 1, "t1", grey=70)
    finished_take(join_desk, 1, "t2", grey=90, box=(1.0, 1.5))
    noise_bed(join_desk)
    out = io.StringIO()

    result = run_join(join_desk, episodes=(1,), stream=out)

    takes = join_desk / "ep01" / "takes"
    assert result.complete, out.getvalue()
    assert result.master == takes / "episode-ep01-join-v1.mp4"
    assert result.marked == takes / "episode-ep01-join-sokii-v1.mp4"
    assert [p.take_id for p in result.parts] == ["t1", "t2"]
    assert result.parts[0].picture.name == "take-ep01-t1-cap-v1.mp4", (
        "the un-marked captioned picture, not -sokii"
    )
    assert result.dissolves == [0.0] and result.seams == [2.5], (
        "takes of one episode meet on a straight cut"
    )
    assert abs(result.fps - 24) < 0.2
    assert len(result.seam_steps_db) == 1 and abs(result.seam_steps_db[0]) < 5

    # One continuous bed: the take audio is silent, so the joined sound IS the bed. No dip where a take's own
    # bed would restart (its 1 s fade-in at the seam) and none at the bed's loop points (its silent tail).
    levels = np.array(measure_rms_windows(result.master, window_seconds=0.1))
    body = levels[12:-17]
    assert body.min() > np.median(body) - 4, (
        f"the bed dips inside the join: {np.round(body, 1).tolist()}"
    )

    # Straight cut: no blended frame at the seam.
    for t in (2.4, 2.5, 2.55):
        assert (
            min(
                abs(region(frame_at(result.master, t), (0, 100, 192, 60)) - g)
                for g in (70, 90)
            )
            < 6
        )

    # Captions move with their picture: t2's caption (1.0-1.5 s on the take) is at 3.5-4.0 s on the join.
    assert region(frame_at(result.master, 3.75), BOX) > 200
    assert region(frame_at(result.master, 3.25), BOX) < 120
    assert region(frame_at(result.master, 1.25), BOX) < 120

    # Marked once: the joined mark looks exactly like one take's single mark; the master carries none.
    joined_mark = region(frame_at(result.marked, 1.0), MARK_BOX)
    take_mark = region(frame_at(one["final"], 1.0), MARK_BOX)
    assert abs(joined_mark - take_mark) < 3, (joined_mark, take_mark)
    assert abs(region(frame_at(result.master, 1.0), MARK_BOX) - 70) < 4
    assert abs(joined_mark - 70) > 5, "the marked file has a mark"
    assert "Join" in (join_desk / "ep01" / "run-notes.md").read_text()

    # Nothing is overwritten: a second join is v2 and v1 is untouched.
    before = {p.name: digest(p) for p in takes.glob("*.mp4")}
    again = run_join(join_desk, episodes=(1,), stream=io.StringIO())
    assert (
        again.master.name == "episode-ep01-join-v2.mp4"
        and again.marked.name == "episode-ep01-join-sokii-v2.mp4"
    )
    assert all(digest(takes / name) == sha for name, sha in before.items())


@needs_ffmpeg
def test_a_series_cut_dissolves_between_episodes_and_captions_land_on_the_joined_timeline(
    join_desk: Path,
) -> None:
    finished_take(join_desk, 1, "t1", seconds=2.0, grey=40)
    finished_take(join_desk, 1, "t2", seconds=2.0, grey=40)
    finished_take(join_desk, 2, "t1", seconds=2.0, grey=200, box=(1.0, 1.5))
    finished_take(join_desk, 2, "t2", seconds=2.0, grey=200)
    noise_bed(join_desk)

    result = run_join(join_desk, episodes=(1, 2), stream=io.StringIO())

    assert result.complete
    assert (
        result.master == join_desk / "shared" / "cuts" / "series-ep01-ep02-join-v1.mp4"
    )
    assert result.dissolves == [0.0, 0.25, 0.0]
    assert result.seams == [2.0, 3.875, 5.75]
    middle = region(frame_at(result.master, 3.875), (0, 100, 192, 60))
    assert 70 < middle < 170, (
        f"mid-dissolve frame is a blend of the two episodes, got {middle:.0f}"
    )
    # ep02 t1 starts at 4.0 - 0.25 = 3.75 s: its caption (1.0-1.5 s) is at 4.75-5.25 s.
    assert region(frame_at(result.master, 5.0), BOX) > 225
    assert region(frame_at(result.master, 4.5), BOX) < 215
    assert "Join" in (join_desk / "ep02" / "run-notes.md").read_text()


@needs_ffmpeg
def test_a_seam_step_over_5_db_stops_the_join_unmarked_and_gain_matching_fixes_it(
    join_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    finished_take(join_desk, 1, "t1", tone=0.01)
    finished_take(join_desk, 1, "t2", tone=0.5)
    noise_bed(join_desk)
    monkeypatch.setattr(
        cli_post, "run_join", functools.partial(run_join, stream=io.StringIO())
    )

    code = main(
        [
            "join",
            "--desk",
            str(join_desk),
            "--episode",
            "1",
            "--no-gain-match",
            "--json",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == JOIN_NOT_DONE
    assert report["complete"] is False and report["marked"] is None
    assert abs(report["seams"][0]["step_db"]) > 5
    takes = join_desk / "ep01" / "takes"
    assert (takes / "episode-ep01-join-v1.mp4").is_file(), (
        "the un-marked master stays to listen to"
    )
    assert not list(takes.glob("episode-ep01-join-sokii-*.mp4")), "nothing is marked"
    assert "STOPPED: seam at 2.50s" in (join_desk / "ep01" / "run-notes.md").read_text()

    matched = run_join(join_desk, episodes=(1,), stream=io.StringIO())
    assert matched.complete and abs(matched.seam_steps_db[0]) < 5, matched.seam_steps_db
    assert matched.gains_db[0] - matched.gains_db[1] > 30, (
        "each take gained to the median: quiet up, loud down"
    )


@needs_ffmpeg
@pytest.mark.parametrize(
    ("second", "wanted"),
    [({"fps": 25}, "not 24 fps: ep01 t2"), ({"size": (208, 336)}, "mixed resolutions")],
)
def test_join_refuses_mixed_rates_and_sizes_before_writing(
    join_desk: Path, second: dict, wanted: str
) -> None:
    finished_take(join_desk, 1, "t1")
    finished_take(join_desk, 1, "t2", **second)
    noise_bed(join_desk)

    with pytest.raises(ValueError, match=wanted):
        run_join(join_desk, episodes=(1,), stream=io.StringIO())
    assert not list((join_desk / "ep01" / "takes").glob("episode-*"))


@needs_ffmpeg
def test_join_refuses_takes_all_at_25_fps(join_desk: Path) -> None:
    finished_take(join_desk, 1, "t1", fps=25)
    finished_take(join_desk, 1, "t2", fps=25)
    noise_bed(join_desk)

    with pytest.raises(ValueError, match="not 24 fps: ep01 t1, ep01 t2"):
        run_join(join_desk, episodes=(1,), stream=io.StringIO())


@needs_ffmpeg
def test_the_joined_file_is_measured_at_24_fps(join_desk: Path, monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:  # fmt: skip
    with pytest.raises(MediaToolError, match="not 24"):
        assert_house_fps(clip(tmp_path / "half-rate.mp4", seconds=1.0, grey=50, fps=12))

    finished_take(join_desk, 1, "t1")
    finished_take(join_desk, 1, "t2")
    noise_bed(join_desk)
    checked: list[str] = []

    def spy(path: Path) -> float:
        checked.append(path.name)
        return assert_house_fps(path)

    monkeypatch.setattr(join_module, "assert_house_fps", spy)
    run_join(join_desk, episodes=(1,), stream=io.StringIO())
    assert checked == ["episode-ep01-join-v1.mp4"]


@needs_ffmpeg
def test_take_files_in_the_order_given_and_unfinished_files_are_refused(
    join_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = finished_take(join_desk, 1, "t1", grey=70)
    two = finished_take(join_desk, 1, "t2", grey=90)
    noise_bed(join_desk)
    trimmed = clip(
        join_desk / "ep01" / "takes" / "take-ep01-t2-trim-v1.mp4", seconds=2.0, grey=90
    )

    assert (
        main(
            [
                "join",
                "--desk",
                str(join_desk),
                "--take-file",
                str(trimmed),
                "--take-file",
                str(one["final"]),
            ]
        )
        == 2
    )
    assert "no finish record names this file" in capsys.readouterr().err

    code = main(
        [
            "join",
            "--desk",
            str(join_desk),
            "--take-file",
            str(two["final"]),
            "--take-file",
            str(one["final"]),
        ]
    )
    assert code == 0
    marked = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert marked.name == "episode-ep01-join-sokii-v1.mp4"
    assert abs(region(frame_at(marked, 1.0), (0, 100, 192, 60)) - 90) < 6, (
        "t2 comes first, as named"
    )


@needs_ffmpeg
def test_an_episode_with_an_unfinished_take_is_refused(join_desk: Path) -> None:
    finished_take(join_desk, 1, "t1")
    noise_bed(join_desk)

    with pytest.raises(ValueError, match="no finished t2"):
        run_join(join_desk, episodes=(1,), stream=io.StringIO())


# --- end to end: two real finish runs, then the join -------------------------------------------

#: Line and cue late in each 3 s take, so the 2 s level windows either side of the seam read the bed, not a line.
FACTS = {
    "job_id": "job_video_scene_1",
    "take_facts": {
        "job_id": "job_video_scene_1",
        "shots": [{"shot_index": 1, "start_seconds": 0.0, "end_seconds": 3.0, "speaks": False}],
        "sfx_cues": [{"shot_index": 1, "sound": "rain", "kind": "sustained", "start_seconds": 2.0,
                      "duration_seconds": 0.5}],
    },
}  # fmt: skip


@needs_ffmpeg
def test_finish_writes_the_record_join_reads(post_desk: Path) -> None:
    from conftest import make_tone

    def sfx(cue, target: Path) -> Path:
        return make_tone(target, seconds=cue.seconds, freq=300, volume=0.3)

    def bed(spine, music, target: Path) -> Path:
        return make_tone(target.with_suffix(".wav"), seconds=2.0, freq=220, volume=0.5)

    api = post_desk / "ep01" / "api"
    for take_id in ("t1", "t2"):
        make_take(post_desk / "ep01" / "takes" / f"take-ep01-{take_id}-raw-v1.mp4", seconds=3.0,
                  tones=((1.8, 2.2, 440),))  # fmt: skip
        (api / f"take-facts-ep01-{take_id}-v1.json").write_text(json.dumps(FACTS))
        finished = run_finish(post_desk, take_id=take_id, sfx_render=sfx, bed_maker=bed,
                              facts_fetcher=lambda *a: None, stream=io.StringIO())  # fmt: skip
        assert finished.complete
    record = json.loads(
        (post_desk / "ep01" / "takes" / "take-ep01-t2-finish-v1.json").read_text()
    )
    assert record["final"] == "ep01/takes/take-ep01-t2-sokii-v1.mp4"
    assert record["master"] in (
        "ep01/takes/take-ep01-t2-cap-v1.mp4",
        "ep01/takes/take-ep01-t2-mix-v1.mp4",
    )
    assert (
        f"(un-marked master `{Path(record['master']).name}`)"
        in (post_desk / "ep01" / "run-notes.md").read_text()
    )
    assert record["pre_bed"] == "ep01/takes/take-ep01-t2-sfx-v1.mp4", (
        "the mix read the take with its effects"
    )

    result = run_join(post_desk, episodes=(1,), stream=io.StringIO())

    assert result.complete
    assert [p.pre_bed.name for p in result.parts] == [
        "take-ep01-t1-sfx-v1.mp4",
        "take-ep01-t2-sfx-v1.mp4",
    ]
    assert result.bed.name.startswith("show-bed-v1")
    assert (
        result.marked is not None
        and result.marked.name == "episode-ep01-join-sokii-v1.mp4"
    )
