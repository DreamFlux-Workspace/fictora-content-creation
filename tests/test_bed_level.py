"""The music bed's level in the mix: the desk's choice, the flag, else measured from the bed file.

Hanakaze (ep 1-7, 2026-10-01): every finish used the CLI default ``--bed-db -16.5``
(18 of 18 run-note entries). The pinned ``show-bed-v1.mp3`` already reads -20.6 LUFS,
so the music sat near -37 dB RMS against voices around -16: near-silent. The desk's
``series.json`` ``bed_db`` was set to -6.0 and ``finish`` and ``join`` never read it.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from conftest import make_take, make_tone, needs_ffmpeg
from test_post_finish import FACTS, TWO_LINES, fake_sfx

from creation import cli_post
from creation.cli_produce import main
from creation.ops.state import load_series, save_series
from creation.post.bed import (
    BED_DB_RANGE,
    BED_MIX_BAND_LUFS,
    BED_MIX_LUFS,
    DEFAULT_BED_DB,
    bed_level,
    chosen_record_level,
    measured_bed_db,
)
from creation.post.finish import run_finish
from creation.post.join import run_join
from creation.post.media import measure_loudness, measure_rms_windows


def _set_desk_bed_db(desk: Path, value: float) -> None:
    series = load_series(desk)
    series.extra["bed_db"] = value
    save_series(desk, series)


def _bed_file(tmp_path: Path) -> Path:
    path = tmp_path / "bed.wav"
    path.write_bytes(b"x")
    return path


def test_the_desk_level_is_used_when_no_flag_is_given(
    post_desk: Path, tmp_path: Path
) -> None:
    _set_desk_bed_db(post_desk, -6.0)

    level = bed_level(
        post_desk, _bed_file(tmp_path), flag=None, measure=lambda _p: -20.6
    )

    assert (level.db, level.source) == (-6.0, "desk")
    assert "series.json bed_db" in level.why
    assert level.warning == "", "-6.0 on a -20.6 LUFS bed lands at -26.6: in the band"


def test_the_flag_wins_over_the_desk(post_desk: Path, tmp_path: Path) -> None:
    _set_desk_bed_db(post_desk, -6.0)

    level = bed_level(
        post_desk, _bed_file(tmp_path), flag=-9.0, measure=lambda _p: -20.6
    )

    assert (level.db, level.source) == (-9.0, "flag")


def test_with_no_flag_and_no_desk_level_the_bed_is_measured_into_the_band(
    post_desk: Path, tmp_path: Path
) -> None:
    level = bed_level(
        post_desk, _bed_file(tmp_path), flag=None, measure=lambda _p: -20.6
    )

    assert level.source == "measured"
    assert level.db == pytest.approx(BED_MIX_LUFS + 20.6)
    assert BED_MIX_BAND_LUFS[0] <= -20.6 + level.db <= BED_MIX_BAND_LUFS[1]
    assert "-20.6 LUFS" in level.why and "under the dialogue" in level.why


def test_a_quiet_bed_is_lifted_and_a_silent_one_is_clamped() -> None:
    assert measured_bed_db(-32.0) == pytest.approx(BED_MIX_LUFS + 32.0)
    assert measured_bed_db(-32.0) > 0, "a quiet bed is lifted, not cut"
    assert measured_bed_db(-70.0) == BED_DB_RANGE[1]
    assert measured_bed_db(-2.0) == BED_DB_RANGE[0]


def test_the_old_default_on_a_levelled_bed_is_named_as_near_silent(
    post_desk: Path, tmp_path: Path
) -> None:
    level = bed_level(
        post_desk, _bed_file(tmp_path), flag=-16.5, measure=lambda _p: -20.6
    )

    assert level.db == -16.5
    assert level.warning.startswith(
        "!! at -16.5 dB the bed (-20.6 LUFS) lands at -37.1 LUFS"
    )
    assert "near-silent" in level.warning and "--bed-db -6.4" in level.warning


def test_a_bed_that_cannot_be_measured_falls_back_to_the_fixed_default(
    post_desk: Path,
) -> None:
    level = bed_level(post_desk, None, flag=None)

    assert (level.db, level.source) == (DEFAULT_BED_DB, "default")


def test_join_carries_a_chosen_level_but_never_the_old_unchosen_default() -> None:
    assert chosen_record_level([(-6.0, "desk"), (-6.0, "flag")]) == -6.0
    assert chosen_record_level([(-10.0, None), (-10.0, None)]) == -10.0
    assert chosen_record_level([(-16.5, None)]) is None, (
        "every pre-fix finish used it unasked"
    )
    assert chosen_record_level([(-6.4, "measured")]) is None, "measured again"
    assert chosen_record_level([(-6.0, "desk"), (-9.0, "desk")]) is None


def test_the_cli_leaves_the_level_to_the_desk(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    seen: list[tuple[str, float | None]] = []

    def record(name: str):
        def run(*_args, **kwargs):
            seen.append((name, kwargs["bed_db"]))
            return SimpleNamespace(complete=True, final="f", marked="m", master="m")

        return run

    monkeypatch.setattr(cli_post, "run_finish", record("finish"))
    monkeypatch.setattr(cli_post, "run_join", record("join"))
    main(["finish", "--desk", "d"])
    main(["join", "--desk", "d", "--episode", "1"])
    main(["finish", "--desk", "d", "--bed-db", "-8"])

    assert seen == [("finish", None), ("join", None), ("finish", -8.0)]


# --- on real files ---------------------------------------------------------------------------------------


def quiet_bed(spine: dict, music: str | None, target: Path) -> Path:
    """A bed far quieter than the show's -20 LUFS ones (about -38 LUFS)."""

    return make_tone(target.with_suffix(".wav"), seconds=6.0, freq=220, volume=0.02)


def _finish(post_desk: Path, **kwargs):
    make_take(post_desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t1-v1.json").write_text(
        json.dumps(FACTS)
    )
    out = io.StringIO()
    result = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=quiet_bed,
                        facts_fetcher=lambda *a: None, colour=False, stream=out, **kwargs)  # fmt: skip
    return result, out.getvalue()


def _record(post_desk: Path) -> dict:
    found = sorted((post_desk / "ep01" / "takes").glob("take-ep01-t1-finish-v*.json"))
    return json.loads(found[-1].read_text())


def _bed_between_lines(result) -> float:
    """The ducked bed's RMS in the gap between the two lines (2.6-3.0 s)."""

    mix = next(s for s in result.steps if s.step == "mix").output
    ducked = mix.with_name(mix.stem + "-ducked-bus.wav")
    levels = measure_rms_windows(ducked, window_seconds=0.1)
    gap = levels[26:30]
    return sum(gap) / len(gap)


@needs_ffmpeg
def test_finish_lifts_a_quiet_bed_to_the_band_and_says_why(post_desk: Path) -> None:
    result, text = _finish(post_desk)

    bed = next(s for s in result.steps if s.step == "bed")
    assert "measured: the bed reads" in bed.detail, bed.detail
    record = _record(post_desk)
    assert record["bed_db_source"] == "measured"
    bed_file = post_desk / load_series(post_desk).bed_path
    lands = measure_loudness(bed_file) + record["bed_db"]
    assert lands == pytest.approx(BED_MIX_LUFS, abs=0.6)
    # A sine reads about 3 dB under its RMS in LUFS terms; what matters is that it is no longer near-silent.
    assert _bed_between_lines(result) > -32.0, "the bed is heard between the lines"


@needs_ffmpeg
def test_finish_reads_the_desk_level_and_the_flag_overrides_it(post_desk: Path) -> None:
    _set_desk_bed_db(post_desk, 4.0)

    result, _text = _finish(post_desk)
    record = _record(post_desk)
    assert (record["bed_db"], record["bed_db_source"]) == (4.0, "desk")
    assert (
        "series.json bed_db" in next(s for s in result.steps if s.step == "bed").detail
    )

    again = run_finish(post_desk, sfx_render=fake_sfx([]), bed_maker=quiet_bed,
                       facts_fetcher=lambda *a: None, colour=False, bed_db=-3.0,
                       stream=io.StringIO())  # fmt: skip
    record = _record(post_desk)
    assert (record["bed_db"], record["bed_db_source"]) == (-3.0, "flag")
    assert again.complete


@needs_ffmpeg
def test_join_reads_the_desk_level_over_what_the_takes_were_finished_at(
    post_desk: Path,
) -> None:
    takes = post_desk / "ep01" / "takes"
    _finish(post_desk, bed_db=-16.5)
    make_take(takes / "take-ep01-t2-raw-v1.mp4", tones=TWO_LINES)
    (post_desk / "ep01" / "api" / "take-facts-ep01-t2-v1.json").write_text(
        json.dumps(FACTS)
    )
    run_finish(post_desk, take_id="t2", sfx_render=fake_sfx([]), bed_maker=quiet_bed, bed_db=-16.5,
               facts_fetcher=lambda *a: None, colour=False, stream=io.StringIO())  # fmt: skip
    _set_desk_bed_db(post_desk, 3.0)

    out = io.StringIO()
    joined = run_join(post_desk, episodes=(1,), stream=out)

    assert "Bed level: +3.0 dB (the desk's series.json bed_db)" in out.getvalue(), (
        out.getvalue()
    )
    assert joined.bed_db == 3.0
