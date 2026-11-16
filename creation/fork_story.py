"""Fork a desk's story into copies that differ only in harness flags, each on its own new desk.

A blind A/B on a harness change that acts from episode 2 on needs stories that
share everything up to episode 2: the look, the cast plates, episode 1. Two
drafts never do (the writer and image models are random). So the operator
approves one story's look, cast and episode 1 once on its desk, then forks it
on the server (fictora-drama ``POST /v1/spines/{id}/forks``,
``docs/runbooks/harness-flags.md``): each fork is a new story with its own
``spine_id`` and the arm's flags, sharing every picture, nothing drawn or paid.

``fork --desk SOURCE --harness-flag NAME=on|off --series NAME`` sends the fork
with the source desk's session, then makes a new desk on the fork the way
``start --spine-id`` attaches a story (the new desk's session is listed on the
fork), with the source desk's ``production.config.json`` so every fork films
the same way. The desk is at episode 1, done: the next command is
``author --episode 2``. ``api/fork.json`` names the source, the flags and when.

``fork-set --desk SOURCE --flag NAME --arm off:all-off --arm on:all-on
--arm off2:all-off --series S`` makes several forks at once and writes one
``api/fork-set.json`` (the same file on every arm's desk) naming each arm's
blind-eval role (sokii-blind-eval ``docs/bundle-schema.md``): the first
all-off arm is ``base``, the first all-on arm ``head``, other all-off arms
``base_repeat``.

The idempotency key is made from the source story, its desk session, the flags
and the series, and saved on the source desk (``api/forks.json``), so a rerun
sends the same key and gets the same fork back. A refusal stops before any
desk is made; nothing is charged either way.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote

from creation.attach_story import (
    ATTACH_FILENAME,
    AttachRefused,
    _error_code,
    _error_text,
    _too_old,
    attach_story,
    open_session,
)
from creation.captions import show_caption_style
from creation.harness_flags import (
    HarnessFlags,
    HarnessFlagsRefused,
    harness_flags_text,
    parse_harness_flags,
)
from creation.ops.folder import slugify_series
from creation.ops.notes import append_run_note
from creation.production_config import ProductionConfig, load_production_config
from creation.production_state import load_production
from creation.rules_epoch import desk_epoch

FORK_FILENAME = "fork.json"
FORK_SET_FILENAME = "fork-set.json"
#: On the source desk: one row per fork sent from it (key, series, flags, fork id, desk).
FORKS_FILENAME = "forks.json"

#: Why the server refused, by its error code. Each line is followed by "Nothing was made or charged."
_REFUSALS: Mapping[str, str] = {
    "operator_only": "only an operator desk session may fork a story, and this desk's session is not one",
    "spine_not_found": "the server does not show this story to the source desk's session "
    "(it is not this desk's story, or the desk is not listed on it)",
    "spine_fork_source_busy": "the source story has a run going. Wait for it to finish "
    "(`fictora-produce status --desk SOURCE`), then run the same command again",
    "spine_fork_past_episode_one": "something past episode 1 is already written, drawn or delivered "
    "on the source story. Fork a story while only episode 1 exists",
    "spine_fork_legacy_rules": "the source story runs the legacy rules, and a legacy story cannot be forked",
    "idempotency_conflict": "this desk already sent a fork under the same key with another body. "
    "Use another --series, or remove that row from the source desk's api/forks.json",
}


class ForkRefused(RuntimeError):
    """The fork was not made (refused, or the kit would not send it); nothing was charged."""


@dataclass(frozen=True)
class ForkResult:
    """A fork desk, new or found from an earlier run."""

    desk: Path
    spine_id: str
    flags: HarnessFlags | None
    series: str
    made_now: bool


def forks_path(spine_id: str) -> str:
    """Return ``/v1/spines/{id}/forks``."""

    return f"/v1/spines/{quote(spine_id, safe='')}/forks"


def fork_key(
    source_spine_id: str,
    source_session_id: str,
    flags: Mapping[str, bool] | None,
    series: str,
) -> str:
    """Return the fork's ``Idempotency-Key``: the same source, flags and series give the same key.

    Parameters
    ----------
    source_spine_id, source_session_id
        The source story and the desk session that forks it.
    flags
        The overrides sent (``None`` for none).
    series
        The fork desk's name, so two forks with the same flags (a noise control) are two forks.

    Returns
    -------
    str
        ``kit-fork-<40 hex>``.
    """

    seed = json.dumps(
        [
            source_spine_id,
            source_session_id,
            dict(sorted((flags or {}).items())),
            series.strip(),
        ],
        separators=(",", ":"),
    )
    return f"kit-fork-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:40]}"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, record: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _fork_rows(source: Path) -> list[dict[str, Any]]:
    raw = _read_json(source / "api" / FORKS_FILENAME)
    rows = raw.get("forks") if isinstance(raw, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def _save_fork_row(source: Path, row: Mapping[str, Any]) -> None:
    rows = [r for r in _fork_rows(source) if r.get("key") != row["key"]]
    _write_json(source / "api" / FORKS_FILENAME, {"forks": [*rows, dict(row)]})


def _desk_on(desk: str | None, spine_id: str | None) -> Path | None:
    if not desk or not spine_id:
        return None
    raw = _read_json(Path(desk) / "production.json")
    if isinstance(raw, dict) and raw.get("spine_id") == spine_id:
        return Path(desk)
    return None


def _attached_desk(parent: Path, spine_id: str) -> Path | None:
    """A desk under ``parent`` already attached to ``spine_id`` (an earlier run stopped after making it)."""

    for desk in sorted(parent.iterdir()) if parent.is_dir() else []:
        if (desk / "api" / ATTACH_FILENAME).is_file() and _desk_on(str(desk), spine_id):
            return desk
    return None


def fork_refusal(status: int, body: Any, *, spine_id: str) -> str:
    """Return the stop line for a fork the server did not make.

    Parameters
    ----------
    status
        The answer's HTTP status.
    body
        Its JSON.
    spine_id
        The source story.

    Returns
    -------
    str
        ``Stopped: …`` naming why, and that nothing was made or charged.
    """

    if _too_old(status, body):
        why = "this deploy cannot fork a story (no POST /v1/spines/{id}/forks)"
    elif status == 422:
        why = (
            "the server does not know one of the flag names (list them in fictora-drama: "
            "uv run python scripts/harness_flags.py)"
        )
    else:
        why = _REFUSALS.get(_error_code(body), "the server refused it")
    return (
        f"Stopped: story {spine_id} was not forked: {why}. Nothing was made or charged. "
        f"Server: {_error_text(status, body)}"
    )


def _fork_config(source: Path) -> ProductionConfig:
    config = load_production_config(source)
    caption_style = config.caption_style
    if not caption_style:
        shown, shown_from = show_caption_style(source)
        if shown_from == "unchecked":
            # Each fork desk would pick its own later, and the arms could differ.
            raise ForkRefused(
                f"the caption look of {source} could not be read from the server, so the forks "
                "could be captioned differently. Set it on the source first "
                f"(`fictora-produce caption-style --desk {source} --set ...`). Nothing was sent."
            )
        caption_style = shown
    # The flags live on the fork's spine; the fork desk never drafts, so it sends none.
    return replace(
        config,
        caption_style=caption_style,
        harness_flags=None,
        rules_epoch=config.rules_epoch or desk_epoch(source).epoch,
    )


def fork_desk(
    source: Path,
    *,
    flags: HarnessFlags | None,
    series: str,
    parent: Path | None = None,
    opener: Callable[[str, Path], Any] | None = None,
    out: TextIO | None = None,
    now: datetime | None = None,
) -> ForkResult:
    """Fork the source desk's story with ``flags`` and make a new desk on the fork. Spends nothing.

    Parameters
    ----------
    source
        The desk whose story's look, cast and episode 1 were approved.
    flags
        Overrides on the source story's stored flags; ``None`` keeps them (a noise-control copy).
    series
        The fork desk's name; part of the idempotency key.
    parent
        Where the fork desk is made; the source desk's folder by default.
    opener
        ``(session_id, out_dir) -> session``; :func:`creation.attach_story.open_session` by default.
    out
        Where the lines are printed.
    now
        Record time (tests).

    Returns
    -------
    ForkResult
        The fork desk. A rerun whose fork desk exists answers it without sending anything.

    Raises
    ------
    ForkRefused
        The source desk has no story, the server refused, or the desk could not be made.
    """

    out = out or sys.stdout
    opener = opener or open_session
    source = source.expanduser().resolve()
    series = series.strip()
    if not series:
        raise ForkRefused("--series is empty: name the fork desk. Nothing was sent.")
    state = load_production(source)
    if not state.spine_id:
        raise ForkRefused(
            f"{source} has no story yet (it was never drafted); nothing to fork. Nothing was sent."
        )
    key = fork_key(state.spine_id, state.session_id, flags, series)
    flags_text = harness_flags_text(flags) if flags else "none (the source's own)"
    row = next((r for r in _fork_rows(source) if r.get("key") == key), None)
    found = _desk_on(row.get("desk"), row.get("spine_id")) if row else None
    if row and found:
        print(
            f"Already forked: {found} works on fork {row['spine_id']} of story {state.spine_id} "
            f"(harness flags {flags_text}). Nothing was sent.",
            file=out,
        )
        return ForkResult(found, str(row["spine_id"]), flags, series, made_now=False)
    config = _fork_config(source)
    body = {"harness_flags": dict(flags) if flags else None}
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    # Saved before the send, so a lost answer still leaves the key on the source desk.
    row = row or {
        "key": key,
        "series": series,
        "harness_flags": flags,
        "spine_id": None,
        "desk": None,
        "created_at": stamp,
    }
    _save_fork_row(source, row)
    print(
        f"Forking story {state.spine_id} (desk {source}) for {series!r}: harness flags {flags_text}.\n"
        f"Sent: POST {forks_path(state.spine_id)} Idempotency-Key {key} {json.dumps(body)}",
        file=out,
    )
    session = opener(state.session_id, source / "api")
    try:
        status, answer = session.post_optional(
            forks_path(state.spine_id), body, idempotency_key=key
        )
    finally:
        session.client.close()
    if not 200 <= status < 300 or not isinstance(answer, Mapping):
        raise ForkRefused(fork_refusal(status, answer, spine_id=state.spine_id))
    fork_id = str(answer["spine_id"])
    row["spine_id"] = fork_id
    _save_fork_row(source, row)
    home = (parent or source.parent).expanduser().resolve()
    record = {
        "source_desk": str(source),
        "source_spine_id": state.spine_id,
        "spine_id": fork_id,
        "spine_version": answer.get("spine_version"),
        "series": series,
        "harness_flags_sent": flags,
        "harness_flags_on": answer.get("harness_flags"),
        "idempotency_key": key,
        "created_at": row["created_at"],
    }
    try:
        desk = (
            _attached_desk(home, fork_id)
            or attach_story(
                fork_id,
                parent=home,
                series=series,
                video_lane=state.video_lane,
                opener=opener,
                config=config,
                announce=False,
            ).desk
        )
        _write_json(desk / "api" / FORK_FILENAME, record)
        row["desk"] = str(desk)
        _save_fork_row(source, row)
    except (AttachRefused, OSError, ValueError) as exc:
        raise ForkRefused(
            f"Story {state.spine_id} was forked as {fork_id} (nothing drawn or charged), but its desk "
            f"was not made: {exc}\nRun the same command again: the same key answers the same fork, "
            "and a desk already made on it is reused. If a folder of that name belongs to another "
            "story, move it away first."
        ) from exc
    on = answer.get("harness_flags")
    lines = [
        str(desk),
        f"Forked story {state.spine_id} as {fork_id}: harness flags sent {flags_text}; "
        f"on for the fork: {', '.join(on) if on else 'none'}.",
        "It shares the source's look, cast plates, voices and episode 1 (script and boards); "
        "nothing was drawn or charged. Settings copied from the source desk's production.config.json.",
        f'Next: uv run fictora-produce author --desk {desk} --episode 2 --line "…" --keep-voices '
        "(the same --line on every fork), then step.",
    ]
    append_run_note(desk, "\n".join(lines[1:]))
    append_run_note(
        source, f"Forked as {fork_id} on desk {desk} (harness flags {flags_text})."
    )
    for line in lines:
        print(line, file=out)
    return ForkResult(desk, fork_id, flags, series, made_now=True)


ALL_OFF, ALL_ON = "all-off", "all-on"


@dataclass(frozen=True)
class ForkArm:
    """One arm of a fork set: its name and the flags its fork sends."""

    name: str
    flags: HarnessFlags | None


def parse_arms(values: Sequence[str], flag_names: Sequence[str]) -> list[ForkArm]:
    """Return ``--arm NAME:SPEC`` values as arms.

    ``SPEC`` is ``all-off`` or ``all-on`` (every ``--flag`` off or on), or ``a=on,b=off``.

    Raises
    ------
    HarnessFlagsRefused
        A malformed arm, a repeated arm name, or ``all-off``/``all-on`` without ``--flag``.
    """

    arms: list[ForkArm] = []
    for raw in values:
        name, sep, spec = raw.partition(":")
        name, spec = name.strip(), spec.strip()
        if not sep or not name or not spec:
            raise HarnessFlagsRefused(
                f"--arm {raw!r}: use NAME:all-off, NAME:all-on or NAME:flag=on,flag=off"
            )
        try:
            slug = slugify_series(name)
        except ValueError:
            raise HarnessFlagsRefused(
                f"--arm {raw!r}: the arm name needs letters or digits"
            ) from None
        if any(slugify_series(arm.name) == slug for arm in arms):
            raise HarnessFlagsRefused(
                f"--arm {name} names the same desk folder as another arm; name each arm differently"
            )
        if spec in (ALL_OFF, ALL_ON):
            words = [
                f"{flag}={'on' if spec == ALL_ON else 'off'}" for flag in flag_names
            ]
            if not words:
                raise HarnessFlagsRefused(
                    f"--arm {raw!r} needs at least one --flag NAME"
                )
        else:
            words = spec.split(",")
        arms.append(ForkArm(name, parse_harness_flags(words)))
    if not arms:
        raise HarnessFlagsRefused("fork-set needs at least one --arm NAME:SPEC")
    return arms


def arm_roles(arms: Sequence[ForkArm]) -> dict[str, str | None]:
    """Each arm's blind-eval role: first all-off ``base``, first all-on ``head``, other all-off ``base_repeat``."""

    roles: dict[str, str | None] = {}
    for arm in arms:
        values = set((arm.flags or {}).values())
        role = None
        if values == {False}:
            role = "base_repeat" if "base" in roles.values() else "base"
        elif values == {True} and "head" not in roles.values():
            role = "head"
        roles[arm.name] = role
    return roles


def fork_set(
    source: Path,
    *,
    arms: Sequence[ForkArm],
    series: str,
    parent: Path | None = None,
    opener: Callable[[str, Path], Any] | None = None,
    out: TextIO | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fork the source desk's story once per arm and record the set on every arm's desk. Spends nothing.

    Each arm's desk is ``"{series} {arm}"``. An arm that fails stops the set; the arms
    made before it keep their forks and desks, and the same command run again finds them
    (nothing is sent for them) and makes the rest.

    Returns
    -------
    dict[str, Any]
        The ``api/fork-set.json`` record.
    """

    out = out or sys.stdout
    source = source.expanduser().resolve()
    roles = arm_roles(arms)
    names = sorted({name for arm in arms for name in arm.flags or {}})
    rows = []
    for arm in arms:
        made = fork_desk(
            source,
            flags=arm.flags,
            series=f"{series} {arm.name}",
            parent=parent,
            opener=opener,
            out=out,
            now=now,
        )
        rows.append(
            {
                "arm": arm.name,
                "role": roles[arm.name],
                "harness_flags": arm.flags,
                "spine_id": made.spine_id,
                "desk": str(made.desk),
            }
        )
    record = {
        "source_desk": str(source),
        "source_spine_id": load_production(source).spine_id,
        "series": series,
        "flags": names,
        "harness_flag": names[0] if len(names) == 1 else None,
        "arms": rows,
        "created_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
    }
    for row in rows:
        _write_json(Path(row["desk"]) / "api" / FORK_SET_FILENAME, record)
    for row in rows:
        print(
            f"{row['arm']} ({row['role'] or 'no eval role'}): {row['desk']}", file=out
        )
    return record


__all__ = [
    "FORKS_FILENAME",
    "FORK_FILENAME",
    "FORK_SET_FILENAME",
    "ForkArm",
    "ForkRefused",
    "ForkResult",
    "arm_roles",
    "fork_desk",
    "fork_key",
    "fork_refusal",
    "fork_set",
    "forks_path",
    "parse_arms",
]
