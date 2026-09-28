"""Dated provider prices the desk books spend with (a take, a still) and the envelopes it warns at.

These are provider costs, copied from the product's one dated price table
(fictora-drama ``prices.py``, #408/#420) with their sources and check dates. The
server's ``POST /v1/spines/{id}/batches/estimate`` returns the same figures as
``cost_estimate``; the desk prefers that answer and uses this table only when
the server sends no dollars, and to book a filmed take from its take facts.

Which endpoint a ``minimax-h3`` take films on is the server's choice, not the
pin's: the server films H3 Max Turbo image-to-video by default and H3 Max
reference-to-video only when engineering switches the deploy to it (fictora-drama
44298f94, 2026-09-28). So the desk prices the endpoint the server names (the
estimate's ``cost_estimate.video_endpoint_id``, a take's ``take_facts.endpoint_id``)
and falls back to the server's default, Turbo, only when no answer has named one.

A lane without a verified price is ``None``: never guessed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Final

H3_MAX_R2V_ENDPOINT: Final = "minimax/h3-max/reference-to-video"
"""H3 Max reference-to-video: an engineering-side switch on the deploy, never the desk's pick."""
H3_MAX_TURBO_I2V_ENDPOINT: Final = "minimax/h3-max-turbo/image-to-video"
"""H3 Max Turbo image-to-video: what ``minimax-h3`` takes film on by default (fictora-drama 44298f94)."""
H3_RESOLUTION: Final = "768P"
#: Turbo films at least this long: a shorter take is compiled (and billed) at five seconds.
H3_TURBO_MIN_SECONDS: Final = 5

#: H3 Max reference-to-video per rendered second, by resolution. Source:
#: https://fal.ai/models/minimax/h3-max/reference-to-video (checked 2026-09-26). No promotion.
H3_MAX_R2V_USD_PER_SECOND: Final[dict[str, Decimal]] = {
    "480P": Decimal("0.05"),
    "768P": Decimal("0.08"),
    "1080P": Decimal("0.16"),
}
#: Reference images an R2V take carries free, and the charge for each square one past them (same page, same day).
H3_MAX_R2V_INCLUDED_REFERENCE_IMAGES: Final = 4
H3_MAX_R2V_EXTRA_REFERENCE_IMAGE_USD: Final = Decimal("0.02048")
#: The most reference images one H3 take sends: the board, then cast plates.
H3_MAX_REFERENCE_IMAGES: Final = 9

#: H3 Max Turbo image-to-video at 768P: $0.02/s through 2026-09-30 (fal promo), $0.04/s from 2026-10-01.
#: Source: https://fal.ai/models/minimax/h3-max-turbo/image-to-video (checked 2026-09-25).
H3_TURBO_PROMO_USD_PER_SECOND: Final = Decimal("0.02")
H3_TURBO_PROMO_LAST_DAY: Final = date(2026, 9, 30)
H3_TURBO_USD_PER_SECOND: Final = Decimal("0.04")

#: One cast plate or one storyboard board (the product's own still price since #404).
STILL_USD: Final = Decimal("0.30")

#: What each lane pin films on when no server answer has named the endpoint yet:
#: ``model_overrides.video`` -> (endpoint, resolution). The server's default, never R2V.
LANE_DEFAULT_ENDPOINTS: Final[dict[str, tuple[str, str]]] = {
    "minimax-h3": (H3_MAX_TURBO_I2V_ENDPOINT, H3_RESOLUTION)
}

LANE_LABELS: Final[dict[str, str]] = {
    H3_MAX_R2V_ENDPOINT: "H3 Max R2V",
    H3_MAX_TURBO_I2V_ENDPOINT: "H3 Max Turbo",
}

#: Spend envelopes (warn only, never a block): first episode of a new series, then continuing by band.
#: Sized when takes filmed on R2V ($1.20 a 15 s take); Turbo takes ($0.30, $0.60 from 2026-10-01) sit well
#: inside them, so they warn only on real overspend (redraws, re-takes). Kept, not tightened.
ENVELOPE_FIRST_USD: Final = 5.50
ENVELOPE_CONTINUING_USD: Final[dict[str, float]] = {
    "15s": 2.50,
    "30s": 5.00,
    "60s": 8.00,
}


def video_usd_per_second(
    endpoint_id: str, resolution: str, *, on: date
) -> Decimal | None:
    """Return a lane's verified per-second rate on a day.

    Parameters
    ----------
    endpoint_id
        Fal endpoint the take films on.
    resolution
        Provider resolution token (``768P``).
    on
        The day the take is filmed.

    Returns
    -------
    Decimal | None
        Dollars per second, or ``None`` when the lane has no verified price.
    """

    if endpoint_id == H3_MAX_R2V_ENDPOINT:
        return H3_MAX_R2V_USD_PER_SECOND.get(resolution.upper())
    if endpoint_id == H3_MAX_TURBO_I2V_ENDPOINT and resolution.upper() == H3_RESOLUTION:
        return (
            H3_TURBO_PROMO_USD_PER_SECOND
            if on <= H3_TURBO_PROMO_LAST_DAY
            else H3_TURBO_USD_PER_SECOND
        )
    return None


def billed_seconds(endpoint_id: str, seconds: float) -> float:
    """Return the seconds one take films and is billed for on a lane.

    Parameters
    ----------
    endpoint_id
        Fal endpoint.
    seconds
        Requested take length.

    Returns
    -------
    float
        ``seconds``, raised to five on Turbo (the server compiles a shorter Turbo take at five).
    """

    if endpoint_id == H3_MAX_TURBO_I2V_ENDPOINT:
        return max(float(seconds), float(H3_TURBO_MIN_SECONDS))
    return float(seconds)


def reference_images_usd(endpoint_id: str, count: int) -> Decimal:
    """Return one take's reference-image surcharge (R2V charges past four; other lanes nothing).

    Parameters
    ----------
    endpoint_id
        Fal endpoint.
    count
        Reference images the take sends.

    Returns
    -------
    Decimal
        Dollars.
    """

    if endpoint_id != H3_MAX_R2V_ENDPOINT:
        return Decimal(0)
    return H3_MAX_R2V_EXTRA_REFERENCE_IMAGE_USD * max(
        0, count - H3_MAX_R2V_INCLUDED_REFERENCE_IMAGES
    )


def reference_images_ceiling(cast_count: int, endpoint_id: str) -> int:
    """Return the most images one take of a story sends to the video model.

    Parameters
    ----------
    cast_count
        Cast cards on the story.
    endpoint_id
        Fal endpoint. R2V sends the board and every plate; Turbo sends the board alone.

    Returns
    -------
    int
        ``1 + cast_count`` capped at nine on R2V; ``1`` on any other lane.
    """

    if endpoint_id != H3_MAX_R2V_ENDPOINT:
        return 1
    return min(H3_MAX_REFERENCE_IMAGES, 1 + max(0, cast_count))


def take_usd(
    endpoint_id: str,
    resolution: str,
    seconds: float,
    *,
    on: date,
    reference_images: int = 0,
) -> float | None:
    """Return one take's price: billed seconds times the rate, plus its reference images.

    Parameters
    ----------
    endpoint_id
        Fal endpoint.
    resolution
        Provider resolution token.
    seconds
        Seconds the take filmed (take facts) or will film.
    on
        The day the take is filmed.
    reference_images
        Reference images the take sends.

    Returns
    -------
    float | None
        Dollars rounded to cents; ``None`` for an unpriced lane.
    """

    rate = video_usd_per_second(endpoint_id, resolution, on=on)
    if rate is None:
        return None
    billed = Decimal(str(billed_seconds(endpoint_id, seconds)))
    total = rate * billed + reference_images_usd(endpoint_id, reference_images)
    return round(float(total), 2)


def server_lane(answer: dict | None) -> tuple[str, str] | None:
    """Read the endpoint and resolution a server answer says the story films on.

    Parameters
    ----------
    answer
        A batch estimate (``cost_estimate.video_endpoint_id`` / ``video_resolution``)
        or a take's ``take_facts`` (``endpoint_id`` / ``resolution``).

    Returns
    -------
    tuple[str, str] | None
        ``(endpoint, resolution)``, or ``None`` when the answer names no endpoint.
    """

    if not isinstance(answer, dict):
        return None
    cost = answer.get("cost_estimate")
    if isinstance(cost, dict) and cost.get("video_endpoint_id"):
        return str(cost["video_endpoint_id"]), str(
            cost.get("video_resolution") or H3_RESOLUTION
        )
    if answer.get("endpoint_id"):
        return str(answer["endpoint_id"]), str(
            answer.get("resolution") or H3_RESOLUTION
        )
    return None


def lane_endpoint(
    video_lane: str | None, *, server: tuple[str, str] | None = None
) -> tuple[str, str] | None:
    """Return what a take on a lane pin films on: the server's word first, else the pin's default.

    Parameters
    ----------
    video_lane
        ``model_overrides.video``.
    server
        ``(endpoint, resolution)`` the server last named for this story (see :func:`server_lane`).

    Returns
    -------
    tuple[str, str] | None
        ``(endpoint, resolution)``; ``None`` when neither is known.
    """

    if server is not None:
        return server
    return LANE_DEFAULT_ENDPOINTS.get(video_lane or "")


def lane_take_usd(
    video_lane: str | None,
    seconds: float,
    *,
    on: date,
    reference_images: int = 0,
    server: tuple[str, str] | None = None,
) -> float | None:
    """Price one take on a lane pin, on the endpoint the server films it on.

    ``minimax-h3`` with no server answer is priced as Turbo: 15 s is $0.30
    through 2026-09-30 and $0.60 from 2026-10-01.

    Parameters
    ----------
    video_lane
        ``model_overrides.video``.
    seconds
        Take length.
    on
        Filming day.
    reference_images
        Reference images the take sends (charged on R2V only).
    server
        ``(endpoint, resolution)`` the server named for this story, when known.

    Returns
    -------
    float | None
        Dollars, or ``None`` when the endpoint has no verified price.
    """

    endpoint = lane_endpoint(video_lane, server=server)
    if endpoint is None:
        return None
    return take_usd(
        endpoint[0], endpoint[1], seconds, on=on, reference_images=reference_images
    )


def lane_label(video_lane: str | None, *, server: tuple[str, str] | None = None) -> str:
    """Return the operator's name for what a lane pin films on (``H3 Max Turbo``).

    Parameters
    ----------
    video_lane
        ``model_overrides.video``.
    server
        ``(endpoint, resolution)`` the server named for this story, when known.

    Returns
    -------
    str
        A short name; the endpoint or the pin itself when unknown.
    """

    endpoint = lane_endpoint(video_lane, server=server)
    return (
        LANE_LABELS.get(endpoint[0], endpoint[0])
        if endpoint
        else str(video_lane or "unknown lane")
    )


def take_facts_usd(facts: dict, *, on: date) -> tuple[float, str] | None:
    """Price one filmed take from its take facts (the lane, length and references it really used).

    Parameters
    ----------
    facts
        ``take_facts`` from ``GET /v1/jobs/{take_job}/take-facts``.
    on
        Filming day.

    Returns
    -------
    tuple[float, str] | None
        Dollars and a one-line account; ``None`` when the facts name no priced lane or length.
    """

    endpoint = str(facts.get("endpoint_id") or "")
    resolution = str(facts.get("resolution") or "")
    seconds = facts.get("duration_seconds")
    if not endpoint or not resolution or not isinstance(seconds, (int, float)):
        return None
    count = int(facts.get("reference_image_count") or 0)
    price = take_usd(
        endpoint, resolution, float(seconds), on=on, reference_images=count
    )
    if price is None:
        return None
    label = LANE_LABELS.get(endpoint, endpoint)
    return (
        price,
        f"{label} {resolution}, {float(seconds):g} s, {count} reference image(s)",
    )


def envelope_usd(*, first_episode: bool, band: str) -> float:
    """Return the warn-only spend envelope for an episode.

    Parameters
    ----------
    first_episode
        True for the first episode of a new series (plates, look and board included).
    band
        ``15s``, ``30s`` or ``60s``.

    Returns
    -------
    float
        Dollars.
    """

    return ENVELOPE_FIRST_USD if first_episode else ENVELOPE_CONTINUING_USD[band]


__all__ = [
    "ENVELOPE_CONTINUING_USD",
    "ENVELOPE_FIRST_USD",
    "H3_MAX_R2V_ENDPOINT",
    "H3_MAX_TURBO_I2V_ENDPOINT",
    "H3_TURBO_MIN_SECONDS",
    "LANE_DEFAULT_ENDPOINTS",
    "STILL_USD",
    "billed_seconds",
    "envelope_usd",
    "lane_endpoint",
    "lane_label",
    "lane_take_usd",
    "reference_images_ceiling",
    "reference_images_usd",
    "server_lane",
    "take_facts_usd",
    "take_usd",
    "video_usd_per_second",
]
