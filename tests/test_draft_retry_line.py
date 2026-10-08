"""A failed draft says why and whether to send it again; a server redraft is never retried twice.

L-20261001-12 (seen three times, latest Beach Court on 5 Oct): a script draft
failed with ``authoring_invalid_output`` and a plain manual retry worked. The
server now writes such a draft once more on its own and says so on the job
error (``details.auto_retried``); the kit says so and does not retry it again.
"""

from __future__ import annotations

from typing import Any

from creation.harness.http_util import (
    DRAFT_RETRY_COST,
    describe_job_error,
    draft_retry_line,
    server_already_retried,
)
from creation.harness.stages_gated import _retryable_stall


def _failed(**details: Any) -> dict[str, Any]:
    return {
        "status": "failed",
        "error": {
            "code": "authoring_validation_failed",
            "message": "authoring_invalid_output at authoring: We couldn't write this part of your story.",
            "retryable": False,
            "details": {
                "errors": [
                    {
                        "path": "authoring",
                        "code": "authoring_invalid_output",
                        "message": "...",
                    }
                ],
                **details,
            },
        },
    }


def test_a_redrafted_failure_says_the_server_already_retried_and_what_it_cost() -> None:
    job = _failed(
        cause="schema_refusal",
        reason="the writer's answer broke the story format; one retry is safe",
        retry_safe=True,
        auto_retried=True,
        draft_runs=2,
    )

    text = describe_job_error(job)

    assert text.startswith("failed authoring_validation_failed: ")
    last = text.splitlines()[-1]
    assert last.startswith(
        "Why: the writer's answer broke the story format; one retry is safe."
    )
    assert "already wrote this draft once more on its own" in last
    assert DRAFT_RETRY_COST in last
    assert "same cost as a manual retry" in last
    assert "does not retry it on its own" in last
    assert server_already_retried(job) is True


def test_a_safe_failure_the_server_did_not_retry_says_one_retry_is_safe() -> None:
    line = draft_retry_line(
        _failed(
            cause="unknown",
            reason="no reason reported; one retry is safe",
            retry_safe=True,
            auto_retried=False,
        )["error"]
    )

    assert line is not None
    assert "Next: one retry is safe" in line
    assert "already wrote" not in line


def test_an_unsafe_failure_says_fix_the_reason_first() -> None:
    line = draft_retry_line(
        _failed(
            cause="provider_balance",
            reason="the writing model's provider refused for credits or billing",
            retry_safe=False,
            auto_retried=False,
        )["error"]
    )

    assert line is not None
    assert "fails the same way" in line
    assert "one retry is safe" not in line


def test_an_error_from_an_older_server_reads_as_before() -> None:
    job = {
        "status": "failed",
        "error": {
            "code": "authoring_validation_failed",
            "message": "rule X",
            "details": {},
        },
    }

    assert describe_job_error(job) == "failed authoring_validation_failed: rule X"
    assert server_already_retried(job) is False


def test_a_stall_the_server_already_redrafted_is_not_retried_by_the_kit() -> None:
    stalled = {
        "status": "failed",
        "error": {
            "code": "authoring_stalled",
            "message": "stalled",
            "retryable": True,
            "details": {},
        },
    }
    assert _retryable_stall(stalled) is True

    stalled["error"]["details"] = {"auto_retried": True, "draft_runs": 2}
    assert _retryable_stall(stalled) is False
