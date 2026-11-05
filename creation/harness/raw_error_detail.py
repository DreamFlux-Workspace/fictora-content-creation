"""The stored failure words behind a job's plain message, for operators who ask (fictora-drama #1339).

The server answers a failed job's ``error.message`` in plain words for the
creator and, to the kit only, the worker's own words as
``error.internal_detail``. The kit prints the plain message and code by
default and the raw words only with ``FICTORA_SHOW_RAW_ERRORS=1``, because
operators debug from them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

#: Environment switch that prints the stored failure words under the plain message.
SHOW_RAW_ERRORS_ENV = "FICTORA_SHOW_RAW_ERRORS"


def raw_detail_line(error: Mapping[str, Any]) -> str | None:
    """One line for the stored failure words, or ``None`` when the error carries none.

    Parameters
    ----------
    error
        A job's ``error`` (or any error envelope's ``error``).

    Returns
    -------
    str | None
        ``Raw detail: …`` with the switch on; a pointer to the switch with it off.
    """
    detail = error.get("internal_detail")
    if not isinstance(detail, str) or not detail.strip():
        return None
    if os.environ.get(SHOW_RAW_ERRORS_ENV, "").strip() == "1":
        return f"Raw detail: {detail.strip()}"
    return f"(raw detail hidden; set {SHOW_RAW_ERRORS_ENV}=1 to print it)"
