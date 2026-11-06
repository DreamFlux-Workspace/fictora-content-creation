"""`approve --gate pitch` on fictora-ops names fictora-produce; `--gate post` says there is no final step.

A gate sent to the wrong command must not come back as argparse's bare "invalid choice".
The pitch yes is fictora-produce's, and fictora-ops says so with the line to run. `--gate post`
on either command says the newest copy is the one to post and records nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from creation import cli_ops, cli_produce


@pytest.mark.parametrize("command", [cli_produce.main, cli_ops.main])
def test_the_post_gate_says_there_is_no_final_step_and_records_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: Callable[[list[str]], int],
) -> None:
    desk = tmp_path / "long-shadow"
    desk.mkdir()

    code = command(["approve", "--desk", str(desk), "--gate", "post", "--episode", "2"])

    out = capsys.readouterr().out
    assert code == 0
    assert 'no "this episode is final" step' in out and "Nothing was recorded" in out
    assert list(desk.iterdir()) == []


def test_ops_points_the_pitch_gate_to_fictora_produce(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_ops.main(
        ["approve", "--desk", str(tmp_path), "--gate", "pitch", "--episode", "3"]
    )

    err = capsys.readouterr().err
    assert code == 2
    assert f"fictora-produce approve --desk {tmp_path} --gate pitch --episode 3" in err
    assert list(tmp_path.iterdir()) == []


def test_ops_pitch_pointer_pastes_as_is_without_an_episode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_ops.main(["approve", "--desk", str(tmp_path), "--gate", "pitch"])

    err = capsys.readouterr().err
    assert code == 2
    line = next(
        text.strip()
        for text in err.splitlines()
        if text.strip().startswith("fictora-produce")
    )
    assert line == f"fictora-produce approve --desk {tmp_path} --gate pitch"


@pytest.mark.parametrize(
    ("command", "gates"),
    [
        (cli_ops.main, "{look,plates,script,board}"),
        (cli_produce.main, "{pitch,look,plates,script,board}"),
    ],
)
def test_approve_help_offers_neither_the_post_gate_nor_the_other_commands_gate(
    capsys: pytest.CaptureFixture[str], command: Callable[[list[str]], int], gates: str
) -> None:
    with pytest.raises(SystemExit):
        command(["approve", "--help"])
    out = capsys.readouterr().out
    assert gates in out and "post" not in out
