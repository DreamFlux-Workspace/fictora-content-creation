"""``cut-rename``, ``cut-retry``, ``cut-save`` and ``show-rename`` against a stand-in server (drama #1333, #1335, #1337)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from creation.post.edit_actions import (
    cut_action_command,
    rename_cut,
    retry_cut,
    save_cut,
)
from creation.post.picked_cut import CutStop
from creation.post.show_rename import rename_show

API = ("https://drama.example", {"Authorization": "Bearer kit-token"})
CUT = {
    "episodes": [1, 2],
    "compilation_url": "https://media.example/all.mp4",
    "parts": [{"part": 1, "video_url": "https://media.example/p1.mp4"}],
}
NOT_FOUND = (404, {"detail": "Not Found"})
NO_METHOD = (405, {"detail": "Method Not Allowed"})


def refusal(status: int, code: str, message: str) -> tuple[int, Any]:
    """A refusal as the series-cut routes write it: flat keys plus the usual envelope."""

    return status, {
        "code": code,
        "message": message,
        "error": {"code": code, "message": message, "details": {}},
    }


class Server:
    """Answers each ``METHOD path`` from its own queue (the last answer repeats) and records every ask."""

    def __init__(self, routes: dict[str, list[tuple[int, Any]]]) -> None:
        self.routes = routes
        self.seen: list[tuple[str, str, dict[str, str], Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        key = f"{request.method} {request.url.path}"
        self.seen.append(
            (request.method, request.url.path, dict(request.url.params), body)
        )
        queue = self.routes.get(key)
        if not queue:
            return httpx.Response(404, json={"detail": "Not Found"})
        status, answer = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=answer)

    def asks(self, method: str, path: str) -> list[tuple[dict[str, str], Any]]:
        return [(q, b) for m, p, q, b in self.seen if m == method and p == path]


def call(server: Server) -> dict[str, Any]:
    return {"api": API, "transport": httpx.MockTransport(server)}


def waits() -> dict[str, Any]:
    now = [0.0]
    return {
        "sleep": lambda s: now.__setitem__(0, now[0] + s),
        "clock": lambda: now[0],
        "progress": lambda line: None,
    }


# cut-rename


def test_rename_sends_only_the_new_name_and_prints_the_servers_name() -> None:
    server = Server(
        {
            "PATCH /v1/series-cuts/c1": [
                (200, {"series_cut_id": "c1", "status": "ready", "title": "Lock Night"})
            ]
        }
    )
    lines = rename_cut("c1", "  Lock Night ", **call(server))
    assert server.asks("PATCH", "/v1/series-cuts/c1") == [({}, {"title": "Lock Night"})]
    assert lines[0].startswith('The edit c1 is now called "Lock Night".')
    assert "not made again" in lines[0]


def test_empty_rename_clears_the_name_back_to_the_shows() -> None:
    server = Server(
        {
            "PATCH /v1/series-cuts/c1": [
                (200, {"series_cut_id": "c1", "status": "ready", "title": None})
            ]
        }
    )
    lines = rename_cut("c1", "", **call(server))
    assert server.asks("PATCH", "/v1/series-cuts/c1") == [({}, {"title": None})]
    assert "first show's name again" in lines[0]


def test_rename_a_bad_name_prints_the_servers_reason() -> None:
    message = "title: the name needs at least one visible character"
    server = Server(
        {
            "PATCH /v1/series-cuts/c1": [
                (422, {"error": {"code": "invalid_request", "message": message}})
            ]
        }
    )
    with pytest.raises(CutStop) as stop:
        rename_cut("c1", "​", **call(server))
    assert stop.value.line == f"NOT DONE: {message} Nothing was changed."


def test_rename_an_unknown_edit_says_to_check_the_id() -> None:
    server = Server(
        {
            "PATCH /v1/series-cuts/nope": [
                refusal(
                    404, "series_cut_not_found", "Series cut is not available: nope"
                )
            ]
        }
    )
    with pytest.raises(CutStop, match="Check the edit id"):
        rename_cut("nope", "X", **call(server))


def test_rename_on_a_server_without_the_route_says_so() -> None:
    server = Server({"PATCH /v1/series-cuts/c1": [NO_METHOD]})
    with pytest.raises(CutStop, match="can't rename an edit yet"):
        rename_cut("c1", "X", **call(server))


# cut-retry


def test_retry_runs_the_same_edit_by_id_and_never_sends_the_picks() -> None:
    server = Server(
        {
            "POST /v1/series-cuts/c1/retry": [
                (202, {"series_cut_id": "c1", "status": "queued"})
            ],
            "GET /v1/series-cuts/c1": [
                (200, {"series_cut_id": "c1", "status": "making"}),
                (
                    200,
                    {
                        "series_cut_id": "c1",
                        "status": "ready",
                        "episodes": [{}, {}, {}],
                        "series_cut": CUT,
                    },
                ),
            ],
        }
    )
    said: list[str] = []
    lines = retry_cut("c1", **call(server), **{**waits(), "progress": said.append})
    assert server.asks("POST", "/v1/series-cuts/c1/retry") == [({}, None)]
    assert not server.asks("POST", "/v1/series-cuts")
    assert said[0].startswith("Making the same edit again")
    assert lines[0].startswith("The server cut these 3 picked episodes")
    assert "Whole show: https://media.example/all.mp4" in lines


def test_retry_of_a_ready_edit_prints_it_without_running_it_again() -> None:
    server = Server(
        {
            "POST /v1/series-cuts/c1/retry": [
                (202, {"series_cut_id": "c1", "status": "ready"})
            ],
            "GET /v1/series-cuts/c1": [
                (200, {"status": "ready", "episodes": [{}, {}], "series_cut": CUT})
            ],
        }
    )
    said: list[str] = []
    lines = retry_cut("c1", **call(server), **{**waits(), "progress": said.append})
    assert said == []
    assert "Whole show: https://media.example/all.mp4" in lines


def test_retry_that_fails_again_offers_the_same_retry() -> None:
    server = Server(
        {
            "POST /v1/series-cuts/c1/retry": [
                (202, {"series_cut_id": "c1", "status": "queued"})
            ],
            "GET /v1/series-cuts/c1": [
                (200, {"status": "failed", "reason": "episode 2 has no sound"})
            ],
        }
    )
    with pytest.raises(CutStop, match="episode 2 has no sound") as stop:
        retry_cut("c1", **call(server), **waits())
    assert stop.value.line.endswith("fictora-produce cut-retry c1")


def test_retry_on_a_server_without_the_route_says_so() -> None:
    server = Server({"POST /v1/series-cuts/c1/retry": [NOT_FOUND]})
    with pytest.raises(CutStop, match="can't try an edit again by its id yet"):
        retry_cut("c1", **call(server), **waits())


# cut-save


def export(status: str, **over: Any) -> dict[str, Any]:
    return {
        "series_cut_id": "c1",
        "part": 2,
        "music": True,
        "status": status,
        "watermark": "sokii",
        "file_name": "lock-night-part-2-sokii.mp4",
        "warnings": [],
        **over,
    }


def media() -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"marked-video")
    )


def test_save_asks_then_polls_the_same_file_and_downloads_it(tmp_path: Path) -> None:
    ready = export(
        "ready",
        export_url="https://media.example/c1-p2.mp4",
        warnings=["Some episodes had no mark."],
    )
    server = Server(
        {
            "POST /v1/series-cuts/c1/export": [(202, export("queued"))],
            "GET /v1/series-cuts/c1/export": [(200, export("making")), (200, ready)],
        }
    )
    lines = save_cut(
        "c1",
        part=2,
        folder=tmp_path,
        media_transport=media(),
        **call(server),
        **waits(),
    )
    assert server.asks("POST", "/v1/series-cuts/c1/export") == [
        ({}, {"part": 2, "music": True})
    ]
    assert {
        q
        for q, _ in [
            (json.dumps(q), b)
            for q, b in server.asks("GET", "/v1/series-cuts/c1/export")
        ]
    } == {json.dumps({"music": "true", "part": "2"})}
    saved = tmp_path / "lock-night-part-2-sokii.mp4"
    assert saved.read_bytes() == b"marked-video"
    assert lines[0] == (
        f"Saved part 2 with the Sokii mark, with the edit's music, ready for Instagram and TikTok: {saved}"
    )
    assert "Note: Some episodes had no mark." in lines


def test_save_of_the_whole_edit_without_music_sends_no_part(tmp_path: Path) -> None:
    ready = export(
        "ready",
        part=None,
        music=False,
        export_url="https://media.example/w.mp4",
        file_name="e-whole-no-music-sokii.mp4",
    )
    server = Server({"POST /v1/series-cuts/c1/export": [(202, ready)]})
    lines = save_cut(
        "c1",
        music=False,
        folder=tmp_path,
        media_transport=media(),
        **call(server),
        **waits(),
    )
    assert server.asks("POST", "/v1/series-cuts/c1/export") == [
        ({}, {"part": None, "music": False})
    ]
    assert not server.asks("GET", "/v1/series-cuts/c1/export")
    assert lines[0].startswith(
        "Saved the whole edit with the Sokii mark, with no added music"
    )


def test_save_never_writes_over_a_file_already_there(tmp_path: Path) -> None:
    (tmp_path / "lock-night-part-2-sokii.mp4").write_bytes(b"older")
    ready = export("ready", export_url="https://media.example/c1-p2.mp4")
    server = Server({"POST /v1/series-cuts/c1/export": [(202, ready)]})
    save_cut(
        "c1",
        part=2,
        folder=tmp_path,
        media_transport=media(),
        **call(server),
        **waits(),
    )
    assert (tmp_path / "lock-night-part-2-sokii.mp4").read_bytes() == b"older"
    assert (tmp_path / "lock-night-part-2-sokii-2.mp4").read_bytes() == b"marked-video"


def test_save_keeps_the_server_file_name_inside_the_folder(tmp_path: Path) -> None:
    ready = export(
        "ready",
        export_url="https://media.example/x.mp4",
        file_name="../../escape-sokii.mp4",
    )
    server = Server({"POST /v1/series-cuts/c1/export": [(202, ready)]})
    save_cut(
        "c1",
        folder=tmp_path / "out",
        media_transport=media(),
        **call(server),
        **waits(),
    )
    assert (tmp_path / "out" / "escape-sokii.mp4").exists()


def test_save_that_fails_prints_the_reason_in_plain_words(tmp_path: Path) -> None:
    server = Server(
        {
            "POST /v1/series-cuts/c1/export": [(202, export("queued"))],
            "GET /v1/series-cuts/c1/export": [
                (200, export("failed", reason="the media worker stopped"))
            ],
        }
    )
    with pytest.raises(
        CutStop, match=r"the media worker stopped\)\. Run the same cut-save again"
    ):
        save_cut("c1", part=2, folder=tmp_path, **call(server), **waits())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("code", "message", "step"),
    [
        (
            "series_cut_not_ready",
            "This edit is still being made; save it once it is ready.",
            "Run cut-retry",
        ),
        (
            "series_cut_part_not_found",
            "This edit has no part 9.",
            "Use a part number that cut printed",
        ),
        (
            "series_cut_no_music_copy",
            "This edit has no copy without its music.",
            "Leave out --no-music",
        ),
        (
            "series_cut_remake_needed",
            "This edit was made before its titles moved clear of the Sokii mark.",
            "Tell the core team",
        ),
        ("series_cut_show_gone", "A show in this edit was deleted.", ""),
    ],
)
def test_save_refusals_print_the_servers_words_and_what_to_do(
    tmp_path: Path, code: str, message: str, step: str
) -> None:
    status = 404 if code.endswith("not_found") else 409
    server = Server(
        {"POST /v1/series-cuts/c1/export": [refusal(status, code, message)]}
    )
    with pytest.raises(CutStop) as stop:
        save_cut("c1", part=9, folder=tmp_path, **call(server), **waits())
    assert stop.value.line.startswith(f"NOT DONE: {message} Nothing was saved.")
    assert step in stop.value.line


def test_save_waits_no_longer_than_asked(tmp_path: Path) -> None:
    server = Server(
        {
            "POST /v1/series-cuts/c1/export": [(202, export("queued"))],
            "GET /v1/series-cuts/c1/export": [(200, export("making"))],
        }
    )
    with pytest.raises(
        CutStop, match="still being made after 30 s. Run the same cut-save again later"
    ):
        save_cut(
            "c1", part=2, folder=tmp_path, wait_seconds=30, **call(server), **waits()
        )


def test_save_on_a_server_without_the_route_says_so(tmp_path: Path) -> None:
    server = Server({"POST /v1/series-cuts/c1/export": [NOT_FOUND]})
    with pytest.raises(CutStop, match="can't save an edit with the Sokii mark yet"):
        save_cut("c1", folder=tmp_path, **call(server), **waits())


def test_save_part_zero_is_a_bad_ask(capsys: pytest.CaptureFixture[str]) -> None:
    args = SimpleNamespace(
        command="cut-save",
        id="c1",
        part=0,
        no_music=False,
        to=Path("."),
        wait=None,
        json=False,
    )
    assert cut_action_command(args) == 2
    assert "--part starts at 1" in capsys.readouterr().err


# show-rename


def spine(version: str, title: str) -> dict[str, Any]:
    return {"spine": {"spine_id": "sp_a", "spine_version": version, "title": title}}


def conflict() -> tuple[int, Any]:
    return 409, {
        "error": {
            "code": "spine_version_conflict",
            "message": "The story spine version is stale.",
            "details": {"latest_spine_version": "v2"},
        }
    }


def test_show_rename_sends_only_the_title_with_the_current_version() -> None:
    server = Server(
        {
            "GET /v1/spines/sp_a": [(200, spine("v1", "Exit Strategy"))],
            "PATCH /v1/spines/sp_a": [(200, spine("v9", "Lock Night"))],
        }
    )
    lines = rename_show("sp_a", "Lock Night", **call(server))
    assert server.asks("PATCH", "/v1/spines/sp_a") == [
        ({}, {"spine_version": "v1", "patch": {"title": "Lock Night"}})
    ]
    assert lines[0].startswith('The show "Exit Strategy" is now called "Lock Night".')


def test_show_rename_reads_the_show_again_and_resends_once_on_a_version_conflict() -> (
    None
):
    server = Server(
        {
            "GET /v1/spines/sp_a": [
                (200, spine("v1", "Exit Strategy")),
                (200, spine("v2", "Exit Strategy")),
            ],
            "PATCH /v1/spines/sp_a": [conflict(), (200, spine("v3", "Lock Night"))],
        }
    )
    rename_show("sp_a", "Lock Night", **call(server))
    sent = [
        body["spine_version"] for _, body in server.asks("PATCH", "/v1/spines/sp_a")
    ]
    assert sent == ["v1", "v2"]
    assert len(server.asks("GET", "/v1/spines/sp_a")) == 2


def test_show_rename_stops_after_a_second_conflict() -> None:
    server = Server(
        {
            "GET /v1/spines/sp_a": [(200, spine("v1", "Exit Strategy"))],
            "PATCH /v1/spines/sp_a": [conflict()],
        }
    )
    with pytest.raises(CutStop, match="Run the same show-rename again"):
        rename_show("sp_a", "Lock Night", **call(server))
    assert len(server.asks("PATCH", "/v1/spines/sp_a")) == 2


def test_show_rename_on_a_server_that_wants_a_cascade_says_it_needs_the_update() -> (
    None
):
    server = Server(
        {
            "GET /v1/spines/sp_a": [(200, spine("v1", "Exit Strategy"))],
            "PATCH /v1/spines/sp_a": [
                (
                    409,
                    {
                        "error": {
                            "code": "cascade_required",
                            "message": "Approved spines need a cascade.",
                        }
                    },
                )
            ],
        }
    )
    with pytest.raises(CutStop, match="needs the show-rename update"):
        rename_show("sp_a", "Lock Night", **call(server))


def test_show_rename_reads_a_desk_folders_own_show_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("creation.post.desk.spine_id", lambda desk: "sp_a")
    server = Server(
        {
            "GET /v1/spines/sp_a": [(200, spine("v1", "Same"))],
            "PATCH /v1/spines/sp_a": [(200, spine("v1", "Same"))],
        }
    )
    assert rename_show(str(tmp_path), "Same", **call(server)) == [
        'The show is already called "Same". Nothing was changed.'
    ]


@pytest.mark.parametrize(
    "command", ["cut-rename", "cut-retry", "cut-save", "show-rename"]
)
def test_commands_are_registered(command: str) -> None:
    from creation.cli_produce import main

    with pytest.raises(SystemExit) as done:
        main([command, "--help"])
    assert done.value.code == 0
