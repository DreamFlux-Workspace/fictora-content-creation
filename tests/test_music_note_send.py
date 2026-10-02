"""``music-note`` sends the note to the harness: a plan first, applied only on ``--yes``.

- Without ``--yes`` only a ``dry_run`` goes out; the plan is printed in plain words and nothing is spent.
- ``--yes`` applies with a stable ``Idempotency-Key`` built from the note's saved id; the answer is written back
  onto ``shared/music-notes.jsonl`` and the spend is booked; the re-mixed episode's re-finish is followed.
- Takes whose music the video model made are filmed again only with ``--confirm-refilm`` (and a verified price).
- ``--send-saved`` sends saved notes not yet applied; ``--revert N`` puts a version back. An applied note is
  never sent twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fake_api import FakeApi

from creation.cli_produce import main
from creation.ops.state import load_series
from creation.post import music_send
from creation.post.bed import music_note_entries

ROUTE = "/v1/spines/sp1/music-notes"
FACTS = "/v1/jobs/job_take_t1/take-facts"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(music_send, "_sleep", lambda _s: None)


def _take(action: str, **extra: Any) -> dict[str, Any]:
    return {"video_job_id": "job_video_1", "take_index": 1, "take_job_id": "job_take_t1",
            "episode_id": "episode_01", "episode_ordinal": 1, "take": 1, "action": action, **extra}  # fmt: skip


def _answer(
    body: dict[str, Any],
    *,
    kind: str = "new_music",
    takes: list[dict[str, Any]] | None = None,
    refilm_usd: float | None = None,
    blocked: bool = False,
) -> dict[str, Any]:
    dry = bool(body.get("dry_run"))
    takes = takes if takes is not None else [_take("remix")]
    refilms = [t for t in takes if t["action"] == "refilm"]
    applied = not dry and not blocked
    new_bed = 0.20 if kind == "new_music" else 0.0
    episodes = []
    if applied and any(t["action"] == "remix" for t in takes):
        episodes = [{"video_job_id": "job_video_1", "episode_id": "episode_01", "rejoined": False,
                     "refinish": {"state": "queued", "episode_id": "episode_01",
                                  "updated_at": "2026-10-02T00:00:00Z"}}]  # fmt: skip
    return {
        "spine_id": "sp1", "spine_version": "1.0.0", "kind": kind,
        "status": "blocked" if blocked else ("planned" if dry else "applied"),
        "message": "planned" if dry else "done",
        "direction": "soft piano" if kind == "new_music" else None,
        "version": {"version": body.get("revert_to_version") or 2, "kind": "new_music", "note": body.get("note"),
                    "based_on": 1},
        "takes": [{**t, "remixed": applied and t["action"] == "remix"} for t in takes],
        "episodes": episodes,
        "cost": {"new_bed_usd": new_bed, "refilm_usd": refilm_usd, "refilm_take_count": len(refilms),
                 "spent_usd": new_bed if applied else 0.0},
        "refilm_needs_confirmation": bool(refilms) and not body.get("confirm_refilm"),
        "job_id": None if dry else "job_audio_1",
    }  # fmt: skip


def _route(api: FakeApi, **kw: Any) -> None:
    api.routes[("POST", ROUTE)] = lambda _m, _p, body: _answer(body, **kw)
    api.routes[("GET", FACTS)] = {
        "take_facts": {"refinish": {"state": "done", "episode_id": "episode_01",
                                    "video_url": "https://media.test/ep01-new.mp4"}}
    }  # fmt: skip


def _posts(api: FakeApi) -> list[tuple[dict[str, Any], str | None]]:
    return [
        (body or {}, key)
        for method, path, body, key in api.calls
        if method == "POST" and path == ROUTE
    ]


def _note(desk: Path, *extra: str) -> int:
    return main(["music-note", "--desk", str(desk), *extra])


def test_without_yes_only_the_plan_goes_out_and_nothing_is_spent(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _route(api)

    assert _note(desk, "calmer, softer piano") == 0

    posts = _posts(api)
    assert [body["dry_run"] for body, _ in posts] == [True]
    assert posts[0][0] == {
        "note": "calmer, softer piano",
        "dry_run": True,
        "confirm_refilm": False,
    }
    text = capsys.readouterr().out
    assert "new music: one new bed for the show, $0.20" in text
    assert "ep01 t1: re-mixed onto its voices, free, no filming" in text
    assert "Not applied: nothing was changed or spent. Applying costs $0.20" in text
    assert "--yes" in text
    assert load_series(desk).spend_usd == 0
    [entry] = music_note_entries(desk)
    assert entry["status"] == "planned" and "job_id" not in entry


def test_yes_applies_saves_the_answer_books_the_bed_and_follows_the_refinish(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _route(api)

    assert _note(desk, "--episode", "1", "--take", "t1", "calmer", "--yes") == 0

    posts = _posts(api)
    assert [body["dry_run"] for body, _ in posts] == [True, False]
    applied, key = posts[1]
    assert applied == {"note": "calmer", "episode_ordinal": 1, "take": "t1",
                       "dry_run": False, "confirm_refilm": False}  # fmt: skip
    [entry] = music_note_entries(desk)
    assert key == f"pfx-music-note-{entry['id']}-apply-1"
    assert entry["status"] == "applied" and entry["version"] == 2
    assert entry["job_id"] == "job_audio_1" and entry["spent_usd"] == pytest.approx(
        0.20
    )
    assert (desk / entry["response"]).is_file()
    assert load_series(desk).spend_usd == pytest.approx(0.20)
    text = capsys.readouterr().out
    assert "Applied: done" in text
    assert "ep01: the delivered cut changed" in text
    assert "https://media.test/ep01-new.mp4" in text
    assert ("GET", FACTS + "?spine_id=sp1", None, None) in api.calls


def test_an_applied_note_is_never_sent_twice(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _route(api)
    assert _note(desk, "calmer", "--yes") == 0
    sent = len(_posts(api))

    assert _note(desk, "calmer", "--yes") == 0

    assert len(_posts(api)) == sent + 2, (
        "a new note with the same words is a new change"
    )
    assert len(music_note_entries(desk)) == 2
    first = music_note_entries(desk)[0]
    capsys.readouterr()
    assert music_send.send_entry(desk, first, yes=True, confirm_refilm=False) == 0
    assert len(_posts(api)) == sent + 2
    assert "never sent twice" in capsys.readouterr().out


def test_the_apply_key_is_stable_when_a_send_is_retried(
    desk: Path, api: FakeApi
) -> None:
    calls = {"n": 0}

    def flaky(_m: str, _p: str, body: dict[str, Any]) -> Any:
        if not body["dry_run"]:
            calls["n"] += 1
            if calls["n"] == 1:
                return SystemExit(
                    "HTTP 503 POST https://api.test/v1/spines/sp1/music-notes: try again"
                )
        return _answer(body)

    api.routes[("POST", ROUTE)] = flaky
    api.routes[("GET", FACTS)] = {
        "take_facts": {"refinish": {"state": "done", "episode_id": "episode_01"}}
    }

    assert _note(desk, "calmer", "--yes") == 2
    assert _note(desk, "calmer", "--yes") == 0

    keys = [key for body, key in _posts(api) if not body["dry_run"]]
    assert len(keys) == 2 and keys[0] == keys[1], "a retried note is the same request"
    [entry] = music_note_entries(desk)
    assert entry["status"] == "applied"


def test_refilm_needs_confirm_refilm_and_shows_the_price(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _route(
        api,
        takes=[_take("remix"), _take("refilm", take=2, take_index=2, refilm_usd=1.4)],
        refilm_usd=1.4,
    )

    assert _note(desk, "calmer") == 0
    text = capsys.readouterr().out
    assert "only filming it again changes it, $1.40" in text
    assert "add --confirm-refilm" in text

    assert _note(desk, "calmer", "--yes") == 0
    assert _posts(api)[-1][0]["confirm_refilm"] is False
    assert "keep their old music" in capsys.readouterr().out

    assert _note(desk, "slower", "--yes", "--confirm-refilm") == 0
    body, key = _posts(api)[-1]
    assert body["confirm_refilm"] is True and body["dry_run"] is False
    assert key is not None and key.endswith("-refilm")
    assert "filming 1 take(s) again $1.40 (confirmed)" in capsys.readouterr().out


def test_a_refilm_without_a_verified_price_is_not_sent(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _route(api, takes=[_take("refilm")], refilm_usd=None)

    assert _note(desk, "calmer", "--yes", "--confirm-refilm") == 2

    assert [body["dry_run"] for body, _ in _posts(api)] == [True]
    assert "no verified price" in capsys.readouterr().out


def test_a_blocked_plan_is_not_applied(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _route(api, blocked=True)

    assert _note(desk, "calmer", "--yes") == 2

    assert [body["dry_run"] for body, _ in _posts(api)] == [True]
    assert music_note_entries(desk)[0]["status"] == "blocked"
    assert "Blocked" in capsys.readouterr().out


def test_send_saved_sends_saved_notes_not_yet_applied(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _route(api, kind="level_only")
    assert _note(desk, "quieter under the lines", "--save-only") == 0
    assert _note(desk, "--episode", "1", "softer", "--save-only") == 0
    assert _posts(api) == [], "--save-only sends nothing"

    assert _note(desk, "--send-saved") == 0
    assert [body["dry_run"] for body, _ in _posts(api)] == [True, True]
    assert (
        "level only: the same music re-mixed at a new level. Free"
        in capsys.readouterr().out
    )

    assert _note(desk, "--send-saved", "--yes") == 0
    applied = [body for body, _ in _posts(api) if not body["dry_run"]]
    assert [b["note"] for b in applied] == ["quieter under the lines", "softer"]
    assert all(e["status"] == "applied" for e in music_note_entries(desk))
    assert load_series(desk).spend_usd == 0, "level notes are free"

    assert _note(desk, "--send-saved", "--yes") == 0
    assert "No saved music notes waiting" in capsys.readouterr().out


def test_revert_puts_a_version_back(
    desk: Path, api: FakeApi, capsys: pytest.CaptureFixture[str]
) -> None:
    _route(api, kind="revert", takes=[])

    assert _note(desk, "--revert", "1") == 0
    assert _note(desk, "--revert", "1", "--yes") == 0

    bodies = [body for body, _ in _posts(api)]
    plan = {"revert_to_version": 1, "dry_run": True, "confirm_refilm": False}
    assert bodies == [plan, plan, {**plan, "dry_run": False}]
    [entry] = music_note_entries(desk)
    assert entry["revert_to_version"] == 1 and entry["status"] == "applied"
    assert "revert: the earlier music v1 is put back. Free" in capsys.readouterr().out


def test_wrong_flag_mixes_are_refused_before_anything_is_sent(
    desk: Path, api: FakeApi
) -> None:
    assert _note(desk) == 2
    assert _note(desk, "calmer", "--revert", "1") == 2
    assert _note(desk, "--take", "t1", "calmer") == 2
    assert _posts(api) == []
    assert music_note_entries(desk) == []
