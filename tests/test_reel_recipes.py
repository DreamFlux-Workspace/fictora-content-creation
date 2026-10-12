"""Reel recipes for trial posts (12 Oct 2026): ``reel --recipes``, the metrics rows, ``reel-results``.

The app makes one reel, the default recipe; only the kit makes several of one episode.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from creation.post.reel_cover import record_metrics_row
from creation.post.reel_results import (
    MIN_POSTS_TO_CALL,
    metrics_sheets,
    results_lines,
    tally,
)
from creation.post.reel_via_server import (
    DEFAULT_RECIPE,
    TRIAL_RECIPES,
    BedChoice,
    operator_body,
    parse_recipe,
    plan_recipe,
    recipe_list,
)


def _body(recipe: str | None) -> dict:
    return operator_body(
        takes=[{"take_id": "t1"}], bed=BedChoice(None, -20.0, None, True, "no bed"), bed_upload=None,
        still_upload=None, caption_style="house", pov=False, seconds=15.0, ending=None, hook_line=None,
        no_hook_line=False, hook_line_position=None, no_cover=False, cover_frame=None, plan=None, recipe=recipe,
    )  # fmt: skip


def test_a_picked_recipe_rides_in_the_operator_block_and_the_default_sends_nothing() -> (
    None
):
    assert _body("straight")["operator"]["opening"] == "straight"
    # The default (or none) sends nothing: a server from before recipes still takes it.
    for recipe in (None, DEFAULT_RECIPE):
        body = _body(recipe)
        assert "opening" not in body["operator"] and "close" not in body["operator"]
        assert "opening" not in body and "close" not in body


def test_recipe_lists() -> None:
    assert recipe_list("trial") == ["flash-forward", "straight"]
    assert recipe_list("straight, flash-forward,straight") == [
        "straight",
        "flash-forward",
    ]
    assert parse_recipe(" straight ") == "straight"
    for retired in (
        "montage",
        "face",
        "flash-forward+cliff",
    ):  # face and cliff were dropped on 12 Oct
        with pytest.raises(ValueError):
            recipe_list(retired)
    with pytest.raises(ValueError):
        recipe_list(" , ")


def test_a_plan_from_before_recipes_is_the_default() -> None:
    assert plan_recipe({}) == DEFAULT_RECIPE
    assert plan_recipe({"recipe": {"name": "straight"}}) == "straight"


def test_each_recipe_of_a_part_keeps_its_own_metrics_row(tmp_path: Path) -> None:
    sheet = tmp_path / "metrics.csv"
    for n, recipe in enumerate(TRIAL_RECIPES, start=1):
        record_metrics_row(
            sheet, {"reel_file": f"reel-ep01-v{n}.mp4", "part": 1, "recipe": recipe}
        )
    # A re-cut of one recipe replaces only that recipe's unposted row.
    record_metrics_row(
        sheet, {"reel_file": "reel-ep01-v4.mp4", "part": 1, "recipe": "straight"}
    )

    rows = list(csv.DictReader(sheet.open(encoding="utf-8")))
    assert [r["recipe"] for r in rows] == list(TRIAL_RECIPES)
    straight = next(r for r in rows if r["recipe"] == "straight")
    assert straight["reel_file"] == "reel-ep01-v4.mp4"
    assert straight["superseded"] == "reel-ep01-v2.mp4"


def _posted(desk: Path, rows: list[dict]) -> None:
    sheet = desk / "reels" / "metrics.csv"
    sheet.parent.mkdir(parents=True)
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "reel_file",
                "part",
                "kind",
                "recipe",
                "posted_at",
                "views",
                "hold_3s_pct",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def test_results_add_posted_reels_up_by_recipe_and_call_a_leader_only_with_enough_posts(
    tmp_path: Path,
) -> None:
    rows = [
        *({"part": i, "recipe": "straight", "posted_at": "x", "views": "1,000", "hold_3s_pct": "60%"} for i in range(MIN_POSTS_TO_CALL)),
        *({"part": i, "recipe": "", "posted_at": "x", "views": "500", "hold_3s_pct": "40"} for i in range(MIN_POSTS_TO_CALL)),
        {"part": 99, "recipe": "straight"},  # made, not posted
        {"part": 98, "kind": "clip", "posted_at": "x", "views": "9"},  # a clip
    ]  # fmt: skip
    _posted(tmp_path / "desk-a", rows)

    sheets = metrics_sheets([tmp_path])
    got = {t.recipe: t for t in tally(sheets)}

    assert set(got) == {"straight", DEFAULT_RECIPE}
    assert (
        got["straight"].mean("views") == 1000
        and got["straight"].mean("hold_3s_pct") == 60
    )
    assert any(
        "Leads on the 3-second hold: straight" in line
        for line in results_lines(list(got.values()), sheets=1)
    )

    few = tally(sheets)
    few[0].posts = few[1].posts = MIN_POSTS_TO_CALL - 1
    assert any("Too few posts" in line for line in results_lines(few, sheets=1))
