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
    BEING_REMADE,
    NEWEST_VERSION_NOTICE,
    CutStop,
    PartChoice,
    Pick,
    cut_command,
    parse_ref,
    plan_parts,
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


#: The kit's question, word for word as the server writes it (founder, 4 Nov 2026: "Kit asks first, we pay").
KIT_QUESTION = "New music for this edit costs $0.08, paid by Sokii. Make it? (yes/no)"

OFFER = {
    "offered": True,
    "price": {"tokens": 16, "usd": 0.08},
    "tracks": 1,
    "episodes_with_music": [1, 2],
    "what_changes": "New music written for this edit, with changes at each episode and at the big turns.",
    "unavailable_reason": None,
    "paid_by": "sokii",
    "question": KIT_QUESTION,
}


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
        self.offer: tuple[int, Any] = (200, OFFER)
        self.seen: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.seen.append((request.method, request.url.path, body))
        if request.url.path == "/v1/series-cuts/pickable":
            return httpx.Response(self.pickable[0], json=self.pickable[1])
        if request.url.path == "/v1/series-cuts/music-offer":
            return httpx.Response(self.offer[0], json=self.offer[1])
        if request.url.path == "/v1/series-cuts/plan":
            return httpx.Response(200, json=plan_answer(body or {}))
        if request.method == "POST":
            return httpx.Response(self.post[0], json=self.post[1])
        poll = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        return httpx.Response(200, json=poll)


def plan_answer(body: dict[str, Any]) -> dict[str, Any]:
    """The plan a server answers: 2 parts from Part 1 unless the body says otherwise."""

    count, start = body.get("part_count", 2), body.get("start_number", 1)
    return {
        "plan_id": "p1",
        "part_count": count,
        "suggested_part_count": 2,
        "start_number": start,
        "suggested_start_number": 1,
        "parts": [
            {"number": n, "approx_seconds": 50, "opening_text": "o", "ending_text": "e"}
            for n in range(start, start + count)
        ],
        "summary": "",
    }


def created(server: Server) -> tuple[str, str, Any]:
    return next(s for s in server.seen if s[:2] == ("POST", "/v1/series-cuts"))


def planned(server: Server) -> tuple[str, str, Any]:
    return next(s for s in server.seen if s[:2] == ("POST", "/v1/series-cuts/plan"))


def transport(server: Server) -> httpx.MockTransport:
    return httpx.MockTransport(server)


P2: list[Pick]


def args(**over: Any) -> SimpleNamespace:
    base = dict(
        list=False,
        episodes=None,
        show=None,
        title=None,
        wait=None,
        json=False,
        file=[],
        music_price=False,
        new_music=False,
        parts=None,
        start_number=None,
        part_text=[],
        yes=True,
    )
    base["yes"] = not over.get(
        "list"
    )  # a made cut has said go ahead; --list takes no --yes
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
    assert parse_ref(f"{desk}:2") == Pick("sp", 2)


@pytest.mark.parametrize(
    "extra",
    [
        {"title": "T"},
        {"wait": 5.0},
        {"music_price": True},
        {"new_music": True},
        {"parts": 3},
        {"start_number": 2},
        {"part_text": ["1:opening=Hi"]},
        {"yes": True},
    ],
)
def test_list_with_make_options_is_a_usage_error(
    extra: dict[str, Any], stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(list=True, **extra)) == 2
    assert stand_in_api.seen == [] and "Usage:" in capsys.readouterr().err


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
        "p1",
        picked=2,
        api=API,
        transport=httpx.MockTransport(flaky),
        sleep=waits.append,
        clock=lambda: 0.0,
    )
    assert calls["n"] == 3 and waits == [2.0, 4.0] and lines[1].startswith("Part 1")


def test_poll_gives_up_with_a_plain_line_at_the_deadline() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(503, text="x")
        return httpx.Response(202, json={"series_cut_id": "c1"})

    now = [0.0]
    with pytest.raises(CutStop, match="busy or restarting"):
        run_cut(
            "p1",
            picked=2,
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
    post = planned(stand_in_api)
    assert post[2] == {
        "episodes": [
            {"spine_id": "sp_a", "episode": 1},
            {"spine_id": "sp_b", "episode": 1},
        ],
        "title": "Exit",
    }
    assert created(stand_in_api)[2] == {"plan_id": "p1"}
    out = capsys.readouterr().out
    assert "all.mp4" not in out
    assert "Part 1: https://media.example/p1.mp4" in out
    assert (
        "Cover: https://media.example/p1.jpg" in out
        and "Post caption: He kept the lock." in out
    )


def test_episodes_are_sent_in_the_order_given(stand_in_api: Server) -> None:
    assert cut_command(args(episodes=["sp_b:1", "sp_a:1"])) == 0
    post = planned(stand_in_api)
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
    lines = run_cut("p1", picked=2, api=API, transport=transport(server),
        sleep=lambda s: now.__setitem__(0, now[0] + 20), clock=lambda: now[0], progress=said.append,
    )  # fmt: skip
    assert (
        sum(1 for s in server.seen if s[0] == "GET" and s[1] == "/v1/series-cuts/c1")
        == 3
    )
    assert any("Still making the cut" in line for line in said)
    assert said[0] == NEWEST_VERSION_NOTICE
    assert lines[1] == "Part 1: https://media.example/p1.mp4"


def test_failed_cut_prints_the_servers_reason_and_retry_by_id() -> None:
    server = Server(polls=[{"status": "failed", "reason": "episode 2 has no sound"}])
    with pytest.raises(CutStop, match="episode 2 has no sound") as stop:
        run_cut("p1", picked=2, api=API, transport=transport(server))
    assert stop.value.line.endswith("To try again, run: fictora-produce cut-retry c1")


def test_a_cut_being_made_again_says_so_once_on_the_cut_path() -> None:
    remade = {"status": "making", "series_cut": CUT}
    server = Server(polls=[remade, remade, {"status": "ready", "series_cut": CUT}])
    said: list[str] = []
    run_cut(
        "p1",
        picked=2,
        api=API,
        transport=transport(server),
        sleep=lambda s: None,
        progress=said.append,
    )
    assert said.count(BEING_REMADE) == 1


def test_ready_cut_prints_its_id_for_rename_and_save() -> None:
    lines = run_cut("p1", picked=2, api=API, transport=transport(Server()))
    assert lines[-1].startswith("Edit id: c1.")
    assert "cut-rename c1" in lines[-1] and "cut-save c1 --part N" in lines[-1]


def test_failed_music_says_to_retry_the_same_edit_by_id() -> None:
    music = {"state": "failed", "message": "the music could not be made."}
    server = Server(polls=[{"status": "ready", "series_cut": CUT, "music": music}])
    lines = run_cut("p1", picked=2, api=API, transport=transport(server))
    assert "To try again, run: fictora-produce cut-retry c1" in lines


def test_waiting_too_long_says_to_retry_the_same_edit_by_id() -> None:
    server = Server(polls=[{"status": "making"}])
    now = [0.0]
    with pytest.raises(CutStop, match="fictora-produce cut-retry c1"):
        run_cut(
            "p1",
            picked=2,
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
        plan_parts(refs, PartChoice(), api=API, transport=transport(stand_in_api))
    assert stop.value.code == 2 and stand_in_api.seen == []


P2 = [Pick("a", 1), Pick("b", 1)]


def test_cut_is_a_registered_command() -> None:
    from creation.cli_produce import main

    with pytest.raises(SystemExit) as done:
        main(["cut", "--help"])
    assert done.value.code == 0


def test_more_than_twenty_episodes_are_fine(stand_in_api: Server) -> None:
    plan_parts(
        [Pick("a", i) for i in range(1, 31)],
        PartChoice(),
        api=API,
        transport=transport(stand_in_api),
    )
    post = planned(stand_in_api)
    assert len(post[2]["episodes"]) == 30


def test_no_files_named_asks_nothing_and_sends_no_video_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "builtins.input", lambda *a: pytest.fail("a question was asked")
    )
    monkeypatch.setattr("sys.stdin", None)
    server = Server()

    plan = plan_parts(
        [Pick("sp_a", 1), Pick("sp_b", 1)],
        PartChoice(),
        api=API,
        transport=transport(server),
    )

    assert plan["parts"]
    post = planned(server)
    assert all("video_url" not in e for e in post[2]["episodes"])
    assert not any(s[1].endswith("/uploads") or "upload" in s[1] for s in server.seen)


def test_no_files_named_tells_the_creator_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "builtins.input", lambda *a: pytest.fail("a question was asked")
    )
    said: list[str] = []

    run_cut(
        "p1",
        picked=2,
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


def test_music_price_shows_the_servers_price_and_makes_nothing(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(episodes=["sp_a:1", "sp_b:1"], music_price=True)) == 0
    assert [s[:2] for s in stand_in_api.seen] == [
        ("POST", "/v1/series-cuts/music-offer")
    ]
    assert stand_in_api.seen[0][2] == {
        "episodes": [
            {"spine_id": "sp_a", "episode": 1},
            {"spine_id": "sp_b", "episode": 1},
        ],
        "title": None,
    }
    out = capsys.readouterr().out
    assert (
        out.splitlines()[0] == KIT_QUESTION
    )  # dollars, paid by Sokii, a yes/no question
    assert (
        "tokens" not in out and "second" not in out and "minute" not in out
    )  # never a length
    assert "picked episode(s) 1, 2" in out
    assert "only if they say yes, run the same cut with --new-music." in out


#: What the server says when none of the picks has a copy without music.
NO_SOURCE = (
    "None of these episodes has a copy without music, so new music cannot go under them. "
    "The edit keeps each episode's own sound."
)


@pytest.mark.parametrize(
    "reason", ["New music is not available for these shows yet.", NO_SOURCE]
)
def test_music_not_offered_says_why_in_plain_words(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str], reason: str
) -> None:
    stand_in_api.offer = (
        200,
        {
            "offered": False,
            "price": None,
            "tracks": 0,
            "episodes_with_music": [],
            "what_changes": "",
            "unavailable_reason": reason,
        },
    )
    assert cut_command(args(episodes=["sp_a:1", "sp_b:1"], music_price=True)) == 0
    out = capsys.readouterr().out
    assert f"No new music for this edit: {reason}" in out
    assert "costs" not in out


def test_default_cut_never_asks_for_new_music(stand_in_api: Server) -> None:
    assert cut_command(args(episodes=["sp_a:1", "sp_b:1"])) == 0
    post = planned(stand_in_api)
    assert "new_music" not in post[2]
    assert not any(s[1].endswith("/music-offer") for s in stand_in_api.seen)


def test_new_music_passes_the_yes_and_prints_the_music_state(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in_api.polls = [
        {
            "status": "ready",
            "series_cut": CUT,
            "music": {
                "state": "laid",
                "message": "New music is laid under the edit.",
            },
        }
    ]
    assert cut_command(args(episodes=["sp_a:1", "sp_b:1"], new_music=True)) == 0
    post = planned(stand_in_api)
    assert (
        post[2]["new_music"] is True and "music_quote" not in post[2]
    )  # Sokii pays: no price goes back
    assert created(stand_in_api)[2] == {"plan_id": "p1"}
    assert "New music: New music is laid under the edit." in capsys.readouterr().out


def test_failed_music_still_prints_the_edit_with_the_servers_words(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in_api.polls = [
        {
            "status": "ready",
            "series_cut": CUT,
            "music": {
                "state": "failed",
                "message": "The new music could not be made, so your edit plays each episode's own sound.",
            },
        }
    ]
    assert cut_command(args(episodes=["sp_a:1", "sp_b:1"], new_music=True)) == 0
    out = capsys.readouterr().out
    assert "New music: The new music could not be made" in out
    assert "Part 1: https://media.example/p1.mp4" in out


def test_music_not_offered_409_stops_plainly(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in_api.post = (
        409,
        {
            "code": "series_cut_music_unavailable",
            "message": "New music is not available for these shows yet.",
        },
    )
    assert cut_command(args(episodes=["sp_a:1", "sp_b:1"], new_music=True)) == 5
    err = capsys.readouterr().err
    assert "new music is not offered for this edit" in err
    assert "without --new-music" in err


def test_music_price_and_new_music_together_is_refused(
    stand_in_api: Server,
) -> None:
    assert (
        cut_command(
            args(episodes=["sp_a:1", "sp_b:1"], music_price=True, new_music=True)
        )
        == 2
    )
    assert stand_in_api.seen == []


def test_older_server_without_music_price_says_so(
    stand_in_api: Server, capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in_api.offer = (404, {"detail": "Not Found"})
    assert cut_command(args(episodes=["sp_a:1", "sp_b:1"], music_price=True)) == 5
    assert "can't price new music for an edit yet" in capsys.readouterr().err
