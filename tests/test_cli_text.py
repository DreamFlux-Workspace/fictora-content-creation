"""Text-or-file flags: ``--lines-json``, ``--description``, ``--prompt``, ``--music`` take the words or a file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from creation.cli_ops import main as ops_main
from creation.cli_text import TextArgError, json_or_file, text_or_file
from creation.ops.state import episode_by_ordinal, load_series

LINES = [{"speaker": "Hana", "original": "Not tonight.", "translation": "Not tonight."}]


def test_inline_text_is_the_text(tmp_path: Path) -> None:
    assert text_or_file("  a quiet tram at night  ", flag="--prompt") == "a quiet tram at night"


def test_an_at_file_and_an_existing_path_are_read(tmp_path: Path) -> None:
    path = tmp_path / "premise.txt"
    path.write_text("\nA quiet tram.\n", encoding="utf-8")

    assert text_or_file(f"@{path}", flag="--prompt") == "A quiet tram."
    assert text_or_file(str(path), flag="--prompt") == "A quiet tram."


def test_a_missing_at_file_is_an_error_not_text(tmp_path: Path) -> None:
    with pytest.raises(TextArgError, match="cannot read"):
        text_or_file(f"@{tmp_path / 'nope.json'}", flag="--lines-json")


def test_json_takes_text_at_file_or_path(tmp_path: Path) -> None:
    path = tmp_path / "lines.json"
    path.write_text(json.dumps(LINES), encoding="utf-8")

    assert json_or_file(json.dumps(LINES), flag="--lines-json") == LINES
    assert json_or_file(f"@{path}", flag="--lines-json") == LINES
    assert json_or_file(str(path), flag="--lines-json") == LINES


def test_a_path_that_does_not_exist_says_so_instead_of_a_bare_json_error(tmp_path: Path) -> None:
    with pytest.raises(TextArgError, match="no file named .*lines.json.* exists"):
        json_or_file(str(tmp_path / "lines.json"), flag="--lines-json")


@pytest.mark.parametrize("form", ["inline", "at-file", "path"])
def test_set_lines_accepts_the_json_or_a_file(desk: Path, tmp_path: Path, form: str) -> None:
    path = tmp_path / "lines.json"
    path.write_text(json.dumps(LINES), encoding="utf-8")
    value = {"inline": json.dumps(LINES), "at-file": f"@{path}", "path": str(path)}[form]

    code = ops_main(["set-lines", "--desk", str(desk), "--episode", "1", "--take", "t1", "--lines-json", value])

    assert code == 0
    take = episode_by_ordinal(load_series(desk), 1).takes[0]
    assert [line.original for line in take.lines] == ["Not tonight."]


def test_set_lines_with_a_missing_file_exits_2_with_a_plain_message(
    desk: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = ops_main(
        ["set-lines", "--desk", str(desk), "--episode", "1", "--take", "t1", "--lines-json", str(tmp_path / "x.json")]
    )

    assert code == 2 and "no file named" in capsys.readouterr().err


def test_bind_reads_the_premise_from_a_file(desk: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from creation import cli_produce

    seen: list[str] = []

    class _State:
        session_id = "s1"
        phase = "ready_draft"

    def fake_bind(desk_path: Path, *, prompt: str, **_: object) -> _State:
        seen.append(prompt)
        return _State()

    monkeypatch.setattr(cli_produce, "bind_desk", fake_bind)
    path = tmp_path / "premise.txt"
    path.write_text("A shop at closing time, rain on the shutters.\n", encoding="utf-8")

    assert cli_produce.main(["bind", "--desk", str(desk), "--prompt", f"@{path}"]) == 0
    assert seen == ["A shop at closing time, rain on the shutters."]
