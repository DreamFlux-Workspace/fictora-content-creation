"""How characters feel, kept by the server: ``emotion`` (L-20261015-2).

The Gallery Heiress (desk ``2026-10-06-blood-vows``) and Back From the Sea
(``2026-10-07-the-funeral-guest``), founder report 15 Oct 2026: characters
cried in almost every shot. The workaround was to strip tear words out of
shots by hand (``edit --frame N --set``, ``edit --beat N --expression``): it
did not stick (the next episode was written crying again) and every edited
board cost a redraw.

The server (fictora-drama, emotion plan) now keeps a character's emotional
state apart from who they are, the same way the outfit plan works
(``outfit_commands``):

- an **emotion plan**: entries ``{cast_id|null, from_episode,
  through_episode|null, mood|null, no_tears|null, note}``. ``no_tears`` swaps
  every tear face of that character (or, series-wide, of everyone) for its
  tearless one on every written shot and every shot written later, and a line
  delivered ``through_tears`` plays ``trailing_off``;
- an **emotional default** per character ``{mood, note}``: how their face sits
  when nothing on the shot or in the plan says otherwise.

The kit only calls it, and keeps nothing of its own:

- ``emotion`` (``--cast NAME`` for one character): free, read-only
  (``GET /v1/spines/{id}/emotions``). Series-wide rules, each character's
  default, their plan entries with ids, and per written episode their mood,
  where it comes from, whether they may cry, and how many shots still cry.
- ``emotion --cast NAME --no-tears --episodes 5-`` / ``--mood sad
  --episodes 5-6`` / ``--everyone --no-tears --episodes 1-``: adds one entry
  (``POST .../emotions`` ``add``).
- ``emotion --remove ENTRY_ID``: takes one entry off (``remove``).
- ``emotion --cast NAME --default composed --note "..."`` / ``--clear-default``:
  sets or clears the default (``PUT .../cast/{id}/emotional-default``).

Every change is a preview first (``preview: true``, nothing stored): it prints
the shots and lines that would change and each drawn board that would need a
``redraw-board`` ($0.30, never drawn here). ``--confirm`` sends it for real;
then those boards are stale on the server and on the desk (preflight's
``board_current`` blocks them, as after an outfit change) and the assistant is
told to ask the human now. A stale story version is read again and sent once
more. A server without the routes stops with "server too old": the kit never
falls back to editing shots one by one.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from creation import episode_commands as ec
from creation.harness.http_util import api_error_text
from creation.orchestrate import save_spine_snapshot
from creation.prices import STILL_USD
from creation.production_state import load_production, save_production

#: The moods the server takes (``DramaMoodFamily``); listed for help text only, the server checks them.
MOODS = (
    "neutral",
    "angry",
    "composed",
    "shocked",
    "happy",
    "sad",
    "deflated",
    "flustered",
)
EVERYONE = "everyone"
_RANGE = re.compile(r"^\s*(\d+)\s*(?:(-|\+)\s*(\d+)?)?\s*$")
SERVER_TOO_OLD = (
    "this server is older than the emotion plan (fictora-drama /emotions): nothing was sent. "
    "Ask engineering to deploy it; do not strip tear words from shots by hand"
)
ASK = "--ask"
ASK_TOO_OLD = (
    "this server cannot read a plain sentence yet (emotion request_text): nothing was changed. "
    "Use the flag forms instead, e.g. --cast NAME --no-tears --episodes 5-"
)
_KNOWN_404 = {"spine_not_found", "cast_not_found", "emotion_entry_not_found"}


# --- Small helpers ------------------------------------------------------------------------------


def parse_range(raw: str) -> tuple[int, int | None]:
    """``5`` / ``5-6`` / ``5-`` / ``5+`` -> ``(from_episode, through_episode)``; ``None`` runs to the end.

    Raises
    ------
    ec.CommandStopped
        Anything else (a list like ``5,7`` too: one entry covers one run of episodes), or an episode
        below 1 or a range running backwards.
    """

    match = _RANGE.match(raw or "")
    if not match:
        raise ec.CommandStopped(
            f"--episodes takes 5, 5-6, or 5- (episode 5 to the end), not {raw!r}; nothing was sent"
        )
    first = int(match.group(1))
    if match.group(2) is None:
        last: int | None = first
    elif match.group(3) is None:
        last = None
    elif match.group(2) == "+":
        raise ec.CommandStopped(
            f"--episodes {raw!r}: write 5-6 for a range, 5- or 5+ for episode 5 to the end; nothing was sent"
        )
    else:
        last = int(match.group(3))
    if first < 1 or (last is not None and last < first):
        raise ec.CommandStopped(
            f"--episodes {raw!r}: episodes are numbered from 1, low to high; nothing was sent"
        )
    return first, last


def _one_line(value: str | None) -> str:
    return " ".join(str(value or "").split())


def range_text(first: Any, last: Any) -> str:
    """``ep 5``, ``ep 5-6`` or ``ep 5 onward``."""

    if last is None:
        return f"ep {first} onward"
    if int(last) == int(first):
        return f"ep {first}"
    return f"ep {first}-{last}"


def entry_text(entry: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """One plan entry in plain words: who, which episodes, what it says."""

    cast_id = entry.get("cast_id")
    who = names.get(str(cast_id), str(cast_id)) if cast_id else EVERYONE
    said = []
    if entry.get("mood"):
        said.append(f"feels {entry['mood']}")
    if entry.get("no_tears") is True:
        said.append("no tears")
    elif entry.get("no_tears") is False:
        said.append("tears allowed")
    note = _one_line(entry.get("note"))
    text = f"{who}, {range_text(entry.get('from_episode'), entry.get('through_episode'))}: {', '.join(said) or '(nothing)'}"
    return f'{text} ("{note}")' if note else text


def _default_text(default: Any) -> str:
    if not isinstance(default, Mapping):
        return "none (neutral)"
    note = _one_line(default.get("note"))
    mood = str(default.get("mood") or "neutral")
    return f'{mood} ("{note}")' if note else mood


def _error(body: Any) -> tuple[str, dict[str, Any]]:
    """``(code, details)`` from the server's error envelope."""

    if not isinstance(body, Mapping):
        return "", {}
    error = body.get("error") if isinstance(body.get("error"), Mapping) else body
    details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
    return str(error.get("code") or ""), dict(details)


def _too_old(status: int, code: str) -> bool:
    return status == 405 or (status == 404 and code not in _KNOWN_404)


# --- Talking to the server ----------------------------------------------------------------------


class _Desk:
    """One desk's session and the server's emotion view (read once per command)."""

    def __init__(self, desk: Path) -> None:
        self.desk = desk
        _, self.state, self.run = ec._desk_session(desk)
        self.spine_id = self.state.spine_id or ""
        self.view = self._read()
        self.names = {
            str(c.get("cast_id")): str(c.get("name") or c.get("cast_id"))
            for c in self.view.get("cast") or []
            if isinstance(c, Mapping)
        }

    def close(self) -> None:
        self.run.client.close()

    @property
    def base(self) -> str:
        return f"/v1/spines/{quote(self.spine_id, safe='')}"

    def _read(self) -> dict[str, Any]:
        """``GET .../emotions`` (stores nothing)."""

        status, answer = self.run.get_optional(f"{self.base}/emotions")
        if 200 <= status < 300 and isinstance(answer, Mapping):
            return dict(answer)
        code, _ = _error(answer)
        if _too_old(status, code):
            raise ec.CommandStopped(SERVER_TOO_OLD)
        raise ec.CommandStopped(
            f"could not read the emotion plan (HTTP {status}): {api_error_text(answer)}"
        )

    def character(self, cast: str) -> dict[str, Any]:
        """The view's row for a name or cast id (any case)."""

        wanted = _one_line(cast).casefold()
        rows = [c for c in self.view.get("cast") or [] if isinstance(c, Mapping)]
        for row in rows:
            if str(row.get("cast_id") or "").casefold() == wanted:
                return dict(row)
        matches = [r for r in rows if _one_line(r.get("name")).casefold() == wanted]
        if len(matches) == 1:
            return dict(matches[0])
        known = ", ".join(sorted(self.names.values())) or "none"
        if matches:
            raise ec.CommandStopped(
                f'two characters are called "{cast}": use the cast id ({", ".join(str(m["cast_id"]) for m in matches)}). '
                "Nothing was sent"
            )
        raise ec.CommandStopped(
            f'no character "{cast}" in this story (characters: {known}). Nothing was sent'
        )

    def send(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any],
        *,
        preview: bool,
        what: str,
    ) -> dict[str, Any]:
        """POST or PUT one change; a stale story version is read again and sent once more."""

        call: Callable[[str, dict[str, Any]], tuple[int, Any]] = (
            self.run.put_optional if method == "PUT" else self.run.post_optional
        )
        version = str(self.view.get("spine_version") or "")
        for attempt in (1, 2):
            status, answer = call(
                path, {"spine_version": version, **body, "preview": preview}
            )
            if 200 <= status < 300 and isinstance(answer, Mapping):
                return dict(answer)
            code, details = _error(answer)
            if _too_old(status, code):
                raise ec.CommandStopped(SERVER_TOO_OLD)
            if code == "spine_version_conflict" and attempt == 1:
                version = str(details.get("latest_spine_version") or "") or str(
                    self._read().get("spine_version") or ""
                )
                continue
            raise ec.CommandStopped(self.refusal(status, answer, what))
        raise AssertionError("unreachable")  # pragma: no cover

    def refusal(self, status: int, answer: Any, what: str) -> str:
        """The server's no, in the kit's words, with the fix."""

        code, _ = _error(answer)
        desk = self.desk
        if what == ASK and status == 422:
            return ASK_TOO_OLD
        if code == "emotion_entry_not_found":
            return (
                f"the plan has no entry {what}: `fictora-produce emotion --desk {desk}` lists the entries "
                "with their ids. Nothing was changed"
            )
        if code == "cast_not_found":
            return (
                f"the server has no character {what} in this story. Nothing was changed"
            )
        if code == "spine_not_found":
            return "the server has no story for this desk. Nothing was changed"
        return (
            f"the server refused the emotion change (HTTP {status}): {api_error_text(answer)}. "
            "Nothing was changed"
        )

    def refresh(self, episode: int) -> None:
        """Bring the desk's copy of the story up to date after an applied change."""

        save_spine_snapshot(self.desk, episode, self.run.spine(self.spine_id))


def _open(desk: Path) -> _Desk:
    return _Desk(desk.expanduser().resolve())


# --- Printing what the server did (or would do) -------------------------------------------------


def _print_change(
    session: _Desk, answer: Mapping[str, Any], *, cause: str, out: Any
) -> list[str]:
    """Print the shots, lines and boards a change touches; return the boards as ``epNN-tK`` keys."""

    names, desk = session.names, session.desk
    preview = bool(answer.get("preview"))
    by_episode: dict[int, dict[str, list[Mapping[str, Any]]]] = {}
    for kind in ("shots_changed", "lines_changed"):
        for row in answer.get(kind) or []:
            if isinstance(row, Mapping):
                by_episode.setdefault(
                    int(row.get("episode_ordinal") or 0), {}
                ).setdefault(kind, []).append(row)
    if not by_episode:
        print(
            "No written shot or line changes: the plan applies to faces as episodes are written.",
            file=out,
        )
    for number in sorted(by_episode):
        rows = by_episode[number]
        shots = rows.get("shots_changed") or []
        lines = rows.get("lines_changed") or []
        verb = "would change" if preview else "changed"
        print(
            f"ep{number:02d}: {len(shots)} face(s) and {len(lines)} line(s) {verb}",
            file=out,
        )
        for shot in shots:
            who = names.get(str(shot.get("cast_id")), str(shot.get("cast_id")))
            print(
                f"  face t{shot.get('set_index')} {shot.get('frame_id')} ({who}): "
                f"{shot.get('before')} -> {shot.get('after')}",
                file=out,
            )
        for line in lines:
            who = names.get(str(line.get("cast_id")), str(line.get("cast_id")))
            print(
                f"  line {line.get('beat_id')} ({who}): {line.get('before')} -> {line.get('after')}",
                file=out,
            )
    keys: list[str] = []
    boards = sorted(
        {
            (int(b.get("episode_ordinal") or 0), int(b.get("set_index") or 1))
            for b in answer.get("boards_to_redraw") or []
            if isinstance(b, Mapping)
        }
    )
    if boards:
        price = float(STILL_USD)
        lead = "would need" if preview else "need"
        print(
            f"Drawn boards that {lead} a redraw to show the new faces (${price:.2f} each, ${price * len(boards):.2f} "
            "in all; only after the human's yes):",
            file=out,
        )
        for number, take in boards:
            keys.append(f"ep{number:02d}-t{take}")
            print(
                f"  fictora-produce redraw-board --desk {desk} --episode {number} --take t{take} "
                f'--cause "emotion: {cause}"',
                file=out,
            )
    else:
        print("No drawn board needs a redraw.", file=out)
    return keys


def _mark_boards_stale(desk: Path, keys: Sequence[str], *, out: Any) -> None:
    """Record outdated boards on the desk, and say: ask the human now.

    The server marks them outdated too (approval and filming refuse them); the
    desk record makes preflight block the take even when its board was approved
    before the change, and ``redraw-board`` clears it (the outfit flow's).
    """

    if not keys:
        return
    state = load_production(desk)
    for key in keys:
        if key not in state.boards_stale:
            state.boards_stale.append(key)
    save_production(desk, state)
    total = float(STILL_USD) * len(keys)
    print(
        f"ASK THE HUMAN NOW: {len(keys)} board(s) ({', '.join(keys)}) still show the old faces and cannot be "
        f"approved or filmed until redrawn. Redraw them for ${total:.2f} in all? On a yes, run the "
        "redraw-board lines above; nothing was spent.",
        file=out,
    )


def _finish(
    session: _Desk,
    answer: Mapping[str, Any],
    *,
    said: str,
    cause: str,
    rerun: str,
    episode: int,
    out: Any,
) -> None:
    """Print a change, then either the preview's next step or what was applied."""

    print(("Preview: " if answer.get("preview") else "") + said, file=out)
    keys = _print_change(session, answer, cause=cause, out=out)
    if answer.get("preview"):
        print(
            "Not applied: this was a preview; nothing was stored. Show the human, then apply it with "
            f"`{rerun} --confirm`.",
            file=out,
        )
        return
    if not answer.get("applied"):
        print("Nothing changed: the story already says this.", file=out)
        return
    session.refresh(episode)
    ec._note(session.desk, episode, f"emotion: {said}")
    _mark_boards_stale(session.desk, keys, out=out)
    print("Applied", file=out)


def _first_episode(answer: Mapping[str, Any], fallback: int) -> int:
    numbers = [
        int(row.get("episode_ordinal") or 0)
        for kind in ("shots_changed", "lines_changed", "boards_to_redraw")
        for row in answer.get(kind) or []
        if isinstance(row, Mapping)
    ]
    return min([n for n in numbers if n > 0] or [fallback])


# --- Commands -----------------------------------------------------------------------------------


def run_emotion_list(desk: Path, *, cast: str | None = None, out: Any = None) -> None:
    """Print the series-wide rules and each character's default, plan and written episodes. Free."""

    out = out or sys.stdout
    session = _open(desk)
    try:
        view, names = session.view, session.names
        rows = (
            [session.character(cast)]
            if cast
            else [c for c in view.get("cast") or [] if isinstance(c, Mapping)]
        )
    finally:
        session.close()
    series = [e for e in view.get("series_plan") or [] if isinstance(e, Mapping)]
    print("Series-wide:", file=out)
    for entry in series:
        print(f"  [{entry.get('entry_id')}] {entry_text(entry, names)}", file=out)
    if not series:
        print("  (no series-wide rule)", file=out)
    for row in rows:
        print(
            f"{row.get('name')} ({row.get('cast_id')}): default {_default_text(row.get('emotional_default'))}",
            file=out,
        )
        for entry in row.get("plan") or []:
            print(f"  [{entry.get('entry_id')}] {entry_text(entry, names)}", file=out)
        for episode in row.get("episodes") or []:
            number = int(episode.get("episode_ordinal") or 0)
            tears = int(episode.get("tear_shots") or 0)
            rule = "no tears" if episode.get("no_tears") else "may cry"
            print(
                f"  ep{number:02d}: {episode.get('mood')} ({episode.get('mood_source')}), {rule}, "
                f"{tears} shot(s) crying",
                file=out,
            )


def run_emotion_add(
    desk: Path,
    *,
    cast: str | None,
    episodes: str,
    mood: str | None = None,
    no_tears: bool | None = None,
    note: str | None = None,
    confirm: bool = False,
    out: Any = None,
) -> None:
    """Add one plan entry (a mood and/or a no-tears rule) for a character, or for everyone with ``cast=None``.

    Without ``confirm`` it is a preview (nothing stored). Free either way; boards it outdates are named
    with their ``redraw-board`` and never drawn here.

    Raises
    ------
    ec.CommandStopped
        Nothing to say, bad ``--episodes``, an unknown character, a server refusal (in plain words), or a
        server too old for the emotion plan.
    """

    out = out or sys.stdout
    first, last = parse_range(episodes)
    mood = _one_line(mood).casefold() or None
    if mood is None and no_tears is None:
        raise ec.CommandStopped(
            "say what to change: --mood MOOD, --no-tears or --tears-ok (or both a mood and a tears rule); "
            "nothing was sent"
        )
    if cast is None and mood is not None:
        raise ec.CommandStopped(
            "a series-wide rule (--everyone) sets tears only, never a mood: name a character with --cast. "
            "Nothing was sent"
        )
    session = _open(desk)
    try:
        cast_id = str(session.character(cast)["cast_id"]) if cast else None
        add = {
            "cast_id": cast_id,
            "from_episode": first,
            "through_episode": last,
            "mood": mood,
            "no_tears": no_tears,
            "note": _one_line(note),
        }
        answer = session.send(
            "POST",
            f"{session.base}/emotions",
            {"add": add},
            preview=not confirm,
            what=cast or EVERYONE,
        )
        said = entry_text(answer.get("added") or add, session.names)
        target = f'--cast "{cast}"' if cast else "--everyone"
        flags = " ".join(
            x
            for x in (
                f"--mood {mood}" if mood else "",
                "--no-tears"
                if no_tears is True
                else ("--tears-ok" if no_tears is False else ""),
                f'--note "{_one_line(note)}"' if _one_line(note) else "",
            )
            if x
        )
        _finish(
            session,
            answer,
            said=said,
            cause=said,
            rerun=f"fictora-produce emotion --desk {session.desk} {target} {flags} --episodes {episodes.strip()}",
            episode=_first_episode(answer, first),
            out=out,
        )
    finally:
        session.close()


def run_emotion_remove(
    desk: Path, *, entry_id: str, confirm: bool = False, out: Any = None
) -> None:
    """Take one plan entry off by its id (preview unless ``confirm``). Faces it already made tearless stay so."""

    out = out or sys.stdout
    entry_id = _one_line(entry_id)
    if not entry_id:
        raise ec.CommandStopped(
            "--remove needs an entry id (`emotion --desk D` lists them); nothing was sent"
        )
    session = _open(desk)
    try:
        everything = [
            *(session.view.get("series_plan") or []),
            *(
                e
                for c in session.view.get("cast") or []
                if isinstance(c, Mapping)
                for e in c.get("plan") or []
            ),
        ]
        entry = next(
            (
                e
                for e in everything
                if isinstance(e, Mapping) and e.get("entry_id") == entry_id
            ),
            None,
        )
        answer = session.send(
            "POST",
            f"{session.base}/emotions",
            {"remove": entry_id},
            preview=not confirm,
            what=entry_id,
        )
        said = f"remove [{entry_id}]" + (
            f" {entry_text(entry, session.names)}" if entry else ""
        )
        _finish(
            session,
            answer,
            said=said,
            cause=f"removed {entry_id}",
            rerun=f"fictora-produce emotion --desk {session.desk} --remove {entry_id}",
            episode=_first_episode(answer, int((entry or {}).get("from_episode") or 1)),
            out=out,
        )
        print(
            "Faces it already made tearless keep their tearless look; the rule just stops applying to new shots.",
            file=out,
        )
    finally:
        session.close()


def run_emotion_default(
    desk: Path,
    *,
    cast: str,
    mood: str | None,
    note: str | None = None,
    confirm: bool = False,
    out: Any = None,
) -> None:
    """Set a character's emotional default (``mood=None`` clears it); preview unless ``confirm``.

    It changes only how their face sits by default: never their look, voice, outfit or sheet.
    """

    out = out or sys.stdout
    mood = _one_line(mood).casefold() or None
    session = _open(desk)
    try:
        row = session.character(cast)
        cast_id, who = str(row["cast_id"]), str(row.get("name") or row["cast_id"])
        default = None if mood is None else {"mood": mood, "note": _one_line(note)}
        answer = session.send(
            "PUT",
            f"{session.base}/cast/{quote(cast_id, safe='')}/emotional-default",
            {"emotional_default": default},
            preview=not confirm,
            what=cast,
        )
        said = f"{who}'s default: {_default_text(answer.get('emotional_default', default))}"
        rerun = f'fictora-produce emotion --desk {session.desk} --cast "{cast}" ' + (
            "--clear-default"
            if mood is None
            else f"--default {mood}"
            + (f' --note "{_one_line(note)}"' if _one_line(note) else "")
        )
        _finish(
            session,
            answer,
            said=said,
            cause=f"{who} default {mood or 'cleared'}",
            rerun=rerun,
            episode=_first_episode(answer, 1),
            out=out,
        )
    finally:
        session.close()


def _ask(session: _Desk, text: str) -> dict[str, Any]:
    """Send the creator's words; the server answers a preview with what it read them as, or a refusal.

    Raises
    ------
    ec.CommandStopped
        The server's refusal, in its own words; or a server too old to read a sentence.
    """

    answer = session.send(
        "POST",
        f"{session.base}/emotions",
        {"request_text": text},
        preview=True,
        what=ASK,
    )
    refusal = answer.get("refusal")
    if isinstance(refusal, Mapping):
        message = _one_line(refusal.get("message")) or str(refusal.get("code") or "")
        raise ec.CommandStopped(
            f"the server did not read that as an emotion change: {message}. Nothing was changed"
        )
    if (
        not isinstance(answer.get("inferred_add"), Mapping)
        and "inferred_default" not in answer
    ):
        raise ec.CommandStopped(ASK_TOO_OLD)
    return answer


def run_emotion_ask(
    desk: Path, *, text: str, confirm: bool = False, out: Any = None
) -> None:
    """Say the change in plain words ("Seo-yeon shouldn't cry from episode 5 on"); the server reads it.

    Without ``confirm`` it prints the server's preview: what it read the words as, the shots and lines
    that would change and the boards that would need a redraw. With ``confirm`` it asks the server
    again (the kit stores nothing between runs) and applies exactly what came back: ``add`` for a plan
    entry, the emotional-default ``PUT`` for a default.

    Raises
    ------
    ec.CommandStopped
        Empty words, the server's refusal (its own message), or a server too old to read a sentence.
    """

    out = out or sys.stdout
    text = _one_line(text)
    if not text:
        raise ec.CommandStopped(
            '--ask needs the change in words, e.g. --ask "Seo-yeon shouldn\'t cry from episode 5 on"; '
            "nothing was sent"
        )
    session = _open(desk)
    try:
        preview = _ask(session, text)
        add = preview.get("inferred_add")
        rerun = f'fictora-produce emotion --desk {session.desk} --ask "{text}"'
        if isinstance(add, Mapping):
            said = entry_text(add, session.names)
            answer = preview
            if confirm:
                print(f"Read as: {said}", file=out)
                answer = session.send(
                    "POST",
                    f"{session.base}/emotions",
                    {"add": dict(add)},
                    preview=False,
                    what=str(add.get("cast_id") or EVERYONE),
                )
                said = entry_text(answer.get("added") or add, session.names)
            episode = _first_episode(answer, int(add.get("from_episode") or 1))
        else:
            cast_id = str(preview.get("cast_id") or "")
            default = preview.get("inferred_default")
            who = session.names.get(cast_id, cast_id)
            said = f"{who}'s default: {_default_text(default)}"
            answer = preview
            if confirm:
                print(f"Read as: {said}", file=out)
                answer = session.send(
                    "PUT",
                    f"{session.base}/cast/{quote(cast_id, safe='')}/emotional-default",
                    {"emotional_default": default},
                    preview=False,
                    what=who,
                )
            episode = _first_episode(answer, 1)
        _finish(
            session,
            answer,
            said=said,
            cause=text,
            rerun=rerun,
            episode=episode,
            out=out,
        )
    finally:
        session.close()


def run_emotion_cli(args: Any) -> None:
    """Route ``fictora-produce emotion`` flags to one command; a flag mix that makes no sense stops first.

    Raises
    ------
    ec.CommandStopped
        Flags that do not go together; nothing is sent.
    """

    adding = bool(args.mood) or args.no_tears or args.tears_ok
    defaulting = args.default is not None or args.clear_default
    if args.ask is not None:
        if (
            adding
            or defaulting
            or args.remove is not None
            or args.cast
            or args.everyone
            or args.episodes
            or args.note
        ):
            raise ec.CommandStopped(
                "--ask comes alone (with --confirm to apply): the server reads who, which episodes and "
                "what from the words. Nothing was sent"
            )
        run_emotion_ask(args.desk, text=args.ask, confirm=args.confirm)
        return
    if args.no_tears and args.tears_ok:
        raise ec.CommandStopped(
            "--no-tears and --tears-ok contradict each other; pick one. Nothing was sent"
        )
    if args.remove is not None:
        if adding or defaulting or args.cast or args.everyone:
            raise ec.CommandStopped(
                "--remove comes alone (with --confirm to apply); nothing was sent"
            )
        run_emotion_remove(args.desk, entry_id=args.remove, confirm=args.confirm)
        return
    if defaulting:
        if adding or args.everyone or not args.cast:
            raise ec.CommandStopped(
                "--default/--clear-default go with --cast only (and --note); nothing was sent"
            )
        if args.default is not None and args.clear_default:
            raise ec.CommandStopped(
                "--default and --clear-default contradict each other; pick one. Nothing was sent"
            )
        run_emotion_default(
            args.desk,
            cast=args.cast,
            mood=None if args.clear_default else args.default,
            note=args.note,
            confirm=args.confirm,
        )
        return
    if adding:
        if bool(args.cast) == bool(args.everyone):
            raise ec.CommandStopped(
                "name one character with --cast, or use --everyone for a series-wide rule; nothing was sent"
            )
        if not args.episodes:
            raise ec.CommandStopped(
                "say which episodes: --episodes 5, 5-6 or 5- (to the end); nothing was sent"
            )
        run_emotion_add(
            args.desk,
            cast=args.cast,
            episodes=args.episodes,
            mood=args.mood,
            no_tears=True if args.no_tears else (False if args.tears_ok else None),
            note=args.note,
            confirm=args.confirm,
        )
        return
    if args.confirm or args.episodes or args.note or args.everyone:
        raise ec.CommandStopped(
            "nothing to change: add --ask, --mood, --no-tears, --tears-ok, --default, --clear-default or --remove. "
            "Nothing was sent"
        )
    run_emotion_list(args.desk, cast=args.cast)


__all__ = [
    "EVERYONE",
    "MOODS",
    "SERVER_TOO_OLD",
    "entry_text",
    "parse_range",
    "range_text",
    "run_emotion_add",
    "run_emotion_ask",
    "run_emotion_cli",
    "run_emotion_default",
    "run_emotion_list",
    "run_emotion_remove",
]
