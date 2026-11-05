"""Finish again, for $0, an episode whose takes are all filmed but whose ending failed (5 Nov 2026).

Blood Vows ep 17: every take filmed fine, then the server's ending (music, finishing pass, delivery)
failed. A re-film asks for fresh takes on purpose (``seed_attempt_for``), so the creator paid $1.20 for
takes she already had. The server (fictora-drama #1341) now decides when a failed run can be finished
from its stored takes and says so on the re-film price (``finish_again`` on ``batches/estimate``). This
module only shows that and asks for it: the kit keeps nothing of its own.

* ``film`` (the price run) prints the server's offer before the re-film price;
* ``finish-again --episode N`` asks the server which run that is, then
  ``POST /v1/video-generations/{job_id}/finish-again``. Nothing is filmed or charged.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from creation.harness.http_util import api_error_text


def finish_again_offer(estimate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The server's ``finish_again`` offer on a re-film estimate, or ``None`` (none, or an older server).

    Parameters
    ----------
    estimate
        The ``batches/estimate`` answer.

    Returns
    -------
    Mapping[str, Any] | None
        ``{"job_id", "message"}`` as the server sent it.
    """

    offer = estimate.get("finish_again")
    if (
        isinstance(offer, Mapping)
        and isinstance(offer.get("job_id"), str)
        and offer["job_id"]
    ):
        return offer
    return None


def finish_again_offer_lines(
    estimate: Mapping[str, Any], *, desk: Path, episode: int
) -> list[str]:
    """The lines ``film`` prints before a re-film price when the server says the episode can be finished free.

    Parameters
    ----------
    estimate
        The ``batches/estimate`` answer.
    desk
        Series desk (for the command to run).
    episode
        Episode ordinal.

    Returns
    -------
    list[str]
        Nothing when there is no offer.
    """

    offer = finish_again_offer(estimate)
    if offer is None:
        return []
    message = (
        str(offer.get("message") or "").strip()
        or "This episode can be finished again for free."
    )
    return [
        f"!! {message}",
        f"   $0, no new takes: `fictora-produce finish-again --desk {desk} --episode {episode}` "
        f"(run {offer['job_id']}). Filming below pays for new takes instead.",
    ]


def run_finish_again(desk: Path, *, episode: int, out: Any = None) -> str:
    """Finish episode N again from its stored takes, when the server says it can be. Spends nothing.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    out
        Text stream.

    Returns
    -------
    str
        The printed summary.

    Raises
    ------
    CommandStopped
        When the server has no run of the episode to finish again, or refuses this one (its reason).
    """

    from dataclasses import replace

    from creation import episode_commands
    from creation.episode_commands import CommandStopped
    from creation.harness import stages_gated as stages

    out = out or sys.stdout
    if episode < 1:
        raise CommandStopped("--episode is 1 or more")
    desk, state, run = episode_commands._desk_session(desk)
    if episode != state.episode_ordinal:
        run.client.close()
        run = episode_commands._open_run(desk, replace(state, episode_ordinal=episode))
    try:
        # The free price run: the server answers which failed run (if any) can be finished.
        offer = finish_again_offer(
            stages.estimate_batch(run, spine_id=state.spine_id or "", episode=episode)
        )
        if offer is None:
            raise CommandStopped(
                f"episode {episode} has no failed run with every take stored, so there is nothing to finish "
                f"for free. Re-film it with `fictora-produce film --desk {desk} --episode {episode}` (priced first)"
            )
        job_id = str(offer["job_id"])
        status, answer = run.post_optional(
            f"/v1/video-generations/{job_id}/finish-again", {}
        )
        if status not in (200, 202):
            raise CommandStopped(
                f"the server did not finish run {job_id}: {api_error_text(answer)}. Nothing was spent"
            )
        state_word = answer.get("state") if isinstance(answer, Mapping) else None
        already = state_word == "already_finishing"
        lines = [
            (
                f"Episode {episode} (run {job_id}) is already being finished again; nothing new was started."
                if already
                else f"Finishing episode {episode} again from its stored takes (run {job_id}): no new takes, $0."
            ),
            "The episode appears in the app once the server finishes it.",
        ]
        text = "\n".join(lines)
        print(text, file=out)
        return text
    finally:
        run.client.close()


__all__ = ["finish_again_offer", "finish_again_offer_lines", "run_finish_again"]
