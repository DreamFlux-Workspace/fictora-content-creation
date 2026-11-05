"""The local voice-source presets' names, kept so ``finish`` knows a file the old ``voice-fx`` treated.

``voice-fx`` is a take edit on the server now (``voice_fx[]`` on the take's
post edit, :mod:`creation.post.take_edit`): the server treats the stretch,
stores the take and joins the episode again. Nothing here edits a file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    """One source treatment: an ffmpeg audio chain plus a static level."""

    chain: str
    static: float


PRESETS: dict[str, Preset] = {
    "intercom": Preset(
        "highpass=f=450,highpass=f=450,lowpass=f=2800,lowpass=f=2800,"
        "acompressor=threshold=0.08:ratio=6:attack=5:release=60:makeup=2,"
        "asoftclip=type=tanh,aecho=0.8:0.55:13|21:0.32|0.18",
        0.004,
    ),
    "phone": Preset(
        "highpass=f=300,highpass=f=300,lowpass=f=3400,lowpass=f=3400,"
        "acompressor=threshold=0.1:ratio=4:attack=5:release=80:makeup=2,asoftclip=type=tanh",
        0.002,
    ),
    "radio": Preset(
        "highpass=f=500,highpass=f=500,lowpass=f=2600,lowpass=f=2600,"
        "acompressor=threshold=0.06:ratio=8:attack=3:release=50:makeup=3,asoftclip=type=atan",
        0.008,
    ),
}


__all__ = ["PRESETS", "Preset"]
