"""`look-options`: pick the art style before the plates, from two or three looks drawn side by side.

GRAFT (action-series review, 15 Oct 2026): three looks and two sets of boards were paid for, one
after the other, before the 80s cel style was accepted (about $2.40 of redraws). The server now
offers a new story two or three looks at one still each and holds the plates until one is
picked (fictora-drama ``look_options``). This module is a thin client of it:

* ``look-options --desk D`` asks the server whether the story is offered a pick and, when it is
  and nothing is drawn, what drawing the looks costs (``preview``: nothing drawn or booked);
* ``--confirm-spend`` (after the human's yes to that price) draws them, saves each as
  ``shared/look/look-option-N-vM.png`` and books what the server booked;
* ``--pick N`` (after the human chose) pins that look on the server and records the look yes on
  the desk, like ``approve --gate look``.

The server writes every look (the story's own style first, then contrasting stylised ones for
its genre) unless the human described their own (``--description``, up to three). The kit keeps
nothing of its own: the options, the pick and the hold live on the server.

``step`` asks the same question before episode 1's plates on a new desk and stops while the
story is offered a pick (:func:`look_options_refusal`); ``step --keep-preset-look`` goes on
without one when the human says the preset's look is the one.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from creation import episode_commands as ec
from creation import orchestrate
from creation.cli_text import TextArgError, text_or_file
from creation.ops.floor import approve_series_gate, record_spend

#: Where the options are saved on the desk (never named ``look-frame-*``: the desk's own look-frame
#: gate is for frames drawn one at a time; the options' hold is the server's).
OPTION_STEM = "look-option"


def _route_missing(status: int, body: Any) -> bool:
    return status == 404 and isinstance(body, Mapping) and "error" not in body


def _refusal(body: Any) -> str:
    error = body.get("error") if isinstance(body, Mapping) else None
    return str(error.get("message")) if isinstance(error, Mapping) else str(body)


def _descriptions(values: Sequence[str]) -> list[str]:
    found: list[str] = []
    for value in values:
        try:
            words = text_or_file(value, flag="--description")
        except TextArgError as exc:
            raise ec.CommandStopped(str(exc)) from exc
        if words:
            found.append(words)
    return found


def _print_options(answer: Mapping[str, Any], out: Any) -> None:
    for index, option in enumerate(answer.get("options") or [], start=1):
        if isinstance(option, Mapping):
            url = option.get("image_url")
            print(
                f"  {index}. {option.get('label')}" + (f"  {url}" if url else ""),
                file=out,
            )


def run_look_options(
    desk: Path,
    *,
    confirm_spend: bool = False,
    pick: int | None = None,
    count: int = 3,
    descriptions: Sequence[str] = (),
    out: Any = None,
) -> Mapping[str, Any]:
    """Show, draw or pick the story's art style options (thin calls to the server).

    Parameters
    ----------
    desk
        Series desk with a drafted story.
    confirm_spend
        The human said yes to the price: draw the looks.
    pick
        The option number (1-based) the human picked.
    count
        How many looks the server writes (2 or 3) when no descriptions are given.
    descriptions
        The human's own looks (words, ``@FILE`` or a file path), drawn as written.
    out
        Text stream.

    Returns
    -------
    Mapping[str, Any]
        The server's answer (the options view, or the story after a pick).

    Raises
    ------
    CommandStopped
        A server refusal, an older server without the route, or a pick that names no option.
    """

    out = out or sys.stdout
    desk = desk.expanduser().resolve()
    if count not in (2, 3):
        raise ec.CommandStopped("--count is 2 or 3")
    looks = _descriptions(descriptions)
    if len(looks) > 3:
        raise ec.CommandStopped("at most three --description looks")
    desk, state, run = ec._desk_session(desk)
    base = f"/v1/spines/{state.spine_id}/look-options"
    try:
        status, view = run.get_optional(base)
        if _route_missing(status, view):
            raise ec.CommandStopped(
                "this Drama API has no look-options route yet (an older deploy); nothing was sent. "
                "Draw a look frame with `look-frame` instead, or ask engineering to deploy it."
            )
        if status >= 400:
            raise ec.CommandStopped(f"Refused: {_refusal(view)}")
        if pick is not None:
            return _pick(desk, state, run, view, pick, out)
        body: dict[str, Any] = {"count": count, "descriptions": looks}
        digest = hashlib.sha256(repr((count, looks)).encode()).hexdigest()[:24]
        if not view.get("offered"):
            # The server answers why (a continuing show, drawn characters, a pinned, picked or
            # kept look); nothing is priced or drawn, and a pick is never cleared.
            print(
                str(view.get("reason") or "This story is not offered a style pick."),
                file=out,
            )
            if confirm_spend:
                raise ec.CommandStopped(
                    f"Refused: {view.get('reason') or 'this story is not offered a style pick'} Nothing was drawn."
                )
            if view.get("options"):
                _print_options(view, out)
            return view
        if not confirm_spend:
            print(str(view.get("reason") or ""), file=out)
            if view.get("options") and not looks:
                print("Drawn looks:", file=out)
                _print_options(view, out)
                print(
                    f"After the human picks one: fictora-produce look-options --desk {desk} --pick N",
                    file=out,
                )
                return view
            # Free; a deterministic key anyway, so an older server that asks for one still answers.
            status, priced = run.post_optional(
                base,
                {**body, "preview": True},
                idempotency_key=f"{run.prefix}-look-options-preview-{digest}",
            )
            if status >= 400:
                raise ec.CommandStopped(f"Refused: {_refusal(priced)}")
            usd = float(priced.get("cost_usd") or 0.0)
            print(
                f"Looks for this story, about ${usd:.2f} (one picture each). Nothing drawn yet.",
                file=out,
            )
            _print_options(priced, out)
            print(
                f"Human yes, then: fictora-produce look-options --desk {desk}"
                + "".join(" --description …" for _ in looks)
                + (f" --count {count}" if count != 3 and not looks else "")
                + " --confirm-spend",
                file=out,
            )
            return priced
        status, drawn = run.post_optional(
            base, body, idempotency_key=f"{run.prefix}-look-options-{digest}"
        )
        if status >= 400:
            raise ec.CommandStopped(f"Refused: {_refusal(drawn)}")
    finally:
        run.client.close()
    folder = desk / "shared" / "look"
    fetch = httpx.Client(timeout=120.0)
    saved: list[Path] = []
    try:
        for index, option in enumerate(drawn.get("options") or [], start=1):
            url = str(option.get("image_url") or "")
            if url.startswith("https://"):
                saved.append(
                    orchestrate.download_to_versioned(
                        fetch, url, folder, f"{OPTION_STEM}-{index}"
                    )
                )
    finally:
        fetch.close()
    usd = float(drawn.get("cost_usd") or 0.0)
    if usd > 0:
        record_spend(desk, episode=1, usd=usd, unit="look-options")
    labels = ", ".join(
        f"{i}. {o.get('label')}"
        for i, o in enumerate(drawn.get("options") or [], start=1)
    )
    ec._note(
        desk,
        1,
        f"look-options drawn (${usd:.2f}{', cached' if drawn.get('cached') else ''}): {labels}",
    )
    for index, path in enumerate(saved, start=1):
        label = (drawn.get("options") or [])[index - 1].get("label")
        print(f"  {index}. {label}: {path}", file=out)
    print(
        f"Show the {len(saved)} looks side by side. After the human picks one: "
        f"fictora-produce look-options --desk {desk} --pick N (the plates wait for it).",
        file=out,
    )
    return drawn


def _pick(
    desk: Path, state: Any, run: Any, view: Mapping[str, Any], pick: int, out: Any
) -> Mapping[str, Any]:
    options = [o for o in view.get("options") or [] if isinstance(o, Mapping)]
    if not 1 <= pick <= len(options):
        raise ec.CommandStopped(
            f"--pick {pick} names no drawn look; the story has {len(options)}"
            + (
                " (draw them first: look-options --confirm-spend)"
                if not options
                else ""
            )
        )
    option = options[pick - 1]
    spine = run.spine(state.spine_id or "")
    status, answer = run.post_optional(
        f"/v1/spines/{state.spine_id}/look-options/{option['option_id']}/pick",
        {"spine_version": spine["spine_version"]},
    )
    if status >= 400:
        raise ec.CommandStopped(f"Refused: {_refusal(answer)}")
    orchestrate.save_spine_snapshot(desk, state.episode_ordinal, dict(answer))
    url = str(option.get("image_url") or "")
    files = sorted(
        (desk / "shared" / "look").glob(f"{OPTION_STEM}-{pick}-v*"),
        key=lambda path: (
            int(path.stem.rsplit("-v", 1)[1])
            if path.stem.rsplit("-v", 1)[1].isdigit()
            else -1
        ),
    )
    shown = str(files[-1].relative_to(desk)) if files else url
    approve_series_gate(desk, "look", path=shown, note=f"pinned {url}", url=url)
    ec._note(desk, 1, f"look picked: {pick}. {option.get('label')} ({url}).")
    print(
        f"Look picked: {option.get('label')}. It is the story's look; next: fictora-produce step --desk {desk}",
        file=out,
    )
    from creation.caption_preview import caption_style_at_look

    caption_style_at_look(desk, out=out)
    return answer


def keep_preset_look(desk: Path, run: Any, spine_id: str, out: Any = None) -> None:
    """Tell the server the human keeps the preset's look: drawn options are dropped, the hold lifts. Free.

    Parameters
    ----------
    desk
        Series desk.
    run
        An open API session.
    spine_id
        The story.
    out
        Text stream.

    Raises
    ------
    RuntimeError
        The server refused (characters already drawn on a picked look).
    """

    status, answer = run.delete_optional(f"/v1/spines/{spine_id}/look-options")
    if _route_missing(status, answer):
        return  # an older server holds nothing
    if status >= 400:
        raise RuntimeError(f"Refused: {_refusal(answer)} Nothing was sent.")
    ec._note(desk, 1, "look-options: the human kept the preset's look.")
    print(
        "Kept the preset's look; no style pick is asked for this story.",
        file=out or sys.stdout,
    )


def look_options_refusal(desk: Path, run: Any, spine_id: str) -> str | None:
    """The stop ``step`` prints before episode 1's plates while the server offers a style pick.

    Parameters
    ----------
    desk
        Series desk.
    run
        An open API session.
    spine_id
        The story.

    Returns
    -------
    str | None
        The refusal, or ``None`` when the story is not offered a pick (a continuing show, a pinned
        or picked look, drawn characters) or the server is older.
    """

    status, view = run.get_optional(f"/v1/spines/{spine_id}/look-options")
    if status >= 400 or not isinstance(view, Mapping):
        return None
    drawn = bool(view.get("options")) and not view.get("picked_option_id")
    if not view.get("offered") and not drawn:
        return None
    if drawn:
        return (
            "Refused: the looks are drawn and none is picked, so the plates would be drawn in a look nobody "
            f"chose. Nothing was sent. Show them; after the human's pick: fictora-produce look-options --desk {desk} "
            "--pick N. Then run step again."
        )
    return (
        "Refused: pick the art style before the plates. Nothing was sent. "
        f"fictora-produce look-options --desk {desk} prints two or three looks for this story and their price "
        "(one picture each); after the human's yes, --confirm-spend draws them and --pick N picks one. "
        "If the human says the preset's look is the one: step --keep-preset-look."
    )


__all__ = [
    "OPTION_STEM",
    "keep_preset_look",
    "look_options_refusal",
    "run_look_options",
]
