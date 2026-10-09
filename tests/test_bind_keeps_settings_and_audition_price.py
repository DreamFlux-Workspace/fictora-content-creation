"""``bind`` keeps the desk's saved settings; auditions are quoted at what they cost.

Back from the Sea L-20261008-2: re-binding to save a new brief rebuilt
``production.config.json`` from bind's own flags, so the Korean language (and
voice mode, caption style, tempo, format) fell back to defaults and the show was
drafted in English. Gallery L-20261008-25 / Dead Heat: auditions were quoted at a
flat $0.30 a character and booked at about $0.015-0.035.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from creation import cli_produce
from creation.ops.floor import init_series_desk
from creation.prices import audition_quote, audition_usd
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)
from creation.production_state import ProductionState, ensure_production
from creation.voice_gate import speaking_voices


@pytest.fixture
def bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def bind(desk: Path, **kwargs: Any) -> ProductionState:
        seen.update(kwargs)
        return ProductionState(
            session_id="s", prompt="p", preset_id="x", preset_version="1"
        )

    monkeypatch.setattr(cli_produce, "bind_desk", bind)
    return seen


def _desk(tmp_path: Path) -> Path:
    desk = init_series_desk(tmp_path, "Back from the Sea", band="30s", episode_count=4)
    save_production_config(
        desk,
        ProductionConfig(
            clip_duration_seconds=10,
            cut_tempo="slow_burn",
            caption_style="plain",
            spoken_language="ko",
            delivery_format="letterbox",
            voice_mode="model",
            letterbox_caption_colour="white",
            lane="drama",
            account="@sea",
            posting_slot="18:30 IST",
            rules_epoch="2026-10-06",
            continuing_fixes_from_episode=3,
        ),
    )
    return desk


def test_rebinding_with_only_a_new_brief_keeps_every_saved_setting(
    tmp_path: Path, bound: dict[str, Any]
) -> None:
    desk = _desk(tmp_path)
    before = asdict(load_production_config(desk))

    assert (
        cli_produce.main(["bind", "--desk", str(desk), "--prompt", "A new brief."]) == 0
    )

    assert asdict(load_production_config(desk)) == before


def test_a_flag_given_to_bind_replaces_only_that_setting(
    tmp_path: Path, bound: dict[str, Any]
) -> None:
    desk = _desk(tmp_path)

    assert (
        cli_produce.main(
            [
                "bind",
                "--desk",
                str(desk),
                "--prompt",
                "A new brief.",
                "--language",
                "ja",
                "--cut-tempo",
                "punchy",
            ]
        )
        == 0
    )

    after = load_production_config(desk)
    assert (after.spoken_language, after.cut_tempo) == ("ja", "punchy")
    assert after.voice_mode == "model"
    assert after.caption_style == "plain"
    assert after.delivery_format == "letterbox"
    assert after.clip_duration_seconds == 10
    assert after.rules_epoch == "2026-10-06"
    assert after.continuing_fixes_from_episode == 3


def test_rebinding_keeps_the_desks_art_style_and_lane(
    tmp_path: Path, bound: dict[str, Any]
) -> None:
    desk = _desk(tmp_path)
    ensure_production(
        desk,
        prompt="Old brief.",
        preset_id="soft-watercolour",
        preset_version="3",
        video_lane="seedance",
    )

    assert (
        cli_produce.main(["bind", "--desk", str(desk), "--prompt", "A new brief."]) == 0
    )
    assert (bound["preset_id"], bound["video_lane"]) == ("soft-watercolour", "seedance")

    assert (
        cli_produce.main(
            [
                "bind",
                "--desk",
                str(desk),
                "--prompt",
                "Again.",
                "--preset-id",
                "noir-ink",
            ]
        )
        == 0
    )
    assert (bound["preset_id"], bound["video_lane"]) == ("noir-ink", "seedance")


def test_a_desk_never_bound_gets_the_old_defaults(
    tmp_path: Path, bound: dict[str, Any]
) -> None:
    desk = init_series_desk(tmp_path, "Fresh", band="15s", episode_count=1)

    assert cli_produce.main(["bind", "--desk", str(desk), "--prompt", "A door."]) == 0
    assert (bound["preset_id"], bound["video_lane"]) == (
        "modern-dark-fantasy",
        "minimax-h3",
    )


# --- Audition price (L-20261008-25) ----------------------------------------------------------------


def test_an_audition_is_priced_per_character_like_the_server_books_it() -> None:
    lines = ["Where were you?", "Don't lie to me.", "Fine."]
    chars = len(" ".join(lines))  # 38
    assert audition_usd(lines, 8) == (
        Decimal(chars) * Decimal("0.10") / 1000 * 8
    ).quantize(Decimal("0.001"))
    # Two characters' sets land near the ledger's ~$0.03-0.07, never the old $0.60.
    assert audition_usd(lines, 8) < Decimal("0.05")
    assert audition_quote(lines) == "about $0.03"


def test_with_no_lines_the_quote_prices_the_longest_audition() -> None:
    assert audition_usd([], 8) == Decimal("0.240")


def test_the_voices_gate_quotes_each_character_from_their_own_lines() -> None:
    spine = {
        "cast": [{"cast_id": "a", "name": "A"}, {"cast_id": "b", "name": "B"}],
        "beats": [
            {
                "dialogue_lines": [
                    {"cast_id": "a", "text": "Hi."},
                    {"cast_id": "b", "text": "x" * 120},
                ]
            },
            {"dialogue_lines": [{"cast_id": "a", "text": "Hi."}]},
        ],
    }
    a, b = speaking_voices(spine)
    assert a.lines == ("Hi.",)
    assert a.audition_price == "about $0.01"
    assert b.audition_price == "about $0.10"
