"""The reel engine is the server's: upload the accepted takes, call the reel route, download what it made.

``fictora-produce reel`` and the reel after every ``finish`` no longer cut on
this laptop. They send the server's reel route (operator mode,
``POST /v1/video-generations/{job_id}/episodes/{episode_id}/reel`` with
``operator``) what only the desk knows, and it cuts the same reel the app's
"Share as reel" cuts ($0: ffmpeg on the server, no model):

- each take's picture and sound before the bed and captions (blur patches
  already applied here), uploaded with ``POST /v1/uploads`` and a signed ``PUT``;
- its accepted caption cues, shots and sound events (from the saved take facts);
- the harness bed and its level, the saved server cover, the episode's text.

Uploads are remembered by digest in ``reels/epNN/uploads.json``, so a file
already uploaded is not sent again; the server's reel cache is keyed by the
digests too, so a repeat costs one request. The rules live with the server:
``fictora-drama`` ``docs/reels/reel-rules.md``.

When the server cannot be reached the reel is not made and the operator is told
how to make it later; nothing is skipped silently, and a ``finish`` never fails
because its reel did (:func:`creation.post.reel.auto_reel`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from creation.harness.http_util import api_error_text

#: The reel route, as ``/openapi.json`` lists it.
REEL_ROUTE = "/v1/video-generations/{job_id}/episodes/{episode_id}/reel"
#: The request schema the operator fields live on.
REEL_REQUEST_SCHEMA = "DramaEpisodeReelRequest"
#: The first render takes tens of seconds; the server allows 180 s plus downloads.
REEL_TIMEOUT_SECONDS = 420.0
UPLOAD_TIMEOUT_SECONDS = 600.0
#: The episode folder's record of what was uploaded (digest -> URL).
UPLOADS_FILE = "uploads.json"

_AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}
_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


class ReelServerError(RuntimeError):
    """The server refused or failed the reel; the message says what to do."""


class ReelServerUnreachable(ReelServerError):
    """The server did not answer (network down, timed out, or a 5xx)."""


def unreachable_message(desk: Path, episode: int) -> str:
    """What the operator reads when the server did not answer."""

    return (
        "Reel not made: the server didn't answer. "
        f"Re-run `fictora-produce reel --desk {desk} --episode {episode}` later."
    )


def content_type(path: Path, *, kind: str) -> str:
    """The upload's media type: ``video/mp4`` for a take, the audio or image type by suffix.

    Raises
    ------
    ReelServerError
        For a bed or still the upload route does not take.
    """

    if kind == "video":
        return "video/mp4"
    table = _AUDIO_TYPES if kind == "audio" else _IMAGE_TYPES
    found = table.get(path.suffix.lower())
    if found is None:
        raise ReelServerError(
            f"`{path.name}`: the server takes {', '.join(sorted(table))} for a {kind} upload"
        )
    return found


def file_sha256(path: Path) -> str:
    """The file's SHA-256 (hex)."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def operator_mode_supported(openapi: Any) -> bool:
    """Whether the deploy's ``/openapi.json`` has the reel route with its ``operator`` field.

    Parameters
    ----------
    openapi
        ``/openapi.json`` as read from the deploy (anything else reads as no).

    Returns
    -------
    bool
        True when the route is listed and its request schema has ``operator``.
    """

    if not isinstance(openapi, Mapping):
        return False
    paths = openapi.get("paths") or {}
    if not any(str(path).endswith("/episodes/{episode_id}/reel") for path in paths):
        return False
    schemas = (openapi.get("components") or {}).get("schemas") or {}
    for name, schema in schemas.items():
        if str(name).endswith(REEL_REQUEST_SCHEMA) and isinstance(schema, Mapping):
            return "operator" in (schema.get("properties") or {})
    return False


class ReelServer:
    """One desk episode's calls to the server's reel engine.

    Parameters
    ----------
    desk, episode
        The desk and episode (credentials, session, ``reels/epNN/uploads.json``).
    run
        An open API session (tests pass a stand-in); default the desk's
        (:func:`creation.post.desk.open_api`), closed by :meth:`close`.
    http
        The client for the signed upload ``PUT`` and the downloads (default a new one).
    """

    def __init__(
        self,
        desk: Path,
        episode: int,
        *,
        run: Any = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.desk = desk
        self.episode = episode
        self._own_run = run is None
        if run is None:
            from creation.post.desk import open_api

            try:
                run = open_api(desk, episode)
            except SystemExit as exc:  # no token: the kit's credential loader exits
                raise ReelServerError(f"no Drama API credentials ({exc})") from exc
        self.run = run
        self.http = http or httpx.Client(
            timeout=UPLOAD_TIMEOUT_SECONDS, follow_redirects=True
        )
        self._own_http = http is None
        #: An upload was taken from ``uploads.json`` instead of sent (a failed reel then uploads again).
        self.reused = False

    def close(self) -> None:
        """Close the clients this object opened."""

        if self._own_run:
            self.run.client.close()
        if self._own_http:
            self.http.close()

    # -- uploads --------------------------------------------------------------

    def _uploads_path(self) -> Path:
        from creation.post.reel import episode_reels

        return episode_reels(self.desk, self.episode) / UPLOADS_FILE

    def _uploads(self) -> dict[str, Any]:
        path = self._uploads_path()
        if not path.is_file():
            return {}
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return body if isinstance(body, dict) else {}

    def _remember(self, sha: str, url: str, name: str) -> None:
        path = self._uploads_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        body = self._uploads()
        body[sha] = {
            "url": url,
            "file": name,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        scratch = path.with_name(path.name + ".tmp")
        scratch.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        scratch.replace(path)

    def forget_uploads(self) -> None:
        """Drop the remembered uploads (the server could not read one: upload again)."""

        path = self._uploads_path()
        if path.is_file():
            path.unlink()

    def upload(self, path: Path, *, kind: str) -> tuple[str, str]:
        """Upload one file (or reuse its earlier upload): ``(url, sha256)``.

        Raises
        ------
        ReelServerUnreachable
            When the server or the storage did not answer.
        ReelServerError
            When the server refused the upload.
        """

        sha = file_sha256(path)
        known = self._uploads().get(sha)
        if isinstance(known, Mapping) and known.get("url"):
            self.reused = True
            return str(known["url"]), sha
        media = content_type(path, kind=kind)
        size = path.stat().st_size
        try:
            status, slot = self.run.post_optional(
                "/v1/uploads", {"content_type": media, "size_bytes": size}
            )
        except httpx.HTTPError as exc:
            raise ReelServerUnreachable(
                f"upload slot for `{path.name}`: {type(exc).__name__}"
            ) from exc
        if status >= 500:
            raise ReelServerUnreachable(f"upload slot for `{path.name}`: HTTP {status}")
        if not 200 <= status < 300 or not isinstance(slot, Mapping):
            raise ReelServerError(
                f"the server refused the upload of `{path.name}`: {api_error_text(slot)}"
            )
        url = str(slot.get("file_url") or slot.get("audio_url") or "")
        headers = {str(k): str(v) for k, v in (slot.get("headers") or {}).items()}
        try:
            with path.open("rb") as handle:
                response = self.http.put(
                    str(slot["upload_url"]), content=handle.read(), headers=headers
                )
        except httpx.HTTPError as exc:
            raise ReelServerUnreachable(
                f"uploading `{path.name}`: {type(exc).__name__}"
            ) from exc
        if not response.is_success:
            raise ReelServerUnreachable(
                f"uploading `{path.name}`: storage answered HTTP {response.status_code}"
            )
        if not url:
            raise ReelServerError("the upload slot named no file URL")
        self._remember(sha, url, path.name)
        return url, sha

    # -- the route ------------------------------------------------------------

    def make(
        self, job_id: str, episode_id: str, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Call the reel route; the answer's JSON.

        Raises
        ------
        ReelServerUnreachable
            No answer, a timeout, or a 5xx (``episode_reel_failed`` included: nothing was stored).
        ReelServerError
            A refusal (the message is the server's).
        """

        path = REEL_ROUTE.format(job_id=job_id, episode_id=episode_id)
        try:
            response = self.run.client.post(
                self.run.url(path),
                headers=self.run.headers(),
                json=dict(body),
                timeout=REEL_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise ReelServerUnreachable(f"{type(exc).__name__}") from exc
        try:
            answer: Any = response.json()
        except ValueError:
            answer = response.text
        if response.is_success and isinstance(answer, dict):
            return answer
        if response.status_code >= 500:
            raise ReelServerUnreachable(
                f"HTTP {response.status_code}: {api_error_text(answer)}"
            )
        if response.status_code in (404, 405, 422) and not self._operator_mode():
            raise ReelServerError(
                "this server's reel route has no operator mode yet (fictora-drama's reel operator mode is "
                "not deployed): make the reel again once it is"
            )
        raise ReelServerError(f"the server refused the reel: {api_error_text(answer)}")

    def _operator_mode(self) -> bool:
        """Whether the deploy's ``/openapi.json`` lists the reel route with ``operator`` (unreadable: yes)."""

        try:
            status, doc = self.run.get_optional("/openapi.json")
        except httpx.HTTPError:
            return True
        return not 200 <= status < 300 or operator_mode_supported(doc)

    def download(self, url: str, dest: Path) -> Path:
        """Download one file the route made into ``dest`` (which must not exist).

        Raises
        ------
        ReelServerUnreachable
            When the file cannot be read.
        """

        try:
            response = self.http.get(url)
        except httpx.HTTPError as exc:
            raise ReelServerUnreachable(
                f"downloading {dest.name}: {type(exc).__name__}"
            ) from exc
        if not response.is_success or not response.content:
            raise ReelServerUnreachable(
                f"downloading {dest.name}: HTTP {response.status_code}"
            )
        with dest.open("xb") as handle:
            handle.write(response.content)
        return dest


def reel_job_id(desk: Path, episode: int, take_ids: list[str]) -> str:
    """The video generation the reel route is called on: the first take's filming job.

    Raises
    ------
    ReelServerError
        When no take of the episode has a filmed clip on the desk.
    """

    from creation.post.take_handles import take_coordinate

    for take in take_ids:
        where = take_coordinate(desk, episode, take)
        if where is not None:
            return where.video_job_id
    raise ReelServerError(
        f"ep{episode:02d}: no filmed clip record names a video job (epNN/api/*raw_scene_clips.json); "
        "the server's reel route is called on the episode's video job"
    )


__all__ = [
    "REEL_ROUTE",
    "ReelServer",
    "ReelServerError",
    "ReelServerUnreachable",
    "content_type",
    "file_sha256",
    "operator_mode_supported",
    "reel_job_id",
    "unreachable_message",
]
