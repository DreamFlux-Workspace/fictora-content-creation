"""``emotion``: one call to the server's emotion plan (The Gallery Heiress, Back From the Sea, 15 Oct 2026).

Characters cried in almost every shot. Stripping tear words from shots by hand did not stick and cost
redraws; the server now keeps how a character feels apart from who they are (``GET``/``POST
/v1/spines/{id}/emotions``, ``PUT .../cast/{id}/emotional-default``) and the kit only calls it.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from creation import emotion_commands as em
from creation import episode_commands as ec
from creation.cli_produce import main as produce_main
from creation.production_state import load_production
from fake_api import FakeApi, serve_emotions, spine_fixture


def _crying() -> dict[str, Any]:
    """Two drawn episodes; Hana cries on every shot and says her ep 2 line through tears."""

    spine = spine_fixture()
    for frame in spine["frames"]:
        frame["visual_brief"]["subject_blocking"][0]["reaction_kind"] = "tear_up"
    spine["beats"][1]["dialogue_lines"][0]["delivery"] = "through_tears"
    return spine


def _serve(api: FakeApi, spine: dict[str, Any] | None = None) -> None:
    api.spine_doc = spine or _crying()
    serve_emotions(api)


def _faces(api: FakeApi, episode_id: str) -> list[Any]:
    return [
        entry.get("reaction_kind")
        for frame in api.spine_doc["frames"]
        if frame["episode_id"] == episode_id
        for entry in frame["visual_brief"]["subject_blocking"]
    ]


def _sent(api: FakeApi) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, body)
        for method, path, body, _ in api.calls
        if method in {"POST", "PUT", "PATCH"} and body is not None
    ]


def test_a_change_is_a_preview_until_confirmed(desk: Path, api: FakeApi) -> None:
    _serve(api)
    out = io.StringIO()

    em.run_emotion_add(desk, cast="Hana", episodes="2-", no_tears=True, out=out)

    ((method, path, body),) = _sent(api)
    assert (method, path) == ("POST", "/v1/spines/sp1/emotions")
    assert body == {
        "spine_version": "v5",
        "add": {
            "cast_id": "cast_hana",
            "from_episode": 2,
            "through_episode": None,
            "mood": None,
            "no_tears": True,
            "note": "",
        },
        "preview": True,
    }
    # Nothing stored: the faces still cry, the plan is empty, no board is marked stale on the desk.
    assert _faces(api, "ep_02") == ["tear_up", "tear_up"]
    assert not api.spine_doc.get("emotion_plan")
    assert load_production(desk).boards_stale == []
    text = out.getvalue()
    assert "Preview: Hana, ep 2 onward: no tears" in text
    assert "ep02: 2 face(s) and 1 line(s) would change" in text
    assert "face t1 frame_ep_02_01 (Hana): tear_up -> freeze" in text
    assert "line beat_ep_02_01 (Hana): through_tears -> trailing_off" in text
    assert "$0.30 each, $0.30 in all" in text
    assert "redraw-board --desk" in text and "--episode 2 --take t1" in text
    assert "Not applied" in text and "--no-tears --episodes 2- --confirm" in text
    assert "ep01" not in text


def test_confirm_applies_names_the_boards_and_asks_the_human(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    out = io.StringIO()

    em.run_emotion_add(
        desk,
        cast="hana",
        episodes="2-",
        no_tears=True,
        note="she has cried enough",
        confirm=True,
        out=out,
    )

    ((_, _, body),) = _sent(api)
    assert body["preview"] is False and body["add"]["note"] == "she has cried enough"
    assert api.spine_doc["emotion_plan"][0]["cast_id"] == "cast_hana"
    assert _faces(api, "ep_02") == ["freeze", "freeze"]
    assert _faces(api, "episode_01") == ["tear_up", "tear_up"]
    assert load_production(desk).boards_stale == ["ep02-t1"]
    text = out.getvalue()
    assert "ASK THE HUMAN NOW" in text and "ep02-t1" in text and "$0.30" in text
    assert "Applied" in text
    # The kit keeps no plan of its own: only the server's story changed.
    assert not list(desk.rglob("*emotion*.json"))

    again = io.StringIO()
    em.run_emotion_add(
        desk,
        cast="Hana",
        episodes="2-",
        no_tears=True,
        note="she has cried enough",
        confirm=True,
        out=again,
    )
    assert "Nothing changed" in again.getvalue()


def test_a_series_wide_no_tears_rule_from_the_cli(
    desk: Path, api: FakeApi, capsys: Any
) -> None:
    _serve(api)

    code = produce_main(
        [
            "emotion",
            "--desk",
            str(desk),
            "--everyone",
            "--no-tears",
            "--episodes",
            "1-",
            "--confirm",
        ]
    )

    assert code == 0
    ((_, _, body),) = _sent(api)
    assert body["add"]["cast_id"] is None and body["add"]["no_tears"] is True
    assert _faces(api, "episode_01") == ["freeze", "freeze"]
    assert _faces(api, "ep_02") == ["freeze", "freeze"]
    assert "everyone, ep 1 onward: no tears" in capsys.readouterr().out


def test_a_mood_for_a_range(desk: Path, api: FakeApi) -> None:
    _serve(api)
    out = io.StringIO()

    em.run_emotion_add(desk, cast="Hana", episodes="1-2", mood="Composed", out=out)

    ((_, _, body),) = _sent(api)
    assert body["add"]["mood"] == "composed"
    assert body["add"]["through_episode"] == 2 and body["add"]["no_tears"] is None
    assert "Hana, ep 1-2: feels composed" in out.getvalue()


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["--everyone", "--mood", "sad", "--episodes", "1"], "sets tears only"),
        (["--cast", "Hana", "--no-tears"], "say which episodes"),
        (
            ["--cast", "Hana", "--everyone", "--no-tears", "--episodes", "1"],
            "name one character",
        ),
        (
            ["--cast", "Hana", "--no-tears", "--tears-ok", "--episodes", "1"],
            "contradict",
        ),
        (["--remove", "emo_1", "--cast", "Hana"], "--remove comes alone"),
        (["--default", "sad"], "go with --cast only"),
        (["--confirm"], "nothing to change"),
        (["--cast", "Hana", "--no-tears", "--episodes", "5,7"], "--episodes takes"),
    ],
)
def test_flags_that_do_not_go_together_stop_before_sending(
    desk: Path, api: FakeApi, capsys: Any, argv: list[str], said: str
) -> None:
    _serve(api)

    code = produce_main(["emotion", "--desk", str(desk), *argv])

    assert code != 0
    assert said in capsys.readouterr().err
    assert _sent(api) == []


def test_remove_takes_an_entry_off_and_an_unknown_id_reads_plainly(
    desk: Path, api: FakeApi
) -> None:
    spine = _crying()
    spine["emotion_plan"] = [
        {
            "entry_id": "emo_1",
            "cast_id": "cast_hana",
            "from_episode": 1,
            "through_episode": None,
            "mood": "sad",
            "no_tears": None,
            "note": "",
        }
    ]
    _serve(api, spine)

    with pytest.raises(ec.CommandStopped, match="lists the entries"):
        em.run_emotion_remove(desk, entry_id="emo_9", confirm=True, out=io.StringIO())
    assert api.spine_doc["emotion_plan"]

    out = io.StringIO()
    em.run_emotion_remove(desk, entry_id="emo_1", out=out)
    assert api.spine_doc["emotion_plan"]  # a preview stores nothing
    assert "Preview: remove [emo_1] Hana, ep 1 onward: feels sad" in out.getvalue()

    em.run_emotion_remove(desk, entry_id="emo_1", confirm=True, out=io.StringIO())
    assert api.spine_doc["emotion_plan"] == []


def test_set_and_clear_a_default(desk: Path, api: FakeApi, capsys: Any) -> None:
    _serve(api)
    path = "/v1/spines/sp1/cast/cast_ren/emotional-default"

    assert (
        produce_main(
            [
                "emotion",
                "--desk",
                str(desk),
                "--cast",
                "Ren",
                "--default",
                "composed",
                "--note",
                "reserved; hides it behind politeness",
            ]
        )
        == 0
    )
    ((method, called, body),) = _sent(api)
    assert (method, called) == ("PUT", path)
    assert body == {
        "spine_version": "v5",
        "emotional_default": {
            "mood": "composed",
            "note": "reserved; hides it behind politeness",
        },
        "preview": True,
    }
    assert "emotional_default" not in api.spine_doc["cast"][1]
    text = capsys.readouterr().out
    assert (
        'Preview: Ren\'s default: composed ("reserved; hides it behind politeness")'
        in text
    )
    assert "No drawn board needs a redraw." in text

    produce_main(
        [
            "emotion",
            "--desk",
            str(desk),
            "--cast",
            "Ren",
            "--default",
            "composed",
            "--confirm",
        ]
    )
    assert api.spine_doc["cast"][1]["emotional_default"] == {
        "mood": "composed",
        "note": "",
    }

    produce_main(
        [
            "emotion",
            "--desk",
            str(desk),
            "--cast",
            "Ren",
            "--clear-default",
            "--confirm",
        ]
    )
    assert _sent(api)[-1][2]["emotional_default"] is None
    assert api.spine_doc["cast"][1]["emotional_default"] is None
    assert "Ren's default: none (neutral)" in capsys.readouterr().out


def test_a_conflict_retries_once_with_the_latest_version(
    desk: Path, api: FakeApi
) -> None:
    _serve(api)
    serve = api.routes[("POST", "/v1/spines/sp1/emotions")]
    seen: list[str] = []

    def bumped(m: str, p: str, body: dict[str, Any] | None) -> Any:
        seen.append((body or {})["spine_version"])
        if len(seen) == 1:
            api.spine_doc["spine_version"] = "v6"  # someone edited the story meanwhile
        return serve(m, p, body)

    api.routes[("POST", "/v1/spines/sp1/emotions")] = bumped

    em.run_emotion_add(
        desk, cast="Hana", episodes="1", no_tears=True, confirm=True, out=io.StringIO()
    )

    assert seen == ["v5", "v6"]
    assert _faces(api, "episode_01") == ["freeze", "freeze"]


def test_an_older_server_stops_and_edits_nothing(desk: Path, api: FakeApi) -> None:
    api.spine_doc = _crying()
    api.routes[("GET", "/v1/spines/sp1/emotions")] = SystemExit("not_found")

    def missing(*_: Any) -> Any:
        return 404, {"detail": "Not Found"}

    api.get_optional = lambda path: missing()  # type: ignore[method-assign]

    with pytest.raises(ec.CommandStopped, match="older than the emotion plan"):
        em.run_emotion_add(
            desk, cast="Hana", episodes="1", no_tears=True, out=io.StringIO()
        )
    assert _sent(api) == []
    assert _faces(api, "episode_01") == ["tear_up", "tear_up"]


def test_an_unknown_character_stops_with_the_names(desk: Path, api: FakeApi) -> None:
    _serve(api)

    with pytest.raises(ec.CommandStopped, match="characters: Hana, Ren"):
        em.run_emotion_add(
            desk, cast="Seo-yeon", episodes="1", no_tears=True, out=io.StringIO()
        )
    assert _sent(api) == []


def test_list_reads_the_server_view(desk: Path, api: FakeApi, capsys: Any) -> None:
    spine = _crying()
    spine["cast"][0]["emotional_default"] = {"mood": "composed", "note": "proud"}
    spine["emotion_plan"] = [
        {
            "entry_id": "emo_1",
            "cast_id": None,
            "from_episode": 2,
            "through_episode": None,
            "mood": None,
            "no_tears": True,
            "note": "",
        },
        {
            "entry_id": "emo_2",
            "cast_id": "cast_hana",
            "from_episode": 1,
            "through_episode": 1,
            "mood": "sad",
            "no_tears": None,
            "note": "the funeral",
        },
    ]
    _serve(api, spine)

    assert produce_main(["emotion", "--desk", str(desk), "--cast", "Hana"]) == 0

    text = capsys.readouterr().out
    assert "[emo_1] everyone, ep 2 onward: no tears" in text
    assert 'Hana (cast_hana): default composed ("proud")' in text
    assert '[emo_2] Hana, ep 1: feels sad ("the funeral")' in text
    assert "ep01: sad (plan), may cry, 2 shot(s) crying" in text
    assert "ep02: composed (default), no tears, 2 shot(s) crying" in text
    assert "Ren (" not in text
    assert _sent(api) == []


@pytest.mark.parametrize(
    ("raw", "parsed"),
    [("5", (5, 5)), ("5-6", (5, 6)), ("5-", (5, None)), (" 5+ ", (5, None))],
)
def test_ranges_parse(raw: str, parsed: tuple[int, int | None]) -> None:
    assert em.parse_range(raw) == parsed


@pytest.mark.parametrize("raw", ["", "five", "6-5", "0", "5,7", "5+6"])
def test_bad_ranges_stop(raw: str) -> None:
    with pytest.raises(ec.CommandStopped):
        em.parse_range(raw)
