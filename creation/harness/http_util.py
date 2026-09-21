"""HTTP helpers shared by create-flow harness sessions."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

_TERMINAL = frozenset({"completed", "failed", "cancelled"})


def api_headers(
    token: str,
    session_id: str,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    """Build standard drama generation API request headers.

    Parameters
    ----------
    token
        Service bearer token.
    session_id
        Drama session correlation id.
    idempotency_key
        Optional idempotency key for mutating requests.

    Returns
    -------
    dict[str, str]
        Headers suitable for drama generation HTTP calls.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Drama-Session-Id": session_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def save_json(path: Path, payload: Any) -> None:
    """Write JSON payload to ``path``, creating parent directories as needed.

    Parameters
    ----------
    path
        Destination file.
    payload
        JSON-serializable value.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def resolve_session_id(*, env_session: str | None) -> str:
    """Resolve a drama session id from env or generate a harness id.

    Parameters
    ----------
    env_session
        Value of ``FICTORA_DRAMA_GENERATION_SESSION_ID`` when set.

    Returns
    -------
    str
        Non-empty session id.
    """
    if env_session and env_session.strip():
        return env_session.strip()
    return f"create-flow-{uuid.uuid4().hex[:12]}"


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json()
    except Exception:
        detail = response.text
    raise SystemExit(f"HTTP {response.status_code}: {detail}")


def _payload_summary(payload: dict[str, Any]) -> str:
    summary = {
        key: payload[key]
        for key in ("status", "progress", "job_id", "spine_id", "error", "failure", "message", "code")
        if key in payload
    }
    return json.dumps(summary, default=str)


def _get_json_with_transport_retries(
    client: httpx.Client,
    *,
    url: str,
    headers: dict[str, str],
    label: str,
    deadline: float,
) -> dict[str, Any]:
    """GET JSON from a poll URL, retrying transient transport failures until ``deadline``.

    Parameters
    ----------
    client
        Shared HTTP client.
    url
        Absolute job status URL.
    headers
        GET headers (typically without ``Content-Type``).
    label
        Human-readable poll label for errors.
    deadline
        Monotonic time after which retries stop.

    Returns
    -------
    dict[str, Any]
        Parsed JSON body.

    Raises
    ------
    SystemExit
        On HTTP error responses or when the deadline passes during retries.
    """
    backoff_seconds = 1.0
    max_backoff_seconds = 30.0

    while True:
        now = time.monotonic()
        if now >= deadline:
            raise SystemExit(f"timed out polling {label} after transport retries")
        try:
            response = client.get(url, headers=headers)
            _raise_for_status(response)
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            raise SystemExit(f"poll {label}: expected JSON object, got {type(payload).__name__}")
        except SystemExit:
            raise
        except httpx.HTTPError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SystemExit(f"transport error polling {label}: {exc}") from exc
            sleep_for = min(backoff_seconds, max_backoff_seconds, remaining)
            time.sleep(sleep_for)
            backoff_seconds = min(backoff_seconds * 2, max_backoff_seconds)


def poll_until_terminal(
    client: httpx.Client,
    *,
    url: str,
    headers: dict[str, str],
    emit: Callable[..., None] | None,
    label: str,
    deadline_seconds: float,
    interval_seconds: float = 20.0,
) -> dict[str, Any]:
    """Poll a drama job URL until it reaches a terminal status or times out.

    Parameters
    ----------
    client
        Shared HTTP client.
    url
        Absolute job status URL.
    headers
        GET headers (typically without ``Content-Type``).
    emit
        Optional callback invoked on each poll tick.
    label
        Human-readable poll label for errors.
    deadline_seconds
        Maximum wall time before ``SystemExit``.
    interval_seconds
        Sleep between polls.

    Returns
    -------
    dict[str, Any]
        Terminal job JSON payload.

    Raises
    ------
    SystemExit
        On HTTP failure or timeout.
    """
    deadline = time.monotonic() + deadline_seconds
    poll_headers = {key: value for key, value in headers.items() if key != "Content-Type"}
    last_payload: dict[str, Any] = {}

    while time.monotonic() < deadline:
        payload = _get_json_with_transport_retries(
            client,
            url=url,
            headers=poll_headers,
            label=label,
            deadline=deadline,
        )
        last_payload = payload
        status = payload.get("status")
        progress = payload.get("progress")

        if emit is not None:
            emit_args: dict[str, Any] = {"status": status, "progress": progress}
            if "job_id" in payload:
                emit_args["job_id"] = payload["job_id"]
            emit(label, **emit_args)

        if status in _TERMINAL:
            return payload

        time.sleep(interval_seconds)

    raise SystemExit(
        f"timed out polling {label} after {deadline_seconds:.0f}s; last payload: {_payload_summary(last_payload)}"
    )
