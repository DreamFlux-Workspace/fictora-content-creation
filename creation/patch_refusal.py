"""Explain the server's ``invalid_patch`` refusal of a beat or frame edit.

From fictora-drama #498 the server names the field and the rule itself
(``details.validation_errors`` ``{loc, msg, type}`` and a message naming the
fields) and names the frame-cast rules (``frame_cast_mismatch``,
``frame_cast_needs_staging``, ``off_screen_cast_on_frame``,
``voice_only_cast_on_screen``, ``details.unknown_frame_cast_ids``); the kit
prints those as they are. What follows is the fallback for an older deploy.

``PATCH /v1/spines/{id}`` and ``POST …/cascade/preview`` answer a request the
contract refuses with a fixed message and no details ("The story spine patch
is invalid.", "The cascade preview edit is invalid."): the server keeps the
historical 400 copy for those routes and drops pydantic's field errors. The
kit knows what it sent, so it says which fields the edit changed, checks each
against the deploy's ``/openapi.json`` (an enum field set to a value the
schema does not list is named with the values it takes), and names the fields
that are known to be strict (``cell_role``, ``reaction_kind``,
``reaction_cast_id``, the lists that need one item).
"""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

#: The refusal code both routes answer for a request the contract refuses.
INVALID_PATCH = "invalid_patch"
#: OpenAPI schema (suffix) of what ``--set`` edits: a frame's brief, a beat's motion direction.
FRAME_BRIEF_SCHEMA = "DramaFrameVisualBrief"
BEAT_DIRECTION_SCHEMA = "DramaMotionDirection"

#: Values a frame's ``cell_role`` takes when the schema cannot be read (fictora-drama ``DramaFrameCellRole``).
CELL_ROLES = ("anchor", "reaction", "action", "insert", "cover")

#: How a speaker plays a beat's line (``DramaMotionDirection.delivery``) when the deploy's schema cannot be
#: read (fictora-drama ``DramaLineDelivery``). ``null`` is a plain delivery.
LINE_DELIVERIES = (
    "laughing",
    "excited",
    "whispered",
    "shouted",
    "through_tears",
    "deadpan",
    "trailing_off",
    "breathless",
    "cold",
)

#: Fields the server holds to a rule the edit can break, and the rule in the operator's words.
STRICT_FIELDS: dict[str, str] = {
    "cell_role": "one of " + ", ".join(CELL_ROLES),
    "reaction_kind": (
        "a kind from the deploy's expression library (`GET /v1/capabilities`), spelled as the kind, "
        "not the label; on a beat, use `--expression` instead of --set"
    ),
    "reaction_cast_id": (
        "needs a reaction_kind on the same frame, and must be a cast_id in that frame's subject_blocking"
    ),
    "subject_blocking": (
        "each entry needs cast_id, frame_position, pose, gaze and interaction (not blank); a cast_id once"
    ),
    "story_objects": "a list with at least one item, none blank",
    "palette": "a list with at least one item, none blank",
    "continuity_invariants": "a list with at least one item, none blank",
    "chain": "a list of one to six steps, none blank",
    "camera_move": "a camera move token the deploy lists (see its /openapi.json)",
    "camera": "an object {move, intensity, end_framing} on a beat's motion direction",
    "delivery": "one of "
    + ", ".join(LINE_DELIVERIES)
    + ", or null for a plain delivery (a beat field)",
    "duration_seconds": "fixed by the server; it cannot be changed",
    "single_take": "fixed by the server; it cannot be changed",
}

#: Named frame-cast refusals (fictora-drama #498) and the edit that fixes each.
FRAME_CAST_FIXES: dict[str, str] = {
    "frame_cast_needs_staging": (
        "someone added to the shot needs staging: send subject_blocking with an entry for them "
        "(cast_id, frame_position, pose, gaze, interaction), e.g. --set 'subject_blocking=[{...}, {...}]'; "
        "cast_refs follows it"
    ),
    "frame_cast_mismatch": (
        "cast_refs and subject_blocking name different people: send only one of them (the other follows)"
    ),
    "off_screen_cast_on_frame": (
        "that character is only heard in this take: bring their line back on screen first "
        "(`line --line ID --on-screen`; if their beat forbids 'visible NAME', take that out of the beat's "
        "forbidden elements too, with `edit --beat`), or leave them out of the shot"
    ),
    "voice_only_cast_on_screen": (
        "a voice-only character is never drawn: leave them out of cast_refs and subject_blocking"
    ),
}
#: The spine rule an older server (before fictora-drama #498) breaks when a frame's cast changes one way.
OLD_CAST_RULE = "subject blocking must match cast_refs"
OLDER_CAST_FIX = (
    "this deploy is older than fictora-drama #498: change who is in the shot through subject_blocking "
    "(the kit sends cast_refs to match); cast_refs alone cannot remove someone there"
)


def changed_fields(changed: Sequence[str]) -> list[str]:
    """Field paths from the edit's change lines (``  visual_brief.cell_role: a  ->  b``).

    Parameters
    ----------
    changed
        Change lines as :func:`creation.episode_commands.build_patch` prints them.

    Returns
    -------
    list[str]
        ``cell_role``, ``subject_blocking.0.pose``, ... (the ``visual_brief.`` /
        ``motion_direction.`` prefix dropped), in order, once each.
    """

    found: list[str] = []
    for line in changed:
        match = re.match(r"\s*([A-Za-z0-9_.]+):\s", line)
        if not match:
            continue
        path = re.sub(r"^(visual_brief|motion_direction)\.", "", match.group(1))
        if path not in found:
            found.append(path)
    return found


def _resolve(schemas: Mapping[str, Any], node: Any) -> Any:
    """Follow ``$ref`` and a nullable ``anyOf`` down to the one schema that is not ``null``."""

    for _ in range(8):
        if not isinstance(node, Mapping):
            return None
        ref = node.get("$ref")
        if isinstance(ref, str):
            node = schemas.get(ref.rsplit("/", 1)[-1])
            continue
        options = [
            option
            for option in node.get("anyOf") or node.get("oneOf") or []
            if isinstance(option, Mapping) and option.get("type") != "null"
        ]
        if len(options) == 1 and "properties" not in node and "enum" not in node:
            node = options[0]
            continue
        return node
    return None


def _schema(openapi: Any, name: str) -> tuple[Mapping[str, Any], Any]:
    if not isinstance(openapi, Mapping):
        return {}, None
    schemas = (openapi.get("components") or {}).get("schemas") or {}
    if not isinstance(schemas, Mapping):
        return {}, None
    for key, value in schemas.items():
        if str(key) == name or str(key).endswith(name):
            return schemas, value
    return schemas, None


def allowed_values(openapi: Any, root: str, path: str) -> list[str] | None:
    """The enum an edited field takes, as the deploy's ``/openapi.json`` lists it.

    Parameters
    ----------
    openapi
        ``/openapi.json`` as read from the deploy (anything else reads as unknown).
    root
        :data:`FRAME_BRIEF_SCHEMA` or :data:`BEAT_DIRECTION_SCHEMA`.
    path
        Dotted field path (a number steps into a list's items).

    Returns
    -------
    list[str] | None
        The values, or ``None`` when the field is not an enum or the schema does not say.
    """

    schemas, node = _schema(openapi, root)
    node = _resolve(schemas, node)
    for part in path.split("."):
        if node is None:
            return None
        if part.isdigit():
            node = _resolve(schemas, node.get("items"))
            continue
        node = _resolve(schemas, (node.get("properties") or {}).get(part))
    if not isinstance(node, Mapping):
        return None
    values = node.get("enum")
    if isinstance(values, list) and values:
        return [str(value) for value in values]
    return None


def delivery_values(openapi: Any) -> tuple[list[str], str]:
    """The ``delivery`` values a beat takes: the deploy's own list when its schema says, else the kit's.

    Parameters
    ----------
    openapi
        ``/openapi.json`` as read from the deploy (anything else reads as unknown).

    Returns
    -------
    tuple[list[str], str]
        The values, and where they came from (said in the refusal).
    """

    read = allowed_values(openapi, BEAT_DIRECTION_SCHEMA, "delivery")
    if read:
        return read, "this deploy's /openapi.json"
    return list(LINE_DELIVERIES), "the kit's list"


def delivery_refusal(value: Any, values: Sequence[str], source: str) -> str | None:
    """Say why a ``delivery`` value would be refused, or ``None`` when the server takes it.

    Parameters
    ----------
    value
        What ``--set delivery=…`` asked for (``None`` clears it and is always taken).
    values
        :func:`delivery_values`.
    source
        Where ``values`` came from.

    Returns
    -------
    str | None
        The refusal, naming every value and the nearest one.
    """

    if value is None or str(value) in values:
        return None
    near = difflib.get_close_matches(
        str(value).strip().lower().replace(" ", "_"), list(values), n=1, cutoff=0.5
    )
    hint = f" (did you mean {near[0]!r}?)" if near else ""
    return (
        f"delivery {value!r} is not one the server takes{hint}. It takes: {', '.join(values)}; "
        f"or null for a plain delivery (from {source}). Nothing was sent."
    )


def _value_at(target: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(target, list) and part.isdigit() and int(part) < len(target):
            target = target[int(part)]
        elif isinstance(target, Mapping):
            target = target.get(part)
        else:
            return None
    return target


def explain_invalid_patch(
    message: str,
    patch: Mapping[str, Any],
    changed: Sequence[str],
    *,
    openapi: Any = None,
) -> str | None:
    """Say which fields a refused beat or frame edit carried, and which of them are likely at fault.

    Parameters
    ----------
    message
        The kit's refusal text (``HTTP 400: invalid_patch: The cascade preview edit is invalid.``).
    patch
        What was sent (``{"frames": [...]}`` or ``{"beats": [...]}``).
    changed
        The edit's change lines.
    openapi
        The deploy's ``/openapi.json`` when it could be read.

    Returns
    -------
    str | None
        The explanation to print under the refusal: the server's own rows when it named them
        (fictora-drama #498), a fix for a named frame-cast refusal, else (an older server's bare
        copy) the fields sent and the strict ones. ``None`` when it is not a beat or frame refusal
        the kit explains.
    """

    if patch.get("frames"):
        entry, root, holder = patch["frames"][0], FRAME_BRIEF_SCHEMA, "visual_brief"
    elif patch.get("beats"):
        entry, root, holder = (
            patch["beats"][0],
            BEAT_DIRECTION_SCHEMA,
            "motion_direction",
        )
    else:
        return None
    code = refusal_code(message)
    if code in FRAME_CAST_FIXES and patch.get("frames"):
        return f"  fix: {FRAME_CAST_FIXES[code]}"
    if code != INVALID_PATCH:
        return None
    body = entry.get(holder) if isinstance(entry, Mapping) else None
    fields = changed_fields(changed)
    sent = [key for key in (entry or {}) if key not in ("frame_id", "beat_id")]
    carried = (
        f"    {', '.join(sent) or '(nothing)'} on {entry.get('frame_id') or entry.get('beat_id')}"
        + (
            f" (the whole {holder} is sent; this edit changed: {', '.join(fields)})"
            if fields
            else ""
        )
    )
    named = server_named_rules(message)
    if named is not None:
        # fictora-drama #498 on: the server names the field and the rule; say it, never guess.
        lines = ["  the server named what is wrong:", *(f"    {row}" for row in named)]
        if any(OLD_CAST_RULE in row for row in named) or OLD_CAST_RULE in message:
            lines.append(f"  fix: {OLDER_CAST_FIX}")
        return "\n".join([*lines, "  what the request carried:", carried])
    lines = [
        "  the server gave no field detail for this refusal (a deploy older than fictora-drama #498). "
        "What the request carried:",
        carried,
    ]
    suspects: list[str] = []
    for path in fields:
        leaf = path.split(".")[-1]
        top = path.split(".")[0]
        value = _value_at(body, path)
        allowed = allowed_values(openapi, root, path)
        if allowed is None and leaf == "cell_role":
            allowed = list(CELL_ROLES)
        if allowed is None and leaf == "delivery":
            allowed = list(LINE_DELIVERIES)
        if allowed is not None and value is not None and str(value) not in allowed:
            suspects.append(
                f"    {path} = {value!r} is not a value the server takes: {', '.join(allowed)}"
            )
            continue
        rule = STRICT_FIELDS.get(leaf) or STRICT_FIELDS.get(top)
        if rule:
            suspects.append(f"    {path}: {rule}")
        elif value in ("", None) or (isinstance(value, str) and not value.strip()):
            suspects.append(
                f"    {path}: the server refuses a blank or null text field"
            )
    if suspects:
        lines.append("  likely at fault (fields the server holds to a rule):")
        lines += suspects
    else:
        lines.append(
            "  none of the changed fields is one the kit knows to be strict: check the value's type "
            "(a list field wants a JSON list, e.g. --set 'story_objects=[\"a cup\"]') and that no text is blank"
        )
    return "\n".join(lines)


def refusal_code(message: str) -> str:
    """The error code in the kit's ``HTTP 400: code: message (details {…})`` text, or ``""``."""

    found = re.search(r"HTTP \d+[^:]*: ([a-z][a-z0-9_]*): ", message)
    return found.group(1) if found else ""


def _details(message: str) -> dict[str, Any] | None:
    raw = re.search(r"\(details (\{.*\})\)", message)
    if not raw:
        return None
    try:
        parsed = json.loads(raw.group(1))
    except ValueError:
        return {"unreadable": True}
    return parsed if isinstance(parsed, dict) else None


def _row(row: Any) -> str:
    if isinstance(row, Mapping):
        loc = row.get("loc")
        where = (
            ".".join(str(part) for part in loc if part not in ("body", "query", "path"))
            if isinstance(loc, list)
            else str(loc or "")
        )
        text = str(row.get("msg") or row.get("message") or row)
        return f"{where}: {text}" if where else text
    return str(row)


def server_named_rules(message: str) -> list[str] | None:
    """What the server said was wrong with an ``invalid_patch``, or ``None`` when it said nothing.

    fictora-drama #498 fills ``details.validation_errors`` (``{loc, msg, type}``)
    and names the fields in the message; the spine contract has always listed
    ``details.violations``; ``details.unknown_frame_cast_ids`` names cast that
    is not in the story.

    Parameters
    ----------
    message
        The kit's refusal text.

    Returns
    -------
    list[str] | None
        One line per rule the server named; ``None`` for the bare fixed copy.
    """

    details = _details(message) or {}
    rows: list[str] = []
    for key in ("validation_errors", "violations"):
        rows += [_row(row) for row in details.get(key) or []]
    unknown = details.get("unknown_frame_cast_ids")
    if unknown:
        rows.append(f"not in the story's cast: {unknown}")
    if details.get("unreadable"):
        rows.append("(the details above were cut short; they are the server's words)")
    if not rows:
        after = re.search(r"is invalid: (.+?)(?: \(details |$)", message)
        if after:
            rows.append(after.group(1).strip())
    return rows or None
