"""The one interface local post uses for generated audio: voices, effects, music, transcripts.

No provider key lives on a producer's laptop. Generated audio is made **on the
server** by the Drama API's operator audio routes, which answer with a durable
URL in our storage (or word timings). Everything else in local post - mix,
duck, colour match, captions, watermark, revoice mute and lay-in - is ffmpeg on
the laptop.

Routes (bearer token, ``X-Drama-Session-Id`` and ``Idempotency-Key`` on each):

- ``POST /v1/spines/{id}/cast/{cast_id}/voice-auditions/render``
- ``POST /v1/spines/{id}/cast/{cast_id}/voice-lines``
- ``POST /v1/spines/{id}/sfx-cues``
- ``POST /v1/spines/{id}/audio-bed/render``
- ``POST /v1/transcripts`` (only for audio already in our storage: this kit
  never uploads local files; it transcribes the take's stored URL)

Each call's ``Idempotency-Key`` is stable across re-runs, so a replay returns
the first answer and never pays twice. ``429`` and ``409
operator_audio_in_progress`` are waited out per ``Retry-After``. A busy or
restarting server (any ``502``/``503``/``504``, ``operator_audio_failed`` and
``operator_audio_timed_out`` included) and a dropped connection are asked again
with the same key through the kit's one retry policy
(:func:`creation.harness.http_util.send_with_retries`: ``Retry-After`` or 10,
20 s jittered, three tries in all, which is what the server allows per key):
safe, because a replayed key or a repeated cue returns the first answer
(L-20261005-18). Every request has an
overall deadline, waits included (a ``cue`` 120 s): past it the command stops
with a message saying to re-run it, never waiting on "in progress" forever.
``cost_usd`` in the answers is operator-only: it goes to run notes and the desk
ledger, never to printed output.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx

from creation.harness.http_util import (
    api_error_text,
    send_with_retries,
    transient_status,
)

#: Most waits on 429 / in-progress before giving up (the rate bucket refills ~1 per 6 s).
MAX_WAITS = 12
#: Longest single wait honoured from ``Retry-After``.
MAX_WAIT_SECONDS = 60.0
#: Replays of a request the server answered 502/503/504 (``operator_audio_failed``,
#: ``operator_audio_timed_out``, a gateway error) or whose connection dropped
#: (the server allows three attempts per key).
MAX_FAILED_REPLAYS = 2
#: Longest one ``cue`` waits for the server, waits on 429 / in progress included.
#: The server cancels its own SFX call sooner, so a stall normally comes back
#: as a 504 first; this is the kit's own floor. A cue that hung >10 min with
#: no output on 2026-09-29 came back in seconds on retry.
CUE_DEADLINE_SECONDS = 120.0
#: Longest any other generated-audio request waits: auditions and a voice line
#: make several provider calls, and a music bed is the slowest single call.
DEFAULT_DEADLINE_SECONDS = 600.0

HINTS = {
    "operator_audio_unavailable": "the Drama API has no media storage configured for generated audio; tell engineering",
    "operator_upload_unavailable": "the Drama API cannot take uploads; this kit only transcribes stored take URLs",
    "voice_not_locked": "this character has no locked voice: run `voice --audition`, then `voice --pick N`",
    "audio_url_not_owned": "the transcript needs a file in our storage (the take's stored URL), not a local file",
    "idempotency_conflict": "this request key was used with another body; tell engineering (the desk's keys are stable)",
    "budget_cap_exceeded": "the audio budget cap is reached; tell engineering",
    "operator_audio_timed_out": "the audio provider kept timing out on the server; run the same command "
    "again later (it replays the same Idempotency-Key, so nothing is paid twice)",
    "voice_audition_unknown_voice": "use a voice from the catalog the server listed; nothing was spent",
    "voice_audition_voice_count": "name 1-10 voices in --voices; nothing was spent",
    "voice_audition_text_too_long": "shorten --text to 300 characters or fewer; nothing was spent",
    "voice_audition_text_language": "write --text in the show's language; nothing was spent",
    "voice_audition_text_blank": "--text needs some words; nothing was spent",
    "voice_audition_text_or_lines": "give --text or let the audition use the character's lines, not both; "
    "nothing was spent",
}


class AudioServiceError(RuntimeError):
    """The server refused or failed a generated-audio request (message says what to do)."""


class AudioServiceTimeout(AudioServiceError):
    """The server gave no answer within the request's deadline (message says how to retry)."""


class AudioService(Protocol):
    """What local post asks the server to generate. Each method returns the route's JSON answer."""

    def render_auditions(
        self,
        *,
        spine_id: str,
        cast_id: str,
        spine_version: str,
        lines: list[str],
        count: int,
        key: str,
        text: str | None = None,
        voices: list[str] | None = None,
    ) -> dict[str, Any]:
        """``{candidates: [{voice_id, text, audio_url, seconds}], cost_usd}``.

        ``text`` (new wording) goes instead of ``lines``; ``voices`` (catalog names,
        read in that order) goes instead of the default slate of ``count``.
        """
        ...

    def voice_line(
        self,
        *,
        spine_id: str,
        cast_id: str,
        text: str,
        language: str,
        key: str,
        spoken_text: str | None = None,
    ) -> dict[str, Any]:
        """``{audio_url, seconds, provider_voice, reading: {checked, read_right, …}, cost_usd}``.

        ``text`` is the performed line in the show's ``language``; ``spoken_text`` (when the
        script has one) is what the voice model is sent, so the server's reading re-check applies.
        """
        ...

    def sfx_cue(
        self, *, spine_id: str, sound: str, seconds: float, key: str
    ) -> dict[str, Any]:
        """``{audio_url, kind, shape_problem, cached, cost_usd}``."""
        ...

    def music_bed(
        self, *, spine_id: str, brief: str | None, key: str
    ) -> dict[str, Any]:
        """``{audio_url, seconds, cached, cost_usd}`` (not pinned on the spine)."""
        ...

    def transcribe(
        self, *, audio_url: str, language: str, spine_id: str | None, key: str
    ) -> dict[str, Any]:
        """``{words: [{word, start, end}], text}`` for a file already in our storage."""
        ...


class DramaApiAudio:
    """:class:`AudioService` on the Drama API operator audio routes.

    Parameters
    ----------
    desk
        Series desk (its session id and credentials).
    episode
        Episode whose ``api/`` folder the session writes to.
    sleep
        Injected for tests.
    clock
        Monotonic seconds; injected for tests.
    """

    def __init__(
        self,
        desk: Path,
        *,
        episode: int = 1,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.desk = desk
        self.episode = episode
        self.sleep = sleep
        self.clock = clock

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        key: str,
        *,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    ) -> dict[str, Any]:
        from creation.post.desk import open_api

        run = open_api(self.desk, self.episode)
        try:
            return post_with_retries(
                run.client,
                run.url(path),
                run.headers(key),
                body,
                sleep=self.sleep,
                deadline_seconds=deadline_seconds,
                clock=self.clock,
            )
        finally:
            run.client.close()

    def render_auditions(
        self,
        *,
        spine_id: str,
        cast_id: str,
        spine_version: str,
        lines: list[str],
        count: int,
        key: str,
        text: str | None = None,
        voices: list[str] | None = None,
    ) -> dict[str, Any]:
        """POST ``…/voice-auditions/render``.

        The body carries ``text`` instead of ``lines`` when given, and ``voices``
        instead of ``candidate_count`` when given, exactly as passed: the server
        owns every rule on them. A body with neither is byte-identical to before,
        so an existing request key replays.
        """

        body: dict[str, Any] = {"spine_version": spine_version}
        body.update({"text": text} if text is not None else {"lines": lines})
        body.update({"voices": list(voices)} if voices else {"candidate_count": count})
        return self._post(
            f"/v1/spines/{spine_id}/cast/{cast_id}/voice-auditions/render", body, key
        )

    def voice_line(
        self,
        *,
        spine_id: str,
        cast_id: str,
        text: str,
        language: str,
        key: str,
        spoken_text: str | None = None,
    ) -> dict[str, Any]:
        """POST ``…/voice-lines``."""

        body: dict[str, Any] = {"text": text, "language": language}
        if spoken_text:
            body["spoken_text"] = spoken_text
        return self._post(
            f"/v1/spines/{spine_id}/cast/{cast_id}/voice-lines", body, key
        )

    def sfx_cue(
        self, *, spine_id: str, sound: str, seconds: float, key: str
    ) -> dict[str, Any]:
        """POST ``/v1/spines/{id}/sfx-cues`` with the :data:`CUE_DEADLINE_SECONDS` deadline."""

        return self._post(
            f"/v1/spines/{spine_id}/sfx-cues",
            {"sound": sound, "seconds": seconds},
            key,
            deadline_seconds=CUE_DEADLINE_SECONDS,
        )

    def music_bed(
        self, *, spine_id: str, brief: str | None, key: str
    ) -> dict[str, Any]:
        """POST ``/v1/spines/{id}/audio-bed/render`` (never pinned from here)."""

        body: dict[str, Any] = {"pin": False}
        if brief:
            body["brief"] = brief
        return self._post(f"/v1/spines/{spine_id}/audio-bed/render", body, key)

    def transcribe(
        self, *, audio_url: str, language: str, spine_id: str | None, key: str
    ) -> dict[str, Any]:
        """POST ``/v1/transcripts`` for a stored URL."""

        body: dict[str, Any] = {"audio_url": audio_url, "language": language}
        if spine_id:
            body["spine_id"] = spine_id
        return self._post("/v1/transcripts", body, key)


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        return min(
            MAX_WAIT_SECONDS,
            max(0.0, float(response.headers.get("Retry-After", default))),
        )
    except ValueError:
        return default


def _code(detail: Any) -> str:
    error = detail.get("error") if isinstance(detail, dict) else None
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def _timed_out(url: str, deadline_seconds: float, why: str) -> AudioServiceTimeout:
    route = url.rsplit("/v1/", 1)[-1]
    return AudioServiceTimeout(
        f"{route}: no answer from the server within {deadline_seconds:g} s ({why}); nothing was saved. "
        "Run the same command again: it sends the same Idempotency-Key, so a render the server "
        "finished is returned without paying twice. If it stops here again, tell engineering."
    )


def post_with_retries(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """POST one operator audio request, waiting out rate limits and replaying a failed render.

    Parameters
    ----------
    client
        HTTP client.
    url
        Absolute route URL.
    headers
        Bearer, session and ``Idempotency-Key`` headers.
    body
        JSON body.
    sleep
        Wait function.
    deadline_seconds
        Longest the whole call may take, every request and wait included.
    clock
        Monotonic seconds.

    Returns
    -------
    dict[str, Any]
        The route's JSON answer.

    Raises
    ------
    AudioServiceTimeout
        When the deadline passes (a request with no answer, or waits that would
        run past it).
    AudioServiceError
        On a refusal, or when waits and replays run out.
    """

    started = clock()

    def left() -> float:
        return deadline_seconds - (clock() - started)

    waits = 0
    while True:
        remaining = left()
        if remaining <= 0:
            raise _timed_out(url, deadline_seconds, "the deadline passed")
        try:
            # Every operator audio route replays its key (or caches the cue by its content),
            # so a busy server or a dropped connection is asked again (L-20261005-18).
            response = send_with_retries(
                lambda: client.post(
                    url, headers=headers, json=body, timeout=max(left(), 0.001)
                ),
                attempts=MAX_FAILED_REPLAYS + 1,
                sleep=sleep,
                time_left=left,
                retry_timeouts=False,
            )
        except httpx.TimeoutException as exc:
            raise _timed_out(
                url, deadline_seconds, "the request got no answer"
            ) from exc
        if response.is_success:
            return response.json()
        try:
            detail: Any = response.json()
        except ValueError:
            detail = response.text
        code = _code(detail)
        if response.status_code == 429 or code == "operator_audio_in_progress":
            if waits < MAX_WAITS and code != "budget_cap_exceeded":
                waits += 1
                wait = _retry_after(
                    response, 15.0 if code == "operator_audio_in_progress" else 6.0
                )
                if wait >= left():
                    raise _timed_out(
                        url,
                        deadline_seconds,
                        "the server still says in progress"
                        if code == "operator_audio_in_progress"
                        else "still rate limited",
                    )
                sleep(wait)
                continue
        elif transient_status(response.status_code, detail, url):
            route = url.rsplit("/v1/", 1)[-1]
            raise AudioServiceError(
                f"HTTP {response.status_code} {route}: the server stayed busy after "
                f"{MAX_FAILED_REPLAYS + 1} tries ({api_error_text(detail)}); nothing was saved. "
                "Run the same command again in a few minutes: it sends the same Idempotency-Key, so "
                "nothing is paid twice."
            )
        hint = HINTS.get(code, "")
        raise AudioServiceError(
            f"HTTP {response.status_code} {url.rsplit('/v1/', 1)[-1]}: {api_error_text(detail)}"
            + (f" -> {hint}" if hint else "")
        )


def download(url: str, dest: Path) -> Path:
    """GET ``url`` into ``dest`` (parent created).

    Returns
    -------
    Path
        ``dest``.
    """

    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        dest.write_bytes(response.content)
    return dest
