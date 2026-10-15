"""`approve --gate post` on fictora-produce (and `--gate pitch` on fictora-ops) names the right command.

Long Shadow (14 Oct 2026, L-20261014-18): `approve --gate post` came back as
argparse's bare "invalid choice". The episode-is-final yes is fictora-ops's;
the pitch yes is fictora-produce's. Each says so, with the line to run, and
records nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from creation import cli_ops, cli_produce


def test_produce_points_the_post_gate_to_fictora_ops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    desk = tmp_path / "long-shadow"
    desk.mkdir()

    code = cli_produce.main(
        [
            "approve",
            "--desk",
            str(desk),
            "--gate",
            "post",
            "--episode",
            "2",
            "--path",
            "ep.mp4",
        ]
    )

    err = capsys.readouterr().err
    assert code == 2
    assert "Nothing was recorded" in err
    assert (
        f"fictora-ops approve --desk {desk} --gate post --episode 2 --path ep.mp4"
        in err
    )
    assert list(desk.iterdir()) == []


def test_produce_post_without_an_episode_shows_where_it_goes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli_produce.main(["approve", "--desk", str(tmp_path), "--gate", "post"])

    err = capsys.readouterr().err
    assert code == 2
    assert f"fictora-ops approve --desk {tmp_path} --gate post --episode N" in err
    assert "N = the episode number" in err
    assert "--path FILE" in err


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


def test_ops_help_does_not_offer_the_pitch_gate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli_ops.main(["approve", "--help"])
    out = capsys.readouterr().out
    assert "{look,plates,script,board,post}" in out and "pitch" not in out
