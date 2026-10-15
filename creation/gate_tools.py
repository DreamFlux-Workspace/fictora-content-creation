"""Which command records which gate's yes, and the message when the other one is used.

The episode-is-final yes (``--gate post``) is recorded by ``fictora-ops``; the
pitch yes (``--gate pitch``) by ``fictora-produce``. Long Shadow (14 Oct 2026,
L-20261014-18) typed ``fictora-produce approve --gate post`` and got only
argparse's "invalid choice". Each command now names the other one, with the
exact line to run, and records nothing.
"""

from __future__ import annotations

import argparse
import shlex
from typing import Final

#: The episode-is-final gate: ``fictora-ops`` only.
POST_GATE: Final = "post"
#: The pitch-card gate: ``fictora-produce`` only.
PITCH_GATE: Final = "pitch"


def _command_line(tool: str, args: argparse.Namespace, *, with_path: bool) -> str:
    parts = [tool, "approve", "--desk", str(args.desk), "--gate", str(args.gate)]
    episode = getattr(args, "episode", None)
    parts += ["--episode", str(episode) if episode is not None else "N"]
    path = getattr(args, "path", None)
    if with_path and path is not None:
        parts += ["--path", str(path)]
    return " ".join(shlex.quote(part) if part != "N" else part for part in parts)


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

    if args.gate == POST_GATE:
        line = _command_line("fictora-ops", args, with_path=True)
        return (
            "The episode-is-final yes (--gate post) is recorded by fictora-ops, not fictora-produce. "
            f"Nothing was recorded. Run:\n  {line}"
            + (
                ""
                if getattr(args, "path", None) is not None
                else "\n(add --path FILE to mark a file other than the newest joined one)"
            )
        )
    line = _command_line("fictora-produce", args, with_path=False)
    return (
        f"The --gate {args.gate} yes is recorded by fictora-produce, not fictora-ops. "
        f"Nothing was recorded. Run:\n  {line}"
    )
