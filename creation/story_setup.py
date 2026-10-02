"""Change what a story was set up with: its spoken language, its brief, and its cast floor.

Production learnings, 1 Oct 2026:

- **A show voiced in Japanese but drafted en-US** (Kuchisake-onna,
  L-20261001-21, -28). ``language --desk D --spoken ja|ko|en`` changes the
  show's language on the server (fictora-drama ``POST
  /v1/spines/{id}/spoken-language``). It shows what the change sets aside
  first (the server's own preview): every performed line, subtitle and gloss
  written for the old language, seed voice clips, speech-level examples, and
  any takes filmed in another language (refused unless ``--confirm-filmed``).
  After it, ``line --spoken`` pins performed lines as on any Japanese show; the
  lines ``line --spoken`` recorded on the desk while the server held the show
  as English are printed as the commands that send them.
- **The stored brief kept narration the script dropped** (L-20261001-1). ``brief
  --desk D --edit @FILE`` replaces the brief on the server (fictora-drama ``PUT
  /v1/spines/{id}/scene-prompt``), ``--strip-narration`` takes the narrator lines
  out of the desk's brief and sends the rest. Both show the change first. Before
  the draft, the same edits change the desk's brief, which the draft sends.
  The draft warns when a brief carries narrator lines (runbook: voice-over is
  laid dry in post, never written into the take).
- **One-character shows** (L-20261001-4). A server that reports ``cast_floor``
  1 (``GET /v1/capabilities``) is asked for exactly the people the story needs;
  an older one still gets the two-member directive (:mod:`creation.plan_prompt`).

Every command ends on a verdict line: ``Applied``, ``Refused: <reason>``, or
``Not applied`` after ``--preview``. Against a server without these routes the
command is refused with that reason and nothing changes.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, TextIO

from creation import episode_commands as ec
from creation.cast_commands import declared_language, language_code
from creation.cli_text import TextArgError, text_or_file
from creation.harness.http_util import api_error_text
from creation.orchestrate import save_spine_snapshot
from creation.plan_prompt import narrator_lines, strip_narration
from creation.production_config import (
    load_production_config,
    save_production_config,
)
from creation.production_state import load_production, save_production

LANGUAGE_ROUTE = "/v1/spines/{spine_id}/spoken-language"
BRIEF_ROUTE = "/v1/spines/{spine_id}/scene-prompt"

#: The desk's ``production.config.json`` names the language by its short code.
_SHORT_CODE = {"ja-JP": "ja", "ko-KR": "ko", "en-US": None}

# --- Shared plumbing -----------------------------------------------------------------------------


def _error(body: Any) -> tuple[str, str, Any]:
    error = body.get("error") if isinstance(body, Mapping) else None
    if not isinstance(error, Mapping):
        return "", api_error_text(body), None
    return (
        str(error.get("code") or ""),
        str(error.get("message") or ""),
        error.get("details"),
    )


def _no_route(status: int, body: Any, what: str) -> str | None:
    """The refusal for a server without the route: a 404/405 that is not the API's own error."""

    code, _, _ = _error(body)
    if status in (404, 405) and not code:
        return f"this server has no {what} route yet (fictora-drama #582 is not deployed); nothing was sent"
    return None


def _verdict(
    out: TextIO, *, refused: str | None = None, preview: bool = False, note: str = ""
) -> None:
    if preview:
        print(
            f"Not applied: --preview showed the change; nothing was sent{note}",
            file=out,
        )
    elif refused is not None:
        print(f"Refused: {ec.refusal_reason(refused)}", file=out)
    else:
        print(f"Applied{note}", file=out)


# --- language ------------------------------------------------------------------------------------


def _effect_rows(effect: Mapping[str, Any], spine: Mapping[str, Any]) -> list[str]:
    names = {
        str(card.get("cast_id")): str(card.get("name"))
        for card in spine.get("cast") or []
    }
    rows: list[str] = []
    cleared = list(effect.get("performed_lines_cleared") or [])
    if cleared:
        rows.append(
            f"  performed lines cleared (written for the old language): {len(cleared)}"
        )
    count = int(effect.get("lines_to_localize") or 0)
    when = {
        "script_approval": "at script approval",
        "next_take": "before the next take (the script is approved)",
    }.get(str(effect.get("when_localized")), "")
    if count:
        rows.append(
            f"  lines localized again into the new language: {count}, {when}".rstrip(
                ", "
            )
        )
    if effect.get("speech_levels_cleared"):
        rows.append(f"  speech levels cleared: {effect['speech_levels_cleared']}")
    if effect.get("speech_level_examples_cleared"):
        rows.append(
            f"  speech levels kept, old-language example dropped: {effect['speech_level_examples_cleared']}"
        )
    voices = [
        names.get(str(cast_id), str(cast_id))
        for cast_id in effect.get("voice_references_cleared") or []
    ]
    if voices:
        rows.append(
            "  seed voice clips made again in the new language with the next take (same voice): "
            + ", ".join(voices)
        )
    filmed = list(effect.get("filmed_in_other_language") or [])
    if filmed:
        rows.append(
            f"  !! filmed in another language: {', '.join(filmed)} (those takes keep their audio)"
        )
    rows.append(
        "  kept: the English script, subtitles (English), names, looks, plates, boards, approvals"
    )
    return rows


def _recorded_pins(desk: Path) -> list[Mapping[str, Any]]:
    path = desk / "shared" / "spoken-language.json"
    if not path.is_file():
        return []
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(
            f"(cannot read {path}: {exc}; no recorded lines to replay)", file=sys.stderr
        )
        return []
    pins = record.get("pinned_lines") if isinstance(record, Mapping) else None
    return [pin for pin in pins or [] if isinstance(pin, Mapping) and pin.get("spoken")]


def replay_commands(desk: Path, language: str) -> list[str]:
    """The ``line --spoken`` commands that send the lines recorded while the server held the show as English.

    Parameters
    ----------
    desk
        Series desk.
    language
        The show's language now (``ja-JP`` / ``ko-KR``).

    Returns
    -------
    list[str]
        One command per recorded line, in the order recorded; empty for an English show.
    """

    if language == "en-US":
        return []
    short = _SHORT_CODE.get(language) or "ja"
    commands: list[str] = []
    for pin in _recorded_pins(desk):
        target = (
            f"--line {pin['line_id']}"
            if pin.get("line_id")
            else f"--add --beat {pin.get('beat_id')}"
        )
        subtitle = (
            f" --subtitle {json.dumps(str(pin['subtitle']), ensure_ascii=False)}"
            if pin.get("subtitle")
            else ""
        )
        commands.append(
            f"fictora-produce line --desk {desk} --episode {pin.get('episode', 1)} {target} "
            f"--spoken {json.dumps(str(pin['spoken']), ensure_ascii=False)}{subtitle} --language {short}"
        )
    return commands


def _remember_language(desk: Path, language: str) -> None:
    """Keep the desk's draft language and its language record in step with the server."""

    cfg = load_production_config(desk)
    short = _SHORT_CODE.get(language)
    if cfg.spoken_language != short:
        save_production_config(desk, dataclasses.replace(cfg, spoken_language=short))
    path = desk / "shared" / "spoken-language.json"
    if path.is_file():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return
        if isinstance(record, dict):
            record["server"] = language
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


def run_language(
    desk: Path,
    *,
    spoken: str,
    preview: bool = False,
    confirm_filmed: bool = False,
    out: TextIO | None = None,
) -> bool:
    """Change the language the show is performed in (``language --desk D --spoken ja|ko|en``).

    The server's preview is shown first (what is cleared and localized again,
    voice clips made again, takes filmed in another language); then the change
    is sent unless ``--preview``. Spends nothing: the localization pass runs at
    script approval or before the next take, and new voice clips with the next
    take.

    Parameters
    ----------
    desk
        Series desk with a story on the API.
    spoken
        ``ja``, ``ko`` or ``en`` (or the full tag).
    preview
        Show the change and send nothing.
    confirm_filmed
        Change it even though takes were filmed in another language.
    out
        Text stream.

    Returns
    -------
    bool
        True when the change was applied (or there was nothing to change).
    """

    out = out or sys.stdout
    language = language_code(spoken)
    if not load_production(desk.expanduser().resolve()).spine_id:
        # Before the draft the language is the desk's: the draft sends it.
        desk = desk.expanduser().resolve()
        before = load_production_config(desk).spoken_language
        print(
            f"language: {before or 'en'} -> {_SHORT_CODE.get(language) or 'en'} (on the desk)",
            file=out,
        )
        if preview:
            _verdict(out, preview=True)
            return False
        _remember_language(desk, language)
        _verdict(out, note=" (on the desk; no story yet: the draft sends it)")
        return True
    desk, state, run = ec._desk_session(desk)
    try:
        spine = run.spine(state.spine_id or "")
        current = ec._spoken_language(spine)
        print(f"language: {current} -> {language}", file=out)
        if current == language:
            _remember_language(desk, language)
            print(
                f"Not applied: the show is already performed in {language}; nothing was sent",
                file=out,
            )
            return True
        declared = declared_language(desk)
        if declared and declared != language:
            print(
                f"  !! the desk recorded this show as {declared}, not {language}",
                file=out,
            )
        path = LANGUAGE_ROUTE.format(spine_id=state.spine_id)
        body: dict[str, Any] = {
            "spine_version": spine["spine_version"],
            "spoken_language": language,
            "confirm_filmed": confirm_filmed,
        }
        status, answer = run.post_optional(path, {**body, "preview": True})
        refused = _no_route(status, answer, "spoken-language")
        if refused is None and status >= 400:
            code, message, details = _error(answer)
            if isinstance(details, Mapping) and isinstance(
                details.get("effect"), Mapping
            ):
                for row in _effect_rows(details["effect"], spine):
                    print(row, file=out)
            hint = (
                " Pass --confirm-filmed after the human's yes."
                if code == "spoken_language_change_filmed"
                else ""
            )
            refused = f"{code}: {message}{hint}" if code else message
        if refused is not None:
            _verdict(out, refused=refused)
            return False
        for row in _effect_rows(answer.get("effect") or {}, spine):
            print(row, file=out)
        if preview:
            _verdict(out, preview=True)
            return False
        status, answer = run.post_optional(path, body)
        if status >= 400:
            code, message, _ = _error(answer)
            _verdict(out, refused=f"{code}: {message}" if code else message)
            return False
        fresh = run.spine(state.spine_id or "")
    finally:
        run.client.close()
    save_spine_snapshot(desk, state.episode_ordinal, fresh)
    _remember_language(desk, language)
    ec._note(
        desk,
        state.episode_ordinal,
        f"language: the show is now performed in {language} (was {current}); "
        f"{len((answer.get('effect') or {}).get('performed_lines_cleared') or [])} performed line(s) cleared, "
        "lines are localized again at script approval or before the next take.",
    )
    commands = replay_commands(desk, language)
    if commands:
        print(
            f"  {len(commands)} performed line(s) recorded on the desk while the server held the show as "
            f"{current}; send them now (each ends on its own verdict):",
            file=out,
        )
        for command in commands:
            print(f"    {command}", file=out)
    _verdict(out)
    return True


# --- brief ---------------------------------------------------------------------------------------


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _desk_brief(desk: Path, spine: Mapping[str, Any] | None) -> tuple[str, str]:
    """The brief to edit, with line breaks where the desk still has them, and where it came from."""

    state = load_production(desk)
    stored = str((spine or {}).get("scene_prompt_normalized") or "")
    if spine is None or not stored or _normalized(state.prompt) == _normalized(stored):
        return state.prompt, "the desk's brief"
    return stored, "the server's stored brief (one line: the desk's copy differs)"


def _diff_rows(before: str, after: str) -> list[str]:
    rows = []
    for row in difflib.unified_diff(
        before.splitlines(), after.splitlines(), lineterm="", n=0
    ):
        if row.startswith(("---", "+++", "@@")):
            continue
        rows.append(f"  {row[:160]}")
    return rows


def run_brief_edit(
    desk: Path,
    *,
    edit: str | None = None,
    strip: bool = False,
    preview: bool = False,
    out: TextIO | None = None,
) -> bool:
    """Replace the story's brief (``brief --desk D --edit @FILE | --strip-narration``).

    Shows the change line by line first. With a story on the API, the server's
    preview validates it (a locked line that cannot fit is refused there, as at
    the draft), then it is sent unless ``--preview``, and the desk's brief
    follows it so later drafts and takes send the same words. Before the draft,
    only the desk's brief changes: the draft sends it. Spends nothing.

    Parameters
    ----------
    desk
        Series desk.
    edit
        The whole new brief: inline words, ``@file`` or a path.
    strip
        Take the narrator and voice-over lines out of the current brief.
    preview
        Show the change and send nothing.
    out
        Text stream.

    Returns
    -------
    bool
        True when the brief was replaced (or already read this way).

    Raises
    ------
    ec.CommandStopped
        Neither or both of ``edit`` and ``strip``; an unreadable ``@file``.
    """

    out = out or sys.stdout
    if (edit is None) == (not strip):
        raise ec.CommandStopped(
            "brief: pass --edit @FILE or --strip-narration (one of them)"
        )
    desk = desk.expanduser().resolve()
    state = load_production(desk)
    run = ec._open_run(desk, state) if state.spine_id else None
    try:
        spine = run.spine(state.spine_id or "") if run is not None else None
        current, source = _desk_brief(desk, spine)
        if strip:
            if source != "the desk's brief":
                raise ec.CommandStopped(
                    "brief --strip-narration reads the desk's brief line by line, and the server's stored brief "
                    "differs from it (the server keeps it on one line). Write the whole brief to a file and pass "
                    "--edit @FILE; nothing was sent"
                )
            new = strip_narration(current)
        else:
            try:
                new = text_or_file(str(edit), flag="--edit")
            except TextArgError as exc:
                raise ec.CommandStopped(str(exc)) from None
        print(f"brief: {source}", file=out)
        if _normalized(new) == _normalized(current) and (
            spine is None
            or _normalized(new)
            == _normalized(str(spine.get("scene_prompt_normalized") or ""))
        ):
            reason = (
                "it has no narrator lines" if strip else "it already reads this way"
            )
            print(f"Not applied: {reason}; nothing was sent", file=out)
            return True
        for row in _diff_rows(current, new) or ["  (whitespace only)"]:
            print(row, file=out)
        left = narrator_lines(new)
        if left:
            print(
                f"  !! the new brief still has {len(left)} narrator / voice-over line(s): {left[0][:80]}",
                file=out,
            )
        if run is None:
            if preview:
                _verdict(out, preview=True)
                return False
            state.prompt = new
            save_production(desk, state)
            _verdict(out, note=" (on the desk; no story yet: the draft sends it)")
            return True
        assert spine is not None
        path = BRIEF_ROUTE.format(spine_id=state.spine_id)
        body = {"spine_version": spine["spine_version"], "scene_prompt": new}
        status, answer = run.put_optional(path, {**body, "preview": True})
        refused = _no_route(status, answer, "brief (scene-prompt)")
        if refused is None and status >= 400:
            code, message, _ = _error(answer)
            refused = f"{code}: {message}" if code else message
        if refused is not None:
            _verdict(out, refused=refused)
            return False
        if answer.get("locked_line_count"):
            print(
                f"  locked lines the server reads in it: {answer['locked_line_count']}",
                file=out,
            )
        if preview:
            _verdict(out, preview=True)
            return False
        status, answer = run.put_optional(path, body)
        if status >= 400:
            code, message, _ = _error(answer)
            _verdict(out, refused=f"{code}: {message}" if code else message)
            return False
        fresh = run.spine(state.spine_id or "")
    finally:
        if run is not None:
            run.client.close()
    state.prompt = new
    save_production(desk, state)
    save_spine_snapshot(desk, state.episode_ordinal, fresh)
    ec._note(
        desk,
        state.episode_ordinal,
        "brief: the stored brief was replaced"
        + (" (narrator lines taken out)" if strip else "")
        + f"; the next takes and episodes read it (sha256 {answer.get('scene_prompt_sha256')}).",
    )
    _verdict(out)
    return True


__all__ = [
    "BRIEF_ROUTE",
    "LANGUAGE_ROUTE",
    "replay_commands",
    "run_brief_edit",
    "run_language",
]
