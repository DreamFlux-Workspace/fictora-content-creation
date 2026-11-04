"""``fictora-produce cut``: the kit sends the pick and prints the server's cut (stand-in server only)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from creation.post import picked_cut
from creation.post.picked_cut import (
    NEWEST_VERSION_NOTICE,
    CutStop,
    Pick,
    cut_command,
    parse_ref,
    run_cut,
    select_show,
)

API = ("https://drama.example", {"Authorization": "Bearer kit-token"})
CUT = {
    "episodes": [1, 2],
    "compilation_url": "https://media.example/all.mp4",
    "parts": [
        {
            "part": 1,
            "video_url": "https://media.example/p1.mp4",
            "cover_url": "https://media.example/p1.jpg",
            "caption": "He kept the lock.",
        }
    ],
}
EPISODES = [
    {
        "spine_id": "sp_b",
        "show_title": "EXIT STRATEGY",
        "episode": 1,
        "episode_title": "Two",
        "seconds": 20,
        "delivered_at": "2026-11-02T10:00:00Z",
    },
    {
        "spine_id": "sp_a",
        "show_title": "Exit Strategy",
        "episode": 1,
        "episode_title": "One",
        "seconds": 18.4,
        "delivered_at": "2026-11-01T10:00:00Z",
    },
    {
        "spine_id": "sp_c",
        "show_title": "Other",
        "episode": 3,
        "episode_title": None,
        "seconds": 30,
        "delivered_at": "2026-10-01T10:00:00Z",
    },
]


class Server:
    def __init__(
        self,
        post: tuple[int, Any] = (202, {"series_cut_id": "c1", "status": "queued"}),
        polls: list[Any] | None = None,
        pickable: tuple[int, Any] | None = None,
    ) -> None:
        self.post = post
        self.polls = list(polls or [{"status": "ready", "series_cut": CUT}])
        self.pickable = pickable or (200, {"episodes": EPISODES})
        self.seen: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.seen.append((request.method, request.url.path, body))
        if request.url.path == "/v1/series-cuts/pickable":
            return httpx.Response(self.pickable[0], json=self.pickable[1])
        if request.method == "POST":
            return httpx.Response(self.post[0], json=self.post[1])
        poll = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        return httpx.Response(200, json=poll)


def transport(server: Server) -> httpx.MockTransport:
    return httpx.MockTransport(server)


P2: list[Pick]


def args(**over: Any) -> SimpleNamespace:
    base = dict(
        list=False, episodes=None, show=None, title=None, wait=None, json=False, file=[]
    )
    return SimpleNamespace(**{**base, **over})


@pytest.fixture(autouse=True)
def stand_in_api(monkeypatch: pytest.MonkeyPatch) -> Server:
    server = Server()
    real = httpx.Client
    monkeypatch.setattr(
        picked_cut,
        "_client",
        lambda t, a: (real(transport=t or transport(server)), API[0], dict(API[1])),
    )
    server.client_patched = True  # type: ignore[attr-defined]
    return server


def test_ref_with_a_show_id() -> None:
    assert parse_ref("spine_9:3") == Pick("spine_9", 3)


def test_ref_with_a_desk_reads_its_spine_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("creation.post.desk.spine_id", lambda desk: "spine_from_desk")
    assert parse_ref(f"{tmp_path}:2") == Pick("spine_from_desk", 2)


@pytest.mark.parametrize("raw", ["spine_9", "spine_9:x", ":2", "spine_9:0"])
def test_bad_refs_stop_with_exit_2(raw: str) -> None:
    with pytest.raises(CutStop) as stop:
        parse_ref(raw)
    assert stop.value.code == 2


def test_a_missing_desk_folder_is_named(tmp_path: Path) -> None:
    with pytest.raises(CutStop, match="no desk folder"):
        parse_ref(f"{tmp_path}/nope:1")


def test_show_matches_every_show_with_the_title_oldest_first() -> None:
    picked = select_show(EPISODES, "exit strategy")
    assert [e["spine_id"] for e in picked] == ["sp_a", "sp_b"]


def test_show_orders_by_each_shows_earliest_delivery_then_episode() -> None:
    eps = [
        {
            "spine_id": "a",
            "show_title": "S",
            "episode": 1,
            "delivered_at": "2026-11-09T00:00:00Z",
        },
        {
            "spine_id": "a",
            "show_title": "S",
            "episode": 2,
            "delivered_at": "2026-11-02T00:00:00Z",
        },
        {
            "spine_id": "b",
            "show_title": "S",
            "episode": 1,
            "delivered_at": "2026-11-05T00:00:00Z",
        },
    ]
    assert [(e["spine_id"], e["episode"]) for e in select_show(eps, "s")] == [
        ("a", 1),
        ("a", 2),
        ("b", 1),
    ]


@pytest.mark.parametrize("raw", ["spine_9:\u00b2", "spine_9:\u00b2=x.mp4"])
def test_superscript_digit_is_a_plain_refusal(raw: str) -> None:
    with pytest.raises(CutStop) as stop:
        parse_ref(raw)
    assert stop.value.code == 2


def test_equals_in_a_desk_path_still_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("creation.post.desk.spine_id", lambda desk: "sp")
    desk = tmp_path / "a=b"
    desk.mkdir()
    f = video(tmp_path, "x=y.mp4")
    assert parse_ref(f"{desk}:2") == Pick("sp", 2)
    assert parse_ref(f"{desk}:2={f}") == Pick("sp", 2, f.resolve())


@pytest.mark.parametrize(
    "extra", [{"file": ["1=x.mp4"]}, {"title": "T"}, {"wait": 5.0}]
)
def test_list_with_make_options_is_a_usage_error(
    extra: dict[str, Any], stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(list=True, **extra)) == 2
    assert stand_in_api.seen == [] and "Usage:" in capsys.readouterr().err


def test_older_server_refuses_before_any_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = with_uploads(monkeypatch)
    server.pickable = (404, {"detail": "Not Found"})
    f = video(tmp_path)
    assert cut_command(args(episodes=[f"a:1={f}", "b:1"])) == 5
    assert [s[1] for s in server.seen] == ["/v1/series-cuts/pickable"]
    assert "can't make a cut from picked episodes yet" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["clip.mov", "clip.webm", "clip.mkv"])
def test_only_mp4_and_m4v_go_up(tmp_path: Path, name: str) -> None:
    with pytest.raises(CutStop, match=r"not an \.mp4 or \.m4v") as stop:
        parse_ref(f"a:1={video(tmp_path, name)}")
    assert stop.value.code == 2
    assert parse_ref(f"a:1={video(tmp_path, 'c.M4V')}").file is not None


def test_poll_retries_busy_and_dropped_connections_until_ready() -> None:
    server = Server()
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/series-cuts/c1":
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(502, text="bad gateway")
            if calls["n"] == 2:
                raise httpx.ConnectError("dropped")
        return server(request)

    waits: list[float] = []
    lines = run_cut(
        P2,
        api=API,
        transport=httpx.MockTransport(flaky),
        sleep=waits.append,
        clock=lambda: 0.0,
    )
    assert calls["n"] == 3 and waits == [2.0, 4.0] and lines[1].startswith("Whole show")


def test_poll_gives_up_with_a_plain_line_at_the_deadline() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(503, text="x")
        return httpx.Response(202, json={"series_cut_id": "c1"})

    now = [0.0]
    with pytest.raises(CutStop, match="busy or restarting"):
        run_cut(
            P2,
            wait_seconds=30,
            api=API,
            transport=httpx.MockTransport(down),
            sleep=lambda s: now.__setitem__(0, now[0] + 20),
            clock=lambda: now[0],
        )


def test_list_prints_handles_grouped_by_show(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(list=True, show="exit strategy")) == 0
    out = capsys.readouterr().out
    assert "pick: sp_a:1" in out and "pick: sp_b:1" in out and "sp_c" not in out
    assert stand_in_api.seen == [("GET", "/v1/series-cuts/pickable", None)]


def test_show_alone_sends_every_episode_in_order_and_prints_the_cut(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(show="Exit Strategy", title="Exit")) == 0
    post = next(s for s in stand_in_api.seen if s[0] == "POST")
    assert post[2] == {
        "episodes": [
            {"spine_id": "sp_a", "episode": 1},
            {"spine_id": "sp_b", "episode": 1},
        ],
        "title": "Exit",
    }
    out = capsys.readouterr().out
    assert "Whole show: https://media.example/all.mp4" in out
    assert "Part 1 for Instagram and TikTok: https://media.example/p1.mp4" in out
    assert (
        "Cover: https://media.example/p1.jpg" in out
        and "Post caption: He kept the lock." in out
    )


def test_episodes_are_sent_in_the_order_given(stand_in_api: Server) -> None:
    assert cut_command(args(episodes=["sp_b:1", "sp_a:1"])) == 0
    post = next(s for s in stand_in_api.seen if s[0] == "POST")
    assert [e["spine_id"] for e in post[2]["episodes"]] == ["sp_b", "sp_a"]


def test_a_show_with_no_match_makes_nothing(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(show="Nope")) == 5
    assert not any(s[0] == "POST" for s in stand_in_api.seen)
    assert "Nothing was made" in capsys.readouterr().err


def test_older_server_refuses_clearly_on_list_and_on_post(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in_api.pickable = (404, {"detail": "Not Found"})
    assert cut_command(args(list=True)) == 5
    stand_in_api.post = (404, {"detail": "Not Found"})
    assert cut_command(args(episodes=["a:1", "b:1"])) == 5
    err = capsys.readouterr().err.splitlines()
    expected = "NOT DONE: the server can't make a cut from picked episodes yet. Tell the core team; nothing was made."
    assert err == [expected, expected]


def test_not_delivered_409_names_the_episode(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in_api.post = (
        409,
        {
            "code": "episode_not_delivered",
            "spine_id": "sp_b",
            "episode": 4,
            "message": "Episode 4 has no finished video.",
        },
    )
    assert cut_command(args(episodes=["sp_a:1", "sp_b:4"])) == 5
    err = capsys.readouterr().err
    assert (
        "episode 4 of sp_b is not delivered yet" in err
        and "Episode 4 has no finished video." in err
    )


def test_unknown_show_404_is_not_taken_for_an_old_server(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in_api.post = (404, {"detail": "spine not found"})
    assert cut_command(args(episodes=["x:1", "y:1"])) == 5
    assert "does not know one of the shows" in capsys.readouterr().err


def test_polls_until_ready_and_says_it_is_working() -> None:
    server = Server(
        polls=[
            {"status": "queued"},
            {"status": "making"},
            {"status": "ready", "series_cut": CUT},
        ]
    )
    now = [0.0]
    said: list[str] = []
    lines = run_cut(
        P2, api=API, transport=transport(server),
        sleep=lambda s: now.__setitem__(0, now[0] + 20), clock=lambda: now[0], progress=said.append,
    )  # fmt: skip
    assert (
        sum(1 for s in server.seen if s[0] == "GET" and s[1] == "/v1/series-cuts/c1")
        == 3
    )
    assert any("Still making the cut" in line for line in said)
    assert said[0] == NEWEST_VERSION_NOTICE
    assert lines[1] == "Whole show: https://media.example/all.mp4"


def test_failed_cut_prints_the_servers_reason() -> None:
    server = Server(polls=[{"status": "failed", "reason": "episode 2 has no sound"}])
    with pytest.raises(CutStop, match="episode 2 has no sound"):
        run_cut(P2, api=API, transport=transport(server))


def test_waiting_too_long_says_to_run_it_again() -> None:
    server = Server(polls=[{"status": "making"}])
    now = [0.0]
    with pytest.raises(
        CutStop, match="run the same command again|Run the same command again"
    ):
        run_cut(
            P2,
            wait_seconds=30,
            api=API,
            transport=transport(server),
            sleep=lambda s: now.__setitem__(0, now[0] + 20),
            clock=lambda: now[0],
        )


@pytest.mark.parametrize("refs", [[Pick("a", 1)], [Pick("a", 1), Pick("a", 1)]])
def test_pick_size_and_duplicates_are_refused_before_sending(
    refs: list[Pick], stand_in_api: Server
) -> None:
    with pytest.raises(CutStop) as stop:
        run_cut(refs, api=API, transport=transport(stand_in_api))
    assert stop.value.code == 2 and stand_in_api.seen == []


P2 = [Pick("a", 1), Pick("b", 1)]


def test_cut_is_a_registered_command() -> None:
    from creation.cli_produce import main

    with pytest.raises(SystemExit) as done:
        main(["cut", "--help"])
    assert done.value.code == 0


def video(tmp_path: Path, name: str = "ep1.mp4", data: bytes = b"video-bytes") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


class Uploads(Server):
    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/uploads":
            self.seen.append(("POST", "/v1/uploads", None))
            n = sum(1 for s in self.seen if s[1] == "/v1/uploads")
            return httpx.Response(
                200,
                json={
                    "file_url": f"https://media.example/up{n}.mp4",
                    "upload_url": f"https://put.example/{n}",
                    "headers": {},
                },
            )
        return super().__call__(request)


def with_uploads(monkeypatch: pytest.MonkeyPatch) -> Uploads:
    server = Uploads()
    real = httpx.Client
    monkeypatch.setattr(
        picked_cut,
        "_client",
        lambda t, a: (real(transport=t or transport(server)), API[0], dict(API[1])),
    )
    return server


def test_ref_carries_a_file(tmp_path: Path) -> None:
    f = video(tmp_path)
    assert parse_ref(f"spine_9:2={f}") == Pick("spine_9", 2, f.resolve())


def test_missing_or_non_video_file_is_refused_before_any_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = with_uploads(monkeypatch)
    with pytest.raises(CutStop, match="no file at"):
        parse_ref(f"a:1={tmp_path}/nope.mp4")
    note = tmp_path / "notes.txt"
    note.write_text("x")
    with pytest.raises(CutStop, match="not an .mp4 or .m4v"):
        parse_ref(f"a:1={note}")
    assert server.seen == []


def test_chosen_file_is_uploaded_once_and_sent_as_video_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = with_uploads(monkeypatch)
    one, same = video(tmp_path), video(tmp_path, "copy.mp4")
    puts: list[str] = []
    lines = run_cut(
        [Pick("a", 1, one.resolve()), Pick("b", 1, same.resolve()), Pick("c", 1, one.resolve()), Pick("d", 1)],
        api=API, transport=transport(server), putter=lambda url, data, h: puts.append(url) or 200,
    )  # fmt: skip
    assert len(puts) == 1 and sum(1 for s in server.seen if s[1] == "/v1/uploads") == 1
    post = next(s for s in server.seen if s[1] == "/v1/series-cuts" and s[0] == "POST")
    assert post[2]["episodes"] == [
        {"spine_id": "a", "episode": 1, "video_url": "https://media.example/up1.mp4"},
        {"spine_id": "b", "episode": 1, "video_url": "https://media.example/up1.mp4"},
        {"spine_id": "c", "episode": 1, "video_url": "https://media.example/up1.mp4"},
        {"spine_id": "d", "episode": 1},
    ]
    assert "Whole show" in lines[1]


def test_show_with_file_swaps_the_nth_episode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = with_uploads(monkeypatch)
    f = video(tmp_path)
    monkeypatch.setattr(
        picked_cut, "run_cut", lambda picks, **kw: [str(p.file) for p in picks]
    )
    assert cut_command(args(show="Exit Strategy", file=[f"2={f}"])) == 0
    assert server.seen[0][1] == "/v1/series-cuts/pickable"


def test_file_number_out_of_range_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CutStop, match="from 1 to 2"):
        picked_cut.apply_file_swaps(P2, [f"3={video(tmp_path)}"])


def test_more_than_twenty_episodes_are_fine(stand_in_api: Server) -> None:
    run_cut(
        [Pick("a", i) for i in range(1, 31)], api=API, transport=transport(stand_in_api)
    )
    post = next(s for s in stand_in_api.seen if s[0] == "POST")
    assert len(post[2]["episodes"]) == 30


def test_video_url_not_ours_prints_the_servers_message(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in_api.post = (
        422,
        {
            "code": "video_url_not_ours",
            "message": "That file did not come from our storage.",
        },
    )
    assert cut_command(args(episodes=["a:1", "b:1"])) == 5
    assert "did not come from our storage" in capsys.readouterr().err


def test_no_files_named_asks_nothing_and_sends_no_video_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "builtins.input", lambda *a: pytest.fail("a question was asked")
    )
    monkeypatch.setattr("sys.stdin", None)
    server = Server()

    lines = run_cut(
        [Pick("sp_a", 1), Pick("sp_b", 1)],
        api=API,
        transport=transport(server),
        sleep=lambda s: None,
    )

    assert lines
    post = next(s for s in server.seen if s[0] == "POST")
    assert all("video_url" not in e for e in post[2]["episodes"])
    assert not any(s[1].endswith("/uploads") or "upload" in s[1] for s in server.seen)


def test_no_files_named_tells_the_creator_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "builtins.input", lambda *a: pytest.fail("a question was asked")
    )
    said: list[str] = []

    run_cut(
        [Pick("sp_a", 1), Pick("sp_b", 1)],
        api=API,
        transport=transport(Server()),
        sleep=lambda s: None,
        progress=said.append,
    )

    assert said == [NEWEST_VERSION_NOTICE]
    assert NEWEST_VERSION_NOTICE.startswith(
        "Heads up: I'll make this edit from the episodes saved on our server."
    )
    assert (
        "30 Oct" not in NEWEST_VERSION_NOTICE
        and "newest version" not in NEWEST_VERSION_NOTICE
    )


def test_one_unnamed_episode_is_enough_to_tell_the_creator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = with_uploads(monkeypatch)
    said: list[str] = []
    picks = [Pick("sp_a", 1, video(tmp_path)), Pick("sp_b", 1)]

    run_cut(
        picks,
        api=API,
        transport=transport(server),
        sleep=lambda s: None,
        progress=said.append,
        putter=lambda url, data, h: 200,
    )

    assert said == [NEWEST_VERSION_NOTICE]


def test_all_files_named_prints_no_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = with_uploads(monkeypatch)
    said: list[str] = []
    picks = [
        Pick("sp_a", 1, video(tmp_path, "a.mp4")),
        Pick("sp_b", 1, video(tmp_path, "b.mp4", b"other")),
    ]

    run_cut(
        picks,
        api=API,
        transport=transport(server),
        sleep=lambda s: None,
        progress=said.append,
        putter=lambda url, data, h: 200,
    )

    assert said == []
