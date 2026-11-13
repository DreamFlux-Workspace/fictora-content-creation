"""Start a new story on a new desk: from a premise, as a replay of an old story, or as a harness-flag pair.

``start --prompt`` makes a desk for a new premise. ``start --from-story X``
makes one for a NEW story with an old story's inputs: its brief (the spine's
``scene_prompt_text``, else its normalized prompt, else the old desk's local
brief), art style preset and version, duration band, video lane, spoken
language, delivery format and the old desk's caption and voice settings.
``harness-pair --from-story X --flag a`` makes two such desks, base with every
named flag off and head with every named flag on, and records the pair on both.

None of these call a paid route. The story is drafted by the next ``step``
(authoring); filming still needs ``--confirm-spend`` as on every desk.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote

from creation.attach_story import open_session
from creation.harness_flags import HarnessFlags, harness_flags_text, pair_arms
from creation.ops.floor import init_series_desk
from creation.ops.folder import run_folder_name
from creation.ops.notes import append_run_note
from creation.orchestrate import bind_desk, unbound_desk_recovery
from creation.production_config import (
    ProductionConfig,
    load_production_config,
    save_production_config,
)
from creation.production_state import ProductionState, load_production, production_path
from creation.rules_epoch import CURRENT_EPOCH

PAIR_FILENAME = "harness-pair.json"
BANDS = ("15s", "30s", "60s")


class ReplayRefused(RuntimeError):
    """The old story cannot be replayed; nothing was made or sent."""


@dataclass(frozen=True)
class StoryInputs:
    """What a new story is drafted from: the premise, its look and its desk settings."""

    prompt: str
    preset: tuple[str, str]
    band: str
    video_lane: str | None
    config: ProductionConfig


@dataclass(frozen=True)
class ReplaySource:
    """An old story read for a replay: its ids, title and the inputs it was drafted from."""

    spine_id: str
    desk: Path
    title: str
    inputs: StoryInputs
    prompt_from: str


def start_new_desk(
    parent: Path,
    series: str,
    inputs: StoryInputs,
    *,
    episodes: int = 1,
    prompt_arg: str,
) -> tuple[Path, ProductionState]:
    """Make and bind a new desk for a story that is not drafted yet. Spends nothing.

    Parameters
    ----------
    parent
        Folder the desk is made in.
    series
        Desk name.
    inputs
        The premise, preset pin, band, lane and config.
    episodes
        Desk episode slots.
    prompt_arg
        The ``--prompt`` as typed, for the recovery line when the desk already exists.

    Returns
    -------
    tuple[Path, ProductionState]
        The bound desk, at phase ``new``, and its state.
    """

    config = replace(inputs.config, rules_epoch=CURRENT_EPOCH)
    try:
        desk = init_series_desk(
            parent,
            series,
            band=inputs.band,  # type: ignore[arg-type]
            episode_count=episodes,
            letterbox=config.delivery_format == "letterbox",
        )
    except FileExistsError:
        desk = (parent.expanduser() / run_folder_name(series, date.today())).resolve()
        raise FileExistsError(
            unbound_desk_recovery(
                desk, prompt_arg=prompt_arg, preset_id=inputs.preset[0]
            )
        ) from None
    try:
        state = bind_desk(
            desk,
            prompt=inputs.prompt,
            preset_id=inputs.preset[0],
            video_lane=inputs.video_lane,
            preset=inputs.preset,
        )
    except (RuntimeError, ValueError, OSError):
        print(
            unbound_desk_recovery(
                desk, prompt_arg=prompt_arg, preset_id=inputs.preset[0], just_made=True
            ),
            file=sys.stderr,
        )
        raise
    # A new desk runs the current rules (creation.rules_epoch); older desks keep theirs.
    save_production_config(desk, config)
    return desk, state


def find_source_desk(source: str, parent: Path) -> Path:
    """Return the old story's desk: ``source`` itself when it is a desk, else the newest desk under ``parent`` on that story.

    Raises
    ------
    ReplayRefused
        No desk under ``parent`` holds that story id.
    """

    path = Path(source).expanduser()
    if production_path(path.resolve()).is_file():
        return path.resolve()
    root = parent.expanduser()
    matches = [
        candidate.parent
        for candidate in root.glob("*/production.json")
        if _spine_id_of(candidate) == source
    ]
    if not matches:
        raise ReplayRefused(
            f"no desk under {root} holds story {source}. Pass the old story's desk folder, or "
            f"attach the story first (uv run fictora-produce start --spine-id {source}) and pass that desk."
        )
    return max(matches, key=lambda desk: production_path(desk).stat().st_mtime)


def _spine_id_of(production_json: Path) -> str | None:
    try:
        raw = json.loads(production_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw.get("spine_id") if isinstance(raw, dict) else None


def read_replay_source(
    source: str,
    *,
    parent: Path,
    opener: Callable[[str, Path], Any] | None = None,
) -> ReplaySource:
    """Read an old story's inputs from its spine (``GET /v1/spines/{id}``) and its desk. Spends nothing.

    Parameters
    ----------
    source
        The old story's spine id, or its desk folder.
    parent
        Where desks are looked for when ``source`` is a spine id.
    opener
        Opens a session for a session id; :func:`creation.attach_story.open_session` by default.

    Raises
    ------
    ReplayRefused
        No desk holds the story, the desk never drafted one, or the server would not read it.
    """

    desk = find_source_desk(source, parent)
    state = load_production(desk)
    if not state.spine_id:
        raise ReplayRefused(
            f"{desk} has no story yet (it was never drafted); nothing to replay."
        )
    session = (opener or open_session)(state.session_id, desk)
    try:
        status, spine = session.get_optional(
            f"/v1/spines/{quote(state.spine_id, safe='')}"
        )
    finally:
        session.client.close()
    if status != 200 or not isinstance(spine, Mapping):
        raise ReplayRefused(
            f"the server did not return story {state.spine_id} to this desk's session (HTTP {status}). "
            "Nothing was made."
        )
    prompt, prompt_from = _story_prompt(spine, state.prompt)
    preset = (
        str(spine.get("art_style_preset_id") or state.preset_id),
        str(spine.get("art_style_preset_version") or state.preset_version),
    )
    if not prompt or not all(preset):
        raise ReplayRefused(
            f"story {state.spine_id} has no brief or art style to replay. Nothing was made."
        )
    band = str(spine.get("duration_band") or state.band)
    language = str(spine.get("spoken_language") or "")
    old = load_production_config(desk)
    config = ProductionConfig(
        clip_duration_seconds=old.clip_duration_seconds,
        caption_style=old.caption_style,
        api_captions=old.api_captions,
        locale=old.locale,
        spoken_language=None
        if not language or language.lower().startswith("en")
        else language,
        delivery_format="letterbox"
        if spine.get("delivery_format") == "letterbox"
        else None,
        opening_action=old.opening_action,
        voice_mode=old.voice_mode,
        letterbox_caption_colour=old.letterbox_caption_colour,
    )
    return ReplaySource(
        spine_id=state.spine_id,
        desk=desk,
        title=str(spine.get("title") or "").strip() or state.spine_id,
        inputs=StoryInputs(
            prompt=prompt,
            preset=preset,
            band=band if band in BANDS else state.band,
            video_lane=str(spine.get("video_lane") or "") or state.video_lane,
            config=config,
        ),
        prompt_from=prompt_from,
    )


def _story_prompt(spine: Mapping[str, Any], local: str) -> tuple[str, str]:
    for field in ("scene_prompt_text", "scene_prompt_normalized"):
        text = str(spine.get(field) or "").strip()
        if text:
            return text, f"the story's {field}"
    return local.strip(), "the old desk's local brief"


def replay_lines(source: ReplaySource, flags: HarnessFlags | None) -> list[str]:
    """Say what a replay carries over, one line each."""

    inputs = source.inputs
    config = inputs.config
    return [
        f'Replaying story {source.spine_id} "{source.title}" (desk {source.desk}) as a NEW story.',
        f"Brief: {source.prompt_from}, {len(inputs.prompt.split())} words.",
        f"Art style {inputs.preset[0]} version {inputs.preset[1]}; band {inputs.band}; "
        f"video lane {inputs.video_lane or 'the server default'}; "
        f"language {config.spoken_language or 'English'}; format {config.delivery_format or 'portrait'}.",
        f"Harness flags: {harness_flags_text(flags) if flags else 'none (the server defaults)'}.",
    ]


def start_replay(
    source: ReplaySource,
    *,
    parent: Path,
    series: str,
    flags: HarnessFlags | None,
    out: TextIO,
) -> Path:
    """Make a desk for a new story with ``source``'s inputs and ``flags``. Spends nothing."""

    inputs = replace(
        source.inputs, config=replace(source.inputs.config, harness_flags=flags)
    )
    for line in replay_lines(source, flags):
        print(line, file=out)
    desk, _ = start_new_desk(parent, series, inputs, prompt_arg=f"@{source.desk}")
    append_run_note(
        desk,
        f"Replay of story {source.spine_id} (desk {source.desk}); harness flags: "
        f"{harness_flags_text(flags) if flags else 'none'}.",
    )
    return desk


def start_harness_pair(
    source: ReplaySource,
    *,
    parent: Path,
    series: str,
    flag_names: Sequence[str],
    out: TextIO,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    """Make the base desk (every named flag off) and the head desk (every named flag on) for one old story.

    Both desks get ``api/harness-pair.json`` naming the old story, the flags
    and both desks, so an export can label which arm is which. Spends nothing.

    Returns
    -------
    tuple[Path, Path]
        ``(base desk, head desk)``.
    """

    off, on = pair_arms(flag_names)
    base = start_replay(
        source, parent=parent, series=f"{series} base", flags=off, out=out
    )
    head = start_replay(
        source, parent=parent, series=f"{series} head", flags=on, out=out
    )
    record = {
        "source_spine_id": source.spine_id,
        "source_desk": str(source.desk),
        "flags": sorted(off),
        "harness_flag": sorted(off)[0] if len(off) == 1 else None,
        "base": {"desk": str(base), "harness_flags": off},
        "head": {"desk": str(head), "harness_flags": on},
        "created_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
    }
    for desk in (base, head):
        (desk / "api").mkdir(parents=True, exist_ok=True)
        (desk / "api" / PAIR_FILENAME).write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    return base, head


__all__ = [
    "PAIR_FILENAME",
    "ReplayRefused",
    "ReplaySource",
    "StoryInputs",
    "find_source_desk",
    "read_replay_source",
    "replay_lines",
    "start_harness_pair",
    "start_new_desk",
    "start_replay",
]
