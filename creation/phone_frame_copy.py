"""A landscape episode's phone-frame copy: the 16:9 film in the 9:16 frame, with a title and captions.

A landscape show (founder, 27 Nov 2026) is delivered 16:9 with nothing on the picture. When the
creator wants a phone-frame copy, the server makes it from the finished episode (nothing is
filmed again) and asks for the title and captions itself. The kit only sends the ask and prints
the server's answer as sent; which shows may have one is the server's call.

Needs fictora-drama's letterboxed-copy route (the 16:9 frames finish PR) live: until then the
route answers 404 and the kit says so.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, TextIO

from creation.harness.http_util import api_error_text
from creation.post.desk import open_api, spine_body, spine_id
from creation.spine_view import episode_id_for

#: The server's route (POST asks for the copy).
PHONE_FRAME_COPY_ROUTE = "/v1/spines/{spine_id}/episodes/{episode_id}/letterboxed-copy"
#: 404 codes the route answers on purpose; any other 404 means the server predates it.
_KNOWN_NOT_FOUND = frozenset({"episode_not_found", "spine_not_found"})
_TAKEN = "The server took the ask for a phone-frame copy."


class _Api(Protocol):
    def spine(self, spine_id: str) -> dict[str, Any]: ...

    def post_optional(self, path: str, body: dict[str, Any]) -> tuple[int, Any]: ...


def run_phone_frame_copy(
    desk: Path, *, episode: int, out: TextIO = sys.stdout, api: _Api | None = None
) -> int:
    """``phone-frame-copy --desk D --episode N``: ask the server for the episode's 9:16 copy.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    out
        Where the server's answer is printed.
    api
        Drama API session (default: the desk's).

    Returns
    -------
    int
        ``0`` when the server took the ask, ``2`` when it refused or predates the route.
    """

    session = open_api(desk, episode) if api is None else None
    run: _Api = api if api is not None else session  # type: ignore[assignment]
    try:
        spine = spine_body(run.spine(spine_id(desk)))
        path = PHONE_FRAME_COPY_ROUTE.format(
            spine_id=spine_id(desk), episode_id=episode_id_for(spine, episode)
        )
        status, answer = run.post_optional(path, {})
        if status in (404, 405) and _code(answer) not in _KNOWN_NOT_FOUND:
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
        print(_answer_text(answer), file=out)
        return 0
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


def _code(answer: Any) -> str | None:
    detail = answer.get("detail") if isinstance(answer, Mapping) else None
    code = (detail if isinstance(detail, Mapping) else answer or {}).get("code")
    return str(code) if code else None


def _answer_text(answer: Any) -> str:
    if not isinstance(answer, Mapping):
        return _TAKEN
    words = [
        str(answer[key]).strip()
        for key in ("line", "message", "question")
        if isinstance(answer.get(key), str) and answer[key].strip()
    ]
    return "\n".join(words) or _TAKEN


__all__ = ["PHONE_FRAME_COPY_ROUTE", "run_phone_frame_copy"]
