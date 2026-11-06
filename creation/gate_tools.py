"""Which command records which gate's yes, and the message when the other one is used.

The pitch yes (``--gate pitch``) is recorded by ``fictora-produce``; ``fictora-ops``
names the line to run and records nothing. Both accept ``--gate post`` only to say there
is no "this episode is final" step: the newest copy saved is the one to post.
"""

from __future__ import annotations

import argparse
import shlex
from typing import Final

#: The episode-is-final gate: answered with :data:`NO_FINAL_STEP`.
POST_GATE: Final = "post"
#: The pitch-card gate: ``fictora-produce`` only.
PITCH_GATE: Final = "pitch"
#: What ``approve --gate post`` says: nothing to approve, nothing recorded.
NO_FINAL_STEP: Final = (
    'There is no "this episode is final" step. The newest copy saved for the episode '
    "is the copy to post on Instagram and TikTok: the copy the status list shows for each episode. "
    "Nothing was recorded."
)


def _command_line(tool: str, args: argparse.Namespace) -> str:
    parts = [tool, "approve", "--desk", str(args.desk), "--gate", str(args.gate)]
    episode = getattr(args, "episode", None)
    if episode is not None:
        parts += ["--episode", str(episode)]
    return " ".join(shlex.quote(part) for part in parts)


def wrong_tool_for_gate(args: argparse.Namespace) -> str:
    """Return the message for an ``approve`` sent to the command that does not record that gate.

    Parameters
    ----------
    args
        The parsed ``approve`` arguments (``desk``, ``gate``, and ``episode`` /
        ``path`` when given).

    Returns
    -------
    str
        What happened (nothing recorded) and the exact command to run instead.
    """

    # fictora-produce's pitch gate defaults to the desk's current episode.
    line = _command_line("fictora-produce", args)
    return (
        f"The --gate {args.gate} yes is recorded by fictora-produce, not fictora-ops. "
        f"Nothing was recorded. Run:\n  {line}"
    )
