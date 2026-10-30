"""finish and join ask the server for the finished episode and download it (item 01, phase 4; 30 Oct 2026).

Every call goes to a stand-in server (``httpx.MockTransport``): the posting-copy
route, the video and the cover downloads. Nothing reaches a real server and
nothing is finished on the laptop.
"""

from __future__ import annotations

import errno
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from creation.cli_produce import main
from creation.post import episode_name, server_finish
from creation.post.desk import saved_spine
from creation.post.server_finish import (
    SERVER_FINISH_NOT_DONE,
    ask_posting_copy,
    run_server_finish,
)
from creation.spine_view import episode_id_for

BASE = "https://drama.example"
VIDEO_URL = "https://media.example/tenants/t/drama/exports/ep01-marked.mp4"
COVER_URL = "https://media.example/tenants/t/drama/covers/ep01.jpg"
VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42 the finished episode"
COVER_BYTES = b"\xff\xd8\xff the cover"
HEADERS = {
    "Authorization": "Bearer kit-token",
    "X-Drama-Session-Id": "content-ops-test",
}
DAY = date(2026, 10, 30)


def answer(**over: Any) -> dict[str, Any]:
    body = {
        "schema_version": "fictora.drama-episode-posting-copy.v1",
        "spine_id": "spine_test",
        "episode_id": "episode_01",
        "generation_job_id": "job_video_1",
        "video_url": VIDEO_URL,
        "file_name": "drama_ep01_draft_v1_2026-07-31.mp4",  # the server's own name: the kit keeps its own
        "source_sha256": "a" * 64,
        "cached": False,
        "cover_url": COVER_URL,
        "cover_hook": ["She kept the key.", "He kept the lock."],
        "done_at": "2026-10-30T12:00:00Z",
    }
    body.update(over)
    return body


class StandIn:
    """The stand-in server: answers the route and the downloads, and keeps what it was asked."""

    def __init__(
        self, *answers: tuple[int, Any], delay: float = 0.0, timeout: bool = False
    ) -> None:
        self.answers = list(answers) or [(200, answer())]
        self.posts: list[httpx.Request] = []
        self.gets: list[str] = []
        self.delay = delay
        self.timeout = timeout

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            self.posts.append(request)
            if self.timeout:
                raise httpx.ReadTimeout("slow", request=request)
            if self.delay:
                time.sleep(self.delay)
            status, body = self.answers[min(len(self.posts), len(self.answers)) - 1]
            if isinstance(body, str):
                return httpx.Response(status, text=body)
            return httpx.Response(status, json=body)
        self.gets.append(str(request.url))
        if str(request.url) == VIDEO_URL:
            return httpx.Response(200, content=VIDEO_BYTES)
        if str(request.url) == COVER_URL:
            return httpx.Response(200, content=COVER_BYTES)
        return httpx.Response(404, text="Not Found")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


@pytest.fixture(autouse=True)
def _pinned_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(episode_name, "today", lambda: DAY)


def finish(desk: Path, server: StandIn, **kw: Any) -> server_finish.ServerFinish:
    return run_server_finish(
        desk,
        episode=1,
        api=(BASE, HEADERS),
        transport=server.transport,
        sleep=lambda _s: None,
        **kw,
    )


def takes(desk: Path) -> list[str]:
    return sorted(p.name for p in (desk / "ep01" / "takes").iterdir())


def test_finish_asks_the_server_and_saves_the_episode_and_cover_under_todays_names(
    post_desk: Path,
) -> None:
    server = StandIn()

    done = finish(post_desk, server)

    assert done.complete, done.error
    # One call to the server's posting-copy route, on the desk's story and episode, with the kit's key.
    (post,) = server.posts
    episode_id = episode_id_for(saved_spine(post_desk, 1)[0], 1)
    assert post.url.path == f"/v1/spines/spine_test/episodes/{episode_id}/posting-copy"
    assert post.headers["Authorization"] == "Bearer kit-token"
    assert post.headers["X-Drama-Session-Id"] == "content-ops-test"
    # Today's readable name and the cover's versioned name, in the episode's takes folder.
    folder = post_desk / "ep01" / "takes"
    assert done.video == folder / "post-test_ep01_draft_v1_2026-10-30.mp4"
    assert done.video.read_bytes() == VIDEO_BYTES
    assert done.cover == folder / "cover-ep01-v1.jpg"
    assert done.cover.read_bytes() == COVER_BYTES
    assert server.gets == [VIDEO_URL, COVER_URL]
    assert done.done_at == "2026-10-30T12:00:00Z"
    # Nothing finished on the laptop: only the two files and their notes, no take steps or finish records.
    assert takes(post_desk) == [
        "cover-ep01-v1.jpg",
        "cover-ep01-v1.json",
        "post-test_ep01_draft_v1_2026-10-30.json",
        "post-test_ep01_draft_v1_2026-10-30.mp4",
    ]


def test_a_repeat_reuses_the_files_already_on_the_laptop(post_desk: Path) -> None:
    first = finish(post_desk, StandIn())
    server = StandIn((200, answer(cached=True)))

    again = finish(post_desk, server)

    assert again.complete and again.reused
    assert again.video == first.video and again.cover == first.cover
    assert server.gets == []  # nothing downloaded twice
    assert len(list((post_desk / "ep01" / "takes").glob("*.mp4"))) == 1


def test_a_new_cut_is_the_next_version(post_desk: Path) -> None:
    finish(post_desk, StandIn())
    new_video = VIDEO_URL.replace("ep01-marked", "ep01-refilmed")

    def answers(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200, json=answer(video_url=new_video, cover_url=COVER_URL)
            )
        return httpx.Response(200, content=b"new cut")

    done = run_server_finish(
        post_desk,
        episode=1,
        api=(BASE, HEADERS),
        transport=httpx.MockTransport(answers),
    )

    assert done.video is not None
    assert done.video.name == "post-test_ep01_draft_v2_2026-10-30.mp4"
    assert done.video.read_bytes() == b"new cut"
    assert (
        done.cover is not None and done.cover.name == "cover-ep01-v1.jpg"
    )  # same cover reused


def test_no_finished_episode_yet_is_a_plain_stop_and_nothing_is_made(
    post_desk: Path,
) -> None:
    server = StandIn(
        (
            404,
            {
                "error": {
                    "code": "episode_video_not_delivered",
                    "message": "not delivered",
                }
            },
        )
    )

    done = finish(post_desk, server)

    assert not done.complete
    assert "no finished episode 1 yet" in (done.error or "")
    assert server.gets == []
    assert takes(post_desk) == []


def test_a_failed_copy_is_not_asked_again_and_says_what_to_do(post_desk: Path) -> None:
    server = StandIn(
        (502, {"error": {"code": "episode_export_failed", "message": "encode failed"}})
    )

    done = finish(post_desk, server)

    assert not done.complete
    assert len(server.posts) == 1
    assert "could not make the copy to post" in (done.error or "")
    assert "Nothing was saved and nothing was charged" in (done.error or "")
    assert takes(post_desk) == []


def test_a_busy_gateway_is_asked_again_within_the_wait(post_desk: Path) -> None:
    server = StandIn((502, "Bad Gateway"), (200, answer()))
    done = finish(post_desk, server)

    assert done.complete
    assert len(server.posts) == 2


def test_an_older_server_without_the_route_says_so(post_desk: Path) -> None:
    done = finish(post_desk, StandIn((404, {"detail": "Not Found"})))

    assert not done.complete
    assert "cannot finish kit episodes yet" in (done.error or "")


def test_the_wait_has_a_timeout_with_a_plain_line(post_desk: Path) -> None:
    done = finish(post_desk, StandIn(timeout=True), wait_seconds=5)

    assert not done.complete
    assert "did not answer within 5 s" in (done.error or "")
    assert "run finish again" in (done.error or "")


def test_progress_lines_while_the_server_finishes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = StandIn(delay=0.25)
    lines: list[str] = []

    body = ask_posting_copy(
        base_url=BASE,
        headers=HEADERS,
        spine_id="spine_test",
        episode_id="episode_01",
        episode=1,
        transport=server.transport,
        say=lines.append,
        progress_every=0.05,
    )

    assert body["video_url"] == VIDEO_URL
    assert any("Still finishing on the server" in line for line in lines)


def test_a_link_off_https_is_not_downloaded(post_desk: Path) -> None:
    done = finish(
        post_desk,
        StandIn((200, answer(video_url="http://media.example/ep01.mp4"))),
    )

    assert not done.complete
    assert "not https" in (done.error or "")
    assert takes(post_desk) == []


def test_no_cover_still_delivers_the_video(post_desk: Path) -> None:
    done = finish(post_desk, StandIn((200, answer(cover_url=None, cover_hook=None))))

    assert done.complete and done.cover is None
    assert any("No cover this time" in note for note in done.notes)


# --- the commands ----------------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["finish", "join"])
def test_finish_and_join_are_the_server_call(
    command: str,
    post_desk: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from creation import cli_post

    asked: list[tuple[Path, int]] = []

    def fake(desk: Path, *, episode: int, wait_seconds: float, **_kw: Any) -> Any:
        asked.append((desk, episode))
        return server_finish.ServerFinish(
            episode, video=desk / "ep01" / "takes" / "x.mp4", seconds=42.0
        )

    monkeypatch.setattr(cli_post, "run_server_finish", fake)

    code = main([command, "--desk", str(post_desk), "--episode", "1", "--json"])

    assert code == 0
    assert asked == [(post_desk.resolve(), 1)]
    assert json.loads(capsys.readouterr().out)["complete"] is True


def test_not_done_exits_with_the_not_done_code(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation import cli_post

    monkeypatch.setattr(
        cli_post,
        "run_server_finish",
        lambda desk, **_kw: server_finish.ServerFinish(1, error="The server is busy."),
    )

    code = main(["finish", "--desk", str(post_desk), "--episode", "1"])

    assert code == SERVER_FINISH_NOT_DONE
    assert "NOT DONE" in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra",
    [
        ["--music", "calmer"],
        ["--no-cover"],
        ["--impact-at", "1.2", "--sfx-adjust", "door=-6"],
        ["--episodes", "1", "2"],
    ],
)
def test_old_laptop_settings_are_refused_by_name_and_the_server_is_not_called(
    extra: list[str],
    post_desk: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from creation import cli_post

    called: list[Any] = []
    monkeypatch.setattr(cli_post, "run_server_finish", lambda *a, **k: called.append(a))

    code = main(["finish", "--desk", str(post_desk), "--episode", "1", *extra])

    assert code == 2
    assert called == []
    err = capsys.readouterr().err
    assert "not used any more" in err and extra[0] in err


# --- saving on this laptop ------------------------------------------------------------------------


def test_a_folder_without_hard_links_still_saves_by_rename(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_links(*_a: Any) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(server_finish.os, "link", no_links)

    done = finish(post_desk, StandIn())

    assert done.complete and done.video is not None
    assert done.video.read_bytes() == VIDEO_BYTES
    assert not list((post_desk / "ep01" / "takes").glob(".*.part"))


def test_a_full_disk_is_a_plain_line_not_a_traceback(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*_a: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(server_finish.os, "link", full)

    done = finish(post_desk, StandIn())

    assert not done.complete
    assert "could not be saved on this laptop (No space left on device" in (
        done.error or ""
    )
    assert not list((post_desk / "ep01" / "takes").glob("*.mp4"))


def test_the_link_note_is_written_before_the_video_so_a_repeat_never_downloads_twice(
    post_desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_note(*_a: Any) -> None:
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(server_finish, "_write_sidecar", no_note)

    done = finish(post_desk, StandIn())

    # No video without its note: a later finish cannot mistake it for another cut and save v2.
    assert not done.complete
    assert not list((post_desk / "ep01" / "takes").glob("*.mp4"))


# --- lines that came out wrong (the server's take line findings) ----------------------------------

OPEN = (
    409,
    {
        "error": {
            "code": "take_line_findings_open",
            "message": "Episode 1 has lines that came out wrong",
            "details": {
                "takes": [
                    {
                        "generation_job_id": "job_video_1",
                        "take_index": 2,
                        "take_number": 2,
                        "listen_url": "https://media.example/t2.mp4",
                        "findings": [
                            {
                                "kind": "missing",
                                "line": "You kept it?",
                                "heard": "",
                                "listen_start_seconds": 3.5,
                                "message": "MISSING: line 2 'You kept it?' was not heard",
                            }
                        ],
                    }
                ]
            },
        }
    },
)


def test_an_open_line_finding_stops_with_each_line_and_the_two_ways_on(
    post_desk: Path,
) -> None:
    server = StandIn(OPEN)

    done = finish(post_desk, server)

    assert not done.complete
    error = done.error or ""
    assert "MISSING: line 2 'You kept it?' was not heard (at 3.5 s)" in error
    assert "t2: listen: https://media.example/t2.mp4" in error
    assert "--keep-line-findings t2" in error and "--take t2" in error
    assert len(server.posts) == 1  # no keep is sent without the human's word
    assert takes(post_desk) == []


def test_keep_line_findings_records_keep_it_then_finishes(post_desk: Path) -> None:
    server = StandIn(OPEN, (200, answer()), (200, answer()))

    done = finish(post_desk, server, keep_line_findings=("t2",))

    assert done.complete, done.error
    paths = [r.url.path for r in server.posts]
    assert paths[1] == "/v1/video-generations/job_video_1/takes/2/decision"
    assert json.loads(server.posts[1].content) == {
        "action": "use",
        "accept_line_findings": True,
    }
    assert paths[0] == paths[2] and paths[0].endswith("/posting-copy")


def test_keeping_another_take_does_not_keep_this_one(post_desk: Path) -> None:
    server = StandIn(OPEN)

    done = finish(post_desk, server, keep_line_findings=("t1",))

    assert not done.complete
    assert len(server.posts) == 1


def test_a_finding_not_about_the_words_prints_the_servers_message() -> None:
    takes = [
        {
            "take_number": 1,
            "findings": [
                {
                    "kind": "speaker_not_on_screen",
                    "line": "Get out.",
                    "heard": "",
                    "message": "SPEAKER NOT ON SCREEN: line 1 (Walt) plays over shot 2, which shows Ines but not Walt",
                },
                {"kind": "speaker_not_on_screen", "line": "Get out.", "heard": ""},
                {"kind": "missing", "line": "You kept it?", "heard": ""},
            ],
        }
    ]

    text = server_finish.line_findings_text(takes, episode=1)

    assert "t1: SPEAKER NOT ON SCREEN: line 1 (Walt) plays over shot 2" in text
    assert 't1: speaker_not_on_screen: line "Get out."' in text
    assert (
        "speaker_not_on_screen: script" not in text
        and 'heard ""' not in text.split("missing")[0]
    )
    assert 't1: missing: script "You kept it?", heard ""' in text
