"""No em or en dash in any burned caption (user rule, 6 Oct 2026).

The script and the voice keep their dashes (the voice reads a dash as a
cut-off); only the text drawn on the picture changes, at the point it becomes
a caption cue, after timing, so word timings are untouched:

- A dash ending the cue ("Please—", "no no—", "I'm NOT—") is an ellipsis:
  "Please…" (the single character the captions already use).
- A dash between words ("D-9341 — do not", "wait—what") is a comma: "D-9341,
  do not", "wait, what".
- A dash opening the cue is dropped.

Em, en, figure and horizontal-bar dashes, two- and three-em dashes, ``--``
and a spaced hyphen (" - ") all count, spaced or not. A hyphen inside a word
(D-9341, well-known, b-but) is untouched.

This mirrors fictora-drama ``caption_dashes.py``.
"""

from __future__ import annotations

import re

ELLIPSIS = "…"
#: The dash characters (figure, en, em, horizontal bar, two-em, three-em) and a run of 2+ hyphens.
_DASH = "(?:[‒–—―⸺⸻]+|-{2,})"
#: Closing quotes and brackets that may follow a cut-off.
_CLOSE = "\"'”’)\\]»"
#: Opening quotes and brackets that may come before an opening dash.
_OPEN = "\"'“‘(\\[«"

#: A lone hyphen with a space before it and a space (or the end) after it is a dash.
_SPACED_HYPHEN = re.compile(r"(?<=[ \t])-(?=[ \t]|$)")
_LEADING = re.compile(rf"^([{_OPEN}]*)[ \t]*{_DASH}[ \t]*")
#: A dash (and any dashes after it) before closing marks and a sentence end: the end mark stays.
_BEFORE_END_MARK = re.compile(
    rf"[ \t]*{_DASH}(?:[ \t]*{_DASH})*[ \t]*(?=[{_CLOSE}]*[.!?{ELLIPSIS}])"
)
_TRAILING = re.compile(rf"\s*{_DASH}(?:[ \t]*{_DASH})*[ \t]*([{_CLOSE}]*)[ \t]*$")
#: A dash between words; a line break either side of it stays (after the comma).
_MIDDLE = re.compile(rf"(\s*){_DASH}(?:[ \t]*{_DASH})*([{_CLOSE}]*)[ \t]*(\n?)")
_PUNCT_THEN_COMMA = re.compile(rf"([,;:.!?{ELLIPSIS}])[ \t]*,")
_COMMA_THEN_PUNCT = re.compile(rf",[ \t]*([,;:.!?{ELLIPSIS}])")
_ELLIPSES = re.compile(rf"(?:\.\.\.|{ELLIPSIS}){{2,}}")
_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_BREAK = re.compile(r"[ \t]+\n")
#: Any dash this module turns into punctuation (for checks: a caption must contain none).
DASH_IN_CAPTION = re.compile(_DASH)


def _middle(match: re.Match[str]) -> str:
    lead, close, newline = match.group(1), match.group(2), match.group(3)
    return "," + close + ("\n" if newline or "\n" in lead else " ")


def caption_text(text: str) -> str:
    """Return ``text`` as a caption draws it: no em or en dash (see the module notes).

    Parameters
    ----------
    text
        One cue's text (it may hold line breaks).

    Returns
    -------
    str
        The text with every dash an ellipsis (end), a comma (between words) or
        gone (opening); hyphens inside words untouched. Idempotent.
    """

    out = _SPACED_HYPHEN.sub("—", text)
    if not DASH_IN_CAPTION.search(out):
        return text
    out = _LEADING.sub(r"\1", out)
    out = _BEFORE_END_MARK.sub("", out)
    out = _TRAILING.sub(lambda m: ELLIPSIS + m.group(1), out)
    out = _MIDDLE.sub(_middle, out)
    out = _PUNCT_THEN_COMMA.sub(r"\1", out)
    out = _COMMA_THEN_PUNCT.sub(r"\1", out)
    out = _ELLIPSES.sub(ELLIPSIS, out)
    out = _SPACES.sub(" ", out)
    out = _SPACE_BEFORE_BREAK.sub("\n", out)
    return out.strip()


def drawn_text(text: str) -> str:
    """``text`` as the picture draws it under the running desk's rules.

    A desk created before 6 Oct 2026 keeps its dashes, as its captions always
    had them (:func:`creation.rules_epoch.legacy_rules`; frozen for those
    desks, do not change); every other desk gets :func:`caption_text`.
    """

    from creation.rules_epoch import legacy_rules

    return text if legacy_rules() else caption_text(text)


__all__ = ["DASH_IN_CAPTION", "ELLIPSIS", "caption_text", "drawn_text"]
