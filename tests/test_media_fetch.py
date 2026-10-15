"""Media downloads always send a browser user agent and try again after a refusal.

GRAFT, 14 Oct 2026 (L-20261014-21): stem and music URLs came back 403 until
they were fetched with ``curl -A "Mozilla/5.0"``. The media host (Cloudflare
R2, public bucket) answers 403 "error code: 1010" to Python urllib's default
user agent. Every kit download now goes through ``creation.media_fetch``, and
``fictora-ops fetch`` is the same call for an operator.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from creation import cli_ops, media_fetch
from creation.media_fetch import (
    BROWSER_USER_AGENT,
    MediaFetchError,
    get_media,
    media_client,
)

URL = "https://pub-x.r2.dev/tenants/t/drama/dialogue-track/abc.wav"


def _like_the_media_host(request: httpx.Request) -> httpx.Response:
    """403 1010 to anything that is not a browser, as Cloudflare answers urllib."""

    if "Mozilla/" not in request.headers.get("user-agent", ""):
        return httpx.Response(403, text="error code: 1010")
    return httpx.Response(200, content=b"RIFFstem")


def test_a_client_built_without_headers_still_sends_a_browser_user_agent() -> None:
    client = httpx.Client(transport=httpx.MockTransport(_like_the_media_host))
    assert get_media(client, URL, sleep=lambda _s: None) == b"RIFFstem"


def test_the_media_client_sends_a_browser_user_agent() -> None:
    client = media_client()
    assert client.headers["user-agent"] == BROWSER_USER_AGENT
    assert "Mozilla/5.0" in BROWSER_USER_AGENT


def test_a_refusal_at_the_edge_is_tried_again() -> None:
    answers = iter(
        [
            httpx.Response(403, text="error code: 1010"),
            httpx.Response(200, content=b"ok"),
        ]
    )
    waits: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: next(answers)))
    assert get_media(client, URL, sleep=waits.append) == b"ok"
    assert waits == [1.0]


def test_a_dropped_connection_is_tried_again() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200, content=b"ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert get_media(client, URL, sleep=lambda _s: None) == b"ok"


def test_a_missing_file_is_not_retried_and_says_so_plainly() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MediaFetchError) as caught:
        get_media(client, URL, sleep=lambda _s: None)
    assert calls["n"] == 1
    assert "HTTP 404" in str(caught.value) and "abc.wav" in str(caught.value)
    # Handlers that caught a failed download before still catch it.
    assert isinstance(caught.value, httpx.HTTPError)


def test_stems_and_music_downloads_go_through_the_media_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``audio_service.download`` (stems, tracks, music) used a bare httpx client."""

    monkeypatch.setattr(
        media_fetch,
        "media_client",
        lambda timeout=180.0: httpx.Client(
            transport=httpx.MockTransport(_like_the_media_host)
        ),
    )
    from creation.post.audio_service import download

    out = download(URL, tmp_path / "ep01" / "t1.stem.wav")
    assert out.read_bytes() == b"RIFFstem"


def test_the_reel_client_sends_a_browser_user_agent() -> None:
    from creation.post import reel_server

    assert reel_server.MEDIA_HEADERS["User-Agent"] == BROWSER_USER_AGENT


def test_fetch_command_saves_a_new_file_and_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        media_fetch,
        "media_client",
        lambda timeout=180.0: httpx.Client(
            transport=httpx.MockTransport(_like_the_media_host)
        ),
    )
    out = tmp_path / "stems" / "t1-stem-v1.wav"
    assert cli_ops.main(["fetch", URL, "--out", str(out)]) == 0
    assert out.read_bytes() == b"RIFFstem"
    out.write_bytes(b"mine")
    assert cli_ops.main(["fetch", URL, "--out", str(out)]) == 2
    assert out.read_bytes() == b"mine"


def test_fetch_command_refuses_a_non_https_url(tmp_path: Path) -> None:
    assert (
        cli_ops.main(
            ["fetch", "http://pub-x.r2.dev/a.wav", "--out", str(tmp_path / "a.wav")]
        )
        == 2
    )


def test_fetch_command_reports_a_failed_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(media_fetch, "RETRY_WAITS", ())
    monkeypatch.setattr(
        media_fetch,
        "media_client",
        lambda timeout=180.0: httpx.Client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(503))
        ),
    )
    out = tmp_path / "a.wav"
    assert cli_ops.main(["fetch", URL, "--out", str(out)]) == 1
    assert not out.exists()
    assert "nothing was saved" in capsys.readouterr().err


def test_the_skill_points_operators_at_fetch_not_urllib() -> None:
    root = Path(__file__).resolve().parents[1]
    for skill in (".claude", ".cursor"):
        text = (root / skill / "skills/episode-production/SKILL.md").read_text(
            encoding="utf-8"
        )
        assert "fictora-ops fetch" in text and "urllib" in text
