"""The show's one music bed: found or made once, pinned on the desk, mixed under every take.

Order ``finish`` uses (first that exists wins):

1. the bed pinned on this desk (``series.json`` ``bed_path``; ``set-bed --path`` sets it);
2. the show's bed pinned on the spine (``series_audio_bed_url``, set by
   ``POST /v1/spines/{id}/audio-bed``), downloaded once into ``shared/beds/``;
3. a bed made once per show on the server (:class:`~creation.post.audio_service.AudioService`,
   a few cents; the server writes the music brief from the show's genre, or
   uses the operator's ``--music`` words), downloaded and levelled here to
   -20 LUFS.

The bed's level in the mix (:func:`bed_level`, what ``finish``, ``join`` and
``reel`` mix it at), first that exists wins:

1. ``--bed-db`` on the command line;
2. the desk's ``series.json`` ``bed_db`` (the show's chosen level);
3. ``join`` / ``reel`` only: the level every take was finished at, when one was
   chosen (a flag or the desk; a pre-fix record at the old -16.5 default counts
   as not chosen);
4. measured: the bed file's loudness is read and the gain set so the bed lands
   at :data:`BED_MIX_LUFS` (about 9 dB under the -18 LUFS dialogue, so voices
   near -16 dB RMS sit about 10 dB over it between lines), clamped to
   :data:`BED_DB_RANGE`.

The chosen level is printed with why, and a level that puts the bed outside
:data:`BED_MIX_BAND_LUFS` gets a ``!!`` line naming the measured level. The duck
under each line is the mix's (:mod:`creation.post.mix`), unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from creation.ops.folder import next_versioned_path
from creation.ops.state import load_series, save_series
from creation.post.audio_service import AudioService, download
from creation.post.media import MediaToolError, measure_loudness, run_ffmpeg
from creation.post.mix import TARGET_LUFS as TARGET_DIALOGUE_LUFS

#: The old fixed level. Every pre-fix finish used it (Hanakaze: 18 of 18): with a -20.6 LUFS bed the
#: music sat near -37 dB RMS against voices around -16. Now only the fallback when the bed cannot be measured.
DEFAULT_BED_DB = -16.5
#: Where the bed lands in the mix between lines (LUFS): about 9 dB under the -18 LUFS dialogue, i.e.
#: about -27 dB RMS against voices near -16 (Hanakaze ep 7: -6.0 dB on the -20.6 LUFS bed read -27.3 dB).
BED_MIX_LUFS = -27.0
#: A bed level that lands the bed outside this band (LUFS in the mix) gets a ``!!`` line.
BED_MIX_BAND_LUFS = (-29.5, -24.5)
#: The measured level never goes outside this (a near-silent file is not lifted into hiss).
BED_DB_RANGE = (-24.0, 12.0)
BED_LUFS = -20.0
BED_SECONDS = 30
BED_USD = 0.20
"""One generated bed, roughly; booked to the ledger by the caller."""

Maker = Callable[[Mapping[str, Any], str | None, Path], Path]
"""``(spine, music words or None, target) -> raw bed file``."""
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


def service_music_maker(audio: AudioService) -> Maker:
    """Make a raw bed through the audio service (server-side), keyed per show and description."""

    def make(spine: Mapping[str, Any], music: str | None, target: Path) -> Path:
        spine_id = str(spine.get("spine_id") or "")
        key = f"bed-{spine_id}" + (
            f"-{hashlib.sha256(music.encode()).hexdigest()[:10]}" if music else ""
        )
        answer = audio.music_bed(spine_id=spine_id, brief=music, key=key)
        url = str(answer["audio_url"])
        return download(url, target)

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
    maker: Maker,
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
        Makes a raw bed (:func:`service_music_maker` in production).
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
        raise ValueError(
            "no bed pinned and no saved spine to make one: pin a file with `set-bed --path`"
        )
    target = next_versioned_path(beds, "show-bed", ".mp3")
    with tempfile.TemporaryDirectory() as scratch:
        raw = maker(spine, music, Path(scratch) / "raw-bed")
        level_bed(raw, target)
    target.with_suffix(".json").write_text(
        json.dumps(
            {"spine_id": spine.get("spine_id"), "music": music, "made_by": "drama-api"},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return Bed(pin_bed(desk, target), "made", BED_USD)


@dataclass(frozen=True)
class BedLevel:
    """The bed's gain in the mix, where it came from, and a ``!!`` line when it sits off the band."""

    db: float
    #: ``flag``, ``desk``, ``takes``, ``measured`` or ``default``.
    source: str
    why: str
    #: The bed file's measured loudness (LUFS), when it was read.
    bed_lufs: float | None = None
    warning: str = ""

    def one_line(self) -> str:
        """Operator line: ``-6.4 dB (measured: ...)`` and the warning when there is one."""

        return f"{self.db:+.1f} dB ({self.why})" + (
            f"; {self.warning}" if self.warning else ""
        )


def measured_bed_db(bed_lufs: float) -> float:
    """The gain that lands a bed of ``bed_lufs`` at :data:`BED_MIX_LUFS`, clamped to :data:`BED_DB_RANGE`."""

    return round(min(max(BED_MIX_LUFS - bed_lufs, BED_DB_RANGE[0]), BED_DB_RANGE[1]), 1)


def desk_bed_db(desk: Path) -> float | None:
    """The desk's chosen bed level (``series.json`` ``bed_db``), or ``None`` when it has none (or not a number)."""

    try:
        raw = load_series(desk).extra.get("bed_db")
    except (OSError, ValueError, KeyError):
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


def chosen_record_level(levels: list[tuple[float, str | None]]) -> float | None:
    """The level every take was finished at, when they agree and it was chosen (a flag or the desk).

    A record from before the level was resolved has no source: its level counts
    as chosen unless it is the old fixed default (:data:`DEFAULT_BED_DB`), which
    nobody chose. A ``measured`` level is measured again, never carried.
    """

    if not levels or len({db for db, _ in levels}) != 1:
        return None
    db = levels[0][0]
    for _db, source in levels:
        if source in ("measured", "default"):
            return None
        if source is None and db == DEFAULT_BED_DB:
            return None
    return db


def bed_level(
    desk: Path,
    bed: Path | None,
    *,
    flag: float | None,
    recorded: float | None = None,
    measure: Callable[[Path], float] = measure_loudness,
) -> BedLevel:
    """The bed's gain in the mix: ``--bed-db``, else the desk's, else the takes', else measured.

    Parameters
    ----------
    desk
        Series desk (``series.json`` ``bed_db``).
    bed
        The bed file the mix lays (measured for the default and for the band check).
    flag
        ``--bed-db`` (wins).
    recorded
        ``join`` / ``reel``: :func:`chosen_record_level` of the takes.
    measure
        Integrated loudness of a file (LUFS).

    Returns
    -------
    BedLevel
        The level and why, with a ``!!`` warning when it lands the bed outside
        :data:`BED_MIX_BAND_LUFS`.
    """

    lufs: float | None = None
    if bed is not None and bed.is_file():
        try:
            read = measure(bed)
        except (MediaToolError, OSError):
            read = None
        lufs = read if read is not None and math.isfinite(read) else None
    target = (
        f"lands the bed at {BED_MIX_LUFS:.0f} LUFS in the mix, about "
        f"{TARGET_DIALOGUE_LUFS - BED_MIX_LUFS:.0f} dB under the dialogue"
    )
    if flag is not None:
        db, source, why = float(flag), "flag", "--bed-db"
    elif (desk_db := desk_bed_db(desk)) is not None:
        db, source, why = desk_db, "desk", "the desk's series.json bed_db"
    elif recorded is not None:
        db, source, why = (
            float(recorded),
            "takes",
            "the level the takes were finished at",
        )
    elif lufs is not None:
        db = measured_bed_db(lufs)
        source = "measured"
        clamped = db != round(BED_MIX_LUFS - lufs, 1)
        why = f"measured: the bed reads {lufs:.1f} LUFS, so {db:+.1f} dB " + (
            f"(clamped to {BED_DB_RANGE[0]:.0f}..{BED_DB_RANGE[1]:+.0f} dB; it lands at {lufs + db:.1f} LUFS)"
            if clamped
            else target
        )
    else:
        db, source = DEFAULT_BED_DB, "default"
        why = "the fixed default: the bed could not be measured"
    warning = ""
    if lufs is not None:
        lands = lufs + db
        low, high = BED_MIX_BAND_LUFS
        if not low <= lands <= high:
            under = TARGET_DIALOGUE_LUFS - lands
            better = measured_bed_db(lufs)
            warning = (
                f"!! at {db:+.1f} dB the bed ({lufs:.1f} LUFS) lands at {lands:.1f} LUFS in the mix, "
                f"{under:.0f} dB under the dialogue ({'near-silent' if lands < low else 'over the voices'}; "
                f"the band is {low:g} to {high:g})"
                + (
                    f": {better:+.1f} dB lands it at {lufs + better:.1f} (`--bed-db {better:g}`, or set "
                    f"series.json bed_db)"
                    if source != "measured"
                    else ""
                )
            )
    return BedLevel(round(db, 1), source, why, lufs, warning)
