"""The music is the harness's: the kit lays only harness-made beds, never doubles music the take carries.

- A bed pinned by hand (the old ``set-bed --path``) is ignored; the bed comes
  from the spine or is made on the server.
- ``--music`` and ``music-note`` save a change note for the harness; nothing is
  made or picked from it.
- A take whose facts say the harness's music is in its soundtrack
  (``soundtrack.music.laid``) gets no bed; one whose facts say the server laid
  the location's ambience (``soundtrack.ambience.laid``) gets no kit ambience
  and no room tone. Absent blocks read as not laid.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import make_take, make_tone, needs_ffmpeg
from test_post_finish import TWO_LINES, fake_sfx
from test_post_join import finished_take, noise_bed
from test_target_audio_soundtrack import NATIVE, TARGET, facts_with

from creation import cli_post
from creation.cli_produce import main
from creation.ops.state import load_series, save_series
from creation.post.bed import (
    MUSIC_IS_HARNESS,
    harness_bed,
    music_notes,
    pinned_bed,
    record_music_note,
    resolve_bed,
)
from creation.post.finish import AMBIENCE_STEP, ROOM_TONE_STEP, run_finish
from creation.post.finish_record import latest_finish_record, write_finish_record
from creation.post.join import run_join
from creation.post.soundtrack import soundtrack_from


def _made_bed(calls: list[str | None]):
    def make(_spine: dict, brief: str | None, target: Path) -> Path:
        calls.append(brief)
        return make_tone(target.with_suffix(".wav"), seconds=6.0, freq=220, volume=0.9)

    return make


def _never(*_: object) -> Path:
    raise AssertionError("no bed may be found, made or laid on this take")


def _hand_pin(desk: Path) -> Path:
    mine = make_tone(desk / "my-own-track.wav", seconds=2.0, freq=330, volume=0.5)
    series = load_series(desk)
    series.bed_path = str(mine)
    save_series(desk, series)
    return mine


def _desk_take(desk: Path, facts: dict) -> Path:
    raw = make_take(
        desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES
    )
    (desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(json.dumps(facts))
    return raw


# --- the contract ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("block", "laid"),
    [
        (None, False),
        ({"laid": True}, True),
        ({"laid": False, "reason": "x"}, False),
        ({"laid": "yes"}, False),
        ("x", False),
    ],
)
def test_ambience_and_music_laid_are_read_and_absent_is_not_laid(
    block: object, laid: bool
) -> None:
    soundtrack = dict(TARGET)
    if block is not None:
        soundtrack["ambience"] = block
        soundtrack["music"] = block

    read = soundtrack_from(facts_with(soundtrack))

    assert read.ambience_laid is laid and read.music_laid is laid
    assert soundtrack_from(facts_with(NATIVE)).music_laid is False
    assert soundtrack_from(facts_with(None)).ambience_laid is False


# --- only harness beds ------------------------------------------------------------------------------------------


def test_a_hand_pinned_file_is_never_the_bed(post_desk: Path) -> None:
    mine = _hand_pin(post_desk)
    calls: list[str | None] = []

    assert not harness_bed(post_desk, mine)
    assert pinned_bed(post_desk) is None
    bed = resolve_bed(post_desk, spine={"spine_id": "spine-1"}, maker=_made_bed(calls))

    assert harness_bed(post_desk, bed.path) and bed.path.name.startswith("show-bed-v")
    assert "ignored the hand-pinned `my-own-track.wav`" in bed.source
    assert calls == [None], (
        "the server writes the brief from the genre; the kit never sends one"
    )
    assert pinned_bed(post_desk) == bed.path


def test_the_spine_bed_and_a_made_bed_are_harness_beds(post_desk: Path) -> None:
    beds = post_desk / "shared" / "beds"
    beds.mkdir(parents=True, exist_ok=True)
    for name in ("series-bed-v1.mp3", "show-bed-v3.mp3"):
        assert harness_bed(post_desk, beds / name)
    for name in ("my-bed.mp3", "show-bed-final.mp3", "nested/show-bed-v1.mp3"):
        assert not harness_bed(post_desk, beds / name)


# --- change notes, not choices ----------------------------------------------------------------------------------


def test_music_notes_are_saved_scoped_and_never_empty(post_desk: Path) -> None:
    record_music_note(post_desk, "calmer", via="music-note")
    record_music_note(
        post_desk, "  quieter   under the lines ", episode=1, take_id="t2"
    )
    record_music_note(post_desk, "more tension", episode=2)

    assert music_notes(post_desk, episode=1, take_id="t1") == ["calmer (show)"]
    assert music_notes(post_desk, episode=1, take_id="t2") == [
        "calmer (show)",
        "quieter under the lines (ep01 t2)",
    ]
    assert music_notes(post_desk, episode=2, take_id="t1") == [
        "calmer (show)",
        "more tension (ep02)",
    ]
    with pytest.raises(ValueError, match="needs words"):
        record_music_note(post_desk, "   ")


def test_set_bed_is_gone_and_music_note_saves_the_change(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    track = make_tone(post_desk / "track.wav", seconds=1.0, freq=330, volume=0.5)

    assert main(["set-bed", "--desk", str(post_desk), "--path", str(track)]) == 2
    assert MUSIC_IS_HARNESS in capsys.readouterr().out
    assert load_series(post_desk).bed_path is None, "nothing was pinned"

    assert (
        main(
            [
                "music-note",
                "--desk",
                str(post_desk),
                "--episode",
                "1",
                "--take",
                "t1",
                "calmer",
                "--save-only",
            ]
        )
        == 0
    )
    assert music_notes(post_desk, episode=1, take_id="t1") == ["calmer (ep01 t1)"]
    assert (
        "set-bed" in cli_post.POST_COMMANDS and "music-note" in cli_post.POST_COMMANDS
    )


# --- finish -----------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_finish_music_is_a_note_for_the_harness_and_the_bed_is_the_servers(
    post_desk: Path,
) -> None:
    _desk_take(post_desk, facts_with(NATIVE))
    _hand_pin(post_desk)
    calls: list[str | None] = []
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=_made_bed(calls), facts_fetcher=lambda *a: None,
        music="softer piano, less drums", thumbnail=False, stream=out,
    )  # fmt: skip

    assert result.complete, out.getvalue()
    assert calls == [None], "the note never becomes a brief"
    bed = next(s for s in result.steps if s.step == "bed")
    assert "made" in bed.detail and "ignored the hand-pinned" in bed.detail
    assert result.music_notes == ("softer piano, less drums (ep01 t1)",)
    assert "Music change notes for the harness" in out.getvalue()
    assert result.as_json()["music_notes"] == ["softer piano, less drums (ep01 t1)"]


@needs_ffmpeg
def test_a_take_carrying_the_harness_music_gets_no_bed(post_desk: Path) -> None:
    _desk_take(post_desk, facts_with({**NATIVE, "music": {"laid": True}}))
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=_never, facts_fetcher=lambda *a: None,
        thumbnail=False, stream=out,
    )  # fmt: skip

    assert result.complete, out.getvalue()
    steps = {s.step: s for s in result.steps}
    assert steps["bed"].status == "skipped" and "music.laid" in steps["bed"].detail
    assert "no bed: the harness's music is in the take" in steps["mix"].detail
    assert "NO MUSIC BED" not in steps["mix"].detail
    assert "music ✓ (in the take, from the harness)" in result.sound_line()
    assert load_series(post_desk).bed_path is None
    record = latest_finish_record(post_desk, 1, "t1")
    assert record is not None and record.music_in_take and record.bed is None


@needs_ffmpeg
def test_a_locked_take_with_server_ambience_and_music_gets_neither_again(
    post_desk: Path,
) -> None:
    _desk_take(
        post_desk,
        facts_with({**TARGET, "ambience": {"laid": True}, "music": {"laid": True}}),
    )
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=_never, facts_fetcher=lambda *a: None,
        ambience_maker=_never, cut_meter=lambda _take: (3.9,), thumbnail=False, stream=out,
    )  # fmt: skip

    assert result.complete and not result.stopped, out.getvalue()
    steps = {s.step: s for s in result.steps}
    assert (
        steps[AMBIENCE_STEP].status == "skipped"
        and "ambience.laid" in steps[AMBIENCE_STEP].detail
    )
    assert steps[ROOM_TONE_STEP].output is None, (
        "no room tone over the server's ambience"
    )
    assert "server's location ambience" in steps[ROOM_TONE_STEP].detail
    assert steps["bed"].status == "skipped"
    assert "ambience ✓ (laid by the server)" in result.sound_line()
    assert "!! no location ambience" not in out.getvalue()


@needs_ffmpeg
@pytest.mark.parametrize(
    ("facts", "bedded"),
    [
        ({**NATIVE}, True),
        (None, True),
    ],
)
def test_without_a_music_fact_the_bed_is_laid_as_before(
    post_desk: Path, facts: dict | None, bedded: bool
) -> None:
    _desk_take(post_desk, facts_with(facts))
    calls: list[str | None] = []

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=_made_bed(calls),
        facts_fetcher=lambda *a: None, thumbnail=False, stream=io.StringIO(),
    )  # fmt: skip

    assert result.complete
    assert (next(s for s in result.steps if s.step == "bed").status == "ran") is bedded
    assert not result.music_in_take


@needs_ffmpeg
@pytest.mark.parametrize("scored", [True, False])
def test_a_take_the_model_scored_gets_no_bed(post_desk: Path, scored: bool) -> None:
    """``model_music: true`` (fictora-drama #569): the model was asked for the genre's music."""

    facts = facts_with(NATIVE)
    facts["take_facts"]["model_music"] = scored
    _desk_take(post_desk, facts)
    calls: list[str | None] = []

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), bed_maker=_made_bed(calls) if not scored else _never,
        facts_fetcher=lambda *a: None, thumbnail=False, stream=io.StringIO(),
    )  # fmt: skip

    assert result.complete
    bed = next(s for s in result.steps if s.step == "bed")
    if scored:
        assert bed.status == "skipped" and "model_music" in bed.detail
        assert result.music_in_take and "music ✓ (in the take" in result.sound_line()
        record = latest_finish_record(post_desk, 1, "t1")
        assert record is not None and record.music_in_take
    else:
        assert bed.status == "ran" and calls == [None]
        assert not result.music_in_take


# --- join -------------------------------------------------------------------------------------------------------


def _music_in_take(desk: Path, take_id: str, *, grey: int) -> None:
    files = finished_take(desk, 1, take_id, grey=grey, tone=0.2)
    write_finish_record(
        desk, episode=1, take_id=take_id, complete=True, pre_bed=files["pre_bed"], master=files["master"],
        final=files["final"], bed=None, bed_db=-16.5, duck_db=None, music_in_take=True,
    )  # fmt: skip


@pytest.fixture
def join_desk(tmp_path: Path) -> Path:
    from creation.ops.floor import init_series_desk

    return init_series_desk(tmp_path, "Join Music", band="30s", episode_count=1)


@needs_ffmpeg
def test_join_lays_no_bed_over_takes_that_carry_the_harness_music(
    join_desk: Path,
) -> None:
    _music_in_take(join_desk, "t1", grey=70)
    _music_in_take(join_desk, "t2", grey=90)
    out = io.StringIO()

    result = run_join(join_desk, episodes=(1,), stream=out)

    assert result.complete, out.getvalue()
    assert result.bed is None
    assert "every take carries the harness's music" in "\n".join(result.summary_lines())


@needs_ffmpeg
def test_join_refuses_takes_with_music_mixed_with_bedded_takes(join_desk: Path) -> None:
    _music_in_take(join_desk, "t1", grey=70)
    finished_take(join_desk, 1, "t2", grey=90)
    noise_bed(join_desk)

    with pytest.raises(ValueError, match="double the music"):
        run_join(join_desk, episodes=(1,), stream=io.StringIO())


@needs_ffmpeg
def test_join_never_lays_a_hand_pinned_file(join_desk: Path) -> None:
    finished_take(join_desk, 1, "t1", grey=70)
    finished_take(join_desk, 1, "t2", grey=90)
    _hand_pin(join_desk)

    with pytest.raises(ValueError, match="no harness bed"):
        run_join(join_desk, episodes=(1,), stream=io.StringIO())


# --- effects the server laid in the track ---------------------------------------------------------------------


def test_effects_the_server_laid_are_read_from_the_take_facts() -> None:
    sfx = {"cues": [
        {"sound": "A door SLAMS!", "laid": True},
        {"sound": "a cup clinks", "laid": False},
        {"laid": True},
    ]}  # fmt: skip

    read = soundtrack_from(facts_with({**TARGET, "sfx": sfx}))

    assert read.sfx_laid == frozenset({"a door slams"})
    assert "1 effect(s) are in the track" in read.one_line()
    assert soundtrack_from(facts_with(TARGET)).sfx_laid == frozenset()


def test_a_locked_take_never_gets_an_effect_the_server_already_laid(
    post_desk: Path,
) -> None:
    _desk_take(
        post_desk,
        facts_with(
            {
                **TARGET,
                "ambience": {"laid": True},
                "music": {"laid": True},
                "sfx": {"cues": [{"sound": "a door slams", "laid": True}]},
            }
        ),  # fmt: skip
    )
    rendered: list[str] = []
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx(rendered), bed_maker=_never, facts_fetcher=lambda *a: None,
        ambience_maker=_never, cut_meter=lambda _take: (3.9,), thumbnail=False, stream=out,
    )  # fmt: skip

    assert rendered == [], (
        "the server's door slam is in the track; laying it again doubles it"
    )
    steps = {s.step: s for s in result.steps}
    assert "already in the track" in steps["sfx"].detail, out.getvalue()
