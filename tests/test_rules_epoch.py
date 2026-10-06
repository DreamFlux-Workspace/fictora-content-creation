"""Desks created before 6 Oct 2026 keep their original behaviour; new rules apply to new desks.

User decision (6 Oct 2026, "only for new desks"): every kit change made on
6 Oct 2026 (#133 reel covers / Part wording / auto reels / lanes / metrics /
genre words / flash-forward note / profile faces, #135 caption speech timing,
#137 no dashes, #141 Bold) applies to NEW desks only, bug and safety fixes
included. A legacy desk must produce exactly what it produced before. The
legacy expectations below are the ones the tests held before each PR (git
history), or the goldens recorded before them.
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import make_take, needs_ffmpeg

from creation import rules_epoch
from creation.captions import (
    Cue,
    Span,
    build_ass,
    build_line_cues,
    resolve_caption_style,
    show_caption_style,
)
from creation.ops.floor import init_series_desk
from creation.post import faces
from creation.post.reel_plan import (
    ReelPlan,
    Segment,
    check_plan,
    genre_family,
    post_text,
)
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)
from creation.rules_epoch import (
    CURRENT_EPOCH,
    desk_epoch,
    desk_rules,
    is_legacy,
    legacy_rules,
    run_rules_epoch,
)


def _old_desk(tmp_path: Path) -> Path:
    """A desk folder dated before 6 Oct 2026 (nothing else on it)."""

    desk = tmp_path / "2026-09-26-old"
    desk.mkdir()
    return desk


def _legacy(desk: Path) -> Path:
    """Mark a test desk as created before 6 Oct 2026 (as an operator's ``rules-epoch --set legacy`` does)."""

    run_rules_epoch(desk, set_to="legacy", out=io.StringIO())
    return desk


# --- classification --------------------------------------------------------------------------------


def test_a_desk_folder_dated_before_the_epoch_is_legacy(tmp_path: Path) -> None:
    old = tmp_path / "2026-09-26-scp-173"
    old.mkdir()
    new = tmp_path / "2026-10-06-three-payments-late"
    new.mkdir()
    assert desk_epoch(old) == rules_epoch.DeskEpoch(
        "legacy", "folder date", "2026-09-26"
    )
    assert is_legacy(old)
    assert not is_legacy(new) and desk_epoch(new).epoch == CURRENT_EPOCH


def test_the_series_day_classifies_a_kit_desk(tmp_path: Path) -> None:
    before = init_series_desk(
        tmp_path, "Old Show", band="15s", episode_count=1, day=date(2026, 10, 5)
    )
    today = init_series_desk(
        tmp_path, "New Show", band="15s", episode_count=1, day=date(2026, 10, 6)
    )
    assert desk_epoch(before).source == "series day" and is_legacy(before)
    assert desk_epoch(today).source == "series day" and not is_legacy(today)
    # The series day wins over the folder's name (a renamed folder keeps its rules).
    renamed = before.rename(tmp_path / "2026-11-01-renamed")
    assert is_legacy(renamed)


def test_the_stamp_overrides_and_no_signal_is_todays_rules(tmp_path: Path) -> None:
    old = init_series_desk(
        tmp_path, "Old Show", band="15s", episode_count=1, day=date(2026, 9, 28)
    )
    out = io.StringIO()
    assert run_rules_epoch(old, out=out) == "legacy"
    assert "created 2026-09-28 (series.json day)" in out.getvalue()
    # An operator opts the old desk in; the stamp is kept in production.config.json.
    assert (
        run_rules_epoch(old, set_to=CURRENT_EPOCH, out=io.StringIO()) == CURRENT_EPOCH
    )
    assert load_production_config(old).rules_epoch == CURRENT_EPOCH
    assert desk_epoch(old).source == "stamp" and not is_legacy(old)
    # ... and back.
    assert run_rules_epoch(old, set_to="legacy", out=io.StringIO()) == "legacy"
    assert is_legacy(old)
    bare = tmp_path / "desk"
    bare.mkdir()
    assert desk_epoch(bare) == rules_epoch.DeskEpoch(CURRENT_EPOCH, "none")
    with pytest.raises(ValueError, match="choose one of"):
        run_rules_epoch(old, set_to="2026-10-07", out=io.StringIO())


def test_other_config_writes_keep_the_stamp(tmp_path: Path) -> None:
    desk = tmp_path / "desk"
    desk.mkdir()
    _legacy(desk)
    config = load_production_config(desk)
    config.caption_style = "plain"
    save_production_config(desk, config)
    assert is_legacy(desk)


def test_start_stamps_a_new_desk_and_bind_keeps_a_desks_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation import cli_produce
    from creation.production_state import ProductionState

    def bind(desk: Path, **_: Any) -> ProductionState:
        return ProductionState(
            session_id="s", prompt="p", preset_id="x", preset_version="1"
        )

    monkeypatch.setattr(
        cli_produce, "resolve_preset", lambda preset_id: (preset_id, "1")
    )
    monkeypatch.setattr(cli_produce, "bind_desk", bind)
    code = cli_produce.main(
        ["start", "--series", "Fresh", "--prompt", "A door.", "--parent", str(tmp_path)]
    )
    assert code == 0
    (desk,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert load_production_config(desk).rules_epoch == CURRENT_EPOCH
    old = init_series_desk(
        tmp_path, "Old", band="15s", episode_count=1, day=date(2026, 9, 1)
    )
    _legacy(old)
    assert cli_produce.main(["bind", "--desk", str(old), "--prompt", "A door."]) == 0
    assert load_production_config(old).rules_epoch == "legacy"


def test_the_command_runs_under_its_desks_rules(tmp_path: Path) -> None:
    desk = _old_desk(tmp_path)
    assert not legacy_rules()
    with desk_rules(desk) as legacy:
        assert legacy and legacy_rules()
    assert not legacy_rules()
    with desk_rules(None):
        assert not legacy_rules()


# --- #137: no dashes (legacy keeps them) -----------------------------------------------------------


def test_a_legacy_desk_keeps_its_dashes_and_a_new_desk_drops_them(
    tmp_path: Path,
) -> None:
    from creation.post.hook_overlay import HookOverlay, overlay_ass
    from creation.post.letterbox import TitleBlock, title_ass

    desk = _old_desk(tmp_path)
    with desk_rules(desk):
        (cues,) = build_line_cues(["Please— no no—"], [Span(0.0, 2.0)])
        assert [c.text for c in cues] == ["Please—", "Please— no", "Please— no no—"]
        assert (
            "Dialogue: 0,0:00:00.00,0:00:00.90,House,,0,0,0,,She lied — again—"
            in build_ass([Cue(0.0, 0.9, "She lied — again—")], width=1080, height=1920)
        )
        assert "She lied — again—" in overlay_ass(
            HookOverlay("hook", "She lied — again—", "top", 0.0, 3.0),
            width=1080,
            height=1920,
        )
        assert (
            "your roommate — texted"
            in title_ass(TitleBlock("POV: your roommate — texted", "x"))[0]
        )
    (cues,) = build_line_cues(["Please— no no—"], [Span(0.0, 2.0)])
    assert [c.text for c in cues] == ["Please…", "Please, no", "Please, no no…"]


# --- #135: caption speech timing (legacy spreads the line as before) -------------------------------

SOURCES_SPINE = {
    "title": "Tiny Show",
    "episode_summaries": [{"episode_id": "episode_01", "ordinal": 1}],
    "beats": [
        {"beat_id": "b1", "episode_id": "episode_01", "ordinal": 1,
         "dialogue_lines": [{"line_id": "l1", "text": "Open the door"}]},
        {"beat_id": "b2", "episode_id": "episode_01", "ordinal": 2,
         "dialogue_lines": [{"line_id": "l2", "text": "Who is there"}]},
        {"beat_id": "b3", "episode_id": "episode_01", "ordinal": 3,
         "dialogue_lines": [{"line_id": "l3", "text": "It was me"}]},
    ],
}  # fmt: skip


def _words(tmp_path: Path) -> Path:
    words = tmp_path / "words.json"
    chunks = [(0.3, 0.6, "Open"), (0.6, 0.8, "the"), (0.8, 1.2, "door"), (3.1, 3.4, "Who"), (3.4, 3.6, "is"),
              (3.6, 3.9, "there"), (4.6, 4.8, "It"), (4.8, 5.0, "was"), (5.0, 5.3, "me")]  # fmt: skip
    words.write_text(
        json.dumps(
            {"chunks": [{"timestamp": [a, b], "text": f" {t}"} for a, b, t in chunks]}
        )
    )
    return words


def test_a_legacy_reel_rebuilds_captions_as_before(tmp_path: Path) -> None:
    from creation.post.reel_sources import rebuild_cues

    desk = _old_desk(tmp_path)
    words = _words(tmp_path)
    windows = [
        (0.3, 1.2, "Open the door", False),
        (3.1, 3.9, "Who is there", False),
        (4.6, 5.3, "It was me", False),
    ]
    with desk_rules(desk):
        notes: list[str] = []
        cues = rebuild_cues(SOURCES_SPINE, desk=desk, episode=1, take_index=1, duration=6.0,
                            words_json=words, notes=notes, label="cap-v1.mp4")  # fmt: skip
        # Before #135: timed on the words json, each line's words spread over its span.
        assert cues is not None and cues[0].start == pytest.approx(0.3)
        assert "timed on `words.json` (words, words, words)" in notes[-1]
        assert not any("ESTIMATED" in n for n in notes)
        notes = []
        rebuild_cues(SOURCES_SPINE, desk=desk, episode=1, take_index=1, duration=6.0,
                     windows=windows, notes=notes, label="cap-v1.mp4")  # fmt: skip
        assert notes[-1] == (
            "⚠ captions rebuilt (no .ass for `cap-v1.mp4`): 3 spine line(s), word flicker, the wording and "
            "3 window(s) the run notes recorded for `cap-v1.mp4` (slant: the spine)"
        )
    notes = []
    rebuild_cues(SOURCES_SPINE, desk=tmp_path, episode=1, take_index=1, duration=6.0,
                 words_json=words, notes=notes, label="cap-v1.mp4")  # fmt: skip
    assert "speech timestamps `words.json`" in notes[-1]


def test_a_legacy_finish_spreads_flicker_words_over_the_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation import captions

    seen: list[bool] = []
    real = captions.time_lines

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(bool(kwargs.get("per_word")))
        return real(*args, **kwargs)

    monkeypatch.setattr(captions, "time_lines", spy)
    for name, legacy in (("2026-09-26-old", True), ("2026-10-06-new", False)):
        desk = tmp_path / name
        (desk / "ep01" / "api").mkdir(parents=True)
        (desk / "ep01" / "api" / "03_spine.json").write_text(json.dumps(SOURCES_SPINE))
        make_take(
            desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
            tones=((0.3, 1.2, 440),),
        )
        words = _words(tmp_path)
        try:
            captions.caption_take(desk, words_json=words, style="house")
        except (RuntimeError, ValueError):
            pass  # only the timing call matters here
        assert seen[-1] is (not legacy)


# --- #141: Bold (legacy desks keep house and are never asked) --------------------------------------


def test_a_legacy_desk_keeps_house_captions_and_is_never_asked(tmp_path: Path) -> None:
    from creation.caption_preview import caption_style_at_look

    desk = _old_desk(tmp_path)
    assert show_caption_style(desk) == ("house", "legacy")
    assert resolve_caption_style(desk) == ("house", "")
    out = io.StringIO()
    assert caption_style_at_look(desk, out=out) is None
    assert out.getvalue() == "" and not (desk / "production.config.json").exists()
    save_production_config(desk, ProductionConfig(caption_style="viral_karaoke"))
    assert resolve_caption_style(desk) == (
        "house",
        "the desk's caption_style 'viral_karaoke' is not a local caption style (house, plain, none); "
        "captioned house",
    )
    fresh = tmp_path / "2026-10-06-new"
    fresh.mkdir()
    assert show_caption_style(fresh) == ("bold", "new")


# --- #133 ------------------------------------------------------------------------------------------


def test_a_legacy_post_says_episode_and_a_new_post_says_part(tmp_path: Path) -> None:
    desk = _old_desk(tmp_path)
    kwargs = dict(
        series="SCP-173 Blink",
        episode=2,
        title="Blinking",
        question="Where will it be?",
        genre="horror",
    )
    with desk_rules(desk):
        legacy = post_text(**kwargs)  # type: ignore[arg-type]
    assert legacy == (
        "SCP-173 Blink · Episode 2: Blinking\n\nWhere will it be?\n"
        "Episode 3 is next. Follow so you don't miss it.\n\n#horror #shortdrama\n"
    )
    assert post_text(**kwargs).startswith("SCP-173 Blink · Part 2: Blinking")  # type: ignore[arg-type]
    assert "Follow for part 3." in post_text(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("genre", "old", "new"),
    [("system_regression", "default", "action"), ("isekai", "default", "action"),
     ("last_human", "default", "action"), ("horror", "horror", "horror"), ("revenge", "action", "action")],
)  # fmt: skip
def test_legacy_genre_words_are_the_old_lists(
    tmp_path: Path, genre: str, old: str, new: str
) -> None:
    desk = _old_desk(tmp_path)
    with desk_rules(desk):
        assert genre_family(genre) == old
    assert genre_family(genre) == new


def test_a_legacy_cold_open_inside_the_last_beat_is_still_warned(
    tmp_path: Path,
) -> None:
    from test_reel_openers_endings import CUES, take

    desk = _old_desk(tmp_path)

    def plan() -> ReelPlan:
        return ReelPlan(
            episode=4, seconds=6.0,
            segments=[Segment("t1", 12.0, 13.5, "cold_open"), Segment("t1", 0.0, 3.0, "plant"),
                      Segment("t1", 11.25, 15.0, "new_fact")],
            last_beat=("t1", 11.25, 15.0),
        )  # fmt: skip

    with desk_rules(desk):
        legacy = plan()
        warnings = check_plan(legacy, [take(cues=CUES)])
    assert (
        "the cold open t1 12.00-13.50 s is inside the last beat (t1 11.25-15.00 s, the new fact): the reel "
        "opens on its own ending. Take the cold open from the pivot or the peak before the reveal"
    ) in warnings
    assert not any("flash-forward" in n for n in legacy.notes)
    new = plan()
    assert not any("ending" in w for w in check_plan(new, [take(cues=CUES)]))
    assert any("that's the flash-forward" in n for n in new.notes)


class _Cascade:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name, self.calls = name, calls

    def detectMultiScale(self, gray, scaleFactor, minNeighbors, minSize):  # noqa: N802, N803
        self.calls.append(self.name)
        return np.asarray([(5, 5, 30, 30)] if self.name == "profile" else [])


def test_a_legacy_desk_reads_faces_without_the_profile_cascade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    table = {k: _Cascade(k, calls) for k in ("human", "anime", "profile")}
    monkeypatch.setattr(faces, "_cascade", lambda kind: table.get(kind))
    frame = np.zeros((168, 96, 3), dtype=np.uint8)
    desk = _old_desk(tmp_path)
    with desk_rules(desk):
        reading = faces.local_detector()(frame)  # type: ignore[misc]
    assert reading.count == 0 and "profile" not in calls
    # A new desk: the profile cascade reads the frame and its mirror (two boxes here).
    assert faces.local_detector()(frame).count == 2  # type: ignore[misc]
    monkeypatch.setattr(faces, "anime_style", lambda *a: None)
    calls.clear()
    faces.detector_for(desk, 1)(frame)  # type: ignore[misc]
    assert "profile" not in calls


@needs_ffmpeg
def test_a_legacy_reel_is_flat_with_no_cover_books_or_auto_reel(tmp_path: Path) -> None:
    from test_reel import _make_reel_desk

    from creation.post.reel import auto_reel, run_reel

    desk = _legacy(_make_reel_desk(tmp_path))
    out = io.StringIO()
    assert auto_reel(desk, 1, trigger="finish", stream=out) is None
    assert out.getvalue() == "" and not (desk / "reels").exists()

    out = io.StringIO()
    result = run_reel(desk, episode=1, seconds=6.0, stream=out)

    assert result.video is not None and result.cover is None
    assert sorted(p.name for p in (desk / "reels").iterdir()) == [
        "post-ep01-v1.txt", "reel-ep01-v1.ass", "reel-ep01-v1.mp4", "reel-plan-ep01-v1.json",
    ]  # fmt: skip
    post = result.post.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert post == (
        "The door was never locked.\nTiny Show · Episode 1: The Door\n\n"
        "Episode 2 is next. Follow so you don't miss it.\n\n#mystery #shortdrama\n"
    )
    said = out.getvalue()
    assert "cover" not in said and "metrics" not in said and "latest" not in said
    assert said.rstrip().splitlines()[-1].startswith("Reel reel-ep01-v1.mp4: ")
    assert "; no cover image" not in said


@needs_ffmpeg
def test_a_legacy_reel_runs_the_commands_it_ran_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The portrait golden as recorded before #133: the reel's files flat in ``reels/``."""

    from test_hook_overlay_portrait_unchanged import GOLDEN, REPO, _recording
    from test_reel import _make_reel_desk

    from creation.post.reel import run_reel

    desk = _legacy(_make_reel_desk(tmp_path))
    calls = _recording(monkeypatch, [desk, REPO])
    result = run_reel(desk, episode=1, seconds=6.0, stream=io.StringIO())
    assert result.ass is not None
    golden = json.loads(GOLDEN.read_text())["reel"]
    before_133 = json.loads(json.dumps(golden).replace("/reels/ep01/", "/reels/"))
    assert [*calls, [result.ass.read_text(encoding="utf-8")]] == before_133


@needs_ffmpeg
def test_a_legacy_finish_burns_what_the_golden_recorded_and_says_house(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No caption style on a legacy desk is house, byte for byte (the golden from before #141)."""

    from test_hook_overlay_portrait_unchanged import REPO, _check, _recording
    from test_post_finish import FACTS, TWO_LINES, _board, fake_bed, fake_sfx

    from creation.post.finish import run_finish

    _legacy(post_desk)
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    _board(post_desk)
    calls = _recording(monkeypatch, [post_desk, REPO])
    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=fake_bed,
                        facts_fetcher=lambda *a: None, stream=out)  # fmt: skip
    assert result.complete
    captions = (post_desk / "ep01" / "takes" / "take-ep01-t1-cap-v1.ass").read_text(
        encoding="utf-8"
    )
    _check("finish", [*calls, [captions]])
    assert "Burning house captions" in out.getvalue()
    thumb = next(s for s in result.steps if s.step == "thumbnail")
    assert "free cover image" not in thumb.detail


@needs_ffmpeg
def test_a_legacy_finish_asks_for_the_paid_cover_in_its_old_words(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_episode_thumbnail import _desk_with_take, _finish

    _legacy(post_desk)
    _desk_with_take(post_desk, monkeypatch)

    result = _finish(post_desk)

    thumb = next(s for s in result.steps if s.step == "thumbnail")
    assert thumb.detail == (
        "no cover on the desk yet. Drawing one on the server costs $0.30; "
        "after the human's yes, finish again with --thumbnail"
    )
