"""The look gate on a desk: which look frames were drawn, which one is newest, and whether the human said yes.

``look-frame`` saves ``shared/look/look-frame-vN.<ext>`` and records the frame's
stored URL in ``shared/look/look-frames.json``. The yes is ``series.look`` in
``series.json``: the same record ``fictora-ops approve --gate look`` writes and
preflight reads before filming. A desk that never drew a look frame has no look
gate here (it may use a preset, or a URL pinned by hand).

The yes covers the frames on the desk when it was given, not frames drawn
after it. Each yes recorded on the desk writes ``shared/look/look-approval.json``
(its time, the approved frame and URL, and every frame drawn by then). A yes with
no such record (older desks, adopted desks) covers the frame it names and every
frame whose file was written no later than the yes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from creation.ops.state import GateRecord, load_series

LOOK_DIR = ("shared", "look")
LOOK_FRAME_STEM = "look-frame"
LOOK_FRAME_INDEX = "look-frames.json"
LOOK_APPROVAL = "look-approval.json"
_PINNED_NOTE = "pinned "
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


@dataclass(frozen=True)
class _LookYes:
    """What one look yes covers: the frame and URL it names, and the frames drawn by then."""

    frame: str | None
    url: str | None
    drawn: frozenset[str] | None


def _frame_name(recorded: str | None) -> str | None:
    if not recorded:
        return None
    name = Path(recorded).name
    return name if _FRAME_RE.match(name) else None


def record_look_approval(
    desk: Path, record: GateRecord, *, url: str | None = None
) -> None:
    """Write down which frames a look yes covers (``shared/look/look-approval.json``).

    Parameters
    ----------
    desk
        Series desk.
    record
        The approved ``series.look`` record just saved; its ``at_utc`` ties the file to it.
    url
        The approved frame's URL, when known (else read from the named frame).
    """

    frames = drawn_look_frames(desk)
    frame = _frame_name(record.path)
    if url is None and frame is not None:
        url = look_frame_url(desk, look_dir(desk) / frame)
    payload = {
        "at_utc": record.at_utc,
        "frame": frame,
        "url": url,
        "drawn": [path.name for path in frames],
    }
    path = look_dir(desk) / LOOK_APPROVAL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _look_yes(desk: Path, look: GateRecord) -> _LookYes:
    path = look_dir(desk) / LOOK_APPROVAL
    raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    note_url = (
        look.note[len(_PINNED_NOTE) :]
        if look.note and look.note.startswith(_PINNED_NOTE)
        else None
    )
    if isinstance(raw, dict) and raw.get("at_utc") == look.at_utc:
        drawn = raw.get("drawn")
        return _LookYes(
            frame=_frame_name(raw.get("frame")) or _frame_name(look.path),
            url=raw.get("url") or note_url,
            drawn=frozenset(map(str, drawn)) if isinstance(drawn, list) else None,
        )
    return _LookYes(frame=_frame_name(look.path), url=note_url, drawn=None)


def _written_by(frame: Path, at_utc: str | None) -> bool:
    """Return whether ``frame`` was written no later than ``at_utc`` (whole seconds, as the yes is stamped)."""

    if not at_utc:
        return True
    try:
        stamped = datetime.fromisoformat(at_utc)
    except ValueError:
        return True
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    approved = stamped.timestamp()
    return int(frame.stat().st_mtime) <= approved


def unapproved_look_frame(desk: Path) -> Path | None:
    """Return the newest drawn look frame the look yes does not cover, or ``None``.

    With no yes, that is the newest frame. With a yes, a frame is covered when
    it is the approved frame, carries the approved URL (a cached redraw of the
    same description), or was already drawn when the yes was given: listed in
    ``look-approval.json`` when that file belongs to this yes, else its file
    was written no later than the yes (older and adopted desks).

    Parameters
    ----------
    desk
        Series desk.

    Returns
    -------
    Path or None
        The frame still waiting for a yes.
    """

    frames = drawn_look_frames(desk)
    if not frames:
        return None
    look = load_series(desk).look
    if look.status != "approved":
        return frames[-1]
    yes = _look_yes(desk, look)

    def covered(frame: Path) -> bool:
        if frame.name == yes.frame:
            return True
        if yes.url and look_frame_url(desk, frame) == yes.url:
            return True
        if yes.drawn is not None:
            return frame.name in yes.drawn
        return _written_by(frame, look.at_utc)

    waiting = [frame for frame in frames if not covered(frame)]
    return waiting[-1] if waiting else None


def pinned_look_url(desk: Path) -> str | None:
    """Return the look URL the server held when the desk last saved the story (``api/spine.json``)."""

    path = desk / "api" / "spine.json"
    if not path.is_file():
        return None
    url = json.loads(path.read_text(encoding="utf-8")).get("look_register_url")
    return str(url) if isinstance(url, str) and url else None


def look_gate_refusal(desk: Path, *, command: str = "step") -> str | None:
    """Return why paid drawing must wait for the look yes, or ``None`` when it may go ahead.

    Only a desk that drew a look frame is held: the human is choosing a look,
    and plates or boards drawn before the yes carry the wrong one. A desk that
    never drew a look frame is not held. A frame drawn after the yes opens the
    gate again (:func:`unapproved_look_frame`).

    Parameters
    ----------
    desk
        Series desk.
    command
        The paid command that was refused (``step``, ``redraw-plate``, ``redraw-board``).

    Returns
    -------
    str or None
        The refusal to print, naming the frame and the approve command.
    """

    frame = unapproved_look_frame(desk)
    if frame is None:
        return None
    look = load_series(desk).look
    shown = frame.relative_to(desk)
    if look.status == "approved":
        why = (
            f"Refused: the look gate is open again. {shown} was drawn after the look was approved "
            f"({look.path or 'the approved look'}), and that yes does not cover it, "
        )
    else:
        why = f"Refused: the look gate is open. {shown} was drawn but the look is not approved, "
    return (
        why
        + "so plates and boards would be paid for before the look is chosen. Nothing was sent. "
        f"Show the frame; after the human's yes: fictora-produce approve --desk {desk} --gate look "
        f"(the newest frame; --path or --url for another). Then run {command} again."
    )
