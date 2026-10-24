"""Desk production.config.json loading."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from creation import cli_produce
from creation.cli_config import add_production_config_args
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)


def test_defaults_when_missing(tmp_path: Path) -> None:
    cfg = load_production_config(tmp_path)
    assert (
        cfg.draft_episode_count == 1
    )  # episode 1 is drafted alone; later episodes are authored
    assert cfg.clip_duration_seconds == 15
    assert cfg.spoken_language is None
    assert (
        cfg.fallback_estimate_usd is None
    )  # an unpriced lane is priced at the Turbo rate, never a stale $1.20


def test_an_old_config_with_retired_choices_still_loads_and_drops_them(
    tmp_path: Path,
) -> None:
    """Cut tempo, pace and music blend are the server's now (founder, 24 Oct 2026).

    An old desk's ``production.config.json`` still holding them loads, ignores them,
    and a save never writes them back.
    """

    (tmp_path / "production.config.json").write_text(
        json.dumps(
            {
                "draft_episode_count": 4,
                "cut_tempo": "one_shot",
                "pace": "talky",
                "music_blend": ["romance", "suspense"],
            }
        ),
        encoding="utf-8",
    )
    cfg = load_production_config(tmp_path)
    assert cfg.draft_episode_count == 4
    for retired in ("cut_tempo", "pace", "music_blend"):
        assert not hasattr(cfg, retired)
    save_production_config(tmp_path, cfg)
    saved = json.loads(
        (tmp_path / "production.config.json").read_text(encoding="utf-8")
    )
    assert not {"cut_tempo", "pace", "music_blend"} & set(saved)


def test_the_retired_flags_and_command_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--cut-tempo`` and ``music-blend`` are gone: argparse refuses them (exit 2)."""

    parser = argparse.ArgumentParser()
    add_production_config_args(parser)
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--cut-tempo", "punchy"])
    assert exc.value.code == 2
    assert "--cut-tempo" in capsys.readouterr().err
    for argv in (
        ["start", "--series", "S", "--prompt", "p", "--cut-tempo", "one_shot"],
        ["bind", "--desk", str(tmp_path), "--prompt", "p", "--cut-tempo", "punchy"],
        ["music-blend", "--desk", str(tmp_path), "--set", "romance"],
    ):
        with pytest.raises(SystemExit) as exc:
            cli_produce.main(argv)
        assert exc.value.code == 2, argv


def test_round_trip(tmp_path: Path) -> None:
    cfg = ProductionConfig(draft_episode_count=4, fallback_estimate_usd=1.5)
    save_production_config(tmp_path, cfg)
    loaded = load_production_config(tmp_path)
    assert loaded.fallback_estimate_usd == 1.5
