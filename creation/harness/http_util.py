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


HOSTED_POST_OFF_HINT = (
    "Hosted post-production is switched off on the Drama API (hosted_post_off). This is not an outage and "
    "nothing to retry: finish the take on this laptop with `uv run fictora-produce finish --desk <desk>` "
    "(sound effects, music, mix, captions, mark)."
)


def hosted_post_off(status_code: int, detail: Any, url: str = "") -> bool:
    """True when a response is the API refusing hosted post-production.

    The API answers ``409 hosted_post_off``; older deployments answered the
    post-production route with a bare ``503 restate_unavailable``. A 503 on any
    other route is a real outage and is not matched.

    Parameters
    ----------
    status_code
        HTTP status.
    detail
        Parsed JSON body (or text).
    url
        Request URL.

    Returns
    -------
    bool
        Whether the operator should run ``finish`` instead.
    """

    error = detail.get("error") if isinstance(detail, dict) else None
    code = str(error.get("code") or "") if isinstance(error, dict) else ""
    if code == "hosted_post_off" or (status_code == 409 and "hosted_post_off" in str(detail)):
        return True
    return status_code == 503 and "post-production" in url and code in {"restate_unavailable", ""}


def http_error_message(response: httpx.Response, detail: Any) -> str:
    """The ``SystemExit`` text for a failed response (hosted-post refusals point at ``finish``)."""

    url = str(response.request.url) if response.request is not None else ""
    if hosted_post_off(response.status_code, detail, url):
        return f"HTTP {response.status_code}: {HOSTED_POST_OFF_HINT}"
    return f"HTTP {response.status_code}: {api_error_text(detail)}"


def api_error_text(body: Any) -> str:
    """Say what the server refused, from its error envelope, in one line.

    The API answers ``{"error": {"code", "message", "details"}, "request_id"}``.
    A 409 or 422 carries the real rule in ``code``, ``message`` and ``details``
    (a take missing an approved line, an authoring rule, why an enrol
    conflicts); this keeps all three instead of a bare status.

    Parameters
    ----------
    body
        Parsed response body (or raw text).

    Returns
    -------
    str
        ``code: message (details …) [request_id]`` or the raw body when it is not an envelope.
    """
    if not isinstance(body, dict):
        return str(body)[:2000]
    error = body.get("error") if isinstance(body.get("error"), dict) else body
    code = error.get("code")
    message = error.get("message")
    if not code and not message:
        return json.dumps(body, default=str)[:2000]
    text = f"{code or 'error'}: {message or ''}".rstrip(": ")
    details = error.get("details")
    if details:
        text += f" (details {json.dumps(details, default=str, ensure_ascii=False)[:1200]})"
    if body.get("request_id"):
        text += f" [request {body['request_id']}]"
    return text


def describe_job_error(job: dict[str, Any]) -> str:
    """Say why a job ended, from its ``error`` (code, message, the first rule details).

    Parameters
    ----------
    job
        Terminal ``GET /v1/jobs/{id}`` or ``/v1/video-generations/{id}`` body.

    Returns
    -------
    str
        ``status code: message (details …)``; ``status (no error detail)`` when there is none.
    """
    status = str(job.get("status") or "unknown")
    for key in ("error", "failure"):
        error = job.get(key)
        if isinstance(error, dict) and (error.get("code") or error.get("message")):
            return f"{status} {api_error_text(error)}"
        if error:
            return f"{status} {str(error)[:1200]}"
    return f"{status} (no error detail)"


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail: Any = response.json()
    except ValueError:
        detail = response.text
    raise SystemExit(http_error_message(response, detail))


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
