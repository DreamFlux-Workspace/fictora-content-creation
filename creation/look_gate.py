"""The look gate on a desk: which look frames were drawn, which one is newest, and whether the human said yes.

``look-frame`` saves ``shared/look/look-frame-vN.<ext>`` and records the frame's
stored URL in ``shared/look/look-frames.json``. The yes is ``series.look`` in
``series.json``: the same record ``fictora-ops approve --gate look`` writes and
preflight reads before filming. A desk that never drew a look frame has no look
gate here (it may use a preset, or a URL pinned by hand).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from creation.ops.state import load_series

LOOK_DIR = ("shared", "look")
LOOK_FRAME_STEM = "look-frame"
LOOK_FRAME_INDEX = "look-frames.json"
_FRAME_RE = re.compile(rf"^{LOOK_FRAME_STEM}-v(\d+)\.(png|jpe?g|webp)$", re.IGNORECASE)


def look_dir(desk: Path) -> Path:
    """Return the desk's ``shared/look`` folder."""

    return desk.joinpath(*LOOK_DIR)


def drawn_look_frames(desk: Path) -> list[Path]:
    """Return the desk's drawn look frames, oldest version first.

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    list[Path]
        ``shared/look/look-frame-vN.*`` files sorted by ``N``.
    """

    folder = look_dir(desk)
    if not folder.is_dir():
        return []
    found = [
        (int(match.group(1)), path)
        for path in folder.iterdir()
        if path.is_file() and (match := _FRAME_RE.match(path.name))
    ]
    return [path for _, path in sorted(found)]


def newest_look_frame(desk: Path) -> Path | None:
    """Return the newest drawn look frame, or ``None`` when none was drawn."""

    frames = drawn_look_frames(desk)
    return frames[-1] if frames else None


def _index(desk: Path) -> dict[str, str]:
    path = look_dir(desk) / LOOK_FRAME_INDEX
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def record_look_frame_url(desk: Path, frame: Path, url: str) -> None:
    """Remember which stored URL a saved look frame came from.

    Parameters
    ----------
    desk
        Series desk.
    frame
        The saved ``look-frame-vN`` file.
    url
        The server's ``image_url`` for it.
    """

    index = _index(desk)
    index[frame.name] = url
    path = look_dir(desk) / LOOK_FRAME_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")


def look_frame_url(desk: Path, frame: Path) -> str | None:
    """Return the stored URL of a saved look frame, or ``None`` when the desk does not know it.

    Frames drawn before the index existed fall back to the look-frame answer
    with the same version (``api/look-frame-vN.json``).

    Parameters
    ----------
    desk
        Series desk.
    frame
        A ``look-frame-vN`` file on the desk.

    Returns
    -------
    str or None
        The https URL the frame was downloaded from.
    """

    known = _index(desk).get(frame.name)
    if known:
        return known
    match = _FRAME_RE.match(frame.name)
    if not match:
        return None
    answer = desk / "api" / f"{LOOK_FRAME_STEM}-v{match.group(1)}.json"
    if not answer.is_file():
        return None
    url = json.loads(answer.read_text(encoding="utf-8")).get("image_url")
    return str(url) if isinstance(url, str) and url.startswith("https://") else None


def look_approved(desk: Path) -> bool:
    """Return whether ``series.look`` is approved (as ``fictora-ops`` records and preflight reads it)."""

    return load_series(desk).look.status == "approved"


def pinned_look_url(desk: Path) -> str | None:
    """Return the look URL the server held when the desk last saved the story (``api/spine.json``)."""

    path = desk / "api" / "spine.json"
    if not path.is_file():
        return None
    url = json.loads(path.read_text(encoding="utf-8")).get("look_register_url")
    return str(url) if isinstance(url, str) and url else None


def look_gate_refusal(desk: Path) -> str | None:
    """Return why paid drawing must wait for the look yes, or ``None`` when it may go ahead.

    Only a desk that drew a look frame is held: the human is choosing a look,
    and plates or boards drawn before the yes carry the wrong one. A desk that
    never drew a look frame is not held.

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    str or None
        The refusal to print, naming the frame and the approve command.
    """

    frame = newest_look_frame(desk)
    if frame is None or look_approved(desk):
        return None
    return (
        f"Refused: the look gate is open. {frame.relative_to(desk)} was drawn but the look is not approved, "
        "so plates and boards would be paid for before the look is chosen. Nothing was sent. "
        f"Show the frame; after the human's yes: fictora-produce approve --desk {desk} --gate look "
        "(the newest frame; --path or --url for another). Then run step again."
    )
