"""``review``: the numbers-only read of a take, on tiny synthetic clips (real ffmpeg, no network).

Each section must find the fault planted in a clip and stay quiet on a clean one.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import board_array, needs_ffmpeg, write_frames
from fake_api import spine_fixture
from PIL import Image

from creation.cli_produce import main
from creation.post.review import (
    NONE,
    OK,
    WARN,
    Section,
    compare_cuts,
    review_take,
    take_kind,
)
from creation.post.whisper import Word, line_windows

pytestmark = needs_ffmpeg

W, H = 96, 168
BLUE, YELLOW = (40, 60, 200), (250, 220, 60)
RED, GREEN = (220, 40, 40), (40, 200, 90)


def _halves(
    count: int,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
    *,
    start: int = 0,
) -> list[Any]:
    """Frames whose top and bottom halves differ (a clean picture: no stacked double frame), drifting gently."""

    x = np.linspace(0, 1, W)[None, :, None]
    frames = []
    for index in range(count):
        wave = 0.6 + 0.3 * np.sin(2 * np.pi * (x + (start + index) * 0.03))
        frame = np.zeros((H, W, 3), dtype=np.float64)
        frame[: H // 2] = wave * np.array(top)[None, None, :]
        frame[H // 2 :] = wave * np.array(bottom)[None, None, :]
        frames.append(np.clip(frame, 0, 255).astype(np.uint8))
    return frames


def _clean(count: int = 72, *, start: int = 0) -> list[Any]:
    return _halves(count, BLUE, YELLOW, start=start)


def _desk_ready(
    desk: Path,
    *,
    spine: dict[str, Any] | None = None,
    facts: dict[str, Any] | None = None,
) -> Path:
    api = desk / "ep01" / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "spine.json").write_text(
        json.dumps(spine or spine_fixture()), encoding="utf-8"
    )
    if facts is not None:
        (api / "take-facts-ep01-t1-v1.json").write_text(
            json.dumps(facts), encoding="utf-8"
        )
    boards = desk / "ep01" / "boards"
    boards.mkdir(parents=True, exist_ok=True)
    Image.fromarray(board_array()).save(boards / "board-ep01-t1-v1.png")
    (desk / "ep01" / "takes").mkdir(parents=True, exist_ok=True)
    return desk / "ep01" / "takes"


def _section(review: Any, name: str) -> Section:
    return next(section for section in review.sections if section.name == name)


def _facts(
    changes: tuple[float, ...], *, seconds: float = 3.0, asked: tuple[int, int] = (1, 1)
) -> dict[str, Any]:
    starts = (0.0, *changes)
    ends = (*changes, seconds)
    return {
        "shots": [
            {"shot_index": i + 1, "start_seconds": a, "end_seconds": b, "speaks": False}
            for i, (a, b) in enumerate(zip(starts, ends))
        ],
        "lines": [
            {"line_id": "line_episode_01_01", "count": asked[0]},
            {"line_id": "line_episode_01_02", "count": asked[1]},
        ],
    }


def _av(path: Path, *, amplitude: float, seconds: float = 3.0) -> Path:
    """A flat picture with a steady 440 Hz sine at ``amplitude`` (0-1 of full scale)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:d={seconds}:r=24",
         "-f", "lavfi", "-i", f"aevalsrc={amplitude}*sin(2*PI*440*t):s=48000:d={seconds}",
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-t", str(seconds), str(path)],
        check=True,
    )  # fmt: skip
    return path


def _wav(path: Path, expr: str, seconds: float = 3.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc={expr.replace(',', chr(92) + ',')}:s=48000:d={seconds}",
         str(path)],
        check=True,
    )  # fmt: skip
    return path


# --- 1. Loudness -----------------------------------------------------------------------------------------


def test_a_finished_take_in_band_reads_clean_and_a_hot_one_is_flagged(
    desk: Path,
) -> None:
    takes = _desk_ready(desk)
    good = _av(
        takes / "take-ep01-t1-sokii-v1.mp4", amplitude=0.178
    )  # about -18 LUFS, -15 dBTP
    hot = _av(
        takes / "take-ep01-t1-sokii-v2.mp4", amplitude=0.9
    )  # about -4 LUFS, over -1 dBTP

    clean = _section(review_take(desk, take_file=good), "Loudness")
    loud = _section(review_take(desk, take_file=hot), "Loudness")

    assert clean.status == OK, clean.lines()
    assert -20 <= clean.data["lufs"] <= -15
    assert "-20 to -15 LUFS" in clean.threshold and "-1.0 dBTP" in clean.threshold
    assert loud.status == WARN
    assert (
        "outside the mix band" in loud.summary
        and "true peak over the ceiling" in loud.summary
    )


def test_a_silent_raw_take_is_flagged_and_a_spoken_one_is_not(desk: Path) -> None:
    takes = _desk_ready(desk)
    silent = _av(takes / "take-ep01-t1-raw-v1.mp4", amplitude=0.0)
    spoken = _av(takes / "take-ep01-t1-raw-v2.mp4", amplitude=0.178)

    assert _section(review_take(desk, take_file=silent), "Loudness").status == WARN
    ok = _section(review_take(desk, take_file=spoken), "Loudness")
    assert ok.status == OK and "silent below -30 LUFS" in ok.threshold


def test_duck_depth_is_read_from_the_mix_buses_when_they_are_on_the_desk(
    desk: Path,
) -> None:
    takes = _desk_ready(desk)
    mix = _av(takes / "take-ep01-t1-mix-v1.mp4", amplitude=0.178)
    stem = mix.with_suffix("")
    _wav(Path(f"{stem}-raw-bus.wav"), "0.2*sin(2*PI*220*t)")
    _wav(Path(f"{stem}-key-bus.wav"), "0.5*sin(2*PI*880*t)*between(t,1,2)")
    ducked = _wav(
        Path(f"{stem}-ducked-bus.wav"), "0.2*sin(2*PI*220*t)*(1-0.645*between(t,1,2))"
    )  # -9 dB

    deep = _section(review_take(desk, take_file=mix), "Loudness")
    assert deep.status == OK, deep.lines()
    assert 8.0 <= deep.data["duck"]["under_db"] <= 10.0
    assert abs(deep.data["duck"]["outside_db"]) < 1.0

    _wav(ducked, "0.2*sin(2*PI*220*t)")  # the ducker never moved
    flat = _section(review_take(desk, take_file=mix), "Loudness")
    assert flat.status == WARN and "duck depth out of band" in flat.summary


def test_without_bus_files_the_duck_depth_says_it_was_not_measured(desk: Path) -> None:
    takes = _desk_ready(desk)
    final = _av(takes / "take-ep01-t1-sokii-v1.mp4", amplitude=0.178)
    loud = _section(review_take(desk, take_file=final), "Loudness")
    assert loud.data["duck"] is None
    assert any("duck depth: not measured" in line for line in loud.details)


# --- 2. Cuts ---------------------------------------------------------------------------------------------


def _cut_take(path: Path) -> Path:
    """3 s: blue/yellow for 36 frames, a hard cut at 1.5 s, then red/green."""

    return write_frames(path, _clean(36) + _halves(36, RED, GREEN, start=36))


def test_cuts_match_the_take_facts_shot_changes(desk: Path) -> None:
    takes = _desk_ready(desk, facts=_facts((1.5,)))
    take = _cut_take(takes / "take-ep01-t1-raw-v1.mp4")

    cuts = _section(review_take(desk, take_file=take), "Cuts")

    assert cuts.status == OK, cuts.lines()
    assert len(cuts.data["cuts"]) == 1 and abs(cuts.data["cuts"][0] - 1.5) < 0.1
    assert cuts.data["missing"] == [] and cuts.data["extra"] == []


def test_an_extra_cut_and_a_missing_shot_change_are_named(desk: Path) -> None:
    takes = _desk_ready(desk, facts=_facts((2.4,)))
    take = _cut_take(takes / "take-ep01-t1-raw-v1.mp4")

    cuts = _section(review_take(desk, take_file=take), "Cuts")

    assert cuts.status == WARN
    assert cuts.data["missing"] == [2.4]
    assert len(cuts.data["extra"]) == 1
    text = "\n".join(cuts.lines())
    assert (
        "no hard cut near the planned shot change at 2.40s" in text
        and "extra cut at 1.5" in text
    )


def test_a_clean_single_shot_take_has_no_cut(desk: Path) -> None:
    takes = _desk_ready(desk, facts=_facts(()))
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    cuts = _section(review_take(desk, take_file=take), "Cuts")
    assert cuts.status == OK and cuts.data["cuts"] == []


def test_without_take_facts_the_cuts_are_listed_but_not_compared(desk: Path) -> None:
    takes = _desk_ready(desk)
    take = _cut_take(takes / "take-ep01-t1-raw-v1.mp4")
    cuts = _section(review_take(desk, take_file=take), "Cuts")
    assert "not compared (no take facts on the desk)" in cuts.summary
    assert len(cuts.data["cuts"]) == 1


def test_compare_cuts_gives_each_planned_change_one_cut() -> None:
    assert compare_cuts((1.0, 1.15, 5.0), (1.1,)) == ([], [1.0, 5.0])
    assert compare_cuts((), (2.0,)) == ([2.0], [])


# --- 3. Frozen / stacked frames ----------------------------------------------------------------------------


def test_a_frozen_stretch_is_flagged_with_its_times(desk: Path) -> None:
    takes = _desk_ready(desk)
    moving = _clean()
    frames = moving[:24] + [moving[24]] * 24 + moving[48:]  # 1.0-2.0 s does not move
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", frames)

    section = _section(review_take(desk, take_file=take), "Frames")

    assert section.status == WARN
    [(start, end)] = section.data["frozen"]
    assert 0.9 <= start <= 1.1 and 1.8 <= end <= 2.1
    assert "frame-to-frame RMSE < 0.002" in section.threshold


def test_a_stacked_double_frame_is_flagged(desk: Path) -> None:
    takes = _desk_ready(desk)
    frames = (
        _clean(24) + _halves(24, BLUE, BLUE, start=24) + _clean(24, start=48)
    )  # top equals bottom 1-2 s
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", frames)

    section = _section(review_take(desk, take_file=take), "Frames")

    assert section.status == WARN
    assert section.data["frozen"] == []
    [(start, end)] = section.data["stacked"]
    assert 0.9 <= start <= 1.1 and 1.7 <= end <= 2.0


def test_a_stacked_stretch_says_its_score(desk: Path) -> None:
    takes = _desk_ready(desk)
    frames = _clean(24) + _halves(24, BLUE, BLUE, start=24) + _clean(24, start=48)
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", frames)

    section = _section(review_take(desk, take_file=take), "Frames")

    [line] = [d for d in section.details if d.startswith("stacked double frame")]
    assert "top-vs-bottom 0.0" in line and "under 0.2" in line, line


def test_top_and_bottom_alike_over_the_whole_take_is_a_note_not_a_warning(
    desk: Path,
) -> None:
    takes = _desk_ready(desk)
    # A dark, even set: top and bottom halves alike for all 3 s (episode 2 flagged 0-14 s on a normal take).
    take = write_frames(
        takes / "take-ep01-t1-raw-v1.mp4", _halves(72, (30, 30, 40), (34, 30, 40))
    )

    section = _section(review_take(desk, take_file=take), "Frames")

    assert section.status == OK, section.lines()
    assert section.data["stacked"] == []
    [(start, end)] = section.data["stacked_whole_take"]
    assert start == 0.0 and end > 2.5
    assert any(
        d.startswith("note: top and bottom halves look alike") for d in section.details
    )


def test_a_moving_clean_take_has_no_frozen_or_stacked_frames(desk: Path) -> None:
    takes = _desk_ready(desk)
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    section = _section(review_take(desk, take_file=take), "Frames")
    assert section.status == OK, section.lines()


# --- 4. Board frames anywhere ----------------------------------------------------------------------------


def test_a_board_frame_mid_take_is_found(desk: Path) -> None:
    takes = _desk_ready(desk)
    frames = _clean()
    frames[40] = board_array()
    take = write_frames(takes / "take-ep01-t1-sokii-v1.mp4", frames)

    board = _section(review_take(desk, take_file=take), "Board")

    assert board.status == WARN
    assert board.data["head_frames"] == 0
    assert [item["frame"] for item in board.data["later"]] == [40]
    assert "1.67s" in "\n".join(board.lines())


def test_head_board_frames_are_fine_on_a_raw_take_and_a_fault_after_deboard(
    desk: Path,
) -> None:
    takes = _desk_ready(desk)
    frames = [board_array()] * 2 + _clean(70)
    raw = write_frames(takes / "take-ep01-t1-raw-v1.mp4", frames)
    deboarded = write_frames(takes / "take-ep01-t1-deboard-v1.mp4", frames)

    on_raw = _section(review_take(desk, take_file=raw), "Board")
    after = _section(review_take(desk, take_file=deboarded), "Board")

    assert (
        on_raw.status == OK
        and on_raw.data["head_frames"] == 2
        and on_raw.data["later"] == []
    )
    assert "finish removes them" in "\n".join(on_raw.lines())
    assert after.status == WARN and "still the board on a deboarded file" in "\n".join(
        after.lines()
    )


def test_a_take_with_no_board_frame_reads_clean(desk: Path) -> None:
    takes = _desk_ready(desk)
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    board = _section(review_take(desk, take_file=take), "Board")
    assert (
        board.status == OK
        and board.data["head_frames"] == 0
        and board.data["later"] == []
    )


# --- 5. Lines --------------------------------------------------------------------------------------------


def _words(
    path: Path,
    words: list[tuple[str, float, float]],
    readings: list[str | None] | None = None,
) -> Path:
    rows = []
    for index, (word, start, end) in enumerate(words):
        row: dict[str, Any] = {"word": word, "start": start, "end": end}
        if readings and readings[index] is not None:
            row["reading"] = readings[index]
        rows.append(row)
    path.write_text(json.dumps({"words": rows}, ensure_ascii=False), encoding="utf-8")
    return path


def test_lines_asked_and_heard_read_clean(desk: Path) -> None:
    takes = _desk_ready(desk, facts=_facts(()))
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    _words(takes / "take-ep01-t1-review-words-v1.json",
           [("We're", 0.2, 0.4), ("closed.", 0.4, 0.8), ("Not", 1.5, 1.7), ("for", 1.7, 1.8), ("me.", 1.8, 2.0)])  # fmt: skip

    lines = _section(review_take(desk, take_file=take), "Lines")

    assert lines.status == OK, lines.lines()
    assert "2 of 2 approved lines asked" in lines.summary
    assert "heard: 2 of 2" in "\n".join(lines.lines())


def test_a_line_not_heard_and_a_line_not_asked_are_flagged(desk: Path) -> None:
    takes = _desk_ready(desk, facts=_facts((), asked=(1, 0)))
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    words = _words(
        takes / "take-ep01-t1-review-words-v1.json",
        [("We're", 0.2, 0.4), ("closed.", 0.4, 0.8)],
    )

    lines = _section(review_take(desk, take_file=take, words_json=words), "Lines")

    text = "\n".join(lines.lines())
    assert lines.status == WARN
    assert "line 2 ('Not for me.') was not in the take's instructions" in text
    assert "2. MISSING  'Not for me.'" in text
    assert lines.data["asked_missing"] == 1


def test_a_line_heard_but_never_asked_for_is_still_flagged(desk: Path) -> None:
    takes = _desk_ready(desk, facts=_facts((), asked=(1, 0)))
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    words = _words(takes / "take-ep01-t1-review-words-v1.json",
                   [("We're", 0.2, 0.4), ("closed.", 0.4, 0.8), ("Not", 1.5, 1.7), ("for", 1.7, 1.8), ("me.", 1.8, 2.0)])  # fmt: skip

    lines = _section(review_take(desk, take_file=take, words_json=words), "Lines")

    assert lines.status == WARN
    assert "1 of 2 approved lines asked" in lines.summary
    assert "heard: 2 of 2" in "\n".join(lines.lines())


def test_a_line_laid_by_hand_with_finish_voice_counts_as_on_the_take(
    desk: Path,
) -> None:
    from creation.post.finish_record import write_finish_record

    takes = _desk_ready(desk, facts=_facts((), asked=(1, 0)))
    take = write_frames(takes / "take-ep01-t1-sokii-v1.mp4", _clean())
    write_finish_record(
        desk, episode=1, take_id="t1", complete=True, pre_bed=None, master=take, final=take,
        bed=None, bed_db=-16.5, duck_db=None,
        hand_voices=[{"file": "voice-ep01-ren-v1.mp3", "start": 1.5, "seconds": 0.6, "line": "Not for me."}],
    )  # fmt: skip

    lines = _section(review_take(desk, take_file=take), "Lines")

    text = "\n".join(lines.lines())
    assert lines.status == OK, text
    assert lines.data["asked_missing"] == 0
    assert "1 of 2 approved lines asked" in lines.summary
    assert "1 laid by hand with finish --voice: 2 of 2 on the take" in lines.summary
    assert "line 2 ('Not for me.') was laid by hand" in text
    assert "was not in the take's instructions" not in text


def test_without_a_transcript_the_heard_check_says_how_to_get_one(desk: Path) -> None:
    takes = _desk_ready(desk, facts=_facts(()))
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    lines = _section(review_take(desk, take_file=take), "Lines")
    assert lines.status == OK
    assert "--transcribe" in "\n".join(lines.lines())
    assert lines.data["heard"] is None


def test_transcribe_asks_the_injected_server_only_when_no_transcript_is_saved(
    desk: Path,
) -> None:
    takes = _desk_ready(desk, facts=_facts(()))
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    calls: list[tuple[int, str]] = []

    def fake(desk_: Path, episode: int, take_id: str) -> Path:
        calls.append((episode, take_id))
        return _words(
            takes / "take-ep01-t1-review-words-v1.json",
            [("We're", 0.2, 0.4), ("closed.", 0.4, 0.8)],
        )

    first = _section(
        review_take(desk, take_file=take, transcribe=True, transcriber=fake), "Lines"
    )
    again = _section(
        review_take(desk, take_file=take, transcribe=True, transcriber=fake), "Lines"
    )

    assert calls == [(1, "t1")]
    assert "transcript made on the server" in "\n".join(first.lines())
    assert first.status == WARN and again.status == WARN  # line 2 was not heard


def test_a_japanese_line_heard_on_the_server_readings_says_by_sound(desk: Path) -> None:
    spine = copy.deepcopy(spine_fixture(spoken_language="ja-JP"))
    first = spine["beats"][0]["dialogue_lines"][0]
    first.update({"text": "笑顔で", "spoken_text": "えがおで"})
    spine["beats"][0]["dialogue_lines"] = [first]
    takes = _desk_ready(
        desk,
        spine=spine,
        facts={"shots": [], "lines": [{"line_id": first["line_id"], "count": 1}]},
    )
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    _words(
        takes / "take-ep01-t1-review-words-v1.json",
        [("笑顔", 0.3, 0.7), ("で", 0.7, 0.9)],
        ["エガオ", "デ"],
    )

    lines = _section(review_take(desk, take_file=take), "Lines")

    assert lines.status == OK, lines.lines()
    assert "(by sound, 100%)" in "\n".join(lines.lines())
    assert lines.data["heard"][0]["by"] == "sound"


def test_line_windows_says_how_each_line_was_matched() -> None:
    english = line_windows(
        (Word(0.0, 0.5, "hello"), Word(0.5, 1.0, "there")), ("hello there",)
    )
    assert english[0].by == "words"
    shape = line_windows((Word(0.0, 0.5, "えがおで"),), ("えがおで",))
    assert shape[0].by == "shape"


# --- The command -----------------------------------------------------------------------------------------


def test_review_prints_one_block_and_exits_0_even_with_faults(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = _desk_ready(desk, facts=_facts((2.4,)))
    frames = _clean(36) + _halves(36, RED, GREEN, start=36)
    frames[50] = board_array()
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", frames)
    notes = desk / "ep01" / "run-notes.md"
    before = notes.read_text(encoding="utf-8") if notes.is_file() else None

    code = main(["review", "--desk", str(desk), "--take-file", str(take)])

    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("Review ep01 t1: take-ep01-t1-raw-v1.mp4 (raw,")
    for name in ("Loudness", "Cuts", "Frames", "Board", "Lines"):
        assert f" {name}: " in out
    assert f"{WARN} Cuts:" in out and f"{WARN} Board:" in out
    assert "Numbers only:" in out
    if before is not None:
        assert "Review ep01 t1" in notes.read_text(encoding="utf-8")


def test_review_json_and_a_missing_take(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = _desk_ready(desk)
    write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())

    assert main(["review", "--desk", str(desk), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "raw" and [s["name"] for s in payload["sections"]] == [
        "Loudness", "Cuts", "Frames", "Board", "Text", "Lines", "People",
    ]  # fmt: skip
    assert all(s["status"] in {OK, WARN, NONE} for s in payload["sections"])

    assert (
        main(["review", "--desk", str(desk), "--take-file", str(takes / "nope.mp4")])
        == 2
    )


def test_take_kind_reads_the_step_in_the_name() -> None:
    assert take_kind(Path("take-ep01-t1-raw-v1.mp4")) == "raw"
    assert take_kind(Path("take-ep01-t1-deboard-v2.mp4")) == "raw"
    assert take_kind(Path("take-ep01-t1-sokii-v1.mp4")) == "finished"
    assert take_kind(Path("take-ep01-t1-trim-v1.mp4")) == "finished"
    assert (
        take_kind(Path("take-ep01-t1-final-ja-captions-v1-sokii.mp4")) == "finished"
    )  # an adopted desk


# --- Integration with main: safe zones (#14), check-lines rows (#15), default file, --file ------------------------


def _captioned(path: Path, *, top: float | None) -> Path:
    """A moving blue/green take with a house-yellow caption box whose top edge sits at ``top`` of the height."""

    frames = _halves(72, BLUE, GREEN)
    if top is not None:
        y = round(H * top)
        for frame in frames:
            frame[y : y + 8, W // 4 : 3 * W // 4] = (255, 229, 0)
    return write_frames(path, frames)


def test_safe_zones_is_a_section_on_a_finished_take_only(desk: Path) -> None:
    takes = _desk_ready(desk)
    raw = _captioned(takes / "take-ep01-t1-raw-v1.mp4", top=0.02)
    covered = _captioned(takes / "take-ep01-t1-sokii-v1.mp4", top=0.02)
    in_band = _captioned(takes / "take-ep01-t1-sokii-v2.mp4", top=0.60)

    assert "Safe zones" not in [
        s.name for s in review_take(desk, take_file=raw).sections
    ]
    bad = _section(review_take(desk, take_file=covered), "Safe zones")
    good = _section(review_take(desk, take_file=in_band), "Safe zones")

    assert bad.status == WARN and "caption in the top 8%" in "\n".join(bad.lines())
    assert good.status == OK, good.lines()
    assert "faces by eye on the zone sheet" in good.threshold
    assert list(takes.glob("take-ep01-t1-sokii-v1-zones-v*.png")) and list(
        takes.glob("take-ep01-t1-sokii-v1-zones-v*.json")
    )


def test_the_default_file_is_the_newest_finished_one_else_the_raw_take(
    desk: Path,
) -> None:
    takes = _desk_ready(desk)
    write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    raw_only = review_take(desk)
    assert raw_only.take.name == "take-ep01-t1-raw-v1.mp4"
    assert "the newest raw take: no finished file yet" in raw_only.block()

    _captioned(takes / "take-ep01-t1-sokii-v1.mp4", top=0.60)
    write_frames(
        takes / "take-ep01-t1-deboard-v1.mp4", _clean()
    )  # newer, but not finished
    chosen = review_take(desk)
    assert chosen.take.name == "take-ep01-t1-sokii-v1.mp4"
    assert "the newest finished file" in chosen.block()


def test_file_is_an_alias_of_take_file(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = _desk_ready(desk)
    write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())
    other = write_frames(takes / "take-ep01-t1-deboard-v1.mp4", _clean())

    assert main(["review", "--desk", str(desk), "--file", str(other)]) == 0
    out = capsys.readouterr().out
    assert "take-ep01-t1-deboard-v1.mp4" in out and "given with --take-file" in out


def test_a_check_lines_speaker_flag_marks_the_lines_section(desk: Path) -> None:
    facts = {
        "shots": [
            {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 1.5, "speaks": True},
            {"shot_index": 2, "start_seconds": 1.5, "end_seconds": 3.0, "speaks": True},
        ],
        "lines": [
            {
                "line_id": "line_episode_01_01",
                "count": 1,
                "shot_index": 1,
                "start_seconds": 0.2,
                "end_seconds": 0.8,
            },
            {
                "line_id": "line_episode_01_02",
                "count": 1,
                "shot_index": 2,
                "start_seconds": 1.6,
                "end_seconds": 2.0,
            },
        ],
    }
    takes = _desk_ready(desk, facts=facts)
    take = write_frames(takes / "take-ep01-t1-raw-v1.mp4", _clean())

    lines = _section(review_take(desk, take_file=take), "Lines")

    text = "\n".join(lines.lines())
    assert "row 1, Hana in frame" in text  # check-lines' own row, as it prints it
    assert "!!" in text and "Ren is not in that row's frames" in text
    assert lines.status == WARN and lines.data["check_lines_flags"] == 1


def test_a_take_without_sound_is_flagged_not_a_crash(desk: Path) -> None:
    takes = _desk_ready(desk)
    silent = takes / "take-ep01-t1-sokii-v1.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:d=2:r=24",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)],
        check=True,
    )  # fmt: skip
    loud = _section(review_take(desk, take_file=silent), "Loudness")
    assert loud.status == WARN and loud.summary == "no audio track in the file"


# --- People ----------------------------------------------------------------------------------------------


def test_review_prints_the_people_section_with_names_from_the_saved_spine(
    desk: Path,
) -> None:
    facts = _facts((1.5,))
    facts["shots"][1]["people"] = {"count": 2, "named": ["cast_hana"], "unnamed": 1}
    takes = _desk_ready(desk, facts=facts)
    take = _cut_take(takes / "take-ep01-t1-raw-v1.mp4")

    review = review_take(desk, take_file=take)
    people = _section(review, "People")

    assert people.status == NONE
    assert (
        "shot 2 (1.50-3.00s): On screen: 2 people (Hana, + 1 unnamed)" in people.details
    )
    assert "People" in review.block()


def test_review_on_facts_without_head_counts_says_so_in_one_line(desk: Path) -> None:
    takes = _desk_ready(desk, facts=_facts((1.5,)))
    take = _cut_take(takes / "take-ep01-t1-raw-v1.mp4")

    people = _section(review_take(desk, take_file=take), "People")

    assert people.details == []
    assert "the server didn't send head counts" in people.summary
