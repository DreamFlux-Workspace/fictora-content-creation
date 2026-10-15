"""Change what a character wears for some episodes: ``outfit`` (L-20261015-1).

The Gallery Heiress (desk ``2026-10-06-blood-vows``, 13-15 Oct 2026): the
producer wanted Seo-yeon in a black evening gown for episodes 5-6. A look note
for one episode and "black evening gown, NOT her blouse" written into every
shot (two redraws, $1.20) were both ignored: the boards kept drawing her ivory
blouse. The server has outfits (``wardrobe_variants`` ``{label, instruction}``
on the card, at most four, each drawn on her sheet; ``wardrobe_variant`` on a
shot's staging asks for it) and, since the outfit-plan change, owns the plan
too: ``POST /v1/spines/{id}/outfits`` adds an outfit, plans it for episodes
(tagging every shot of theirs already written) or removes one, and the
writer puts a planned outfit on an episode's shots when it writes them.

The first ``outfit`` (kit #236) did the work itself: one card edit, one story
edit per episode, and the plan in a file on the desk (``shared/outfits.json``)
that the server never saw, so an episode written later came back in the base
outfit and only a kit warning caught it. The kit now only calls the server:

- ``outfit --cast NAME``: free, read-only (``GET .../outfits``). Their outfits
  (base and each named one with its words), the plan, and per episode what
  their written shots wear.
- ``outfit --cast NAME --add LABEL --look TEXT|@FILE``: adds the outfit (or
  rewords one the card has). Free. Their sheet only shows it once redrawn, so
  when the server says ``sheet_needs_redraw`` the kit prices that one redraw
  ($0.30) and draws it on ``--confirm-spend`` after the human's yes
  (``redraw-plate``'s flow).
- ``outfit --cast NAME --wear LABEL|base --episodes 5-6``: plans the outfit and
  tags their written shots. Free. With ``--add`` both go in one request. The
  boards drawn with the old outfit are named with the ``redraw-board`` that
  fixes each ($0.30), never drawn here. Episodes not written yet are planned:
  the writer dresses them.
- ``outfit --cast NAME --remove LABEL``: takes an outfit off the card, refused
  by the server while the plan or a shot still uses it.
- A desk with the old ``shared/outfits.json`` sends its plan to the server once
  (on the next ``outfit`` run) and the file becomes ``outfits.migrated.json``.

A server without the route stops with "server too old"; there is no fallback
to the old client-side edits. The board picture check's ``wrong_outfit``
finding still prints with the other picture checks.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from creation import cast_commands as cc
from creation import episode_commands as ec
from creation.cli_text import TextArgError, text_or_file
from creation.desk_media_urls import drawn_cast_rows
from creation.harness.http_util import api_error_text
from creation.orchestrate import save_spine_snapshot
from creation.prices import STILL_USD

#: Most outfits one card holds (``DramaCastVisualBrief.wardrobe_variants``, max_length=4).
MAX_OUTFITS = 4
#: The word for the card's own outfit (``wardrobe_variant: null``).
BASE = "base"
#: The plan the first ``outfit`` kept on the desk; sent to the server once, then renamed.
OLD_PLAN_FILE = ("shared", "outfits.json")
MIGRATED_PLAN_FILE = ("shared", "outfits.migrated.json")
#: Sheet redraws whose price was shown (``{"priced": ["cast_id:label", ...]}``).
PRICED_FILE = ("shared", "outfit-sheet-priced.json")
_EPISODES = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")
SERVER_TOO_OLD = (
    "this server is older than the outfit plan (fictora-drama /outfits): nothing was sent. "
    "Ask engineering to deploy it; the kit no longer edits outfits shot by shot"
)


# --- Small helpers ------------------------------------------------------------------------------


def parse_episodes(raw: str) -> list[int]:
    """``5`` / ``5-6`` / ``5,7`` / ``2-3,6`` -> sorted episode numbers.

    Raises
    ------
    ec.CommandStopped
        Anything else, or an episode below 1.
    """

    if not _EPISODES.match(raw or ""):
        raise ec.CommandStopped(
            f"--episodes takes 5, 5-6 or 5,7 (not {raw!r}); nothing was sent"
        )
    numbers: set[int] = set()
    for part in raw.split(","):
        first, _, last = part.partition("-")
        start, end = int(first), int(last or first)
        if start < 1 or end < start:
            raise ec.CommandStopped(
                f"--episodes {raw!r}: episodes are numbered from 1, low to high; nothing was sent"
            )
        numbers.update(range(start, end + 1))
    return sorted(numbers)


def _one_line(value: str) -> str:
    return " ".join(str(value).split())


def _say(label: str | None) -> str:
    return f'"{label}"' if label else "their base outfit"


def _episodes_text(numbers: Sequence[int]) -> str:
    return ", ".join(str(n) for n in numbers)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _priced(desk: Path) -> set[str]:
    data = _read_json(desk.joinpath(*PRICED_FILE))
    return set(data.get("priced") or []) if isinstance(data, dict) else set()


def _mark_priced(desk: Path, key: str, *, on: bool) -> None:
    priced = _priced(desk)
    (priced.add if on else priced.discard)(key)
    path = desk.joinpath(*PRICED_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"priced": sorted(priced)}, indent=2) + "\n", encoding="utf-8"
    )


def _priced_key(cast_id: str, label: str) -> str:
    return f"{cast_id}:{_one_line(label).casefold()}"


def _error(body: Any) -> tuple[str, dict[str, Any], str]:
    """``(code, details, message)`` from the server's error envelope."""

    if not isinstance(body, Mapping):
        return "", {}, str(body)
    error = body.get("error") if isinstance(body.get("error"), Mapping) else body
    details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
    return str(error.get("code") or ""), dict(details), str(error.get("message") or "")


def _too_old(status: int, code: str) -> bool:
    return status == 405 or (
        status == 404 and code not in {"spine_not_found", "cast_not_found"}
    )


# --- Talking to the server ----------------------------------------------------------------------


class _Desk:
    """One desk's session, spine and the character a command names."""

    def __init__(self, desk: Path, cast: str) -> None:
        self.desk = desk
        _, self.state, self.run = ec._desk_session(desk)
        self.spine_id = self.state.spine_id or ""
        self.spine = self.run.spine(self.spine_id)
        self.card = cc.exit_card(self.spine, cast)
        self.cast_id = str(self.card["cast_id"])
        self.who = str(self.card.get("name") or self.cast_id)

    def close(self) -> None:
        self.run.client.close()

    @property
    def path(self) -> str:
        return f"/v1/spines/{quote(self.spine_id, safe='')}/outfits"

    def outfits(self) -> list[dict[str, str]]:
        brief = self.card.get("visual_brief")
        if not isinstance(brief, Mapping):
            return []
        return [
            {"label": str(v["label"]), "instruction": str(v.get("instruction") or "")}
            for v in brief.get("wardrobe_variants") or []
            if isinstance(v, Mapping) and v.get("label")
        ]

    def change(self, change: Mapping[str, Any], *, preview: bool) -> dict[str, Any]:
        """``POST .../outfits`` for this character; a stale story version is read again and sent once more."""

        version = str(self.spine.get("spine_version") or "")
        for attempt in (1, 2):
            body = {
                "spine_version": version,
                "cast_id": self.cast_id,
                **change,
                "preview": preview,
            }
            status, answer = self.run.post_optional(self.path, body)
            if 200 <= status < 300 and isinstance(answer, Mapping):
                return dict(answer)
            code, details, _ = _error(answer)
            if _too_old(status, code):
                raise ec.CommandStopped(SERVER_TOO_OLD)
            if code == "spine_version_conflict" and attempt == 1:
                version = str(details.get("latest_spine_version") or "") or str(
                    self.run.spine(self.spine_id).get("spine_version") or ""
                )
                continue
            raise ec.CommandStopped(self.refusal(status, answer, change))
        raise AssertionError("unreachable")  # pragma: no cover

    def refusal(self, status: int, answer: Any, change: Mapping[str, Any]) -> str:
        """The server's no, in the kit's words, with the fix."""

        code, details, _ = _error(answer)
        who, desk = self.who, self.desk
        wear = change.get("wear") or {}
        label = (
            (change.get("add") or {}).get("label")
            or wear.get("wardrobe_variant")
            or change.get("remove")
            or ""
        )
        if code == "cast_not_drawn":
            return (
                f"{who} is heard only: no look, so no outfit. Give them one first: "
                f'fictora-produce cast --desk {desk} --name "{who}" --look @look.txt. Nothing was changed'
            )
        if code == "unknown_wardrobe_variant":
            have = (
                ", ".join(f'"{x}"' for x in details.get("available") or [])
                or "none yet"
            )
            return (
                f'{who} has no outfit "{label}" (outfits: base, {have}); nothing was changed. '
                f'Add it first: outfit --cast "{who}" --add "{label}" --look "what it is"'
            )
        if code == "wardrobe_variant_limit":
            have = ", ".join(f'"{o["label"]}"' for o in self.outfits())
            return (
                f"{who} already has {details.get('limit') or MAX_OUTFITS} outfits ({have}), the most a card holds. "
                f'Remove one first (outfit --cast "{who}" --remove LABEL). Nothing was changed'
            )
        if code == "outfit_in_use":
            where = (
                _episodes_text(details.get("episode_ordinals") or []) or "some shots"
            )
            return (
                f'{who} still wears "{label}" in episode(s) {where}. Dress those episodes in another outfit first '
                f'(outfit --cast "{who}" --wear base --episodes N). Nothing was changed'
            )
        return f"the server refused the outfit change (HTTP {status}): {api_error_text(answer)}. Nothing was changed"

    def view(self) -> dict[str, Any]:
        """``GET .../outfits`` (stores nothing)."""

        status, answer = self.run.get_optional(self.path)
        if 200 <= status < 300 and isinstance(answer, Mapping):
            return dict(answer)
        code, _, _ = _error(answer)
        if _too_old(status, code):
            raise ec.CommandStopped(SERVER_TOO_OLD)
        raise ec.CommandStopped(
            f"could not read the outfits (HTTP {status}): {api_error_text(answer)}"
        )

    def refresh(self, episode: int) -> None:
        self.spine = self.run.spine(self.spine_id)
        save_spine_snapshot(self.desk, episode, self.spine)
        self.card = cc.exit_card(self.spine, self.cast_id)


def _open(desk: Path, cast: str) -> _Desk:
    return _Desk(desk.expanduser().resolve(), cast)


# --- The old desk plan, sent once ---------------------------------------------------------------


def migrate_old_plan(desk: Path, *, out: Any = None) -> bool:
    """Send a desk's old ``shared/outfits.json`` plan to the server once, then rename the file.

    Returns
    -------
    bool
        Whether a plan file was found and sent.

    Raises
    ------
    ec.CommandStopped
        The server is too old (the file stays as it is).
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    path = desk.joinpath(*OLD_PLAN_FILE)
    if not path.is_file():
        return False
    data = _read_json(path)
    plan = data if isinstance(data, Mapping) else {}
    print("Moving this desk's outfit plan to the server (once):", file=out)
    for cast_id, entry in sorted(plan.items()):
        if cast_id.startswith("_") or not isinstance(entry, Mapping):
            continue
        by_label: dict[str, list[int]] = {}
        for number, label in (entry.get("episodes") or {}).items():
            by_label.setdefault(str(label), []).append(int(number))
        if not by_label:
            continue
        session = _open(desk, cast_id)
        try:
            for label, numbers in sorted(by_label.items()):
                wanted = None if label.casefold() == BASE else label
                try:
                    answer = session.change(
                        {
                            "wear": {
                                "wardrobe_variant": wanted,
                                "episode_ordinals": sorted(numbers),
                            }
                        },
                        preview=False,
                    )
                except ec.CommandStopped as exc:
                    if str(exc) == SERVER_TOO_OLD:
                        raise
                    print(
                        f"  !! {session.who}: {_say(wanted)} in ep {_episodes_text(sorted(numbers))} not moved: {exc}",
                        file=out,
                    )
                    continue
                print(
                    f"  {session.who}: {_say(wanted)} in ep {_episodes_text(sorted(numbers))} "
                    f"({len(answer.get('shots_changed') or [])} shot(s) changed)",
                    file=out,
                )
        finally:
            session.close()
    path.rename(desk.joinpath(*MIGRATED_PLAN_FILE))
    print(
        f"  the old plan file is now {Path(*MIGRATED_PLAN_FILE)}; the server keeps the plan from here.",
        file=out,
    )
    return True


# --- Printing what the server did ---------------------------------------------------------------


def _print_change(
    session: _Desk,
    answer: Mapping[str, Any],
    *,
    episodes: Sequence[int],
    label: str | None,
    out: Any,
) -> None:
    desk, who = session.desk, session.who
    shots: dict[int, list[Mapping[str, Any]]] = {}
    for shot in answer.get("shots_changed") or []:
        if isinstance(shot, Mapping):
            shots.setdefault(int(shot.get("episode_ordinal") or 0), []).append(shot)
    pending = set(int(n) for n in answer.get("pending_episode_ordinals") or [])
    boards: dict[int, list[int]] = {}
    for board in answer.get("boards_to_redraw") or []:
        if isinstance(board, Mapping):
            boards.setdefault(int(board.get("episode_ordinal") or 0), []).append(
                int(board.get("set_index") or 1)
            )
    for number in sorted(set(episodes) | set(shots) | pending):
        rows = shots.get(number) or []
        if rows:
            print(f"ep{number:02d} outfit ({who}):", file=out)
            for shot in rows:
                print(
                    f"  t{shot.get('set_index')} {shot.get('frame_id')}: {_say(shot.get('before'))} -> {_say(shot.get('after'))}",
                    file=out,
                )
            print(f"ep{number:02d}: {len(rows)} shot(s) now {_say(label)}.", file=out)
        elif number in pending:
            print(
                f"ep{number:02d}: shots not written yet; the writer will put {_say(label)} on when they are written.",
                file=out,
            )
        else:
            print(
                f"ep{number:02d}: nothing to change (no shot of {who} needed it).",
                file=out,
            )
        for take in sorted(set(boards.get(number) or [])):
            print(
                f"  t{take} board was drawn with the old outfit: redraw it (${float(STILL_USD):.2f}, after the human's yes): "
                f'fictora-produce redraw-board --desk {desk} --episode {number} --take t{take} --cause "outfit: {label or BASE}"',
                file=out,
            )


def _sheet_drawn(spine: Mapping[str, Any], cast_id: str) -> bool:
    drawn = {str(row["cast_id"]) for row in drawn_cast_rows(dict(spine))}
    return cast_id in drawn and cc._has_plates(spine)


def _sheet_command(desk: Path, who: str, label: str) -> str:
    return f'fictora-produce outfit --desk {desk} --cast "{who}" --add "{label}" --confirm-spend'


def _sheet_price(desk: Path, who: str, label: str, *, again: bool = False) -> str:
    lead = "No price shown for this redraw yet. " if again else ""
    return (
        f'{lead}{who}\'s sheet: one redraw so it shows "{label}" next to the base outfit, about '
        f"${float(STILL_USD):.2f}. Nothing drawn yet. The boards draw an outfit from the sheet.\n"
        f"Human yes, then `{_sheet_command(desk, who, label)}`."
    )


# --- Commands -----------------------------------------------------------------------------------


def run_outfit_list(desk: Path, *, cast: str, out: Any = None) -> None:
    """Print one character's outfits, the plan, and what their written shots wear. Free."""

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    migrate_old_plan(desk, out=out)
    session = _open(desk, cast)
    try:
        view = session.view()
    finally:
        session.close()
    who, cast_id = session.who, session.cast_id
    entry = next(
        (
            c
            for c in view.get("cast") or []
            if isinstance(c, Mapping) and c.get("cast_id") == cast_id
        ),
        None,
    )
    if entry is None:
        print(f"{who} ({cast_id}) is heard only: no look, so no outfits.", file=out)
        return
    print(f"{who} ({cast_id}) outfits:", file=out)
    print(
        f"  base: {'; '.join(str(x) for x in entry.get('base_outfit') or []) or '(no look yet)'}",
        file=out,
    )
    for outfit in entry.get("outfits") or []:
        print(f'  "{outfit.get("label")}": {outfit.get("instruction")}', file=out)
    planned: set[int] = set()
    for row in entry.get("plan") or []:
        numbers = [int(n) for n in row.get("episode_ordinals") or []]
        planned.update(numbers)
        print(
            f'plan: "{row.get("wardrobe_variant")}" in ep {_episodes_text(numbers)}',
            file=out,
        )
    written = set()
    for episode in entry.get("episodes") or []:
        number = int(episode.get("episode_ordinal") or 0)
        written.add(number)
        counts = {k: v for k, v in (episode.get("shots_by_outfit") or {}).items() if v}
        said = ", ".join(
            f"{n} shot(s) {'base' if k == BASE else repr(k)}" for k, n in counts.items()
        )
        print(f"ep{number:02d}: {said or f'{who} is in no shot'}", file=out)
    for number in sorted(planned - written):
        print(
            f"ep{number:02d}: shots not written yet; the writer will dress them as planned",
            file=out,
        )


def run_outfit_change(
    desk: Path,
    *,
    cast: str,
    add: str | None = None,
    look: str | None = None,
    wear: str | None = None,
    episodes: str | None = None,
    preview_only: bool = False,
    confirm_spend: bool = False,
    out: Any = None,
) -> Path | None:
    """Add an outfit and/or plan one for episodes: one ``POST .../outfits``; then the sheet's price or redraw.

    Returns
    -------
    Path | None
        The new plate when ``confirm_spend`` drew it; else ``None``.

    Raises
    ------
    ec.CommandStopped
        A blank or ``base`` name, a missing ``--look`` or ``--episodes``, bad ``--episodes``, a server
        refusal (in plain words), or a server too old for the outfit plan.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    change: dict[str, Any] = {}
    name: str | None = None
    if add is not None:
        name = _one_line(add)
        if not name:
            raise ec.CommandStopped(
                '--add needs the outfit\'s name, e.g. --add "black gown"; nothing was sent'
            )
        if name.casefold() == BASE:
            raise ec.CommandStopped(
                '"base" is the card\'s own outfit; give the new one another name. Nothing was sent'
            )
    words: str | None = None
    if look is not None:
        try:
            words = _one_line(text_or_file(look, flag="--look"))
        except TextArgError as exc:
            raise ec.CommandStopped(str(exc)) from None
        if not words:
            raise ec.CommandStopped(
                "--look is empty: say what the outfit is. Nothing was sent"
            )
    numbers: list[int] = []
    wanted: str | None = None
    if wear is not None:
        if not episodes:
            raise ec.CommandStopped(
                "--wear needs --episodes (5, 5-6 or 5,7); nothing was sent"
            )
        numbers = parse_episodes(episodes)
        wanted = None if _one_line(wear).casefold() == BASE else _one_line(wear)
        change["wear"] = {"wardrobe_variant": wanted, "episode_ordinals": numbers}
    if not preview_only:
        migrate_old_plan(desk, out=out)
    session = _open(desk, cast)
    try:
        who, cast_id = session.who, session.cast_id
        if name is not None:
            existing = next(
                (
                    o
                    for o in session.outfits()
                    if o["label"].casefold() == name.casefold()
                ),
                None,
            )
            if existing is not None:
                name = existing["label"]
            if words is not None:
                change["add"] = {"label": name, "instruction": words}
            elif existing is None:
                raise ec.CommandStopped(
                    f'{who} has no outfit "{name}" yet: add it with --look "what it is". Nothing was sent'
                )
        answer: dict[str, Any] = {}
        if change:
            answer = session.change(change, preview=preview_only)
            if "add" in change:
                print(f'outfit for {who} ({cast_id}): "{name}": {words}', file=out)
            if "wear" in change:
                _print_change(session, answer, episodes=numbers, label=wanted, out=out)
            if preview_only:
                print("Not applied: --preview; nothing was stored", file=out)
                return None
            if not answer.get("applied"):
                print("Nothing changed: the story already says this.", file=out)
            else:
                first = (
                    numbers[0]
                    if numbers
                    else int(session.card.get("intro_episode_ordinal") or 1)
                )
                session.refresh(first)
                what = []
                if "add" in change:
                    what.append(f'"{name}": {words}')
                if "wear" in change:
                    what.append(f"wears {_say(wanted)} in episode(s) {episodes}")
                ec._note(desk, first, f"outfit: {who} ({cast_id}) " + "; ".join(what))
                print("Applied", file=out)
        if name is None:
            return None
        key = _priced_key(cast_id, name)
        if change and answer.get("applied") and "add" in change:
            _mark_priced(desk, key, on=False)
        needs_sheet = bool(answer.get("sheet_needs_redraw")) if change else True
        if not needs_sheet:
            return None
        if not _sheet_drawn(session.spine, cast_id):
            print(
                f'{who} has no sheet drawn yet: "{name}" is drawn on the sheet with the others at the plates step.',
                file=out,
            )
            return None
        if not confirm_spend:
            _mark_priced(desk, key, on=True)
            print(_sheet_price(desk, who, name), file=out)
            if wear is None:
                print(
                    f'Then dress the shots: outfit --desk {desk} --cast "{who}" --wear "{name}" --episodes N (free).',
                    file=out,
                )
            return None
        if key not in _priced(desk):
            _mark_priced(desk, key, on=True)
            print(_sheet_price(desk, who, name, again=True), file=out)
            # A yes the kit cannot honour is a stop (exit 2), never a quiet exit 0 that lets the
            # next paid command on the same shell line run over it (L-20261014-28).
            raise ec.CommandStopped(
                f'outfit --confirm-spend did NOT draw "{name}" on {who}\'s sheet: no price was shown for it on '
                "this desk. Nothing was sent or charged. Show the price above to the human, then run it again "
                "on its own line."
            )
    finally:
        session.close()
    plate = ec.run_redraw_plate_with_note(
        desk,
        cast=cast_id,
        note=f'Show the outfit "{name}" on the sheet as its own figure.',
        out=out,
    )
    _mark_priced(desk, key, on=False)
    return plate


def run_outfit_add(
    desk: Path,
    *,
    cast: str,
    label: str,
    look: str | None,
    preview_only: bool = False,
    confirm_spend: bool = False,
    out: Any = None,
) -> Path | None:
    """``--add`` alone (:func:`run_outfit_change`)."""

    return run_outfit_change(
        desk,
        cast=cast,
        add=label,
        look=look,
        preview_only=preview_only,
        confirm_spend=confirm_spend,
        out=out,
    )


def run_outfit_wear(
    desk: Path,
    *,
    cast: str,
    label: str,
    episodes: str,
    preview_only: bool = False,
    out: Any = None,
) -> None:
    """``--wear`` alone (:func:`run_outfit_change`)."""

    run_outfit_change(
        desk,
        cast=cast,
        wear=label,
        episodes=episodes,
        preview_only=preview_only,
        out=out,
    )


def run_outfit_remove(desk: Path, *, cast: str, label: str, out: Any = None) -> None:
    """Take one outfit off a card; the server refuses while the plan or a shot still uses it. Free."""

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    name = _one_line(label)
    if name.casefold() == BASE:
        raise ec.CommandStopped("the base outfit cannot be removed; nothing was sent")
    migrate_old_plan(desk, out=out)
    session = _open(desk, cast)
    try:
        answer = session.change({"remove": name}, preview=False)
        print(f'outfit for {session.who} ({session.cast_id}): - "{name}"', file=out)
        if answer.get("applied"):
            episode = int(session.card.get("intro_episode_ordinal") or 1)
            session.refresh(episode)
            ec._note(
                desk,
                episode,
                f'outfit: {session.who} ({session.cast_id}) "{name}" removed',
            )
            print("Applied", file=out)
        else:
            print("Nothing changed.", file=out)
    finally:
        session.close()


__all__ = [
    "BASE",
    "MAX_OUTFITS",
    "SERVER_TOO_OLD",
    "migrate_old_plan",
    "parse_episodes",
    "run_outfit_add",
    "run_outfit_change",
    "run_outfit_list",
    "run_outfit_remove",
    "run_outfit_wear",
]
