"""Readable names for the episode file the kit hands over (founder, 14 Oct 2026).

The joined, marked episode is written as
``<series-slug>_ep<NN>_<draft|final>_v<N>_<YYYY-MM-DD>.mp4``, for example
``back-from-the-sea_ep01_draft_v3_2026-10-14.mp4``:

* ``series-slug``: the show's title from ``series.json`` (lowercased, anything
  but letters and digits a single hyphen, at most :data:`SLUG_MAX` characters),
  else the desk folder's name, else ``series``.
* ``draft`` for every file ``join`` makes. ``final`` only on the copy
  ``fictora-ops approve --gate post --episode N`` makes once the human has
  said the episode is final (:func:`mark_final`).
* ``v<N>``: one counter per episode across draft and final, continuing the
  old ``episode-epNN-join-sokii-vN.mp4`` numbers on a desk that has them, so a
  name never collides and nothing is ever overwritten.
* the date the file was made.

Old files keep their names: nothing is renamed or moved, and
:func:`episode_files` finds the old ``episode-epNN-join-sokii-vN.mp4`` files
beside the new ones. Takes, segments, reels and series cuts keep their names.
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from datetime import date
from pathlib import Path

#: Longest series slug in a file name.
SLUG_MAX = 40
#: Slug used when neither the title nor the desk folder has a letter or digit.
FALLBACK_SLUG = "series"
#: The two stages a readable episode name carries.
STAGES = ("draft", "final")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_COMBINING = re.compile("[\u0300-\u036f]")
#: ``<slug>_ep01_draft_v3_2026-10-14.mp4`` (the episode, stage and version in groups).
NEW_NAME = re.compile(
    r"^(?P<slug>[a-z0-9-]+)_ep(?P<ep>\d{2,})_(?P<stage>draft|final)_v(?P<v>\d+)_(?P<day>\d{4}-\d{2}-\d{2})\.mp4$"
)
#: ``episode-ep01-join-sokii-v3.mp4``, the marked joined episode before 14 Oct 2026.
OLD_NAME = re.compile(r"^episode-ep(?P<ep>\d+)-join-sokii-v(?P<v>\d+)\.mp4$")


def slugify(text: str) -> str:
    """``Back From The Sea!`` -> ``back-from-the-sea``, ``Café Nights`` -> ``cafe-nights`` (capped at :data:`SLUG_MAX`); ``""`` when nothing is left."""

    # The app's slug, step for step: NFKD, drop combining marks (U+0300-U+036F), lowercase,
    # anything else a hyphen ("Café Nights" -> "cafe-nights").
    folded = _COMBINING.sub("", unicodedata.normalize("NFKD", text))
    slug = _NON_ALNUM.sub("-", folded.lower()).strip("-")
    return slug[:SLUG_MAX].rstrip("-")


def series_slug(desk: Path) -> str:
    """The desk's series slug: its ``series.json`` title, else the folder name, else ``series``."""

    title = ""
    try:
        raw = json.loads((desk / "series.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            title = str(raw.get("title") or "")
    except (OSError, ValueError):
        pass
    return slugify(title) or slugify(desk.name) or FALLBACK_SLUG


def episode_number(name: str) -> int | None:
    """The episode a delivered episode file (old or new name) belongs to, else ``None``."""

    match = NEW_NAME.match(name) or OLD_NAME.match(name)
    return int(match.group("ep")) if match else None


def episode_files(folder: Path, episode: int) -> list[Path]:
    """Every delivered file for ``episode`` in ``folder``, old and new names, oldest version first."""

    if not folder.is_dir():
        return []
    found = [
        (int(match.group("v")), path)
        for path in folder.glob("*.mp4")
        if (match := NEW_NAME.match(path.name) or OLD_NAME.match(path.name))
        and int(match.group("ep")) == episode
    ]
    return [path for _, path in sorted(found, key=lambda item: (item[0], item[1].name))]


def _version(name: str) -> int:
    match = NEW_NAME.match(name) or OLD_NAME.match(name)
    return int(match.group("v")) if match else 0


def today() -> date:
    """The date stamped on a new file (a function so tests can pin it)."""

    return date.today()


def next_episode_path(
    folder: Path,
    desk: Path,
    episode: int,
    stage: str = "draft",
    day: date | None = None,
) -> Path:
    """The next unused readable name for ``episode`` in ``folder``. Never an existing file.

    Parameters
    ----------
    folder
        The episode's ``takes`` folder.
    desk
        Series desk (for the slug).
    episode
        Episode ordinal.
    stage
        ``draft`` or ``final``.
    day
        Date in the name (default today).

    Returns
    -------
    Path
        ``<slug>_epNN_<stage>_v<N>_<YYYY-MM-DD>.mp4`` with ``N`` one past the highest
        version of the episode's old or new files.

    Raises
    ------
    ValueError
        When ``stage`` is not ``draft`` or ``final``.
    """

    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}, not {stage!r}")
    version = max((_version(p.name) for p in episode_files(folder, episode)), default=0)
    stamp = (day or today()).isoformat()
    slug = series_slug(desk)
    while True:
        version += 1
        path = folder / f"{slug}_ep{episode:02d}_{stage}_v{version}_{stamp}.mp4"
        if not path.exists():
            return path


def newest_episode_file(folder: Path, episode: int) -> Path | None:
    """The highest-version delivered file for ``episode`` (old or new name), else ``None``."""

    files = episode_files(folder, episode)
    return files[-1] if files else None


def mark_final(desk: Path, episode: int, source: Path | None = None) -> Path | None:
    """Copy the episode file the human called final to a new ``…_final_vN_…`` name.

    The source is left as it was; the copy gets the next version number, so nothing is
    overwritten. A source already named ``final`` is not copied again.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    source
        The file the human watched. Default: the episode's newest joined file.

    Returns
    -------
    Path | None
        The final copy (or ``source`` when it already carries ``final``); ``None`` when
        there is no episode file to copy.
    """

    folder = desk / f"ep{episode:02d}" / "takes"
    if source is None:
        source = newest_episode_file(folder, episode)
    if source is None or not source.is_file() or source.suffix.lower() != ".mp4":
        return None
    named = NEW_NAME.match(source.name)
    if named and named.group("stage") == "final":
        return source
    folder.mkdir(parents=True, exist_ok=True)
    target = next_episode_path(folder, desk, episode, "final")
    shutil.copy2(source, target)
    return target
