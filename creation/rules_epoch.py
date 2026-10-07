"""Which rules a desk runs under: desks created before 6 Oct 2026 keep their original behaviour.

User decision (6 Oct 2026, "only for new desks"): every kit change made on
6 Oct 2026 applies to NEW desks only, bug and safety fixes included. A desk
created before that day (a **legacy** desk) must produce exactly what it
produced before. One rules epoch per desk, decided here and nowhere else;
never an environment switch.

How a desk is classified (first signal that answers wins):

1. ``rules_epoch`` in the desk's ``production.config.json``: ``"legacy"``, or
   a date (``"2026-10-06"``). ``start`` stamps :data:`CURRENT_EPOCH` on every
   new desk; ``fictora-produce rules-epoch --desk D --set legacy|2026-10-06``
   sets it by hand (an operator may opt an older desk in).
2. ``day`` in ``series.json``: the local date the kit created the desk
   (``init_series_desk`` writes ``date.today()``; every kit desk has it).
3. The desk folder's date prefix (``2026-09-26-scp-173``): the same date, for a
   desk whose ``series.json`` is missing or unreadable.

A date before :data:`EPOCH_DATE` is legacy. A desk none of these date (not a
kit desk: no stamp, no ``series.json`` day, no dated folder) runs today's
rules.

Inspected on the real desks (6 Oct 2026, read only): every desk under
``~/Downloads/documents`` has a ``YYYY-MM-DD-`` folder prefix; desks made by
``start`` / ``init-series`` also carry the same date as ``series.json`` ``day``
(desks from before ``day`` was written have ``""``); none has a created
timestamp in ``production.json`` or ``production.config.json``.

Code that has the desk asks :func:`is_legacy`. Pure text and picture helpers
that never see a desk (dash removal, the face detector, the reel planner's
genre words) ask :func:`legacy_rules`, which is what the command running now
set with :func:`desk_rules` (``fictora-produce`` sets it once per command from
``--desk``; ``run_finish``, ``run_reel`` and ``caption_take`` set it too, so a
direct call is covered).

The legacy code paths these gates pick are frozen for desks created before
2026-10-06: do not change them.

Fixes that also reach continuing desks (founder decision, 7 Oct 2026): a gate
for a fix that changes no voice, character or art style asks
:func:`continuing_fix` instead of :func:`is_legacy`, and the fix reaches a
legacy desk only while its name is in :data:`CONTINUING_FIXES`. Every other
gate stays legacy. To approve another fix later, add its name to the
allow-list and swap its gate. (Do not use ``rules-epoch --set``: that opts a
desk into every 6 Oct change.)
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import json
import re
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

#: The day the new rules start: a desk created before it is legacy.
EPOCH_DATE = date(2026, 10, 6)
#: What ``start`` stamps on a new desk (``production.config.json`` ``rules_epoch``).
CURRENT_EPOCH = EPOCH_DATE.isoformat()
#: The explicit value for a desk that keeps its original behaviour.
LEGACY = "legacy"
#: The values ``rules-epoch --set`` takes.
EPOCH_CHOICES = (LEGACY, CURRENT_EPOCH)

#: The 6 Oct fixes a legacy desk also gets (founder decision, 7 Oct 2026, after
#: a risk review: "Group A", fixes that change no voice, character or art
#: style). Mirrors fictora-drama ``rules_epoch.CONTINUING_FIXES``.
CONTINUING_FIXES: frozenset[str] = frozenset(
    {
        # Seam fix: join lays a steady bed under a loud seam and trims a near-silent head on a filmed cut (#156). Approved 2026-10-07.
        "seam_fix",
    }
)

_FOLDER_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
_ACTIVE: ContextVar[bool] = ContextVar("fictora_legacy_rules", default=False)

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class DeskEpoch:
    """A desk's rules epoch and the signal it came from."""

    #: ``legacy`` or :data:`CURRENT_EPOCH`.
    epoch: str
    #: ``stamp`` (production.config.json), ``series day``, ``folder date`` or ``none``.
    source: str
    #: The date or stamp read (``""`` with ``none``).
    value: str = ""

    @property
    def legacy(self) -> bool:
        return self.epoch == LEGACY


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def _stamp(desk: Path) -> str | None:
    path = desk / "production.config.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = raw.get("rules_epoch") if isinstance(raw, dict) else None
    return str(value).strip() if value else None


def _series_day(desk: Path) -> str | None:
    path = desk / "series.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = raw.get("day") if isinstance(raw, dict) else None
    return str(value).strip() if value and _date(str(value)) else None


def _epoch_for(day: date) -> str:
    return LEGACY if day < EPOCH_DATE else CURRENT_EPOCH


def desk_epoch(desk: Path) -> DeskEpoch:
    """Classify a desk (see the module notes for the order of the signals).

    Parameters
    ----------
    desk
        Series desk folder.

    Returns
    -------
    DeskEpoch
        Its epoch and where that came from.
    """

    desk = Path(desk).expanduser().resolve()
    stamp = _stamp(desk)
    if stamp:
        if stamp.lower() == LEGACY:
            return DeskEpoch(LEGACY, "stamp", stamp)
        stamped = _date(stamp)
        if stamped is not None:
            return DeskEpoch(_epoch_for(stamped), "stamp", stamp)
    day = _series_day(desk)
    if day:
        return DeskEpoch(_epoch_for(_date(day)), "series day", day)  # type: ignore[arg-type]
    match = _FOLDER_DATE.match(desk.name)
    folder = _date(match.group(1)) if match else None
    if folder is not None:
        return DeskEpoch(_epoch_for(folder), "folder date", match.group(1))  # type: ignore[union-attr]
    return DeskEpoch(CURRENT_EPOCH, "none")


def is_legacy(desk: Path | None) -> bool:
    """True when the desk was created before 6 Oct 2026 and keeps its original behaviour.

    ``None`` (no desk) is never legacy.
    """

    return desk is not None and desk_epoch(desk).legacy


def continuing_fix(desk: Path | None, name: str) -> bool:
    """True when the 6 Oct fix ``name`` applies to this desk.

    Always True for a new desk (and for no desk); for a desk created before
    6 Oct 2026 only while ``name`` is in :data:`CONTINUING_FIXES` (founder
    decision, 7 Oct 2026).

    Parameters
    ----------
    desk
        Series desk, or ``None``.
    name
        The fix's name, as listed in :data:`CONTINUING_FIXES`.
    """

    return not is_legacy(desk) or name in CONTINUING_FIXES


def legacy_rules() -> bool:
    """The rules of the command running now: True for a legacy desk (:func:`desk_rules`)."""

    return _ACTIVE.get()


@contextlib.contextmanager
def desk_rules(desk: Path | None) -> Iterator[bool]:
    """Run the block under ``desk``'s rules (:func:`legacy_rules` answers for it).

    Parameters
    ----------
    desk
        Series desk; ``None`` (a command with no desk) runs today's rules.

    Yields
    ------
    bool
        Whether the desk is legacy.
    """

    legacy = is_legacy(desk)
    token = _ACTIVE.set(legacy)
    try:
        yield legacy
    finally:
        _ACTIVE.reset(token)


def under_desk_rules(func: F) -> F:
    """Decorator: run ``func`` under the rules of its ``desk`` argument (first positional or ``desk=``)."""

    signature = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        bound = signature.bind_partial(*args, **kwargs)
        desk = bound.arguments.get("desk")
        with desk_rules(Path(desk) if desk is not None else None):
            return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def run_rules_epoch(desk: Path, *, set_to: str | None = None, out: Any = None) -> str:
    """``rules-epoch --desk D [--set legacy|2026-10-06]``: print (or set) the desk's rules epoch (free).

    Parameters
    ----------
    desk
        Series desk.
    set_to
        ``legacy`` or :data:`CURRENT_EPOCH` to store in ``production.config.json``;
        ``None`` only reads.
    out
        Text stream (stdout by default).

    Returns
    -------
    str
        The epoch after the call.
    """

    import sys

    from creation.production_config import (
        load_production_config,
        save_production_config,
    )

    out = out or sys.stdout
    desk = Path(desk).expanduser().resolve()
    if not desk.is_dir():
        raise FileNotFoundError(f"{desk} is not a desk folder")
    if set_to is not None:
        if set_to not in EPOCH_CHOICES:
            raise ValueError(
                f"--set {set_to!r}: choose one of {', '.join(EPOCH_CHOICES)}"
            )
        config = load_production_config(desk)
        config.rules_epoch = set_to
        save_production_config(desk, config)
    found = desk_epoch(desk)
    why = {
        "stamp": f"rules_epoch {found.value!r} in production.config.json",
        "series day": f"created {found.value} (series.json day)",
        "folder date": f"created {found.value} (the folder's date)",
        "none": "no creation date on the desk",
    }[found.source]
    meaning = (
        "keeps its original behaviour (created before 6 Oct 2026)"
        if found.legacy
        else "runs the current rules (6 Oct 2026 on)"
    )
    print(f"Rules epoch: {found.epoch} ({why}): this desk {meaning}.", file=out)
    return found.epoch


__all__ = [
    "CONTINUING_FIXES",
    "CURRENT_EPOCH",
    "EPOCH_CHOICES",
    "EPOCH_DATE",
    "LEGACY",
    "DeskEpoch",
    "continuing_fix",
    "desk_epoch",
    "desk_rules",
    "is_legacy",
    "legacy_rules",
    "run_rules_epoch",
    "under_desk_rules",
]
