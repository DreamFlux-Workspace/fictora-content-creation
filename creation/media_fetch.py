"""Download provider URLs into versioned desk review folders."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import httpx

from creation.ops.folder import next_versioned_path


def _suffix_from_url(url: str, default: str) -> str:
    path = urlparse(url).path
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".mp4"):
        if path.lower().endswith(ext):
            return ext
    return default


def download_to_versioned(
    client: httpx.Client,
    url: str,
    directory: Path,
    stem: str,
    *,
    default_suffix: str = ".png",
) -> Path:
    """Fetch one URL into ``stem-vN`` under ``directory``.

    Parameters
    ----------
    client
        HTTP client for GET.
    url
        Public media URL.
    directory
        Review folder (``plates/``, ``boards/``, ``takes/``).
    stem
        Filename stem without version.
    default_suffix
        Extension when the URL has none.

    Returns
    -------
    Path
        Written file path.
    """

    suffix = _suffix_from_url(url, default_suffix)
    dest = next_versioned_path(directory, stem, suffix)
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest
