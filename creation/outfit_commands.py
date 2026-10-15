"""Change what a character wears for some episodes: ``outfit`` (L-20261015-1).

The Gallery Heiress (desk ``2026-10-06-blood-vows``, 13-15 Oct 2026): the
producer wanted Seo-yeon in a black evening gown for episodes 5-6. A look note
for one episode and "black evening gown, NOT her blouse" written into every
shot (two redraws, $1.20) were both ignored: the boards kept drawing her ivory
blouse. The server already has outfits: ``wardrobe_variants`` (``{label,
instruction}``, at most four) on the card's ``visual_brief`` draws each one on
her sheet, and ``wardrobe_variant`` on a shot's ``subject_blocking`` entry asks
for it in that shot. The kit had no command for either, so it was hand-built
JSON and one ``edit`` per shot.

- ``outfit --cast NAME``: free, read-only. Their outfits (BASE and each named
  one with its words), the plan on the desk, and per episode which of her
  shots wear what; a shot that does not match the plan is marked ``!!``.
- ``outfit --cast NAME --add LABEL --look TEXT|@FILE``: adds the outfit to
  their card (the whole ``visual_brief`` goes, as ``cast --look`` sends it:
  ``PATCH`` before the script gate, the ``cast_card`` cascade with paid items
  off after it). Free. Their sheet only shows it once redrawn, so it then prices
  that one redraw ($0.30) and draws it on ``--confirm-spend`` after the
  human's yes (``redraw-plate``'s flow). ``--add`` on an outfit the card has
  changes its words. ``--remove LABEL`` takes one off, refused while a shot or
  the plan still uses it.
- ``outfit --cast NAME --wear LABEL|base --episodes 5-6``: tags every shot of the
  character in those episodes (one story edit per episode, the same ``PATCH`` or
  episode cascade ``edit --frame`` uses, paid items off). Free. Shots already
  right are skipped; the boards drawn with the old outfit are named with the
  ``redraw-board`` that fixes each ($0.30), never drawn here. The plan is kept
  on the desk (``shared/outfits.json``), also for episodes whose shots are not
  written yet.
- The plan is checked for free before the boards are priced and after they
  are drawn (:func:`outfit_plan_lines`): a shot of a planned episode with the character
  in another outfit gets a ``!!`` line and the ``--wear`` command that fixes
  it. A warning, never a block.

The boards drawing the tagged outfit in every panel is the server's part
(fictora-drama); its picture check names a panel drawn in the wrong outfit
(``wrong_outfit``), printed with the other picture checks.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from creation import cast_commands as cc
from creation import episode_commands as ec
from creation.cli_text import TextArgError, text_or_file
from creation.desk_media_urls import drawn_cast_rows
from creation.orchestrate import save_spine_snapshot
from creation.prices import STILL_USD
from creation.spine_view import board_assets, frames_by_set

#: Most outfits one card holds (``DramaCastVisualBrief.wardrobe_variants``, max_length=4).
MAX_OUTFITS = 4
#: The word for the card's own outfit (``wardrobe_variant: null``).
BASE = "base"
#: The plan on the desk: ``{cast_id: {"name": ..., "episodes": {"5": label | "base"}}}``.
PLAN_FILE = ("shared", "outfits.json")
_EPISODES = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")


# --- Plan on the desk ---------------------------------------------------------------------------


def _plan_path(desk: Path) -> Path:
    return desk.joinpath(*PLAN_FILE)


def load_plan(desk: Path) -> dict[str, Any]:
    """The outfit plan saved on the desk (``{}`` when none)."""

    path = _plan_path(desk)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_plan(desk: Path, plan: Mapping[str, Any]) -> None:
    path = _plan_path(desk)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _record_wear(
    desk: Path, card: Mapping[str, Any], label: str | None, episodes: Sequence[int]
) -> None:
    plan = load_plan(desk)
    entry = plan.setdefault(str(card["cast_id"]), {})
    entry["name"] = str(card.get("name") or card["cast_id"])
    worn = entry.setdefault("episodes", {})
    for number in episodes:
        worn[str(number)] = label if label is not None else BASE
    _save_plan(desk, plan)


def _priced_key(cast_id: str, label: str) -> str:
    return f"{cast_id}:{' '.join(label.split()).casefold()}"


def _mark_priced(desk: Path, key: str, *, on: bool) -> None:
    plan = load_plan(desk)
    priced = set(plan.get("_sheet_priced") or [])
    if on:
        priced.add(key)
    else:
        priced.discard(key)
    plan["_sheet_priced"] = sorted(priced)
    _save_plan(desk, plan)


def _was_priced(desk: Path, key: str) -> bool:
    return key in set(load_plan(desk).get("_sheet_priced") or [])


# --- Reading the story --------------------------------------------------------------------------


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


def outfits_of(card: Mapping[str, Any]) -> list[dict[str, str]]:
    """The card's named outfits (``wardrobe_variants``), in order."""

    brief = card.get("visual_brief")
    if not isinstance(brief, Mapping):
        return []
    return [
        {"label": str(v["label"]), "instruction": str(v.get("instruction") or "")}
        for v in brief.get("wardrobe_variants") or []
        if isinstance(v, Mapping) and v.get("label")
    ]


def resolve_outfit(card: Mapping[str, Any], wanted: str) -> str | None:
    """The card's label for ``wanted`` (any case), ``None`` for ``base``.

    Raises
    ------
    ec.CommandStopped
        Not one of their outfits; the message lists them and names ``--add``.
    """

    key = _one_line(wanted).casefold()
    if key == BASE:
        return None
    for outfit in outfits_of(card):
        if outfit["label"].casefold() == key:
            return outfit["label"]
    who = str(card.get("name") or card["cast_id"])
    have = ", ".join(f'"{o["label"]}"' for o in outfits_of(card)) or "none yet"
    raise ec.CommandStopped(
        f'{who} has no outfit "{_one_line(wanted)}" (outfits: base, {have}); nothing was sent. '
        f'Add it first: outfit --cast "{who}" --add "{_one_line(wanted)}" --look "what it is"'
    )


def _episode_ordinals(spine: Mapping[str, Any]) -> dict[str, int]:
    return {
        str(s.get("episode_id")): int(s.get("ordinal") or 0)
        for s in spine.get("episode_summaries") or []
        if isinstance(s, Mapping) and s.get("episode_id")
    }


def _staging(
    frame: Mapping[str, Any], cast_id: str
) -> tuple[int, Mapping[str, Any]] | None:
    blocking = (frame.get("visual_brief") or {}).get("subject_blocking") or []
    for index, entry in enumerate(blocking):
        if isinstance(entry, Mapping) and entry.get("cast_id") == cast_id:
            return index, entry
    return None


def _worn(entry: Mapping[str, Any]) -> str | None:
    label = entry.get("wardrobe_variant")
    return _one_line(label) if isinstance(label, str) and label.strip() else None


def _same(a: str | None, b: str | None) -> bool:
    return (a or "").casefold() == (b or "").casefold()


def _say(label: str | None) -> str:
    return f'"{label}"' if label else "their base outfit"


def shots_of(
    spine: Mapping[str, Any], cast_id: str, episode: int
) -> list[tuple[int, Mapping[str, Any], int, str | None]]:
    """``(take, frame, blocking index, outfit worn)`` for each shot of ``episode`` that stages ``cast_id``."""

    rows: list[tuple[int, Mapping[str, Any], int, str | None]] = []
    for take, frames in sorted(frames_by_set(dict(spine), episode=episode).items()):
        for frame in frames:
            found = _staging(frame, cast_id)
            if found is not None:
                rows.append((take, frame, found[0], _worn(found[1])))
    return rows


def _has_frames(spine: Mapping[str, Any], episode: int) -> bool:
    return bool(frames_by_set(dict(spine), episode=episode))


# --- The check ----------------------------------------------------------------------------------


def outfit_plan_lines(
    desk: Path, spine: Mapping[str, Any], *, episode: int
) -> list[str]:
    """``!!`` lines for shots of ``episode`` that do not wear the outfit the desk's plan gives (free).

    Parameters
    ----------
    desk
        Series desk (its ``shared/outfits.json``).
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[str]
        One line per character whose shots are off the plan, with the fix; empty when every shot
        matches or no plan names the episode.
    """

    lines: list[str] = []
    cards = {
        str(c["cast_id"]): c
        for c in spine.get("cast") or []
        if isinstance(c, Mapping) and c.get("cast_id")
    }
    for cast_id, entry in sorted(load_plan(desk).items()):
        if cast_id.startswith("_") or not isinstance(entry, Mapping):
            continue
        planned = (entry.get("episodes") or {}).get(str(episode))
        if planned is None:
            continue
        card = cards.get(cast_id)
        who = str((card or {}).get("name") or entry.get("name") or cast_id)
        label = None if planned == BASE else str(planned)
        if card is None:
            continue
        if label is not None and not any(
            _same(o["label"], label) for o in outfits_of(card)
        ):
            lines.append(
                f'!! outfit: ep{episode:02d}: the plan dresses {who} in "{label}", which is no longer on their card. '
                f'Add it again (outfit --desk {desk} --cast "{who}" --add "{label}" --look "...") or plan another.'
            )
            continue
        off = [
            f"t{take} shot {frame.get('ordinal')} ({_say(worn)})"
            for take, frame, _, worn in shots_of(spine, cast_id, episode)
            if not _same(worn, label)
        ]
        if off:
            lines.append(
                f"!! outfit: ep{episode:02d}: {who} should wear {_say(label)} (the plan) but "
                f"{len(off)} shot(s) do not: {', '.join(off)}. Fix (free): fictora-produce outfit --desk {desk} "
                f'--cast "{who}" --wear "{label or BASE}" --episodes {episode}'
            )
    return lines


# --- Commands -----------------------------------------------------------------------------------


def _read_spine(desk: Path) -> dict[str, Any]:
    _, state, run = ec._desk_session(desk)
    try:
        return run.spine(state.spine_id or "")
    finally:
        run.client.close()


def run_outfit_list(desk: Path, *, cast: str, out: Any = None) -> None:
    """Print one character's outfits, the plan, and what each of their shots wears. Free."""

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    spine = _read_spine(desk)
    card = cc.exit_card(spine, cast)
    cast_id = str(card["cast_id"])
    who = str(card.get("name") or cast_id)
    brief = (
        card.get("visual_brief")
        if isinstance(card.get("visual_brief"), Mapping)
        else {}
    )
    base = (
        "; ".join(str(a) for a in (brief or {}).get("wardrobe_anchors") or [])
        or "(no look yet)"
    )
    print(f"{who} ({cast_id}) outfits:", file=out)
    print(f"  base: {base}", file=out)
    for outfit in outfits_of(card):
        print(f'  "{outfit["label"]}": {outfit["instruction"]}', file=out)
    planned = (load_plan(desk).get(cast_id) or {}).get("episodes") or {}
    if planned:
        listed = ", ".join(
            f"ep{int(n)}: {'base' if v == BASE else repr(v)}"
            for n, v in sorted(planned.items(), key=lambda kv: int(kv[0]))
        )
        print(f"plan: {listed}", file=out)
    numbers = sorted(set(_episode_ordinals(spine).values()) | {int(n) for n in planned})
    for number in numbers:
        if number < 1:
            continue
        if not _has_frames(spine, number):
            print(f"ep{number:02d}: shots not written yet", file=out)
            continue
        shots = shots_of(spine, cast_id, number)
        if not shots:
            print(f"ep{number:02d}: {who} is in no shot", file=out)
            continue
        counts: dict[str, int] = {}
        for _, _, _, worn in shots:
            counts[worn or BASE] = counts.get(worn or BASE, 0) + 1
        print(
            f"ep{number:02d}: "
            + ", ".join(
                f"{n} shot(s) {'base' if k == BASE else repr(k)}"
                for k, n in counts.items()
            ),
            file=out,
        )
        for line in outfit_plan_lines(desk, spine, episode=number):
            if f" {who} " in line:
                print(f"  {line}", file=out)


def _brief_with_outfits(
    card: Mapping[str, Any], outfits: list[dict[str, str]]
) -> dict[str, Any]:
    brief = copy.deepcopy(dict(card["visual_brief"]))
    brief["wardrobe_variants"] = outfits
    return {"cast_id": str(card["cast_id"]), "visual_brief": brief}


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
    """Add (or reword) one outfit on a card, then price, or on ``confirm_spend`` draw, their sheet's redraw.

    Returns
    -------
    Path | None
        The new plate on ``confirm_spend``; else ``None``.

    Raises
    ------
    ec.CommandStopped
        No look on the card, a blank label or look, ``base`` as a label, a fifth outfit, or a server
        refusal; nothing is sent before these checks pass.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    name = _one_line(label)
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
    spine = _read_spine(desk)
    card = cc.exit_card(spine, cast)
    cast_id = str(card["cast_id"])
    who = str(card.get("name") or cast_id)
    if not isinstance(card.get("visual_brief"), Mapping):
        raise ec.CommandStopped(
            f"{who} has no look yet, so no outfit can be added. Give them one first: "
            f'fictora-produce cast --desk {desk} --name "{who}" --look @look.txt. Nothing was sent'
        )
    outfits = outfits_of(card)
    existing = next(
        (o for o in outfits if o["label"].casefold() == name.casefold()), None
    )
    if existing is None and words is None:
        raise ec.CommandStopped(
            f'{who} has no outfit "{name}" yet: add it with --look "what it is". Nothing was sent'
        )
    changed = False
    if existing is None:
        if len(outfits) >= MAX_OUTFITS:
            have = ", ".join(f'"{o["label"]}"' for o in outfits)
            raise ec.CommandStopped(
                f"{who} already has {MAX_OUTFITS} outfits ({have}), the most a card holds. Remove one first "
                f'(outfit --cast "{who}" --remove LABEL). Nothing was sent'
            )
        outfits = [*outfits, {"label": name, "instruction": words or ""}]
        print(f'outfit for {who} ({cast_id}): + "{name}": {words}', file=out)
        changed = True
    elif words is not None and words != existing["instruction"]:
        print(
            f'outfit for {who} ({cast_id}): "{existing["label"]}" was: {existing["instruction"]}',
            file=out,
        )
        print(f"{' ' * (len(who) + len(cast_id) + 15)}now: {words}", file=out)
        outfits = [{**o, "instruction": words} if o is existing else o for o in outfits]
        name = existing["label"]
        changed = True
    else:
        name = existing["label"]
        if not confirm_spend:
            print(
                f'{who} already has "{name}" with these words; nothing to send.',
                file=out,
            )
    episode = int(card.get("intro_episode_ordinal") or 1)
    fresh: dict[str, Any] | None = spine
    if changed:
        fresh, cascade = cc._send_cast_patch(
            desk,
            _brief_with_outfits(card, outfits),
            episode=episode,
            select_regen=False,
            preview_only=preview_only,
            out=out,
        )
        if fresh is None:
            print("Not applied: --preview; nothing was sent", file=out)
            return None
        save_spine_snapshot(desk, episode, fresh)
        if not any(
            _same(o["label"], name) for o in outfits_of(cc.exit_card(fresh, cast_id))
        ):
            print(
                f'!! the server answered but {who}\'s card does not show "{name}": it may be an older deploy. '
                "Nothing was charged; run the command again or ask engineering.",
                file=out,
            )
            return None
        ec._note(
            desk,
            episode,
            f'outfit: {who} ({cast_id}) "{name}": {words}'
            + (" through the cast cascade" if cascade else ""),
        )
        print("Applied", file=out)
        _mark_priced(desk, _priced_key(cast_id, name), on=False)
    if preview_only:
        print("(preview only: nothing was sent)", file=out)
        return None
    if not _sheet_drawn(fresh or spine, cast_id):
        print(
            f'{who} has no sheet drawn yet: "{name}" is drawn on the sheet with the others at the plates step. '
            f'Next: dress the shots with outfit --cast "{who}" --wear "{name}" --episodes N.',
            file=out,
        )
        return None
    key = _priced_key(cast_id, name)
    if not confirm_spend:
        _mark_priced(desk, key, on=True)
        print(_sheet_price(desk, who, name), file=out)
        print(
            f'Then dress the shots: outfit --desk {desk} --cast "{who}" --wear "{name}" --episodes N (free).',
            file=out,
        )
        return None
    if not _was_priced(desk, key):
        _mark_priced(desk, key, on=True)
        print(_sheet_price(desk, who, name, again=True), file=out)
        return None
    plate = ec.run_redraw_plate_with_note(
        desk,
        cast=cast_id,
        note=f'Show the outfit "{name}" on the sheet as its own figure.',
        out=out,
    )
    _mark_priced(desk, key, on=False)
    print(
        f'Next: dress the shots: outfit --desk {desk} --cast "{who}" --wear "{name}" --episodes N (free).',
        file=out,
    )
    return plate


def run_outfit_remove(desk: Path, *, cast: str, label: str, out: Any = None) -> None:
    """Take one outfit off a card; refused while any shot or the plan still uses it. Free."""

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    spine = _read_spine(desk)
    card = cc.exit_card(spine, cast)
    cast_id = str(card["cast_id"])
    who = str(card.get("name") or cast_id)
    name = resolve_outfit(card, label)
    if name is None:
        raise ec.CommandStopped("the base outfit cannot be removed; nothing was sent")
    wearing = sorted(
        {
            f"ep{ordinal:02d}"
            for ordinal in set(_episode_ordinals(spine).values())
            for _, _, _, worn in shots_of(spine, cast_id, ordinal)
            if _same(worn, name)
        }
    )
    planned = sorted(
        int(n)
        for n, v in ((load_plan(desk).get(cast_id) or {}).get("episodes") or {}).items()
        if _same(v, name)
    )
    if wearing or planned:
        where = ", ".join(
            wearing
            + [f"ep{n:02d} (planned)" for n in planned if f"ep{n:02d}" not in wearing]
        )
        raise ec.CommandStopped(
            f'{who} still wears "{name}" in {where}. Dress those episodes in another outfit first '
            f'(outfit --cast "{who}" --wear base --episodes N). Nothing was sent'
        )
    outfits = [o for o in outfits_of(card) if o["label"] != name]
    print(f'outfit for {who} ({cast_id}): - "{name}"', file=out)
    episode = int(card.get("intro_episode_ordinal") or 1)
    fresh, cascade = cc._send_cast_patch(
        desk,
        _brief_with_outfits(card, outfits),
        episode=episode,
        select_regen=False,
        preview_only=False,
        out=out,
    )
    if fresh is not None:
        save_spine_snapshot(desk, episode, fresh)
    ec._note(
        desk,
        episode,
        f'outfit: {who} ({cast_id}) "{name}" removed'
        + (" through the cast cascade" if cascade else ""),
    )
    print("Applied", file=out)


def _wear_patch(
    spine: Mapping[str, Any], cast_id: str, episode: int, label: str | None
) -> tuple[dict[str, Any], list[str], set[int]]:
    entries: list[dict[str, Any]] = []
    changed: list[str] = []
    takes: set[int] = set()
    for take, frame, index, worn in shots_of(spine, cast_id, episode):
        if _same(worn, label):
            continue
        brief = copy.deepcopy(dict(frame.get("visual_brief") or {}))
        brief["subject_blocking"][index] = {
            **brief["subject_blocking"][index],
            "wardrobe_variant": label,
        }
        entries.append({"frame_id": frame["frame_id"], "visual_brief": brief})
        changed.append(
            f"  t{take} shot {frame.get('ordinal')} ({frame['frame_id']}): {_say(worn)} -> {_say(label)}"
        )
        takes.add(take)
    return {"frames": entries}, changed, takes


def run_outfit_wear(
    desk: Path,
    *,
    cast: str,
    label: str,
    episodes: str,
    preview_only: bool = False,
    out: Any = None,
) -> None:
    """Dress one character in one outfit for every shot of theirs in ``episodes``; record the plan. Free.

    Raises
    ------
    ec.CommandStopped
        Bad ``--episodes``, an outfit not on their card, or a server refusal (the episodes sent before it
        stay sent and are named).
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    numbers = parse_episodes(episodes)
    spine = _read_spine(desk)
    card = cc.exit_card(spine, cast)
    cast_id = str(card["cast_id"])
    who = str(card.get("name") or cast_id)
    wanted = resolve_outfit(card, label)
    approved = spine.get("approval_state") == "approved"
    done: list[int] = []
    pending: list[int] = []
    for number in numbers:
        if not _has_frames(spine, number):
            pending.append(number)
            print(
                f"ep{number:02d}: shots not written yet; the plan is kept and checked before its boards.",
                file=out,
            )
            continue
        patch, changed, takes = _wear_patch(spine, cast_id, number, wanted)
        if not changed:
            shots = shots_of(spine, cast_id, number)
            print(
                f"ep{number:02d}: "
                + (
                    f"all {len(shots)} shot(s) already {_say(wanted)}."
                    if shots
                    else f"{who} is in no shot."
                ),
                file=out,
            )
            done.append(number)
            continue
        if preview_only and not approved:
            print(f"ep{number:02d} outfit ({who}):", file=out)
            for row in changed:
                print(row, file=out)
            continue
        try:
            _, _, fresh, _, _, _ = ec._send_story_edit(
                desk,
                episode=number,
                build=lambda _s, p=patch, c=changed: (p, c, f"outfit ({who})"),
                select_regen=False,
                preview_only=preview_only,
                out=out,
            )
        except ec.CommandStopped as exc:
            sent = (
                f" Episodes already changed: {', '.join(str(n) for n in done)}."
                if done and not preview_only
                else ""
            )
            raise ec.CommandStopped(f"ep{number:02d}: {exc}{sent}") from None
        spine = fresh
        done.append(number)
        if preview_only:
            continue
        print(f"ep{number:02d}: {len(changed)} shot(s) now {_say(wanted)}.", file=out)
        drawn = {take for take, _ in board_assets(spine, episode=number)}
        stale = sorted(takes & drawn)
        for take in stale:
            print(
                f"  t{take} board was drawn with the old outfit: redraw it (${float(STILL_USD):.2f}, after the human's yes): "
                f'fictora-produce redraw-board --desk {desk} --episode {number} --take t{take} --cause "outfit: {label}"',
                file=out,
            )
    if preview_only:
        print("Not applied: --preview; nothing was sent", file=out)
        return
    _record_wear(desk, card, wanted, numbers)
    first = numbers[0] if numbers else 1
    ec._note(
        desk,
        first,
        f"outfit: {who} ({cast_id}) wears {_say(wanted)} in episode(s) {episodes}",
    )
    if pending:
        print(
            f"Plan saved for ep {', '.join(str(n) for n in pending)}: once those shots are written, the board price "
            f"warns about any shot of {who} not in {_say(wanted)}; run this command again then.",
            file=out,
        )
    print("Applied", file=out)


__all__ = [
    "BASE",
    "MAX_OUTFITS",
    "load_plan",
    "outfit_plan_lines",
    "outfits_of",
    "parse_episodes",
    "resolve_outfit",
    "run_outfit_add",
    "run_outfit_list",
    "run_outfit_remove",
    "run_outfit_wear",
    "shots_of",
]
