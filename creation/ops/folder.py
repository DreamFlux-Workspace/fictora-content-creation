"""Run-folder layout and versioned filenames for content productions."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

DEFAULT_RUN_PARENT = Path.home() / "Downloads" / "documents"
"""Default parent for dated series folders."""

RUN_SUBDIRS: tuple[str, ...] = (
    "reference",
    "plates",
    "boards",
    "fixtures",
    "voices",
    "sfx",
    "beds",
    "takes",
    "scripts",
    "artifact",
    "api",
)
"""Fixed review-surface directories inside one production folder."""

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_VERSION_STEM_RE = re.compile(r"-v(\d+)$")


def default_templates_dir() -> Path:
    """Return the in-repo content-ops templates directory.

    Returns
    -------
    Path
        ``docs/content-ops/templates`` under the repository root.
    """

    return Path(__file__).resolve().parents[2] / "docs" / "content-ops" / "templates"


def slugify_series(name: str) -> str:
    """Return a filesystem slug for a series title.

    Parameters
    ----------
    name
        Human series title or already-slugged name.

    Returns
    -------
    str
        Lowercase hyphenated slug.

    Raises
    ------
    ValueError
        When ``name`` has no letters or digits.
    """

    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError("series name must contain letters or digits")
    return slug


def run_folder_name(series: str, day: date) -> str:
    """Return the dated folder name for one production.

    Parameters
    ----------
    series
        Series title or slug.
    day
        Calendar date stamped on the folder.

    Returns
    -------
    str
        ``YYYY-MM-DD-<slug>``.
    """

    return f"{day.isoformat()}-{slugify_series(series)}"


def next_versioned_path(directory: Path, stem: str, suffix: str) -> Path:
    """Return the next unused ``stem-vN.suffix`` path. Never overwrites.

    Parameters
    ----------
    directory
        Folder that holds versioned files.
    stem
        Filename without version or suffix (for example ``board-ep01-t1``).
    suffix
        Extension, with or without a leading dot.

    Returns
    -------
    Path
        Path whose basename is ``{stem}-v{n}{suffix}`` for the lowest unused
        ``n`` starting at 1.

    Raises
    ------
    FileNotFoundError
        When ``directory`` does not exist.
    ValueError
        When ``stem`` is empty or already carries a ``-vN`` suffix.
    """

    if not directory.is_dir():
        raise FileNotFoundError(f"version directory not found: {directory}")
    cleaned = stem.strip()
    if not cleaned:
        raise ValueError("stem must be non-empty")
    if _VERSION_STEM_RE.search(cleaned):
        raise ValueError("stem must not include a -vN suffix")
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    pattern = re.compile(rf"^{re.escape(cleaned)}-v(\d+){re.escape(ext)}$")
    used = {int(match.group(1)) for path in directory.glob(f"{cleaned}-v*{ext}") if (match := pattern.match(path.name))}
    version = 1
    while version in used:
        version += 1
    return directory / f"{cleaned}-v{version}{ext}"


def init_named_run_folder(
    run_dir: Path,
    series: str,
    *,
    day: date | None = None,
    templates_dir: Path | None = None,
    extra_substitutions: dict[str, str] | None = None,
) -> Path:
    """Create one review folder at an exact path.

    Parameters
    ----------
    run_dir
        Destination folder. Must not already exist.
    series
        Series title or slug.
    day
        Date stamped into templates. Defaults to today.
    templates_dir
        Optional override for note and brief templates.
    extra_substitutions
        Extra ``{{placeholder}}`` replacements.

    Returns
    -------
    Path
        Created folder.

    Raises
    ------
    FileExistsError
        When ``run_dir`` already exists.
    FileNotFoundError
        When a required template is missing.
    """

    if run_dir.exists():
        raise FileExistsError(f"run folder already exists: {run_dir}")
    stamp = day or date.today()
    run_dir.mkdir(parents=True)
    for name in RUN_SUBDIRS:
        (run_dir / name).mkdir()
    source = templates_dir or default_templates_dir()
    substitutions = {
        "{{series}}": series.strip(),
        "{{date}}": stamp.isoformat(),
        "{{slug}}": slugify_series(series),
    }
    if extra_substitutions:
        substitutions.update(extra_substitutions)
    for filename in ("run-notes.md", "brief.md"):
        _copy_template(source / filename, run_dir / filename, substitutions)
    scripts_readme = run_dir / "scripts" / "README.md"
    scripts_readme.write_text(
        (
            "Copy tools from the previous production into this folder.\n"
            "Do not rebuild aligned API stages. Measure boards with\n"
            "`uv run python scripts/content_ops_run.py measure-board <png>`.\n"
        ),
        encoding="utf-8",
    )
    return run_dir


def init_run_folder(
    parent: Path,
    series: str,
    *,
    day: date | None = None,
    templates_dir: Path | None = None,
) -> Path:
    """Create one production folder with the runbook layout.

    Parameters
    ----------
    parent
        Directory that will hold the dated series folder.
    series
        Series title or slug.
    day
        Folder date. Defaults to today.
    templates_dir
        Optional override for note and brief templates.

    Returns
    -------
    Path
        Created run folder.

    Raises
    ------
    FileExistsError
        When the dated series folder already exists.
    FileNotFoundError
        When a required template is missing.
    """

    stamp = day or date.today()
    return init_named_run_folder(
        parent / run_folder_name(series, stamp),
        series,
        day=stamp,
        templates_dir=templates_dir,
    )


def _copy_template(src: Path, dest: Path, substitutions: dict[str, str]) -> None:
    """Copy one template file and apply ``{{placeholder}}`` substitutions.

    Parameters
    ----------
    src
        Template path.
    dest
        Destination path.
    substitutions
        Exact placeholder to replacement map.

    Raises
    ------
    FileNotFoundError
        When ``src`` does not exist.
    """

    if not src.is_file():
        raise FileNotFoundError(f"missing content-ops template: {src}")
    text = src.read_text(encoding="utf-8")
    for key, value in substitutions.items():
        text = text.replace(key, value)
    dest.write_text(text, encoding="utf-8")
