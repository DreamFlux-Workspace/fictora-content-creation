"""The spend ledger says what the money bought: ``spend --unit`` and the per-series ``spend_log``."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from creation import episode_commands as ec
from creation.cli_ops import main as ops_main
from creation.ops.floor import init_series_desk, record_spend
from creation.ops.state import SpendEntry, load_series, save_series
from fake_api import FakeApi

ENTRY_KEYS = {"at_utc", "episode", "usd", "unit", "take_id"}
#: What the retired internal kit wrote (scripts/content_ops/floor.py record_spend).
OLD_LOG = [
    {"at_utc": "2026-09-26T18:19:05+00:00", "episode": 1, "usd": 0.3, "unit": "board", "take_id": "t1"},
    {"at_utc": "2026-09-26T18:40:00+00:00", "episode": 1, "usd": 0.06, "unit": "show-bed", "take_id": None},
]


def _series_json(desk: Path) -> dict:
    return json.loads((desk / "series.json").read_text(encoding="utf-8"))


def test_spend_unit_is_kept_in_the_series_ledger(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    desk = init_series_desk(tmp_path, "Ledger", band="15s", episode_count=1)

    code = ops_main(
        ["spend", "--desk", str(desk), "--episode", "1", "--usd", "0.3", "--take", "t1", "--unit", "cue:gaan-sting"]
    )

    assert code == 0
    assert "booked $0.30 for cue:gaan-sting; series spend $0.30" in capsys.readouterr().out
    (entry,) = _series_json(desk)["spend_log"]
    assert set(entry) == ENTRY_KEYS
    assert (entry["episode"], entry["usd"], entry["unit"], entry["take_id"]) == (1, 0.3, "cue:gaan-sting", "t1")
    assert entry["at_utc"]


def test_a_booking_without_a_unit_is_logged_unlabelled_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    desk = init_series_desk(tmp_path, "Ledger", band="15s", episode_count=1)
    record_spend(desk, episode=1, usd=0.3)  # every existing caller keeps working unchanged
    assert ops_main(["spend", "--desk", str(desk), "--episode", "1", "--usd", "0.1"]) == 0

    assert "unlabelled (pass --unit" in capsys.readouterr().out
    assert [entry["unit"] for entry in _series_json(desk)["spend_log"]] == ["unlabelled", "unlabelled"]
    assert load_series(desk).spend_usd == pytest.approx(0.4)


def test_an_adopted_desks_old_spend_log_is_read_as_the_ledger_not_carried_as_extra(tmp_path: Path) -> None:
    desk = init_series_desk(tmp_path, "Old Desk", band="15s", episode_count=1)
    raw = _series_json(desk)
    raw.pop("spend_log", None)
    raw["spend_log"] = OLD_LOG  # written by the internal kit, round-tripped by adopt-desk (#13)
    (desk / "series.json").write_text(json.dumps(raw), encoding="utf-8")

    series = load_series(desk)
    assert "spend_log" not in series.extra
    assert series.spend_log == [SpendEntry(**entry) for entry in OLD_LOG]

    save_series(desk, series)
    assert _series_json(desk)["spend_log"] == OLD_LOG  # unchanged on a plain save

    record_spend(desk, episode=1, usd=0.3, unit="look-frame")
    log = _series_json(desk)["spend_log"]
    assert log[:2] == OLD_LOG and len(log) == 3  # appended once, never duplicated
    assert log[2]["unit"] == "look-frame" and set(log[2]) == ENTRY_KEYS


def test_look_frame_books_with_its_unit(desk: Path, api: FakeApi) -> None:
    api.routes[("POST", "/v1/spines/sp1/look-frame")] = {
        "image_url": "https://cdn.example/look-frame/0f0f.png",
        "cached": False,
        "cost_usd": 0.3,
    }
    description = desk / "shared" / "look" / "look.txt"
    description.parent.mkdir(parents=True, exist_ok=True)
    description.write_text("Muted teal night, sodium streetlight, rain on glass.", encoding="utf-8")

    ec.run_look_frame(desk, description=description, out=io.StringIO())

    (entry,) = _series_json(desk)["spend_log"]
    assert (entry["unit"], entry["usd"]) == ("look-frame", 0.3)
