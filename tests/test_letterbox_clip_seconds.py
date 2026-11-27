"""Default take length: 15 s for every new show; an older 4:3 show's shorter takes come from its spine."""

from __future__ import annotations

import argparse

import pytest

from creation.cli_config import default_clip_seconds


def _args(**kw: object) -> argparse.Namespace:
    base = {"clip_seconds": None, "delivery_format": None, "band": "30s"}
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.parametrize("band", ["15s", "30s", "60s"])
@pytest.mark.parametrize("delivery", [None, "portrait", "letterbox", "landscape"])
def test_a_new_desk_films_15_second_takes(band: str, delivery: str | None) -> None:
    assert default_clip_seconds(_args(delivery_format=delivery, band=band)) == 15


def test_clip_seconds_given_wins() -> None:
    assert (
        default_clip_seconds(
            _args(delivery_format="letterbox", band="15s", clip_seconds=9)
        )
        == 9
    )
