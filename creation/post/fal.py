"""Fal queue calls for local post, with the request id kept on the desk so a re-run never pays twice.

Local post uses the producer's own ``FAL_KEY`` (repo ``.env``, never printed)
for four public endpoints: Eleven v3 voices, ElevenLabs sound effects v2,
Stable Audio 2.5 music and Whisper. Nothing here holds a server prompt.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from creation.harness.env import load_env_file

VOICE_ENDPOINT = "fal-ai/elevenlabs/tts/eleven-v3"
SFX_ENDPOINT = "fal-ai/elevenlabs/sound-effects/v2"
MUSIC_ENDPOINT = "fal-ai/stable-audio-25/text-to-audio"
WHISPER_ENDPOINT = "fal-ai/whisper"

PENDING_FILENAME = "post-fal-pending.json"
"""Desk-root ledger of Fal requests admitted but not yet collected."""


class FalJobFailed(RuntimeError):
    """Fal answered a request with a failure."""


class FalCalls(Protocol):
    """The Fal calls local post makes (a fake in tests)."""

    def submit(self, endpoint: str, arguments: dict[str, Any]) -> str:
        """Queue one request; return its request id."""
        ...

    def result(self, endpoint: str, request_id: str) -> dict[str, Any]:
        """Wait for one request and return its output."""
        ...

    def upload(self, path: Path) -> str:
        """Upload a local file; return a URL Fal can read."""
        ...


def repo_root() -> Path:
    """This repository's root (where ``.env`` lives)."""

    return Path(__file__).resolve().parents[2]


def require_fal_key() -> None:
    """Load ``FAL_KEY`` from the repo ``.env`` when unset; never print it.

    Raises
    ------
    RuntimeError
        When no key is available.
    """

    load_env_file(repo_root() / ".env")
    if not os.environ.get("FAL_KEY", "").strip():
        raise RuntimeError(
            "FAL_KEY is not set. Local post (voices, sound effects, music, Whisper) runs on your own Fal key: "
            "put FAL_KEY=... in the repo .env (see .env.example). Never print it."
        )


class FalClientCalls:
    """``fal_client`` queue calls. Needs ``FAL_KEY``."""

    def __init__(self) -> None:
        require_fal_key()

    def submit(self, endpoint: str, arguments: dict[str, Any]) -> str:
        """Queue one request.

        Parameters
        ----------
        endpoint
            Fal endpoint id.
        arguments
            Endpoint arguments.

        Returns
        -------
        str
            Request id.
        """

        import fal_client

        return str(fal_client.submit(endpoint, arguments=arguments).request_id)

    def result(self, endpoint: str, request_id: str) -> dict[str, Any]:
        """Wait for one request.

        Parameters
        ----------
        endpoint
            Fal endpoint id.
        request_id
            From :meth:`submit`.

        Returns
        -------
        dict[str, Any]
            Output.

        Raises
        ------
        FalJobFailed
            When Fal failed the request.
        """

        import fal_client

        try:
            output = fal_client.result(endpoint, request_id)
        except fal_client.FalClientHTTPError as exc:
            raise FalJobFailed(f"Fal request {request_id} failed: {str(exc)[:300]}") from exc
        if not isinstance(output, dict):
            raise FalJobFailed(f"Fal request {request_id} answered without an output object")
        return output

    def upload(self, path: Path) -> str:
        """Upload a local file to Fal storage.

        Parameters
        ----------
        path
            File.

        Returns
        -------
        str
            Public URL.
        """

        import fal_client

        return str(fal_client.upload_file(path))


def _ledger(desk: Path) -> dict[str, str]:
    path = desk / PENDING_FILENAME
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in payload.items()} if isinstance(payload, dict) else {}


def _write_ledger(desk: Path, ledger: dict[str, str]) -> None:
    (desk / PENDING_FILENAME).write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class FalRun:
    """One collected Fal request."""

    request_id: str
    output: dict[str, Any]
    path: Path


def run_fal_once(
    desk: Path,
    *,
    unit: str,
    endpoint: str,
    arguments: dict[str, Any],
    fal: FalCalls,
    collect: Callable[[dict[str, Any]], Path],
) -> FalRun:
    """Submit one request (recording its id first) and collect it; a re-run picks the same request up.

    Parameters
    ----------
    desk
        Series desk; pending ids live in ``post-fal-pending.json``.
    unit
        Stable name for this request across re-runs.
    endpoint
        Fal endpoint id.
    arguments
        Endpoint arguments.
    fal
        Queue calls.
    collect
        Saves the output; the unit stays pending until it succeeds.

    Returns
    -------
    FalRun
        Request id, output and saved file.

    Raises
    ------
    FalJobFailed
        When Fal failed the request (the unit is cleared so the next run submits afresh).
    """

    ledger = _ledger(desk)
    request_id = ledger.get(unit)
    if request_id is None:
        request_id = fal.submit(endpoint, arguments)
        ledger[unit] = request_id
        _write_ledger(desk, ledger)
    try:
        output = fal.result(endpoint, request_id)
    except FalJobFailed:
        ledger.pop(unit, None)
        _write_ledger(desk, ledger)
        raise
    saved = collect(output)
    ledger = _ledger(desk)
    ledger.pop(unit, None)
    _write_ledger(desk, ledger)
    return FalRun(request_id=request_id, output=output, path=saved)


def output_url(output: dict[str, Any], key: str = "audio") -> str:
    """The file URL in a Fal output (``{key: {url}}`` or ``{key: [{url}]}``).

    Raises
    ------
    RuntimeError
        When the output carries no URL under ``key``.
    """

    value = output.get(key)
    if isinstance(value, list):
        value = value[0] if value else None
    url = value.get("url") if isinstance(value, dict) else None
    if not url:
        raise RuntimeError(f"Fal answered without {key}.url")
    return str(url)


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
