"""Tests for video enrol error classification."""

from __future__ import annotations

from creation.harness.video_enrol_errors import (
    error_text_mentions_episode_ordinal_field,
    request_films_one_episode,
    server_refused_episode_ordinal_field,
)


def test_episode_ordinals_detail_does_not_count_as_episode_ordinal_field() -> None:
    text = 'boards_not_approved_for_generation {"episode_ordinals": [1]}'
    assert error_text_mentions_episode_ordinal_field(text) is False


def test_boards_refusal_is_not_old_server_schema_error() -> None:
    text = 'HTTP 422 boards_not_approved_for_generation {"episode_ordinals": [1]}'
    body = {"episode_count": 2, "episode_ordinal": 2}
    assert server_refused_episode_ordinal_field(text, request_body=body) is False


def test_body_with_episode_ordinal_never_counts_as_schema_refusal() -> None:
    text = 'HTTP 422 validation_error extra input episode_ordinal'
    body = {"episode_count": 2, "episode_ordinal": 2}
    assert server_refused_episode_ordinal_field(text, request_body=body) is False


def test_old_server_schema_refusal_is_detected() -> None:
    text = "HTTP 422 validation_error body.episode_ordinal extra input"
    assert server_refused_episode_ordinal_field(text, request_body={"episode_count": 2}) is True


def test_request_films_one_episode_reads_ordinal() -> None:
    assert request_films_one_episode({"episode_ordinal": 2}) == 2
