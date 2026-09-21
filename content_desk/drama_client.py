"""Thin HTTP client for the hosted Drama Generation API."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

import httpx

_TERMINAL = frozenset({"completed", "failed", "cancelled"})


class DramaAPIError(RuntimeError):
    """Raised when the drama API returns a non-success response."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class DramaClient:
    """One operator session against prod-drama (or staging)."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        session_id: str | None = None,
        poll_interval_seconds: float = 15.0,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session_id = session_id or f"content-desk-{uuid.uuid4().hex[:12]}"
        self.poll_interval_seconds = poll_interval_seconds
        self._client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        """Close the underlying HTTP client."""

        self._client.close()

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self, *, idempotency_key: str | None = None, read: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Drama-Session-Id": self.session_id,
            "Accept": "application/json",
        }
        if not read:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _ok(self, response: httpx.Response) -> dict[str, Any]:
        if response.is_success:
            return response.json()
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise DramaAPIError(response.status_code, detail)

    def get(self, path: str) -> dict[str, Any]:
        """GET one drama API path."""

        return self._ok(self._client.get(self._url(path), headers=self._headers(read=True)))

    def post(self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        """POST JSON to one drama API path."""

        return self._ok(
            self._client.post(self._url(path), headers=self._headers(idempotency_key=idempotency_key), json=body),
        )

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """PUT JSON to one drama API path."""

        return self._ok(self._client.put(self._url(path), headers=self._headers(), json=body))

    def poll_job(
        self,
        job_id: str,
        *,
        video_route: bool,
        deadline_seconds: float,
        on_tick: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Poll until a plan, cast, boards, or video job reaches a terminal status.

        Parameters
        ----------
        job_id
            Coordinator job id from an enrol response.
        video_route
            When true, poll ``/v1/video-generations/{id}`` instead of ``/v1/jobs/{id}``.
        deadline_seconds
            Wall-clock timeout.
        on_tick
            Optional callback on each poll payload.

        Returns
        -------
        dict[str, Any]
            Terminal job JSON.

        Raises
        ------
        DramaAPIError
            On HTTP failure.
        TimeoutError
            When the deadline passes before completion.
        """

        path = f"/v1/video-generations/{job_id}" if video_route else f"/v1/jobs/{job_id}"
        deadline = time.monotonic() + deadline_seconds
        last: dict[str, Any] = {}
        read_headers = self._headers(read=True)

        while time.monotonic() < deadline:
            response = self._client.get(self._url(path), headers=read_headers)
            payload = self._ok(response)
            last = payload
            if on_tick is not None:
                on_tick(payload)
            if payload.get("status") in _TERMINAL:
                return payload
            time.sleep(self.poll_interval_seconds)

        raise TimeoutError(f"job {job_id} timed out; last={json.dumps(last, default=str)[:500]}")
