"""Cast enrol recovery helpers."""

from __future__ import annotations

from creation.harness.stages_gated import _cast_plates_present


def test_cast_plates_present_from_media_assets() -> None:
    spine = {
        "cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_b"}],
        "media_assets": [
            {"relation_type": "cast_card", "relation_id": "cast_a", "url": "https://x/a.png", "stale": False},
            {"relation_type": "cast_card", "relation_id": "cast_b", "url": "https://x/b.png", "stale": False},
        ],
    }
    assert _cast_plates_present(spine) is True


def test_cast_plates_present_false_when_one_missing() -> None:
    spine = {
        "cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_b"}],
        "media_assets": [
            {"relation_type": "cast_card", "relation_id": "cast_a", "url": "https://x/a.png", "stale": False},
        ],
    }
    assert _cast_plates_present(spine) is False
