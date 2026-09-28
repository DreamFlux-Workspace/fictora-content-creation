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


def test_a_body_without_episode_ordinal_is_never_a_schema_refusal() -> None:
    # An older server can only refuse the field when it was sent (episode 1 on an
    # older deploy leaves it out).
    text = "HTTP 422 validation_error body.episode_ordinal extra input"
    assert server_refused_episode_ordinal_field(text, request_body={"episode_count": 1}) is False


def test_old_server_schema_refusal_is_detected() -> None:
    # The body carries the field: that is exactly when an older deploy refuses it
    # (050f555 short-circuited this case to False, so the refusal went out raw).
    text = "HTTP 422 validation_error body.episode_ordinal extra input"
    body = {"episode_count": 2, "episode_ordinal": 2}
    assert server_refused_episode_ordinal_field(text, request_body=body) is True
    assert server_refused_episode_ordinal_field("HTTP 422 validation_error extra input episode_ordinal", request_body=body)


def test_request_films_one_episode_reads_ordinal() -> None:
    assert request_films_one_episode({"episode_ordinal": 2}) == 2
