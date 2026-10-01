"""Trim handles (fictora-drama #563): the kit sets them, finish cuts at them last, and readers move times.

The server keeps two handles per take on the filmed timeline and cuts picture
and sound at them only when it joins the episode; every take-facts time stays
on the filmed timeline. The kit does the same: ``trim --start/--end`` /
``--reset`` sets them (preview, then ``Applied`` / ``Refused``), ``finish``
lays everything on the take as filmed and cuts its record's files at the
handles last, and whatever reads take-facts times against a cut file
(``join``'s seam speech, ``review``, ``reel``) subtracts ``start_s``.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import make_take, needs_ffmpeg
from fake_api import FakeApi

from creation.cli_produce import main as produce_main
from creation.post.finish import run_finish
from creation.post.finish_record import latest_finish_record
from creation.post.join import JoinPart, part_speech
from creation.post.media import count_frames, probe_video
from creation.post.take_handles import (
    facts_on_handled_file,
    handles_from_facts,
    shift_ass,
)

TRIM = "/v1/video-generations/job_video_1/takes/2/trim"
FACTS_ROUTE = "/v1/jobs/job_take_t1/take-facts"
URL = "https://media.test/clip/original.mp4"


def _window(start: float, end: float, *, source: str = "auto") -> dict[str, Any]:
    return {
        "trim": {"start_s": start, "end_s": end, "source": source, "updated_at": None},
        "playable_window": {
            "media_url": URL,
            "is_original": True,
            "start_s": start,
            "end_s": end,
            "duration_s": round(end - start, 3),
            "take_duration_s": 15.0,
            "frame_rate": 24.0,
            "auto_start_s": 0.417,
            "auto_end_s": 15.0,
        },  # fmt: skip
    }


def _facts(
    start: float = 0.417, end: float = 15.0, *, source: str = "auto"
) -> dict[str, Any]:
    return {
        "take_facts": {
            "job_id": "job_take_t1",
            "duration_seconds": 15,
            "board_frames": {
                "head_frames": 10,
                "tail_frames": 0,
                "head_s": 0.417,
                "tail_s": 0.0,
                "frame_rate": 24.0,
                "timeline_shift_s": 0.0,
                "original": {
                    "url": URL,
                    "content_sha256": "a" * 64,
                    "content_length": 10,
                },
            },  # fmt: skip
            "soundtrack": {
                "mode": "target_audio",
                "lines": [
                    {"line_id": "l1", "start_s": 0.5, "end_s": 3.6},
                    {"line_id": "l3", "start_s": 11.5, "end_s": 13.3},
                ],
            },
            "sfx_cues": [{"sound": "door", "start_seconds": 12.0, "shot_index": 2}],
            **_window(start, end, source=source),
        }
    }


def _desk_with_take(desk: Path, facts: dict[str, Any] | None = None) -> None:
    api = desk / "ep01" / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "17_raw_scene_clips.json").write_text(
        json.dumps({"coordinator_job_id": "job_video_1", "coordinator_status": "completed", "clips": [
            {"job_id": "job_take_t2", "url": "https://media.test/t2.mp4", "set_index": 2, "episode_id": "episode_01"},
            {"job_id": "job_take_t1", "url": "https://media.test/t1.mp4", "set_index": 1, "episode_id": "episode_01"},
        ]}),
        encoding="utf-8",
    )  # fmt: skip
    (api / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts or _facts()), encoding="utf-8"
    )


def _answer(
    start: float, end: float, *, source: str = "creator", changed: bool = True
) -> dict[str, Any]:
    return {"schema_version": "fictora.drama-take-trim-response.v1", "job_id": "job_video_1", "take_index": 2,
            "take_job_id": "job_take_t1", "changed": changed, **_window(start, end, source=source)}  # fmt: skip


def _trim(desk: Path, *extra: str) -> int:
    return produce_main(
        ["trim", "--desk", str(desk), "--episode", "1", "--take", "t1", *extra]
    )


def _last(text: str) -> str:
    return [row for row in text.splitlines() if row.strip()][-1]


def _puts(api: FakeApi) -> list[tuple[dict[str, Any] | None, str | None]]:
    return [(body, key) for method, path, body, key in api.calls if method == "PUT"]


# --- trim --start/--end / --reset ------------------------------------------------------------------


def test_trim_preview_shows_the_change_and_sends_nothing(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _desk_with_take(desk)

    code = _trim(desk, "--start", "0.5", "--end", "10", "--preview")

    out = capsys.readouterr().out
    assert code == 0
    assert "take 2 of video job job_video_1" in out, (
        "the take's index is its place in the video job"
    )
    assert "now:  0.417-15.000 s (auto)" in out
    assert "new:  0.500-10.000 s (creator) -> plays 9.500 s of 15.000 s" in out
    assert "!! line l3 (11.50-13.30 s) is outside the window" in out
    assert "!! effect 'door' at 12.00 s is outside the window" in out
    assert _last(out).startswith("Not applied: --preview")
    assert _puts(api) == []


def test_trim_sends_the_handles_saves_the_facts_and_ends_on_applied(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _desk_with_take(desk)
    api.routes[("PUT", TRIM)] = _answer(0.5, 10.0)
    api.routes[("GET", FACTS_ROUTE)] = {
        "take_facts": _facts(0.5, 10.0, source="creator")["take_facts"]
    }

    code = _trim(desk, "--start", "0.51", "--end", "10")

    out = capsys.readouterr().out
    assert code == 0
    [(body, key)] = _puts(api)
    assert body == {"start_s": 0.5, "end_s": 10.0}, "snapped to the frame at 24 fps"
    assert key and "trim-ep01-t1" in key, "the route needs an Idempotency-Key"
    assert "0.51 s is between frames at 24 fps: sent as 0.500 s" in out
    assert "stored: 0.500-10.000 s (creator)" in out
    saved = desk / "ep01" / "api" / "take-facts-ep01-t1-v2.json"
    assert saved.is_file() and "take-facts-ep01-t1-v2.json" in out
    handles = handles_from_facts(json.loads(saved.read_text(encoding="utf-8")))
    assert handles is not None and (handles.start_s, handles.end_s, handles.source) == (
        0.5,
        10.0,
        "creator",
    )
    assert _last(out) == "Applied"


def test_trim_reset_sends_reset_alone(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _desk_with_take(desk)
    api.routes[("PUT", TRIM)] = _answer(0.417, 15.0, source="auto")
    api.routes[("GET", FACTS_ROUTE)] = {"take_facts": _facts()["take_facts"]}

    code = _trim(desk, "--reset")

    assert code == 0
    assert _puts(api)[0][0] == {"reset": True}
    assert _last(capsys.readouterr().out) == "Applied"


def test_a_window_under_a_second_is_refused_before_sending(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _desk_with_take(desk)

    code = _trim(desk, "--start", "2", "--end", "2.5")

    err = capsys.readouterr().err
    assert code == 2
    assert _last(err).startswith("Refused: ") and "at least 1 s" in err
    assert _puts(api) == []


def test_a_server_refusal_names_the_fix_and_ends_on_refused(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _desk_with_take(desk)

    def refuse(*_: Any) -> Any:
        raise SystemExit(
            f"HTTP 422 PUT {TRIM}: take_trim_not_frame_aligned: 0.51 is not on a frame (nearest 0.500, 0.542)"
        )

    api.routes[("PUT", TRIM)] = refuse

    code = _trim(desk, "--start", "0.5", "--end", "10")

    err = capsys.readouterr().err
    assert code == 2
    assert "fix: put each handle on a frame" in err
    assert _last(err).startswith("Refused: HTTP 422 PUT")


def test_a_server_without_the_route_says_so(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _desk_with_take(desk)

    def missing(*_: Any) -> Any:
        raise SystemExit(f"HTTP 404 PUT {TRIM}: Not Found")

    api.routes[("PUT", TRIM)] = missing

    assert _trim(desk, "--start", "0.5", "--end", "10") == 2
    assert "fictora-drama #563 is not deployed" in capsys.readouterr().err


def test_the_local_cut_still_needs_its_finished_file(
    desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _trim(desk, "--cut", "1-2") == 2
    assert "trim needs --take-file" in capsys.readouterr().err


# --- finish cuts at the handles last ---------------------------------------------------------------

TWO_LINES = ((1.0, 2.0, 440), (3.2, 4.0, 880))


def _onset(path: Path, *, after: float = 0.0) -> float:
    """Seconds of the first loud sample of a file's sound (a tone switching on)."""

    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    pcm = np.abs(np.frombuffer(raw, dtype=np.float32))
    start = int(after * 8000)
    loud = np.nonzero(pcm[start:] > 0.3 * float(pcm.max()))[0]
    return (start + int(loud[0])) / 8000 if len(loud) else -1.0


@needs_ffmpeg
def test_finish_cuts_the_record_files_at_the_handles_after_laying_everything(
    post_desk: Path,
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    facts = {"take_facts": {"job_id": "job_take_t1", "shots": [], "sfx_cues": [],
                            **_window(0.5, 4.5, source="creator")}}  # fmt: skip
    facts["take_facts"]["playable_window"]["take_duration_s"] = 5.0
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )
    out = io.StringIO()

    def bed(spine: dict, music: str | None, target: Path) -> Path:
        from conftest import make_tone

        return make_tone(target.with_suffix(".wav"), seconds=6.0, freq=220, volume=0.05)

    result = run_finish(post_desk, facts_fetcher=lambda *a: None, sfx_render=lambda c, t: t, bed_maker=bed,
                        colour=False, thumbnail=False, stream=out)  # fmt: skip

    assert result.complete, out.getvalue()
    assert "-handles-" in result.final.name, out.getvalue()
    record = latest_finish_record(post_desk, 1, "t1")
    assert record is not None and record.edits[-1]["op"] == "handles"
    assert (record.edits[-1]["start_s"], record.edits[-1]["end_s"]) == (0.5, 4.5)
    for role in ("pre_bed", "master", "final"):
        path = record.resolve(post_desk, role)
        assert path is not None and "handles" in path.name
        assert count_frames(path) == 96, f"{role}: 4.0 s at 24 fps"
        assert abs(probe_video(path).duration_seconds - 4.0) < 0.06
    pre_bed = record.resolve(post_desk, "pre_bed")
    assert pre_bed is not None
    assert _onset(pre_bed) == pytest.approx(0.5, abs=0.03), (
        "Kenji's line at 1.0 s filmed plays at 0.5 s"
    )
    assert "trim handles 0.500-4.500 s (creator)" in out.getvalue()


@needs_ffmpeg
def test_finish_cuts_nothing_when_the_handles_are_the_whole_take(
    post_desk: Path,
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    facts = {
        "take_facts": {
            "job_id": "job_take_t1",
            "shots": [],
            "sfx_cues": [],
            **_window(0.0, 5.0),
        }
    }
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )
    out = io.StringIO()

    def bed(spine: dict, music: str | None, target: Path) -> Path:
        from conftest import make_tone

        return make_tone(target.with_suffix(".wav"), seconds=6.0, freq=220, volume=0.05)

    result = run_finish(post_desk, facts_fetcher=lambda *a: None, sfx_render=lambda c, t: t, bed_maker=bed,
                        colour=False, thumbnail=False, stream=out)  # fmt: skip

    assert "handles" not in result.final.name
    assert "the whole take plays; nothing cut" in out.getvalue()


# --- readers on a cut file subtract start_s --------------------------------------------------------


def test_facts_on_a_cut_file_move_by_start_and_drop_what_is_outside() -> None:
    moved = facts_on_handled_file(
        _facts(), [{"op": "handles", "start_s": 0.5, "end_s": 10.0}]
    )

    assert moved is not None
    lines = moved["take_facts"]["soundtrack"]["lines"]
    assert lines == [{"line_id": "l1", "start_s": 0.0, "end_s": 3.1}], (
        "l3 at 11.5 s is past the end"
    )
    assert moved["take_facts"]["sfx_cues"] == [], "the door at 12 s is past the end"
    assert facts_on_handled_file(_facts(), [{"op": "soften"}]) == _facts(), (
        "no handles edit: unchanged"
    )


def test_join_leaves_out_speech_where_it_lands_on_the_cut_take(post_desk: Path) -> None:
    takes = post_desk / "ep01" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    (takes / "take-ep01-t1-raw-v1-words-v1.json").write_text(
        json.dumps(
            {
                "words": [
                    {"word": "Wait", "start": 1.0, "end": 1.5},
                    {"word": "late", "start": 9.0, "end": 9.4},
                ]
            }
        )
    )
    from creation.post.finish_record import FinishRecord

    record = FinishRecord(episode=1, take_id="t1", complete=True, pre_bed=None, master="m.mp4", final="f.mp4",
                          bed=None, bed_db=-20.0, duck_db=None,
                          edits=({"op": "handles", "start_s": 0.5, "end_s": 5.0},))  # fmt: skip
    part = JoinPart(
        episode=1,
        take_id="t1",
        picture=takes / "p.mp4",
        pre_bed=takes / "p.mp4",
        record=record,
    )

    windows, said = part_speech(post_desk, part, 4.5)

    assert windows == [(0.5, 1.0)], (
        "1.0-1.5 s filmed is 0.5-1.0 s on the cut take; 9.0 s is past its end"
    )
    assert "trim handles" in said


def test_burned_captions_move_with_the_cut(tmp_path: Path) -> None:
    ass = tmp_path / "m.ass"
    ass.write_text(
        "[Events]\n"
        "Dialogue: 0,0:00:00.20,0:00:00.80,Main,,0,0,0,,cut away\n"
        "Dialogue: 0,0:00:02.00,0:00:02.50,Main,,0,0,0,,stays, with a comma\n"
        "Dialogue: 0,0:00:09.80,0:00:10.40,Main,,0,0,0,,clipped\n",
        encoding="utf-8",
    )

    shift_ass(ass, tmp_path / "out.ass", start_s=0.5, end_s=10.0)

    rows = (tmp_path / "out.ass").read_text(encoding="utf-8").splitlines()
    assert rows[1] == "Dialogue: 0,0:00:00.00,0:00:00.30,Main,,0,0,0,,cut away"
    assert (
        rows[2] == "Dialogue: 0,0:00:01.50,0:00:02.00,Main,,0,0,0,,stays, with a comma"
    )
    assert rows[3] == "Dialogue: 0,0:00:09.30,0:00:09.50,Main,,0,0,0,,clipped"


@needs_ffmpeg
def test_review_of_a_cut_file_plans_its_shot_changes_minus_start(
    post_desk: Path,
) -> None:
    from creation.post.finish_record import write_finish_record
    from creation.post.review import review_take

    takes = post_desk / "ep01" / "takes"
    final = make_take(takes / "take-ep01-t1-handles-cover-v1.mp4", seconds=4.0)
    write_finish_record(post_desk, episode=1, take_id="t1", complete=True, pre_bed=final, master=final,
                        final=final, bed=None, bed_db=-20.0, duck_db=None,
                        edits=[{"op": "handles", "start_s": 0.5, "end_s": 4.5}])  # fmt: skip
    facts = {"take_facts": {"job_id": "j", "shots": [
        {"shot_index": 1, "start_seconds": 0.0, "end_seconds": 2.5},
        {"shot_index": 2, "start_seconds": 2.5, "end_seconds": 5.0},
    ]}}  # fmt: skip
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )

    result = review_take(post_desk, episode=1, take_id="t1", take_file=final)

    cuts = next(s for s in result.sections if s.name == "Cuts")
    assert cuts.data["planned"] == [2.0], (
        "the change at 2.5 s filmed is at 2.0 s on the cut file"
    )


@needs_ffmpeg
def test_reel_reads_a_cut_takes_events_minus_start(post_desk: Path) -> None:
    from creation.post.finish_record import FinishRecord
    from creation.post.reel import TakeSource, measure_take

    source = make_take(
        post_desk / "ep01" / "takes" / "take-ep01-t1-handles-prebed-v1.mp4", seconds=4.0
    )
    facts = {"take_facts": {"job_id": "j", "shots": [{"shot_index": 9, "start_seconds": 0.0, "end_seconds": 5.0}],
                            "sfx_cues": [
        {"sound": "slam", "kind": "event", "start_seconds": 3.0, "shot_index": 9},
        {"sound": "tap", "kind": "event", "start_seconds": 0.2, "shot_index": 9},
    ]}}  # fmt: skip
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(facts)
    )
    record = FinishRecord(episode=1, take_id="t1", complete=True, pre_bed=None, master="m.mp4", final="f.mp4",
                          bed=None, bed_db=-20.0, duck_db=None,
                          edits=({"op": "handles", "start_s": 0.5, "end_s": 4.5},))  # fmt: skip

    measured = measure_take(post_desk, 1, TakeSource("t1", source, None, record, None))

    assert measured.events == (2.5,), (
        "the slam at 3.0 s filmed is at 2.5 s; the tap at 0.2 s was cut away"
    )
