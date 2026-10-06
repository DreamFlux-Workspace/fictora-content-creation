"""Default take length of a letterbox desk follows the server (fictora-drama #617)."""

from __future__ import annotations

import argparse

import pytest

from creation.cli_config import default_clip_seconds


def _args(**kw: object) -> argparse.Namespace:
    base = {"clip_seconds": None, "delivery_format": None, "band": "30s"}
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.parametrize(("band", "seconds"), [("15s", 7), ("30s", 10), ("60s", 10)])
def test_a_letterbox_desk_films_the_servers_take_length(
    band: str, seconds: int
) -> None:
    assert (
        default_clip_seconds(_args(delivery_format="letterbox", band=band)) == seconds
    )


@pytest.mark.parametrize("band", ["15s", "30s", "60s"])
def test_a_portrait_desk_keeps_15_second_takes(band: str) -> None:
    """Guard: portrait is unchanged."""
    assert default_clip_seconds(_args(band=band)) == 15


def test_clip_seconds_given_wins() -> None:
    assert (
        default_clip_seconds(
            _args(delivery_format="letterbox", band="15s", clip_seconds=9)
        )
        == 9
    )
