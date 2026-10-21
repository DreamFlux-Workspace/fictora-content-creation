"""Attach an existing server story to a new desk, find one by title, and let go of it again.

A story made in the creator app belongs to the creator's app session, and the
server shows it to that session only. The operator kit used to have no way to
work on such a story: ``bind`` and ``start`` only make new stories, and
``adopt-desk`` only opens old internal-kit folders. With the server's operator
routes (fictora-drama ``feat/story-collaborators``) the kit lists the new
desk's session on the story as a collaborator: the creator stays the owner,
so the story stays in their app library, and what the desk writes (a new
episode) shows there too.

Server-heavy: the desk keeps only the ids it needs (the story id and the
desk's session, in ``production.json``; ``api/attach.json`` says the desk was
attached and when it let go). Cast, episodes, show language, art style,
voices and caption look are read from the server; the spine snapshot saved
under ``api/`` is the same cache every other desk keeps.

Commands: ``fictora-produce stories --title T`` (read only),
``fictora-produce start --spine-id ID`` and ``fictora-produce detach --desk D``.
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote, urlencode

from creation.harness.credentials import load_drama_api_credentials
from creation.harness.session import DramaApiRunSession
from creation.ops.floor import init_series_desk
from creation.ops.folder import run_folder_name
from creation.ops.notes import append_run_note
from creation.production_config import ProductionConfig, save_production_config
from creation.production_state import ProductionState, save_production
from creation.rules_epoch import CURRENT_EPOCH, LEGACY

SEARCH_PATH = "/v1/operator/spines"
ATTACH_FILENAME = "attach.json"
BANDS = ("15s", "30s", "60s")
CAPTION_LOOKS = ("bold", "subtle")
#: A server older than the operator routes answers these for the new paths.
_NO_ROUTE = (404, 405)


class AttachRefused(RuntimeError):
    """The server refused (or cannot do) the attach; nothing was left on disk or on the story."""


def collaborators_path(spine_id: str, session_id: str | None = None) -> str:
    """Return the collaborators route for a story (and one session on it).

    Parameters
    ----------
    spine_id
        Story id.
    session_id
        A listed session, for the ``DELETE`` route.

    Returns
    -------
    str
        ``/v1/spines/{id}/collaborators`` or ``…/collaborators/{session}``.
    """

    base = f"/v1/spines/{quote(spine_id, safe='')}/collaborators"
    return base if session_id is None else f"{base}/{quote(session_id, safe='')}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def open_session(session_id: str, out_dir: Path) -> DramaApiRunSession:
    """Open a Drama API session for ``session_id`` (replaced in tests).

    Parameters
    ----------
    session_id
        ``X-Drama-Session-Id`` the calls are made under.
    out_dir
        Where the session would write artefacts (these commands write none).

    Returns
    -------
    DramaApiRunSession
        An open session; close its ``client`` when done.
    """

    base, token = load_drama_api_credentials(_repo_root())
    return DramaApiRunSession(
        base_url=base, token=token, out_dir=out_dir, session_id=session_id
    )


def _error_code(body: Any) -> str:
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping) and error.get("code"):
            return str(error["code"])
        if body.get("detail"):
            return str(body["detail"])
    return str(body)[:200]


def _error_text(status: int, body: Any) -> str:
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            message = error.get("message")
            code = error.get("code")
            return (
                f"HTTP {status} {code}: {message}"
                if message
                else f"HTTP {status} {code}"
            )
    return f"HTTP {status}: {_error_code(body)}"


def _too_old(status: int, body: Any) -> bool:
    return status in _NO_ROUTE and _error_code(body) in (
        "Not Found",
        "Method Not Allowed",
    )


def search_stories(title: str | None, *, session: Any) -> list[dict[str, Any]]:
    """Find stories in the token's tenant by title (``GET /v1/operator/spines``). Spends nothing.

    Parameters
    ----------
    title
        Part of the title (any case); ``None`` lists the newest.
    session
        Open Drama API session (an operator session).

    Returns
    -------
    list[dict[str, Any]]
        One row per story: ``spine_id``, ``title``, ``owner_kind``,
        ``episodes_written``, ``updated_at``.

    Raises
    ------
    AttachRefused
        The server has no operator search route, or refused.
    """

    query = f"?{urlencode({'title': title})}" if title else ""
    status, body = session.get_optional(f"{SEARCH_PATH}{query}")
    if status == 200 and isinstance(body, Mapping):
        return [
            dict(row) for row in body.get("spines") or [] if isinstance(row, Mapping)
        ]
    if _too_old(status, body):
        raise AttachRefused(
            "server too old: this deploy has no operator story search (GET /v1/operator/spines). "
            "Nothing was read. Ask the Fictora team for the story id."
        )
    raise AttachRefused(f"story search refused: {_error_text(status, body)}")


def story_lines(rows: list[dict[str, Any]], *, title: str | None) -> list[str]:
    """Plain lines for ``stories``: one per story, newest first.

    Parameters
    ----------
    rows
        From :func:`search_stories`.
    title
        What was searched for.

    Returns
    -------
    list[str]
        Lines to print.
    """

    if not rows:
        what = f'titled like "{title}"' if title else "in this account"
        return [f"No story {what}. Nothing was read or changed."]
    owners = {
        "app_creator": "app creator",
        "operator": "operator desk",
        "none": "no owner",
    }
    lines = []
    for row in rows:
        lines.append(
            f'{row.get("spine_id")}  "{row.get("title") or ""}"  '
            f"owner: {owners.get(str(row.get('owner_kind')), row.get('owner_kind'))}  "
            f"episodes written: {row.get('episodes_written', 0)}  updated: {row.get('updated_at', '')}"
        )
    lines.append(
        'Attach one to a new desk: uv run fictora-produce start --spine-id ID [--series "Desk name"]'
    )
    return lines


def last_written_episode(spine: Mapping[str, Any]) -> int:
    """The last episode anything was written or drawn for (``0`` for none), as the server counts it.

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` JSON.

    Returns
    -------
    int
        Highest ordinal with beats, frames, a delivered package or an
        authoring state past ``summary_only``.
    """

    ordinals: dict[str, int] = {}
    written: set[str] = set()
    for summary in spine.get("episode_summaries") or []:
        if not isinstance(summary, Mapping):
            continue
        episode_id = str(summary.get("episode_id") or "")
        try:
            ordinals[episode_id] = int(summary.get("ordinal") or 0)
        except (TypeError, ValueError):
            continue
        if summary.get("authoring_state") not in (None, "summary_only"):
            written.add(episode_id)
    for key in ("beats", "frames"):
        for item in spine.get(key) or []:
            if isinstance(item, Mapping) and item.get("episode_id"):
                written.add(str(item["episode_id"]))
    progress = spine.get("progress")
    if isinstance(progress, Mapping):
        for episode_id, value in progress.items():
            if isinstance(value, Mapping) and value.get("package_version"):
                written.add(str(episode_id))
    return max((ordinals.get(episode_id, 0) for episode_id in written), default=0)


def desk_rules_epoch(spine: Mapping[str, Any]) -> str:
    """The kit rules epoch an attached desk runs under, read from the story's own.

    Parameters
    ----------
    spine
        ``GET /v1/spines/{id}`` JSON.

    Returns
    -------
    str
        :data:`CURRENT_EPOCH` for a story stamped on or after it, else
        :data:`LEGACY` (unset, ``legacy`` or an earlier date: the server reads
        those as legacy too).
    """

    value = str(spine.get("rules_epoch") or "")
    return (
        CURRENT_EPOCH
        if value and value != LEGACY and value >= CURRENT_EPOCH
        else LEGACY
    )


def _caption_look(
    session: Any, spine_id: str, spine: Mapping[str, Any]
) -> tuple[str, str]:
    """The show's caption look and where it came from (the server's caption-look route first)."""

    status, body = session.get_optional(
        f"/v1/spines/{quote(spine_id, safe='')}/caption-look"
    )
    if (
        status == 200
        and isinstance(body, Mapping)
        and body.get("caption_look") in CAPTION_LOOKS
    ):
        return str(
            body["caption_look"]
        ), f"server caption look ({body.get('source', 'show')})"
    stored = spine.get("caption_look")
    if stored in CAPTION_LOOKS:
        return str(stored), "the story's caption look"
    # A show with episodes already out keeps Subtle, the server's default for an unset look.
    return "subtle", "default (the story has none set)"


@dataclass
class AttachResult:
    """What ``start --spine-id`` made."""

    desk: Path
    spine_id: str
    session_id: str
    episode: int
    lines: list[str]


def _config_from_spine(
    spine: Mapping[str, Any], caption_style: str
) -> ProductionConfig:
    config = ProductionConfig()
    config.caption_style = caption_style
    language = str(spine.get("spoken_language") or "")
    config.spoken_language = (
        None if not language or language.lower().startswith("en") else language
    )
    if spine.get("delivery_format") == "letterbox":
        config.delivery_format = "letterbox"
    config.rules_epoch = desk_rules_epoch(spine)
    boundary = spine.get("continuing_fixes_from_episode")
    if config.rules_epoch == LEGACY and isinstance(boundary, int):
        config.continuing_fixes_from_episode = boundary
    return config


def attach_story(
    spine_id: str,
    *,
    parent: Path,
    series: str | None = None,
    video_lane: str = "minimax-h3",
    opener: Callable[[str, Path], Any] | None = None,
    today: date | None = None,
    out: TextIO | None = None,
) -> AttachResult:
    """Make a new desk for an existing server story (``start --spine-id``). Spends nothing.

    Lists the new desk's session on the story (``POST …/collaborators``; the
    owner never moves), reads the story and its caption look, then makes the
    desk: ``production.json`` points at the story's last written episode,
    done, so the next command is the next episode (``arc`` / ``brief`` /
    ``author``). When anything after the listing fails, the listing is taken
    off again and no desk is left.

    Parameters
    ----------
    spine_id
        The story's id (``stories --title`` finds it).
    parent
        Folder the dated desk goes in.
    series
        Desk name; the story's title when ``None``.
    video_lane
        Lane recorded on the desk (the server names the lane it films on).
    opener
        ``(session_id, out_dir) -> session``; :func:`open_session` by default.
    today
        Desk date (tests).
    out
        Where the summary is printed.

    Returns
    -------
    AttachResult
        The desk and what was read.

    Raises
    ------
    AttachRefused
        The server is too old, refused the listing or the read, or the desk
        folder already exists. Nothing is left behind.
    """

    out = out or sys.stdout
    opener = opener or open_session
    parent = parent.expanduser().resolve()
    stamp = today or date.today()
    # The desk's session must exist before the desk: it is what the server lists.
    session_id = ProductionState.new_session_id(
        run_folder_name(series or spine_id, stamp)
    )
    session = opener(session_id, parent)
    listed = False
    try:
        status, body = session.post_optional(
            collaborators_path(spine_id), {"session_id": session_id}
        )
        if _too_old(status, body):
            raise AttachRefused(
                "server too old: this deploy cannot attach a story to a desk "
                "(POST /v1/spines/{id}/collaborators). Nothing was changed."
            )
        if status >= 300 or not isinstance(body, Mapping):
            raise AttachRefused(
                f"the server did not attach the story: {_error_text(status, body)}. Nothing was changed."
            )
        listed = True
        owner_kind = str(body.get("owner_kind") or "")
        spine = session.get(f"/v1/spines/{quote(spine_id, safe='')}")
        caption_style, caption_source = _caption_look(session, spine_id, spine)
        title = str(spine.get("title") or "").strip() or spine_id
        name = (series or title).strip()
        desk = parent / run_folder_name(name, stamp)
        if desk.exists():
            raise AttachRefused(
                f"a desk for this date and name already exists: {desk}. Pass another --series name."
            )
        band = str(spine.get("duration_band") or "15s")
        if band not in BANDS:
            band = "15s"
        episode = last_written_episode(spine)
        config = _config_from_spine(spine, caption_style)
        desk = init_series_desk(
            parent,
            name,
            band=band,  # type: ignore[arg-type]
            episode_count=max(episode, 1),
            day=stamp,
            continuing=True,
            letterbox=config.delivery_format == "letterbox",
        )
    except BaseException:
        if listed:
            session.delete_optional(collaborators_path(spine_id, session_id))
        session.client.close()
        raise
    try:
        state = ProductionState(
            session_id=session_id,
            prompt=str(spine.get("scene_prompt") or spine.get("logline") or title),
            preset_id=str(spine.get("art_style_preset_id") or ""),
            preset_version=str(spine.get("art_style_preset_version") or ""),
            video_lane=video_lane,
            episode_ordinal=max(episode, 1),
            band=band,
            spine_id=spine_id,
            phase="complete" if episode else "new",
        )
        save_production(desk, state)
        save_production_config(desk, config)
        from creation.orchestrate import save_spine_snapshot

        save_spine_snapshot(desk, max(episode, 1), dict(spine))
        record = {
            "spine_id": spine_id,
            "session_id": session_id,
            "owner_kind": owner_kind,
            "attached_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "detached_at": None,
        }
        (desk / "api" / ATTACH_FILENAME).write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        session.client.close()
    language = str(spine.get("spoken_language") or "en-US")
    lines = [
        str(desk),
        f'Attached story {spine_id} "{title}" (owner: {owner_kind or "unknown"}; the owner is unchanged, '
        "so it stays in the creator's library).",
        f"Episodes written: {episode}. Show language: {language}. Art style: {state.preset_id or 'unknown'}. "
        f"Caption look: {caption_style} ({caption_source}). Rules: {config.rules_epoch}.",
        "Nothing was spent. Voices are the show's own: run `uv run fictora-produce voice-mode --desk "
        f"{desk}` before the next episode.",
        f'Next episode: uv run fictora-produce author --desk {desk} --episode {episode + 1} --line "…"',
        f"When the work is done: uv run fictora-produce detach --desk {desk}",
    ]
    append_run_note(desk, "\n".join(lines[1:]))
    for line in lines:
        print(line, file=out)
    return AttachResult(
        desk=desk,
        spine_id=spine_id,
        session_id=session_id,
        episode=episode,
        lines=lines,
    )


def detach_story(
    desk: Path,
    *,
    opener: Callable[[str, Path], Any] | None = None,
    out: TextIO | None = None,
) -> int:
    """Take the desk's session off its story (``DELETE …/collaborators/{session}``). Spends nothing.

    The story, its episodes and its owner are untouched; the desk keeps its
    files but can no longer read or write the story.

    Parameters
    ----------
    desk
        A desk made by ``start --spine-id``.
    opener
        ``(session_id, out_dir) -> session``; :func:`open_session` by default.
    out
        Where the result is printed.

    Returns
    -------
    int
        ``0`` when the session is off the story (now or already), ``2`` when refused.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    path = desk / "api" / ATTACH_FILENAME
    if not path.is_file():
        print(
            f"Stopped: {desk} was not attached with `start --spine-id` (no api/{ATTACH_FILENAME}). Nothing was sent.",
            file=sys.stderr,
        )
        return 2
    record = json.loads(path.read_text(encoding="utf-8"))
    spine_id, session_id = str(record["spine_id"]), str(record["session_id"])
    opener = opener or open_session
    session = opener(session_id, desk / "api")
    try:
        status, body = session.delete_optional(collaborators_path(spine_id, session_id))
    finally:
        session.client.close()
    if status >= 300 and not (status == 404 and record.get("detached_at")):
        print(
            f"Refused: {_error_text(status, body)}. The desk is still attached.",
            file=sys.stderr,
        )
        return 2
    changed = bool(body.get("changed")) if isinstance(body, Mapping) else False
    record["detached_at"] = record.get("detached_at") or datetime.now(UTC).isoformat(
        timespec="seconds"
    )
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    line = (
        f"Detached: this desk's session is off story {spine_id}; the story and its owner are unchanged."
        if changed or status < 300
        else f"Already detached from story {spine_id}."
    )
    append_run_note(desk, line)
    print(line, file=out)
    return 0


def stories_command(
    title: str | None,
    *,
    opener: Callable[[str, Path], Any] | None = None,
    out: TextIO | None = None,
) -> int:
    """``fictora-produce stories [--title T]``: print the matching stories. Read only.

    Parameters
    ----------
    title
        Part of the title.
    opener
        ``(session_id, out_dir) -> session``; :func:`open_session` by default.
    out
        Where the lines are printed.

    Returns
    -------
    int
        ``0`` on an answer, ``2`` when refused.
    """

    out = out or sys.stdout
    opener = opener or open_session
    session = opener(f"content-ops-stories-{uuid.uuid4().hex[:8]}", Path.cwd())
    try:
        rows = search_stories(title, session=session)
    except AttachRefused as exc:
        print(exc, file=sys.stderr)
        return 2
    finally:
        session.client.close()
    for line in story_lines(rows, title=title):
        print(line, file=out)
    return 0
