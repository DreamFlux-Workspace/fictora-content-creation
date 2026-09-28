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
operator_audio_in_progress`` are waited out per ``Retry-After``; ``502
operator_audio_failed`` is replayed with the same key (the server retries up
to three attempts). ``cost_usd`` in the answers is operator-only: it goes to
run notes and the desk ledger, never to printed output.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx

from creation.harness.http_util import api_error_text

#: Most waits on 429 / in-progress before giving up (the rate bucket refills ~1 per 6 s).
MAX_WAITS = 12
#: Longest single wait honoured from ``Retry-After``.
MAX_WAIT_SECONDS = 60.0
#: Replays of a ``502 operator_audio_failed`` (the server allows three attempts per key).
MAX_FAILED_REPLAYS = 2

HINTS = {
    "operator_audio_unavailable": "the Drama API has no media storage configured for generated audio; tell engineering",
    "operator_upload_unavailable": "the Drama API cannot take uploads; this kit only transcribes stored take URLs",
    "voice_not_locked": "this character has no locked voice: run `voice --audition`, then `voice --pick N`",
    "audio_url_not_owned": "the transcript needs a file in our storage (the take's stored URL), not a local file",
    "idempotency_conflict": "this request key was used with another body; tell engineering (the desk's keys are stable)",
    "budget_cap_exceeded": "the audio budget cap is reached; tell engineering",
}


class AudioServiceError(RuntimeError):
    """The server refused or failed a generated-audio request (message says what to do)."""


class AudioService(Protocol):
    """What local post asks the server to generate. Each method returns the route's JSON answer."""

    def render_auditions(
        self, *, spine_id: str, cast_id: str, spine_version: str, lines: list[str], count: int, key: str
    ) -> dict[str, Any]:
        """``{candidates: [{voice_id, text, audio_url, seconds}], cost_usd}``."""
        ...

    def voice_line(
        self, *, spine_id: str, cast_id: str, text: str, language: str, key: str, spoken_text: str | None = None
    ) -> dict[str, Any]:
        """``{audio_url, seconds, provider_voice, reading: {checked, read_right, …}, cost_usd}``.

        ``text`` is the performed line in the show's ``language``; ``spoken_text`` (when the
        script has one) is what the voice model is sent, so the server's reading re-check applies.
        """
        ...

    def sfx_cue(self, *, spine_id: str, sound: str, seconds: float, key: str) -> dict[str, Any]:
        """``{audio_url, kind, shape_problem, cached, cost_usd}``."""
        ...

    def music_bed(self, *, spine_id: str, brief: str | None, key: str) -> dict[str, Any]:
        """``{audio_url, seconds, cached, cost_usd}`` (not pinned on the spine)."""
        ...

    def transcribe(self, *, audio_url: str, language: str, spine_id: str | None, key: str) -> dict[str, Any]:
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
    """

    def __init__(self, desk: Path, *, episode: int = 1, sleep: Callable[[float], None] = time.sleep) -> None:
        self.desk = desk
        self.episode = episode
        self.sleep = sleep

    def _post(self, path: str, body: dict[str, Any], key: str) -> dict[str, Any]:
        from creation.post.desk import open_api

        run = open_api(self.desk, self.episode)
        try:
            return post_with_retries(run.client, run.url(path), run.headers(key), body, sleep=self.sleep)
        finally:
            run.client.close()

    def render_auditions(
        self, *, spine_id: str, cast_id: str, spine_version: str, lines: list[str], count: int, key: str
    ) -> dict[str, Any]:
        """POST ``…/voice-auditions/render``."""

        body = {"spine_version": spine_version, "lines": lines, "candidate_count": count}
        return self._post(f"/v1/spines/{spine_id}/cast/{cast_id}/voice-auditions/render", body, key)

    def voice_line(
        self, *, spine_id: str, cast_id: str, text: str, language: str, key: str, spoken_text: str | None = None
    ) -> dict[str, Any]:
        """POST ``…/voice-lines``."""

        body: dict[str, Any] = {"text": text, "language": language}
        if spoken_text:
            body["spoken_text"] = spoken_text
        return self._post(f"/v1/spines/{spine_id}/cast/{cast_id}/voice-lines", body, key)

    def sfx_cue(self, *, spine_id: str, sound: str, seconds: float, key: str) -> dict[str, Any]:
        """POST ``/v1/spines/{id}/sfx-cues``."""

        return self._post(f"/v1/spines/{spine_id}/sfx-cues", {"sound": sound, "seconds": seconds}, key)

    def music_bed(self, *, spine_id: str, brief: str | None, key: str) -> dict[str, Any]:
        """POST ``/v1/spines/{id}/audio-bed/render`` (never pinned from here)."""

        body: dict[str, Any] = {"pin": False}
        if brief:
            body["brief"] = brief
        return self._post(f"/v1/spines/{spine_id}/audio-bed/render", body, key)

    def transcribe(self, *, audio_url: str, language: str, spine_id: str | None, key: str) -> dict[str, Any]:
        """POST ``/v1/transcripts`` for a stored URL."""

        body: dict[str, Any] = {"audio_url": audio_url, "language": language}
        if spine_id:
            body["spine_id"] = spine_id
        return self._post("/v1/transcripts", body, key)


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        return min(MAX_WAIT_SECONDS, max(0.0, float(response.headers.get("Retry-After", default))))
    except ValueError:
        return default


def _code(detail: Any) -> str:
    error = detail.get("error") if isinstance(detail, dict) else None
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def post_with_retries(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
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

    Returns
    -------
    dict[str, Any]
        The route's JSON answer.

    Raises
    ------
    AudioServiceError
        On a refusal, or when waits and replays run out.
    """

    waits = replays = 0
    while True:
        response = client.post(url, headers=headers, json=body)
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
                sleep(_retry_after(response, 15.0 if code == "operator_audio_in_progress" else 6.0))
                continue
        elif response.status_code == 502 and code == "operator_audio_failed" and replays < MAX_FAILED_REPLAYS:
            replays += 1
            continue
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
