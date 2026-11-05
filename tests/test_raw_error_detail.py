"""A failed job prints its plain message and code; the stored words only with FICTORA_SHOW_RAW_ERRORS=1."""

from __future__ import annotations

import pytest

from creation.harness.http_util import describe_job_error
from creation.harness.raw_error_detail import SHOW_RAW_ERRORS_ENV

RAW = "ValueError: music swap cut sha256 3f9a does not match the finishing report's cut sha256 0d1e"
FAILED_JOB = {
    "status": "failed",
    "error": {
        "code": "episode_assembly_failed",
        "message": "Your takes are safe. We couldn't put the episode together; try Finish again.",
        "retryable": False,
        "details": {"finished_take_job_ids": ["job_take_1"]},
        "internal_detail": RAW,
    },
}


def test_the_operator_reads_the_plain_message_and_code_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SHOW_RAW_ERRORS_ENV, raising=False)

    text = describe_job_error(FAILED_JOB)

    assert text.startswith(
        "failed episode_assembly_failed: Your takes are safe. We couldn't put the episode together; try Finish again."
    )
    assert RAW not in text
    assert f"set {SHOW_RAW_ERRORS_ENV}=1" in text


def test_the_switch_prints_the_stored_words(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SHOW_RAW_ERRORS_ENV, "1")

    assert f"Raw detail: {RAW}" in describe_job_error(FAILED_JOB)


def test_an_error_without_stored_words_prints_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SHOW_RAW_ERRORS_ENV, "1")
    job = {
        "status": "failed",
        "error": {
            "code": "provider_no_credit",
            "message": "Filming is paused on our side.",
        },
    }

    assert (
        describe_job_error(job)
        == "failed provider_no_credit: Filming is paused on our side."
    )
