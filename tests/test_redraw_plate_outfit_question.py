"""A note that changes what a character wears asks "just this episode, or from now on?" before any redraw.

fictora-drama #1156 (The Funeral Guest, 28 Oct 2026): the server reads the
note, writes the question and owns the change; ``redraw-plate`` sends the
desk's episode with the note, prints the server's words, stops before drawing
while the question is open, and sends ``--outfit`` as the answer. A server
without #1156 (no ``episode_ordinal``, no ``outfit_change``) works as before.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from conftest import set_phase
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.harness.http_util import ApiRefused
from creation.ops.state import load_series
from fake_api import FakeApi, png_bytes

NOTES = "/v1/spines/sp1/cast/cast_ren/notes"
ANSWER = "/v1/spines/sp1/cast/cast_ren/notes/n1/outfit"
REGENERATE = "/v1/spines/sp1/cast/cast_ren/regenerate"
NOTE = "She wears her funeral hanbok: white jeogori, black chima."
OUTFIT = "funeral hanbok: white jeogori, black chima"
QUESTION = f"Should Ren wear {OUTFIT} just for episode 1, or from now on?"


def _asked(episode: int) -> dict[str, Any]:
    return {
        "outfit": OUTFIT,
        "status": "asked",
        "episode_ordinals": [episode],
        "question": f"Should Ren wear {OUTFIT} just for episode {episode}, or from now on?",
        "options": [
            {"answer": "this_episode", "label": f"Just episode {episode}"},
            {"answer": "from_now_on", "label": "From now on"},
        ],
    }


def _card(api: FakeApi) -> dict[str, Any]:
    return next(c for c in api.spine_doc["cast"] if c["cast_id"] == "cast_ren")


def _serve(
    api: FakeApi, *, new_server: bool = True, applied: str | None = None
) -> None:
    """A note route like #1156 (or an older one), the answer route, and a redraw."""

    def add_note(_m: str, _p: str, body: dict[str, Any] | None) -> Any:
        body = body or {}
        if not new_server and "episode_ordinal" in body:
            return ApiRefused(
                f"HTTP 422 POST {NOTES}: episode_ordinal: Extra inputs are not permitted",
                status=422,
                body={
                    "detail": [
                        {"type": "extra_forbidden", "loc": ["body", "episode_ordinal"]}
                    ]
                },
            )
        note: dict[str, Any] = {"note_id": "n1", "text": body["text"]}
        if new_server and "wears" in body["text"]:
            if applied == "from_now_on":
                note["outfit_change"] = {"outfit": OUTFIT, "status": "from_now_on"}
            else:
                note["outfit_change"] = _asked(int(body.get("episode_ordinal") or 1))
        _card(api).setdefault("creator_notes", []).append(note)
        api.spine_doc["spine_version"] = "v6"
        return {"spine": api.spine_doc}

    def answer(_m: str, _p: str, body: dict[str, Any] | None) -> Any:
        note = next(n for n in _card(api)["creator_notes"] if n["note_id"] == "n1")
        change = note["outfit_change"]
        if change["status"] != "asked":
            return ApiRefused(
                "HTTP 409 POST: outfit_question_not_open",
                status=409,
                body={"error": {"code": "outfit_question_not_open"}},
            )
        if (body or {})["answer"] == "from_now_on":
            note["outfit_change"] = {"outfit": OUTFIT, "status": "from_now_on"}
        else:
            note["outfit_change"] = {
                "outfit": OUTFIT,
                "status": "episodes",
                "episode_ordinals": change["episode_ordinals"],
                "wardrobe_variant": "funeral hanbok",
            }
        api.spine_doc["spine_version"] = "v7"
        return {"spine": api.spine_doc}

    def redraw(_m: str, _p: str, _b: dict[str, Any] | None) -> dict[str, Any]:
        for asset in api.spine_doc["media_assets"]:
            if asset.get("relation_id") == "cast_ren":
                asset["url"] = "https://r2.example/ren-v2.png"
        return {"job_id": "job_ren"}

    api.routes[("POST", NOTES)] = add_note
    api.routes[("POST", ANSWER)] = answer
    api.routes[("POST", REGENERATE)] = redraw
    api.jobs["job_ren"] = {"job_id": "job_ren", "status": "completed"}


@pytest.fixture
def plated(desk: Path) -> Path:
    set_phase(desk, "wait_plates")
    plates = desk / "ep01" / "plates"
    plates.mkdir(parents=True, exist_ok=True)
    (plates / "plate-ep01-1-v1.png").write_bytes(png_bytes(200))
    (plates / "plate-ep01-2-v1.png").write_bytes(png_bytes(60))
    return desk


def test_an_outfit_note_stops_with_the_servers_question_before_any_draw(
    plated: Path, api: FakeApi
) -> None:
    _serve(api)

    with pytest.raises(ec.CommandStopped) as stop:
        ec.run_redraw_plate_with_note(plated, cast="Ren", note=NOTE, out=io.StringIO())

    text = str(stop.value)
    assert QUESTION in text, "the server's question, word for word"
    assert "Just episode 1:" in text and "From now on:" in text, (
        "the server's two labels"
    )
    assert f'--note "{NOTE}" --outfit this-episode' in text
    assert f'--note "{NOTE}" --outfit from-now-on' in text
    assert "Nothing was drawn or charged" in text
    assert api.posted(NOTES) == [
        {"spine_version": "v5", "text": NOTE, "episode_ordinal": 1}
    ], "the desk's episode rides with the note"
    assert api.posted(REGENERATE) == [], "no paid redraw while the question is open"
    assert api.posted(ANSWER) == []
    assert load_series(plated).spend_log == []

    # Run again without an answer: the note is not stacked, and the question stands.
    with pytest.raises(ec.CommandStopped, match="just for episode 1, or from now on"):
        ec.run_redraw_plate_with_note(plated, cast="Ren", note=NOTE, out=io.StringIO())
    assert len(api.posted(NOTES)) == 1 and api.posted(REGENERATE) == []


@pytest.mark.parametrize(
    ("flag", "sent", "line"),
    [
        ("from-now-on", "from_now_on", f"Ren's card now wears {OUTFIT}, from now on"),
        (
            "this-episode",
            "this_episode",
            f'Ren wears {OUTFIT} (the outfit "funeral hanbok") in episode 1 only',
        ),
    ],
)
def test_the_answer_goes_to_the_server_then_the_redraw_runs(
    plated: Path, api: FakeApi, flag: str, sent: str, line: str
) -> None:
    _serve(api)
    with pytest.raises(ec.CommandStopped):
        ec.run_redraw_plate_with_note(plated, cast="Ren", note=NOTE, out=io.StringIO())
    out = io.StringIO()

    made = ec.run_redraw_plate_with_note(
        plated, cast="Ren", note=NOTE, out=out, outfit=flag
    )

    assert len(api.posted(NOTES)) == 1, "the note is never sent twice"
    assert api.posted(ANSWER) == [{"spine_version": "v6", "answer": sent}]
    answered = next(i for i, c in enumerate(api.calls) if c[1] == ANSWER)
    drawn = next(i for i, c in enumerate(api.calls) if c[1] == REGENERATE)
    assert answered < drawn, "answered before anything is drawn"
    assert line in out.getvalue()
    assert made.name == "plate-ep01-2-v2.png"
    assert [e.unit for e in load_series(plated).spend_log] == [
        "plate-note-redraw:cast_ren"
    ]


def test_a_note_the_server_applied_says_what_changed_and_draws(
    plated: Path, api: FakeApi
) -> None:
    _serve(api, applied="from_now_on")
    out = io.StringIO()

    ec.run_redraw_plate_with_note(plated, cast="Ren", note=NOTE, out=out)

    assert f"Ren's card now wears {OUTFIT}, from now on" in out.getvalue()
    assert api.posted(ANSWER) == []
    assert len(api.posted(REGENERATE)) == 1


def test_an_answer_already_given_elsewhere_is_not_an_error(
    plated: Path, api: FakeApi
) -> None:
    _serve(api)
    with pytest.raises(ec.CommandStopped):
        ec.run_redraw_plate_with_note(plated, cast="Ren", note=NOTE, out=io.StringIO())
    answer = api.routes[("POST", ANSWER)]

    def raced(method: str, path: str, body: dict[str, Any] | None) -> Any:
        # The app answered "from now on" a moment earlier.
        answer(method, path, {"answer": "from_now_on"})
        return answer(method, path, body)

    api.routes[("POST", ANSWER)] = raced
    out = io.StringIO()

    ec.run_redraw_plate_with_note(
        plated, cast="Ren", note=NOTE, out=out, outfit="this-episode"
    )

    assert "from now on" in out.getvalue(), "the server's answer stands"
    assert len(api.posted(REGENERATE)) == 1


def test_an_older_server_gets_the_note_alone_and_draws_as_before(
    plated: Path, api: FakeApi
) -> None:
    _serve(api, new_server=False)
    out = io.StringIO()

    ec.run_redraw_plate_with_note(plated, cast="Ren", note=NOTE, out=out)

    assert api.posted(NOTES) == [
        {"spine_version": "v5", "text": NOTE, "episode_ordinal": 1},
        {"spine_version": "v5", "text": NOTE},
    ], "refused for the extra field (nothing saved), then sent as before"
    assert len(_card(api)["creator_notes"]) == 1
    assert api.posted(ANSWER) == []
    assert len(api.posted(REGENERATE)) == 1
    assert "wears" not in out.getvalue().replace(NOTE, "")


def test_another_note_refusal_is_not_resent(plated: Path, api: FakeApi) -> None:
    _serve(api)
    api.routes[("POST", NOTES)] = lambda _m, _p, _b: ApiRefused(
        "HTTP 409 POST: spine_version_conflict",
        status=409,
        body={"error": {"code": "spine_version_conflict"}},
    )

    with pytest.raises(SystemExit, match="spine_version_conflict"):
        ec.run_redraw_plate_with_note(plated, cast="Ren", note=NOTE, out=io.StringIO())

    assert len(api.posted(NOTES)) == 1
    assert api.posted(REGENERATE) == []


def test_an_answer_with_no_open_question_sends_nothing_for_it(
    plated: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(api)

    ec.run_redraw_plate_with_note(
        plated,
        cast="Ren",
        note="Older, a scar over the left brow",
        out=io.StringIO(),
        outfit="from-now-on",
    )

    assert api.posted(ANSWER) == []
    assert "no open outfit question" in capsys.readouterr().err
    assert len(api.posted(REGENERATE)) == 1


def test_the_cli_stops_with_the_question_and_takes_the_answer(
    plated: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _serve(api)
    argv = ["redraw-plate", "--desk", str(plated), "--cast", "Ren", "--note", NOTE]

    assert produce_main(argv) != 0
    assert QUESTION in capsys.readouterr().err
    assert api.posted(REGENERATE) == []

    assert produce_main([*argv, "--outfit", "from-now-on"]) == 0
    assert api.posted(ANSWER) == [{"spine_version": "v6", "answer": "from_now_on"}]
    assert len(api.posted(REGENERATE)) == 1

    with pytest.raises(SystemExit):
        produce_main([*argv, "--outfit", "always"])
