"""CLI: orchestrate episode production on a series desk."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Sequence

from creation.captions import caption_take, find_ffmpeg, take_index_from_name
from creation.cli_config import (
    add_production_config_args,
    bound_preset_and_lane,
    config_from_args,
    merge_config_from_args,
)
from creation.cli_post import (
    POST_COMMANDS,
    add_caption_style_arg,
    add_post_parsers,
    dispatch_post,
)
from creation.cli_text import HELP_SUFFIX, force_utf8_output, text_or_file
from creation.episode_commands import (
    EPISODE_COMMANDS,
    add_episode_parsers,
    dispatch_episode,
    run_approve_look,
)
from creation.narrator_cast import add_narrator_answer_args, interactive_ask
from creation.plan_prompt import narrator_warning
from creation.ops.floor import init_series_desk
from creation.ops.folder import DEFAULT_RUN_PARENT, run_folder_name
from creation.ops.notes import append_run_note
from creation.orchestrate import (
    approve_gate,
    bind_desk,
    published_preset_lines,
    resolve_preset,
    run_step,
    status_message,
    unbound_desk_recovery,
)
from creation.production_config import load_production_config, save_production_config
from creation.rules_epoch import (
    CURRENT_EPOCH,
    EPOCH_CHOICES,
    desk_rules,
    run_continuing_fixes,
    run_rules_epoch,
)
from creation.stylised_only import NOTICE_KINDS
from creation.recover import (
    RETRYABLE_STEPS,
    cancel_video_job,
    prepare_video_retry,
    retry_failed_step,
)
from creation.setup_check import run_setup_check
from creation.shared_spine import shared_spine_refusal


def _warn_if_no_local_ffmpeg() -> None:
    """Warn at desk creation when this machine cannot burn local captions."""

    try:
        find_ffmpeg()
    except RuntimeError as exc:
        print(f"WARNING: {exc}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch fictora-produce commands."""

    force_utf8_output()

    parser = argparse.ArgumentParser(
        description="Orchestrate Drama API production on a content desk."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser(
        "start", help="Create desk + bind prompt (does not call API yet)."
    )
    start.add_argument("--series", required=True)
    start.add_argument("--prompt", required=True, help=f"The premise. {HELP_SUFFIX}")
    start.add_argument("--band", default="15s", choices=("15s", "30s", "60s"))
    start.add_argument(
        "--episodes",
        type=int,
        default=1,
        help="Desk episode slots (API draft plans 4 for cadence).",
    )
    start.add_argument("--parent", type=Path, default=DEFAULT_RUN_PARENT)
    start.add_argument(
        "--preset-id",
        default="modern-dark-fantasy",
        help="Published art-style preset (list them: fictora-produce presets). "
        "Checked before the desk is made.",
    )
    start.add_argument("--video-lane", default="minimax-h3")
    add_production_config_args(start)

    bind = sub.add_parser("bind", help="Bind API orchestration to an existing desk.")
    bind.add_argument("--desk", type=Path, required=True)
    bind.add_argument("--prompt", required=True, help=f"The premise. {HELP_SUFFIX}")
    bind.add_argument(
        "--preset-id",
        default=None,
        help="Published art-style preset (list them: fictora-produce presets). "
        "Default: the desk's saved preset (modern-dark-fantasy on a desk never bound).",
    )
    bind.add_argument(
        "--video-lane",
        default=None,
        help="Video lane. Default: the desk's saved lane (minimax-h3 on a desk never bound).",
    )
    bind.add_argument("--episode", type=int, default=1)
    add_production_config_args(bind)

    sub.add_parser(
        "presets",
        help="List the published art-style presets (id, version, name) for --preset-id. Spends nothing.",
    )

    cfg_show = sub.add_parser(
        "config", help="Print merged production.config.json for a desk."
    )
    cfg_show.add_argument("--desk", type=Path, required=True)

    epoch = sub.add_parser(
        "rules-epoch",
        help="Which rules the desk runs under (free): desks created before 6 Oct 2026 keep their original "
        "behaviour (legacy); new desks get the current rules. Without --set it prints the value and why.",
    )
    epoch.add_argument("--desk", type=Path, required=True)
    epoch.add_argument(
        "--set",
        dest="set_to",
        default=None,
        choices=EPOCH_CHOICES,
        help="Store it in production.config.json: legacy (keep the original behaviour) or "
        f"{CURRENT_EPOCH} (opt the desk in to the current rules). Only on the human's say-so.",
    )

    continuing = sub.add_parser(
        "continuing-fixes",
        help="Turn the Group B fixes on for a desk created before 6 Oct 2026, from its next unstarted "
        "episode on (free; founder decision 7 Oct 2026). Dry run unless --apply.",
    )
    continuing.add_argument("--desk", type=Path, required=True)
    continuing.add_argument(
        "--from-episode",
        dest="from_episode",
        type=int,
        default=None,
        help="The server spine's continuing_fixes_from_episode, to stay in step (the later of it and the desk's wins).",
    )
    continuing.add_argument(
        "--apply",
        action="store_true",
        help="Write it (only on the founder's sign-off).",
    )

    sub.add_parser("status", help="Show production phase.").add_argument(
        "--desk", type=Path, required=True
    )

    step = sub.add_parser(
        "step", help="Run the next automated API step (one enrol block per call)."
    )
    step.add_argument("--desk", type=Path, required=True)
    step.add_argument(
        "--confirm-spend",
        action="store_true",
        help="After estimate gate, confirm spend and film the take.",
    )
    opening = step.add_mutually_exclusive_group()
    opening.add_argument(
        "--single-frame-start",
        action="store_true",
        help="Tests only: open each take on a single full picture instead of the storyboard (off by "
        "default since 9 Oct 2026, fictora-drama #694: it changed continuing shows' art style). "
        "Goes with --confirm-spend; a resumed step keeps what the film was first sent with.",
    )
    opening.add_argument(
        "--no-single-frame-start",
        action="store_true",
        help="This film only (operator): open each take on the storyboard, said explicitly (the same as "
        "no flag since 9 Oct 2026). Goes with --confirm-spend; a resumed step keeps what the film was first sent with.",
    )
    step.add_argument(
        "--accept-notice",
        action="append",
        default=[],
        choices=NOTICE_KINDS,
        help="Draft only: the creator saw this brief notice and chose to go on "
        "(style_not_available after picking a stylised preset; real_person_not_allowed to let the "
        "writer make an original character). Repeat for both.",
    )
    add_narrator_answer_args(step)

    pitch = sub.add_parser(
        "pitch",
        help="The episode's pitch card (free): with --file, check it and store it as epNN/pitch-vN.json "
        "(never overwritten) and print it; without, print the current one. On desks from 6 Oct 2026 `step` "
        "draws no plates or boards until the card has the human's yes (`approve --gate pitch`).",
    )
    pitch.add_argument("--desk", type=Path, required=True)
    pitch.add_argument(
        "--episode",
        type=int,
        default=None,
        help="Episode ordinal (default: the desk's current episode).",
    )
    pitch.add_argument(
        "--file",
        type=Path,
        default=None,
        help="The card: pitch.md (one `## field` heading per field) or pitch.json.",
    )

    ap = sub.add_parser(
        "approve",
        help="Human yes on the pitch, look, plates, script, or board (the desk's current episode).",
    )
    ap.add_argument("--desk", type=Path, required=True)
    ap.add_argument(
        "--gate", required=True, choices=("pitch", "look", "plates", "script", "board")
    )
    ap.add_argument(
        "--episode",
        type=int,
        default=None,
        help="Pitch only: the episode whose newest pitch card the human said yes to "
        "(default: the desk's current episode). Desk-only record, free.",
    )
    ap.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Board file reviewed (default: the boards step made). "
        "Look: the look-frame-vN file chosen (default: the newest).",
    )
    ap.add_argument(
        "--url",
        default=None,
        help="Look only: the chosen frame's public https URL (default: the chosen look-frame's image_url).",
    )
    ap.add_argument(
        "--again",
        action="store_true",
        help="Plates or board: send the approval again on the story's current version. Plates: outside "
        "wait_plates (after a cast_not_approved refusal; same plates, $0, draws nothing), then retry-step. "
        "Board: a board redrawn after its yes on a desk past the board gate (a board approve outside "
        "wait_board does this too); $0, the phase is kept.",
    )
    ap.add_argument(
        "--accept-dim",
        action="store_true",
        help="Ignored by the API: board brightness is information only, never a block.",
    )

    cancel = sub.add_parser(
        "cancel-job", help="Cancel a stuck video or coordinator job."
    )
    cancel.add_argument("--desk", type=Path, required=True)
    cancel.add_argument("--job-id", required=True)

    retry = sub.add_parser(
        "retry-video",
        help="One new paid take after a human yes. Refuses a second retry.",
    )
    retry.add_argument("--desk", type=Path, required=True)
    retry.add_argument("--job-id", default=None)
    retry.add_argument(
        "--new-paid-take",
        action="store_true",
        help="Required. Confirms this enrol is a new paid take, not a stuck-job retry.",
    )

    retry_step = sub.add_parser(
        "retry-step",
        help="After a failed step (phase=failed): put the failed stage back with a fresh key. Sends nothing.",
    )
    retry_step.add_argument("--desk", type=Path, required=True)
    retry_step.add_argument(
        "--cause", default=None, help="Why it should work now (run notes)."
    )
    retry_step.add_argument(
        "--phase",
        default=None,
        choices=tuple(RETRYABLE_STEPS),
        help="The stage that failed, only when the desk cannot tell.",
    )

    cap = sub.add_parser(
        "caption",
        help="Burn house captions on the raw take locally (ffmpeg on this machine).",
    )
    cap.add_argument("--desk", type=Path, required=True)
    cap.add_argument("--episode", type=int, default=1)
    cap.add_argument(
        "--take",
        type=Path,
        default=None,
        help="Raw MP4; default newest take-epNN-t1-raw-v*.mp4. A take-epNN-tK-… file gets only "
        "take K's lines; a file not named for one take (a joined episode) gets every line.",
    )
    cap.add_argument(
        "--line-start",
        type=float,
        action="append",
        default=None,
        help="Seconds where a line starts, once per line in order. Overrides speech detection.",
    )
    cap.add_argument(
        "--line-end",
        type=float,
        action="append",
        default=None,
        help="Seconds where a line's caption goes off, once per line in order.",
    )
    cap.add_argument(
        "--words-json",
        type=Path,
        default=None,
        help="Transcript of the take to time the lines on (any show); "
        "default: the newest take-epNN-t1-*words-vN.json when captioning the raw take.",
    )
    add_caption_style_arg(cap)
    cap.add_argument(
        "--no-open", action="store_true", help="Do not open the captioned file."
    )

    sub.add_parser(
        "setup-check",
        help="Is this laptop ready? API token set and accepted, ffmpeg/ffprobe with libass and the kit's filters, "
        "Python, uv. Exit 1 on any ✗. Spends nothing.",
    )

    add_post_parsers(sub)
    add_episode_parsers(sub)

    for command_parser in sub.choices.values():
        command_parser.add_argument(
            "--shared-spine-ok",
            action="store_true",
            help="Run even though another desk names this desk's story (spine_id): both desks change.",
        )

    args = parser.parse_args(list(argv) if argv is not None else None)
    desk_arg = getattr(args, "desk", None)
    if isinstance(desk_arg, Path) and not args.shared_spine_ok:
        refused = shared_spine_refusal(
            args.command,
            desk_arg,
            transcribe=bool(getattr(args, "transcribe", False)),
            sends=args.command == "music-note" and bool(getattr(args, "yes", False)),
        )
        if refused:
            print(refused, file=sys.stderr)
            return 2
    # Desks created before 6 Oct 2026 keep their original behaviour for the whole command.
    episode_arg = getattr(args, "episode", None)
    with desk_rules(
        desk_arg if isinstance(desk_arg, Path) else None,
        episode_arg
        if isinstance(episode_arg, int) and not isinstance(episode_arg, bool)
        else None,
    ):
        return _run_command(args)


def _desk_episode(desk: Path) -> int:
    """The desk's current episode (1 on a desk with no production state yet)."""

    from creation.production_state import load_production

    try:
        return load_production(desk.expanduser().resolve()).episode_ordinal
    except (FileNotFoundError, ValueError, KeyError):
        return 1


def _run_command(args: argparse.Namespace) -> int:
    """Run one parsed command (under its desk's rules, :func:`creation.rules_epoch.desk_rules`)."""

    if args.command == "setup-check":
        return run_setup_check()
    if args.command == "continuing-fixes":
        try:
            run_continuing_fixes(
                args.desk, from_episode=args.from_episode, apply=args.apply
            )
        except (ValueError, FileNotFoundError) as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0
    if args.command == "rules-epoch":
        try:
            run_rules_epoch(args.desk, set_to=args.set_to)
        except (ValueError, FileNotFoundError) as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0
    if args.command in EPISODE_COMMANDS:
        try:
            return dispatch_episode(args)
        except (RuntimeError, ValueError, FileNotFoundError, PermissionError) as exc:
            print(exc, file=sys.stderr)
            return 2

    try:
        if args.command in POST_COMMANDS:
            return dispatch_post(args)
        if args.command == "presets":
            for line in published_preset_lines():
                print(line)
            print("Use one with: fictora-produce start … --preset-id ID")
            return 0
        if args.command == "start":
            config = config_from_args(args)  # refused before the desk is made
            prompt = text_or_file(args.prompt, flag="--prompt")
            # Check the preset before anything lands on disk: a wrong id used to leave a
            # half-made desk that the next start refused (L-20260930-13).
            preset = resolve_preset(args.preset_id)
            try:
                desk = init_series_desk(
                    args.parent,
                    args.series,
                    band=args.band,
                    episode_count=args.episodes,
                    letterbox=config.delivery_format == "letterbox",
                )
            except FileExistsError:
                desk = (
                    args.parent.expanduser()
                    / run_folder_name(args.series, date.today())
                ).resolve()
                raise FileExistsError(
                    unbound_desk_recovery(
                        desk, prompt_arg=args.prompt, preset_id=preset[0]
                    )
                ) from None
            try:
                state = bind_desk(
                    desk,
                    prompt=prompt,
                    preset_id=args.preset_id,
                    video_lane=args.video_lane,
                    preset=preset,
                )
            except (RuntimeError, ValueError, OSError):
                print(
                    unbound_desk_recovery(
                        desk,
                        prompt_arg=args.prompt,
                        preset_id=preset[0],
                        just_made=True,
                    ),
                    file=sys.stderr,
                )
                raise
            # A new desk runs the current rules (creation.rules_epoch); older desks keep theirs.
            config.rules_epoch = CURRENT_EPOCH
            save_production_config(desk, config)
            print(desk)
            _warn_if_no_local_ffmpeg()
            narration = narrator_warning(prompt, desk=desk)
            if narration:
                print(narration, file=sys.stderr)
            print(f"bound session_id={state.session_id} phase={state.phase}")
            print("Next: uv run fictora-produce step --desk", desk)
            return 0
        if args.command == "bind":
            prompt = text_or_file(args.prompt, flag="--prompt")
            preset_id, video_lane = bound_preset_and_lane(
                args.desk, preset_id=args.preset_id, video_lane=args.video_lane
            )
            state = bind_desk(
                args.desk,
                prompt=prompt,
                preset_id=preset_id,
                video_lane=video_lane,
                episode_ordinal=args.episode,
            )
            # Re-binding keeps the desk's saved settings (language, voice mode,
            # caption style, tempo, format, rules epoch, ...); only flags given
            # here replace them (L-20261008-2).
            bound = merge_config_from_args(load_production_config(args.desk), args)
            save_production_config(args.desk, bound)
            print(f"bound session_id={state.session_id} phase={state.phase}")
            narration = narrator_warning(prompt, desk=args.desk)
            if narration:
                print(narration, file=sys.stderr)
            return 0
        if args.command == "config":
            print(json.dumps(asdict(load_production_config(args.desk)), indent=2))
            return 0
        if args.command == "status":
            print(status_message(args.desk))
            return 0
        if args.command == "step":
            result = run_step(
                args.desk,
                confirm_spend=args.confirm_spend,
                accept_notices=args.accept_notice,
                narrator_heard_only=args.narrator_heard_only,
                narrator_on_screen=args.narrator_on_screen,
                ask=interactive_ask(),
                single_frame_start=args.single_frame_start,
                no_single_frame_start=args.no_single_frame_start,
            )
            print(result.message)
            for path in result.paths:
                print(f"  file: {path}")
            return 0
        if args.command == "pitch":
            from creation.pitch_card import PitchRefused, run_pitch

            try:
                return run_pitch(
                    args.desk,
                    episode=args.episode or _desk_episode(args.desk),
                    file=args.file,
                )
            except PitchRefused as exc:
                print(exc, file=sys.stderr)
                return 2
        if (
            args.command == "approve"
            and args.episode is not None
            and args.gate != "pitch"
        ):
            raise ValueError("--episode is for --gate pitch only")
        if args.command == "approve" and args.gate == "pitch":
            from creation.pitch_card import run_approve_pitch

            run_approve_pitch(
                args.desk, episode=args.episode or _desk_episode(args.desk)
            )
            return 0
        if (
            args.command == "approve"
            and args.again
            and args.gate not in ("plates", "board")
        ):
            raise ValueError("--again is for --gate plates or --gate board")
        if args.command == "approve" and args.gate == "look":
            run_approve_look(args.desk, url=args.url, path=args.path)
            return 0
        if args.command == "approve":
            if args.url is not None:
                raise ValueError("--url is for --gate look only")
            result = approve_gate(
                args.desk,
                gate=args.gate,
                path=args.path,
                accept_dim=True if args.accept_dim else None,
                again=args.again,
            )
            print(result.message)
            return 0
        if args.command == "caption":
            from creation.captions import current_spine, resolve_caption_style
            from creation.post.review import saved_words

            style, style_note = resolve_caption_style(
                args.desk.expanduser().resolve(), args.caption_style
            )
            if style_note:
                print(f"note: {style_note}")
            if style == "none":
                print("caption style none: nothing burned.")
                return 0

            # The lines the story has now, not a desk copy saved before a line was changed (L-20261001-8).
            spine, spine_note = current_spine(args.desk, args.episode)
            if spine_note:
                print(spine_note)

            words_json = args.words_json or (
                saved_words(args.desk.expanduser().resolve(), args.episode, "t1")
                if args.take is None
                else None
            )
            result = caption_take(
                args.desk,
                episode_ordinal=args.episode,
                take=args.take,
                line_starts=args.line_start,
                line_ends=args.line_end,
                words_json=words_json,
                # A take file gets only its own beats' lines; a joined episode file gets them all.
                take_index=take_index_from_name(args.take) if args.take else None,
                style=style,
                spine=spine,
            )
            ep_dir = args.desk.expanduser().resolve() / f"ep{args.episode:02d}"
            timing = "; ".join(result.timing_lines())
            warnings = "".join(
                f" {w}."
                for w in (
                    *result.not_english,
                    result.font_warning,
                    result.take_lines_warning,
                    result.timing_warning,
                    spine_note,
                )
                if w
            )
            append_run_note(
                ep_dir,
                f"Local {style} captions ({'whole English lines' if result.whole_lines else 'word flicker'}): "
                f"{result.video.name} (cues {result.ass.name}). Lines: {timing}.{warnings}",
            )
            italic = result.italic or (False,) * len(result.lines)
            methods = result.methods or ("speech",) * len(result.lines)
            shown = result.shown or (None,) * len(result.lines)
            for line, span, seen, how, slanted in zip(
                result.lines, result.anchors, shown, methods, italic
            ):
                mark = "  (italic: heard, not seen)" if slanted else ""
                span = seen or span
                print(f"  {span.start:6.2f}-{span.end:6.2f}s  {line}{mark}  [{how}]")
            for warning in (*result.not_english, result.timing_warning):
                if warning:
                    print(warning)
            print(f"  file: {result.ass}")
            print(f"  file: {result.video}")
            print(
                "Human QC: watch the captioned take. Wrong timing? Re-run with --line-start / --line-end per line."
            )
            if not args.no_open and sys.platform == "darwin":
                subprocess.run(
                    ["open", str(result.video)], stdin=subprocess.DEVNULL, check=False
                )
            return 0
        if args.command == "cancel-job":
            payload, lines = cancel_video_job(args.desk, args.job_id)
            print(payload)
            for line in lines:
                print(line)
            return 0
        if args.command == "retry-step":
            retried = retry_failed_step(args.desk, cause=args.cause, phase=args.phase)
            how = " (read from the saved error)" if retried.inferred else ""
            print(f"phase={retried.phase}{how} key_prefix={retried.key_prefix}")
            print(f"Will re-run {retried.cost}.")
            print(f"Backup: {retried.backup}")
            print("Next: fictora-produce step --desk …")
            return 0
        if args.command == "retry-video":
            if not args.new_paid_take:
                print(
                    "Refused. retry-video starts a new paid take and another ffmpeg job on Railway.",
                    file=sys.stderr,
                )
                print(
                    "Pass --new-paid-take only after a human yes for a new take.",
                    file=sys.stderr,
                )
                return 2
            suffix = prepare_video_retry(args.desk, job_id=args.job_id)
            print(f"phase=ready_video suffix={suffix}")
            print(
                "Hosted post-production is off: after the take lands, run `fictora-produce finish --desk …`."
            )
            print("Next: uv run fictora-produce step --desk … --confirm-spend")
            return 0
    except (
        RuntimeError,
        ValueError,
        FileNotFoundError,
        FileExistsError,
        PermissionError,
        subprocess.CalledProcessError,
    ) as exc:
        print(exc, file=sys.stderr)
        return 2
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
