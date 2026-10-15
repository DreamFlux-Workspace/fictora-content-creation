"""``tempo`` is set on the server: picture and sound sped together, every time moved (L-20261014-20).

The local edit left the take facts on the filmed times, so captions went up
about 0.9 s late. Now the kit only asks: ``--preview`` sends a dry run and
changes nothing; a tempo is sent under a key built from the desk, take and
tempo (a retry is the same request), the take facts (on the new timeline) are
saved again and the new take becomes the newest raw take; a take already at
that speed is fetched again; only a server without the route (or a take with
no video job on the desk) leaves it to the local edit, said so; any other
refusal stops with exit 2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fake_api import FakeApi
from test_take_trim_handles import FACTS_ROUTE, _desk_with_take, _facts

from creation import media_fetch
from creation.cli_produce import main as produce_main

TEMPO = "/v1/video-generations/job_video_1/takes/2/tempo"


def _answer(status: str = "applied") -> dict[str, Any]:
    return {
        "schema_version": "fictora.drama-take-tempo-response.v1",
        "job_id": "job_video_1",
        "take_index": 2,
        "take_job_id": "job_take_t1",
        "status": status,
        "factor": 1.5,
        "from_s": 0.417,
        "to_s": 15.0,
        "filmed_duration_s": 15.0,
        "duration_s": 10.14,
        "lines": [
            {"line_id": "l1", "filmed_start_s": 0.5, "start_s": 0.472, "end_s": 2.539}
        ],
        "video_url": "https://media.test/tempo/t1.mp4" if status != "planned" else None,
        "message": "Take 2 at 1.5x. Picture and sound change speed together.",
    }


def _tempo(desk: Path, *extra: str) -> int:
    return produce_main(
        ["tempo", "--desk", str(desk), "--episode", "1", "--take", "t1", *extra]
    )


def _posts(api: FakeApi) -> list[dict[str, Any] | None]:
    return [
        body
        for method, path, body, _key in api.calls
        if method == "POST" and path == TEMPO
    ]


def test_a_preview_sends_a_dry_run_and_changes_nothing(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _desk_with_take(desk)
    api.routes[("POST", TEMPO)] = _answer("planned")

    assert _tempo(desk, "--factor", "1.5", "--preview") == 0

    assert _posts(api) == [{"factor": 1.5, "dry_run": True}]
    text = capsys.readouterr().out
    assert "l1: filmed at 0.50 s, now 0.47-2.54 s" in text
    assert "Not applied" in text


def test_a_tempo_is_set_on_the_server_and_the_new_take_and_facts_are_saved(
    desk: Path,
    api: FakeApi,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _desk_with_take(desk)
    api.routes[("POST", TEMPO)] = _answer()
    moved = _facts()
    moved["take_facts"]["soundtrack"]["lines"][0].update(start_s=0.472, end_s=2.539)
    moved["take_facts"]["tempo"] = {"factor": 1.5, "from_s": 0.417, "to_s": 15.0,
                                    "filmed_duration_s": 15.0, "duration_s": 10.14}  # fmt: skip
    api.routes[("GET", FACTS_ROUTE)] = moved
    fetched: list[str] = []

    def download(
        client: Any,
        url: str,
        directory: Path,
        stem: str,
        *,
        default_suffix: str = ".png",
    ) -> Path:
        fetched.append(url)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stem}-v9.mp4"
        path.write_bytes(b"mp4")
        return path

    monkeypatch.setattr(media_fetch, "download_to_versioned", download)

    assert _tempo(desk, "--factor", "1.5", "--from", "0.417", "--to", "15") == 0

    assert _posts(api) == [
        {"factor": 1.5, "dry_run": False, "from_s": 0.417, "to_s": 15.0}
    ]
    assert fetched == ["https://media.test/tempo/t1.mp4"]
    saved = sorted((desk / "ep01" / "api").glob("take-facts-ep01-t1-v*.json"))
    assert len(saved) == 2 and '"tempo"' in saved[-1].read_text(encoding="utf-8")
    assert "take-ep01-t1-raw-v9.mp4" in capsys.readouterr().out


def test_a_server_without_the_route_is_left_to_the_local_edit_and_says_so(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _desk_with_take(desk)

    def missing(*_: Any) -> Any:
        raise SystemExit(f"HTTP 404 POST {TEMPO}: Not Found")

    api.routes[("POST", TEMPO)] = missing

    # Falls through to the local edit, which here finds no raw take on the desk (and says so).
    assert _tempo(desk, "--factor", "1.5") != 0

    captured = capsys.readouterr()
    assert "this server has no take tempo" in captured.out
    assert "no raw take take-ep01-t1-raw-vN.mp4" in captured.err


def test_a_model_sound_take_is_sent_to_the_server_too(desk: Path, api: FakeApi) -> None:
    """The server speeds the whole take now, its own sound included: no local fallback by sound."""

    _desk_with_take(desk)

    def native(*_: Any) -> Any:
        raise SystemExit(f"HTTP 409 POST {TEMPO}: take_tempo_native_sound: old server")

    api.routes[("POST", TEMPO)] = native

    assert _tempo(desk, "--factor", "1.5") == 2


def test_a_retry_is_the_same_request_and_a_take_already_at_that_speed_is_fetched_again(
    desk: Path,
    api: FakeApi,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _desk_with_take(desk)
    keys: list[str | None] = []

    def already(method: str, path: str, body: Any) -> Any:
        raise SystemExit(
            f"HTTP 409 POST {TEMPO}: take_tempo_already_set: Take 2 already plays at 1.5x. Revert it first"
        )

    api.routes[("POST", TEMPO)] = already
    api.routes[("GET", FACTS_ROUTE)] = _facts()
    fetched: list[str] = []

    def download(
        client: Any,
        url: str,
        directory: Path,
        stem: str,
        *,
        default_suffix: str = ".png",
    ) -> Path:
        fetched.append(url)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stem}-v9.mp4"
        path.write_bytes(b"mp4")
        return path

    monkeypatch.setattr(media_fetch, "download_to_versioned", download)

    assert _tempo(desk, "--factor", "1.5") == 0
    assert _tempo(desk, "--factor", "1.5") == 0
    keys = [
        key
        for method, path, _body, key in api.calls
        if method == "POST" and path == TEMPO
    ]

    assert (
        len(keys) == 2 and keys[0] == keys[1]
    )  # the same desk, take and tempo: the same key
    assert fetched and "Applied (already, at this speed)" in capsys.readouterr().out
    assert (
        _tempo(desk, "--factor", "1.2") == 2
    )  # a different speed is refused, not hidden


def test_a_take_with_no_video_job_on_the_desk_warns_before_the_local_edit(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _tempo(desk, "--factor", "1.5") != 0

    assert "the desk has no video job for this take" in capsys.readouterr().out
    assert _posts(api) == []


def test_a_refusal_stops_with_exit_2_and_never_runs_the_local_edit(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _desk_with_take(desk)

    def overrun(*_: Any) -> Any:
        raise SystemExit(
            f"HTTP 422 POST {TEMPO}: take_tempo_lines_overrun: line l3 would end at 10.6 s"
        )

    api.routes[("POST", TEMPO)] = overrun

    assert _tempo(desk, "--factor", "1.5") == 2
    assert "take_tempo_lines_overrun" in capsys.readouterr().err
