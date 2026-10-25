"""The kit's long waits print the server's steps (fictora-drama making_steps, founder design 25 Oct 2026).

The server owns the steps; the kit prints them as they change, the same
states the app draws: ``✓`` done, ``⠋`` running, ``○`` upcoming, ``✕`` failed,
then the server's one failure sentence or its folded "Made in N steps ✓".
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from creation.harness import http_util


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _steps(filming: str, editing: str, *, done: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "id": "filming",
            "label": "Filming your scenes",
            "state": filming,
            "detail": f"{done} of 3",
        },
        {"id": "editing", "label": "Editing it together", "state": editing},
        {"id": "final_check", "label": "Final check", "state": "upcoming"},
    ]


def _client(*payloads: dict[str, Any]) -> Mock:
    queue = list(payloads)

    def fake_get(url: str, headers: dict[str, str]) -> Mock:
        response = Mock()
        response.is_success = True
        response.json.return_value = queue.pop(0) if len(queue) > 1 else queue[0]
        return response

    client = Mock()
    client.get = fake_get
    return client


def test_the_steps_print_as_the_server_sends_them() -> None:
    assert http_util.format_wait_steps(
        {"steps": _steps("done", "running", done=3)}
    ) == ("✓ Filming your scenes 3 of 3\n⠋ Editing it together\n○ Final check")


def test_a_failed_step_prints_its_cross_and_the_servers_sentence() -> None:
    printed = http_util.format_wait_steps(
        {
            "steps": _steps("done", "failed", done=3),
            "failed_message": "Editing it together didn’t finish. Try again picks up from here.",
        }
    )

    assert printed is not None
    assert "✕ Editing it together" in printed
    assert printed.endswith(
        "Editing it together didn’t finish. Try again picks up from here."
    )


def test_the_cast_step_prints_one_line_per_picture() -> None:
    printed = http_util.format_wait_steps(
        {
            "steps": [
                {
                    "id": "drawing",
                    "label": "Drawing your cast",
                    "state": "running",
                    "items": [
                        {"label": "Mira", "state": "done"},
                        {"label": "the place", "state": "upcoming"},
                    ],
                }
            ]
        }
    )

    assert printed == "⠋ Drawing your cast\n    ✓ Mira\n    ○ the place"


def test_an_older_server_prints_nothing() -> None:
    assert http_util.format_wait_steps({"status": "running", "progress": 50}) is None


def test_the_poll_prints_each_change_once_and_the_folded_line_at_the_end() -> None:
    said: list[str] = []
    running = {
        "status": "running",
        "progress": 50,
        "job_id": "v",
        "steps": _steps("running", "upcoming"),
    }
    done = {
        "status": "completed",
        "progress": 100,
        "job_id": "v",
        "steps": [{**step, "state": "done"} for step in _steps("done", "done", done=3)],
        "done_summary": "Made in 3 steps ✓",
    }

    http_util.poll_until_terminal(
        _client(running, running, done),
        url="https://example.test/v1/video-generations/v",
        headers={},
        emit=None,
        label="video",
        deadline_seconds=600.0,
        interval_seconds=1.0,
        clock=_Clock(),
        say_steps=said.append,
    )

    assert len(said) == 2
    assert said[0].startswith("⠋ Filming your scenes 1 of 3")
    assert said[1].endswith("Made in 3 steps ✓")
