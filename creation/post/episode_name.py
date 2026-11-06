"""Readable names for the episode file the kit hands over (founder, 14 Oct 2026).

The joined, marked episode is written as
``<series-slug>_ep<NN>_<draft|final>_v<N>_<YYYY-MM-DD>.mp4``, for example
``back-from-the-sea_ep01_draft_v3_2026-10-14.mp4``:

* ``series-slug``: the show's title from ``series.json`` (lowercased, anything
  but letters and digits a single hyphen, at most :data:`SLUG_MAX` characters),
  else the desk folder's name, else ``series``.
* ``draft`` for every file ``finish`` downloads. ``final`` only on desk copies made
  before 6 Nov 2026, when an episode still needed a "this episode is final" yes;
  they are still read, never made.
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
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

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


def sidecar_path(path: Path) -> Path:
    """The JSON record saved beside a downloaded file."""

    return path.with_suffix(".json")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def finished_files(folder: Path, episode: int) -> list[tuple[Path, dict[str, Any]]]:
    """Every episode file ``finish`` downloaded, newest first, with the server's answer saved beside it.

    Parameters
    ----------
    folder
        The episode's ``takes`` folder.
    episode
        Episode ordinal.

    Returns
    -------
    list[tuple[Path, dict[str, Any]]]
        Each file and its sidecar; files with no readable sidecar are left out.
    """

    found: list[tuple[Path, dict[str, Any]]] = []
    for path in reversed(episode_files(folder, episode)):
        meta = _read_json(sidecar_path(path))
        if meta and path.is_file():
            found.append((path, meta))
    return found


def saved_video(folder: Path, episode: int, video_url: str) -> Path | None:
    """The episode file already downloaded for this exact server copy, if any.

    Parameters
    ----------
    folder
        The episode's ``takes`` folder.
    episode
        Episode ordinal.
    video_url
        The server copy's link.

    Returns
    -------
    Path | None
        The newest file downloaded from ``video_url``, else ``None``.
    """

    return next(
        (
            path
            for path, meta in finished_files(folder, episode)
            if meta.get("video_url") == video_url
        ),
        None,
    )
