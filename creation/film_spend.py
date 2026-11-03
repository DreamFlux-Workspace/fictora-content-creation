"""Print what the server says a film was quoted at and cost, and warn when it went over budget.

The server owns the rule (fictora-drama ``kit_film_spend``, founder 3 Nov 2026: "allow
for it to go overbudget but show warning and how much it went overbudget only for kit").
``film --confirm-spend`` and ``step --confirm-spend`` film in one call: the server quotes
the film when it admits it, films, and every ``GET /v1/video-generations/{id}`` carries
``kit_spend`` (quoted, cost so far, over budget). The kit keeps no budget of its own; it
prints what the server says.
"""

from __future__ import annotations

from typing import Any

from creation.harness.session import DramaApiRunSession


def read_kit_spend(run: DramaApiRunSession, job_id: str) -> dict[str, Any] | None:
    """Return the server's ``kit_spend`` for a film job, or ``None`` when it sent none.

    Parameters
    ----------
    run
        Session that owns the job.
    job_id
        The video job.

    Returns
    -------
    dict[str, Any] | None
        ``quoted_usd``, ``approved_usd``, ``cost_usd``, ``cost_final``, ``over_budget_usd``.
    """

    if not job_id:
        return None
    status, body = run.get_optional(f"/v1/video-generations/{job_id}")
    if (
        200 <= status < 300
        and isinstance(body, dict)
        and isinstance(body.get("kit_spend"), dict)
    ):
        return body["kit_spend"]
    return None


def quote_line(spend: dict[str, Any] | None) -> str:
    """The line printed when the film is admitted: the server's quote.

    Parameters
    ----------
    spend
        :func:`read_kit_spend`'s answer.

    Returns
    -------
    str
        One line for the operator.
    """

    if spend is None:
        return (
            "!! The server did not send a quote for this film (an older server?). It films as the human "
            "confirmed; check the spend line when the takes are in."
        )
    quoted = _usd(spend.get("quoted_usd"))
    if quoted is None:
        return "Server quote: none (no verified price for this lane). Filming as the human confirmed."
    return f"Server quote: ${quoted:.2f}. Filming now; if it costs more, the overage is shown when the takes are in."


def cost_lines(spend: dict[str, Any] | None) -> list[str]:
    """The lines printed once the takes are in: the cost, and a warning when it went over.

    Parameters
    ----------
    spend
        :func:`read_kit_spend`'s answer.

    Returns
    -------
    list[str]
        Empty when the server sent nothing to say.
    """

    if spend is None:
        return []
    quoted = _usd(spend.get("quoted_usd"))
    approved = _usd(spend.get("approved_usd"))
    cost = _usd(spend.get("cost_usd"))
    over = _usd(spend.get("over_budget_usd"))
    lines: list[str] = []
    if cost is not None:
        quote = f" (quoted ${quoted:.2f})" if quoted is not None else ""
        settled = (
            "."
            if spend.get("cost_final")
            else "; the server is still finishing the run, so it can still move."
        )
        lines.append(f"Server cost: ${cost:.2f}{quote}{settled}")
    if over is not None and cost is not None:
        budget = f"approved ${approved:.2f}, " if approved is not None else ""
        quoted_part = f"quoted ${quoted:.2f}, " if quoted is not None else ""
        lines.append(
            f"⚠ Went over budget by ${over:.2f} ({budget}{quoted_part}cost ${cost:.2f})"
        )
    return lines


def _usd(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
