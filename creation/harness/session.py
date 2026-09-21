"""HTTP session wrapper for one create-flow harness run."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from creation.harness.http_util import api_headers, poll_until_terminal, save_json


class DramaApiRunSession:
    """One production drama API walk with JSON artefacts and JSONL run log."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        out_dir: Path,
        session_id: str | None = None,
        idempotency_prefix: str | None = None,
        poll_interval_seconds: float = 15.0,
        client_timeout: float = 180.0,
        log_name: str = "run.log",
    ) -> None:
        """Bind credentials, output directory, and correlation ids for one run.

        Parameters
        ----------
        base_url
            Drama generation service origin.
        token
            Service bearer token.
        out_dir
            Directory for numbered JSON artefacts and the run log.
        session_id
            ``X-Drama-Session-Id``; generated when omitted.
        idempotency_prefix
            Prefix for ``Idempotency-Key`` values; generated when omitted.
        poll_interval_seconds
            Sleep between job poll requests.
        client_timeout
            HTTP client timeout in seconds.
        log_name
            Filename for append-only JSONL phase log under ``out_dir``.
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.out = out_dir
        self.session_id = session_id or f"create-flow-{uuid.uuid4().hex[:12]}"
        self.prefix = idempotency_prefix or f"create-flow-{uuid.uuid4().hex[:8]}"
        self.poll_interval_seconds = poll_interval_seconds
        self._log_name = log_name
        self.client = httpx.Client(timeout=client_timeout)

    def url(self, path: str) -> str:
        """Join a drama API path against the configured base URL."""
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def headers(self, idempotency_key: str | None = None, *, read: bool = False) -> dict[str, str]:
        """Build request headers for this session."""
        headers = api_headers(self.token, self.session_id, idempotency_key=idempotency_key)
        if read:
            headers.pop("Content-Type", None)
        return headers

    def emit(self, phase: str, **payload: Any) -> None:
        """Append one JSON log line to stdout and ``out_dir / log_name``."""
        record = {"phase": phase, "ts": time.time(), **payload}
        line = json.dumps(record, default=str)
        print(line, flush=True)
        self.out.mkdir(parents=True, exist_ok=True)
        with (self.out / self._log_name).open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def save(self, name: str, payload: Any) -> None:
        """Write one JSON artefact under ``out_dir``."""
        save_json(self.out / name, payload)

    def _ok(self, response: httpx.Response) -> dict[str, Any]:
        if response.is_success:
            return response.json()
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise SystemExit(f"HTTP {response.status_code} {response.request.method} {response.request.url}: {detail}")

    def get(self, path: str) -> dict[str, Any]:
        """GET a drama API path and return JSON."""
        return self._ok(self.client.get(self.url(path), headers=self.headers(read=True)))

    def post(self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        """POST JSON to a drama API path.

        Parameters
        ----------
        path
            Drama API path.
        body
            JSON object.
        idempotency_key
            Optional ``Idempotency-Key``.

        Returns
        -------
        dict[str, Any]
            Parsed JSON body.
        """

        return self._ok(
            self.client.post(self.url(path), headers=self.headers(idempotency_key), json=body),
        )

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """PUT JSON to a drama API path.

        Parameters
        ----------
        path
            Drama API path.
        body
            JSON object.

        Returns
        -------
        dict[str, Any]
            Parsed JSON body.
        """

        return self._ok(self.client.put(self.url(path), headers=self.headers(), json=body))

    def poll_job(
        self,
        job_id: str,
        *,
        label: str,
        video_route: bool,
        deadline_seconds: float,
    ) -> dict[str, Any]:
        """Poll a plan, cast, boards, or video job until terminal."""
        path = f"/v1/video-generations/{job_id}" if video_route else f"/v1/jobs/{job_id}"
        return poll_until_terminal(
            self.client,
            url=self.url(path),
            headers=self.headers(read=True),
            emit=lambda phase, **payload: self.emit(f"poll_{label}", **payload),
            label=label,
            deadline_seconds=deadline_seconds,
            interval_seconds=self.poll_interval_seconds,
        )

    def spine(self, spine_id: str) -> dict[str, Any]:
        """Fetch the latest story spine snapshot."""
        return self.get(f"/v1/spines/{spine_id}")
