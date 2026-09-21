"""Pilot batch estimate episode selection."""

from __future__ import annotations

import pytest

from creation.harness.stages_gated import _pilot_batch_estimate_episode_ids


def test_pilot_estimate_needs_two_planned_episodes() -> None:
    spine = {"episode_summaries": [{"episode_id": "episode_01"}]}
    with pytest.raises(SystemExit, match="at least 2 planned episodes"):
        _pilot_batch_estimate_episode_ids(spine)


def test_pilot_estimate_uses_first_two_episodes() -> None:
    spine = {
        "episode_summaries": [
            {"episode_id": "episode_01"},
            {"episode_id": "episode_02"},
            {"episode_id": "episode_03"},
            {"episode_id": "episode_04"},
        ]
    }
    assert _pilot_batch_estimate_episode_ids(spine) == ["episode_01", "episode_02"]
