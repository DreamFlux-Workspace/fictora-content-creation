"""``cut`` plans the parts with the server, asks "Make it?", and makes nothing until yes.

Founder, 5 Nov 2026: a punchy edit is parts only; every part is printed before anything is made.
The kit prints the server's own words and asks one question; anything else the human types goes
back to the server as the change they want, against the plan the server kept, and the new plan is
asked again. Their yes sends the plan's id alone: the kit never sends a plan's fields back.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from creation.post import picked_cut
from creation.post.picked_cut import (
    LOCAL_FILES_GONE,
    NO_PLAN_ROUTE,
    cut_command,
)

API = ("https://drama.example", {"Authorization": "Bearer kit-token"})
CUT = {
    "episodes": [1, 2],
    "compilation_url": "https://media.example/whole.mp4",
    "parts": [
        {"part": 3, "video_url": "https://media.example/p3.mp4"},
        {"part": 4, "video_url": "https://media.example/p4.mp4"},
    ],
}
THREE_PARTS = "make it three parts"
UNCLEAR = "different"
END_FIVE = "end part 5 with she runs"
TOO_MANY = "9 parts please"
MAX_PARTS = 4
OUT_OF_RANGE = "This edit can have 1 to 4 parts. I made it 4."
SHOWS = {"sp_a": "Dead Heat", "sp_b": "Low Tide"}
CHANGE_PATH = re.compile(r"^/v1/series-cuts/plan/([^/]+)/change$")


TAKEN = "<the server's own reason>"


class PlanServer:
    """Plans 2 parts starting at Part 3 (this creator already posted Parts 1 and 2) unless told otherwise.

    Every plan it answers is kept under ``plan_1``, ``plan_2``, ... with the count, numbers and texts
    it showed; a change is applied over the kept plan, and make takes a kept plan's id alone.
    """

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, Any]] = []
        self.plan_missing = False
        self.plan_missing_status = 404
        self.kept: dict[str, dict[str, Any]] = {}

    def plans(self) -> list[Any]:
        return [b for _m, p, b in self.seen if p == "/v1/series-cuts/plan"]

    def changes(self) -> list[tuple[str, Any]]:
        return [
            (m.group(1), b) for _v, p, b in self.seen if (m := CHANGE_PATH.match(p))
        ]

    def creates(self) -> list[Any]:
        return [b for m, p, b in self.seen if (m, p) == ("POST", "/v1/series-cuts")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.seen.append((request.method, request.url.path, body))
        if request.url.path == "/v1/series-cuts/plan":
            if self.plan_missing:
                detail = (
                    "Not Found"
                    if self.plan_missing_status == 404
                    else "Method Not Allowed"
                )
                return httpx.Response(self.plan_missing_status, json={"detail": detail})
            return self.plan(body)
        if change := CHANGE_PATH.match(request.url.path):
            kept = self.kept.get(change.group(1))
            if kept is None:
                return httpx.Response(
                    404, json={"code": "series_cut_plan_not_found", "message": TAKEN}
                )
            return self.plan({**kept, "change_request": body["change_request"]})
        if request.method == "POST":
            if (body or {}).get("plan_id") not in self.kept:
                return httpx.Response(
                    404, json={"code": "series_cut_plan_not_found", "message": TAKEN}
                )
            return httpx.Response(202, json={"series_cut_id": "c1"})
        return httpx.Response(200, json={"status": "ready", "series_cut": CUT})

    def plan(self, asked: dict[str, Any]) -> httpx.Response:
        change = asked.get("change_request")
        count = asked.get("part_count", 2)
        clarify = None
        if change == THREE_PARTS:
            count = 3
        if change == TOO_MANY:
            count, clarify = MAX_PARTS, OUT_OF_RANGE
        if change == UNCLEAR:
            clarify = "Different how: more parts, fewer, or other openings?"
        start = asked.get("start_number", 3)
        texts = {t["number"]: dict(t) for t in asked.get("texts", [])}
        if change == END_FIVE:
            texts.setdefault(5, {"number": 5})["ending_text"] = "she runs"
        default = {
            3: ("Mira finds the key", "the door is already open"),
            4: ("Someone is inside", "she drops the key"),
            5: ("The lights go out", "he says her name"),
        }
        parts = []
        for n in range(start, start + count):
            opening, ending = default.get(n, (f"opening {n}", f"ending {n}"))
            spine = "sp_a" if n == start else "sp_b"
            parts.append(
                {
                    "number": n,
                    "approx_seconds": 52 if n == start else 47.6,
                    "start_s": 0.0 if n == start else 51.3,
                    "end_s": 51.3 if n == start else 98.9,
                    "cover_url": f"https://media.example/cover{n}.jpg",
                    "episodes": [
                        {"spine_id": spine, "episode": 1, "show_title": SHOWS[spine]}
                    ],
                    "opening_text": texts.get(n, {}).get("opening_text", opening),
                    "ending_text": texts.get(n, {}).get("ending_text", ending),
                }
            )
        summary = [
            f"I'll split this into {count} parts:",
            *(
                f'Part {p["number"]}: starts "{p["opening_text"]}", ends "{p["ending_text"]}"'
                for p in parts
            ),
            "Make it, or tell me what to change?",
        ]
        plan_id = f"plan_{len(self.kept) + 1}"
        self.kept[plan_id] = {
            "episodes": asked.get("episodes"),
            "title": asked.get("title"),
            "part_count": count,
            "start_number": start,
            "texts": [
                {
                    "number": p["number"],
                    "opening_text": p["opening_text"],
                    "ending_text": p["ending_text"],
                }
                for p in parts
            ],
        }
        plan: dict[str, Any] = {
            "plan_id": plan_id,
            "part_count": count,
            "suggested_part_count": 2,
            "max_part_count": MAX_PARTS,
            "start_number": start,
            "suggested_start_number": 3,
            "parts": parts,
            "summary": "\n".join(summary),
            "clarify": clarify,
        }
        return httpx.Response(200, json=plan)


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
        file=[], music_price=False, new_music=False, parts=None, start_number=None, part_text=[],
        change=None, yes=False, plan=None,
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


def screen(out: str) -> list[str]:
    """The printed lines up to the last question, as the human sees them: their answer ends its line."""

    lines = out.replace(picked_cut.GO_AHEAD, picked_cut.GO_AHEAD + "\n").splitlines()
    return lines[: len(lines) - lines[::-1].index(picked_cut.GO_AHEAD)]


def two_parts(plan_id: str) -> list[str]:
    return [
        f"Plan id: {plan_id}",
        "I'll split this into 2 parts:",
        'Part 3: starts "Mira finds the key", ends "the door is already open"',
        'Part 4: starts "Someone is inside", ends "she drops the key"',
        "Part 3 plays Dead Heat ep 1.",
        "Part 4 plays Low Tide ep 1.",
        "Make it, or tell me what to change?",
        picked_cut.GO_AHEAD,
    ]


def three_parts(plan_id: str) -> list[str]:
    return [
        f"Plan id: {plan_id}",
        "I'll split this into 3 parts:",
        'Part 3: starts "Mira finds the key", ends "the door is already open"',
        'Part 4: starts "Someone is inside", ends "she drops the key"',
        'Part 5: starts "The lights go out", ends "he says her name"',
        "Part 3 plays Dead Heat ep 1.",
        "Part 4 plays Low Tide ep 1.",
        "Part 5 plays Low Tide ep 1.",
        "Make it, or tell me what to change?",
        picked_cut.GO_AHEAD,
    ]


EPISODES = [
    {"spine_id": "sp_a", "episode": 1},
    {"spine_id": "sp_b", "episode": 1},
]


def test_the_servers_summary_and_every_part_are_printed_then_one_question(
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
    assert seen_at_question[0].splitlines() == two_parts("plan_1")
    assert seen_at_question[0].count("Make it") == 1, "asked twice"


def test_typed_words_go_to_the_server_against_the_plan_shown_and_the_new_plan_is_asked_again(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch, THREE_PARTS, "n")
    assert cut_command(args()) == 5
    assert server.plans() == [{"episodes": EPISODES, "title": None}]
    assert server.changes() == [("plan_1", {"change_request": THREE_PARTS})]
    assert server.creates() == []
    assert screen(capsys.readouterr().out) == two_parts("plan_1") + three_parts(
        "plan_2"
    )


def test_a_second_change_keeps_the_first_and_yes_makes_the_last_plan_by_its_id_alone(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch, THREE_PARTS, END_FIVE, "y")
    assert cut_command(args()) == 0
    assert server.changes() == [
        ("plan_1", {"change_request": THREE_PARTS}),
        ("plan_2", {"change_request": END_FIVE}),
    ]
    assert server.creates() == [{"plan_id": "plan_3"}]
    printed = screen(capsys.readouterr().out)
    assert printed[len(two_parts("plan_1")) + len(three_parts("plan_2")) :] == [
        *three_parts("plan_3")[:4],
        'Part 5: starts "The lights go out", ends "she runs"',
        *three_parts("plan_3")[5:],
    ]


def test_the_make_request_carries_the_plan_id_and_nothing_of_the_plan(
    server: PlanServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers(monkeypatch, THREE_PARTS, "y")
    assert cut_command(args()) == 0
    (made,) = server.creates()
    assert made == {"plan_id": "plan_2"} and set(made) == {"plan_id"}


def test_the_servers_question_back_or_what_it_did_instead_is_printed_above_the_plan(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch, UNCLEAR, TOO_MANY, "")
    assert cut_command(args()) == 5
    printed = screen(capsys.readouterr().out)
    after_first = printed[len(two_parts("plan_1")) :]
    assert after_first[: len(two_parts("plan_2")) + 1] == [
        "Different how: more parts, fewer, or other openings?",
        *two_parts("plan_2"),
    ]
    assert after_first[len(two_parts("plan_2")) + 1 :][:3] == [
        OUT_OF_RANGE,
        "Plan id: plan_3",
        "I'll split this into 4 parts:",
    ]
    assert server.creates() == []


@pytest.mark.parametrize("answer", ["n", "N", "", "no", "  "])
def test_no_or_nothing_typed_makes_nothing_and_says_how_to_pick_the_plan_up(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
) -> None:
    answers(monkeypatch, answer)
    assert cut_command(args()) == 5
    assert server.creates() == [] and len(server.plans()) == 1
    err = capsys.readouterr().err
    assert picked_cut.NOT_CONFIRMED in err
    assert (
        "cut --plan plan_1 --yes" in err and 'cut --plan plan_1 --change "WORDS"' in err
    )


def test_no_keyboard_counts_as_no(
    server: PlanServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def closed() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    assert cut_command(args()) == 5
    assert server.creates() == []


def test_change_flag_goes_to_the_first_plan_and_yes_makes_that_plan_without_asking(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch)
    assert cut_command(args(change=THREE_PARTS, yes=True)) == 0
    assert server.plans() == [
        {"episodes": EPISODES, "title": None, "change_request": THREE_PARTS}
    ]
    assert server.changes() == [] and server.creates() == [{"plan_id": "plan_1"}]
    assert three_parts("plan_1")[1] in capsys.readouterr().out


def test_first_settings_go_to_the_first_plan_only_and_make_sends_the_id(
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
    assert server.creates() == [{"plan_id": "plan_1"}]
    assert 'Part 6: starts "Back again", ends "Gone"' in capsys.readouterr().out


def seeded(server: PlanServer) -> str:
    """A plan the server already kept from an earlier run (the kit printed its id and stopped)."""

    server.plan({"episodes": EPISODES, "title": None})
    return "plan_1"


def test_plan_flag_with_words_changes_that_plan_and_asks_about_the_new_one(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_id = seeded(server)
    answers(monkeypatch, "y")
    assert cut_command(args(episodes=None, plan=plan_id, change=THREE_PARTS)) == 0
    assert server.plans() == []
    assert server.changes() == [(plan_id, {"change_request": THREE_PARTS})]
    assert server.creates() == [{"plan_id": "plan_2"}]
    assert screen(capsys.readouterr().out) == three_parts("plan_2")


def test_plan_flag_with_yes_makes_that_plan_as_shown_without_a_question(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_id = seeded(server)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("a question was asked"))
    assert cut_command(args(episodes=None, plan=plan_id, yes=True)) == 0
    assert server.plans() == [] and server.changes() == []
    assert server.creates() == [{"plan_id": plan_id}]
    assert "Part 3: https://media.example/p3.mp4" in capsys.readouterr().out


def test_plan_flag_with_words_and_yes_changes_then_makes(
    server: PlanServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_id = seeded(server)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("a question was asked"))
    assert (
        cut_command(args(episodes=None, plan=plan_id, change=THREE_PARTS, yes=True))
        == 0
    )
    assert server.changes() == [(plan_id, {"change_request": THREE_PARTS})]
    assert server.creates() == [{"plan_id": "plan_2"}]


def test_plan_flag_with_blank_words_and_no_yes_makes_nothing(
    server: PlanServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_id = seeded(server)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("a question was asked"))
    assert cut_command(args(episodes=None, plan=plan_id, change="   ")) == 2
    assert server.changes() == [] and server.creates() == []


def test_yes_does_not_make_a_changed_plan_the_server_answered_with_a_question(
    server: PlanServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers(monkeypatch)
    assert cut_command(args(change=UNCLEAR, yes=True)) == 5
    assert server.creates() == []


@pytest.mark.parametrize("reply", ["ok", "make it", "Yes"])
def test_plain_ways_of_saying_yes_make_the_plan(
    server: PlanServer, monkeypatch: pytest.MonkeyPatch, reply: str
) -> None:
    answers(monkeypatch, reply)
    assert cut_command(args()) == 0
    assert server.changes() == [] and server.creates() == [{"plan_id": "plan_1"}]


def test_a_405_from_an_older_server_says_it_cannot_plan_the_parts_yet(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server.plan_missing, server.plan_missing_status = True, 405
    answers(monkeypatch)
    assert cut_command(args(yes=True)) == 5
    assert picked_cut.NO_PLAN_ROUTE in capsys.readouterr().err


@pytest.mark.parametrize(
    "over",
    [
        {"episodes": None},
        {"episodes": ["sp_a:1", "sp_b:1"], "yes": True},
        {"episodes": None, "show": "Dead Heat", "yes": True},
        {"episodes": None, "title": "X", "yes": True},
        {"episodes": None, "new_music": True, "yes": True},
        {"episodes": None, "parts": 3, "yes": True},
        {"episodes": None, "part_text": ["3:ending=Gone"], "yes": True},
    ],
)
def test_plan_flag_alone_or_with_fresh_plan_settings_is_refused_before_any_call(
    server: PlanServer, capsys: pytest.CaptureFixture[str], over: dict[str, Any]
) -> None:
    assert cut_command(args(plan="plan_1", **over)) == 2
    assert server.seen == []
    assert "Stopped:" in capsys.readouterr().err


def test_a_plan_the_server_no_longer_has_stops_plainly(
    server: PlanServer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(episodes=None, plan="plan_9", yes=True)) == 5
    assert server.creates() == [{"plan_id": "plan_9"}]
    assert capsys.readouterr().err.strip() == f"NOT DONE: {TAKEN} Nothing was made."
    assert (
        cut_command(args(episodes=None, plan="plan_9", change=THREE_PARTS, yes=True))
        == 5
    )


def test_no_instagram_or_tiktok_in_anything_a_punchy_edit_prints(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from creation.cli_produce import main

    answers(monkeypatch, UNCLEAR, THREE_PARTS, "y")
    assert cut_command(args()) == 0
    server.plan_missing = True
    assert cut_command(args(yes=True)) == 5
    for command in ("cut", "join", "finish"):
        with pytest.raises(SystemExit):
            main([command, "--help"])
    printed = capsys.readouterr()
    everything = (printed.out + printed.err).lower()
    assert "part 3: https://media.example/p3.mp4" in everything
    assert "instagram" not in everything and "tiktok" not in everything


@pytest.mark.parametrize("bad", ["2=Hi", "x:opening=Hi", "2:middle=Hi", "2:opening="])
def test_a_bad_part_text_is_refused_before_any_call(
    server: PlanServer, bad: str
) -> None:
    assert cut_command(args(part_text=[bad], yes=True)) == 2
    assert server.seen == []


def test_an_edit_that_is_not_ready_makes_nothing(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def not_ready(body: dict[str, Any]) -> httpx.Response:
        error = {
            "code": "series_cut_not_ready",
            "message": "An episode is still being finished.",
        }
        return httpx.Response(409, json={"error": error})

    monkeypatch.setattr(server, "plan", not_ready)
    assert cut_command(args(yes=True)) == 5
    assert server.creates() == []
    assert capsys.readouterr().err.strip() == (
        "NOT DONE: An episode is still being finished. Run the same command again later; nothing was made."
    )


def test_new_music_goes_to_the_plan_and_make_sends_only_the_id(
    server: PlanServer,
) -> None:
    assert cut_command(args(new_music=True, yes=True)) == 0
    assert server.plans()[0]["new_music"] is True
    assert server.creates() == [{"plan_id": "plan_1"}]


def test_only_the_parts_are_offered_never_the_whole_video(
    server: PlanServer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(yes=True)) == 0
    out = capsys.readouterr().out
    assert "Part 3: https://media.example/p3.mp4" in out
    assert "Part 4: https://media.example/p4.mp4" in out
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
    assert "--plan PLAN_ID" in text


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
    assert seen_at_question[0].splitlines() == two_parts("plan_1")
    (planned,) = server.plans()
    assert planned["episodes"] == [
        {"spine_id": "spine_test", "episode": 2},
        {"spine_id": "spine_test", "episode": 1},
    ]
    assert server.creates() == [{"plan_id": "plan_1"}]
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
    assert server.creates() == [{"plan_id": "plan_1"}]
    assert 'Part 8: starts "opening 8", ends "GONE"' in (capsys.readouterr().out)


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
    assert main([command, "--desk", str(post_desk), "--parts", "3"]) == 2
    assert server.seen == [] and finished == []
    assert "add --episodes" in capsys.readouterr().err
