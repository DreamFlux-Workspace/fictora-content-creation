"""Download media URLs (stems, music, takes, boards, plates) the one way that always works.

The server's media lives on a public Cloudflare R2 host. Cloudflare answers
**403 "error code: 1010"** (blocked by the client's signature) to the default
user agent of Python's ``urllib`` (``Python-urllib/3.x``), while curl, httpx,
browsers and ffmpeg get the file. GRAFT, 14 Oct 2026 (L-20261014-21): stems
and music came back 403 until they were fetched with a browser user agent.

Every kit download goes through :func:`get_media`: a browser user agent, and
one more try after a refusal or a busy answer. ``fictora-ops fetch`` is the
same call for an operator who needs a file a command did not save.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx

from creation.ops.folder import next_versioned_path

#: A browser user agent. The media host refuses urllib's default (Cloudflare 1010).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
#: Headers every media GET sends.
MEDIA_HEADERS = {"User-Agent": BROWSER_USER_AGENT}
#: Answers worth one more try: a refusal at the edge, rate limits, a busy host.
RETRY_STATUSES = frozenset({403, 408, 425, 429, 500, 502, 503, 504})
#: Seconds to wait before each further try.
RETRY_WAITS = (1.0, 3.0)


class MediaFetchError(httpx.HTTPError):
    """A media URL could not be downloaded (the message says why, plainly).

    An :class:`httpx.HTTPError`, so every handler that caught a failed
    download before still catches it.
    """


def media_client(timeout: float = 180.0) -> httpx.Client:
    """An HTTP client for media downloads: browser user agent, redirects followed."""

    return httpx.Client(timeout=timeout, follow_redirects=True, headers=MEDIA_HEADERS)


def get_media(
    client: httpx.Client,
    url: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """GET one media URL and return its bytes.

    Sends :data:`BROWSER_USER_AGENT` whatever ``client`` was built with, and
    tries again (:data:`RETRY_WAITS`) after an answer in
    :data:`RETRY_STATUSES` or a dropped connection.

    Raises
    ------
    MediaFetchError
        When every try failed, or the answer was another error.
    """

    last = ""
    for attempt in range(len(RETRY_WAITS) + 1):
        if attempt:
            sleep(RETRY_WAITS[attempt - 1])
        try:
            response = client.get(url, follow_redirects=True, headers=MEDIA_HEADERS)
        except httpx.TransportError as exc:
            last = type(exc).__name__
            continue
        if response.is_success:
            return response.content
        last = f"HTTP {response.status_code}"
        if response.status_code not in RETRY_STATUSES:
            break
    raise MediaFetchError(f"could not download {_short(url)} ({last})")


def fetch_to(
    url: str,
    dest: Path,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Download ``url`` into ``dest`` (parent created) and return ``dest``."""

    own = client is None
    http = client or media_client()
    try:
        data = get_media(http, url, sleep=sleep)
    finally:
        if own:
            http.close()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def _short(url: str) -> str:
    parts = urlparse(url)
    name = parts.path.rsplit("/", 1)[-1]
    return f"{parts.netloc}/…/{name}" if name else parts.netloc or url


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
    dest.write_bytes(get_media(client, url))
    return dest
