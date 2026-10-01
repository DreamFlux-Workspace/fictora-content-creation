"""Presets: listed before a desk is made, checked before ``start`` creates anything (L-20260930-13, -14)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from creation import orchestrate
from creation.cli_produce import main as produce_main
from creation.production_state import production_path

ROWS: list[dict[str, Any]] = [
    {"preset_id": "modern-dark-fantasy", "version": "1.2.0", "label": "Cold Gate"},
    {"preset_id": "modern-dark-fantasy", "version": "1.10.0", "label": "Cold Gate"},
    {"preset_id": "slice-of-life", "version": "1.3.0", "label": "Meadow Hour"},
    {
        "preset_id": "creator-mine",
        "version": "1.0.0",
        "label": "My look",
        "scope": "tenant",
        "status": "extracting",
    },
]


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Serve ``ROWS`` as ``GET /v1/art-style-presets``; count the reads."""

    calls: list[int] = []

    def rows() -> list[dict[str, Any]]:
        calls.append(1)
        return ROWS

    monkeypatch.setattr(orchestrate, "fetch_preset_rows", rows)
    return calls


def _start(tmp_path: Path, preset: str, *extra: str) -> int:
    return produce_main(
        [
            "start",
            "--series",
            "Night Tram",
            "--prompt",
            "A quiet tram at night.",
            "--parent",
            str(tmp_path),
            "--preset-id",
            preset,
            *extra,
        ]
    )


def test_a_wrong_preset_makes_no_desk_and_names_the_valid_ones(
    tmp_path: Path, catalog: list[int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _start(tmp_path, "dark-fantasy") == 2

    err = capsys.readouterr().err
    assert "preset not published: 'dark-fantasy'" in err
    assert "modern-dark-fantasy" in err and "slice-of-life" in err
    assert "fictora-produce presets" in err
    assert list(tmp_path.iterdir()) == [], "no half-made desk is left behind"


def test_a_good_preset_after_a_wrong_one_starts_cleanly(
    tmp_path: Path, catalog: list[int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _start(tmp_path, "dark-fantasy") == 2
    assert _start(tmp_path, "modern-dark-fantasy") == 0

    (desk,) = tmp_path.iterdir()
    state = production_path(desk).read_text(encoding="utf-8")
    assert '"preset_version": "1.10.0"' in state, "the newest version wins"
    assert len(catalog) == 2, "start reads the catalog once per run"


def test_a_half_made_desk_from_an_older_kit_names_the_bind_command(
    tmp_path: Path, catalog: list[int], capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.ops.floor import init_series_desk

    desk = init_series_desk(tmp_path, "Night Tram", band="15s", episode_count=1)

    assert _start(tmp_path, "modern-dark-fantasy") == 2

    err = capsys.readouterr().err
    assert "never bound" in err
    assert f"fictora-produce bind --desk {desk}" in err
    assert "--preset-id modern-dark-fantasy" in err
    assert "--prompt 'A quiet tram at night.'" in err


def test_a_bound_desk_that_exists_points_at_step(
    tmp_path: Path, catalog: list[int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert _start(tmp_path, "modern-dark-fantasy") == 0
    capsys.readouterr()

    assert _start(tmp_path, "modern-dark-fantasy") == 2

    err = capsys.readouterr().err
    assert "already bound" in err and "fictora-produce step --desk" in err


def test_bind_with_a_wrong_preset_lists_the_valid_ones(
    tmp_path: Path, catalog: list[int]
) -> None:
    from creation.ops.floor import init_series_desk

    desk = init_series_desk(tmp_path, "Night Tram", band="15s", episode_count=1)

    with pytest.raises(RuntimeError, match="Published presets: modern-dark-fantasy"):
        orchestrate.bind_desk(desk, prompt="A tram.", preset_id="horror")
    assert not production_path(desk).exists()


def test_a_tenant_preset_still_extracting_is_not_ready(catalog: list[int]) -> None:
    with pytest.raises(
        RuntimeError, match="'creator-mine' is not ready yet .*extracting"
    ):
        orchestrate.resolve_preset("creator-mine")


def test_presets_lists_the_published_catalog(
    catalog: list[int], capsys: pytest.CaptureFixture[str]
) -> None:
    assert produce_main(["presets"]) == 0

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert any(
        "modern-dark-fantasy" in line and "1.10.0" in line and "Cold Gate" in line
        for line in lines
    )
    assert not any("1.2.0" in line for line in lines), "one row per preset, newest"
    assert any("slice-of-life" in line and "Meadow Hour" in line for line in lines)
    assert any("creator-mine" in line and "not ready" in line for line in lines)
