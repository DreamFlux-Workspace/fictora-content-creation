"""A letterbox show's 9:16 deliverable built by finish and join (founder decisions, 6 Oct 2026)."""

from __future__ import annotations

import copy
from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from conftest import make_take, needs_ffmpeg
from test_post_finish import FACTS, _board, fake_bed, fake_sfx

from creation.captions import (
    Cue,
    LetterboxBand,
    Span,
    letterbox_caption_place,
    phrase_groups,
    text_width,
    time_words,
)
from creation.post.delivery_geometry import layout, title_sizes
from creation.post.finish import run_finish
from creation.post.join import run_join
from creation.post.letterbox import (
    TitleBlock,
    fit_title,
    italic_overrides,
    record_desk_hook_line,
    resolve_caption_colour,
    title_block,
    voice_end_spans,
)
from creation.post.media import decode_frames, probe_video
from creation.production_config import load_production_config, save_production_config

FOUR_THREE = "256x192"


# --- layout (the "Not Home" reference, user decision 2026-10-06) -----------------------------------


def test_the_layout_matches_not_home_on_1080x1920() -> None:
    place = layout()
    picture = place.picture
    assert (picture.x, picture.y, picture.width, picture.height) == (0, 555, 1080, 810)
    assert place.title.bottom == 435, "the last title baseline, as on Not Home"
    assert (place.caption.y, place.caption.bottom) == (1407, 1536), (
        "the box whose capital ink starts at y 1417, down to the chrome"
    )
    assert place.caption_highest_top == 1385
    assert (place.caption.x, place.caption.right) == (60, 950), (
        "clear of the right-hand rail"
    )
    assert (place.caption_size, place.caption_min_size) == (62, 52), (
        "ASS 62 draws Not Home's 56 px"
    )
    assert place.mark == (32, 173), (
        "its ink lands at x 38-97, y 179-228 (Not Home: 39-96, 179-227)"
    )
    assert place.title.y >= place.mark[1] + 63, "the title never runs into the mark"
    assert title_sizes() == (62, 60, 58, 56, 54, 52)


def _band() -> LetterboxBand:
    place = layout()
    return LetterboxBand(place.caption.x, place.caption.right, place.caption.y, place.caption_highest_top,
                         place.caption.bottom, place.caption_size, place.caption_min_size)  # fmt: skip


def test_a_caption_keeps_one_line_by_stepping_down_then_moves_up_when_it_needs_two() -> (
    None
):
    band = _band()
    assert letterbox_caption_place("Three missed payments", band, 1080) == (
        ["Three missed payments"], 62, 1407, 540,
    )  # fmt: skip
    room = band.right - band.left
    wide = "She has been missing for two years"
    assert text_width(wide, 62) > room >= text_width(wide, 52), (
        "the fixture fits one line only smaller"
    )
    lines, size, top, _ = letterbox_caption_place(wide, band, 1080)
    assert len(lines) == 1 and 52 <= size < 62 and top == 1407, (
        "one line, a smaller size"
    )
    long = "Previous owner is my sister. She has been missing"
    lines, size, top, _ = letterbox_caption_place(long, band, 1080)
    assert len(lines) == 2 and size == 62
    assert top == 1407 and top + 2 * size <= 1536, (
        "two lines at 62 still end above the chrome"
    )
    # A band whose top sits lower (a bigger caption, another canvas): the block moves up, never above 1385.
    low = replace(band, top=1460)
    lines, size, top, _ = letterbox_caption_place(long, low, 1080)
    assert top == 1536 - 2 * size, "moved up to end on the chrome floor"
    lower = replace(band, top=1460, floor=1480)
    assert letterbox_caption_place(long, lower, 1080)[2] == 1385, "never above y 1385"


def test_a_caption_centres_on_the_frame_unless_it_would_cross_the_right_hand_buttons() -> (
    None
):
    from creation.post.delivery_geometry import arial_bold_width

    band = _band()
    lines, size, _top, centre = letterbox_caption_place(
        "Who are you texting?", band, 1080
    )
    assert (lines, size, centre) == (["Who are you texting?"], 62, 540), (
        "a caption whose right edge stays left of x 950 centres on x 540, like Not Home"
    )
    wide = "Previous owner was my mother!"
    width = arial_bold_width(wide, 62)
    assert 540 + width / 2 > 950 and width <= 890, (
        "the fixture would cross x 950 if centred"
    )
    lines, size, _top, centre = letterbox_caption_place(wide, band, 1080)
    assert len(lines) == 1 and size == 62
    assert abs(centre + width / 2 - 950) <= 0.5, (
        "shifted left just enough: its right edge on x 950"
    )
    assert centre - width / 2 >= 60


def test_caption_widths_are_arial_bolds_own_advances_the_servers_table() -> None:
    from creation.post.delivery_geometry import ARIAL_BOLD_ADVANCES, arial_bold_width

    assert ARIAL_BOLD_ADVANCES["W"] == 1933 and ARIAL_BOLD_ADVANCES[" "] == 569
    assert arial_bold_width("Who are you texting?", 62) == pytest.approx(
        564.23, abs=0.01
    )


# --- phrase chunking ------------------------------------------------------------------------------


def _phrases(text: str) -> list[str]:
    words = time_words(text, Span(0.0, 3.0))
    return [" ".join(words[i].text for i in group) for group in phrase_groups(words)]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("That's my sister. She's been missing for two years.",
         ["That's my sister.", "She's been missing", "for two years."]),
        ("Three missed payments to the clinic. I'm taking the arm.",
         ["Three missed payments", "to the clinic.", "I'm taking the arm."]),
        ("Wait! I'll pay Friday, I swear!", ["Wait! I'll pay Friday,", "I swear!"]),
        ("She sold me this arm. Last week.", ["She sold me this arm.", "Last week."]),
        ("I paid. You took it anyway. Why?", ["I paid.", "You took it anyway. Why?"]),
    ],
)  # fmt: skip
def test_letterbox_captions_build_up_in_phrases_and_never_leave_one_word_alone(
    line: str, expected: list[str]
) -> None:
    assert _phrases(line) == expected


def test_a_phrase_holds_at_most_five_words_unless_an_orphan_joins_it() -> None:
    for line in (
        "Not tonight, not ever, two things I will never do again.",
        "Run run run run run run run run run run run.",
        "Go. Now.",
        "I know.",
    ):
        groups = _phrases(line)
        assert all(len(g.split()) >= 2 for g in groups) or len(line.split()) == 1, (
            groups
        )
        assert all(len(g.split()) <= 6 for g in groups), groups
    assert _phrases("Go.") == ["Go."]
    breath = [
        Cue(0.0, 0.3, "Wait"),
        Cue(0.3, 0.6, "here"),
        Cue(1.2, 1.5, "for"),
        Cue(1.5, 1.8, "me"),
    ]
    assert [len(g) for g in phrase_groups(breath)] == [2, 2], (
        "a pause of 0.6 s is a breath"
    )


# --- the title block ------------------------------------------------------------------------------

SPINE = {
    "title": "Three Payments Late",
    "delivery_format": "letterbox",
    "episode_summaries": [
        {"episode_id": "episode_01", "ordinal": 1,
         "hook_line_selected": {"kind": "option", "index": 2, "text": "The arm knows his sister"}},
    ],
}  # fmt: skip


def test_the_title_block_is_the_setup_line_white_and_the_hook_line_yellow(
    tmp_path: Path,
) -> None:
    block, _ = title_block(SPINE, 1, desk=tmp_path)
    assert block == TitleBlock(
        "Three Payments Late", "The arm knows his sister", "spine"
    )
    titled = copy.deepcopy(SPINE)
    titled["episode_summaries"][0]["title_line"] = "He came for the arm"
    assert title_block(titled, 1, desk=tmp_path)[0].setup == "Three Payments Late", (
        "the server has no episode title line: the setup line is the series title"
    )
    over, _ = title_block(SPINE, 1, desk=tmp_path, override="He came to take her arm.")
    assert (over.hook, over.hook_source) == ("He came to take her arm.", "--hook-line")
    record_desk_hook_line(
        tmp_path,
        1,
        {"kind": "custom", "text": "It came with a secret."},
        why="older server",
    )
    picked, _ = title_block(SPINE, 1, desk=tmp_path)
    assert picked.hook == "It came with a secret." and "desk" in picked.hook_source
    off = copy.deepcopy(SPINE)
    off["episode_summaries"][0]["hook_line_selected"] = {"kind": "off"}
    assert title_block(off, 1)[0] == TitleBlock("Three Payments Late", "", "spine")
    assert title_block(SPINE, 1, off=True)[0] is None


def test_the_title_fits_the_servers_ladder_two_lines_each_and_stays_under_the_mark() -> (
    None
):
    place = layout()
    fitted = fit_title(
        TitleBlock(
            "Three Payments Late", "He came to take her arm. It came with a secret."
        ),
        place,
    )
    assert fitted.size in title_sizes() and not fitted.note
    assert fitted.size == 62, "Not Home's 56 px (ASS 62)"
    assert len(fitted.setup) == 1 and len(fitted.hook) in (1, 2)
    pitch = fitted.size * 63 / 62
    assert (len(fitted.setup) + len(fitted.hook) - 1) * pitch <= place.title.height
    huge = fit_title(TitleBlock("A " * 40, "B " * 60), place)
    assert huge.note.startswith("!!") and len(huge.hook) <= 2


# --- caption colour -------------------------------------------------------------------------------


def test_the_caption_colour_is_the_flag_then_the_desk_then_the_show_then_yellow(
    post_desk: Path,
) -> None:
    spine = {"letterbox_caption_colour": "white"}
    assert resolve_caption_colour(post_desk, {}) == ("yellow", "the default")
    assert resolve_caption_colour(post_desk, spine)[0] == "white"
    config = load_production_config(post_desk)
    config.letterbox_caption_colour = "yellow"
    save_production_config(post_desk, config)
    assert resolve_caption_colour(post_desk, spine)[0] == "yellow"
    assert resolve_caption_colour(post_desk, spine, "white") == (
        "white",
        "--caption-colour",
    )
    with pytest.raises(ValueError, match="yellow or white"):
        resolve_caption_colour(post_desk, spine, "pink")


# --- captions end with the voice ------------------------------------------------------------------


def _line(line_id: str, start: float, end: float) -> SimpleNamespace:
    return SimpleNamespace(line_id=line_id, start=start, end=end)


def test_a_caption_ends_where_the_voice_does_not_where_its_window_does(
    tmp_path: Path,
) -> None:
    # 0.05 s windows over 6 s: ambience at -28 between lines; l1's voice 0.5-3.0 s in a 0.5-4.0 s window,
    # its tail ducked ambience (-38); l2's voice runs to its window's end.
    levels = [-28.0] * 120
    for i in range(10, 80):
        levels[i] = -15.0 if i < 60 else -38.0
    for i in range(90, 110):
        levels[i] = -14.0
    ends = voice_end_spans(
        tmp_path / "take.mp4", [_line("l1", 0.5, 4.0), _line("l2", 4.5, 5.5)],
        measure=lambda _p, window_seconds: levels, duration=6.0,
    )  # fmt: skip
    assert ends[0].trimmed and ends[0].end == pytest.approx(3.0)
    assert not ends[1].trimmed and ends[1].end == 5.5
    words = [SimpleNamespace(start=0.6, end=1.0), SimpleNamespace(start=1.1, end=2.2)]
    by_words = voice_end_spans(
        tmp_path / "t.mp4", [_line("l1", 0.5, 4.0)], words=words, duration=6.0
    )
    assert (
        by_words[0].end == pytest.approx(2.25) and by_words[0].how == "transcript words"
    ), "never in the first half of the window"


def test_a_line_with_no_gap_beside_it_keeps_its_window(tmp_path: Path) -> None:
    levels = [-15.0] * 100
    ends = voice_end_spans(
        tmp_path / "t.mp4", [_line("l1", 0.0, 2.5), _line("l2", 2.6, 5.0)],
        measure=lambda _p, window_seconds: levels, duration=5.0,
    )  # fmt: skip
    assert [e.end for e in ends] == [2.5, 5.0]


# --- italics follow what the take draws -----------------------------------------------------------

ITALIC_SPINE = {
    "cast": [{"cast_id": "cast_dez", "name": "Dez Halloran"}, {"cast_id": "cast_noor", "name": "Noor"},
             {"cast_id": "cast_ghost", "name": "Ghost", "voice_only": True}],
    "frames": [{"frame_id": "f1", "storyboard_group_id": "set1", "take_shot": 1, "cast_refs": ["cast_noor"],
                "board_correction": "Row 1: Dez's black hand clamps her wrist, his face visible as he speaks."},
               {"frame_id": "f2", "storyboard_group_id": "set1", "take_shot": 2, "cast_refs": ["cast_noor"],
                "board_correction": "Dez stays off-screen here, heard only."}],
    "beats": [{"frame_id": "f1", "dialogue_lines": [
                  {"line_id": "l1", "cast_id": "cast_dez", "off_screen": True},
                  {"line_id": "g1", "cast_id": "cast_ghost", "off_screen": True}]},
              {"frame_id": "f2", "dialogue_lines": [{"line_id": "l2", "cast_id": "cast_dez", "off_screen": True}]}],
}  # fmt: skip


def test_a_stale_off_screen_flag_does_not_slant_a_speaker_the_take_draws() -> None:
    lines = [_line("l1", 0.5, 2.0), _line("g1", 2.0, 2.5), _line("l2", 6.0, 8.0)]
    overrides, notes = italic_overrides(ITALIC_SPINE, None, lines)
    assert overrides == {"l1": False}, notes
    assert any("l1: upright" in n and "Dez in frame" in n for n in notes)
    assert any("g1: italic (a voice-only character)" in n for n in notes)
    assert any(n.startswith("l2: italic") for n in notes), (
        "the note says he is off screen"
    )
    facts = {"take_facts": {"shots": [{"shot_index": 2, "start_seconds": 5.0, "end_seconds": 10.0,
                                        "people": {"named": ["cast_dez"]}}]}}  # fmt: skip
    assert italic_overrides(ITALIC_SPINE, facts, lines)[0] == {"l1": False, "l2": False}


# --- finish and join build the 9:16 file ----------------------------------------------------------


def _letterbox_desk(desk: Path, *, locked: bool = False) -> None:
    api = desk / "ep01" / "api"
    spine = json.loads((api / "03_spine.json").read_text(encoding="utf-8"))
    spine.update(title="Three Payments Late", delivery_format="letterbox")
    spine["episode_summaries"][0]["hook_line_selected"] = {"kind": "option", "index": 0,
                                                           "text": "The arm knows his sister"}  # fmt: skip
    spine["beats"][0]["dialogue_lines"][0]["off_screen"] = True
    (api / "03_spine.json").write_text(json.dumps(spine), encoding="utf-8")
    facts = copy.deepcopy(FACTS)
    facts["take_facts"]["shots"][0]["people"] = {"count": 1, "named": ["cast_kenji"]}
    if locked:
        facts["take_facts"]["soundtrack"] = {
            "mode": "target_audio", "reason": None, "track_url": "https://x/track.wav", "native_foley": False,
            "lines": [{"line_id": "l1", "cast_id": "cast_kenji", "start_s": 1.0, "end_s": 2.9, "off_screen": True},
                      {"line_id": "l2", "cast_id": "cast_aya", "start_s": 3.2, "end_s": 4.0}],
        }  # fmt: skip
    (api / "take-facts-ep01-t1-v1.json").write_text(json.dumps(facts))


def _frame(video: Path, at: float) -> np.ndarray:
    frames = decode_frames(video, width=1080, height=1920, fps=24)
    return frames[min(len(frames) - 1, int(round(at * 24)))]


def _yellow(frame: np.ndarray) -> np.ndarray:
    r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
    return (r > 200) & (g > 180) & (b < 90)


@needs_ffmpeg
def test_finish_builds_the_9_16_letterbox_file_with_mark_title_and_band_captions(
    post_desk: Path,
) -> None:
    _letterbox_desk(post_desk, locked=True)
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
              tones=((1.0, 2.0, 440), (3.2, 4.0, 880)), size=FOUR_THREE, colour="gray")  # fmt: skip
    _board(post_desk)
    out = io.StringIO()

    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed, facts_fetcher=lambda *a: None,
                        cut_meter=lambda _take: (2.5,), stream=out)  # fmt: skip

    log = out.getvalue()
    assert result.complete, log
    steps = [s.step for s in result.steps]
    assert (
        steps.index("mix")
        < steps.index("letterbox")
        < steps.index("captions")
        < steps.index("watermark")
    )
    assert "hook-line" not in steps, "the title block goes on with the mark"
    info = probe_video(result.final)
    assert (info.width, info.height) == (1080, 1920)
    takes = post_desk / "ep01" / "takes"
    record = json.loads(
        next(iter(sorted(takes.glob("take-ep01-t1-finish-v*.json")))).read_text()
    )
    assert record["letterbox"] is True and record["caption_colour"] == "yellow"
    master = post_desk / record["master"]
    assert (probe_video(master).width, probe_video(master).height) == (1080, 1920)
    assert (takes / "take-ep01-t1-mix-v1.mp4").is_file(), (
        "the 4:3 take stays on the desk"
    )
    assert probe_video(takes / "take-ep01-t1-mix-v1.mp4").width == 256
    ass = master.with_suffix(".ass").read_text(encoding="utf-8")
    assert "\\an8\\pos(540,1407)" in ass, "ink top at y 1417, centred on the frame"
    assert "Style: House,Arial,62," in ass
    assert "&H0000E5FF" in ass and "Italic,,0,0,0,,Wait" not in ass, (
        "upright: the take draws Kenji"
    )
    detail = {s.step: s.detail for s in result.steps}
    assert "letterbox band under the picture, yellow, phrases" in detail["captions"]
    assert "title block" in detail["watermark"]

    frame = _frame(result.final, 1.6)
    pic = frame[555:1365]
    assert pic.mean() > 80 and (pic.max(axis=-1) < 20).mean() < 0.05, (
        "the picture fills 1080x810 at y 555-1365"
    )
    assert frame[1700:].max() < 30 and frame[300:480, 0:40].max() < 30, (
        "pure black bands"
    )
    assert _yellow(frame[1385:1540, 60:950]).sum() > 200, (
        "a yellow caption in the band under the picture"
    )
    assert _yellow(pic).sum() < 50, "no caption over the picture"
    assert _yellow(frame[260:500]).sum() > 200, "the yellow hook line above the picture"
    white = (frame[260:500].min(axis=-1) > 220).sum()
    assert white > 200, "the white setup line"
    assert frame[179:228, 38:98].mean() > frame[179:228, 200:260].mean() + 20, (
        "the mark in the top band"
    )


@needs_ffmpeg
def test_caption_colour_is_refused_on_a_portrait_take(post_desk: Path) -> None:
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4")
    with pytest.raises(ValueError, match="letterbox"):
        run_finish(
            post_desk,
            caption_colour="white",
            facts_fetcher=lambda *a: None,
            stream=io.StringIO(),
        )


@needs_ffmpeg
def test_join_marks_and_titles_the_joined_letterbox_file_once(tmp_path: Path) -> None:
    from conftest import SPINE

    from creation.ops.floor import init_series_desk
    from creation.post.finish_record import write_finish_record
    from creation.post.letterbox import pad_to_canvas
    from test_post_join import clip, noise_bed

    desk = init_series_desk(tmp_path, "Join LB", band="30s", episode_count=1)
    api = desk / "ep01" / "api"
    api.mkdir(parents=True, exist_ok=True)
    spine = copy.deepcopy(SPINE)
    spine.update(title="Three Payments Late", delivery_format="letterbox")
    spine["episode_summaries"][0]["hook_line_selected"] = {
        "kind": "option",
        "index": 0,
        "text": "Hook here",
    }
    (api / "spine.json").write_text(json.dumps({**spine, "beats": spine["beats"]}))
    takes = desk / "ep01" / "takes"
    for take_id, grey in (("t1", 70), ("t2", 90)):
        pre = clip(
            takes / f"take-ep01-{take_id}-mix-v1.mp4",
            seconds=2.0,
            grey=grey,
            tone=0.1,
            size=(256, 192),
        )
        master = pad_to_canvas(pre, takes / f"take-ep01-{take_id}-cap-v1.mp4")
        write_finish_record(desk, episode=1, take_id=take_id, complete=True, pre_bed=pre, master=master,
                            final=master, bed=None, bed_db=-16.5, duck_db=None,
                            letterbox={"letterbox": True, "caption_colour": "yellow"})  # fmt: skip
    noise_bed(desk)
    out = io.StringIO()

    result = run_join(desk, episodes=(1,), stream=out)

    assert result.complete, out.getvalue()
    assert (probe_video(result.marked).width, probe_video(result.marked).height) == (
        1080,
        1920,
    )
    frame = _frame(result.marked, 3.0)
    assert _yellow(frame[260:500]).sum() > 100, "the hook line on the joined file"
    assert frame[179:228, 38:98].max() > 100, "the mark in the top band"
    master = _frame(result.master, 3.0)
    assert master[179:228, 38:98].max() < 30, "the master carries no mark"
    assert any("letterbox: Sokii mark in the top band" in n for n in result.notes)
    with pytest.raises(ValueError, match="burned in yellow"):
        run_join(desk, episodes=(1,), caption_colour="white", stream=io.StringIO())
