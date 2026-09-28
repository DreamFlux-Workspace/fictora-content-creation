"""Dated provider prices the desk books spend with (a take, a still) and the envelopes it warns at.

These are provider costs, copied from the product's one dated price table
(fictora-drama ``prices.py``, #408/#420) with their sources and check dates. The
server's ``POST /v1/spines/{id}/batches/estimate`` returns the same figures as
``cost_estimate``; the desk prefers that answer and uses this table only when
the server sends no dollars, and to book a filmed take from its take facts.

A lane without a verified price is ``None``: never guessed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Final

H3_MAX_R2V_ENDPOINT: Final = "minimax/h3-max/reference-to-video"
"""The lane drama takes film on since 2026-09-25 (``minimax-h3`` pin)."""
H3_MAX_TURBO_I2V_ENDPOINT: Final = "minimax/h3-max-turbo/image-to-video"
"""The older fast lane. Priced only when a take's facts name it."""
H3_RESOLUTION: Final = "768P"

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

#: What each lane pin films on: ``model_overrides.video`` -> (endpoint, resolution).
LANE_ENDPOINTS: Final[dict[str, tuple[str, str]]] = {"minimax-h3": (H3_MAX_R2V_ENDPOINT, H3_RESOLUTION)}

LANE_LABELS: Final[dict[str, str]] = {H3_MAX_R2V_ENDPOINT: "H3 Max R2V", H3_MAX_TURBO_I2V_ENDPOINT: "H3 Max Turbo"}

#: Spend envelopes (warn only, never a block): first episode of a new series, then continuing by band.
ENVELOPE_FIRST_USD: Final = 5.50
ENVELOPE_CONTINUING_USD: Final[dict[str, float]] = {"15s": 2.50, "30s": 5.00, "60s": 8.00}


def video_usd_per_second(endpoint_id: str, resolution: str, *, on: date) -> Decimal | None:
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
        return H3_TURBO_PROMO_USD_PER_SECOND if on <= H3_TURBO_PROMO_LAST_DAY else H3_TURBO_USD_PER_SECOND
    return None


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
    return H3_MAX_R2V_EXTRA_REFERENCE_IMAGE_USD * max(0, count - H3_MAX_R2V_INCLUDED_REFERENCE_IMAGES)


def reference_images_ceiling(cast_count: int) -> int:
    """Return the most reference images one take of a story sends: its board and every plate, at most nine.

    Parameters
    ----------
    cast_count
        Cast cards on the story.

    Returns
    -------
    int
        ``1 + cast_count`` capped at nine.
    """

    return min(H3_MAX_REFERENCE_IMAGES, 1 + max(0, cast_count))


def take_usd(endpoint_id: str, resolution: str, seconds: float, *, on: date, reference_images: int = 0) -> float | None:
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
    total = rate * Decimal(str(seconds)) + reference_images_usd(endpoint_id, reference_images)
    return round(float(total), 2)


def lane_take_usd(video_lane: str | None, seconds: float, *, on: date, reference_images: int = 0) -> float | None:
    """Price one take on a lane pin (``minimax-h3``: 15 s = $1.20 plus images past four).

    Parameters
    ----------
    video_lane
        ``model_overrides.video``.
    seconds
        Take length.
    on
        Filming day.
    reference_images
        Reference images the take sends.

    Returns
    -------
    float | None
        Dollars, or ``None`` when the lane is not in the table.
    """

    endpoint = LANE_ENDPOINTS.get(video_lane or "")
    if endpoint is None:
        return None
    return take_usd(endpoint[0], endpoint[1], seconds, on=on, reference_images=reference_images)


def lane_label(video_lane: str | None) -> str:
    """Return the operator's name for what a lane pin films on (``H3 Max R2V``).

    Parameters
    ----------
    video_lane
        ``model_overrides.video``.

    Returns
    -------
    str
        A short name; the pin itself when unknown.
    """

    endpoint = LANE_ENDPOINTS.get(video_lane or "")
    return LANE_LABELS.get(endpoint[0], endpoint[0]) if endpoint else str(video_lane or "unknown lane")


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
    price = take_usd(endpoint, resolution, float(seconds), on=on, reference_images=count)
    if price is None:
        return None
    label = LANE_LABELS.get(endpoint, endpoint)
    return price, f"{label} {resolution}, {float(seconds):g} s, {count} reference image(s)"


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
    "STILL_USD",
    "envelope_usd",
    "lane_label",
    "lane_take_usd",
    "reference_images_ceiling",
    "reference_images_usd",
    "take_facts_usd",
    "take_usd",
    "video_usd_per_second",
]
