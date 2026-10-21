"""A creator's line that cannot fit its take: show the server's fitted version and send back the choice.

Founder decision, 20 Oct 2026 (fictora-drama #962). When a line the creator
wrote or approved (a line the brief locks word for word, a line approved in a
picked direction, or a line edit) cannot fit the take it plays in, the server
answers with a line-fit proposal: a plain reason, the creator's lines, a fitted
(or trimmed) version and the choices. The server owns every word, number and
state. This module only prints the server's text word for word and sends the
creator's choice back; it keeps no rule, wording or state of its own.

Where proposals arrive:

* a failed first draft or episode job: error ``details.fit_proposals``, cause
  ``needs_creator_choice`` (an older server answers only
  ``locked_lines_out_of_bounds``; :mod:`creation.orchestrate` still pauses on that);
* a line edit (``PATCH /v1/spines/{id}``) or a brief replaced
  (``PUT /v1/spines/{id}/scene-prompt``): ``409 line_needs_creator_choice``
  with ``details.fit_proposals``.

Routes: ``GET /v1/line-fit-proposals?spine_id=|job_id=``,
``GET /v1/line-fit-proposals/{id}``, ``POST /v1/line-fit-proposals/{id}/choose``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

#: The server's code for a line that cannot fit its take (409, and the job's field error).
LINE_NEEDS_CREATOR_CHOICE = "line_needs_creator_choice"
#: The job error's ``details.cause`` for the same stop.
NEEDS_CREATOR_CHOICE_CAUSE = "needs_creator_choice"
#: Routes.
PROPOSALS_ROUTE = "/v1/line-fit-proposals"
#: ``--choose`` values and the offer action each answers.
CHOICE_ACTIONS: dict[str, str] = {
    "line_fit_use": "use",
    "line_fit_trim": "trim",
    "line_fit_edit": "edit",
}
CHOICES = ("use", "trim", "edit")


class LineFitStopped(RuntimeError):
    """The command stopped and says why (the CLI prints it and exits 2)."""


# --- reading proposals out of a refusal or a failed job -------------------------------------------


def _error_of(body: Any) -> Mapping[str, Any]:
    if not isinstance(body, Mapping):
        return {}
    error = body.get("error")
    if isinstance(error, Mapping):
        return error
    failure = body.get("failure")
    if isinstance(failure, Mapping):
        return failure
    return body


def fit_proposals(body: Any) -> list[dict[str, Any]]:
    """The ``details.fit_proposals`` of an API refusal or a failed job, or ``[]``.

    Parameters
    ----------
    body
        A 409 answer (``{"error": {...}}``) or a terminal job body.

    Returns
    -------
    list[dict[str, Any]]
        The server's proposals, as sent.
    """

    details = _error_of(body).get("details")
    if not isinstance(details, Mapping):
        return []
    found = details.get("fit_proposals")
    if not isinstance(found, list):
        return []
    return [
        dict(item)
        for item in found
        if isinstance(item, Mapping) and item.get("proposal_id")
    ]


def failure_cause(body: Any) -> str | None:
    """The job error's ``details.cause`` (``needs_creator_choice`` for a line-fit stop), if any."""

    details = _error_of(body).get("details")
    cause = details.get("cause") if isinstance(details, Mapping) else None
    return str(cause) if cause else None


def proposals_from_exit(exc: BaseException) -> list[dict[str, Any]]:
    """The proposals an :class:`~creation.harness.http_util.ApiRefused` or ``JobFailed`` carries, or ``[]``."""

    for name in ("body", "job"):
        found = fit_proposals(getattr(exc, name, None))
        if found:
            return found
    return []


# --- printing: the server's words, as sent ---------------------------------------------------------


def _line_row(line: Mapping[str, Any]) -> list[str]:
    speaker = line.get("speaker")
    said = f'{speaker}: "{line.get("text")}"' if speaker else f'"{line.get("text")}"'
    rows = [f"    [{line.get('slot')}] {said}"]
    if line.get("action"):
        rows.append(f"        ({line['action']})")
    return rows


def command(desk: Path | str, proposal_id: str, choice: str | None = None) -> str:
    """The ``line-fit`` command to paste for one proposal (and choice)."""

    text = f"fictora-produce line-fit --desk {desk} --proposal {proposal_id}"
    if choice == "edit":
        return text + " --choose edit --edit @lines.txt"
    return text + (f" --choose {choice}" if choice else "")


def proposal_card(proposal: Mapping[str, Any], *, desk: Path | str = "<desk>") -> str:
    """Print one proposal as the server wrote it: message, lines, notes, question, choices.

    Parameters
    ----------
    proposal
        One ``DramaLineFitProposal``.
    desk
        Series desk, for the commands to paste.

    Returns
    -------
    str
        The card. Every sentence in it is the server's; the kit adds only the
        slot names and the command for each choice.
    """

    pid = str(proposal.get("proposal_id"))
    where = f"episode {proposal.get('episode_ordinal')}"
    if proposal.get("take_number"):
        where += f", take {proposal['take_number']}"
    rows = [f"Line fit {pid} ({where}):", str(proposal.get("message") or "")]
    rows.append("  Your lines:")
    for line in proposal.get("original_lines") or []:
        rows.extend(_line_row(line))
    offered = proposal.get("fitted_lines") or proposal.get("trimmed_lines") or []
    if offered:
        rows.append(
            "  Fitted version:"
            if proposal.get("fitted_lines")
            else "  Trimmed to one take:"
        )
        for line in offered:
            rows.extend(_line_row(line))
    for note in proposal.get("rule_notes") or []:
        rows.append(f"  - {note}")
    if proposal.get("question"):
        rows.append(str(proposal["question"]))
    rows.append("  Choices:")
    for offer in proposal.get("choices") or []:
        choice = CHOICE_ACTIONS.get(str(offer.get("action")))
        rows.append(f"    {offer.get('label')}: {offer.get('text')}")
        if choice:
            rows.append(f"      {command(desk, pid, choice)}")
    return "\n".join(rows)


def pause_text(
    proposals: Sequence[Mapping[str, Any]], *, desk: Path | str = "<desk>"
) -> str:
    """The stop printed when a draft, episode, line edit or brief waits on the creator's line-fit choice.

    Parameters
    ----------
    proposals
        The server's ``fit_proposals``.
    desk
        Series desk.

    Returns
    -------
    str
        A one-line header, each card, and the command to show them again.
    """

    cards = "\n\n".join(proposal_card(p, desk=desk) for p in proposals)
    return (
        "PAUSED for the creator: a line does not fit its take. Show the creator the server's card below word "
        "for word and run the choice they make; nothing was drafted or changed.\n"
        f"{cards}\n"
        f"Show it again: fictora-produce line-fit --desk {desk}"
    )


# --- the command -----------------------------------------------------------------------------------


def read_edit_lines(source: str) -> list[dict[str, str]]:
    """Parse ``--edit``: ``@file`` or a path or words; JSON ``[{slot, text}]`` or one ``slot: text`` per line.

    Parameters
    ----------
    source
        The ``--edit`` value.

    Returns
    -------
    list[dict[str, str]]
        ``[{"slot", "text"}]`` as the server takes them.

    Raises
    ------
    LineFitStopped
        Nothing to send, or a line without its slot.
    """

    raw = source[1:] if source.startswith("@") else source
    try:
        path = Path(raw).expanduser()
        text = path.read_text(encoding="utf-8") if path.is_file() else raw
    except OSError:
        text = raw
    try:
        loaded = json.loads(text)
    except ValueError:
        loaded = None
    if isinstance(loaded, Mapping):
        loaded = loaded.get("lines")
    lines: list[dict[str, str]] = []
    if isinstance(loaded, list):
        for item in loaded:
            if (
                isinstance(item, Mapping)
                and item.get("slot")
                and str(item.get("text") or "").strip()
            ):
                lines.append(
                    {"slot": str(item["slot"]), "text": str(item["text"]).strip()}
                )
    else:
        for row in text.splitlines():
            if not row.strip():
                continue
            slot, sep, said = row.partition(":")
            if not sep or not slot.strip() or not said.strip():
                raise LineFitStopped(
                    f"--edit: each line is `slot: the new line` (slots are in brackets on the card); got {row!r}"
                )
            lines.append({"slot": slot.strip().strip("[]"), "text": said.strip()})
    if not lines:
        raise LineFitStopped("--edit has no lines to send; nothing was sent")
    return lines


def _open(desk: Path) -> tuple[Path, Any, Any]:
    from creation import episode_commands as ec
    from creation.production_state import load_production

    desk = desk.expanduser().resolve()
    state = load_production(desk)
    return desk, state, ec._open_run(desk, state)


def _no_route(status: int, body: Any) -> bool:
    return status in (404, 405) and not _error_of(body).get("code")


def open_proposals(run: Any, state: Any) -> list[dict[str, Any]]:
    """The desk's open proposals: its story's, then each saved job's (a first draft has no story yet).

    Parameters
    ----------
    run
        Session.
    state
        The desk's production state.

    Returns
    -------
    list[dict[str, Any]]
        Open proposals, newest first, each once.

    Raises
    ------
    LineFitStopped
        The server has no line-fit route yet.
    """

    queries: list[str] = []
    if state.spine_id:
        queries.append(f"spine_id={state.spine_id}")
    for record in (state.pending or {}).values():
        if isinstance(record, Mapping):
            job = record.get("plan_job_id") or record.get("job_id")
            if job:
                queries.append(f"job_id={job}")
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for query in queries:
        status, body = run.get_optional(f"{PROPOSALS_ROUTE}?{query}")
        if _no_route(status, body):
            raise LineFitStopped(
                "this server has no line-fit route yet (fictora-drama #962 is not deployed); nothing was sent"
            )
        if status >= 400 or not isinstance(body, Mapping):
            continue
        for proposal in body.get("proposals") or []:
            pid = str(proposal.get("proposal_id"))
            if proposal.get("status", "open") == "open" and pid not in seen:
                seen.add(pid)
                found.append(dict(proposal))
    return found


def _get_proposal(run: Any, proposal_id: str) -> dict[str, Any]:
    status, body = run.get_optional(f"{PROPOSALS_ROUTE}/{proposal_id}")
    if _no_route(status, body):
        raise LineFitStopped(
            "this server has no line-fit route yet (fictora-drama #962 is not deployed); nothing was sent"
        )
    if status >= 400 or not isinstance(body, Mapping):
        from creation.harness.http_util import api_error_text

        raise LineFitStopped(
            f"line-fit {proposal_id}: HTTP {status}: {api_error_text(body)}"
        )
    return dict(body)


def _follow_redraft(
    desk: Path,
    run: Any,
    state: Any,
    proposal: Mapping[str, Any],
    job_id: str,
    *,
    out: TextIO,
) -> int:
    """Follow the draft the server started again with the chosen lines, like ``step`` / ``author`` do."""

    from creation import episode_commands as ec
    from creation.harness.http_util import describe_job_error
    from creation.orchestrate import draft_unit
    from creation.production_config import load_production_config
    from creation.production_state import save_production

    episode = int(proposal.get("episode_ordinal") or 1)
    first_draft = proposal.get("source") == "plan_author"
    record = state.pending.get(draft_unit(episode)) if first_draft else None
    if record is not None:
        # `step` adopts this job (same brief, same key) instead of showing the old pause again.
        record["plan_job_id"] = job_id
        record.pop("paused", None)
        save_production(desk, state)
    before = run.spine(state.spine_id) if (not first_draft and state.spine_id) else None
    print(
        f"[line-fit] Following job {job_id} (the draft with the chosen lines).",
        file=sys.stderr,
    )
    terminal = run.poll_job(
        job_id,
        label="plan" if first_draft else f"author-ep{episode:02d}",
        video_route=False,
        deadline_seconds=load_production_config(desk).poll_plan_deadline_seconds,
    )
    if terminal.get("status") != "completed":
        again = fit_proposals(terminal)
        if again:
            if record is not None:
                record["paused"] = LINE_NEEDS_CREATOR_CHOICE
                save_production(desk, state)
            print(pause_text(again, desk=desk), file=out)
            return 0
        raise LineFitStopped(f"job {job_id} {describe_job_error(terminal)}")
    if first_draft:
        if record is not None and terminal.get("spine_id"):
            record["spine_id"] = terminal["spine_id"]
            save_production(desk, state)
        print(
            f"Job {job_id} completed. Next: fictora-produce step --desk {desk} (it picks this draft up; "
            "no new draft is sent).",
            file=out,
        )
        return 0
    spine = run.spine(state.spine_id or "")
    ec._save_desk_json(desk, f"author-ep{episode:02d}-terminal", terminal)
    ec._show_authored(
        desk,
        state.spine_id or "",
        before=before or {},
        spine=spine,
        terminal=terminal,
        episode=episode,
        steer=" (line-fit choice)",
        label="line-fit",
        narrator_heard_only=(),
        narrator_on_screen=(),
        ask=None,
        out=out,
    )
    return 0


def _applied(
    desk: Path, proposal: Mapping[str, Any], answer: Mapping[str, Any], *, out: TextIO
) -> None:
    from creation.orchestrate import save_spine_snapshot, sync_spine_lines

    spine = answer.get("spine")
    episode = int(proposal.get("episode_ordinal") or 1)
    print(f"Server: applied ({answer.get('proposal_id')}).", file=out)
    if isinstance(spine, Mapping):
        save_spine_snapshot(desk, episode, dict(spine))
        try:
            sync_spine_lines(desk, dict(spine), episode=episode)
        except (KeyError, ValueError, FileNotFoundError):
            pass
        print(
            f"(story saved; read the lines: fictora-produce line --desk {desk} --episode {episode})",
            file=out,
        )


def run_line_fit(
    desk: Path,
    *,
    proposal_id: str | None = None,
    choose: str | None = None,
    edit: str | None = None,
    out: TextIO | None = None,
) -> int:
    """Show the desk's line-fit proposals, or send the creator's choice on one. Spends nothing.

    Parameters
    ----------
    desk
        Series desk.
    proposal_id
        The proposal; with no id, the desk's open ones.
    choose
        ``use``, ``trim`` or ``edit``; ``None`` only shows.
    edit
        With ``choose == "edit"``: the creator's lines (:func:`read_edit_lines`).
    out
        Text stream.

    Returns
    -------
    int
        Exit code (0).

    Raises
    ------
    LineFitStopped
        Bad arguments, no proposal, or the server refused.
    """

    out = out or sys.stdout
    if choose is not None and choose not in CHOICES:
        raise LineFitStopped(f"--choose is one of {', '.join(CHOICES)}")
    if (edit is not None) != (choose == "edit"):
        raise LineFitStopped(
            "--edit @file goes with --choose edit (and --choose edit needs --edit)"
        )
    lines = read_edit_lines(edit) if edit is not None else []
    desk, state, run = _open(desk)
    try:
        if proposal_id:
            proposals = [_get_proposal(run, proposal_id)]
        else:
            proposals = open_proposals(run, state)
        if choose is None:
            if not proposals:
                print("No line is waiting on the creator's choice.", file=out)
                return 0
            for proposal in proposals:
                print(proposal_card(proposal, desk=desk), file=out)
                print("", file=out)
            return 0
        if len(proposals) != 1:
            raise LineFitStopped(
                "--choose needs one proposal: pass --proposal ID"
                + (
                    " (none open on this desk)"
                    if not proposals
                    else f" ({len(proposals)} are open)"
                )
            )
        proposal = proposals[0]
        pid = str(proposal["proposal_id"])
        body: dict[str, Any] = {
            "choice": choose,
            "source_sha256": proposal.get("source_sha256"),
        }
        if proposal.get("spine_version"):
            body["spine_version"] = proposal["spine_version"]
        if lines:
            body["lines"] = lines
        status, answer = run.post_optional(f"{PROPOSALS_ROUTE}/{pid}/choose", body)
        if _no_route(status, answer):
            raise LineFitStopped(
                "this server has no line-fit route yet (fictora-drama #962 is not deployed); nothing was sent"
            )
        if status >= 400 or not isinstance(answer, Mapping):
            from creation.harness.http_util import api_error_text

            raise LineFitStopped(f"Refused (HTTP {status}): {api_error_text(answer)}")
        result = str(answer.get("status"))
        if result == "applied":
            _applied(desk, proposal, answer, out=out)
            return 0
        if result == "needs_choice" and isinstance(
            answer.get("next_proposal"), Mapping
        ):
            print("Server: needs_choice (the edited lines still do not fit).", file=out)
            print(proposal_card(answer["next_proposal"], desk=desk), file=out)
            return 0
        if result == "redrafting" and answer.get("job_id"):
            print(f"Server: redrafting (job {answer['job_id']}).", file=out)
            return _follow_redraft(
                desk, run, state, proposal, str(answer["job_id"]), out=out
            )
        raise LineFitStopped(
            f"the server answered {result!r}, which this kit does not handle; nothing else was sent "
            f"({json.dumps(dict(answer), default=str)[:400]})"
        )
    finally:
        run.client.close()


def add_line_fit_parser(sub: Any) -> None:
    """Register ``fictora-produce line-fit``."""

    parser = sub.add_parser(
        "line-fit",
        help="A line that cannot fit its take: show the server's fitted version and choices, or send the "
        "creator's choice. Spends nothing (a redraft is the draft already started).",
    )
    parser.add_argument("--desk", type=Path, required=True)
    parser.add_argument(
        "--proposal",
        default=None,
        metavar="ID",
        help="The proposal; default: the desk's open ones.",
    )
    parser.add_argument(
        "--choose", choices=CHOICES, default=None, help="The creator's choice."
    )
    parser.add_argument(
        "--edit",
        default=None,
        metavar="@FILE",
        help="With --choose edit: the creator's lines, one `slot: line` each (or JSON [{slot, text}]).",
    )


def dispatch_line_fit(args: argparse.Namespace) -> int:
    """Run ``line-fit`` from parsed arguments; a stop prints and exits 2."""

    try:
        return run_line_fit(
            args.desk, proposal_id=args.proposal, choose=args.choose, edit=args.edit
        )
    except (LineFitStopped, RuntimeError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 2


__all__ = [
    "LINE_NEEDS_CREATOR_CHOICE",
    "NEEDS_CREATOR_CHOICE_CAUSE",
    "LineFitStopped",
    "add_line_fit_parser",
    "command",
    "dispatch_line_fit",
    "failure_cause",
    "fit_proposals",
    "open_proposals",
    "pause_text",
    "proposal_card",
    "proposals_from_exit",
    "read_edit_lines",
    "run_line_fit",
]
