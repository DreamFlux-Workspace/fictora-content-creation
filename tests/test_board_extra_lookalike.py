"""The board gate prints the server's lookalike-extra warning (Iron Saint, 16 Oct 2026).

The unnamed spearman was drawn with Toma's topknot and a beard, so Ren's punch
on him read as a punch on Toma. The server (fictora-drama
``extras_apart_from_cast``) rewrites such a look before a new show's board is
drawn and returns what is still drawn alike as ``extra_lookalike_risks``.
"""

from __future__ import annotations

from creation.spine_view import extra_lookalike_lines

RISK = {
    "code": "extra_lookalike_risk",
    "severity": "warn",
    "frame_ordinals": [7, 8],
    "who": "tall spearman",
    "resembles": "Toma",
    "cast_id": "cast_toma",
    "traits": ["a topknot"],
    "fix_frame_ordinal": 7,
    "fix_field": "unnamed_figures.0.look",
    "fix_value": "tall thin adult man, loose shoulder-length dark hair and a straw rain cape; clean-shaven",
    "message": "The unnamed extra 'tall spearman' (frames 7, 8) is written with a topknot, like Toma.",
    "how_to_fix": "Give the extra a look of their own, then redraw the board before filming.",
}


def _exposure(*risks: dict) -> dict:
    return {
        "boards": [
            {
                "set_index": 1,
                "mean_percent": 30.0,
                "extra_lookalike_risks": list(risks),
            },
            {"set_index": 2, "mean_percent": 30.0},
        ]
    }


def test_one_line_per_extra_and_one_redraw_carrying_the_servers_look() -> None:
    lines = extra_lookalike_lines(_exposure(RISK), desk="desks/is", episode=1)

    assert lines == [
        "  !! t1 board: the unnamed extra 'tall spearman' (frames 7, 8) is written with a topknot, like Toma; "
        "a hit on them can read as a hit on Toma (warning only)",
        '     fictora-produce redraw-board --desk desks/is --episode 1 --take t1 --note "The unnamed tall spearman '
        'is not Toma: tall thin adult man, loose shoulder-length dark hair and a straw rain cape; clean-shaven."',
    ]


def test_two_extras_on_one_board_share_one_redraw() -> None:
    bald = {
        **RISK,
        "who": "stocky bandit",
        "resembles": "Ren Saeki",
        "traits": ["a shaved or bald head"],
    }
    lines = extra_lookalike_lines(_exposure(RISK, bald), desk="d", episode=1)

    assert [line.startswith("  !!") for line in lines] == [True, True, False]
    assert lines[2].count("redraw-board") == 1 and "not Ren Saeki" in lines[2]


def test_other_boards_an_old_server_and_odd_shapes_print_nothing() -> None:
    assert extra_lookalike_lines(_exposure(RISK), desk="d", episode=1, sets=[2]) == []
    assert extra_lookalike_lines(None, desk="d", episode=1) == []
    assert (
        extra_lookalike_lines({"boards": [{"set_index": 1}]}, desk="d", episode=1) == []
    )
    assert (
        extra_lookalike_lines(_exposure({"who": "x"}, "junk"), desk="d", episode=1)
        == []
    )
