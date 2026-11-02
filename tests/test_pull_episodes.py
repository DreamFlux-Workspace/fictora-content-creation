"""pull-episodes brings the server's current episode files into the desk, never overwriting (2 Nov 2026).

The server is faked with ``httpx.MockTransport``: the standing route, the run's status and the downloads.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from creation.cli_post import POST_COMMANDS
from creation.post import episode_name, pull_episodes as pull
from creation.shared_spine import READ_OR_LOCAL_COMMANDS

BASE = "https://drama.example"
HEADERS = {
    "Authorization": "Bearer kit-token",
    "X-Drama-Session-Id": "content-ops-test",
}
URL1 = "https://media.example/ep01-v2.mp4"
URL2 = "https://media.example/ep02-v1.mp4"
BYTES = {URL1: b"ep1 repaired whole", URL2: b"ep2 current"}


def standing(*numbers: int) -> dict[str, Any]:
    return {
        "episodes": [
            {
                "episode_ordinal": n,
                "video": "finished",
                "video_job_id": f"job{n}",
                "episode_id": f"ep_{n:02d}",
                "duration_ms": 27000,
            }
            for n in numbers
        ]
    }


def job(n: int, url: str) -> dict[str, Any]:
    return {
        "output": {
            "scenes": [
                {"episode_id": f"ep_{n:02d}", "video_url": url, "duration_ms": 27000}
            ]
        }
    }


class Server:
    def __init__(self, standing_body: Any = None, missing: bool = False) -> None:
        self.standing = standing_body if standing_body is not None else standing(1)
        self.missing = missing
        self.jobs = {"job1": job(1, URL1), "job2": job(2, URL2)}
        self.paths: list[str] = []
        self.methods: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.methods.add(request.method)
        url = str(request.url)
        if url in BYTES:
            return httpx.Response(200, content=BYTES[url])
        self.paths.append(request.url.path)
        if request.url.path.endswith("/standing"):
            if self.missing:
                return httpx.Response(404, json={"detail": "Not Found"})
            return httpx.Response(200, json=self.standing)
        return httpx.Response(200, json=self.jobs[request.url.path.rsplit("/", 1)[1]])


@pytest.fixture(autouse=True)
def _day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(episode_name, "today", lambda: date(2026, 11, 2))
    monkeypatch.setattr(pull, "_local_seconds", lambda path: 13.0)


def run(desk: Path, server: Server, **kw: Any) -> tuple[int, str]:
    out = io.StringIO()
    code = pull.pull_episodes(
        desk,
        api=(BASE, HEADERS),
        transport=httpx.MockTransport(server),
        stream=out,
        **kw,
    )
    return code, out.getvalue()


def test_a_new_version_is_saved_beside_the_old_file_which_is_untouched(
    post_desk: Path,
) -> None:
    folder = post_desk / "ep01" / "takes"
    old = folder / "post-test_ep01_draft_v1_2026-10-30.mp4"
    old.write_bytes(b"half episode")

    code, text = run(post_desk, Server())

    new = folder / "post-test_ep01_draft_v2_2026-11-02.mp4"
    assert code == 0
    assert new.read_bytes() == BYTES[URL1]
    assert old.read_bytes() == b"half episode"
    assert f"ep 1: new version saved as {new.name} (27 s) in {folder}." in text
    assert (
        f"Your older version is still here if you need it: {old.name} (13 s)." in text
    )
    assert "1 new version(s) saved, 0 already up to date" in text


def test_a_second_pull_says_up_to_date_and_downloads_nothing(post_desk: Path) -> None:
    run(post_desk, Server())
    files = sorted(p.name for p in (post_desk / "ep01" / "takes").iterdir())

    code, text = run(post_desk, Server())

    assert code == 0
    assert "ep 1: already up to date" in text
    assert sorted(p.name for p in (post_desk / "ep01" / "takes").iterdir()) == files


def test_it_only_reads_the_server_and_never_overwrites_a_name_that_appeared(
    post_desk: Path,
) -> None:
    server = Server()
    run(post_desk, server)
    assert server.methods == {"GET"}
    assert server.paths[0] == "/v1/spines/spine_test/standing"
    assert server.paths[1] == "/v1/video-generations/job1"

    server.jobs["job1"] = job(1, URL2)  # the server repaired it again
    code, _ = run(post_desk, server)
    folder = post_desk / "ep01" / "takes"
    assert (folder / "post-test_ep01_draft_v1_2026-11-02.mp4").read_bytes() == BYTES[
        URL1
    ]
    assert (folder / "post-test_ep01_draft_v2_2026-11-02.mp4").read_bytes() == BYTES[
        URL2
    ]
    assert code == 0


def test_an_older_server_is_said_plainly_and_nothing_changes(post_desk: Path) -> None:
    code, text = run(post_desk, Server(missing=True))

    assert code == pull.PULL_NOT_DONE
    assert "cannot say which episode files are current yet" in text
    assert sorted((post_desk / "ep01" / "takes").iterdir()) == []


def test_one_episode_asks_only_for_that_episode(post_desk: Path) -> None:
    server = Server(standing(1, 2))

    code, text = run(post_desk, server, episode=2)

    assert code == 0
    assert "ep 2: new version saved" in text and "ep 1:" not in text
    assert server.paths == [
        "/v1/spines/spine_test/standing",
        "/v1/video-generations/job2",
    ]
    assert (
        post_desk / "ep02" / "takes" / "post-test_ep02_draft_v1_2026-11-02.mp4"
    ).read_bytes() == BYTES[URL2]


def test_an_episode_the_server_has_not_finished_is_not_done(post_desk: Path) -> None:
    code, text = run(post_desk, Server(), episode=3)

    assert code == pull.PULL_NOT_DONE
    assert "ep 3: the server has no finished episode 3 yet" in text


def test_the_command_is_registered_and_allowed_on_shared_stories() -> None:
    assert "pull-episodes" in POST_COMMANDS
    assert "pull-episodes" in READ_OR_LOCAL_COMMANDS
