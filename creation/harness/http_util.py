"""HTTP helpers shared by create-flow harness sessions."""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from creation.harness.board_edit_refusal import board_edit_choice
from creation.harness.minor_scene_refusal import minor_scene_fix, says_minor_scene

_TERMINAL = frozenset({"completed", "failed", "cancelled"})

#: A running job whose ``updated_at`` and ``progress`` have not moved for this long
#: gets a plain "it may be stuck" warning (and again every this long). Never cancels.
STALE_JOB_SECONDS = 600.0


class PollClock(Protocol):
    """What the poll loop needs from a clock (``time`` itself, or a fake in tests)."""

    def monotonic(self) -> float:
        """Seconds on a clock that never goes back."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait ``seconds``."""
        ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: Any) -> datetime | None:
    """Read an ISO-8601 ``updated_at`` as UTC; ``None`` when it is not one."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stale_job_warning(
    *, minutes: int, last_update: datetime, job_id: str | None, desk: str | None
) -> str:
    """The plain warning for a job the server has stopped updating.

    Parameters
    ----------
    minutes
        Whole minutes since the job last changed.
    last_update
        When it last changed (UTC).
    job_id
        The job, for the cancel command (``<id>`` when unknown).
    desk
        The desk, for the cancel command (``D`` when unknown).

    Returns
    -------
    str
        One line to show the operator.
    """

    return (
        f"The server has not updated this job for {minutes} min "
        f"(last update {last_update.astimezone(timezone.utc):%H:%M} UTC). It may be stuck: "
        "check `fictora-produce status`; stop it with "
        f"`fictora-produce cancel-job --desk {desk or 'D'} --job-id {job_id or '<id>'}`."
    )


def _print_warning(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr, flush=True)


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
    if code == "hosted_post_off" or (
        status_code == 409 and "hosted_post_off" in str(detail)
    ):
        return True
    return (
        status_code == 503
        and "post-production" in url
        and code in {"restate_unavailable", ""}
    )


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
        ``code: message (details …) [request_id]`` or the raw body when it is not an envelope;
        a ``minor_in_intimate_scene`` refusal adds a line per scene with its fix.
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
        text += (
            f" (details {json.dumps(details, default=str, ensure_ascii=False)[:1200]})"
        )
    if body.get("request_id"):
        text += f" [request {body['request_id']}]"
    if says_minor_scene(code, message):
        # fictora-drama #562: name each scene and child and how to fix it, wherever the refusal comes from.
        text += "\n" + minor_scene_fix(details if isinstance(details, dict) else None)
    # fictora-drama #641: a board drawn before a script edit; the commands that clear it.
    choice = board_edit_choice(code, details if isinstance(details, dict) else None)
    if choice is not None:
        text += "\n" + choice
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
        for key in (
            "status",
            "progress",
            "job_id",
            "spine_id",
            "error",
            "failure",
            "message",
            "code",
        )
        if key in payload
    }
    return json.dumps(summary, default=str)


class ConnectionDropped(SystemExit):
    """A poll lost the connection; the job is still on the server.

    A ``SystemExit`` like every other stop, so callers that catch those are
    unchanged; ``step`` tells it apart and leaves the desk where it was, so the
    same command picks the job up instead of the desk being marked failed.
    """


def connection_dropped(label: str, exc: BaseException) -> str:
    """The line to print when a poll loses the connection.

    The job keeps running. The same command picks it up. Nothing is charged again.

    Parameters
    ----------
    label
        What was being waited on.
    exc
        The transport error. Only its type is shown.

    Returns
    -------
    str
        The operator line.
    """

    return (
        f"The connection dropped while waiting on {label}. The job is still on the server. "
        "Run the same command again. It picks up the same job. Nothing is charged twice. "
        f"({type(exc).__name__})"
    )


def _get_json_with_transport_retries(
    client: httpx.Client,
    *,
    url: str,
    headers: dict[str, str],
    label: str,
    deadline: float,
    clock: PollClock = time,
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
    clock
        Monotonic clock and sleep (``time``; a fake in tests).

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
        now = clock.monotonic()
        if now >= deadline:
            raise SystemExit(f"timed out polling {label} after transport retries")
        try:
            response = client.get(url, headers=headers)
            _raise_for_status(response)
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            raise SystemExit(
                f"poll {label}: expected JSON object, got {type(payload).__name__}"
            )
        except SystemExit:
            raise
        except httpx.HTTPError as exc:
            remaining = deadline - clock.monotonic()
            if remaining <= 0:
                raise ConnectionDropped(connection_dropped(label, exc)) from exc
            sleep_for = min(backoff_seconds, max_backoff_seconds, remaining)
            clock.sleep(sleep_for)
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
    job_id: str | None = None,
    desk: str | None = None,
    stale_after_seconds: float = STALE_JOB_SECONDS,
    warn: Callable[[str], None] | None = _print_warning,
    clock: PollClock = time,
    utc_now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Poll a drama job URL until it reaches a terminal status or times out.

    While the job runs, its ``updated_at`` and ``progress`` are watched. When
    neither has moved for ``stale_after_seconds`` (10 minutes), one plain warning
    says the job may be stuck and how to check and cancel it, and it is said
    again every ``stale_after_seconds`` while nothing moves. The poll never
    cancels or retries on its own, and the deadline is unchanged.

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
    job_id
        The job polled, named in the stuck warning's cancel command.
    desk
        The desk, named in the stuck warning's cancel command.
    stale_after_seconds
        How long without a change before the stuck warning (and between repeats).
    warn
        Where the stuck warning goes (stderr); ``None`` stays quiet.
    clock
        Monotonic clock and sleep (``time``; a fake in tests).
    utc_now
        Wall clock for the "last update" time when the job has no ``updated_at``.

    Returns
    -------
    dict[str, Any]
        Terminal job JSON payload.

    Raises
    ------
    SystemExit
        On HTTP failure or timeout.
    """
    deadline = clock.monotonic() + deadline_seconds
    poll_headers = {
        key: value for key, value in headers.items() if key != "Content-Type"
    }
    last_payload: dict[str, Any] = {}
    seen: tuple[Any, Any] | None = None
    changed_at = clock.monotonic()
    changed_utc = utc_now()
    next_warning = changed_at + stale_after_seconds

    while clock.monotonic() < deadline:
        payload = _get_json_with_transport_retries(
            client,
            url=url,
            headers=poll_headers,
            label=label,
            deadline=deadline,
            clock=clock,
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

        now = clock.monotonic()
        marker = (payload.get("updated_at"), progress)
        if marker != seen:
            seen = marker
            changed_at = now
            changed_utc = _parse_utc(payload.get("updated_at")) or utc_now()
            next_warning = now + stale_after_seconds
        elif warn is not None and now >= next_warning:
            warn(
                stale_job_warning(
                    minutes=int((now - changed_at) // 60),
                    last_update=changed_utc,
                    job_id=job_id or payload.get("job_id"),
                    desk=desk,
                )
            )
            next_warning += stale_after_seconds

        clock.sleep(interval_seconds)

    raise SystemExit(
        f"timed out polling {label} after {deadline_seconds:.0f}s; last payload: {_payload_summary(last_payload)}"
    )
