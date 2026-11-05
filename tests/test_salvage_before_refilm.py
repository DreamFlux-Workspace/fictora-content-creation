"""Salvage before re-filming: free edits first (trim, tempo, zoom), a reuse list, and the
re-film price naming the free edit (Gallery Heiress review, 12 Oct 2026)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import re

import pytest

from creation.cli_produce import main
from creation.post import salvage
from creation.post.salvage import (
    FREE_FIXES,
    free_fix_hints,
    free_fix_lines,
    keep_for_reuse,
    needs_refilm,
    refilm_cause_lines,
    reuse_entries,
    reuse_lines,
)

# --- the free-fix rules -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flaw", "edit"),
    [
        ("same person rendered twice at shot 3", "trim --cut"),
        ("the male MC looked at himself for half a second", "trim --cut"),
        ("jump cut as the stranger enters the doorway", "zoom"),
        ("a stray hand in the corner", "zoom"),
    ],
)
def test_flaws_the_review_saved_for_free_point_to_a_free_edit(
    flaw: str, edit: str
) -> None:
    hints = free_fix_hints(flaw)
    assert hints and any(edit in hint for hint in hints), hints
    assert not needs_refilm(flaw)


@pytest.mark.parametrize(
    "flaw",
    [
        "a blood-looking smear across her face",
        "face melts in shot 2",
        "wrong character for the whole take",
    ],
)
def test_a_smear_on_a_face_or_the_wrong_person_throughout_goes_straight_to_a_refilm(
    flaw: str,
) -> None:
    assert needs_refilm(flaw)
    assert free_fix_hints(flaw) == [] and refilm_cause_lines(flaw) == []


def test_the_free_fix_block_names_every_free_edit_the_short_flaw_rule_and_the_reuse_list() -> (
    None
):
    text = "\n".join(free_fix_lines("D", 2, "t1", take_file="F.mp4"))
    for command in ("trim --desk D --episode 2 --take t1 --cut", "trim --desk D --episode 2 --take t1 --start",
                    "tempo --desk D --episode 2 --take t1", "zoom --desk D --episode 2 --take t1",
                    "keep-take --desk D --episode 2 --take t1"):  # fmt: skip
        assert command in text, command
    assert "0.5 s or less is a cut, never a re-film" in text
    assert "smear or melt on a face" in text
    for word in ("$", "model", "fal", "seedance", "h3"):
        assert word not in text.lower().replace("model voices", ""), word


REFUSED = re.compile(r"--take-file|\bjoin\b|\bdeboard\b|--hold-unsure")
WRONG_ORDER = "scenes out of order, the takes are swapped"


def _every_salvage_line() -> list[str]:
    lines = [
        fix.command.format(desk="D", episode=2, take="t1", file="F.mp4")
        for fix in FREE_FIXES
    ]
    lines += [hint for _, hint in salvage._FREE_HINTS]
    lines += free_fix_lines("D", 2, "t1", take_file="F.mp4")
    lines += free_fix_lines()
    for cause in (
        WRONG_ORDER,
        "wrong order",
        "double at shot 2",
        "a pop",
        "the ring is late",
    ):
        lines += free_fix_hints(cause) + refilm_cause_lines(cause)
    return lines


def test_no_salvage_hint_names_a_command_the_cli_refuses() -> None:
    for line in _every_salvage_line():
        assert not REFUSED.search(line), line


def test_wrong_order_says_there_is_no_free_edit_and_names_who_fixes_it() -> None:
    text = " ".join(free_fix_hints(WRONG_ORDER))
    assert "no free edit" in text and "film that take again" in text
    assert "core team" in text


def test_no_printed_hint_in_the_kit_source_advises_a_refused_command() -> None:
    root = Path(__file__).resolve().parents[1] / "creation" / "post"
    forbidden = re.compile(
        r"--hold-unsure`|run deboard|finish --take-file|`join --take-file|tempo --desk \{desk\} --take-file"
    )
    for name in ("salvage.py", "review.py", "finish.py", "take_handles.py"):
        for number, line in enumerate((root / name).read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            assert not forbidden.search(line), f"{name}:{number}: {line.strip()}"


# --- the re-film price names the free edit first -----------------------------------------------------------------


def test_refilm_price_names_the_free_edit_when_the_cause_reads_like_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = refilm_cause_lines("same person rendered twice at shot 2")
    assert lines[0].startswith("!! salvage first") and any(
        "trim --cut" in line for line in lines
    )
    plain = refilm_cause_lines(
        "the ring leaves her hand before the line: move the drop after 'Not for me'"
    )
    assert len(plain) == 1 and plain[0].startswith("Salvage first")


# --- the reuse list ---------------------------------------------------------------------------------------------


def test_keep_take_adds_a_decent_rejected_take_to_the_reuse_list(
    post_desk: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    takes = post_desk / "ep01" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    (takes / "take-ep01-t1-raw-v1.mp4").write_bytes(b"mp4")
    (takes / "take-ep01-t1-raw-v2.mp4").write_bytes(b"mp4")

    assert main(["keep-take", "--desk", str(post_desk), "--episode", "1", "--take", "t1",
                 "--note", "decent doorway wide; could be a flashback later"]) == 0  # fmt: skip
    said = capsys.readouterr().out
    assert "Kept for reuse: ep01 t1 `ep01/takes/take-ep01-t1-raw-v2.mp4`" in said, said
    assert (
        reuse_entries(post_desk)[0]["note"]
        == "decent doorway wide; could be a flashback later"
    )

    assert main(["keep-take", "--desk", str(post_desk), "--list"]) == 0
    assert "Takes kept for reuse (1):" in capsys.readouterr().out


def test_keep_take_needs_a_note_and_an_empty_desk_says_so(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--note"):
        keep_for_reuse(tmp_path, episode=1, take_id="t1", note="  ")
    assert reuse_lines(tmp_path) == ["No takes kept for reuse on this desk yet."]
    entry = keep_for_reuse(tmp_path, episode=3, take_id="t2", note="good reaction shot",
                           now=datetime(2026, 10, 12, tzinfo=UTC))  # fmt: skip
    assert entry == {"episode": 3, "take": "t2", "file": None, "note": "good reaction shot",
                     "kept_at": "2026-10-12T00:00:00+00:00"}  # fmt: skip
