"""``fictora-produce`` local post commands: voice, voice-fx, revoice, voice-line, cue, music-note, finish, join, review, and the edits."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from pathlib import Path

from creation.cli_text import HELP_SUFFIX, text_or_file
from creation.post.edit_commands import EDIT_COMMANDS, add_edit_parsers, dispatch_edit
from creation.post.handmade import CUE_DEFAULT_SECONDS
from creation.post.impact_post import MAX_EDITOR_HITS
from creation.post.review_command import (
    REVIEW_COMMANDS,
    add_review_parser,
    dispatch_review,
)
from creation.post.server_finish import (
    DEFAULT_WAIT_SECONDS,
    SERVER_FINISH_NOT_DONE,
    run_server_finish,
)

POST_COMMANDS = (
    frozenset(
        {
            "voice",
            "voice-mode",
            "music-lock",
            "caption-style",
            "hook-line",
            "voice-fx",
            "revoice",
            "voice-line",
            "cue",
            "set-bed",
            "music-note",
            "rescore-music",
            "finish",
            "join",
        }
    )
    | EDIT_COMMANDS
    | REVIEW_COMMANDS
)


def _impact_hits(values: list[Any]) -> tuple[Any, ...]:
    """Every ``--impact-at`` hit, refused when there are more than the server takes (it would refuse them all)."""

    hits = tuple(hit for group in values for hit in group)
    if len(hits) > MAX_EDITOR_HITS:
        raise SystemExit(
            f"fictora-produce finish: --impact-at names {len(hits)} hits; the server takes at most "
            f"{MAX_EDITOR_HITS} a take. Keep the {MAX_EDITOR_HITS} that matter most."
        )
    return hits


def add_caption_style_arg(parser: argparse.ArgumentParser) -> None:
    """``--caption-style bold|subtle|house|plain|none`` on ``finish`` and ``caption`` (default: the show's)."""

    from creation.captions import CAPTION_STYLES

    parser.add_argument(
        "--caption-style",
        choices=CAPTION_STYLES,
        default=None,
        help="bold: one short white line at a time, one yellow word, bigger and lower (a new show's "
        "default); subtle (or house, its older name): the yellow Arial Bold word flicker (a show with "
        "finished episodes keeps it); plain: white whole lines; none: no captions. Default: caption_style "
        "in the desk's production.config.json, else bold for a new show and subtle for a continuing one.",
    )


def add_caption_scale_arg(parser: argparse.ArgumentParser) -> None:
    """``--caption-scale X`` on ``finish`` and ``caption``: the captions bigger or smaller, kit-placed."""

    parser.add_argument(
        "--caption-scale",
        type=float,
        default=1.0,
        metavar="X",
        help="Every caption line at the style's size times X (0.5-2; default 1, the house size). "
        "The kit wraps and places the bigger lines itself (the hook card keeps its own size) and "
        "the master's .ass keeps every line, so never re-burn captions by hand. 45 on a 1344-high take already IS the house 64 on 1920.",
    )


#: Settings ``finish`` and ``join`` took when they ran on this laptop (before 30 Oct 2026), and what
#: happens to each now. The server finishes the episode; these are refused by name, never silently
#: dropped. ``--caption-label``, ``--no-caption-labels``, ``--episodes`` and the take edits (``--mute``,
#: ``--voice``, ``--cue``, ``--impact-at`` with ``--take tK``, fictora-drama #1189 / #1190) are not
#: here: they go to the server's own routes.
_LAPTOP_FLAG_HOMES: dict[str, str] = {
    # Has a home today.
    "--music": "a music change goes through `music-note`",
    "--accept-line-mismatch": "keeping a take whose line came out wrong is --keep-line-findings tK",
    "--over-locked-voices": "not needed any more: the server decides whether a take's voices can be muted "
    "or re-voiced, and says so when they cannot",
    # No server field for it.
    "--sfx-adjust": 'a sound\'s level is a sound note on the story (`sound-note --desk D "quieter door"`, '
    "every take); a level for one take only waits for the server's take edits",
    # Gone for good: what the server does instead.
    "--sign-overlay": "the server writes a sign's words into the picture when it films; nothing is drawn on top",
    "--prop-text": "the server keeps notes and letters unreadable when it films; nothing is drawn on top",
    "--prop-box": "the server keeps notes and letters unreadable when it films; nothing is drawn on top",
    "--ending": "episodes end on a hard cut; a freeze-to-black ending is not made",
    "--caption-style": "captions are the server's; the show's caption style and size are kept on the server, "
    "not on the desk",
    "--caption-scale": "captions are the server's; the show's caption style and size are kept on the server, "
    "not on the desk",
    "--caption-colour": "captions are the server's; the show's caption style and size are kept on the server, "
    "not on the desk",
    "--no-colour-match": "the server films close to the approved board; colour match is off",
    "--colour-strength": "the server films close to the approved board; colour match is off",
    "--no-deboard": "the server always cuts storyboard frames off the start of a take",
    "--no-impact": "the server places the impact punch-ins itself",
    "--no-panels": "the server draws a system or game show's panels itself",
    "--thought-db": "the server sets how loud inner-voice lines are",
    "--line-start": "the server times each caption from the line's own sound",
    "--line-end": "the server times each caption from the line's own sound",
    "--watermark-y": "the server owns where the Sokii mark and the hook title sit",
    "--hook-line-position": "the server owns where the Sokii mark and the hook title sit",
    "--no-cover": "the server always draws the cover; leave it out when posting if it is not wanted",
    "--take-file": "the server joins the takes and evens their loudness itself",
    "--from-record": "the server joins the takes and evens their loudness itself",
    "--dissolve": "the server joins the takes and evens their loudness itself",
    "--gain-match": "the server joins the takes and evens their loudness itself",
    "--no-gain-match": "the server joins the takes and evens their loudness itself",
    "--accept-seam": "the server joins the takes and evens their loudness itself",
    "--accepted-by": "the server joins the takes and evens their loudness itself",
    "--no-seam-fix": "the server joins the takes and evens their loudness itself",
}
LAPTOP_ONLY_FLAGS: tuple[str, ...] = tuple(_LAPTOP_FLAG_HOMES)
#: On ``join`` only: caption labels belong to one take, so they go with ``finish --take tK``.
_JOIN_REFUSED_HOMES: dict[str, str] = {
    "--caption-label": "caption labels belong to one take: `finish --desk D --episode N --take tK "
    '--caption-label "TEXT@A-B"`',
    "--no-caption-labels": "caption labels belong to one take: `finish --desk D --episode N --take tK "
    "--no-caption-labels`",
    **{
        flag: f"an edit belongs to one take: `finish --desk D --episode N --take tK {flag} ...`"
        for flag in ("--mute", "--voice", "--cue", "--impact-at")
    },
}


class _LaptopOnly(argparse.Action):
    """Remember a laptop-only setting by name so the command can refuse it plainly."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        seen = list(getattr(namespace, self.dest, None) or [])
        if option_string and option_string not in seen:
            seen.append(option_string)
        setattr(namespace, self.dest, seen)


def _add_server_finish_args(parser: argparse.ArgumentParser) -> None:
    """``--wait``, ``--json``, the hook line, and the old laptop settings (refused) on ``finish`` and ``join``."""

    parser.add_argument(
        "--wait",
        type=float,
        default=None,
        metavar="SECONDS",
        help=f"Longest wait for the server (default {DEFAULT_WAIT_SECONDS / 60:g} minutes).",
    )
    title = parser.add_mutually_exclusive_group()
    title.add_argument(
        "--hook-line",
        default=None,
        metavar="TEXT",
        help="Your own hook line: saved on the server first as the episode's hook line, then the server "
        "finishes the episode with it.",
    )
    title.add_argument(
        "--no-hook-line",
        action="store_true",
        help="Turn the episode's hook line off on the server, then finish.",
    )
    parser.add_argument(
        "--keep-line-findings",
        action="append",
        default=[],
        metavar="TAKE",
        help="The human listened to a take whose line came out wrong and keeps it as it is (tK, or all): "
        "recorded on the server, then the episode is finished.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the result as JSON on stdout."
    )
    parser.add_argument(
        "--episodes",
        type=int,
        nargs="+",
        default=None,
        help="Show the show's series cut instead (the server makes it from every finished episode; "
        "nothing is joined on this laptop).",
    )
    for flag in LAPTOP_ONLY_FLAGS:
        parser.add_argument(
            flag,
            dest="laptop_only",
            action=_LaptopOnly,
            nargs="*",
            default=None,
            help=argparse.SUPPRESS,
        )


def laptop_only_refusal(command: str, flags: list[str]) -> str:
    """The plain stop for old laptop settings on ``finish`` / ``join``."""

    grouped: dict[str, list[str]] = {}
    for flag in flags:
        home = _LAPTOP_FLAG_HOMES.get(flag) or _JOIN_REFUSED_HOMES.get(flag, "")
        grouped.setdefault(home, []).append(flag)
    rows = [
        f"  {', '.join(names)}: {home}." if home else f"  {', '.join(names)}."
        for home, names in grouped.items()
    ]
    return "\n".join(
        [
            f"Stopped: {command} now gets the episode finished on the server, made the same way as an app "
            f"episode. These were laptop settings and are not used any more: {', '.join(flags)}.",
            *rows,
            f"Run {command} again without them. Nothing was made or charged.",
        ]
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
        help="Hear, approve or change a character's voice (never regenerate): --audition renders 4-10 candidates "
        "on their real lines ($0.30 a set, rendered on the server); --pick N locks one on the cast card (free, "
        "and is the human's yes); --keep / --keep-all approve the voice as it is (free). Filming waits for a yes "
        "on every speaking character's voice.",
    )
    voice.add_argument("--desk", type=Path, required=True)
    voice.add_argument(
        "--accept-notice",
        action="append",
        default=[],
        choices=("real_person_not_allowed",),
        help="With --pick: the creator saw the server's notice that the voice names a real person and says it "
        "is someone they made up; sends acknowledged_notices (fictora-drama #833).",
    )
    voice.add_argument(
        "--cast", default=None, help="cast_id or name (not with --keep-all or --list)."
    )
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
    mode.add_argument(
        "--keep",
        action="store_true",
        help="The human heard this character's locked voice and keeps it (free; records the yes filming waits for).",
    )
    mode.add_argument(
        "--keep-all",
        action="store_true",
        help="The human keeps every speaking character's locked voice as it is (free).",
    )
    mode.add_argument(
        "--list",
        action="store_true",
        help="Print each speaking character's voice and whether it has the human's yes (free).",
    )
    voice.add_argument(
        "--episode",
        type=int,
        default=None,
        help="Audition on this episode's lines only.",
    )
    voice.add_argument(
        "--heard",
        action="store_true",
        help="With --keep / --keep-all: the human heard each voice (its sample or audition). "
        "Desks from 6 Oct 2026 keep nothing without it.",
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

    hook = sub.add_parser(
        "hook-line",
        help="List or choose an episode's on-screen hook line (free): --list reads the saved spine (no call); "
        '--pick K selects the writer\'s option K, --text "..." sets your own, --off shows none, on the route '
        "the app uses. An older server: the pick is kept on the desk for finish and join.",
    )
    hook.add_argument("--desk", type=Path, required=True)
    hook.add_argument("--episode", type=int, default=1)
    hook_which = hook.add_mutually_exclusive_group()
    hook_which.add_argument(
        "--list", action="store_true", help="Print the options (free, no call)."
    )
    hook_which.add_argument(
        "--pick",
        type=int,
        default=None,
        metavar="K",
        help="Option K, as --list numbers them.",
    )
    hook_which.add_argument(
        "--text", default=None, help="Your own hook line, as written."
    )
    hook_which.add_argument(
        "--off", action="store_true", help="No hook line for this episode."
    )
    hook_setup = hook.add_mutually_exclusive_group()
    hook_setup.add_argument(
        "--setup-line",
        default=None,
        metavar="TEXT",
        help="A letterbox episode's own setup line (the white title line, at most 60 characters), "
        "alone or with a hook-line choice; finish and join use it before the series title.",
    )
    hook_setup.add_argument(
        "--default-setup-line",
        action="store_true",
        help="Clear the episode's own setup line (the series title shows).",
    )
    hook_setup.add_argument(
        "--titles",
        action="store_true",
        help="Print the server's letterbox title card (free): its suggested titles, the series title and "
        "No title, the pick marked, and the yellow line. The same card the app shows.",
    )
    hook_setup.add_argument(
        "--title",
        type=int,
        default=None,
        metavar="K",
        help="Pick preview K of the server's title card (No title included). After the video the server "
        "re-finishes it with the new title, free.",
    )
    hook_setup.add_argument(
        "--no-title",
        action="store_true",
        help="No white title line on this letterbox episode (the yellow line stays).",
    )

    voice_mode = sub.add_parser(
        "voice-mode",
        help="Whose voices the show's takes speak in (free): locked (each character's locked voice, filmed as a "
        "dialogue track; the Voices gate applies) or model (the video model's own voices from the cast "
        "descriptions, as before option C; no Voices gate). Without --set it prints the show's mode and why. "
        "Takes already filmed keep their voices.",
    )
    voice_mode.add_argument("--desk", type=Path, required=True)
    voice_mode.add_argument(
        "--set",
        dest="set_to",
        default=None,
        choices=("locked", "model"),
        help="Store this mode for the show (only on the human's say-so).",
    )

    music_lock = sub.add_parser(
        "music-lock",
        help="An older show's music lock (free): finish (no music in the take) or in-take (the harness's music "
        "is in each take). New videos carry no music (20 Oct 2026); the lock only matters for an older "
        "episode's takes and their music-free stem. Without --set it prints the show's lock and the harness's.",
    )
    music_lock.add_argument("--desk", type=Path, required=True)
    music_lock.add_argument(
        "--set",
        dest="set_to",
        default=None,
        choices=("finish", "in-take"),
        help="Change the show's music on purpose (only on the human's say-so); needs --reason.",
    )
    music_lock.add_argument(
        "--reason",
        default=None,
        help="Why the music changes (logged on the desk and sent to the harness).",
    )

    caption_style = sub.add_parser(
        "caption-style",
        help="The show's captions (free): bold (one short white line at a time, one yellow word; a new "
        "show's default) or subtle (the yellow word flicker; a show with finished episodes keeps it), "
        "plain or none. Without --set it prints the show's style and why.",
    )
    caption_style.add_argument("--desk", type=Path, required=True)
    caption_style.add_argument(
        "--set",
        dest="set_to",
        default=None,
        choices=("bold", "subtle", "house", "plain", "none"),
        help="Save this style for the show (finish and caption read it).",
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
        "Whisper-checked): only an off-screen line or a thought already in the episode's script (add it "
        "first with `line --add ... --off-screen` or `inner-voice`); an on-screen line is refused before any "
        "spend (re-film the take instead). Saved to epNN/voices/; lay it with finish --take tK --voice PATH@SECONDS (a take edit on the server).",
    )
    line.add_argument("--desk", type=Path, required=True)
    line.add_argument("--cast", required=True, help="cast_id or name.")
    line.add_argument(
        "--text",
        required=True,
        help="The line as the script has it (its words, or the performed line in the show's spoken language).",
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
        help="Gone: delivered videos carry no music (20 Oct 2026). Says what to do instead.",
    )
    bed.add_argument("--desk", type=Path, default=None)
    bed.add_argument("--path", type=Path, default=None, help=argparse.SUPPRESS)

    note = sub.add_parser(
        "music-note",
        help='Say what should change about the music ("calmer", "quieter", "less music", "mix up the music", '
        '"no music here") for the whole show, one --episode, or one --take, and send it to the harness: the '
        "note is saved on the desk (shared/music-notes.jsonl), the harness's plan and price are shown (a dry "
        "run), and it is applied only with --yes. A note the harness reads as level only (quieter, louder) is a "
        "free re-mix; anything else is new music (about $0.20). A no-music note the harness would answer with "
        "new music is held, never applied. Takes that can only change by filming again need --confirm-refilm "
        "too. --revert N puts music version N back; --send-saved sends saved notes not yet applied. The kit "
        "never picks or makes music itself.",
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

    rescore = sub.add_parser(
        "rescore-music",
        help="Give already-made episodes the music the harness's CURRENT rules give (opt-in; never films): "
        "the harness lays it on each take's own voices-and-room track and joins the episode again (the old cut "
        "is kept). Prints the harness's plan first (which takes change, which are skipped and why, the cost: only "
        "new cue versions); --yes applies, then rebuilds this laptop's episode file (finish + join on the new "
        "takes, a new <slug>_epNN_draft_vN file, nothing overwritten). --revert puts back what a re-score "
        "replaced. The kit never lays or picks music itself.",
    )
    rescore.add_argument("--desk", type=Path, required=True)
    rescore_scope = rescore.add_mutually_exclusive_group(required=True)
    rescore_scope.add_argument("--episode", type=int, default=None, help="One episode.")
    rescore_scope.add_argument(
        "--all",
        dest="all_episodes",
        action="store_true",
        help="The whole show, in episode order (each episode's piece depends on what the show used just before).",
    )
    rescore.add_argument(
        "--revert",
        action="store_true",
        help="Put back the music the latest re-score replaced (free; plan first, --yes applies).",
    )
    rescore.add_argument(
        "--rescore-id",
        default=None,
        metavar="ID",
        help="With --revert: undo exactly this re-score (printed when it was applied).",
    )
    rescore.add_argument(
        "--yes",
        action="store_true",
        help="Apply after showing the plan. Without it nothing is changed or spent.",
    )

    fin = sub.add_parser(
        "finish",
        help="Get the episode finished on the server and download it (free): one mix at -14 loudness, captions, "
        "the hook title, the Sokii mark and the cover, the same as an app episode. Saved as "
        "epNN/takes/<show>_epNN_draft_vN_<date>.mp4 and cover-epNN-vN.jpg. Nothing is finished on this laptop. "
        f"Exits {SERVER_FINISH_NOT_DONE} (NOT DONE) when the server did not give the episode.",
    )
    fin.add_argument("--desk", type=Path, required=True)
    fin.add_argument(
        "--episode", type=int, default=None, help="Default: the desk's current episode."
    )
    fin.add_argument(
        "--take",
        dest="take_id",
        default=None,
        help="Not needed to finish (the server finishes the whole episode in one go); names the take "
        "for --caption-label, --no-caption-labels and the take edits (--mute, --voice, --cue, --impact-at).",
    )
    fin.add_argument(
        "--mute", action="append", default=[], metavar="A-B",
        help="Take edit (with --take tK, free): silence the take's own sound over A-B seconds on the take "
        "as delivered (repeat). Sent to the server; the episode is rebuilt there.",
    )  # fmt: skip
    fin.add_argument(
        "--voice", action="append", default=[], metavar="FILE@SECONDS[@DB] | CHARACTER",
        help="Take edit (with --take tK): lay a voice-line file at SECONDS (free; levelled unless @DB), or "
        "name a character to re-voice all their lines on the take in their locked voice (paid in tokens; "
        "the server says when a take cannot be re-voiced). Repeat.",
    )  # fmt: skip
    fin.add_argument(
        "--cue", action="append", default=[], metavar="SOUND@SECONDS[@DB]",
        help='Take edit (with --take tK, paid in tokens): add a sound the server makes, like "low rumble@2.5", '
        "or a saved cue file's sound (repeat).",
    )  # fmt: skip
    fin.add_argument(
        "--impact-at", action="append", default=[], metavar="SECONDS[:blast]",
        help="Take edit (with --take tK, free): a frame that is a hit, punched in with a shake there "
        "(comma-separated or repeated; ':blast' for the bigger one).",
    )  # fmt: skip
    fin.add_argument(
        "--preview", action="store_true",
        help="With take edits: ask the server what they would cost in tokens; nothing is changed or charged.",
    )  # fmt: skip
    fin.add_argument(
        "--caption-label", action="append", default=[], metavar="TEXT@A-B",
        help="A caption with no spoken line under it: TEXT@A-B seconds on the --take as filmed (repeat). "
        "Saved on the server as the take's labels (its notes print), then the episode is finished.",
    )  # fmt: skip
    fin.add_argument(
        "--no-caption-labels", action="store_true",
        help="Clear the --take's caption labels on the server, then finish.",
    )  # fmt: skip
    _add_server_finish_args(fin)

    join = sub.add_parser(
        "join",
        help="The same as finish: get the whole episode finished on the server and download it (free). "
        "The server puts the takes together; nothing is joined on this laptop.",
    )
    join.add_argument("--desk", type=Path, required=True)
    join.add_argument(
        "--episode", type=int, default=None, help="Default: the desk's current episode."
    )
    for flag in _JOIN_REFUSED_HOMES:
        join.add_argument(
            flag,
            dest="laptop_only",
            action=_LaptopOnly,
            nargs="*",
            default=None,
            help=argparse.SUPPRESS,
        )
    _add_server_finish_args(join)

    add_edit_parsers(sub)
    add_review_parser(sub)


def _save_hook_line_first(args: argparse.Namespace) -> None:
    """``--hook-line TEXT`` / ``--no-hook-line`` on finish or join: saved on the server, then burned (D1).

    The server checks the line and its warnings print word for word; the burn
    uses the line the server answers, so the app and join show the
    same one (decision D1, 21 Oct 2026). The kit keeps no copy on the desk.
    """

    from creation.hook_line import save_burned_hook_line
    from creation.production_state import load_production

    episode = (
        args.episode
        or load_production(args.desk.expanduser().resolve()).episode_ordinal
    )
    stream = sys.stderr if getattr(args, "json", False) else sys.stdout
    saved = save_burned_hook_line(
        args.desk,
        episode,
        text=args.hook_line if args.hook_line else None,
        off=bool(args.no_hook_line) and not args.hook_line,
        out=stream,
    )
    if args.hook_line:
        args.hook_line = saved
        args.no_hook_line = saved is None


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

    from creation.post.bed import NO_MUSIC_RULE
    from creation.post.handmade import run_cue, run_voice_line
    from creation.post.voice import run_revoice, run_voice_audition, run_voice_pick

    if args.command == "voice":
        if args.keep_all or args.list:
            if args.cast:
                raise ValueError(
                    "--keep-all and --list cover every character; drop --cast"
                )
        elif not args.cast:
            raise ValueError("--cast NAME is required (or --keep-all / --list)")
        if (args.keep or args.keep_all or args.list) and (args.text or args.voices):
            raise ValueError("--text and --voices go with --audition")
        if args.keep or args.keep_all or args.list:
            from creation.post.voice import run_voice_gate

            run_voice_gate(
                args.desk,
                cast=args.cast,
                keep=args.keep or args.keep_all,
                episode=args.episode,
                heard=args.heard,
            )
            return 0
        if args.audition:
            run_voice_audition(
                args.desk, cast=args.cast, episode=args.episode, count=args.count, cause=args.cause,
                text=args.text, voices=args.voices,
            )  # fmt: skip
        else:
            if args.text or args.voices:
                raise ValueError("--text and --voices go with --audition")
            run_voice_pick(
                args.desk,
                cast=args.cast,
                pick=args.pick,
                accept_notices=args.accept_notice,
            )
        return 0
    if args.command == "hook-line":
        from creation.hook_line import run_hook_line

        return run_hook_line(
            args.desk, episode=args.episode, list_only=args.list, pick=args.pick,
            text=args.text, off=args.off, setup_line=args.setup_line,
            default_setup_line=args.default_setup_line, titles=args.titles,
            title=args.title, no_title=args.no_title,
        )  # fmt: skip
    if args.command == "voice-mode":
        from creation.voice_mode import run_voice_mode

        run_voice_mode(args.desk, set_to=args.set_to)
        return 0
    if args.command == "music-lock":
        from creation.music_lock import run_music_lock

        run_music_lock(args.desk, set_to=args.set_to, reason=args.reason)
        return 0
    if args.command == "caption-style":
        from creation.caption_preview import run_caption_style

        run_caption_style(args.desk, set_to=args.set_to)
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
        print(f"set-bed is gone. {NO_MUSIC_RULE}")
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
    if args.command == "rescore-music":
        from creation.post.music_rescore import run_rescore_music

        return run_rescore_music(
            args.desk,
            episode=args.episode,
            all_episodes=args.all_episodes,
            revert=args.revert,
            rescore_id=args.rescore_id,
            yes=args.yes,
        )
    if args.command in ("finish", "join"):
        return _server_finish_command(args)
    raise ValueError(f"unknown command {args.command}")


def _send_caption_labels(
    desk: Path, args: argparse.Namespace, raw: list[str], *, clear: bool
) -> str | None:
    """``--caption-label`` / ``--no-caption-labels``: saved on the server's caption-labels route.

    The server keeps and burns them; the kit keeps no copy. Returns the stop line, or ``None``.
    """

    from creation.post.caption_labels import save_take_labels
    from creation.post.hand import parse_caption_label
    from creation.post.take_facts import take_number

    if not getattr(args, "take_id", None):
        return "Stopped: caption labels belong to one take; add --take tK. Nothing was made or charged."
    if clear and raw:
        return "Stopped: pass --caption-label or --no-caption-labels, not both. Nothing was made or charged."
    try:
        take = take_number(args.take_id)
        labels = () if clear else tuple(parse_caption_label(spec) for spec in raw)
        save_take_labels(desk, args.episode, take, labels, out=sys.stderr)
    except (ValueError, RuntimeError) as exc:
        return f"Stopped: {exc}. Nothing was made or charged."
    return None


def _show_series_cut(desk: Path, args: argparse.Namespace) -> int:
    """``--episodes``: print the series cut the server keeps for the show (no laptop join)."""

    from creation.post.server_finish import series_cut_lines

    ready, lines = series_cut_lines(desk, episodes=tuple(args.episodes))
    if args.json:
        print(json.dumps({"ready": ready, "lines": lines}, indent=2))
    else:
        print("\n".join(lines))
    return 0 if ready else SERVER_FINISH_NOT_DONE


#: ``finish`` settings that edit one take on the server (fictora-drama #1189 / #1190).
_TAKE_EDIT_FLAGS = (
    ("--mute", "mute"),
    ("--voice", "voice"),
    ("--cue", "cue"),
    ("--impact-at", "impact_at"),
)


def _take_edit_settings(args: argparse.Namespace) -> dict[str, list[str]]:
    """The take-edit settings given on ``finish`` (none on ``join``)."""

    return {
        dest: list(getattr(args, dest, None) or [])
        for _flag, dest in _TAKE_EDIT_FLAGS
        if getattr(args, dest, None)
    }


def _send_take_edits(
    desk: Path, args: argparse.Namespace, edits: dict[str, list[str]]
) -> int:
    """``finish --take tK --mute/--voice/--cue/--impact-at``: the server edits the take; nothing is downloaded.

    The edit answers at once and the server puts the episode back together in the background,
    so this run stops after the server's answer; a later ``finish`` downloads the new cut.
    """

    from creation.post.take_edit import TakeEditStop, build_request, run_take_edit

    named = [flag for flag, dest in _TAKE_EDIT_FLAGS if dest in edits]
    take_id = getattr(args, "take_id", None)
    if not take_id:
        print(
            f"Stopped: {', '.join(named)} edit one take; add --take tK. Nothing was changed or charged.",
            file=sys.stderr,
        )
        return 2
    mixed = [
        flag
        for flag, given in (
            ("--caption-label", getattr(args, "caption_label", None)),
            ("--no-caption-labels", getattr(args, "no_caption_labels", False)),
            ("--hook-line", getattr(args, "hook_line", None)),
            ("--no-hook-line", getattr(args, "no_hook_line", False)),
            ("--keep-line-findings", getattr(args, "keep_line_findings", None)),
        )
        if given
    ]
    if mixed:
        print(
            f"Stopped: a take edit ({', '.join(named)}) runs on its own; {', '.join(mixed)} goes with a plain "
            "finish. Run them as two commands. Nothing was changed or charged.",
            file=sys.stderr,
        )
        return 2
    try:
        request = build_request(
            desk=desk,
            episode=args.episode,
            mutes=edits.get("mute", ()),
            voices=edits.get("voice", ()),
            cues=edits.get("cue", ()),
            impact_at=edits.get("impact_at", ()),
        )
        lines = run_take_edit(
            desk,
            episode=args.episode,
            take_id=take_id,
            request=request,
            preview=bool(getattr(args, "preview", False)),
        )
    except TakeEditStop as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print(f"Stopped: {exc}. Nothing was changed or charged.", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"take": take_id, "lines": lines}, indent=2))
    else:
        print("\n".join(lines))
    return 0


def _server_finish_command(args: argparse.Namespace) -> int:
    """``finish`` / ``join``: the server finishes the episode; the kit downloads it (item 01, phase 4)."""

    from creation.production_state import load_production

    laptop = list(getattr(args, "laptop_only", None) or [])
    if laptop:
        print(laptop_only_refusal(args.command, laptop), file=sys.stderr)
        return 2
    desk = args.desk.expanduser().resolve()
    if args.episodes:
        mixed = [
            flag
            for flag, given in (
                ("--take", getattr(args, "take_id", None)),
                ("--caption-label", getattr(args, "caption_label", None)),
                ("--no-caption-labels", getattr(args, "no_caption_labels", False)),
            )
            if given
        ]
        if mixed:
            print(
                f"Stopped: --episodes shows the show's series cut and finishes nothing, so it does not go "
                f"with {', '.join(mixed)}. Run them as two commands. Nothing was made or charged.",
                file=sys.stderr,
            )
            return 2
        return _show_series_cut(desk, args)
    if args.episode is None:
        args.episode = load_production(desk).episode_ordinal
    edits = _take_edit_settings(args)
    if edits:
        return _send_take_edits(desk, args, edits)
    if getattr(args, "preview", False):
        print(
            "Stopped: --preview prices take edits (--mute, --voice, --cue, --impact-at with --take tK); "
            "a plain finish is free. Nothing was changed or charged.",
            file=sys.stderr,
        )
        return 2
    labels = list(getattr(args, "caption_label", None) or [])
    clear = bool(getattr(args, "no_caption_labels", False))
    if labels or clear:
        stop = _send_caption_labels(desk, args, labels, clear=clear)
        if stop is not None:
            print(stop, file=sys.stderr)
            return 2
    elif getattr(args, "take_id", None):
        print(
            f"Note: --take is not needed any more; the server finishes all of episode {args.episode} at once.",
            file=sys.stderr,
        )
    if args.hook_line or args.no_hook_line:
        _save_hook_line_first(args)
    done = run_server_finish(
        desk,
        episode=args.episode,
        wait_seconds=DEFAULT_WAIT_SECONDS if args.wait is None else args.wait,
        keep_line_findings=tuple(args.keep_line_findings),
    )
    if args.json:
        print(json.dumps(done.as_json(), indent=2))
    else:
        print("\n".join(done.summary_lines()))
    if not done.complete and args.json:
        print(done.summary_lines()[0], file=sys.stderr)
    return 0 if done.complete else SERVER_FINISH_NOT_DONE
