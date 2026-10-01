"""finish lays and captions the episode's inner-voice cues; the plate count skips an unused character.

Hana inner voice, ep 1 (2026-09-30): a thought saved with `inner-voice` was silent in the finished
file (no dry line, nothing laid, no caption), and the kit expected one plate too many for a voice
whose only line was removed (fictora-drama #517 draws none).
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import SPINE, make_take, make_tone, needs_ffmpeg

from creation.desk_media_urls import drawn_cast_rows
from creation.inner_voice import take_cues
from creation.ops.state import load_series
from creation.post import handmade
from creation.post.audio_service import AudioServiceError
from creation.post.finish import INNER_VOICE_STEP, run_finish
from creation.post.media import measure_rms_windows
from creation.post.sfx import SfxCue
from creation.stranded_voice import unlooked_unused_cast_ids, unused_cast_ids

#: Kenji at 1-2 s, Aya at 3.2-4 s (the spine's two lines).
TWO_LINES = ((1.0, 2.0, 440), (3.2, 4.0, 880))
THOUGHT = "He never looks back."


def _cue(
    cue_id: str, start: float, end: float, *, who: str = "cast_aya", line: str = THOUGHT
) -> dict[str, Any]:
    return {"cue_id": cue_id, "start_ms": int(start * 1000), "end_ms": int(end * 1000),
            "speaker_cast_id": who, "line": line}  # fmt: skip


# --- which take a cue falls in, and where on it -----------------------------------------------------


def test_a_one_take_episode_lays_its_cues_where_they_are_and_names_one_past_the_end() -> (
    None
):
    plan = take_cues(
        [_cue("iv_ep01_01", 4.3, 4.9), _cue("iv_ep01_02", 5.4, 6.0)],
        take=1,
        lengths=[5.0],
        last_filmed=True,
    )

    assert [(c.cue_id, c.start, c.end) for c in plan.cues] == [("iv_ep01_01", 4.3, 4.9)]
    assert plan.offset == 0.0
    (problem,) = plan.problems
    assert problem.startswith(
        "iv_ep01_02 at 5.40s starts after the last filmed take (t1) ends at 5.00s"
    )


def test_on_a_two_take_episode_take_2_starts_where_take_1_ends() -> None:
    cues = [
        _cue("iv_ep01_01", 1.0, 2.0),
        _cue("iv_ep01_02", 4.5, 6.0),  # straddles the seam at 5.0 s
        _cue("iv_ep01_03", 6.2, 7.0),
        _cue("iv_ep01_04", 11.0, 12.0),  # after t2 ends at 10.0 s
    ]

    first = take_cues(cues, take=1, lengths=[5.0], last_filmed=False)
    second = take_cues(cues, take=2, lengths=[5.0, 5.0], last_filmed=True)

    assert [c.cue_id for c in first.cues] == ["iv_ep01_01", "iv_ep01_02"]
    assert first.problems == (), (
        "a later take is filmed: a cue past t1 is not t1's problem"
    )
    seam = first.cues[1]
    assert (seam.start, seam.end) == (4.5, 6.0)
    assert (
        "past the seam at 5.00s" in seam.seam
        and "laid on t1, where it starts" in seam.seam
    )
    assert first.cues[0].seam == ""
    assert [(c.cue_id, c.start, c.end, c.episode_start) for c in second.cues] == [
        ("iv_ep01_03", 1.2, 2.0, 6.2)
    ], "the straddling cue is laid once, on t1; t2 counts from 5.0 s"
    assert second.offset == 5.0
    assert [p.split(" ")[0] for p in second.problems] == ["iv_ep01_04"]


def test_take_2_uses_take_1s_measured_length_not_the_clip_length() -> None:
    plan = take_cues(
        [_cue("iv_ep01_01", 4.5, 5.0)], take=2, lengths=[4.2, 5.0], last_filmed=True
    )

    assert [(c.cue_id, c.start) for c in plan.cues] == [("iv_ep01_01", 0.3)]


def test_an_earlier_take_missing_from_the_desk_names_every_cue_that_may_be_this_takes() -> (
    None
):
    plan = take_cues(
        [_cue("iv_ep01_01", 6.2, 7.0)], take=2, lengths=[None, 5.0], last_filmed=True
    )

    assert plan.cues == ()
    (problem,) = plan.problems
    assert "t1 has no raw take on the desk, so where t2 starts is unknown" in problem
    assert plan.offset is None


# --- finish on a real take ----------------------------------------------------------------------------

FACTS = {
    "job_id": "job_video_scene_1",
    "take_facts": {
        "job_id": "job_video_scene_1",
        "shots": [{"shot_index": 1, "start_seconds": 0.0, "end_seconds": 5.0, "speaks": True}],
        "sfx_cues": [{"shot_index": 1, "sound": "a door slams", "kind": "event", "start_seconds": 2.4,
                      "duration_seconds": 0.5}],
    },
}  # fmt: skip


class FakeAudio:
    """The server's ``voice-lines`` route: records each request."""

    def __init__(self, refuse: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.refuse = refuse

    def voice_line(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.refuse:
            raise AudioServiceError(self.refuse)
        return {"audio_url": "https://media.test/thought.mp3", "seconds": 0.5, "provider_voice": "Laura",
                "reading": {"checked": True, "read_right": True}, "cost_usd": 0.002}  # fmt: skip


def _sfx(cue: SfxCue, target: Path) -> Path:
    return make_tone(target, seconds=cue.seconds, freq=300, volume=0.8)


def _bed(spine: dict, music: str | None, target: Path) -> Path:
    return make_tone(target.with_suffix(".wav"), seconds=12.0, freq=220, volume=0.9)


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fetched: list[str] = []

    def fake_download(url: str, dest: Path) -> Path:
        fetched.append(url)
        return make_tone(dest, seconds=0.5, freq=1200, volume=0.6)

    monkeypatch.setattr(handmade, "download", fake_download)
    return fetched


def _desk_with_cues(
    post_desk: Path, cues: list[dict[str, Any]], *, spine: dict[str, Any] | None = None
) -> None:
    body = copy.deepcopy(spine or SPINE)
    body["episode_summaries"][0]["inner_voice"] = cues
    (post_desk / "ep01" / "api" / "03_spine.json").write_text(
        json.dumps(body), encoding="utf-8"
    )
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )


def _finish(post_desk: Path, audio: FakeAudio, **kwargs: Any):
    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=_sfx, bed_maker=_bed, facts_fetcher=lambda *a: None,
                        voice_audio=audio, stream=out, **kwargs)  # fmt: skip
    return result, out.getvalue()


def _step(result, name: str):
    return next(s for s in result.steps if s.step == name)


@needs_ffmpeg
def test_finish_makes_the_dry_line_lays_it_at_the_cue_and_captions_it_in_georgia_italic(
    post_desk: Path, downloads: list[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    make_take(takes / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    _desk_with_cues(post_desk, [_cue("iv_ep01_01", 4.3, 4.9)])
    audio = FakeAudio()

    result, text = _finish(post_desk, audio)

    assert result.complete, text
    assert [s.step for s in result.steps][:3] == ["deboard", INNER_VOICE_STEP, "sfx"]
    step = _step(result, INNER_VOICE_STEP)
    assert step.status == "ran" and step.output is not None
    assert "iv_ep01_01" in step.detail and "@4.30s" in step.detail
    assert "t1 starts at 0.00s on the episode" in step.detail
    # The dry line is made on the server's voice-lines route in Aya's locked voice, never with a key here.
    ((call,),) = [audio.calls]
    assert (
        call["cast_id"] == "cast_aya"
        and call["text"] == THOUGHT
        and call["language"] == "en"
    )
    assert call["key"] == handmade.voice_line_key(
        episode=1, cast_id="cast_aya", text=THOUGHT, voice="Laura", language="en"
    ), "the key voice-line and revoice send for the same line"
    # Laid at 4.3 s into the take's own audio: silent there before, loud after.
    before = measure_rms_windows(takes / "take-ep01-t1-raw-v1.mp4", window_seconds=0.1)
    after = measure_rms_windows(step.output, window_seconds=0.1)
    assert before[44] < -60 and after[44] > -40
    assert after[26] < -60, "nothing else moves"
    # Captioned where it plays, in the heard-not-seen Georgia italic; the script lines keep their timing.
    ass = sorted(takes.glob("take-ep01-t1-cap-v*.ass"))[-1].read_text()
    thought = [
        row
        for row in ass.splitlines()
        if row.startswith("Dialogue:") and ",Italic," in row
    ]
    assert thought and all(",Italic," in row for row in thought)
    assert thought[0].split(",")[1] == "0:00:04.30"
    assert "He" in thought[0]
    assert not any(",Italic," in row and "Wait" in row for row in ass.splitlines())
    captions = _step(result, "captions")
    assert "'He never looks back.' (laid)" in captions.detail
    assert "3.2" in captions.detail.split("'Not tonight.' (speech)")[0][-14:], (
        captions.detail
    )
    # Booked like voice-line, on this take; the finish record names what was laid.
    assert [
        (e.unit, e.take_id)
        for e in load_series(post_desk).spend_log
        if e.unit.startswith("voice")
    ] == [("voice-line:aya", "t1")]
    record = json.loads(
        sorted(takes.glob("take-ep01-t1-finish-v*.json"))[-1].read_text()
    )
    assert [(c["cue_id"], c["start"]) for c in record["inner_voice"]] == [
        ("iv_ep01_01", 4.3)
    ]
    assert "inner voice ✓" in result.sound_line()
    assert "$" not in text, "cost never reaches printed output"


@needs_ffmpeg
def test_a_second_finish_reuses_the_dry_line_and_pays_nothing(
    post_desk: Path, downloads: list[str]
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    _desk_with_cues(post_desk, [_cue("iv_ep01_01", 4.3, 4.9)])
    audio = FakeAudio()

    _finish(post_desk, audio)
    again, text = _finish(post_desk, audio)

    assert again.complete, text
    assert len(audio.calls) == 1, (
        "the line on the desk answers the second finish: no second call"
    )
    assert len(downloads) == 1
    booked = [e for e in load_series(post_desk).spend_log if e.unit.startswith("voice")]
    assert len(booked) == 1, "booked once"
    assert "1 dry line(s) reused from the desk" in _step(again, INNER_VOICE_STEP).detail
    assert _step(again, INNER_VOICE_STEP).cost_usd == 0.0


@needs_ffmpeg
def test_a_refusal_from_the_route_names_the_cue_and_the_rest_of_the_take_still_finishes(
    post_desk: Path, downloads: list[str]
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    _desk_with_cues(post_desk, [_cue("iv_ep01_01", 4.3, 4.9)])
    audio = FakeAudio(refuse="HTTP 409 operator_audio_unavailable -> tell engineering")

    result, text = _finish(post_desk, audio)

    assert not result.complete
    assert result.sound_missing == (INNER_VOICE_STEP,)
    status = {s.step: s.status for s in result.steps}
    assert all(
        status[name] == "ran" for name in ("sfx", "bed", "mix", "captions", "watermark")
    ), status
    (why,) = result.inner_voice_not_laid
    assert why.startswith("iv_ep01_01 (Aya): HTTP 409 operator_audio_unavailable")
    assert "NOT DONE" in text and "inner voice ✗" in text
    assert downloads == [] and not list((post_desk / "ep01" / "voices").glob("*.mp3"))
    assert not [
        e for e in load_series(post_desk).spend_log if e.unit.startswith("voice")
    ]


@needs_ffmpeg
def test_a_thinker_with_no_locked_voice_is_named_and_nothing_is_asked_of_the_server(
    post_desk: Path, downloads: list[str]
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    spine = copy.deepcopy(SPINE)
    spine["cast"][1]["voice_brief"] = {}
    _desk_with_cues(post_desk, [_cue("iv_ep01_01", 4.3, 4.9)], spine=spine)
    audio = FakeAudio()

    result, text = _finish(post_desk, audio)

    assert audio.calls == []
    assert not result.complete and result.sound_missing == (INNER_VOICE_STEP,)
    assert "iv_ep01_01 (Aya): Aya has no locked voice" in result.inner_voice_not_laid[0]
    assert _step(result, "watermark").status == "ran"


@needs_ffmpeg
def test_take_2_lays_a_cue_at_its_time_less_take_1s_measured_length(
    post_desk: Path, downloads: list[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    make_take(takes / "take-ep01-t1-raw-v1.mp4", seconds=4.0, tones=((1.0, 2.0, 440),))
    make_take(takes / "take-ep01-t2-raw-v1.mp4", tones=TWO_LINES)
    _desk_with_cues(
        post_desk, [_cue("iv_ep01_01", 3.6, 4.4), _cue("iv_ep01_02", 8.3, 8.9)]
    )
    audio = FakeAudio()

    result, _text = _finish(post_desk, audio, take_id="t2")

    step = _step(result, INNER_VOICE_STEP)
    assert "t2 starts at 4.00s on the episode" in step.detail
    assert "iv_ep01_02" in step.detail and "@4.30s" in step.detail
    assert "iv_ep01_01" not in step.detail, (
        "a cue that starts in t1 stays on t1, even across the seam"
    )
    assert result.inner_voice_not_laid == ()


# --- the plate count ------------------------------------------------------------------------------


def _plated(**card: Any) -> dict[str, Any]:
    return {
        "cast": [
            {"cast_id": "cast_hana", "name": "Hana", "visual_brief": {"face": "round"}},
            {"cast_id": "cast_voice", "name": "Hana (inner voice)", **card},
        ],
        "beats": [
            {
                "dialogue_lines": [
                    {"line_id": "l1", "cast_id": "cast_hana", "text": "Hi."}
                ]
            },
        ],
        "episode_summaries": [{"episode_id": "episode_01", "inner_voice": []}],
        "frames": [{"frame_id": "f1", "cast_refs": ["cast_hana"]}],
    }


def _ids(spine: dict[str, Any]) -> list[str]:
    return [row["cast_id"] for row in drawn_cast_rows(spine)]


def test_a_character_nobody_sees_or_hears_with_no_look_owes_no_plate() -> None:
    spine = _plated(voice_only=False)

    assert unused_cast_ids(spine) == {"cast_voice"}
    assert _ids(spine) == ["cast_hana"], (
        "the server's drawn_cast skips them (fictora-drama #517)"
    )


def test_an_unused_look_owes_no_plate_once_frames_exist() -> None:
    """Sighted ep 1 (L-20261001-2 / -4): a narrator with a full look and no line left.

    The server's drawn_cast now skips every unused character once the spine has
    frames, brief or not, so the kit's plate count must not count him either.
    """

    spine = _plated(visual_brief={"face": "sharp"})

    assert unused_cast_ids(spine) == {"cast_voice"}
    assert unlooked_unused_cast_ids(spine) == frozenset()
    assert _ids(spine) == ["cast_hana"]


def test_before_any_frame_nobody_is_unused() -> None:
    spine = _plated()
    spine["frames"] = []

    assert unused_cast_ids(spine) == frozenset()
    assert _ids(spine) == ["cast_hana", "cast_voice"]


@pytest.mark.parametrize(
    "reach",
    ["line", "vocalization", "inner_voice", "cast_refs", "subject_blocking"],
)
def test_any_line_sound_thought_or_frame_keeps_the_character_counted(
    reach: str,
) -> None:
    spine = _plated()
    if reach == "line":
        spine["beats"][0]["dialogue_lines"].append(
            {
                "line_id": "l2",
                "cast_id": "cast_voice",
                "text": "Hm.",
                "off_screen": False,
            }
        )
    elif reach == "vocalization":
        spine["beats"][0]["vocalization"] = {"cast_id": "cast_voice", "kind": "gasp"}
    elif reach == "inner_voice":
        spine["episode_summaries"][0]["inner_voice"] = [
            _cue("iv_ep01_01", 1.0, 2.0, who="cast_voice")
        ]
    elif reach == "cast_refs":
        spine["frames"][0]["cast_refs"].append("cast_voice")
    else:
        spine["frames"][0]["visual_brief"] = {
            "subject_blocking": [{"cast_id": "cast_voice"}]
        }

    assert unused_cast_ids(spine) == frozenset()
    assert _ids(spine) == ["cast_hana", "cast_voice"]


def test_the_servers_voice_only_flag_still_skips_a_voice() -> None:
    spine = _plated(voice_only=True)
    spine["beats"][0]["dialogue_lines"].append(
        {"line_id": "l2", "cast_id": "cast_voice", "text": "Hm.", "off_screen": True}
    )

    assert _ids(spine) == ["cast_hana"]


# --- a Japanese thought under an English caption (Hanakaze ep 7) ------------------------------------


@needs_ffmpeg
def test_a_thought_says_its_spoken_words_and_is_captioned_in_english_italic(
    post_desk: Path, downloads: list[str]
) -> None:
    from creation.inner_voice import save_spoken

    takes = post_desk / "ep01" / "takes"
    make_take(takes / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    _desk_with_cues(post_desk, [_cue("iv_ep01_01", 4.3, 4.9)])
    save_spoken(
        post_desk, 1, cue_id="iv_ep01_01", line=THOUGHT, spoken_text="振り返らない。"
    )
    audio = FakeAudio()

    result, text = _finish(post_desk, audio)

    assert result.complete, text
    ((call,),) = [audio.calls]
    assert call["text"] == THOUGHT and call["spoken_text"] == "振り返らない。", (
        "the voice says the spoken words, as voice-line --text/--spoken-text sends them"
    )
    step = _step(result, INNER_VOICE_STEP)
    assert (
        "says '振り返らない。' under the caption 'He never looks back.'" in step.detail
    )
    ass = sorted(takes.glob("take-ep01-t1-cap-v*.ass"))[-1].read_text()
    thought = [
        row
        for row in ass.splitlines()
        if row.startswith("Dialogue:") and ",Italic," in row
    ]
    assert thought and thought[0].split(",")[1] == "0:00:04.30"
    assert "He" in thought[0] and "振" not in ass, (
        "captioned in English, heard-not-seen italic"
    )
    assert "NOT ENGLISH" not in _step(result, "captions").detail
    record = json.loads(
        sorted(takes.glob("take-ep01-t1-finish-v*.json"))[-1].read_text()
    )
    assert record["inner_voice"][0]["spoken_text"] == "振り返らない。"


@needs_ffmpeg
def test_a_thought_written_in_japanese_with_no_caption_is_named(
    post_desk: Path, downloads: list[str]
) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    _desk_with_cues(post_desk, [_cue("iv_ep01_01", 4.3, 4.9, line="振り返らない。")])

    result, _text = _finish(post_desk, FakeAudio())

    assert (
        "!! iv_ep01_01: the thought is not English, so it plays uncaptioned"
        in _step(result, INNER_VOICE_STEP).detail
    )
