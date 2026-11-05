"""``cut`` plans the parts with the server, asks "Make it?", and makes nothing until yes.

Founder, 5 Nov 2026: a punchy edit is parts only; every part is printed before anything is made.
The kit prints the server's own words and asks one question; anything else the human types goes
back to the server as the change they want, and the new plan is asked again.
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
#: The fake server's reading of a typed change: these words split it into three parts.
THREE_PARTS = "make it three parts"
#: These words make the server ask back instead of planning anew.
UNCLEAR = "different"
#: A second change, read by the fake server only on top of the plan it is sent.
END_FIVE = "end part 5 with she runs"
MAX_PARTS = 4
OUT_OF_RANGE = "This edit can have 1 to 4 parts."
#: Where the fake server splits 2 and 3 parts; odd numbers so the kit can't have worked them out.
SPLITS = {2: [51.3], 3: [51.3, 98.7]}
SHOWS = {"sp_a": "Dead Heat", "sp_b": "Low Tide"}


class PlanServer:
    """Plans 2 parts starting at Part 3 (this creator already posted Parts 1 and 2) unless told otherwise."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, Any]] = []
        self.plan_missing = False
        self.too_long_over = 1000
        self.too_long_message = TOO_LONG

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
        change = body.get("change_request")
        count = 3 if change == THREE_PARTS else body.get("part_count", 2)
        start = body.get("start_number", 3)
        if count > MAX_PARTS:
            return httpx.Response(
                422,
                json={
                    "error": {
                        "code": "series_cut_parts_out_of_range",
                        "message": OUT_OF_RANGE,
                        "details": {
                            "parts": count,
                            "parts_max": MAX_PARTS,
                            "parts_suggested": 2,
                        },
                    }
                },
            )
        texts = {t["number"]: dict(t) for t in body.get("texts", [])}
        if change == END_FIVE:
            texts.setdefault(5, {"number": 5})["ending_text"] = "she runs"
        for n, t in texts.items():
            if any(
                len(t.get(k, "")) > self.too_long_over
                for k in ("opening_text", "ending_text")
            ):
                side = (
                    "opening"
                    if len(t.get("opening_text", "")) > self.too_long_over
                    else "ending"
                )
                return httpx.Response(
                    422,
                    json={
                        "error": {
                            "code": "part_text_too_long",
                            "message": self.too_long_message,
                            "part": n,
                            "which": side,
                            "details": {"part": n, "which": side},
                        }
                    },
                )
        default = {
            3: ("Mira finds the key", "the door is already open"),
            4: ("Someone is inside", "she drops the key"),
            5: ("The lights go out", "he says her name"),
        }
        sent = body.get("splits")
        splits = (
            sent
            if sent is not None and count == body.get("part_count")
            else SPLITS.get(count)
        )
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
        plan: dict[str, Any] = {
            "part_count": count,
            "suggested_part_count": 2,
            "max_part_count": MAX_PARTS,
            "start_number": start,
            "suggested_start_number": 3,
            "parts": parts,
            "splits": splits,
            "summary": "\n".join(summary),
            "clarify": None,
        }
        if change == UNCLEAR:
            plan["clarify"] = "Different how: more parts, fewer, or other openings?"
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
        change=None, yes=False,
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


TWO_PART_PRINTOUT = [
    "I'll split this into 2 parts:",
    'Part 3: starts "Mira finds the key", ends "the door is already open"',
    'Part 4: starts "Someone is inside", ends "she drops the key"',
    "Part 3 plays Dead Heat ep 1.",
    "Part 4 plays Low Tide ep 1.",
    "Make it, or tell me what to change?",
    picked_cut.GO_AHEAD,
]
THREE_PART_PRINTOUT = [
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
THREE_PART_TEXTS = [
    {"number": 3, "opening_text": "Mira finds the key", "ending_text": "the door is already open"},
    {"number": 4, "opening_text": "Someone is inside", "ending_text": "she drops the key"},
    {"number": 5, "opening_text": "The lights go out", "ending_text": "he says her name"},
]  # fmt: skip


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
    assert seen_at_question[0].splitlines() == TWO_PART_PRINTOUT
    assert seen_at_question[0].count("Make it") == 1, "asked twice"


def test_typed_words_go_to_the_server_as_the_change_and_the_new_plan_is_asked_again(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch, THREE_PARTS, "n")
    assert cut_command(args()) == 5
    assert server.plans() == [
        {"episodes": EPISODES, "title": None},
        {
            "episodes": EPISODES,
            "title": None,
            "part_count": 2,
            "start_number": 3,
            "texts": THREE_PART_TEXTS[:2],
            "splits": SPLITS[2],
            "change_request": THREE_PARTS,
        },
    ]
    assert server.creates() == []
    assert screen(capsys.readouterr().out) == TWO_PART_PRINTOUT + THREE_PART_PRINTOUT


def test_a_second_change_keeps_the_first(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch, THREE_PARTS, END_FIVE, "y")
    assert cut_command(args()) == 0
    texts = [*THREE_PART_TEXTS[:2], {**THREE_PART_TEXTS[2], "ending_text": "she runs"}]
    assert server.plans()[2] == {
        "episodes": EPISODES,
        "title": None,
        "part_count": 3,
        "start_number": 3,
        "texts": THREE_PART_TEXTS,
        "splits": SPLITS[3],
        "change_request": END_FIVE,
    }
    assert server.creates() == [
        {
            "episodes": EPISODES,
            "title": None,
            "part_count": 3,
            "start_number": 3,
            "texts": texts,
            "splits": SPLITS[3],
        }
    ]
    printed = screen(capsys.readouterr().out)
    assert printed[len(TWO_PART_PRINTOUT) + len(THREE_PART_PRINTOUT) :] == [
        *THREE_PART_PRINTOUT[:3],
        'Part 5: starts "The lights go out", ends "she runs"',
        *THREE_PART_PRINTOUT[4:],
    ]


def test_a_part_length_out_of_range_is_shown_plainly_and_the_last_plan_asked_again(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch, "make part 4 tiny", "y")
    server_plan = server.plan

    def too_short(body: dict[str, Any]) -> httpx.Response:
        if body.get("change_request") == "make part 4 tiny":
            refusal = {
                "code": "part_length_out_of_range",
                "message": "x",
                "details": {"part": 4, "seconds": 21.4},
            }
            return httpx.Response(422, json={"error": refusal})
        return server_plan(body)

    server.plan = too_short  # type: ignore[method-assign]
    assert cut_command(args()) == 0
    assert screen(capsys.readouterr().out) == [
        *TWO_PART_PRINTOUT,
        "Part 4 would be 21.4 s; parts must be 35 to 68 s.",
        *TWO_PART_PRINTOUT,
    ]
    assert [c["splits"] for c in server.creates()] == [SPLITS[2]]


def test_yes_makes_exactly_the_last_plan_shown(
    server: PlanServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers(monkeypatch, THREE_PARTS, "y")
    assert cut_command(args()) == 0
    assert server.creates() == [
        {
            "episodes": EPISODES,
            "title": None,
            "part_count": 3,
            "start_number": 3,
            "texts": THREE_PART_TEXTS,
            "splits": SPLITS[3],
        }
    ]
    assert not {"cover_url", "episodes", "start_s", "end_s"} & set(
        server.creates()[0]["texts"][0]
    )


def test_the_servers_question_back_is_printed(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch, UNCLEAR, "")
    assert cut_command(args()) == 5
    printed = screen(capsys.readouterr().out)
    assert printed[len(TWO_PART_PRINTOUT) :] == [
        "Different how: more parts, fewer, or other openings?",
        *TWO_PART_PRINTOUT,
    ]


@pytest.mark.parametrize("answer", ["n", "N", "", "no", "  "])
def test_no_or_nothing_typed_makes_nothing(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
) -> None:
    answers(monkeypatch, answer)
    assert cut_command(args()) == 5
    assert server.creates() == [] and len(server.plans()) == 1
    assert picked_cut.NOT_CONFIRMED in capsys.readouterr().err


def test_no_keyboard_counts_as_no(
    server: PlanServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def closed() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)
    assert cut_command(args()) == 5
    assert server.creates() == []


def test_change_flag_goes_to_the_plan_and_yes_makes_that_plan_without_asking(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch)
    assert cut_command(args(change=THREE_PARTS, yes=True)) == 0
    assert server.plans() == [
        {"episodes": EPISODES, "title": None, "change_request": THREE_PARTS}
    ]
    assert [(c["part_count"], c["texts"]) for c in server.creates()] == [
        (3, THREE_PART_TEXTS)
    ]
    assert "change_request" not in server.creates()[0]
    assert THREE_PART_PRINTOUT[0] in capsys.readouterr().out


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
    made = server.creates()[0]
    assert (made["part_count"], made["start_number"]) == (3, 5)
    assert {"number": 6, "opening_text": "Back again", "ending_text": "Gone"} in made[
        "texts"
    ]
    assert 'Part 6: starts "Back again", ends "Gone"' in capsys.readouterr().out


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


def test_too_long_text_is_shown_plainly_and_asked_again(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server.too_long_over = 20
    answers(monkeypatch, "y")
    long_ending = "4:ending=she drops the key and runs into the night"
    assert cut_command(args(part_text=[long_ending])) == 0
    assert screen(capsys.readouterr().out) == [TOO_LONG, *TWO_PART_PRINTOUT]
    assert [c["texts"][1]["ending_text"] for c in server.creates()] == [
        "she drops the key"
    ]


def test_too_long_without_words_names_the_part_from_the_error_details(
    server: PlanServer, capsys: pytest.CaptureFixture[str]
) -> None:
    server.too_long_over = 20
    server.too_long_message = ""
    long_ending = "4:ending=she drops the key and runs into the night"
    assert cut_command(args(part_text=[long_ending], yes=True)) == 2
    assert capsys.readouterr().err.strip() == (
        "Stopped: The text for part 4 is too long to fit. Nothing was made."
    )


def test_too_many_parts_typed_is_shown_plainly_and_the_last_plan_asked_again(
    server: PlanServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers(monkeypatch, "9 parts please", "y")
    server_plan = server.plan

    def nine_parts(body: dict[str, Any]) -> httpx.Response:
        if body.get("change_request") == "9 parts please":
            body = {**body, "part_count": 9}
        return server_plan(body)

    server.plan = nine_parts  # type: ignore[method-assign]
    assert cut_command(args()) == 0
    assert screen(capsys.readouterr().out) == [
        *TWO_PART_PRINTOUT,
        OUT_OF_RANGE,
        *TWO_PART_PRINTOUT,
    ]
    assert [c["part_count"] for c in server.creates()] == [2]


def test_too_many_parts_under_yes_stops_with_the_servers_words(
    server: PlanServer, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cut_command(args(parts=9, yes=True)) == 2
    assert server.creates() == []
    assert (
        capsys.readouterr().err.strip() == f"Stopped: {OUT_OF_RANGE} Nothing was made."
    )


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

    server.plan = not_ready  # type: ignore[method-assign]
    assert cut_command(args(yes=True)) == 5
    assert server.creates() == []
    assert capsys.readouterr().err.strip() == (
        "NOT DONE: An episode is still being finished. Run the same command again later; nothing was made."
    )


def test_new_music_goes_to_the_plan_and_the_cut(server: PlanServer) -> None:
    assert cut_command(args(new_music=True, yes=True)) == 0
    assert server.plans()[0]["new_music"] is True
    assert server.creates()[0]["new_music"] is True


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


# --- join / finish --desk D --episodes: the same plan, print and question as cut ------------------------

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
    assert seen_at_question[0].splitlines() == TWO_PART_PRINTOUT
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
    made = server.creates()[0]
    assert (made["part_count"], made["start_number"]) == (3, 7)
    assert {"number": 8, "opening_text": "opening 8", "ending_text": "GONE"} in made[
        "texts"
    ]
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
    assert main([command, "--desk", str(post_desk), "--yes"]) == 2
    assert server.seen == [] and finished == []
    assert "add --episodes" in capsys.readouterr().err
