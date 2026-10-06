"""Letterbox desks: 10-second takes, so 3 slots at 30 s and 6 at 60 s, read from the spine.

The server films a 4:3 letterbox story in 10-second takes (fictora-drama #604).
The desk opens its take slots from the band, then the spine's
``beats_per_storyboard_set`` has the last word; on a 4:3 story the desk's take
length follows the spine too, so estimates price what the server films.
Portrait desks are left exactly as they were.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
from conftest import turbo_take_usd

from creation import orchestrate
from creation.cli_config import add_production_config_args, config_from_args
from creation.ops.floor import init_series_desk
from creation.ops.state import (
    TakeState,
    load_series,
    spine_take_seconds,
    sync_take_slots_to_spine,
    take_ids_for_band,
)
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)
from creation.production_state import ensure_production


def _spine(pattern: list[int], *, board_aspect: str | None = "4:3") -> dict[str, Any]:
    body: dict[str, Any] = {"spine_id": "sp1", "beats_per_storyboard_set": pattern}
    if board_aspect is not None:
        body["board_aspect"] = board_aspect
        body["delivery_format"] = "letterbox"
    return body


def _desk(tmp_path: Path, band: str, *, letterbox: bool) -> Path:
    return init_series_desk(
        tmp_path, "Lock the Door", band=band, episode_count=1, letterbox=letterbox
    )


@pytest.mark.parametrize(("band", "count"), [("15s", 2), ("30s", 3), ("60s", 6)])
def test_a_letterbox_desk_opens_one_slot_per_10_seconds(band: str, count: int) -> None:
    assert take_ids_for_band(band, letterbox=True) == [
        f"t{n}" for n in range(1, count + 1)
    ]


@pytest.mark.parametrize(("band", "count"), [("15s", 1), ("30s", 2), ("60s", 4)])
def test_a_portrait_desk_keeps_its_15_second_slots(band: str, count: int) -> None:
    """Guard: portrait slot counts are unchanged."""

    assert take_ids_for_band(band) == [f"t{n}" for n in range(1, count + 1)]
    assert take_ids_for_band(band, letterbox=False) == take_ids_for_band(band)


def test_start_letterbox_opens_three_slots_at_30_seconds(tmp_path: Path) -> None:
    desk = _desk(tmp_path, "30s", letterbox=True)
    assert [take.take_id for take in load_series(desk).episodes[0].takes] == [
        "t1",
        "t2",
        "t3",
    ]


def test_the_spine_adds_the_slots_a_letterbox_story_films(tmp_path: Path) -> None:
    series = load_series(
        _desk(tmp_path, "30s", letterbox=False)
    )  # opened as portrait: t1, t2
    notes = sync_take_slots_to_spine(series, _spine([2, 2, 2]))
    assert [take.take_id for take in series.episodes[0].takes] == ["t1", "t2", "t3"]
    assert notes == ["ep01: the story films 3 takes; added t3–t3."]


def test_the_spine_drops_empty_slots_the_story_does_not_film(tmp_path: Path) -> None:
    # A letterbox start on a server with real 4:3 takes off: the story stays portrait.
    series = load_series(_desk(tmp_path, "30s", letterbox=True))
    sync_take_slots_to_spine(series, _spine([3, 3], board_aspect=None))
    assert [take.take_id for take in series.episodes[0].takes] == ["t1", "t2"]


def test_a_slot_holding_work_is_never_dropped(tmp_path: Path) -> None:
    series = load_series(_desk(tmp_path, "30s", letterbox=True))
    series.episodes[0].takes[2] = TakeState(take_id="t3", filmed_count=1)
    notes = sync_take_slots_to_spine(series, _spine([3, 3], board_aspect=None))
    assert len(series.episodes[0].takes) == 3
    assert notes and notes[0].startswith("!! ep01:") and "t3" in notes[0]


def test_a_spine_without_a_pattern_changes_nothing(tmp_path: Path) -> None:
    series = load_series(_desk(tmp_path, "30s", letterbox=False))
    assert sync_take_slots_to_spine(series, {"spine_id": "sp1"}) == []
    assert len(series.episodes[0].takes) == 2


@pytest.mark.parametrize(
    ("spine", "band", "seconds"),
    [
        (_spine([2, 2, 2]), "30s", 10),
        (_spine([2] * 6), "60s", 10),
        (_spine([2, 2]), "30s", 15),  # boarded on the first canary, before 10 s takes
        (
            _spine([3, 3], board_aspect=None),
            "30s",
            None,
        ),  # portrait: the desk's own length
    ],
)
def test_the_take_length_is_read_off_a_4_3_spine(
    spine: dict[str, Any], band: str, seconds: int | None
) -> None:
    assert spine_take_seconds(spine, band) == seconds


def _args(*flags: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_production_config_args(parser)
    return parser.parse_args(list(flags))


def test_a_letterbox_start_defaults_to_10_second_takes() -> None:
    assert (
        config_from_args(_args("--delivery-format", "letterbox")).clip_duration_seconds
        == 10
    )


def test_portrait_still_defaults_to_15_and_an_explicit_length_wins() -> None:
    assert config_from_args(_args()).clip_duration_seconds == 15
    assert (
        config_from_args(_args("--delivery-format", "portrait")).clip_duration_seconds
        == 15
    )
    assert (
        config_from_args(
            _args("--delivery-format", "letterbox", "--clip-seconds", "8")
        ).clip_duration_seconds
        == 8
    )


def test_saving_a_letterbox_spine_sets_the_slots_and_the_take_length(
    tmp_path: Path,
) -> None:
    desk = _desk(tmp_path, "60s", letterbox=False)
    save_production_config(
        desk, ProductionConfig(clip_duration_seconds=15, delivery_format="letterbox")
    )

    orchestrate.save_spine_snapshot(desk, 1, _spine([2] * 6))

    assert [take.take_id for take in load_series(desk).episodes[0].takes] == [
        f"t{n}" for n in range(1, 7)
    ]
    assert load_production_config(desk).clip_duration_seconds == 10


def test_saving_a_portrait_spine_leaves_the_desk_alone(tmp_path: Path) -> None:
    """Guard: a portrait desk's slots and take length never move."""

    desk = _desk(tmp_path, "30s", letterbox=False)
    save_production_config(desk, ProductionConfig(clip_duration_seconds=15))
    before = (desk / "series.json").read_text(encoding="utf-8")

    orchestrate.save_spine_snapshot(desk, 1, _spine([3, 3], board_aspect=None))

    assert (desk / "series.json").read_text(encoding="utf-8") == before
    assert load_production_config(desk).clip_duration_seconds == 15


def test_a_letterbox_episode_estimate_prices_three_10_second_takes(
    tmp_path: Path,
) -> None:
    desk = _desk(tmp_path, "30s", letterbox=True)
    save_production_config(desk, ProductionConfig(delivery_format="letterbox"))
    orchestrate.save_spine_snapshot(desk, 1, _spine([2, 2, 2]))
    state = ensure_production(desk, prompt="p", preset_id="x", preset_version="1")
    state.video_lane = "minimax-h3"
    cfg = load_production_config(desk)
    takes = len(load_series(desk).episodes[0].takes)

    usd = orchestrate.table_estimate_usd(state, cfg, cast_count=2, takes=takes)

    assert takes == 3
    assert usd == pytest.approx(3 * turbo_take_usd(10.0))
    assert usd == pytest.approx(2 * turbo_take_usd(15.0)), (
        "the same 30 s of video as a portrait episode"
    )
