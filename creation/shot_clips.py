"""Shot clips: film a show's takes one clip per board row, joined with hard cuts (a test opt-in).

Founder-approved test (16 Oct 2026, Iron Saint "Low Tide"). The server keeps
the opt-in on the spine (``shot_clips``, fictora-drama ``POST
/v1/spines/{id}/shot-clips``, operators only). With it on:

- a portrait board may hold five rows, so a 30 s episode of two takes can hold
  nine shots (boards of 5 + 4 rows);
- each H3 Turbo take is filmed as one short clip per board row: the clip starts
  on its own row's left cell, is told only that row's words, films the row's
  planned seconds (billed at least 5 s each) to its slice of the take's locked
  voice track, and never starts on another clip's last frame;
- the server cuts each clip to its row's seconds and joins them into the
  take's one clip under the take's dialogue track. The desk then gets one take
  per board exactly as before, so ``finish`` (SFX, voices, impact, captions),
  ``join`` and reels run unchanged.

The kit's part is thin:

- ``fictora-produce shot-clips --desk D [--on | --off]`` reads or sets the
  opt-in (free). Set it before the episode's boards are drawn: it is what lets
  a board hold five rows.
- ``film --shot-clips`` is how an opted-in show is filmed. It prices from the
  server's estimate, which counts every clip at its own billed length, and it
  refuses (nothing sent) when that estimate does not price clips (an older
  server) or when the flag and the show disagree. A film never goes out as
  shot clips by accident, and an opted-in show never films without the flag.
- the booked price of a filmed take is the sum of its clips (take facts
  ``shot_clips``; :func:`creation.prices.take_facts_usd`), not the joined
  take's length.

When the mode ships for real it becomes the default for action shows with no
switch: this module, the command and the flag go.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, TextIO

import httpx

from creation.harness.http_util import api_error_text
from creation.post.desk import open_api, refresh_spine, spine_body

#: The server's route (POST sets; the spine's ``shot_clips`` reads it).
SHOT_CLIPS_ROUTE = "/v1/spines/{spine_id}/shot-clips"

#: Said when an opted-in show is filmed without the flag (nothing sent, nothing charged).
NEEDS_FLAG = (
    "This show films one clip per board row (shot clips, a test opt-in): film it with "
    "`film --episode {episode} --shot-clips`, which prices every clip. Nothing was sent or charged."
)
#: Said when the flag is given on a show that did not opt in.
NOT_OPTED_IN = (
    "--shot-clips films a show that opted into shot clips, and this one has not. Run "
    "`fictora-produce shot-clips --desk <desk> --on` before its boards are drawn (a board may then hold "
    "five rows), then film. Nothing was sent or charged."
)
#: Said when the server's estimate does not price clips.
NOT_PRICED = (
    "The Drama API did not price this film as shot clips (an older deploy, or no estimate), so the price "
    "is not known. Nothing was sent or charged. Ask engineering for the shot-clips deploy (fictora-drama "
    "feat/shot-clips), then price again."
)


class _Api(Protocol):
    """The slice of :class:`creation.harness.session.DramaApiRunSession` used here."""

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]: ...

    def spine(self, spine_id: str) -> dict[str, Any]: ...


def films_shot_clips(spine: Mapping[str, Any] | None) -> bool:
    """Whether a show films one clip per board row (its spine's ``shot_clips``).

    Parameters
    ----------
    spine
        Spine JSON (bare or ``{"spine": …}``), or ``None``.

    Returns
    -------
    bool
        ``True`` only when the server stores the opt-in.
    """

    return bool(spine) and spine_body(spine).get("shot_clips") is True


def set_shot_clips(run: _Api, spine: Mapping[str, Any], on: bool) -> dict[str, Any]:
    """Store the opt-in (``POST …/shot-clips``, free). A stale spine version is read again and sent once more.

    Parameters
    ----------
    run
        Open API session.
    spine
        The show's spine.
    on
        ``True`` to film one clip per board row, ``False`` to film whole takes.

    Returns
    -------
    dict[str, Any]
        The server's answer (``shot_clips``, ``changed``, ``applied``, ``notice``).

    Raises
    ------
    RuntimeError
        The server is older than the route, or refused the change.
    """

    body = spine_body(spine)
    sid = str(body.get("spine_id") or "")
    path = SHOT_CLIPS_ROUTE.format(spine_id=sid)
    for attempt in (1, 2):
        status, answer = run.post_optional(
            path, {"spine_version": body.get("spine_version"), "on": on}
        )
        if 200 <= status < 300 and isinstance(answer, Mapping):
            return dict(answer)
        error = answer.get("error") if isinstance(answer, Mapping) else None
        code = str(error.get("code") or "") if isinstance(error, Mapping) else ""
        if status == 409 and code == "spine_version_conflict" and attempt == 1:
            body = spine_body(run.spine(sid))
            continue
        if status in (404, 405) and not code:
            raise RuntimeError(
                "This Drama API has no shot-clips route yet (an older deploy). Nothing was changed."
            )
        raise RuntimeError(
            f"the server refused shot clips (HTTP {status}): {api_error_text(answer)}"
        )
    raise AssertionError("unreachable")


def run_shot_clips(
    desk: Path, *, set_to: bool | None = None, out: TextIO | None = None
) -> bool:
    """``shot-clips --desk D [--on | --off]``: read or change whether the show films one clip per row (free).

    Parameters
    ----------
    desk
        Series desk bound to a story.
    set_to
        ``True``/``False`` to store it for the show; ``None`` to read it.
    out
        Text stream.

    Returns
    -------
    bool
        Whether the show films shot clips after the call.
    """

    from creation.production_state import load_production

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    episode = load_production(desk).episode_ordinal
    run = open_api(desk, episode)
    try:
        spine = refresh_spine(run, desk, episode)
        if set_to is None:
            on = films_shot_clips(spine)
            print(
                "Shot clips: "
                + (
                    "ON - each take films one clip per board row (film with --shot-clips)."
                    if on
                    else "off - whole takes."
                ),
                file=out,
            )
            return on
        try:
            answer = set_shot_clips(run, spine, set_to)
        except httpx.TransportError as exc:
            raise RuntimeError(
                f"the Drama API could not be reached; nothing was changed ({exc})"
            ) from None
        refresh_spine(run, desk, episode)
        print(
            f"Shot clips {'ON' if set_to else 'off'} for this show. {answer.get('notice') or ''}".strip(),
            file=out,
        )
        return bool(answer.get("shot_clips"))
    finally:
        run.client.close()  # type: ignore[attr-defined]


def shot_clips_film_refusal(
    spine: Mapping[str, Any] | None, *, flag: bool, episode: int
) -> str | None:
    """Why a film cannot go out, before anything is priced or sent; ``None`` when it can.

    Parameters
    ----------
    spine
        The show's spine.
    flag
        ``--shot-clips`` was given.
    episode
        Episode being filmed.

    Returns
    -------
    str | None
        The plain refusal, or ``None``.
    """

    on = films_shot_clips(spine)
    if on and not flag:
        return NEEDS_FLAG.format(episode=episode)
    if flag and not on:
        return NOT_OPTED_IN
    return None


def shot_clips_priced(estimate: Mapping[str, Any] | None) -> int | None:
    """The clip count the server's estimate priced, or ``None`` when it did not price clips."""

    cost = estimate.get("cost_estimate") if isinstance(estimate, Mapping) else None
    if not isinstance(cost, Mapping) or cost.get("total_usd") is None:
        return None
    clips = cost.get("shot_clips")
    return int(clips) if isinstance(clips, int) and clips > 0 else None


def shot_clips_price_line(estimate: Mapping[str, Any]) -> str:
    """One line saying what the clips are billed: how many, and their seconds.

    Parameters
    ----------
    estimate
        The ``batches/estimate`` answer (it priced clips).

    Returns
    -------
    str
        ``Shot clips: 9 clip(s) ... 45 s billed.``
    """

    cost = estimate.get("cost_estimate") or {}
    clips = shot_clips_priced(estimate) or 0
    seconds = cost.get("billed_seconds")
    return (
        f"Shot clips: {clips} clip(s), one per board row, each billed on its own (at least 5 s); "
        f"{seconds} s billed in all. Each clip is cut to its row's seconds and the take is joined on the "
        "server, so finish and join read one take per board as before."
    )


__all__ = [
    "NEEDS_FLAG",
    "NOT_OPTED_IN",
    "NOT_PRICED",
    "SHOT_CLIPS_ROUTE",
    "films_shot_clips",
    "run_shot_clips",
    "set_shot_clips",
    "shot_clips_film_refusal",
    "shot_clips_price_line",
    "shot_clips_priced",
]
