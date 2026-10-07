"""Say the server's out-of-date-board refusals with the commands that clear them (fictora-drama #641).

On a story created since 6 Oct 2026 the server marks a take whose script was
edited after its board was drawn, and refuses to film or estimate it, free:

* ``board_behind_wording_edit``: only a line's words changed (a typo fix, a
  re-pinned Korean or Japanese line). The board doesn't show the words, so
  the recommended fix is to approve the board again (free) and film it as it
  is; a redraw ($0.30) is the alternative, only when the new line changes
  what the take should show (founder decision, 7 Oct 2026).
* ``board_behind_script_edit``: what happens in the take changed (action,
  shot plan, reaction, who speaks, on/off screen, a line added or removed).
  Only a redraw clears it.

Each 422 names its takes in ``details.takes[]`` (``episode_ordinal``,
``set_index``; the wording one also ``changed_lines[]`` with ``before`` and
``after``). The server's own message already says what changed; this adds the
commands to paste.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Only a line's words changed: approve the board again (recommended) or redraw.
BOARD_BEHIND_WORDING_EDIT = "board_behind_wording_edit"
#: The take's staging changed: redraw.
BOARD_BEHIND_SCRIPT_EDIT = "board_behind_script_edit"


def _takes(details: Mapping[str, Any] | None) -> list[tuple[Any, Any]]:
    rows: list[tuple[Any, Any]] = []
    for take in (details or {}).get("takes") or []:
        if isinstance(take, Mapping) and take.get("set_index") is not None:
            rows.append((take.get("episode_ordinal") or "N", take["set_index"]))
    return rows


def _redraw(desk: str, episode: Any, set_index: Any) -> str:
    return f"`fictora-produce redraw-board --desk {desk} --episode {episode} --take t{set_index}`"


def board_edit_choice(
    code: Any, details: Mapping[str, Any] | None, *, desk: str = "D"
) -> str | None:
    """The commands for an out-of-date-board refusal, or ``None`` for any other error.

    Parameters
    ----------
    code
        The refusal's ``code``.
    details
        Its ``details`` (``{"takes": [...]}``), or ``None``.
    desk
        The desk to put in the commands (``D`` when the caller has none).

    Returns
    -------
    str | None
        For ``board_behind_wording_edit``: the approve command first
        (recommended, free), then a redraw command per take as the
        alternative. For ``board_behind_script_edit``: a redraw command per
        take. ``None`` otherwise.
    """

    takes = _takes(details) or [("N", "K")]
    if str(code or "") == BOARD_BEHIND_WORDING_EDIT:
        rows = [
            "  Recommended (free): keep the board and film it as it is. The board doesn't show the words: "
            f"`fictora-produce approve --desk {desk} --gate board`, then film again.",
            "  Or, only if the new line changes what the take should show, redraw it ($0.30):",
        ]
        rows += [f"    {_redraw(desk, episode, index)}" for episode, index in takes]
        return "\n".join(rows)
    if str(code or "") == BOARD_BEHIND_SCRIPT_EDIT:
        rows = [
            "  What happens in the take changed, so only a redraw clears it ($0.30 each):"
        ]
        rows += [f"    {_redraw(desk, episode, index)}" for episode, index in takes]
        rows.append("  Then approve the boards and film again.")
        return "\n".join(rows)
    return None


__all__ = [
    "BOARD_BEHIND_SCRIPT_EDIT",
    "BOARD_BEHIND_WORDING_EDIT",
    "board_edit_choice",
]
