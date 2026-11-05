"""The local voice-source preset names, kept so ``finish`` knows a file the old ``voice-fx`` treated.

``voice-fx`` is a take edit on the server now (``voice_fx[]`` on the take's
post edit, :mod:`creation.post.take_edit`): the server treats the stretch,
stores the take and joins the episode again. Nothing here edits a file.
"""

from __future__ import annotations

PRESET_NAMES: tuple[str, ...] = ("intercom", "phone", "radio")

__all__ = ["PRESET_NAMES"]
