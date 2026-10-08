"""Background shouts (fictora-drama #682): printed in the script, edited or dropped with `line`, never characters."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest

from creation import episode_commands as ec
from creation.brief_lines import brief_vs_spine_lines
from creation.cli_produce import main as produce_main
from creation.crowd_lines import beat_shouts, episode_shouts, shout_label
from creation.harness.http_util import api_error_text
from creation.ops.state import episode_by_ordinal, load_series
from creation.spine_view import script_lines, spoken_lines
from fake_api import FakeApi, spine_fixture

SHOUT_ID = "crowd_episode_01_01_1"


def _with_shouts(spine: dict[str, Any], *, korean: bool = False) -> dict[str, Any]:
    """Episode 1's beat gets a soldier's shout after its lines and three kids' before them."""

    soldier: dict[str, Any] = {
        "line_id": SHOUT_ID,
        "speaker": {
            "label": "soldier",
            "count": 1,
            "age_band": "adult",
            "gender": "male",
        },
        "text": "Charge!",
    }
    kids: dict[str, Any] = {
        "line_id": "crowd_episode_01_01_2",
        "speaker": {"label": "kid", "count": 3},
        "text": "Again!",
        "order": "before_line",
    }
    if korean:
        soldier.update(spoken_text="돌격!", subtitle_text="Charge!")
        kids.update(spoken_text="또!", subtitle_text="Again!")
    spine["beats"][0]["crowd_lines"] = [soldier, kids]
    return spine


def _refusal(code: str, message: str, details: dict[str, Any]) -> SystemExit:
    body = {
        "error": {"code": code, "message": message, "details": details},
        "request_id": "req_1",
    }
    return SystemExit(
        f"HTTP 400 PATCH https://drama.example/v1/spines/sp1: {api_error_text(body)}"
    )


def _apply(api: FakeApi, patch: dict[str, Any]) -> None:
    """Apply a shout patch as fictora-drama #682 does, with its named refusals."""

    shouts = {
        s["line_id"]: s
        for b in api.spine_doc["beats"]
        for s in b.get("crowd_lines") or []
    }
    asked = [e["line_id"] for e in patch.get("crowd_lines") or []] + list(
        patch.get("remove_crowd_line_ids") or []
    )
    unknown = sorted(i for i in asked if i not in shouts)
    if unknown:
        raise _refusal("invalid_patch", "The story spine patch references unknown stable ids.",
                       {"unknown_crowd_line_ids": unknown})  # fmt: skip
    for entry in patch.get("crowd_lines") or []:
        if len(str(entry.get("text") or "x").split()) > 6:
            raise _refusal("invalid_patch", "The story spine violates the contract.",
                           {"violations": [{"loc": "beats.0.crowd_lines.0", "message":
                            f"crowd line {entry['line_id']} has 7 words; a background shout carries at most 6"}]})  # fmt: skip
        shouts[entry["line_id"]].update(
            {k: v for k, v in entry.items() if k != "line_id"}
        )
    removed = set(patch.get("remove_crowd_line_ids") or [])
    for beat in api.spine_doc["beats"]:
        if beat.get("crowd_lines"):
            beat["crowd_lines"] = [
                s for s in beat["crowd_lines"] if s["line_id"] not in removed
            ]


def _before_gate(api: FakeApi, *, korean: bool = False) -> None:
    lang = "ko-KR" if korean else "en-US"
    api.spine_doc = _with_shouts(
        spine_fixture(approved=False, spoken_language=lang), korean=korean
    )

    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        _apply(api, (body or {})["patch"])
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch


def _after_gate(api: FakeApi) -> list[dict[str, Any]]:
    api.spine_doc = _with_shouts(spine_fixture())
    edits: list[dict[str, Any]] = []

    def preview(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        edits.append(copy.deepcopy((body or {})["edit"]))
        return {"proposal_id": "prop_1", "items": []}

    def execute(*_: Any) -> dict[str, Any]:
        _apply(api, edits[-1]["patch"])
        return {"stale_storyboard_sets": []}

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = preview
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = execute
    return edits


def _patches(api: FakeApi) -> list[dict[str, Any]]:
    return [
        body["patch"] for method, _, body, _ in api.calls if method == "PATCH" and body
    ]


# --- the script view -------------------------------------------------------------------------


def test_the_script_prints_shouts_in_play_order_marked_background() -> None:
    script = script_lines(_with_shouts(spine_fixture()), episode=1, take_ids=["t1"])

    rows = [
        row.strip()
        for row in script
        if row.startswith("    ") and ":" in row and "shot" not in row
    ]
    assert rows == [
        '[background] Kids ×3: "Again!"',
        "Hana: We're closed.",
        "Ren: Not for me.",
        '[background] Soldier: "Charge!"',
    ]


def test_a_localized_show_prints_the_performed_shout_and_its_subtitle() -> None:
    spine = _with_shouts(spine_fixture(spoken_language="ko-KR"), korean=True)

    script = "\n".join(script_lines(spine, episode=1, take_ids=["t1"]))

    assert '[background] Soldier: "돌격!"  (Charge!)' in script
    assert '[background] Kids ×3: "또!"  (Again!)' in script


def test_shouts_are_never_characters_or_counted_lines() -> None:
    spine = _with_shouts(spine_fixture())
    beat = spine["beats"][0]
    # Two character lines plus two shouts: still two spoken lines, and no "too many lines" warning.
    beat["dialogue_lines"].append(
        {"line_id": "line_episode_01_03", "cast_id": "cast_hana", "text": "Go."}
    )

    assert [line.speaker for line in spoken_lines(spine, beat)] == [
        "Hana",
        "Ren",
        "Hana",
    ]
    assert not any(
        "lines; a 15 s take holds three" in row
        for row in script_lines(spine, episode=1, take_ids=["t1"])
    )
    assert ec.episode_lines(spine, episode=1)[-1][1]["line_id"] == "line_episode_01_03"
    assert all(
        not str(line["line_id"]).startswith("crowd_")
        for _, line in ec.episode_lines(spine, episode=1)
    )


def test_a_spine_without_shouts_prints_exactly_as_before() -> None:
    script = script_lines(spine_fixture(), episode=1, take_ids=["t1"])
    assert "[background]" not in "\n".join(script)
    assert beat_shouts(spine_fixture()["beats"][0]) == []


@pytest.mark.parametrize(
    ("label", "count", "shown"),
    [
        ("soldier", 1, "Soldier"),
        ("kid", 3, "Kids ×3"),
        ("old woman", 2, "Old women ×2"),
        ("vendor", 1, "Vendor"),
    ],
)
def test_who_shouts_is_labelled_like_the_server_captions(
    label: str, count: int, shown: str
) -> None:
    assert shout_label(label, count) == shown


def test_a_brief_line_the_writers_gave_to_background_people_is_shouted_not_cut() -> (
    None
):
    brief = (
        "## Lines\n\n### Take 1\n\n| # | Beat | Speaker | Original | Translation |\n| --- | --- | --- | --- | --- |\n"
        "| 1 | 1 | Hana | We're closed. | |\n| 2 | 1 | Soldier | Charge! | |\n"
    )
    spine = _with_shouts(spine_fixture())
    spine["beats"][0]["dialogue_lines"] = spine["beats"][0]["dialogue_lines"][:1]

    text = "\n".join(brief_vs_spine_lines(brief, spine, episode=1))

    assert "shouted by background people 1" in text
    assert (
        f'shouted    brief Soldier: "Charge!"  ->  [background] Soldier: "Charge!"  [{SHOUT_ID}]'
        in text
    )
    assert "cut 0" in text


# --- line --shout / --remove-shout -----------------------------------------------------------


def test_line_with_no_change_lists_the_shouts_with_their_numbers(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api)
    out = io.StringIO()

    ec.run_line(desk, episode=1, out=out)

    text = out.getvalue()
    assert "background shouts (not characters" in text
    assert f'  s1. {SHOUT_ID}  beat 1  [background] Soldier: "Charge!"' in text
    assert '  s2. crowd_episode_01_01_2  beat 1  [background] Kids ×3: "Again!"' in text
    # The numbered character lines are unchanged: shouts are numbered on their own.
    assert "  1. line_episode_01_01" in text and "  3." not in text


def test_shout_text_before_the_gate_patches_crowd_lines_and_resaves(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api)
    out = io.StringIO()

    ec.run_line(desk, episode=1, shout="s1", text="Hold the line!", out=out)

    assert _patches(api) == [
        {"crowd_lines": [{"line_id": SHOUT_ID, "text": "Hold the line!"}]}
    ]
    saved = json.loads((desk / "api" / "spine.json").read_text(encoding="utf-8"))
    assert saved["beats"][0]["crowd_lines"][0]["text"] == "Hold the line!"
    assert saved["beats"][0]["crowd_lines"][0]["speaker"]["label"] == "soldier"
    text = out.getvalue()
    assert f"ep01 background shout {SHOUT_ID}:" in text
    assert "  text: Charge!  ->  Hold the line!" in text
    assert 'now: [background] Soldier: "Hold the line!"' in text
    assert "script approval: not given yet" in text
    assert text.rstrip().splitlines()[-1].startswith("Applied")
    # The desk's take lines are the characters' only.
    take = episode_by_ordinal(load_series(desk), 1).takes[0]
    assert [line.speaker for line in take.lines] == ["Hana", "Ren"]


def test_remove_shout_after_the_gate_goes_through_the_cascade(
    desk: Path, api: FakeApi
) -> None:
    edits = _after_gate(api)
    out = io.StringIO()

    ec.run_line(desk, episode=1, remove_shout=SHOUT_ID, out=out)

    assert edits[0]["patch"] == {"remove_crowd_line_ids": [SHOUT_ID]}
    assert edits[0]["target_id"] == "episode_01"
    assert [s.line_id for s in episode_shouts(api.spine_doc, episode=1)] == [
        "crowd_episode_01_01_2"
    ]
    text = out.getvalue()
    assert f'  - {SHOUT_ID}  [background] Soldier: "Charge!"' in text
    assert "the server keeps this script approved" in text


def test_a_localized_show_pins_the_performed_shout_and_its_subtitle(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api, korean=True)

    ec.run_line(
        desk,
        episode=1,
        shout="1",
        spoken="진격!",
        subtitle="Advance!",
        out=io.StringIO(),
    )

    assert _patches(api) == [
        {
            "crowd_lines": [
                {
                    "line_id": SHOUT_ID,
                    "spoken_text": "진격!",
                    "subtitle_text": "Advance!",
                }
            ]
        }
    ]


@pytest.mark.parametrize(
    ("kwargs", "stop"),
    [
        (
            {"shout": "s1", "text": "Charge the gate now and fast, go!"},
            "at most 6 words; this one has 7",
        ),
        ({"shout": "s1", "spoken": "돌격!"}, "this show is en-US: use --text"),
        (
            {"shout": "s1", "text": "Go!", "subtitle": "Go!"},
            "--subtitle describes a pinned shout",
        ),
        (
            {"shout": "s1", "speaker": "Hana", "text": "Go!"},
            "--speaker cannot go with a background shout",
        ),
        (
            {"shout": "s1", "off_screen": True},
            "--off-screen/--on-screen cannot go with a background shout",
        ),
        ({"shout": "s1"}, "say what to change on the shout"),
        ({"shout": "s1", "text": "Charge!"}, f"nothing to change on {SHOUT_ID}"),
        ({"shout": "s9", "text": "Go!"}, "no background shout 's9' in episode 1"),
        ({"remove_shout": "s1", "text": "Go!"}, "--remove-shout drops the shout"),
        (
            {"line": SHOUT_ID, "text": "Go!"},
            "is a background shout, not a character's line",
        ),
        ({"remove": SHOUT_ID}, "is a background shout, not a character's line"),
    ],
)
def test_command_line_mistakes_stop_before_any_call(
    desk: Path, api: FakeApi, kwargs: dict[str, Any], stop: str
) -> None:
    _before_gate(api)
    with pytest.raises(ec.CommandStopped, match=stop):
        ec.run_line(desk, episode=1, out=io.StringIO(), **kwargs)
    assert _patches(api) == []


@pytest.mark.parametrize(
    ("details", "fix"),
    [
        (
            {"unknown_crowd_line_ids": ["crowd_x"]},
            "the story has no background shout crowd_x",
        ),
        (
            {"spoken_text_on_english_show_crowd_line_ids": [SHOUT_ID]},
            "this show is performed in English",
        ),
        (
            {
                "violations": [
                    {
                        "loc": "x",
                        "message": "crowd line c has 7 words; a background shout carries at most 6",
                    }
                ]
            },
            "a background shout carries at most 6 words",
        ),
    ],
)
def test_the_servers_named_shout_refusals_are_said_plainly(
    details: dict[str, Any], fix: str
) -> None:
    message = str(
        _refusal("invalid_patch", "The story spine patch is invalid.", details).code
    )

    explained = ec.explain_refusal(message, spine_fixture(), episode=1)

    assert f"fix: {fix}" in explained


def test_a_refusal_from_the_server_is_explained_and_nothing_syncs(
    desk: Path, api: FakeApi
) -> None:
    _before_gate(api)
    api.spine_doc["beats"][0]["crowd_lines"][0]["line_id"] = "crowd_renamed"

    def stale(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        patch = (body or {})["patch"]
        raise _refusal(
            "invalid_patch",
            "unknown ids",
            {"unknown_crowd_line_ids": [patch["crowd_lines"][0]["line_id"]]},
        )

    api.routes[("PATCH", "/v1/spines/sp1")] = stale
    with pytest.raises(
        ec.EditRefused, match="the story has no background shout crowd_renamed"
    ):
        ec.run_line(desk, episode=1, shout="s1", text="Go!", out=io.StringIO())


def test_the_cli_takes_shout_and_remove_shout(desk: Path, api: FakeApi) -> None:
    _before_gate(api)

    assert (
        produce_main(
            [
                "line",
                "--desk",
                str(desk),
                "--episode",
                "1",
                "--shout",
                "s2",
                "--text",
                "One more!",
            ]
        )
        == 0
    )
    assert (
        produce_main(
            ["line", "--desk", str(desk), "--episode", "1", "--remove-shout", SHOUT_ID]
        )
        == 0
    )

    assert _patches(api) == [
        {"crowd_lines": [{"line_id": "crowd_episode_01_01_2", "text": "One more!"}]},
        {"remove_crowd_line_ids": [SHOUT_ID]},
    ]


# --- after filming: the line check, captions and the soundtrack review -----------------------


def _shout_desk(desk: Path) -> None:
    """The desk's spine snapshot: episode 1 with the soldier's and the kids' shouts."""

    spine = _with_shouts(spine_fixture())
    spine["beats"][0]["crowd_lines"][0]["text"] = "Hold the line, men!"
    api_dir = desk / "ep01" / "api"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "spine.json").write_text(json.dumps(spine), encoding="utf-8")


SHOUT_FACTS = {
    "take_facts": {
        "lines": [
            {"line_id": "crowd_episode_01_01_2", "count": 1, "shot_index": 0},
            {"line_id": "line_episode_01_01", "count": 1, "shot_index": 0},
            {"line_id": "line_episode_01_02", "count": 1, "shot_index": 0},
            {"line_id": SHOUT_ID, "count": 1, "shot_index": 1},
        ]
    }
}


def test_the_line_check_hears_an_asked_shout_as_script_not_extra_speech(
    desk: Path,
) -> None:
    from creation.post import line_check as lc
    from creation.post.review import take_lines
    from creation.post.whisper import Word

    _shout_desk(desk)
    found = take_lines(desk, 1, "t1", shouts_asked=lc.shouts_asked_of(SHOUT_FACTS))
    assert found is not None
    rows, _language = found
    assert [row["line_id"] for row in rows] == [
        "crowd_episode_01_01_2",
        "line_episode_01_01",
        "line_episode_01_02",
        SHOUT_ID,
    ]
    assert rows[-1]["cast_id"] == f"crowd:{SHOUT_ID}"
    lines = lc.script_lines_for_take(
        rows, SHOUT_FACTS, cast_names={"cast_hana": "Hana", "cast_ren": "Ren"}
    )
    assert [line.speaker for line in lines] == [
        "Kids ×3, background",
        "Hana",
        "Ren",
        "Soldier, background",
    ]
    spoken = "again we're closed not for me hold the line men".split()
    words = [Word(i * 0.4, i * 0.4 + 0.3, w) for i, w in enumerate(spoken)]

    check = lc.check_take_lines(lines, words, take_id="t1")

    assert [f.kind for f in check.faults] == []


def test_without_the_shouts_the_same_take_would_stop_on_extra_speech(
    desk: Path,
) -> None:
    """Guard for the test above: the kit's old script (characters only) calls the shout invented speech."""

    from creation.post import line_check as lc
    from creation.post.review import take_lines
    from creation.post.whisper import Word

    _shout_desk(desk)
    found = take_lines(desk, 1, "t1")
    assert found is not None and [row["line_id"] for row in found[0]] == [
        "line_episode_01_01",
        "line_episode_01_02",
    ]
    lines = lc.script_lines_for_take(found[0], SHOUT_FACTS)
    spoken = "we're closed not for me hold the line men".split()
    words = [Word(i * 0.4, i * 0.4 + 0.3, w) for i, w in enumerate(spoken)]

    assert [f.kind for f in lc.check_take_lines(lines, words, take_id="t1").faults] == [
        "extra"
    ]


def test_a_shout_the_take_was_never_asked_for_is_not_expected(desk: Path) -> None:
    from creation.post import line_check as lc
    from creation.post.review import take_lines

    _shout_desk(desk)
    facts = {"take_facts": {"lines": [{"line_id": SHOUT_ID, "count": 0}]}}

    found = take_lines(desk, 1, "t1", shouts_asked=lc.shouts_asked_of(facts))

    assert found is not None and SHOUT_ID not in [row["line_id"] for row in found[0]]


def test_captions_carry_shouts_in_play_order() -> None:
    from creation.captions import episode_caption_lines

    lines = episode_caption_lines(
        _with_shouts(spine_fixture(spoken_language="ko-KR"), korean=True), 1
    )

    assert [line.text for line in lines] == [
        "Again!",
        "We're closed.",
        "Not for me.",
        "Charge!",
    ]
    assert lines[0].performed == "또!" and not lines[0].italic


def test_the_soundtrack_review_names_a_shout_as_background_never_a_cast_member() -> (
    None
):
    from creation.post.soundtrack import soundtrack_lines

    facts = {
        "take_facts": {
            "soundtrack": {
                "mode": "target_audio",
                "lines": [
                    {
                        "line_id": "line_episode_01_01",
                        "cast_id": "cast_hana",
                        "start_s": 0.2,
                        "end_s": 1.0,
                    },
                    {
                        "line_id": SHOUT_ID,
                        "cast_id": f"crowd:{SHOUT_ID}",
                        "start_s": 1.4,
                        "end_s": 1.9,
                    },
                ],
            }
        }
    }

    rows = soundtrack_lines(facts, cast_names={"cast_hana": "Hana"})

    assert any(f"{SHOUT_ID} (background shout) 1.40-1.90s" in row for row in rows)
    assert any("line_episode_01_01 (Hana)" in row for row in rows)


def test_check_lines_reports_shouts_apart_from_the_approved_lines(desk: Path) -> None:
    rows = ec.shout_check_rows(
        _with_shouts(spine_fixture()),
        {"lines": [{"line_id": SHOUT_ID, "count": 1, "shot_index": 2}]},
        episode=1,
        take_index=1,
        take_count=1,
        label="ep01 t1",
    )

    assert rows == [
        '[background] Soldier: "Charge!"'.join(["ep01 t1: ", " asked on shot 2"]),
        'ep01 t1: [background] Kids ×3: "Again!" was not in the take\'s instructions (a background shout; '
        "not counted above)",
    ]


def test_finishs_line_check_asks_for_the_shouts_its_take_facts_name(
    desk: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation.post import line_check as lc
    from creation.post import review

    asked: list[list[str]] = []

    def spy(_desk: Path, _episode: int, _take: str, *, shouts_asked: Any = ()) -> None:
        asked.append(list(shouts_asked))
        return None

    monkeypatch.setattr(review, "take_lines", spy)

    lc.desk_line_check(
        desk, episode=1, take_id="t1", take=desk / "t1.mp4", facts=SHOUT_FACTS
    )

    assert asked == [
        ["crowd_episode_01_01_2", "line_episode_01_01", "line_episode_01_02", SHOUT_ID]
    ]
