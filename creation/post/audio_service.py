"""The one interface local post uses for generated audio: voices, effects, music, transcripts.

No provider key lives on a producer's laptop. Generated audio (voice audition
samples, a dry line in a cast voice, a sound-effect cue, a music bed, a Whisper
transcript) is made **on the server**, behind Drama API operator endpoints that
answer with an audio URL (or word timings). Everything else in local post -
mix, duck, colour match, captions, watermark, revoice mute and lay-in - is
ffmpeg on the laptop.

:class:`AudioService` is that boundary. :class:`DramaApiAudio` is its
implementation against the Drama API; the operator routes are being added in a
separate server change, so until they are wired here every call raises
:class:`AudioServicePending` with a message the operator can act on, and
``finish`` reports NOT DONE. Tests use a fake.

Each call carries an idempotency ``key`` that is stable across re-runs, so the
server never renders (or bills) the same thing twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import httpx

PENDING_MESSAGE = (
    "generated audio (voices, sound effects, music, transcripts) comes from Drama API operator endpoints "
    "that are not live yet; no provider key is used on this laptop. Tell engineering; the ffmpeg steps "
    "(colour, mix, captions, mark) still ran"
)


class AudioServicePending(RuntimeError):
    """The Drama API does not offer this generated-audio endpoint yet."""


class AudioService(Protocol):
    """What local post asks the server to generate. Every method returns a fetchable result."""

    def audition_sample(self, *, text: str, voice: str, key: str) -> str:
        """Render one audition candidate (``voice`` saying ``text``); return its audio URL."""
        ...

    def voice_line(self, *, text: str, voice: str, key: str) -> str:
        """Render one dry line in a cast voice; return its audio URL."""
        ...

    def sfx_cue(self, *, sound: str, kind: str, seconds: float, key: str) -> str:
        """Render one sound effect from its authored Sound label; return its audio URL."""
        ...

    def music_bed(self, *, spine_id: str, genre: str | None, description: str | None, seconds: int, key: str) -> str:
        """Render the show's music bed (the server writes the music brief); return its audio URL."""
        ...

    def transcribe(self, *, media: Path, key: str) -> dict[str, Any]:
        """Whisper word timings for ``media``: ``{"chunks": [{"text", "timestamp": [start, end]}]}``."""
        ...


class DramaApiAudio:
    """:class:`AudioService` on the Drama API (operator endpoints; routes pending).

    Parameters
    ----------
    desk
        Series desk (its session and spine are used once the routes are wired).
    """

    def __init__(self, desk: Path) -> None:
        self.desk = desk

    def _pending(self, what: str) -> AudioServicePending:
        return AudioServicePending(f"{what}: {PENDING_MESSAGE}")

    def audition_sample(self, *, text: str, voice: str, key: str) -> str:
        """Pending server route."""

        raise self._pending("voice audition sample")

    def voice_line(self, *, text: str, voice: str, key: str) -> str:
        """Pending server route."""

        raise self._pending("dry voice line")

    def sfx_cue(self, *, sound: str, kind: str, seconds: float, key: str) -> str:
        """Pending server route."""

        raise self._pending("sound-effect cue")

    def music_bed(self, *, spine_id: str, genre: str | None, description: str | None, seconds: int, key: str) -> str:
        """Pending server route."""

        raise self._pending("music bed")

    def transcribe(self, *, media: Path, key: str) -> dict[str, Any]:
        """Pending server route."""

        raise self._pending("Whisper transcript")


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
