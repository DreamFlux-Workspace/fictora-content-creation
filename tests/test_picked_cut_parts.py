"""``cut`` plans the Instagram and TikTok parts with the server and makes nothing until go ahead.

Founder, 5 Nov 2026: a punchy edit is parts only; every part is printed before anything is made;
the server suggests the count, the first number and each part's texts; the human can change them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from creation.post import picked_cut
from creation.post.picked_cut import LOCAL_FILES_GONE, NO_PLAN_ROUTE, cut_command

API = ("https://drama.example", {"Authorization": "Bearer kit-token"})
CUT = {
    "episodes": [1, 2],
    "compilation_url": "https://media.example/whole.mp4",
    "parts": [
        {"part": 3, "video_url": "https://media.example/p3.mp4"},
        {"part": 4, "video_url": "https://media.example/p4.mp4"},
    ],
}
TOO_LONG = "Part 4's ending is too long to fit on screen. Try 8 words or fewer."


class PlanServer:
    """Plans 2 parts starting at Part 3 (this creator already posted Parts 1 and 2) unless told otherwise."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, Any]] = []
        self.plan_missing = False
        self.too_long_over = 1000

    def plans(self) -> list[Any]:
        return [b for m, p, b in self.seen if p == "/v1/series-cuts/plan"]

    def creates(self) -> list[Any]:
        return [b for m, p, b in self.seen if (m, p) == ("POST", "/v1/series-cuts")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.seen.append((request.method, request.url.path, body))
        if request.url.path == "/v1/series-cuts/plan":
            if self.plan_missing:
                return httpx.Response(404, json={"detail": "Not Found"})
            return self.plan(body)
        if request.method == "POST":
            return httpx.Response(202, json={"series_cut_id": "c1"})
        return httpx.Response(200, json={"status": "ready", "series_cut": CUT})

    def plan(self, body: dict[str, Any]) -> httpx.Response:
        count, start = body.get("part_count", 2), body.get("start_number", 3)
        texts = {t["number"]: t for t in body.get("texts", [])}
        for n, t in texts.items():
            if any(
                len(t.get(k, "")) > self.too_long_over
                for k in ("opening_text", "ending_text")
            ):
                return httpx.Response(
                    422,
                    json={
                        "error": {
                            "code": "part_text_too_long",
                            "message": TOO_LONG,
                            "number": n,
                        }
                    },
                )
        default = {
            3: ("Mira finds the key", "the door is already open"),
            4: ("Someone is inside", "she drops the key"),
            5: ("The lights go out", "he says her name"),
        }
        parts = []
        for n in range(start, start + count):
            opening, ending = default.get(n, (f"opening {n}", f"ending {n}"))
            parts.append(
                {
                    "number": n,
                    "approx_seconds": 52 if n == start else 47.6,
                    "opening_text": texts.get(n, {}).get("opening_text", opening),
                    "ending_text": texts.get(n, {}).get("ending_text", ending),
                }
            )
        return httpx.Response(
            200,
            json={
                "part_count": count,
                "suggested_part_count": 2,
                "start_number": start,
                "suggested_start_number": 3,
                "parts": parts,
                "summary": f"{count} Instagram and TikTok parts, Part {start} to Part {start + count - 1}:",
            },
        )


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> PlanServer:
    fake = PlanServer()
    real = httpx.Client
    monkeypatch.setattr(
        picked_cut,
        "_client",
        lambda t, a: (real(transport=httpx.MockTransport(fake)), API[0], dict(API[1])),
    )
    return fake


def args(**over: Any) -> SimpleNamespace:
    base = dict(
        list=False, episodes=["sp_a:1", "sp_b:1"], show=None, title=None, wait=None, json=False,
        file=[], music_price=False, new_music=False, parts=None, start_number=None, part_text=[], yes=False,
    )  # fmt: skip
    return SimpleNamespace(**{**base, **over})


def answers(monkeypatch: pytest.MonkeyPatch, *typed: str) -> None:
    """Feed ``input`` these answers in order; a question past the end fails the test."""

    feed: Iterator[str] = iter(typed)

    def fake_input() -> str:
        try:
            return next(feed)
        except StopIteration:
            pytest.fail("asked one question too many")

    monkeypatch.setattr("builtins.input", fake_input)


TWO_PART_PRINTOUT = [
    "2 Instagram and TikTok parts, Part 3 to Part 4:",
    'Part 3 · about 0:52 · starts with "Mira finds the key" · ends with "the door is already open"',
    'Part 4 · about 0:48 · starts with "Someone is inside" · ends with "she drops the key"',
    "2 part(s) is the server's suggestion.",
    "Starting at Part 3 is the server's suggestion (it carries on from this creator's earlier parts of the show).",
    "Each opening and ending is the server's suggestion.",
]


def test_every_part_is_printed_before_anything_is_made(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen_at_question: list[str] = []

    def at_question() -> str:
        seen_at_question.append(capsys.readouterr().out)
        assert server.creates() == [], "the cut started before the human answered"
        return "y"

    monkeypatch.setattr("builtins.input", at_question)
    assert cut_command(args()) == 0
    printed = seen_at_question[0].splitlines()
    assert printed[: len(TWO_PART_PRINTOUT)] == TWO_PART_PRINTOUT
    assert printed[-1] == picked_cut.GO_AHEAD
    assert (
        server.creates()[0]["part_count"] == 2
        and server.creates()[0]["start_number"] == 3
    )


@pytest.mark.parametrize("answer", ["n", "", "no", "maybe"])
def test_anything_but_yes_or_edit_makes_nothing(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
) -> None:
    answers(monkeypatch, answer)
    assert cut_command(args()) == 5
    assert server.creates() == [] and len(server.plans()) == 1
    assert "nothing was made" in capsys.readouterr().err


def test_no_keyboard_counts_as_no(
    server: PlanServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def closed() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    assert cut_command(args()) == 5
    assert server.creates() == []


def test_edit_plans_again_prints_it_and_the_cut_makes_the_edited_plan(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(
        monkeypatch,
        "e",
        "3",  # parts
        "1",  # first number
        "2",  # change part 2
        "",  # keep its opening
        "she runs",  # new ending
        "",  # done editing
        "y",
    )
    assert cut_command(args()) == 0
    assert server.plans()[1] == {
        "episodes": [
            {"spine_id": "sp_a", "episode": 1},
            {"spine_id": "sp_b", "episode": 1},
        ],
        "title": None,
        "part_count": 3,
        "start_number": 1,
        "texts": [{"number": 2, "ending_text": "she runs"}],
    }
    assert [
        {k: v for k, v in c.items() if k != "episodes"} for c in server.creates()
    ] == [
        {
            "title": None,
            "part_count": 3,
            "start_number": 1,
            "texts": [{"number": 2, "ending_text": "she runs"}],
        }
    ]
    out = capsys.readouterr().out
    assert 'Part 2 · about 0:48 · starts with "opening 2" · ends with "she runs"' in out
    assert "3 part(s) is your choice; the server suggested 2." in out
    assert "Starting at Part 1 is your choice; the server suggested Part 3." in out
    assert "except the ones you changed: Part 2 ending." in out


def test_flags_pass_through_to_the_plan_and_the_cut_without_asking(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch)
    code = cut_command(
        args(
            parts=3,
            start_number=5,
            part_text=["6:opening=Back again", "6:ending=Gone"],
            yes=True,
        )
    )
    assert code == 0
    wanted = {
        "part_count": 3,
        "start_number": 5,
        "texts": [{"number": 6, "opening_text": "Back again", "ending_text": "Gone"}],
    }
    assert {k: server.plans()[0][k] for k in wanted} == wanted
    assert {k: server.creates()[0][k] for k in wanted} == wanted
    assert (
        'Part 6 · about 0:48 · starts with "Back again" · ends with "Gone"'
        in capsys.readouterr().out
    )


@pytest.mark.parametrize("bad", ["2=Hi", "x:opening=Hi", "2:middle=Hi", "2:opening="])
def test_a_bad_part_text_is_refused_before_any_call(
    server: PlanServer, bad: str
) -> None:
    assert cut_command(args(part_text=[bad], yes=True)) == 2
    assert server.seen == []


def test_too_long_text_is_shown_plainly_and_asked_again(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server.too_long_over = 20
    answers(
        monkeypatch,
        "",  # keep 2 parts
        "",  # keep Part 3 first
        "4",
        "",  # keep opening
        "she drops it",  # shorter ending
        "",
        "y",
    )
    assert (
        cut_command(
            args(part_text=["4:ending=she drops the key and runs into the night"])
        )
        == 0
    )
    out = capsys.readouterr().out
    assert out.splitlines()[0] == TOO_LONG
    assert [c["texts"] for c in server.creates()] == [
        [{"number": 4, "ending_text": "she drops it"}]
    ]


def test_too_long_text_under_yes_stops_with_the_servers_words(
    server: PlanServer, capsys: pytest.CaptureFixture[str]
) -> None:
    server.too_long_over = 20
    assert (
        cut_command(args(part_text=["4:ending=she drops the key and runs"], yes=True))
        == 2
    )
    assert server.creates() == []
    assert capsys.readouterr().err.strip() == f"Stopped: {TOO_LONG} Nothing was made."


def test_only_the_parts_are_offered_never_the_whole_video(
    server: PlanServer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(yes=True)) == 0
    out = capsys.readouterr().out
    assert "Part 3 for Instagram and TikTok: https://media.example/p3.mp4" in out
    assert "Part 4 for Instagram and TikTok: https://media.example/p4.mp4" in out
    assert "whole.mp4" not in out and "Whole" not in out


def test_cut_help_offers_no_whole_video_and_no_local_files(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from creation.cli_produce import main

    with pytest.raises(SystemExit):
        main(["cut", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "whole" not in text.lower() and "--file" not in text and "=FILE" not in text
    assert (
        "--parts" in text
        and "--start-number" in text
        and "--part-text" in text
        and "--yes" in text
    )


@pytest.mark.parametrize(
    "over",
    [
        {"episodes": ["sp_a:1=/tmp/better.mp4", "sp_b:1"]},
        {"episodes": None, "show": "Dead Heat", "file": ["2=/tmp/better.mp4"]},
        {"file": ["1=/tmp/better.mp4"]},
    ],
)
def test_a_laptop_video_file_is_refused_in_plain_words(
    server: PlanServer, capsys: pytest.CaptureFixture[str], over: dict[str, Any]
) -> None:
    assert cut_command(args(yes=True, **over)) == 2
    assert server.seen == []
    assert capsys.readouterr().err.strip() == LOCAL_FILES_GONE


def test_a_laptop_video_file_is_refused_from_the_command_line(
    server: PlanServer, capsys: pytest.CaptureFixture[str]
) -> None:
    from creation.cli_produce import main

    assert (
        main(["cut", "--show", "Dead Heat", "--file", "2=/tmp/better.mp4", "--yes"])
        == 2
    )
    assert server.seen == []
    assert LOCAL_FILES_GONE in capsys.readouterr().err


def test_a_server_that_cannot_plan_makes_nothing(
    server: PlanServer, capsys: pytest.CaptureFixture[str]
) -> None:
    server.plan_missing = True
    assert cut_command(args(yes=True)) == 5
    assert server.creates() == []
    assert capsys.readouterr().err.strip() == NO_PLAN_ROUTE


# --- join / finish --desk D --episodes: the same plan, print and go-ahead as cut ------------------------

DESK_COMMANDS = pytest.mark.parametrize("command", ["join", "finish"])


def desk_run(command: str, desk: Any, *extra: str) -> int:
    from creation.cli_produce import main

    return main([command, "--desk", str(desk), "--episodes", "2", "1", *extra])


@DESK_COMMANDS
def test_desk_episodes_print_every_part_before_anything_is_made(
    command: str,
    post_desk: Any,
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen_at_question: list[str] = []

    def at_question() -> str:
        seen_at_question.append(capsys.readouterr().out)
        assert server.creates() == [], "the cut started before the human answered"
        return "y"

    monkeypatch.setattr("builtins.input", at_question)
    assert desk_run(command, post_desk) == 0
    printed = seen_at_question[0].splitlines()
    assert printed[: len(TWO_PART_PRINTOUT)] == TWO_PART_PRINTOUT
    assert printed[-1] == picked_cut.GO_AHEAD
    made = server.creates()
    assert len(made) == 1
    assert made[0]["episodes"] == [
        {"spine_id": "spine_test", "episode": 2},
        {"spine_id": "spine_test", "episode": 1},
    ]
    assert (made[0]["part_count"], made[0]["start_number"]) == (2, 3)
    assert not any(path.startswith("/v1/spines/") for _m, path, _b in server.seen)
    out = capsys.readouterr().out
    assert "https://media.example/p3.mp4" in out and "whole.mp4" not in out


@DESK_COMMANDS
def test_desk_episodes_no_makes_nothing(
    command: str,
    post_desk: Any,
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch, "n")
    assert desk_run(command, post_desk) == 5
    assert server.creates() == [] and len(server.plans()) == 1
    assert picked_cut.NOT_CONFIRMED in capsys.readouterr().err


@DESK_COMMANDS
def test_desk_episodes_yes_goes_ahead_with_the_settings_given(
    command: str,
    post_desk: Any,
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("a question was asked"))
    extra = ("--yes", "--parts", "3", "--start-number", "7")
    assert desk_run(command, post_desk, *extra, "--part-text", "8:ending=GONE") == 0
    sent = {"part_count": 3, "start_number": 7, "texts": [{"number": 8, "ending_text": "GONE"}]}  # fmt: skip
    assert {k: server.plans()[0][k] for k in sent} == sent
    assert {k: server.creates()[0][k] for k in sent} == sent
    assert 'Part 8 · about 0:48 · starts with "opening 8" · ends with "GONE"' in (
        capsys.readouterr().out
    )


@DESK_COMMANDS
def test_desk_episodes_on_a_server_that_cannot_plan_make_nothing(
    command: str,
    post_desk: Any,
    server: PlanServer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server.plan_missing = True
    assert desk_run(command, post_desk, "--yes") == 5
    assert server.creates() == []
    assert NO_PLAN_ROUTE in capsys.readouterr().err


@DESK_COMMANDS
def test_part_settings_without_episodes_are_refused(
    command: str,
    post_desk: Any,
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from creation import cli_post
    from creation.cli_produce import main

    finished: list[Any] = []
    monkeypatch.setattr(
        cli_post, "run_server_finish", lambda *a, **k: finished.append(a)
    )
    assert main([command, "--desk", str(post_desk), "--yes"]) == 2
    assert server.seen == [] and finished == []
    assert "add --episodes" in capsys.readouterr().err
