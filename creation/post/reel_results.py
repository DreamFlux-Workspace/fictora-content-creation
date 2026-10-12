"""``reel-results``: the posted reels' numbers by recipe, read from the desks' ``reels/metrics.csv``.

A reel's recipe is how it opens and where it stops (``reel --recipes``). The
operator fills each posted row from Instagram's insights; this adds the rows up
by recipe so the next default is picked on views, not taste. A recipe with fewer
than :data:`MIN_POSTS_TO_CALL` posted reels is shown but not called.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from creation.post.reel_cover import METRICS_FILE

#: Below this many posted reels a recipe's numbers are noise: shown, never called a winner.
MIN_POSTS_TO_CALL = 10
DEFAULT_RECIPE = "flash-forward+payoff"
#: The numbers summed per recipe (the operator's columns), in print order.
NUMBERS = ("views", "hold_3s_pct", "avg_watch_pct", "follows")


@dataclass
class RecipeTally:
    """One recipe's posted reels: how many, and each number's values."""

    recipe: str
    posts: int = 0
    values: dict[str, list[float]] = field(
        default_factory=lambda: {n: [] for n in NUMBERS}
    )

    def mean(self, name: str) -> float | None:
        got = self.values[name]
        return sum(got) / len(got) if got else None


def _number(text: str | None) -> float | None:
    cleaned = (text or "").strip().replace(",", "").rstrip("%")
    try:
        return float(cleaned)
    except ValueError:
        return None


def metrics_sheets(paths: Iterable[Path]) -> list[Path]:
    """Every ``reels/metrics.csv`` under the given desks (or folders of desks)."""

    found: list[Path] = []
    for path in paths:
        path = path.expanduser()
        direct = path / "reels" / METRICS_FILE
        found += (
            [direct]
            if direct.is_file()
            else sorted(path.glob(f"*/reels/{METRICS_FILE}"))
        )
    return list(dict.fromkeys(found))


def tally(sheets: Sequence[Path]) -> list[RecipeTally]:
    """Posted reel rows (a ``posted_at`` or ``views`` filled) added up by recipe, most posts first.

    Clips are left out (a clip has no recipe); a row from before recipes is the default.
    """

    by: dict[str, RecipeTally] = {}
    for sheet in sheets:
        with sheet.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if (row.get("kind") or "reel") != "reel":
                    continue
                if not (
                    (row.get("posted_at") or "").strip()
                    or (row.get("views") or "").strip()
                ):
                    continue
                recipe = (row.get("recipe") or "").strip() or DEFAULT_RECIPE
                entry = by.setdefault(recipe, RecipeTally(recipe))
                entry.posts += 1
                for name in NUMBERS:
                    value = _number(row.get(name))
                    if value is not None:
                        entry.values[name].append(value)
    return sorted(by.values(), key=lambda t: (-t.posts, t.recipe))


def results_lines(tallies: Sequence[RecipeTally], *, sheets: int) -> list[str]:
    """The table in plain words, and which recipe leads on the 3-second hold once there are enough posts."""

    if not tallies:
        return [
            f"No posted reel yet in {sheets} sheet(s): fill posted_at and views in reels/{METRICS_FILE}."
        ]

    def show(value: float | None, unit: str = "") -> str:
        return (
            "-"
            if value is None
            else f"{value:,.0f}{unit}"
            if unit != "%"
            else f"{value:.1f}%"
        )

    rows = [f"Posted reels by recipe ({sheets} sheet(s)):"]
    for t in tallies:
        rows.append(
            f"  {t.recipe:<22} {t.posts:>3} posted  views {show(t.mean('views'))}  "
            f"past 3 s {show(t.mean('hold_3s_pct'), '%')}  watched {show(t.mean('avg_watch_pct'), '%')}  "
            f"follows {show(t.mean('follows'))}"
        )
    ready = [
        t
        for t in tallies
        if t.posts >= MIN_POSTS_TO_CALL and t.mean("hold_3s_pct") is not None
    ]
    if len(ready) >= 2:
        best = max(ready, key=lambda t: t.mean("hold_3s_pct") or 0.0)
        rows.append(
            f"Leads on the 3-second hold: {best.recipe} (over {MIN_POSTS_TO_CALL}+ posts each)."
        )
    else:
        rows.append(
            f"Too few posts to call it: each recipe needs {MIN_POSTS_TO_CALL} posted reels with the 3-second hold filled."
        )
    return rows


__all__ = [
    "MIN_POSTS_TO_CALL",
    "RecipeTally",
    "metrics_sheets",
    "results_lines",
    "tally",
]
