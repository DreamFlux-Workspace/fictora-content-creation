"""Preflight reads an episode 2+ opening (decision #7, 5 Oct 2026): every episode now has one."""

from __future__ import annotations

import json
from pathlib import Path

from creation.ops.preflight import _new_episode_handoff_info


def _desk(tmp_path: Path, ep2: dict[str, object]) -> Path:
    api = tmp_path / "ep02" / "api"
    api.mkdir(parents=True)
    spine = {
        "episode_summaries": [
            {"episode_id": "episode_01", "ordinal": 1},
            {"episode_id": "episode_02", "ordinal": 2, **ep2},
        ],
        "beats": [{"episode_id": "episode_02", "ordinal": 1, "dialogue_lines": []}],
        "frames": [],
    }
    (api / "spine.json").write_text(json.dumps(spine), encoding="utf-8")
    return tmp_path


def test_preflight_names_an_episode_two_opening_and_its_first_line(
    tmp_path: Path,
) -> None:
    desk = _desk(
        tmp_path,
        {
            "opening_image": "Mina spinning from the oven",
            "first_line": "Who ordered this?",
        },
    )

    line = _new_episode_handoff_info(2, desk)

    assert (
        "Planned opening: Mina spinning from the oven (first line: “Who ordered this?”)."
        in line
    )


def test_a_silent_opening_says_so_and_an_old_spine_says_nothing(tmp_path: Path) -> None:
    assert "(silent opening)" in _new_episode_handoff_info(
        2, _desk(tmp_path / "a", {"opening_image": "Hands freeze"})
    )
    assert "Planned opening" not in _new_episode_handoff_info(
        2, _desk(tmp_path / "b", {})
    )
