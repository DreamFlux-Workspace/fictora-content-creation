"""``fictora-produce`` local post commands: voice, voice-fx, revoice, voice-line, cue, music-note, finish, join, review, and the edits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from creation.cli_text import HELP_SUFFIX, text_or_file
from creation.post.edit_commands import EDIT_COMMANDS, add_edit_parsers, dispatch_edit
from creation.post.finish import FINISH_INCOMPLETE, run_finish
from creation.post.hand import parse_caption_label, parse_placed, parse_range
from creation.post.join import JOIN_NOT_DONE, run_join
from creation.post.handmade import CUE_DEFAULT_SECONDS
from creation.post.review_command import (
    REVIEW_COMMANDS,
    add_review_parser,
    dispatch_review,
)
from creation.post.sfx import parse_adjustment
from creation.post.thumbnail import THUMBNAIL_USD

POST_COMMANDS = (
    frozenset(
        {
            "voice",
            "voice-fx",
            "revoice",
            "voice-line",
            "cue",
            "set-bed",
            "music-note",
            "finish",
            "join",
            "reel",
        }
    )
    | EDIT_COMMANDS
    | REVIEW_COMMANDS
)


def add_caption_style_arg(parser: argparse.ArgumentParser) -> None:
    """``--caption-style house|plain|none`` on ``finish`` and ``caption`` (default: the desk's config)."""

    from creation.captions import CAPTION_STYLES

    parser.add_argument(
        "--caption-style",
        choices=CAPTION_STYLES,
        default=None,
        help="house: yellow Arial Bold word flicker (the default); plain: white whole lines, same size and "
        "safe band; none: no captions. Default: caption_style in the desk's production.config.json.",
    )


def add_post_parsers(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the local post subcommands.

    Parameters
    ----------
    sub
        ``fictora-produce`` subparsers.
    """

    voice = sub.add_parser(
        "voice",
        help="Change a character's voice (never regenerate): --audition renders 4-10 candidates on their real "
        "lines ($0.30 a set, rendered on the server); --pick N locks one on the cast card (free).",
    )
    voice.add_argument("--desk", type=Path, required=True)
    voice.add_argument("--cast", required=True, help="cast_id or name.")
    mode = voice.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--audition", action="store_true", help="Render a candidate set and list it."
    )
    mode.add_argument(
        "--pick",
        default=None,
        metavar="N|NAME",
        help="Lock candidate N (or that voice) from the newest set.",
    )
    voice.add_argument(
        "--episode",
        type=int,
        default=None,
        help="Audition on this episode's lines only.",
    )
    voice.add_argument("--count", type=int, default=8, help="Candidates, 4-10.")
    voice.add_argument(
        "--cause",
        default=None,
        help="Why a second audition set is paid for (required for one).",
    )
    voice.add_argument(
        "--text",
        default=None,
        help="Audition this wording (new or on the spine, up to 300 characters; sent to the server as is).",
    )
    voice.add_argument(
        "--voices",
        default=None,
        metavar="A,B,...",
        help="Exactly these Eleven v3 voices, in this order, instead of the default slate (--count ignored).",
    )

    fx = sub.add_parser(
        "voice-fx",
        help="Give a stretch of a take's voice a source (intercom, phone, radio): local ffmpeg, $0, a new file.",
    )
    fx.add_argument(
        "--file", type=Path, required=True, help="The take (or audio file) to treat."
    )
    fx.add_argument(
        "--range",
        dest="span",
        action="append",
        required=True,
        metavar="A-B",
        help="Seconds treated, like 0-3.5. Repeat for every line the voice speaks: one file.",
    )
    fx.add_argument("--preset", required=True, choices=("intercom", "phone", "radio"))
    fx.add_argument(
        "--desk", type=Path, default=None, help="Series desk, for a run note."
    )
    fx.add_argument("--episode", type=int, default=1)

    revoice = sub.add_parser(
        "revoice",
        help="Replace one character's lines on a filmed take with their locked (newly picked) voice: mute the "
        "original, lay the new line in. Writes take-epNN-tK-revoice-vN.mp4. Then run finish --take-file on it.",
    )
    revoice.add_argument("--desk", type=Path, required=True)
    revoice.add_argument("--cast", required=True, help="cast_id or name.")
    revoice.add_argument("--episode", type=int, default=1)
    revoice.add_argument("--take", dest="take_id", default="t1")
    revoice.add_argument(
        "--take-file", type=Path, default=None, help="Default: the newest raw take."
    )
    revoice.add_argument(
        "--words-json",
        type=Path,
        default=None,
        help="Saved Whisper words; default: transcribe.",
    )
    revoice.add_argument(
        "--voice-db", type=float, default=0.0, help="Gain on the new lines."
    )
    revoice.add_argument(
        "--over-locked-voices",
        action="store_true",
        help="Revoice a take whose sound is already the locked voices (take facts soundtrack target_audio): "
        "only when a new voice was picked after it was filmed. Refused without it.",
    )

    line = sub.add_parser(
        "voice-line",
        help="One dry line in a character's locked voice, made on the server ($0.10 per 1,000 characters, "
        "Whisper-checked). Saved to epNN/voices/; lay it with finish --voice PATH@SECONDS.",
    )
    line.add_argument("--desk", type=Path, required=True)
    line.add_argument("--cast", required=True, help="cast_id or name.")
    line.add_argument(
        "--text",
        required=True,
        help="The line as performed, in the show's spoken language.",
    )
    line.add_argument("--episode", type=int, default=1)
    line.add_argument(
        "--spoken-text",
        default=None,
        help="Phonetic spelling sent to the voice instead of --text.",
    )

    cue = sub.add_parser(
        "cue",
        help="One hand sound cue made on the server from a description (~$0.002 a second). Saved to epNN/sfx/; "
        "prints its RMS per half second and shape check. Place it with finish --cue PATH@SECONDS[@DB].",
    )
    cue.add_argument("--desk", type=Path, required=True)
    cue.add_argument("--episode", type=int, required=True)
    cue.add_argument(
        "--description",
        required=True,
        help='What it sounds like: "a descending comic brass sting".',
    )
    cue.add_argument(
        "--seconds", type=float, default=CUE_DEFAULT_SECONDS, help="Length, 0.5-22 s."
    )

    bed = sub.add_parser(
        "set-bed",
        help="Gone: the music is the harness's, never a file you pin. Says how to ask for a change (music-note).",
    )
    bed.add_argument("--desk", type=Path, default=None)
    bed.add_argument("--path", type=Path, default=None, help=argparse.SUPPRESS)

    note = sub.add_parser(
        "music-note",
        help='Say what should change about the show\'s music ("calmer", "quieter under the lines") and send it to '
        "the harness: the note is saved on the desk (shared/music-notes.jsonl), the harness's plan and price are "
        "shown (a dry run), and it is applied only with --yes. Takes that can only change by filming again need "
        "--confirm-refilm too. --revert N puts music version N back; --send-saved sends saved notes not yet "
        "applied. The kit never picks or makes music itself.",
    )
    note.add_argument("--desk", type=Path, required=True)
    note.add_argument(
        "--episode", type=int, default=None, help="Default: the whole show."
    )
    note.add_argument(
        "--take", dest="take_id", default=None, help="With --episode: one take (tK)."
    )
    note.add_argument(
        "note",
        nargs="?",
        default=None,
        help=f"The change, in your words. {HELP_SUFFIX}",
    )
    note.add_argument(
        "--revert",
        type=int,
        default=None,
        metavar="N",
        help="Put the show's music version N back (free; plan first, --yes applies).",
    )
    note.add_argument(
        "--send-saved",
        action="store_true",
        help="Send every saved note the harness has not applied yet (each planned first; --yes applies).",
    )
    note.add_argument(
        "--save-only",
        action="store_true",
        help="Only save the note on the desk; send it later with --send-saved.",
    )
    note.add_argument(
        "--yes",
        action="store_true",
        help="Apply after showing the plan (new music is about $0.20; level notes and reverts are free). "
        "Without it nothing is changed or spent.",
    )
    note.add_argument(
        "--confirm-refilm",
        action="store_true",
        help="Also film again the takes whose music the video model made, at their usual take price (the "
        "total is shown in the plan). Without it those takes keep their old music.",
    )
    note.add_argument(
        "--wait",
        type=float,
        default=None,
        metavar="SECONDS",
        help="How long to follow the episodes being put together again after applying (default 600; 0 reads once).",
    )

    fin = sub.add_parser(
        "finish",
        help="Finish an accepted take on this laptop: board frames out, SFX, the harness's music bed, colour match to the board, mix, captions, "
        f"Sokii mark. Exits {FINISH_INCOMPLETE} (NOT DONE) when music, SFX or the mix did not go on.",
    )
    fin.add_argument("--desk", type=Path, required=True)
    fin.add_argument("--episode", type=int, default=1)
    fin.add_argument("--take", dest="take_id", default="t1")
    fin.add_argument(
        "--take-file",
        type=Path,
        default=None,
        help="Default: the newest raw take (or a revoice file).",
    )
    fin.add_argument(
        "--no-deboard",
        action="store_true",
        help="Keep the take's opening frames even when they are the board.",
    )
    fin.add_argument(
        "--no-colour-match", action="store_true", help="Keep the take's own look."
    )
    fin.add_argument(
        "--colour-strength", type=float, default=1.0, help="0..1 toward the board."
    )
    fin.add_argument(
        "--bed-db",
        type=float,
        default=None,
        help="Music bed level in the mix (dB on the bed file). Default: the desk's series.json bed_db, else "
        "measured from the bed so it sits about 9 dB under the dialogue (printed with why).",
    )
    fin.add_argument(
        "--music",
        default=None,
        help='A music change note for the harness ("calmer"): saved like `music-note`, never used to make or '
        f"pick music here. {HELP_SUFFIX}",
    )
    fin.add_argument(
        "--duck-db",
        type=float,
        default=None,
        help="Duck the bed exactly N dB (1-30) under the voice.",
    )
    fin.add_argument(
        "--sfx-adjust",
        action="append",
        default=[],
        help='This take only, on top of the sound notes the take facts carry: "door=-6", "hum=drop", "shot:3=+4".',
    )
    fin.add_argument(
        "--line-start",
        type=float,
        action="append",
        default=None,
        help="Caption line start, per line.",
    )
    fin.add_argument(
        "--line-end",
        type=float,
        action="append",
        default=None,
        help="Caption line end (the caption goes off exactly here), once per line in order.",
    )
    fin.add_argument(
        "--mute", action="append", default=[],
        help="Silence stray speech in the take's own audio: A-B seconds on the take as filmed (repeat).",
    )  # fmt: skip
    fin.add_argument(
        "--voice", action="append", default=[],
        help="Lay a dry line (voice-line): PATH@SECONDS[@DB] on the take as filmed; levelled unless @DB (repeat).",
    )  # fmt: skip
    fin.add_argument(
        "--sign-overlay",
        action="store_true",
        help="Where the text check finds possible garbled lettering in a shot whose take facts carry a story "
        "sign, draw the sign's exact words in the house font over it for that shot (instead of a blur). "
        "Without it, finish prints the suggestion.",
    )
    fin.add_argument(
        "--caption-label", action="append", default=[], metavar="TEXT@A-B",
        help="A caption with no spoken line under it (a short line the take never says clearly): "
        "TEXT@A-B seconds on the take as filmed, drawn like the other captions (repeat).",
    )  # fmt: skip
    fin.add_argument(
        "--cue", action="append", default=[],
        help="Lay a hand cue (cue): PATH@SECONDS[@DB] on the take as filmed; -8 dB unless @DB, clamped (repeat).",
    )  # fmt: skip
    fin.add_argument(
        "--over-locked-voices",
        action="store_true",
        help="Allow --mute, --voice or a revoice file on a take whose sound is already the locked voices "
        "(take facts soundtrack target_audio). Refused without it.",
    )
    fin.add_argument(
        "--watermark-y",
        type=int,
        default=None,
        help="Mark top offset (never into the top 8%%).",
    )
    cover = fin.add_mutually_exclusive_group()
    cover.add_argument(
        "--thumbnail",
        action="store_true",
        help="Draw the episode cover on the server when none is on the desk yet "
        f"(${THUMBNAIL_USD:.2f}, after the human's yes; free when the server already drew this clip). "
        "Without it, finish reuses a saved cover or spends nothing.",
    )
    cover.add_argument(
        "--no-thumbnail",
        action="store_true",
        help="No cover on the deliverable (not even a saved one).",
    )
    add_caption_style_arg(fin)
    fin.add_argument(
        "--json", action="store_true", help="Print the report as JSON on stdout."
    )

    join = sub.add_parser(
        "join",
        help="Join finished takes into one file (free): one harness bed across the join, a cut between takes and a "
        f"0.25 s dissolve between episodes, 24 fps asserted, seam steps under 5 dB, marked once. Exits "
        f"{JOIN_NOT_DONE} (NOT DONE, nothing marked) when a seam steps more than 5 dB.",
    )
    join.add_argument("--desk", type=Path, required=True)
    which = join.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "--episode",
        type=int,
        default=None,
        help="Every take of this episode, newest finish, in order.",
    )
    which.add_argument(
        "--episodes",
        type=int,
        nargs="+",
        default=None,
        help="A series cut: these episodes in order.",
    )
    which.add_argument(
        "--take-file", type=Path, action="append", default=None,
        help="A finished take (the -sokii final or its -cap file), in order (repeat).",
    )  # fmt: skip
    join.add_argument(
        "--from-record", type=Path, action="append", default=None, metavar="RECORDED_FILE",
        help="A --take-file no finish record names (a re-captioned copy) joins as the recorded file it was "
        "made from: same size, frames and sound are checked, and the stand-in goes in the run notes. "
        "The Nth --from-record goes with the Nth such --take-file. Use the un-marked -cap master, not the final.",
    )  # fmt: skip
    join.add_argument(
        "--dissolve",
        type=float,
        default=None,
        help="Seconds at every seam (0 = straight cut).",
    )
    join.add_argument(
        "--bed-db",
        type=float,
        default=None,
        help="Default: the desk's series.json bed_db, else the level the takes were finished at when one "
        "was chosen, else measured from the bed (about 9 dB under the dialogue).",
    )
    join.add_argument(
        "--duck-db",
        type=float,
        default=None,
        help="Default: what the takes were finished with.",
    )
    join.add_argument(
        "--no-gain-match", action="store_true", help="Keep each take's own level."
    )
    join.add_argument(
        "--watermark-y",
        type=int,
        default=None,
        help="Mark top offset (never into the top 8%%).",
    )
    join.add_argument(
        "--accept-seam",
        default=None,
        metavar="WHY",
        help="A human listened through the master and accepts every seam over 5 dB (say why); the join "
        "is marked anyway and who and why go in the run notes. Needs --accepted-by.",
    )
    join.add_argument(
        "--accepted-by",
        default=None,
        metavar="NAME",
        help="Who accepted the seam (with --accept-seam).",
    )
    join.add_argument(
        "--json", action="store_true", help="Print the report as JSON on stdout."
    )

    reel = sub.add_parser(
        "reel",
        help="A social reel (IG/TikTok) cut from the episode's rendered footage: local, $0, no new video. "
        "Opens on the strongest frame as a short flash-forward, ends on the new fact; captions re-timed "
        "from the accepted cut; one bed; the Sokii mark. Writes only under <desk>/reels/.",
    )
    reel.add_argument("--desk", type=Path, required=True)
    reel.add_argument("--episode", type=int, required=True)
    reel.add_argument(
        "--seconds",
        type=float,
        default=15.0,
        help="Target length, 6-30 s (default 15).",
    )
    reel.add_argument(
        "--plan-only", action="store_true",
        help="Write and print the plan (reels/reel-plan-epNN-vN.json); render nothing.",
    )  # fmt: skip
    reel.add_argument(
        "--plan", type=Path, default=None, help="Render this (hand-edited) plan JSON."
    )
    reel.add_argument(
        "--take-file", action="append", default=None, metavar="[tK=]FILE",
        help="The accepted finished file of a take (picks its finish record; with none, its source and captions "
        "are worked out from the desk, each step printed with a ⚠). Default: the newest record, else the newest "
        "-sokii file the run notes name.",
    )  # fmt: skip
    reel.add_argument(
        "--source", action="append", default=None, metavar="[tK=]FILE",
        help="The take's picture before captions (default: the finish record's pre_bed).",
    )  # fmt: skip
    reel.add_argument(
        "--captions", action="append", default=None, metavar="[tK=]FILE.ass",
        help="The accepted cut's caption file (default: the record master's .ass).",
    )  # fmt: skip
    add_caption_style_arg(reel)
    reel.add_argument("--watermark-y", type=int, default=None)

    add_edit_parsers(sub)
    add_review_parser(sub)


def dispatch_post(args: argparse.Namespace) -> int:
    """Run one local post command.

    Parameters
    ----------
    args
        Parsed namespace whose ``command`` is in :data:`POST_COMMANDS`.

    Returns
    -------
    int
        ``0`` done; ``5`` finish or join NOT DONE.

    Raises
    ------
    ValueError
        When the command is unknown.
    """

    if args.command in EDIT_COMMANDS:
        return dispatch_edit(args)
    if args.command in REVIEW_COMMANDS:
        return dispatch_review(args)

    from creation.post.bed import MUSIC_IS_HARNESS
    from creation.post.handmade import run_cue, run_voice_line
    from creation.post.voice import run_revoice, run_voice_audition, run_voice_pick

    if args.command == "voice":
        if args.audition:
            run_voice_audition(
                args.desk, cast=args.cast, episode=args.episode, count=args.count, cause=args.cause,
                text=args.text, voices=args.voices,
            )  # fmt: skip
        else:
            if args.text or args.voices:
                raise ValueError("--text and --voices go with --audition")
            run_voice_pick(args.desk, cast=args.cast, pick=args.pick)
        return 0
    if args.command == "voice-fx":
        from creation.ops.notes import append_run_note
        from creation.post.voice_fx import apply_voice_fx
        from creation.post.voice_fx import parse_range as parse_fx_span

        spans = [parse_fx_span(raw) for raw in args.span]
        made = apply_voice_fx(
            args.file.expanduser().resolve(), ranges=spans, preset=args.preset
        )
        if args.desk is not None:
            run_dir = args.desk.expanduser().resolve() / f"ep{args.episode:02d}"
            if (run_dir / "run-notes.md").is_file():
                treated = ", ".join(f"{a:.2f}-{b:.2f}s" for a, b in sorted(spans))
                append_run_note(
                    run_dir,
                    f"voice-fx {args.preset} on `{args.file.name}` {treated} -> `{made.name}`, $0.",
                )
        print(made)
        print("Listen to it; then finish (or caption) with --take-file on it.")
        return 0
    if args.command == "revoice":
        run_revoice(
            args.desk,
            cast=args.cast,
            episode=args.episode,
            take_id=args.take_id,
            take_file=args.take_file,
            words_json=args.words_json,
            voice_db=args.voice_db,
            over_locked_voices=args.over_locked_voices,
        )
        return 0
    if args.command == "voice-line":
        run_voice_line(
            args.desk,
            cast=args.cast,
            text=args.text,
            episode=args.episode,
            spoken_text=args.spoken_text,
        )
        return 0
    if args.command == "cue":
        run_cue(
            args.desk,
            episode=args.episode,
            description=args.description,
            seconds=args.seconds,
        )
        return 0
    if args.command == "set-bed":
        print(f"set-bed is gone. {MUSIC_IS_HARNESS}")
        return 2
    if args.command == "music-note":
        from creation.post.music_send import DEFAULT_WAIT_SECONDS, run_music_note

        return run_music_note(
            args.desk,
            note=text_or_file(args.note, flag="music-note")
            if args.note is not None
            else None,
            episode=args.episode,
            take_id=args.take_id,
            revert=args.revert,
            send_saved=args.send_saved,
            save_only=args.save_only,
            yes=args.yes,
            confirm_refilm=args.confirm_refilm,
            wait_seconds=DEFAULT_WAIT_SECONDS if args.wait is None else args.wait,
        )
    if args.command == "finish":
        result = run_finish(
            args.desk,
            episode=args.episode,
            take_id=args.take_id,
            take_file=args.take_file,
            deboard=not args.no_deboard,
            colour=not args.no_colour_match,
            colour_strength=args.colour_strength,
            bed_db=args.bed_db,
            music=text_or_file(args.music, flag="--music") if args.music else None,
            duck_db=args.duck_db,
            sfx_adjust=tuple(parse_adjustment(raw) for raw in args.sfx_adjust),
            line_starts=tuple(args.line_start) if args.line_start else None,
            line_ends=tuple(args.line_end) if args.line_end else None,
            watermark_y=args.watermark_y,
            mutes=tuple(parse_range(raw) for raw in args.mute),
            voices=tuple(parse_placed(raw, flag="--voice") for raw in args.voice),
            cues=tuple(parse_placed(raw, flag="--cue") for raw in args.cue),
            caption_labels=tuple(
                parse_caption_label(raw) for raw in args.caption_label
            ),
            thumbnail=not args.no_thumbnail,
            draw_thumbnail=args.thumbnail,
            over_locked_voices=args.over_locked_voices,
            caption_style=args.caption_style,
        )
        if args.json:
            print(json.dumps(result.as_json(), indent=2))
        else:
            print(result.final)
        return 0 if result.complete else FINISH_INCOMPLETE
    if args.command == "reel":
        from creation.post.reel import run_reel

        run_reel(
            args.desk,
            episode=args.episode,
            seconds=args.seconds,
            plan_only=args.plan_only,
            plan_file=args.plan,
            take_files=tuple(args.take_file or ()),
            sources=tuple(args.source or ()),
            captions=tuple(args.captions or ()),
            caption_style=args.caption_style,
            watermark_y=args.watermark_y,
        )
        return 0
    if args.command == "join":
        joined = run_join(
            args.desk,
            episodes=tuple(
                args.episodes or ([args.episode] if args.episode is not None else [])
            ),
            take_files=tuple(args.take_file or ()),
            from_records=tuple(args.from_record or ()),
            dissolve=args.dissolve,
            bed_db=args.bed_db,
            duck_db=args.duck_db,
            gain_match=not args.no_gain_match,
            watermark_y=args.watermark_y,
            accept_seam=args.accept_seam,
            accepted_by=args.accepted_by,
        )
        if args.json:
            print(json.dumps(joined.as_json(), indent=2))
        else:
            print(joined.marked or joined.master)
        return 0 if joined.complete else JOIN_NOT_DONE
    raise ValueError(f"unknown command {args.command}")
