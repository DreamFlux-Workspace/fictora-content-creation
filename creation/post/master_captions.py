"""The ``.ass`` beside a finished master holds its dialogue captions AND the text drawn over them (L-20261006-8).

Every tool after ``finish`` reads the master's captions from the ``.ass``
beside it (``master.with_suffix(".ass")``): the reel, the trim-handle cut, an
edit's caption carry, a hand re-burn. ``finish`` burns the captions first and
then, on some takes, more text over them (the hook card on an episode's first
take, the system panels). Those steps wrote their own burn file beside their
own video, and that video became the master, so the master's ``.ass`` held
only the hook card (or the panels): a 64 px re-burn from it dropped every
dialogue caption (L-20261006-8, L-20261005-7), and the reel read the hook card
as the take's only caption.

Now each overlay step burns from its own ``-overlay`` file, and the ``.ass``
beside its video is the captions file with the overlay's events added: the
captions keep their styles and cues; the overlay's events follow with their
own styles, a higher layer (drawn on top, as burned), and the event name
:data:`OVERLAY_NAME`. A re-burn of that file draws both; every caption reader
(:func:`creation.post.reel.parse_ass_cues`) skips the overlay events, so the
captions it reads are the dialogue captions only.

A tooling fix: the burned pictures are unchanged, so it applies to every desk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The ``Name`` field of an event drawn over the captions (hook card, panels): not a caption.
OVERLAY_NAME = "overlay"
#: Overlay events are lifted this many layers above the captions (they were burned on top).
OVERLAY_LAYER_LIFT = 10

_SECTION = re.compile(r"^\[(.+)\]\s*$")


@dataclass(frozen=True)
class _Ass:
    info: list[str]
    styles_format: str | None
    styles: list[str]
    events_format: str | None
    events: list[str]

    def play_res(self) -> tuple[str | None, str | None]:
        def value(key: str) -> str | None:
            for line in self.info:
                if line.lower().startswith(key.lower() + ":"):
                    return line.split(":", 1)[1].strip()
            return None

        return value("PlayResX"), value("PlayResY")


def _read(text: str) -> _Ass:
    section = ""
    info: list[str] = []
    styles: list[str] = []
    events: list[str] = []
    styles_format = events_format = None
    for line in text.splitlines():
        found = _SECTION.match(line.strip())
        if found:
            section = found.group(1).strip().lower()
            continue
        if not line.strip():
            continue
        if section == "script info":
            info.append(line)
        elif section in ("v4+ styles", "v4 styles"):
            if line.startswith("Format:"):
                styles_format = line
            elif line.startswith("Style:"):
                styles.append(line)
        elif section == "events":
            if line.startswith("Format:"):
                events_format = line
            elif line.startswith(("Dialogue:", "Comment:")):
                events.append(line)
    return _Ass(info, styles_format, styles, events_format, events)


def _style_name(line: str) -> str:
    return line.split(":", 1)[1].split(",", 1)[0].strip()


def _as_overlay(event: str) -> str:
    """The event with its layer lifted and its name set to :data:`OVERLAY_NAME`."""

    head, rest = event.split(":", 1)
    parts = rest.split(",", 9)
    if len(parts) < 10:
        return event
    try:
        parts[0] = f" {int(parts[0].strip()) + OVERLAY_LAYER_LIFT}"
    except ValueError:
        pass
    parts[4] = OVERLAY_NAME
    return f"{head}:{','.join(parts)}"


def is_overlay_event(fields: list[str]) -> bool:
    """True for an event drawn over the captions (its ``Name`` field, fields split after ``Dialogue:``)."""

    return len(fields) > 4 and fields[4].strip() == OVERLAY_NAME


def combined_ass(captions: str | None, overlay: str) -> str:
    """The captions file with the overlay's events (and styles) added after them.

    Parameters
    ----------
    captions
        The ``.ass`` text the overlay was burned over (the captions, maybe with
        an earlier overlay already in it), or ``None`` when the take has no
        captions (``--caption-style none``, a wordless take).
    overlay
        The overlay's own burn file.

    Returns
    -------
    str
        The file to keep beside the overlay's video.

    Raises
    ------
    ValueError
        When the two files are drawn on different canvases (``PlayResX/Y``).
    """

    over = _read(overlay)
    if captions is None:
        base = _Ass(over.info, over.styles_format, [], over.events_format, [])
    else:
        base = _read(captions)
        if base.play_res() != over.play_res():
            raise ValueError(
                f"the captions are drawn on {base.play_res()} and the overlay on {over.play_res()}"
            )
    names = {_style_name(line) for line in base.styles}
    styles = [*base.styles, *(s for s in over.styles if _style_name(s) not in names)]
    events = [*base.events, *(_as_overlay(e) for e in over.events)]
    out = ["[Script Info]", *base.info, "", "[V4+ Styles]"]
    if base.styles_format or over.styles_format:
        out.append(base.styles_format or over.styles_format or "")
    out += [*styles, "", "[Events]"]
    if base.events_format or over.events_format:
        out.append(base.events_format or over.events_format or "")
    out += events
    return "\n".join(out) + "\n"


def write_beside(video: Path, *, captions: Path | None, overlay: Path) -> Path:
    """Write ``video``'s ``.ass``: ``captions`` with ``overlay``'s events added (:func:`combined_ass`)."""

    target = video.with_suffix(".ass")
    text = combined_ass(
        captions.read_text(encoding="utf-8") if captions is not None else None,
        overlay.read_text(encoding="utf-8"),
    )
    target.write_text(text, encoding="utf-8")
    return target


__all__ = [
    "OVERLAY_LAYER_LIFT",
    "OVERLAY_NAME",
    "combined_ass",
    "is_overlay_event",
    "write_beside",
]
