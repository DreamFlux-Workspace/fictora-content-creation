"""Continuing desks get the "Group B" fixes from one episode on (founder decision, 7 Oct 2026).

A desk created before 6 Oct 2026 (legacy, :mod:`creation.rules_epoch`) gets
per-word caption timing (#135), captions without dashes (#137) and the pitch
card gate (#155) only from ``continuing_fixes_from_episode`` in its
``production.config.json``. Unset, nothing changes; earlier episodes keep
their original behaviour; the boundary episode gets the fix.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import make_take

from creation.captions import Span, build_line_cues
from creation.ops.floor import init_series_desk
from creation.pitch_card import pitch_gate_refusal
from creation.production_config import load_production_config
from creation.rules_epoch import (
    CONTINUING_FIXES,
    CONTINUING_FIXES_FROM_EPISODE,
    continuing_fix,
    continuing_fixes_from_episode,
    desk_rules,
    is_legacy,
    run_continuing_fixes,
)


def _old_desk(
    tmp_path: Path, *, start: int | None = None, name: str = "2026-09-26-old"
) -> Path:
    desk = tmp_path / name
    desk.mkdir()
    if start is not None:
        (desk / "production.config.json").write_text(
            json.dumps({"continuing_fixes_from_episode": start})
        )
    return desk


def test_the_allow_lists_are_group_a_and_group_b_only() -> None:
    # caption_dashes moved to Group A on 9 Oct 2026 (captions only), in step with the server.
    # pitch_card moved to Group A on 9 Oct 2026 (founder): a reminder on every continuing desk.
    # per_word_captions moved to Group A on 9 Oct 2026 (founder): Group B is empty in the kit.
    assert CONTINUING_FIXES == frozenset(
        {"seam_bed", "caption_dashes", "pitch_card", "per_word_captions"}
    )
    assert CONTINUING_FIXES_FROM_EPISODE == frozenset()


def test_continuing_fix_reads_the_desks_boundary_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from creation import rules_epoch

    # Group B is empty since 9 Oct 2026; the boundary logic stays for any fix added later.
    monkeypatch.setattr(
        rules_epoch, "CONTINUING_FIXES_FROM_EPISODE", frozenset({"a_later_fix"})
    )
    off = _old_desk(tmp_path, name="2026-09-26-off")
    on = _old_desk(tmp_path, start=3, name="2026-09-26-on")
    new = init_series_desk(tmp_path / "new", "New", band="30s", episode_count=1)
    assert is_legacy(on) and continuing_fixes_from_episode(on) == 3
    for name in rules_epoch.CONTINUING_FIXES_FROM_EPISODE:
        assert continuing_fix(new, name, episode=1) is True
        assert continuing_fix(off, name, episode=9) is False
        assert [continuing_fix(on, name, episode=n) for n in (1, 2, 3, 4)] == [
            False,
            False,
            True,
            True,
        ]
        assert continuing_fix(on, name) is False  # no episode: never on
    assert continuing_fix(on, "bold_captions", episode=9) is False  # not in Group B
    assert continuing_fix(off, "seam_bed") is True  # Group A unchanged


def test_dashes_go_on_every_episode_of_a_legacy_desk(tmp_path: Path) -> None:
    # Group A since 9 Oct 2026 (captions only): before the boundary, after it, and with Group B off.
    desk = _old_desk(tmp_path, start=2)
    dropped = ["Please…", "Please, no", "Please, no no…"]
    for episode in (None, 1, 2, 3):
        with desk_rules(desk, episode):
            (cues,) = build_line_cues(["Please— no no—"], [Span(0.0, 2.0)])
            assert [c.text for c in cues] == dropped, episode
    with desk_rules(_old_desk(tmp_path, name="2026-09-26-off"), 5):
        (cues,) = build_line_cues(["Please— no no—"], [Span(0.0, 2.0)])
        assert [c.text for c in cues] == dropped


def test_per_word_timing_on_every_episode_of_every_desk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``per_word_captions`` is Group A since 9 Oct 2026 (founder): Group B on, off, before or after."""

    from test_rules_epoch import SOURCES_SPINE, _words

    from creation import captions, rules_epoch

    seen: list[bool] = []
    real = captions.time_lines

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(bool(kwargs.get("per_word")))
        return real(*args, **kwargs)

    monkeypatch.setattr(captions, "time_lines", spy)
    # This machine's ffmpeg may lack libass; the burn after the timing may fail, the timing call is what counts.
    monkeypatch.setattr(captions, "find_ffmpeg", lambda: ("ffmpeg", "ffprobe"))

    def timed(name: str, start: int | None) -> bool:
        desk = _old_desk(tmp_path, start=start, name=name)
        (desk / "ep01" / "api").mkdir(parents=True)
        (desk / "ep01" / "api" / "03_spine.json").write_text(json.dumps(SOURCES_SPINE))
        make_take(
            desk / "ep01" / "takes" / "take-ep01-t1-raw-v1.mp4",
            tones=((0.3, 1.2, 440),),
        )
        try:
            captions.caption_take(
                desk, episode_ordinal=1, words_json=_words(tmp_path), style="house"
            )
        except (RuntimeError, ValueError):
            pass  # only the timing call matters here
        return seen[-1]

    for name, start in (
        ("2026-09-26-a", None),
        ("2026-09-26-b", 2),
        ("2026-09-26-c", 1),
    ):
        assert timed(name, start) is True, name
    for start, episode in ((2, 1), (2, 2), (None, 5)):
        desk = _old_desk(tmp_path, start=start, name=f"2026-09-26-d{start}{episode}")
        assert captions._per_word_desk(desk, episode) is True
    # Off the allow-list, a legacy desk spreads the words over the line again.
    monkeypatch.setattr(
        rules_epoch,
        "CONTINUING_FIXES",
        rules_epoch.CONTINUING_FIXES - {"per_word_captions"},
    )
    assert timed("2026-09-26-z", None) is False


def test_the_pitch_card_reminds_every_continuing_desk_on_every_episode_and_never_holds(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``pitch_card`` is Group A since 9 Oct 2026 (founder): before the boundary, after it, Group B off."""

    from creation import rules_epoch

    on = _old_desk(tmp_path, start=3)
    off = _old_desk(tmp_path, name="2026-09-26-off")
    for desk, episode in ((on, 2), (on, 3), (off, 1), (off, 3)):
        # Reminded, never held (founder decision 8 Oct 2026, #185).
        assert pitch_gate_refusal(desk, episode=episode, stage="the plates") is None
        err = capsys.readouterr().err
        assert (
            f"Reminder before the plates: Episode {episode} has no pitch card." in err
        )
        assert "Going ahead" in err
    # A new desk is still held.
    new = init_series_desk(tmp_path / "new", "New", band="30s", episode_count=1)
    refusal = pitch_gate_refusal(new, episode=1, stage="the plates")
    assert refusal is not None and refusal.startswith("Stopped before the plates.")
    # Off the allow-list, the old behaviour: no reminder before a desk's Group B episode, or with it unset.
    monkeypatch.setattr(
        rules_epoch, "CONTINUING_FIXES", rules_epoch.CONTINUING_FIXES - {"pitch_card"}
    )
    monkeypatch.setattr(
        rules_epoch,
        "CONTINUING_FIXES_FROM_EPISODE",
        rules_epoch.CONTINUING_FIXES_FROM_EPISODE | {"pitch_card"},
    )
    for desk, episode in ((on, 2), (off, 3)):
        assert pitch_gate_refusal(desk, episode=episode, stage="the plates") is None
        assert "Reminder" not in capsys.readouterr().err


def test_the_command_proposes_past_every_started_episode_and_writes_only_with_apply(
    tmp_path: Path,
) -> None:
    desk = _old_desk(tmp_path)
    for name in ("ep01/takes/a.mp4", "ep02/pitch-v1.json"):
        (desk / name).parent.mkdir(parents=True, exist_ok=True)
        (desk / name).write_text("x")
    (desk / "ep03").mkdir()  # an empty folder is not work
    out = io.StringIO()
    assert run_continuing_fixes(desk, out=out) == 3
    assert "Dry run: nothing written" in out.getvalue()
    assert continuing_fixes_from_episode(desk) is None
    # The server's later boundary wins; an earlier one never reopens a started episode.
    assert run_continuing_fixes(desk, from_episode=5, out=io.StringIO()) == 5
    assert run_continuing_fixes(desk, from_episode=1, out=io.StringIO()) == 3
    assert run_continuing_fixes(desk, apply=True, out=io.StringIO()) == 3
    assert load_production_config(desk).continuing_fixes_from_episode == 3
    assert load_production_config(desk).rules_epoch is None  # never rules-epoch --set
    assert is_legacy(desk)
    assert (
        run_continuing_fixes(desk, apply=True, out=io.StringIO()) is None
    )  # already on
    new = init_series_desk(tmp_path / "new", "New", band="30s", episode_count=1)
    assert run_continuing_fixes(new, apply=True, out=io.StringIO()) is None


def test_the_cli_runs_it_and_passes_the_commands_episode(tmp_path: Path) -> None:
    from creation import cli_produce

    desk = _old_desk(tmp_path)
    assert cli_produce.main(["continuing-fixes", "--desk", str(desk)]) == 0
    assert continuing_fixes_from_episode(desk) is None
    assert cli_produce.main(["continuing-fixes", "--desk", str(desk), "--apply"]) == 0
    assert continuing_fixes_from_episode(desk) == 1


def test_a_decorated_command_runs_under_its_episodes_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``under_desk_rules`` reads ``episode`` / ``episode_ordinal`` (run_finish, run_reel, caption_take)."""

    from creation import rules_epoch
    from creation.caption_dashes import drawn_text
    from creation.rules_epoch import continuing_fix_now, under_desk_rules

    # Group B is empty since 9 Oct 2026; a stand-in name checks the episode reading.
    monkeypatch.setattr(
        rules_epoch, "CONTINUING_FIXES_FROM_EPISODE", frozenset({"a_later_fix"})
    )

    @under_desk_rules
    def finish(desk: Path, *, episode: int = 1) -> bool:
        return continuing_fix_now("a_later_fix")

    @under_desk_rules
    def caption(desk: Path, *, episode_ordinal: int = 1) -> bool:
        return continuing_fix_now("a_later_fix")

    @under_desk_rules
    def dashes(desk: Path, *, episode: int = 1) -> str:
        return drawn_text("Please—")

    desk = _old_desk(tmp_path, start=2)
    assert [finish(desk, episode=1), finish(desk, episode=2), finish(desk)] == [
        False,
        True,
        False,
    ]
    assert [caption(desk, episode_ordinal=1), caption(desk, episode_ordinal=3)] == [
        False,
        True,
    ]
    # caption_dashes is Group A since 9 Oct 2026: every episode, Group B on or off.
    assert [dashes(desk, episode=1), dashes(desk, episode=2)] == ["Please…", "Please…"]
    assert dashes(_old_desk(tmp_path, name="2026-09-26-nob"), episode=1) == "Please…"
