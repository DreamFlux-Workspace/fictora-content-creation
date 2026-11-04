"""``fictora-produce music-switch``: read or set where episode music is on, on the server.

The server keeps the switch in its database and only admins may read or set it
(``GET``/``PUT /v1/internal/music-switch``): off, on for everyone, or on for the
listed accounts only. The kit keeps nothing. It sends the admin's email from
``--as`` with the kit's service token and prints the server's answer in plain
English. It never reads a Railway variable.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

import httpx

from creation.harness.credentials import env_drama_api_credentials
from creation.harness.http_util import envelope_error, older_server

ROUTE = "/v1/internal/music-switch"
REPO_ROOT = Path(__file__).resolve().parents[1]
NOT_DONE = 2
HISTORY_SHOWN = 5

#: ``(method, url, headers, body) -> (status, json body)``; tests pass a fake.
Send = Callable[
    [str, str, Mapping[str, str], Mapping[str, Any] | None], tuple[int, Any]
]


def _send(
    method: str, url: str, headers: Mapping[str, str], body: Mapping[str, Any] | None
) -> tuple[int, Any]:
    response = httpx.request(
        method, url, headers=dict(headers), json=body, timeout=30.0
    )
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


def _when(raw: Any) -> str:
    try:
        return datetime.fromisoformat(str(raw)).strftime("%d %b %Y %H:%M UTC")
    except ValueError:
        return str(raw)


def where_music_is_on(mode: Any, accounts: Sequence[Any]) -> str:
    """One sentence for a switch setting."""

    if mode == "everyone":
        return "Episode music is on for everyone."
    if mode == "canary":
        return f"Episode music is on only for these accounts: {', '.join(str(a) for a in accounts)}."
    return "Episode music is off for everyone."


def switch_lines(view: Mapping[str, Any]) -> list[str]:
    """The setting, who changed it last, and the recent changes, as the admin reads them."""

    lines = [where_music_is_on(view.get("mode"), view.get("accounts") or [])]
    if view.get("changed_by"):
        lines.append(
            f"Last changed by {view['changed_by']} on {_when(view.get('changed_at'))}."
        )
    else:
        lines.append("Nobody has changed it yet, so it is off (the default).")
    history = [item for item in view.get("history") or [] if isinstance(item, Mapping)]
    if len(history) > 1:
        lines.append("Recent changes, newest first:")
        for item in history[:HISTORY_SHOWN]:
            setting = where_music_is_on(item.get("mode"), item.get("accounts") or [])
            lines.append(
                f"  {_when(item.get('changed_at'))}, {item.get('changed_by')}: {setting}"
            )
    return lines


def refusal(status: int, body: Any, email: str) -> str:
    """What went wrong, in plain English."""

    error = envelope_error(body)
    message = error.get("message") if error else None
    if older_server(status, body):
        return "This server has no music switch yet. Nothing was changed."
    if status == 403:
        return f"Only admins can read or change the music switch, and {email} is not one. Nothing was changed."
    if status == 401:
        return "The server did not accept the kit's token or the email. Nothing was changed."
    if status == 503:
        return "The server cannot reach its database for the music switch right now. Nothing was changed."
    if status == 422:
        return f"The server refused that setting ({message or 'it is not a valid setting'}). Nothing was changed."
    return f"The server answered HTTP {status}. Nothing was changed."


def run_music_switch(
    action: str,
    accounts: Sequence[str] = (),
    *,
    email: str,
    out: TextIO | None = None,
    send: Send = _send,
    env_file: Path | None = None,
) -> int:
    """Show the switch (``status``) or set it (``on``, ``off``, ``canary``) and print where music is on.

    Parameters
    ----------
    action
        ``status``, ``on`` (everyone), ``off``, or ``canary`` (only ``accounts``).
    accounts
        Account ids for ``canary``.
    email
        The admin's email, sent as the signed-in user.
    out
        Text stream (stdout by default).
    send
        The HTTP call; tests pass a fake.
    env_file
        Dotenv file with the API address and token (the repo's ``.env`` by default).

    Returns
    -------
    int
        ``0`` shown or set; ``2`` when nothing was shown or changed.
    """

    out = out or sys.stdout
    if action == "canary" and not [a for a in accounts if a.strip()]:
        print(
            "canary needs at least one account id: music-switch canary ACCOUNT_ID ...",
            file=out,
        )
        return NOT_DONE
    if action != "canary" and accounts:
        print("Only canary takes account ids.", file=out)
        return NOT_DONE
    base_url, token = env_drama_api_credentials(env_file or REPO_ROOT / ".env")
    if not token:
        print(
            "Set FICTORA_DRAMA_GENERATION_SERVICE_TOKEN in the kit's .env first.",
            file=out,
        )
        return NOT_DONE
    headers = {"Authorization": f"Bearer {token}", "X-Fictora-User-Email": email}
    url = base_url.rstrip("/") + ROUTE
    if action == "status":
        status, body = send("GET", url, headers, None)
    else:
        mode = {"on": "everyone", "off": "off", "canary": "canary"}[action]
        wanted: dict[str, Any] = {"mode": mode}
        if mode == "canary":
            wanted["accounts"] = [a.strip() for a in accounts if a.strip()]
        status, body = send("PUT", url, headers, wanted)
    if status >= 400 or not isinstance(body, Mapping):
        print(refusal(status, body, email), file=out)
        return NOT_DONE
    if action != "status":
        print(
            "Changed on the server. Every server picks it up within half a minute.",
            file=out,
        )
    for line in switch_lines(body):
        print(line, file=out)
    return 0
