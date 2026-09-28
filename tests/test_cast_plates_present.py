"""Cast enrol recovery helpers."""

from __future__ import annotations

from creation.harness.stages_gated import _cast_plates_present


def test_cast_plates_present_from_media_assets() -> None:
    spine = {
        "cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_b"}],
        "media_assets": [
            {
                "relation_type": "cast_card",
                "relation_id": "cast_a",
                "url": "https://x/a.png",
                "stale": False,
            },
            {
                "relation_type": "cast_card",
                "relation_id": "cast_b",
                "url": "https://x/b.png",
                "stale": False,
            },
        ],
    }
    assert _cast_plates_present(spine) is True


def test_cast_plates_present_false_when_one_missing() -> None:
    spine = {
        "cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_b"}],
        "media_assets": [
            {
                "relation_type": "cast_card",
                "relation_id": "cast_a",
                "url": "https://x/a.png",
                "stale": False,
            },
        ],
    }
    assert _cast_plates_present(spine) is False


def test_voice_only_cast_is_not_waited_on() -> None:
    """fictora-drama #453: a character the story only hears gets no plate.

    The server flags them ``voice_only: true``; the recovery check must not
    wait forever for a plate that is never drawn.
    """

    spine = {
        "cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_voice", "voice_only": True}],
        "media_assets": [
            {
                "relation_type": "cast_card",
                "relation_id": "cast_a",
                "url": "https://x/a.png",
                "stale": False,
            },
        ],
    }
    assert _cast_plates_present(spine) is True


def test_voice_only_flag_false_or_absent_still_owes_a_plate() -> None:
    spine = {
        "cast": [{"cast_id": "cast_a"}, {"cast_id": "cast_b", "voice_only": False}],
        "media_assets": [
            {
                "relation_type": "cast_card",
                "relation_id": "cast_a",
                "url": "https://x/a.png",
                "stale": False,
            },
        ],
    }
    assert _cast_plates_present(spine) is False


def test_an_empty_cast_still_has_no_plates() -> None:
    assert _cast_plates_present({"cast": [], "media_assets": []}) is False
