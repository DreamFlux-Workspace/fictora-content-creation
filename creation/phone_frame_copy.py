"""A landscape episode's phone-frame copy: the 16:9 film in the 9:16 frame, with a title and captions.

A landscape show (founder, 27 Nov 2026) is delivered 16:9 with no title on the picture. When the
creator wants a phone-frame copy, the server makes it from the finished episode (nothing is
filmed again). The kit sends the creator's title and caption choices as given and prints the
server's answer; which shows may have one, and what an unset choice means, is the server's call.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from creation.harness.http_util import api_error_text

PHONE_FRAME_COPY_ROUTE = (
    "/v1/video-generations/{job_id}/episodes/{episode_id}/letterboxed-copy"
)
#: The marked copy for Instagram and TikTok: the episode export.
EXPORT_ROUTE = "/v1/video-generations/{job_id}/episodes/{episode_id}/export"
EXPORT_VERSION = "letterboxed_copy"
_MADE = "The server made the phone-frame copy"
_POST_WITH_SAVE = (
    "That link is the in-app copy. To post it on Instagram and TikTok, save the marked copy: "
    "phone-frame-copy --desk {desk} --episode {episode} --save"
)


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

    desk = desk.expanduser().resolve()
    answer = _ask(
        desk, episode, PHONE_FRAME_COPY_ROUTE, body, out=out, api=api,
        idempotency_key=f"kit-phone-frame-copy-{uuid.uuid4().hex}",
    )  # fmt: skip
    if answer is None:
        return 2
    print(_made_line(answer), file=out)
    print(_POST_WITH_SAVE.format(desk=desk, episode=episode), file=out)
    return 0


def run_save_phone_frame_copy(
    desk: Path,
    *,
    episode: int,
    out: TextIO = sys.stdout,
    api: Any = None,
    transport: Any = None,
) -> int:
    """``phone-frame-copy --save``: download the marked copy to post on Instagram and TikTok.

    The server's export of the episode's letterboxed copy (``version: letterboxed_copy``), with the
    Sokii mark as every posted file carries it; the in-app link is not the file to post.

    Parameters
    ----------
    desk
        Series desk.
    episode
        Episode ordinal.
    out
        Where the result is printed.
    api
        Drama API session with ``post_optional`` (default: the desk's).
    transport
        HTTP transport for the download (tests fake the server with it).

    Returns
    -------
    int
        ``0`` when the file is on the desk, ``2`` when the server refused or the download failed.
    """

    from creation.ops.folder import next_versioned_path
    from creation.post.server_finish import ServerFinishError, download_file

    desk = desk.expanduser().resolve()
    answer = _ask(
        desk, episode, EXPORT_ROUTE, {"version": EXPORT_VERSION}, out=out, api=api,
        older=_no_letterboxed_export,
    )  # fmt: skip
    if answer is None:
        return 2
    url = answer.get("export_url") if isinstance(answer, Mapping) else None
    if not isinstance(url, str) or not url:
        print("Stopped: the server's answer named no file to save.", file=out)
        return 2
    takes = desk / f"ep{episode:02d}" / "takes"
    takes.mkdir(parents=True, exist_ok=True)
    try:
        saved = download_file(
            url, next_versioned_path(takes, f"phone-frame-ep{episode:02d}", ".mp4"),
            transport=transport, kind="phone-frame copy",
            meta={"episode": episode, "export_url": url, "version": EXPORT_VERSION},
            again=f"phone-frame-copy --desk {desk} --episode {episode} --save",
        )  # fmt: skip
    except ServerFinishError as exc:
        print(f"Stopped: {exc}", file=out)
        return 2
    print(
        f"Saved the phone-frame copy with the Sokii mark: {saved}. Post this file on Instagram and TikTok.",
        file=out,
    )
    return 0


def _ask(
    desk: Path,
    episode: int,
    route: str,
    body: dict[str, Any],
    *,
    out: TextIO,
    api: Any,
    idempotency_key: str | None = None,
    older: Callable[[int, Any], bool] | None = None,
) -> Any:
    """POST ``body`` to the episode's ``route``; the answer, or ``None`` after printing why not.

    ``older`` tells a server without the route from a refusal (default :func:`_no_route`).
    """

    from creation.post.desk import open_api
    from creation.post.server_finish import _desk_episode_id
    from creation.post.take_handles import take_coordinate

    where = take_coordinate(desk, episode, "t1")
    if where is None:
        print(
            f"Stopped: the desk has no filmed episode {episode}, so the server cannot find it. "
            "Nothing was sent.",
            file=out,
        )
        return None
    path = route.format(
        job_id=where.video_job_id, episode_id=_desk_episode_id(desk, episode)
    )
    run = api if api is not None else open_api(desk, episode)
    try:
        status, answer = run.post_optional(path, body, idempotency_key=idempotency_key)
    finally:
        if api is None:
            run.client.close()
    if (older or _no_route)(status, answer):
        print(
            "Stopped: this Drama API has no phone-frame copy yet (an older deploy). "
            "Nothing was made or charged.",
            file=out,
        )
        return None
    if not 200 <= status < 300:
        print(
            f"The server refused the phone-frame copy (HTTP {status}): {api_error_text(answer)}",
            file=out,
        )
        return None
    return answer


def _no_route(status: int, answer: Any) -> bool:
    return status in (404, 405) and _error_code(answer) is None


def _no_letterboxed_export(status: int, answer: Any) -> bool:
    """A server whose export knows no ``letterboxed_copy`` version refuses the field as invalid."""

    return _no_route(status, answer) or (
        status == 422
        and _error_code(answer) in (None, "invalid_request")
        and "version" in api_error_text(answer)
    )


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


__all__ = [
    "EXPORT_ROUTE",
    "PHONE_FRAME_COPY_ROUTE",
    "copy_request",
    "run_phone_frame_copy",
    "run_save_phone_frame_copy",
]
