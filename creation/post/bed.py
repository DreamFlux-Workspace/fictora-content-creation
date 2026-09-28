"""The show's one music bed: found or made once, pinned on the desk, mixed under every take.

Order ``finish`` uses (first that exists wins):

1. the bed pinned on this desk (``series.json`` ``bed_path``; ``set-bed --path`` sets it);
2. the show's bed pinned on the spine (``series_audio_bed_url``, set by
   ``POST /v1/spines/{id}/audio-bed``), downloaded once into ``shared/beds/``;
3. a bed made locally, once per show: Fal Stable Audio 2.5 (a few cents),
   instrumental, seeded from the spine id so one show always gets the same
   piece, levelled to -20 LUFS. The description is the operator's
   ``--music`` text, else a plain one built from the show's genre.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from creation.ops.folder import next_versioned_path
from creation.ops.state import load_series, save_series
from creation.post.fal import MUSIC_ENDPOINT, FalCalls, FalClientCalls, download, output_url
from creation.post.media import run_ffmpeg

DEFAULT_BED_DB = -16.5
BED_LUFS = -20.0
BED_SECONDS = 30
BED_USD = 0.06
"""Stable Audio 2.5 bed, roughly; booked to the ledger by the caller."""

Maker = Callable[[str, int, Path], Path]
Downloader = Callable[[str, Path], Path]


@dataclass(frozen=True)
class Bed:
    """The bed a mix uses and where it came from."""

    path: Path
    source: str
    cost_usd: float = 0.0

    def one_line(self) -> str:
        """Operator line."""

        return f"{self.source} bed `{self.path.name}`"


def bed_description(spine: Mapping[str, Any], music: str | None = None) -> str:
    """What the music endpoint is asked for: the operator's words, else a plain genre line."""

    if music and music.strip():
        return music.strip()
    genre = str(spine.get("microdrama_genre") or "drama").replace("_", " ")
    return (
        f"Instrumental underscore for a {genre} short drama. Soft, unobtrusive, sits under dialogue, "
        "steady and loopable. No vocals, no lyrics."
    )


def bed_seed(spine: Mapping[str, Any]) -> int:
    """Stable seed per show (from the spine id)."""

    digest = hashlib.sha256(str(spine.get("spine_id") or "show").encode()).hexdigest()
    return int(digest[:8], 16)


def fal_music_maker(fal: FalCalls | None = None) -> Maker:
    """Render a raw bed on Fal Stable Audio 2.5."""

    def make(description: str, seed: int, target: Path) -> Path:
        client = fal or FalClientCalls()
        arguments = {"prompt": description, "seconds_total": BED_SECONDS, "seed": seed}
        output = client.result(MUSIC_ENDPOINT, client.submit(MUSIC_ENDPOINT, arguments))
        return download(output_url(output, "audio"), target)

    return make


def level_bed(raw: Path, target: Path) -> Path:
    """Loudness-normalise a bed to -20 LUFS as mp3."""

    run_ffmpeg(
        ["-i", str(raw), "-af", f"loudnorm=I={BED_LUFS:.0f}:TP=-1.5:LRA=9,aresample=48000",
         "-c:a", "libmp3lame", "-b:a", "160k", str(target)]
    )  # fmt: skip
    return target


def pin_bed(desk: Path, path: Path) -> Path:
    """Pin ``path`` as this desk's show bed (``series.json`` ``bed_path``).

    Raises
    ------
    FileNotFoundError
        When the file is missing.
    """

    if not path.is_file():
        raise FileNotFoundError(f"bed not found: {path}")
    series = load_series(desk)
    resolved = path.expanduser().resolve()
    try:
        series.bed_path = str(resolved.relative_to(desk.resolve()))
    except ValueError:
        series.bed_path = str(resolved)
    save_series(desk, series)
    return resolved


def pinned_bed(desk: Path) -> Path | None:
    """The bed pinned on this desk, when its file is still there."""

    stored = load_series(desk).bed_path
    if not stored:
        return None
    path = Path(stored) if Path(stored).is_absolute() else desk / stored
    return path if path.is_file() else None


def resolve_bed(
    desk: Path,
    *,
    spine: Mapping[str, Any] | None,
    music: str | None = None,
    maker: Maker | None = None,
    downloader: Downloader = download,
) -> Bed:
    """Find or make the show's bed and pin it on the desk.

    Parameters
    ----------
    desk
        Series desk.
    spine
        Saved spine (genre, spine id, ``series_audio_bed_url``).
    music
        Operator's own description for a bed that has to be made.
    maker
        Renders a raw bed (Fal Stable Audio 2.5 by default).
    downloader
        Fetches the spine's pinned bed.

    Returns
    -------
    Bed
        The bed to mix.

    Raises
    ------
    ValueError
        When nothing is pinned and there is no spine to make one from.
    """

    found = pinned_bed(desk)
    if found is not None and not music:
        return Bed(found, "pinned")
    beds = desk / "shared" / "beds"
    beds.mkdir(parents=True, exist_ok=True)
    url = str((spine or {}).get("series_audio_bed_url") or "")
    if url and not music:
        suffix = Path(urlparse(url).path).suffix or ".mp3"
        path = downloader(url, next_versioned_path(beds, "series-bed", suffix))
        return Bed(pin_bed(desk, path), "the show's (spine)")
    if spine is None:
        raise ValueError("no bed pinned and no saved spine to make one: pin a file with `set-bed --path`")
    description = bed_description(spine, music)
    target = next_versioned_path(beds, "show-bed", ".mp3")
    with tempfile.TemporaryDirectory() as scratch:
        raw = (maker or fal_music_maker())(description, bed_seed(spine), Path(scratch) / "raw-bed")
        level_bed(raw, target)
    target.with_suffix(".json").write_text(
        json.dumps({"spine_id": spine.get("spine_id"), "description": description, "endpoint": MUSIC_ENDPOINT},
                   indent=2) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    return Bed(pin_bed(desk, target), "made", BED_USD)
