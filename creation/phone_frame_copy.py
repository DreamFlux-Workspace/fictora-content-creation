"""A landscape episode's phone-frame copy: the 16:9 film in the 9:16 frame, with a title and captions.

A landscape show (founder, 27 Nov 2026) is delivered 16:9 with nothing on the picture. When the
creator wants a phone-frame copy, the server makes it from the finished episode (nothing is
filmed again). The kit sends the creator's title and caption choices as given and prints the
server's answer; which shows may have one, and what an unset choice means, is the server's call.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

from creation.harness.http_util import api_error_text

PHONE_FRAME_COPY_ROUTE = (
    "/v1/video-generations/{job_id}/episodes/{episode_id}/letterboxed-copy"
)
_MADE = "The server made the phone-frame copy"


def copy_request(
    *,
    setup_line: str | None = None,
    hook_line: str | None = None,
    captions: bool | None = None,
    caption_colour: str | None = None,
) -> dict[str, Any]:
    """The request body: only the choices the operator made; the server fills the rest.

    Parameters
    ----------
    setup_line, hook_line
        The title's white and yellow lines.
    captions
        Captions under the picture, or none.
    caption_colour
        ``yellow`` or ``white``.

    Returns
    -------
    dict[str, Any]
        The JSON body.
    """

    chosen = {
        "setup_line": setup_line,
        "hook_line": hook_line,
        "captions": captions,
        "caption_colour": caption_colour,
    }
    return {key: value for key, value in chosen.items() if value is not None}


def run_phone_frame_copy(
    desk: Path,
    *,
    episode: int,
    body: dict[str, Any],
    out: TextIO = sys.stdout,
    api: Any = None,
) -> int:
    """``phone-frame-copy --desk D --episode N``: ask the server for the episode's 9:16 copy.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    body
        :func:`copy_request`.
    out
        Where the server's answer is printed.
    api
        Drama API session with ``post_optional`` (default: the desk's).

    Returns
    -------
    int
        ``0`` when the server took the ask, ``2`` when nothing was sent or the server refused.
    """

    from creation.post.desk import open_api
    from creation.post.server_finish import _desk_episode_id
    from creation.post.take_handles import take_coordinate

    desk = desk.expanduser().resolve()
    where = take_coordinate(desk, episode, "t1")
    if where is None:
        print(
            f"Stopped: the desk has no filmed episode {episode}, so the server cannot find it. "
            "Nothing was sent.",
            file=out,
        )
        return 2
    path = PHONE_FRAME_COPY_ROUTE.format(
        job_id=where.video_job_id, episode_id=_desk_episode_id(desk, episode)
    )
    run = api if api is not None else open_api(desk, episode)
    try:
        status, answer = run.post_optional(
            path, body, idempotency_key=f"kit-phone-frame-copy-{uuid.uuid4().hex}"
        )
    finally:
        if api is None:
            run.client.close()
    if status in (404, 405) and _error_code(answer) is None:
        print(
            "Stopped: this Drama API cannot make a phone-frame copy yet (an older deploy). "
            "Nothing was made or charged.",
            file=out,
        )
        return 2
    if not 200 <= status < 300:
        print(
            f"The server refused the phone-frame copy (HTTP {status}): {api_error_text(answer)}",
            file=out,
        )
        return 2
    print(_made_line(answer), file=out)
    return 0


def _made_line(answer: Any) -> str:
    copy = answer.get("letterboxed_copy") if isinstance(answer, Mapping) else None
    url = copy.get("video_url") if isinstance(copy, Mapping) else None
    if not url:
        return f"{_MADE}."
    return f"{_MADE} ({'with' if copy.get('captions') else 'without'} captions): {url}"


def _error_code(answer: Any) -> str | None:
    if not isinstance(answer, Mapping):
        return None
    for holder in (answer.get("error"), answer.get("detail"), answer):
        if isinstance(holder, Mapping) and holder.get("code"):
            return str(holder["code"])
    return None


__all__ = ["PHONE_FRAME_COPY_ROUTE", "copy_request", "run_phone_frame_copy"]
