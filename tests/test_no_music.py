"""No music in delivered videos (founder decision, 20 Oct 2026): voice, sound effects and room sound only.

Every video the kit delivers (``finish``, ``join``, the Instagram reel and the
TikTok part) ships with no music bed, so a trending sound can be added inside
Instagram or TikTok at posting. Captions stay on for every Instagram and TikTok
video. A "with score" version is the server's to make on request; the kit lays,
finds, makes and sends no bed, even on a desk that has a show bed pinned from
before.

- A take whose facts say the harness's music is baked in and whose music-free
  stem is stored gets that stem (by mixing, not filming) on every show.
- Music that cannot be taken out (the video model's) is named with a ``!!``
  line and ``music !!``; the take still finishes.
- ``--music`` and ``music-note`` still save a change note for the harness.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import make_take, make_tone, needs_ffmpeg
from test_post_finish import TWO_LINES, fake_sfx
from test_post_join import finished_take
from test_target_audio_soundtrack import NATIVE, TARGET, facts_with

from creation import cli_post
from creation.captions import posting_caption_style
from creation.cli_produce import main
from creation.ops.state import load_series, save_series
from creation.post import finish as finish_module
from creation.post import join as join_module
from creation.post.bed import NO_MUSIC_RULE, music_notes, record_music_note
from creation.post.finish import AMBIENCE_STEP, ROOM_TONE_STEP, run_finish
from creation.post.finish_record import latest_finish_record, write_finish_record
from creation.post.join import run_join
from creation.post.soundtrack import soundtrack_from
from creation.production_config import load_production_config, save_production_config

STEM_URL = "https://media.example.com/tenants/t/drama/tracks/stem.wav"


def _never(*_: object) -> Path:
    raise AssertionError("nothing may be made or fetched for this take")


def _desk_take(desk: Path, facts: dict, take_id: str = "t1") -> Path:
    raw = make_take(
        desk / "ep01" / "takes" / f"take-ep01-{take_id}-raw-v1.mp4", tones=TWO_LINES
    )
    (desk / "ep01" / "api" / f"take-facts-ep01-{take_id}-v1.json").write_text(
        json.dumps(facts)
    )
    return raw


def _pin_old_bed(desk: Path) -> Path:
    """A show bed an older finish pinned on the desk (``series.json`` ``bed_path``): never laid now."""

    bed = make_tone(
        desk / "shared" / "beds" / "show-bed-v1.wav", seconds=2.0, freq=220, volume=0.9
    )
    series = load_series(desk)
    series.bed_path = str(bed.relative_to(desk))
    save_series(desk, series)
    return bed


def _mix_spy(monkeypatch: pytest.MonkeyPatch, module: object) -> list[object]:
    """Every ``bed`` the module's mix was given."""

    beds: list[object] = []
    real = module.mix_take  # type: ignore[attr-defined]

    def spy(*args: object, **kwargs: object):  # noqa: ANN202
        beds.append(kwargs.get("bed", args[2] if len(args) > 2 else None))
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "mix_take", spy)
    return beds


def _stem_fetch(calls: list[str]):
    def fetch(url: str, dest: Path) -> Path:
        calls.append(url)
        return make_tone(dest, seconds=15.0, freq=300, volume=0.3)

    return fetch


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


def test_the_kit_has_no_bed_to_lay() -> None:
    """The bed finders, makers and levels are gone from the kit; only the music notes stay."""

    from creation.post import bed

    for gone in (
        "resolve_bed",
        "pinned_bed",
        "bed_level",
        "DEFAULT_BED_DB",
        "service_music_maker",
    ):
        assert not hasattr(bed, gone), gone
    assert not hasattr(join_module, "loop_bed")
    assert "trending sound" in NO_MUSIC_RULE and "with score" in NO_MUSIC_RULE


# --- finish ------------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_finish_lays_no_music_even_with_a_show_bed_pinned_from_before(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _desk_take(post_desk, facts_with(NATIVE))
    _pin_old_bed(post_desk)
    beds = _mix_spy(monkeypatch, finish_module)
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), facts_fetcher=lambda *a: None,
        thumbnail=False, stream=out,
    )  # fmt: skip

    assert result.complete, out.getvalue()
    assert beds == [None], "the mix is given no bed"
    assert "bed" not in {s.step for s in result.steps}
    mix = next(s for s in result.steps if s.step == "mix")
    assert "no music" in mix.detail
    assert result.sound_line().startswith("Sound: no music ✓ · SFX ✓ · mix ✓")
    assert "music" not in result.sound_missing and not result.music_in_take
    record = latest_finish_record(post_desk, 1, "t1")
    assert record is not None and record.bed is None and record.bed_db is None
    assert not list((post_desk / "shared" / "beds").glob("show-bed-v2*")), (
        "no new bed made"
    )


@needs_ffmpeg
def test_finish_flags_for_the_bed_level_and_duck_are_gone(post_desk: Path) -> None:
    _desk_take(post_desk, facts_with(NATIVE))

    for flag in ("--bed-db", "--duck-db"):
        with pytest.raises(SystemExit):
            main(["finish", "--desk", str(post_desk), flag, "-6"])
    with pytest.raises(TypeError):
        run_finish(post_desk, bed_db=-6.0, stream=io.StringIO())  # type: ignore[call-arg]


@needs_ffmpeg
def test_finish_music_is_a_note_for_the_harness_and_nothing_is_made(
    post_desk: Path,
) -> None:
    _desk_take(post_desk, facts_with(NATIVE))
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), facts_fetcher=lambda *a: None,
        music="softer piano, less drums", thumbnail=False, stream=out,
    )  # fmt: skip

    assert result.complete, out.getvalue()
    assert result.music_notes == ("softer piano, less drums (ep01 t1)",)
    assert "Music change notes for the harness" in out.getvalue()
    assert not list((post_desk / "shared").glob("beds/*")), "no bed made or pinned"


@needs_ffmpeg
def test_baked_in_music_with_a_stem_is_taken_out_on_every_show(
    post_desk: Path,
) -> None:
    _desk_take(
        post_desk, facts_with({**NATIVE, "music": {"laid": True, "stem_url": STEM_URL}})
    )
    calls: list[str] = []
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), facts_fetcher=lambda *a: None,
        stem_fetcher=_stem_fetch(calls), thumbnail=False, stream=out,
    )  # fmt: skip

    assert result.complete, out.getvalue()
    assert calls == [STEM_URL]
    assert result.source.name.startswith("take-ep01-t1-stem-v")
    assert not result.music_in_take and result.sound_line().startswith(
        "Sound: no music ✓"
    )
    assert "swapped for the take's music-free stem" in out.getvalue()
    record = latest_finish_record(post_desk, 1, "t1")
    assert record is not None and not record.music_in_take and record.bed is None


@needs_ffmpeg
def test_an_older_take_finished_with_music_keeps_it_and_is_never_stripped_automatically(
    post_desk: Path,
) -> None:
    """Founder, 20 Oct 2026: old episodes stay as they are; their music goes only on request."""

    _desk_take(
        post_desk, facts_with({**NATIVE, "music": {"laid": True, "stem_url": STEM_URL}})
    )
    final = post_desk / "ep01" / "takes" / "take-ep01-t1-final-v1.mp4"
    write_finish_record(
        post_desk, episode=1, take_id="t1", complete=True, pre_bed=final, master=final,
        final=final, duck_db=None, music_in_take=True,
    )  # fmt: skip
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), facts_fetcher=lambda *a: None,
        stem_fetcher=_never, thumbnail=False, stream=out,
    )  # fmt: skip

    assert result.complete, out.getvalue()
    assert result.music_in_take, "the music it was finished with stays"
    assert "nothing is stripped automatically" in result.music_warning
    assert '"no music" note' in result.music_warning


@needs_ffmpeg
@pytest.mark.parametrize(
    "facts",
    [
        pytest.param({"model_music": True}, id="the-video-model-made-it"),
        pytest.param({"music": {"laid": True}}, id="no-stem-stored"),
    ],
)
def test_music_that_cannot_be_taken_out_is_named_and_the_take_still_finishes(
    post_desk: Path, facts: dict
) -> None:
    payload = facts_with(
        {**NATIVE, **({"music": facts["music"]} if "music" in facts else {})}
    )
    if facts.get("model_music"):
        payload["take_facts"]["model_music"] = True
    _desk_take(post_desk, payload)
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), facts_fetcher=lambda *a: None,
        stem_fetcher=_never, thumbnail=False, stream=out,
    )  # fmt: skip

    assert result.complete, out.getvalue()
    assert result.music_in_take and "music !!" in result.sound_line()
    assert result.music_warning.startswith("!! t1 carries music")
    assert "filming it again" in result.music_warning
    assert result.music_warning in result.summary_lines()
    assert "bed" not in {s.step for s in result.steps}


@needs_ffmpeg
def test_a_locked_take_with_server_ambience_needs_no_bed_and_no_room_tone(
    post_desk: Path,
) -> None:
    _desk_take(post_desk, facts_with({**TARGET, "ambience": {"laid": True}}))
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx([]), facts_fetcher=lambda *a: None,
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
    assert "bed" not in steps
    assert "ambience ✓ (laid by the server)" in result.sound_line()
    assert result.sound_line().startswith("Sound: no music ✓")


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


def test_set_bed_is_gone_and_music_note_and_music_lock_still_work(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    track = make_tone(post_desk / "track.wav", seconds=1.0, freq=330, volume=0.5)

    assert main(["set-bed", "--desk", str(post_desk), "--path", str(track)]) == 2
    assert NO_MUSIC_RULE in capsys.readouterr().out
    assert load_series(post_desk).bed_path is None, "nothing was pinned"
    # The music lock stays for older episodes (founder, 20 Oct 2026): it still reads.
    assert main(["music-lock", "--desk", str(post_desk)]) == 0

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
        )  # fmt: skip
        == 0
    )
    assert music_notes(post_desk, episode=1, take_id="t1") == ["calmer (ep01 t1)"]
    assert (
        "set-bed" in cli_post.POST_COMMANDS and "music-note" in cli_post.POST_COMMANDS
    )
    assert "music-lock" in cli_post.POST_COMMANDS


# --- join -------------------------------------------------------------------------------------------------------


def _music_in_take(desk: Path, take_id: str, *, grey: int) -> None:
    files = finished_take(desk, 1, take_id, grey=grey, tone=0.2)
    write_finish_record(
        desk, episode=1, take_id=take_id, complete=True, pre_bed=files["pre_bed"], master=files["master"],
        final=files["final"], duck_db=None, music_in_take=True,
    )  # fmt: skip


@pytest.fixture
def join_desk(tmp_path: Path) -> Path:
    from creation.ops.floor import init_series_desk

    return init_series_desk(tmp_path, "Join Music", band="30s", episode_count=1)


@needs_ffmpeg
def test_join_lays_no_music_even_with_a_show_bed_pinned_from_before(
    join_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    finished_take(join_desk, 1, "t1", grey=70)
    finished_take(join_desk, 1, "t2", grey=90)
    _pin_old_bed(join_desk)
    beds = _mix_spy(monkeypatch, join_module)
    out = io.StringIO()

    result = run_join(join_desk, episodes=(1,), stream=out)

    assert result.complete, out.getvalue()
    assert beds and all(bed is None for bed in beds), "the join's mix is given no bed"
    assert not hasattr(result, "bed")
    assert any(line.startswith("Music: none") for line in result.summary_lines())


@needs_ffmpeg
def test_join_names_takes_an_older_finish_left_with_music_and_never_refuses(
    join_desk: Path,
) -> None:
    _music_in_take(join_desk, "t1", grey=70)
    finished_take(join_desk, 1, "t2", grey=90)
    out = io.StringIO()

    result = run_join(join_desk, episodes=(1,), stream=out)

    assert result.complete, out.getvalue()
    assert any(
        note.startswith("!! ep01 t1 still carry music") for note in result.notes
    ), result.notes


# --- captions stay on for Instagram and TikTok ------------------------------------------------------------------


def test_a_reel_or_tiktok_part_never_goes_out_without_captions(post_desk: Path) -> None:
    style, note = posting_caption_style(post_desk, "none")
    assert style == "bold" and "keeps its captions on" in note, (
        "a new show's style instead"
    )
    assert posting_caption_style(post_desk, "plain") == ("plain", "")

    config = load_production_config(post_desk)
    config.caption_style = "none"
    save_production_config(post_desk, config)
    style, note = posting_caption_style(post_desk)
    assert style != "none" and "the desk's caption_style none" in note


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


@needs_ffmpeg
def test_a_locked_take_never_gets_an_effect_the_server_already_laid(
    post_desk: Path,
) -> None:
    _desk_take(
        post_desk,
        facts_with(
            {
                **TARGET,
                "ambience": {"laid": True},
                "sfx": {"cues": [{"sound": "a door slams", "laid": True}]},
            }
        ),  # fmt: skip
    )
    rendered: list[str] = []
    out = io.StringIO()

    result = run_finish(
        post_desk, sfx_render=fake_sfx(rendered), facts_fetcher=lambda *a: None,
        ambience_maker=_never, cut_meter=lambda _take: (3.9,), thumbnail=False, stream=out,
    )  # fmt: skip

    assert rendered == [], (
        "the server's door slam is in the track; laying it again doubles it"
    )
    steps = {s.step: s for s in result.steps}
    assert "already in the track" in steps["sfx"].detail, out.getvalue()
