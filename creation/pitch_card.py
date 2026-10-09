"""The episode's pitch card: the idea the producer says yes to before any paid drawing (founder, 7 Oct 2026).

Across four operator series about half of all spend (~$13.50 of ~$44) went on
re-renders after the producer rejected the IDEA or the SCRIPT late, after
boards or takes: "the romance feels forced", "the plot is all over the place",
restraint filmed as blank faces ("she does not have any expression"), invented
prop business ("don't make up weird things this is bl"), broken world rules
("sota cannot feel the presence of a ghost"), the premise device in every
episode ("what do u keep bringing rain in?"). The producer approved lines,
never the idea. The pitch card is that idea, written down and approved first.

- ``pitch --desk D --episode N --file pitch.md|.json`` (free) checks the card
  and stores it as ``epNN/pitch-vN.json`` (versioned, never overwritten), then
  prints it. A card missing a field, or breaking the PG rule, is refused with
  a plain list and nothing is stored. ``pitch --desk D --episode N`` prints the
  current one.
- ``approve --desk D --gate pitch [--episode N]`` records the human's yes on the
  newest card (desk only: ``epNN/pitch-approvals.json``). A card changed after
  its yes (a new version, or the file edited by hand) needs a new yes.
- On desks created on or after 6 Oct 2026 (:func:`creation.rules_epoch.is_legacy`)
  nothing paid runs for an episode whose newest card lacks its yes: ``step``
  (plates, boards, filming), ``redraw-board``, ``redraw-plate`` and
  ``film --confirm-spend`` all stop first (:func:`pitch_gate_refusal`). Older
  desks are never held: before each of those they get a reminder and go ahead,
  on every episode (``pitch_card`` is in :data:`creation.rules_epoch.CONTINUING_FIXES`
  since 9 Oct 2026, founder; reminder-only since 8 Oct 2026, #185).
- The script gate prints the approved card, its world rules ("check every line
  and beat against these") and free warnings that compare the script with it
  (:func:`script_pitch_lines`), on every desk.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from creation.ops.folder import next_versioned_path
from creation.patch_refusal import LINE_DELIVERIES
from creation.rules_epoch import continuing_fix, is_legacy

#: Stored as ``epNN/pitch-vN.json``.
PITCH_STEM = "pitch"
#: The human's yes on each version: ``epNN/pitch-approvals.json``.
APPROVALS_NAME = "pitch-approvals.json"
SCHEMA = "fictora.kit.pitch.v1"

#: Every field a card needs, in the order it prints.
FIELDS = (
    "throughline",
    "charged_situation",
    "world_rules",
    "premise_device",
    "emotional_beats",
    "intimacy",
    "shouted_lines",
    "hook_line_on_screen",
    "hook",
    "ending",
    "voices",
)
#: What each field is, in the words the refusal uses.
FIELD_WORDS: Mapping[str, str] = {
    "throughline": "throughline: the episode in one sentence (about 200 characters at most)",
    "charged_situation": "charged_situation: the ONE situation the episode turns on",
    "world_rules": "world_rules: a list of the show's rules (carried from the last approved pitch when left out)",
    "premise_device": "premise_device: {device: the show's premise device, a word or a list of words in each language "
    '("rain" or ["rain", "비", "雨"]), used: yes/no, why: why or why not}',
    "emotional_beats": "emotional_beats: a list of {beat: what happens, expression: the named face, delivery: how the line is played}",
    "intimacy": "intimacy: `none`, or a PG plan carried through the aftermath",
    "shouted_lines": "shouted_lines: yes or no (ask the producer)",
    "hook_line_on_screen": "hook_line_on_screen: yes or no (ask the producer whether a hook line is burned on screen)",
    "hook": "hook: the frame-0 image in words",
    "ending": "ending: the last beat or cliffhanger",
    "voices": "voices: {mode: locked or model, characters: {NAME: voice}}",
}
#: A throughline is one sentence, about this long at most.
THROUGHLINE_MAX_CHARS = 200
#: The deliveries a beat takes (the server's ``DramaLineDelivery``), plus ``plain`` for a plain delivery.
DELIVERIES: tuple[str, ...] = (*LINE_DELIVERIES, "plain")

#: The PG rule, as the skill writes it.
PG_RULE = (
    "PG rule (Fictora policy, never worked around): no kissing, embracing or face contact on screen, "
    "for anyone. Carry romance through the aftermath, not the contact: the pair springing apart, "
    "lipstick smeared on his mouth, her thumb on her own lip, hands meeting on an umbrella handle."
)
_PG_BANNED = re.compile(
    r"\b(kiss\w*|embrac\w*|make[s]? out|making out|made out|touching lips|lips? (?:touch\w*|meet\w*|lock\w*)"
    r"|lip[- ]lock\w*|face contact)\b",
    re.IGNORECASE,
)
#: Fields that are filmed: the PG rule reads all of them.
_FILMED_FIELDS = ("intimacy", "hook", "ending", "charged_situation")

#: Restrained direction films as a blank face (The Gallery Heiress, L-20261006-13, $1.80).
RESTRAINT = re.compile(
    r"\b(freez(?:e|es|ing)|froze|frozen|blank(?:ly)?|stunned|cold smile|smile goes cold|smile_goes_cold"
    r"|deadpan|expressionless|no expression|poker[- ]?face\w*|stone[- ]faced|stony|impassive|emotionless"
    r"|unreadable|numb(?:ly)?|refuses? to (?:break|cry|react)|stoic\w*|vacant|empty stare|neutral face)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?。！？]+(?=\s+\S|\s*$)")
_YES = frozenset({"yes", "y", "true", "1", "on"})
_NO = frozenset({"no", "n", "false", "0", "off"})


class PitchRefused(ValueError):
    """A pitch card the kit will not store; the message lists every problem. Nothing was stored."""


@dataclass(frozen=True)
class StoredPitch:
    """One stored version of an episode's pitch card."""

    episode: int
    version: int
    path: Path
    pitch: dict[str, Any]

    @property
    def digest(self) -> str:
        return pitch_digest(self.pitch)


# --- reading a card -------------------------------------------------------------------------------


def _field_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def _bullets(body: str) -> list[str]:
    items = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        items.append(re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line))
    return items


def _key_values(body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in _bullets(body):
        key, sep, value = item.partition(":")
        if sep:
            found[_field_key(key)] = value.strip()
    return found


def _md_beat(item: str) -> dict[str, str]:
    parts = [part.strip() for part in item.split("|")]
    beat: dict[str, str] = {}
    for index, part in enumerate(parts):
        key, sep, value = part.partition(":")
        name = _field_key(key) if sep else ""
        if name in ("beat", "expression", "delivery"):
            beat[name] = value.strip()
        elif index == 0:
            beat["beat"] = part
    return beat


def parse_pitch_markdown(text: str) -> dict[str, Any]:
    """Read a pitch card written as Markdown: one ``## field`` heading per field.

    ``world_rules`` and ``emotional_beats`` are bullet lists; an emotional beat is
    ``- what happens | expression: named face | delivery: through_tears``.
    ``premise_device`` and ``voices`` are ``key: value`` lines (``voices`` takes
    ``mode: locked`` and one ``NAME: voice`` line per speaking character).
    Yes/no fields take ``yes`` or ``no``.

    Parameters
    ----------
    text
        The Markdown.

    Returns
    -------
    dict[str, Any]
        The raw card (checked by :func:`validate_pitch`).
    """

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = re.match(r"^#{2,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            current = _field_key(heading.group(1))
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    raw: dict[str, Any] = {}
    for key, lines in sections.items():
        body = "\n".join(lines).strip()
        if key == "world_rules":
            raw[key] = _bullets(body)
        elif key == "emotional_beats":
            raw[key] = [_md_beat(item) for item in _bullets(body)]
        elif key == "premise_device":
            values = _key_values(body)
            raw[key] = {
                name: values[name]
                for name in ("device", "used", "why")
                if name in values
            }
        elif key == "voices":
            values = _key_values(body)
            mode = values.pop("mode", "")
            names = {}
            for item in _bullets(body):
                name, sep, voice = item.partition(":")
                if sep and _field_key(name) != "mode":
                    names[name.strip()] = voice.strip()
            raw[key] = {"mode": mode, "characters": names}
        else:
            raw[key] = body
    return raw


def read_pitch_file(path: Path) -> dict[str, Any]:
    """Read a card from ``.json`` or Markdown (anything else is read as Markdown)."""

    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{path}: no such pitch file")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PitchRefused(
                f"{path} is not valid JSON: {exc}. Nothing was stored."
            ) from exc
        if not isinstance(raw, dict):
            raise PitchRefused(
                f"{path}: the pitch must be a JSON object. Nothing was stored."
            )
        return raw
    return parse_pitch_markdown(text)


def device_words(value: Any) -> list[str]:
    """The premise device as a list of words, one or more per language.

    A list is taken as it is; a string is split on commas, semicolons and
    slashes (``"rain, 비, 雨"``), so a single word still works.
    """

    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    elif isinstance(value, str):
        items = re.split(r"[,;/、，]", value)
    else:
        return []
    return [item.strip() for item in items if item and item.strip()]


def device_label(words: Any) -> str:
    """The device words as printed: ``rain / 비 / 雨``."""

    return " / ".join(device_words(words))


def _yes_no(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower().rstrip(".")
    if text in _YES:
        return True
    if text in _NO:
        return False
    return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def sentence_count(text: str) -> int:
    """How many sentences ``text`` holds (a closing stop counts once)."""

    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, len(_SENTENCE_END.findall(stripped)))


def pg_words(text: str) -> list[str]:
    """Words in ``text`` the PG rule forbids on screen (kissing, embracing, face contact)."""

    return sorted({match.group(0).lower() for match in _PG_BANNED.finditer(text or "")})


def restraint_words(text: str) -> list[str]:
    """Restraint words in ``text`` (freeze, blank, stunned, cold smile, deadpan …)."""

    return sorted({match.group(0).lower() for match in RESTRAINT.finditer(text or "")})


def validate_pitch(
    raw: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
    memory_rules: Sequence[str] = (),
) -> tuple[dict[str, Any], list[str]]:
    """Check a card's fields and shapes; fill what carries from the last approved card.

    ``world_rules`` and the premise ``device`` left out are carried from
    ``previous`` (the last approved pitch of an earlier episode); world rules
    are also taken from the series memory's canon when nothing else has them.

    Parameters
    ----------
    raw
        The card as read (:func:`read_pitch_file`).
    previous
        The previous episode's approved card, or ``None``.
    memory_rules
        Canon lines from the series memory saved on the desk.

    Returns
    -------
    tuple[dict[str, Any], list[str]]
        The card to store, and notes on what was carried (printed so the
        producer re-confirms).

    Raises
    ------
    PitchRefused
        Something is missing or the wrong shape, or a filmed field breaks the
        PG rule. The message lists every problem; nothing is stored.
    """

    problems: list[str] = []
    notes: list[str] = []
    pitch: dict[str, Any] = {}
    previous = previous or {}

    throughline = _text(raw.get("throughline"))
    if not throughline:
        problems.append(FIELD_WORDS["throughline"])
    elif sentence_count(throughline) > 1:
        problems.append(
            f"throughline is {sentence_count(throughline)} sentences: write ONE sentence (one throughline per episode)"
        )
    elif len(throughline) > THROUGHLINE_MAX_CHARS:
        problems.append(
            f"throughline is {len(throughline)} characters: keep it to one sentence of about "
            f"{THROUGHLINE_MAX_CHARS} at most"
        )
    pitch["throughline"] = throughline

    charged = raw.get("charged_situation")
    if isinstance(charged, list):
        problems.append(
            "charged_situation is a list: name the ONE situation the episode turns on"
        )
        charged = ""
    pitch["charged_situation"] = _text(charged)
    if not pitch["charged_situation"] and not isinstance(
        raw.get("charged_situation"), list
    ):
        problems.append(FIELD_WORDS["charged_situation"])

    rules = raw.get("world_rules")
    if rules is None or rules == "" or rules == []:
        carried = [
            str(rule) for rule in previous.get("world_rules") or [] if str(rule).strip()
        ]
        source = "the previous episode's approved pitch"
        if not carried:
            carried = [str(rule) for rule in memory_rules if str(rule).strip()]
            source = "the series memory (canon)"
        if carried:
            rules = carried
            notes.append(
                f"world_rules carried from {source}: re-confirm them with the producer."
            )
    if isinstance(rules, str):
        rules = _bullets(rules)
    if not isinstance(rules, list) or not [r for r in rules if str(r).strip()]:
        problems.append(FIELD_WORDS["world_rules"] + "; episode 1 writes them down")
        rules = []
    pitch["world_rules"] = [str(rule).strip() for rule in rules if str(rule).strip()]
    dropped = [
        rule
        for rule in previous.get("world_rules") or []
        if rule not in pitch["world_rules"]
    ]
    if dropped and raw.get("world_rules"):
        notes.append(
            "world_rules no longer lists "
            + "; ".join(f'"{rule}"' for rule in dropped)
            + " from the previous episode's approved pitch: confirm with the producer that it no longer holds."
        )

    device = raw.get("premise_device")
    if not isinstance(device, Mapping):
        problems.append(FIELD_WORDS["premise_device"])
        device = {}
    own = device_words(device.get("device"))
    word = own or device_words((previous.get("premise_device") or {}).get("device"))
    if word and not own and device:
        notes.append(
            f'premise device "{device_label(word)}" carried from the previous episode\'s approved pitch.'
        )
    used = _yes_no(device.get("used"))
    why = _text(device.get("why"))
    if device:
        if not word:
            problems.append(
                "premise_device.device: the show's premise device, a word or a list of words in each language "
                '("rain", or ["rain", "비", "雨"])'
            )
        if used is None:
            problems.append("premise_device.used: yes or no")
        if not why:
            problems.append(
                "premise_device.why: why it is used, or why not, this episode"
            )
    pitch["premise_device"] = {"device": word, "used": bool(used), "why": why}

    beats = raw.get("emotional_beats")
    shaped: list[dict[str, str]] = []
    if not isinstance(beats, list) or not beats:
        problems.append(FIELD_WORDS["emotional_beats"])
        beats = []
    for index, beat in enumerate(beats, start=1):
        if not isinstance(beat, Mapping):
            problems.append(
                f"emotional_beats {index}: write {{beat, expression, delivery}}"
            )
            continue
        entry = {
            name: _text(beat.get(name)) for name in ("beat", "expression", "delivery")
        }
        for name in ("beat", "expression", "delivery"):
            if not entry[name]:
                problems.append(
                    f"emotional_beats {index}: {name} is missing"
                    + (
                        " (a named face: tears, rage, a trembling smile)"
                        if name == "expression"
                        else ""
                    )
                    + (
                        f" (one of {', '.join(DELIVERIES)})"
                        if name == "delivery"
                        else ""
                    )
                )
        delivery = entry["delivery"].lower().replace(" ", "_")
        if entry["delivery"] and delivery not in DELIVERIES:
            problems.append(
                f"emotional_beats {index}: delivery {entry['delivery']!r} is not one the server takes "
                f"(one of {', '.join(DELIVERIES)})"
            )
        entry["delivery"] = delivery if entry["delivery"] else ""
        shaped.append(entry)
    pitch["emotional_beats"] = shaped

    intimacy = raw.get("intimacy")
    pitch["intimacy"] = _text(intimacy)
    if not pitch["intimacy"]:
        problems.append(FIELD_WORDS["intimacy"])

    for name in ("shouted_lines", "hook_line_on_screen"):
        value = _yes_no(raw.get(name))
        if value is None:
            problems.append(FIELD_WORDS[name])
        pitch[name] = bool(value)

    for name in ("hook", "ending"):
        pitch[name] = _text(raw.get(name))
        if not pitch[name]:
            problems.append(FIELD_WORDS[name])

    voices = raw.get("voices")
    if voices is not None and not isinstance(voices, Mapping):
        problems.append(FIELD_WORDS["voices"])
        voices = None
    if isinstance(voices, Mapping):
        characters = voices.get("characters")
        pitch["voices"] = {
            "mode": _text(voices.get("mode")),
            "characters": {
                str(name): str(voice)
                for name, voice in (
                    characters.items() if isinstance(characters, Mapping) else []
                )
            },
        }
    else:
        pitch["voices"] = {"mode": "", "characters": {}}

    banned: list[str] = []
    for name in _FILMED_FIELDS:
        found = pg_words(str(pitch.get(name) or ""))
        if found:
            banned.append(f"{name} ({', '.join(found)})")
    for index, beat in enumerate(shaped, start=1):
        found = pg_words(beat.get("beat", "") + " " + beat.get("expression", ""))
        if found:
            banned.append(f"emotional_beats {index} ({', '.join(found)})")
    if banned:
        problems.append("breaks the PG rule: " + "; ".join(banned) + ". " + PG_RULE)

    if problems:
        raise PitchRefused(
            "Pitch not stored. Nothing was saved. Fix these, then run `pitch` again:\n"
            + "\n".join(f"- {problem}" for problem in problems)
        )
    return pitch, notes


def pitch_warnings(
    pitch: Mapping[str, Any], previous: Mapping[str, Any] | None
) -> list[str]:
    """Free warnings on a card: restraint words in an expression, the premise device two episodes running."""

    lines: list[str] = []
    for index, beat in enumerate(pitch.get("emotional_beats") or [], start=1):
        found = restraint_words(str(beat.get("expression") or ""))
        if found:
            lines.append(
                f'!! emotional beat {index}: "{beat.get("expression")}" is restrained ({", ".join(found)}). '
                'Restraint films as a blank face (The Gallery Heiress: "she does not have any expression"). '
                "Name a big, visible expression: tears, rage, a trembling smile."
            )
    device = pitch.get("premise_device") or {}
    before = (previous or {}).get("premise_device") or {}
    if device.get("used") and before.get("used"):
        lines.append(
            f'!! the premise device "{device_label(device.get("device"))}" was used in the previous episode too. '
            'Use it sparingly and find this episode\'s own turn (Noodle24: "what do u keep bringing rain in?").'
        )
    return lines


# --- the desk's cards -----------------------------------------------------------------------------


def pitch_digest(pitch: Mapping[str, Any]) -> str:
    """The card's fingerprint: an edit after the yes changes it."""

    text = json.dumps(pitch, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ep_dir(desk: Path, episode: int) -> Path:
    return Path(desk).expanduser().resolve() / f"ep{episode:02d}"


_VERSION = re.compile(rf"^{PITCH_STEM}-v(\d+)\.json$")


def stored_pitches(desk: Path, episode: int) -> list[StoredPitch]:
    """Every stored version of the episode's card, oldest first (unreadable files are skipped)."""

    folder = _ep_dir(desk, episode)
    found: list[StoredPitch] = []
    if not folder.is_dir():
        return found
    for path in folder.iterdir():
        match = _VERSION.match(path.name)
        if not match:
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pitch = record.get("pitch") if isinstance(record, dict) else None
        if isinstance(pitch, dict):
            found.append(StoredPitch(episode, int(match.group(1)), path, pitch))
    return sorted(found, key=lambda item: item.version)


def current_pitch(desk: Path, episode: int) -> StoredPitch | None:
    """The newest stored card for the episode, or ``None``."""

    stored = stored_pitches(desk, episode)
    return stored[-1] if stored else None


def _approvals(desk: Path, episode: int) -> list[dict[str, Any]]:
    path = _ep_dir(desk, episode) / APPROVALS_NAME
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = raw.get("approvals") if isinstance(raw, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def is_approved(desk: Path, stored: StoredPitch) -> bool:
    """True when this version, exactly as it reads now, has the human's yes."""

    return any(
        row.get("version") == stored.version and row.get("digest") == stored.digest
        for row in _approvals(desk, stored.episode)
    )


def approved_pitch(desk: Path, episode: int) -> StoredPitch | None:
    """The episode's newest card when it has the human's yes, else ``None``."""

    newest = current_pitch(desk, episode)
    return newest if newest is not None and is_approved(desk, newest) else None


def previous_approved_pitch(desk: Path, episode: int) -> StoredPitch | None:
    """The approved card of the nearest earlier episode, or ``None``."""

    for earlier in range(episode - 1, 0, -1):
        found = approved_pitch(desk, earlier)
        if found is not None:
            return found
    return None


def memory_canon(desk: Path) -> list[str]:
    """Canon lines from the newest series memory saved on the desk (``api/memory-vN.json``), if any."""

    folder = Path(desk).expanduser().resolve() / "api"
    if not folder.is_dir():
        return []

    def version(path: Path) -> int:
        match = re.search(r"-v(\d+)\.json$", path.name)
        return int(match.group(1)) if match else 0

    for path in sorted(folder.glob("memory-v*.json"), key=version, reverse=True):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        memory = raw.get("memory", raw) if isinstance(raw, dict) else {}
        canon = memory.get("canon") if isinstance(memory, dict) else None
        if isinstance(canon, list):
            lines = []
            for item in canon:
                text = item.get("text") if isinstance(item, Mapping) else item
                if str(text or "").strip():
                    lines.append(str(text).strip())
            return lines
    return []


def store_pitch(
    desk: Path, episode: int, raw: Mapping[str, Any]
) -> tuple[StoredPitch, list[str], bool]:
    """Check a card and store it as the episode's next version (never overwrites).

    Returns
    -------
    tuple[StoredPitch, list[str], bool]
        The stored card, the notes and warnings to print, and whether a new
        version was written (``False`` when it is the same as the newest).

    Raises
    ------
    PitchRefused
        See :func:`validate_pitch`; nothing is stored.
    """

    desk = Path(desk).expanduser().resolve()
    if not desk.is_dir():
        raise FileNotFoundError(f"{desk} is not a desk folder")
    if episode < 1:
        raise ValueError("--episode starts at 1")
    previous = previous_approved_pitch(desk, episode)
    pitch, notes = validate_pitch(
        raw,
        previous=previous.pitch if previous else None,
        memory_rules=memory_canon(desk),
    )
    notes += pitch_warnings(pitch, previous.pitch if previous else None)
    newest = current_pitch(desk, episode)
    if newest is not None and newest.digest == pitch_digest(pitch):
        return newest, notes, False
    folder = _ep_dir(desk, episode)
    folder.mkdir(parents=True, exist_ok=True)
    path = next_versioned_path(folder, PITCH_STEM, ".json")
    version = int(_VERSION.match(path.name).group(1))  # type: ignore[union-attr]
    record = {
        "schema": SCHEMA,
        "episode": episode,
        "version": version,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pitch": pitch,
    }
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return StoredPitch(episode, version, path, pitch), notes, True


def approve_pitch(desk: Path, episode: int) -> StoredPitch:
    """Record the human's yes on the episode's newest card (desk only, free).

    Raises
    ------
    RuntimeError
        The episode has no stored card.
    """

    desk = Path(desk).expanduser().resolve()
    newest = current_pitch(desk, episode)
    if newest is None:
        raise RuntimeError(
            f"Episode {episode} has no pitch card yet. Store one first: "
            f"`fictora-produce pitch --desk {desk} --episode {episode} --file pitch.md`."
        )
    if is_approved(desk, newest):
        return newest
    rows = _approvals(desk, episode)
    rows.append(
        {
            "version": newest.version,
            "digest": newest.digest,
            "file": newest.path.name,
            "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    path = _ep_dir(desk, episode) / APPROVALS_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(
            {"episode": episode, "approvals": rows}, ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return newest


# --- printing -------------------------------------------------------------------------------------


def desk_voice_lines(desk: Path) -> list[str]:
    """What the desk knows about the show's voices: the voice mode and each character's voice (no network)."""

    from creation.production_config import load_production_config

    desk = Path(desk).expanduser().resolve()
    lines: list[str] = []
    mode = load_production_config(desk).voice_mode
    lines.append(
        f"  voice mode on the desk: {mode}"
        if mode
        else "  voice mode on the desk: not set (the server decides; `voice-mode --desk D` reads it)"
    )
    spine_path = desk / "api" / "spine.json"
    try:
        spine = (
            json.loads(spine_path.read_text(encoding="utf-8"))
            if spine_path.is_file()
            else {}
        )
    except (OSError, json.JSONDecodeError):
        spine = {}
    for card in (spine.get("cast") or []) if isinstance(spine, dict) else []:
        if not isinstance(card, Mapping):
            continue
        brief = (
            card.get("voice_brief")
            if isinstance(card.get("voice_brief"), Mapping)
            else {}
        )
        voice = brief.get("provider_voice") or "no voice yet"
        lines.append(f"  {card.get('name') or card.get('cast_id')}: {voice}")
    return lines


def _desk_spine(desk: Path) -> dict[str, Any] | None:
    path = desk / "api" / "spine.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _desk_shot_lines(desk: Path, episode: int) -> list[str]:
    """The episode's shots by take from the desk's story, as the app's card shows them (none without a story)."""

    from creation.pitch_shots import pitch_takes, shot_lines
    from creation.spine_view import episode_id_for

    spine = _desk_spine(desk)
    if spine is None:
        return []
    try:
        episode_id = episode_id_for(spine, episode)
    except (KeyError, ValueError, LookupError):
        return []
    return shot_lines(pitch_takes(spine, str(episode_id)))


def pitch_text(
    desk: Path,
    stored: StoredPitch,
    *,
    with_voices: bool = True,
    with_hook_line: bool = True,
) -> str:
    """The card as the human reads it.

    With a hook line on screen the card prints its words and the writer's
    options (:func:`creation.hook_line.gate_hook_line_lines`), never only a
    yes/no (founder, 7 Oct 2026, L-20261005-11); ``with_hook_line=False`` leaves
    them to the caller (the script gate prints them itself).
    """

    pitch = stored.pitch
    status = "approved" if is_approved(desk, stored) else "NOT approved yet"
    out = [
        f"Pitch card, episode {stored.episode} (v{stored.version}, {status}) — {stored.path.name}",
        f"  Throughline: {pitch.get('throughline')}",
        f"  Charged situation: {pitch.get('charged_situation')}",
        f"  Hook (frame 0): {pitch.get('hook')}",
        f"  Ending: {pitch.get('ending')}",
        "  Emotional beats:",
    ]
    for index, beat in enumerate(pitch.get("emotional_beats") or [], start=1):
        out.append(
            f"    {index}. {beat.get('beat')} — face: {beat.get('expression')}; delivery: {beat.get('delivery')}"
        )
    device = pitch.get("premise_device") or {}
    out.append(
        f"  Premise device: {device_label(device.get('device'))} — {'used' if device.get('used') else 'not used'} ({device.get('why')})"
    )
    out.append(f"  Intimacy: {pitch.get('intimacy')}")
    out.append(f"  Shouted lines: {'yes' if pitch.get('shouted_lines') else 'no'}")
    out.append(
        f"  Hook line burned on screen: {'yes' if pitch.get('hook_line_on_screen') else 'no'}"
        + ("" if pitch.get("hook_line_on_screen") else " (finish with --no-hook-line)")
    )
    if with_hook_line and pitch.get("hook_line_on_screen"):
        from creation.hook_line import gate_hook_line_lines

        out.extend(gate_hook_line_lines(desk, _desk_spine(desk), stored.episode))
    out.append("  World rules:")
    out.extend(f"    - {rule}" for rule in pitch.get("world_rules") or [])
    out.extend(_desk_shot_lines(desk, stored.episode))
    if with_voices:
        voices = pitch.get("voices") or {}
        out.append("  Voices (the operator confirms with the producer):")
        if voices.get("mode") or voices.get("characters"):
            out.append(f"  on the card: mode {voices.get('mode') or 'not given'}")
            out.extend(
                f"    {name}: {voice}"
                for name, voice in (voices.get("characters") or {}).items()
            )
        out.extend(desk_voice_lines(desk))
    return "\n".join(out)


def run_pitch(
    desk: Path, *, episode: int, file: Path | None = None, out: TextIO | None = None
) -> int:
    """``pitch --desk D --episode N [--file F]``: store and print the card, or print the current one (free)."""

    out = out or sys.stdout
    desk = Path(desk).expanduser().resolve()
    if file is None:
        newest = current_pitch(desk, episode)
        if newest is None:
            print(
                f"Episode {episode} has no pitch card yet. Write one with the producer, then "
                f"`fictora-produce pitch --desk {desk} --episode {episode} --file pitch.md`.",
                file=out,
            )
            return 1
        print(pitch_text(desk, newest), file=out)
        return 0
    stored, notes, written = store_pitch(desk, episode, read_pitch_file(file))
    print(pitch_text(desk, stored), file=out)
    for note in notes:
        print(note, file=out)
    if written:
        print(
            f"Stored {stored.path}. Show the human the card; after their yes: "
            f"`fictora-produce approve --desk {desk} --gate pitch --episode {episode}`.",
            file=out,
        )
    else:
        print(
            f"Unchanged: the card is the same as v{stored.version}; nothing new stored.",
            file=out,
        )
    return 0


def run_approve_pitch(
    desk: Path, *, episode: int, out: TextIO | None = None
) -> StoredPitch:
    """``approve --gate pitch``: record the yes and say what it covers."""

    out = out or sys.stdout
    stored = approve_pitch(desk, episode)
    print(
        f"Pitch approved: episode {episode}, v{stored.version} ({stored.path.name}). "
        "A change to the card needs a new yes.",
        file=out,
    )
    return stored


# --- the gate and the script checks ---------------------------------------------------------------


def pitch_gate_refusal(
    desk: Path, *, episode: int, stage: str, rerun: str = "`step` again"
) -> str | None:
    """The stop before anything paid on a new desk until the episode's newest pitch has a yes, or ``None``.

    ``step`` (plates, boards, the filming confirm), ``redraw-board``,
    ``redraw-plate`` and ``film --confirm-spend`` ask it before they send
    anything, so a pitch rewritten mid-episode holds every paid draw until its
    new yes. ``stage`` names what is held, ``rerun`` what to run after the yes.
    Desks created before 6 Oct 2026 are never held: they get a reminder on
    stderr instead, on every episode (founder 8 Oct 2026, reminder-only; every
    episode since 9 Oct 2026, ``pitch_card`` in Group A).
    """

    desk = Path(desk).expanduser().resolve()
    # Every desk since 9 Oct 2026 (``pitch_card`` in Group A); off the allow-list a legacy desk skips it.
    if not continuing_fix(desk, "pitch_card", episode=episode):
        return None
    if approved_pitch(desk, episode) is not None:
        return None
    newest = current_pitch(desk, episode)
    if newest is None:
        why = f"Episode {episode} has no pitch card."
    elif any(row.get("version") == newest.version for row in _approvals(desk, episode)):
        why = f"Episode {episode}'s pitch v{newest.version} changed after its yes."
    else:
        why = f"Episode {episode}'s pitch v{newest.version} has no yes yet."
    if is_legacy(desk):
        # A continuing desk is reminded, never held (founder decision 8 Oct 2026:
        # "fixes apply but don't make it blocking").
        print(
            f"Reminder before {stage}: {why} The producer approves the idea before any picture is paid "
            f"for: `fictora-produce pitch --desk {desk} --episode {episode} --file pitch.md`, then "
            f"`fictora-produce approve --desk {desk} --gate pitch --episode {episode}`. Going ahead "
            "(this show started before 6 Oct 2026, so the pitch card does not hold it).",
            file=sys.stderr,
        )
        return None
    return (
        f"Stopped before {stage}. Nothing was sent.\n{why} The producer approves the idea before any "
        "picture is paid for: write the pitch card with them (throughline, the one charged situation, world "
        "rules, premise device, emotional beats with expression and delivery, intimacy, shouted lines, the "
        "on-screen hook line, hook, ending, voices), store it with "
        f"`fictora-produce pitch --desk {desk} --episode {episode} --file pitch.md`, show it, then after "
        f"their yes `fictora-produce approve --desk {desk} --gate pitch --episode {episode}`, and {rerun}."
    )


_STOP_WORDS = frozenset(
    "a an the and or but of to in on at by for with from into onto her his their its he she they him them "
    "is are was were be been as it that this then than so up out over under".split()
)


def _words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z']+", text.lower())
        if len(word) > 2 and word not in _STOP_WORDS
    }


def _beat_text(spine: Mapping[str, Any], beat: Mapping[str, Any]) -> str:
    from creation.spine_view import spoken_lines

    parts = [str(beat.get("motion_intent") or "")]
    for line in spoken_lines(spine, beat):
        parts.append(line.original)
        parts.append(line.translation)
    # Background shouts are part of what the beat shows ("the soldiers charge").
    for line in [*(beat.get("dialogue_lines") or []), *(beat.get("crowd_lines") or [])]:
        if isinstance(line, Mapping):
            parts.append(str(line.get("text") or ""))
            parts.append(str(line.get("spoken_text") or ""))
            parts.append(str(line.get("subtitle_text") or ""))
    return " ".join(part for part in parts if part)


def _episode_beats(spine: Mapping[str, Any], episode: int) -> list[Mapping[str, Any]]:
    from creation.spine_view import episode_id_for

    episode_id = episode_id_for(spine, episode)
    return [
        beat
        for beat in spine.get("beats") or []
        if isinstance(beat, Mapping) and beat.get("episode_id") == episode_id
    ]


def _match_beat(
    wanted: str,
    beats: Sequence[Mapping[str, Any]],
    spine: Mapping[str, Any],
    index: int,
) -> Mapping[str, Any] | None:
    words = _words(wanted)
    best: Mapping[str, Any] | None = None
    best_score = 0
    for beat in beats:
        score = len(words & _words(_beat_text(spine, beat)))
        if score > best_score:
            best, best_score = beat, score
    if best is not None:
        return best
    return beats[index] if index < len(beats) else None


#: The expression a beat names at the end of its intent (the server's tag): "Expression: Mina — tear_up".
_EXPRESSION_TAIL = re.compile(
    r"\bExpression:\s*(?:(?P<face>[^.;:\n]+?)\s*(?:\u2014|\u2013|\s-\s)\s*)?(?P<kind>[a-z_]+)\b",
    re.IGNORECASE,
)
#: Expression kinds that film as a held, blank face (canary 7 Oct: shot 3 ``stunned_blank``, unwarned).
RESTRAINT_KINDS = frozenset(
    {"blank", "stunned_blank", "deadpan", "neutral", "poker_face", "stoic", "frozen", "numb",
     "expressionless", "cold_stare", "smile_goes_cold", "vacant_stare", "impassive"}
)  # fmt: skip
#: A beat about a death or a loss: a restrained face there is never the drama.
_GRIEF = re.compile(
    r"\b(?:grie(?:f|v\w*)|mourn\w*|funeral|died|death|passed away|bereave\w*|widow\w*|"
    r"late (?:mother|father|mom|mum|dad|wife|husband|son|daughter|brother|sister|grand\w+))\b",
    re.IGNORECASE,
)


def beat_frames(
    spine: Mapping[str, Any], beat: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    """The frames a beat is drawn on: its anchor frame and the rest of that board row."""

    frames = [f for f in spine.get("frames") or [] if isinstance(f, Mapping)]
    anchor = next(
        (
            f
            for f in frames
            if beat.get("frame_id") and f.get("frame_id") == beat.get("frame_id")
        ),
        None,
    )
    if anchor is None:
        return []

    def row(frame: Mapping[str, Any]) -> int:
        raw = frame.get("board_row")
        return int(raw) if str(raw or "").isdigit() else int(frame.get("ordinal") or 0)

    return [
        f
        for f in frames
        if f is anchor
        or (
            f.get("storyboard_group_id") == anchor.get("storyboard_group_id")
            and f.get("episode_id") == anchor.get("episode_id")
            and row(f) == row(anchor)
        )
    ]


def beat_expressions(spine: Mapping[str, Any], beat: Mapping[str, Any]) -> list[str]:
    """Every expression kind a beat carries: its ``reaction_kind``, its intent's ``Expression:`` tag, and
    the ``reaction_kind`` on its frames (canary 7 Oct: the panel showed ``tear_up`` and the check said none)."""

    found: list[str] = []
    kinds = [str(beat.get("reaction_kind") or "")]
    kinds += [
        m.group("kind")
        for m in _EXPRESSION_TAIL.finditer(str(beat.get("motion_intent") or ""))
    ]
    for frame in beat_frames(spine, beat):
        brief = frame.get("visual_brief")
        if isinstance(brief, Mapping):
            kinds.append(str(brief.get("reaction_kind") or ""))
    for kind in kinds:
        kind = kind.strip().replace(" ", "_").lower()
        if kind and kind != "none" and kind not in found:
            found.append(kind)
    return found


def restrained_kinds(kinds: Sequence[str]) -> list[str]:
    """The kinds that film as a blank face (:data:`RESTRAINT_KINDS`, or a restraint word in the kind)."""

    return [
        k for k in kinds if k in RESTRAINT_KINDS or restraint_words(k.replace("_", " "))
    ]


def _is_grief(spine: Mapping[str, Any], beat: Mapping[str, Any]) -> bool:
    return bool(_GRIEF.search(_beat_text(spine, beat)))


def _beat_delivery(beat: Mapping[str, Any]) -> str:
    direction = beat.get("motion_direction")
    value = direction.get("delivery") if isinstance(direction, Mapping) else None
    return str(value or beat.get("delivery") or "")


def emotional_beat_lines(
    spine: Mapping[str, Any], pitch: Mapping[str, Any], *, episode: int
) -> list[str]:
    """Warn when a beat the pitch calls emotional has no expression or no delivery on the story.

    Each pitch beat is matched to the story beat that shares the most words with
    it (else the beat in the same place). An expression is any kind the beat
    carries (:func:`beat_expressions`: its ``reaction_kind``, its intent's
    ``Expression:`` tag, its frames' ``reaction_kind``), or the pitch's named
    face written into its intent.
    """

    beats = _episode_beats(spine, episode)
    lines: list[str] = []
    for index, wanted in enumerate(pitch.get("emotional_beats") or []):
        beat = _match_beat(str(wanted.get("beat") or ""), beats, spine, index)
        if beat is None:
            lines.append(
                f'!! pitch emotional beat {index + 1} ("{wanted.get("beat")}") has no beat in the script.'
            )
            continue
        number = beat.get("ordinal") or beat.get("beat_id")
        face_words = _words(str(wanted.get("expression") or ""))
        has_face = bool(beat_expressions(spine, beat)) or bool(
            face_words and face_words & _words(str(beat.get("motion_intent") or ""))
        )
        missing = []
        if not has_face:
            missing.append(
                f'no expression (pitch: "{wanted.get("expression")}"; '
                f"`edit --episode {episode} --beat {number} --expression KIND`, or write the face into the intent)"
            )
        if not _beat_delivery(beat):
            missing.append(
                f"no delivery (pitch: {wanted.get('delivery')}; "
                f"`edit --episode {episode} --beat {number} --set delivery={wanted.get('delivery') or 'KIND'}`)"
            )
        if missing:
            lines.append(
                f'!! beat {number} (pitch emotional beat {index + 1}, "{wanted.get("beat")}"): '
                + "; ".join(missing)
                + ". Without both it films as a blank face."
            )
    return lines


def _device_pattern(device: Any) -> re.Pattern[str] | None:
    """One pattern for every device word: a Latin word at a word start (``rain`` finds ``rainy``);
    a Korean, Japanese or Chinese word anywhere (those scripts do not space or mark word starts)."""

    parts: list[str] = []
    for word in device_words(device):
        pieces = [re.escape(piece) for piece in word.split()]
        joined = r"\s+".join(pieces)
        parts.append(rf"\b{joined}\w*" if word[0].isascii() else joined)
    if not parts:
        return None
    return re.compile("|".join(f"(?:{part})" for part in parts), re.IGNORECASE)


def premise_device_lines(
    spine: Mapping[str, Any], pitch: Mapping[str, Any], *, episode: int
) -> list[str]:
    """Warn when the premise device is in this episode though the pitch says not, or in each of the last 3."""

    device = pitch.get("premise_device") or {}
    pattern = _device_pattern(device.get("device"))
    if pattern is None:
        return []

    def present(ordinal: int) -> bool:
        text = " ".join(
            _beat_text(spine, beat) for beat in _episode_beats(spine, ordinal)
        )
        return bool(pattern.search(text))

    lines: list[str] = []
    if not device.get("used") and present(episode):
        lines.append(
            f'!! the premise device "{device_label(device.get("device"))}" is in this episode\'s lines or beats, '
            "but the approved pitch says it is not used. Take it out, or change the pitch and ask again."
        )
    if episode >= 3 and all(
        present(ordinal) for ordinal in range(episode - 2, episode + 1)
    ):
        lines.append(
            f'!! the premise device "{device_label(device.get("device"))}" is in each of the last 3 episodes '
            f"({episode - 2}–{episode}). Use it sparingly; find this episode's own turn "
            '(Noodle24: "what do u keep bringing rain in?").'
        )
    return lines


def _restrained_kind_line(
    beat: Mapping[str, Any], kinds: Sequence[str], why: str
) -> str:
    return (
        f"!! beat {beat.get('ordinal') or beat.get('beat_id')}: restrained expression ({', '.join(kinds)}) "
        f"on {why}. It films as a blank face: give it a big expression "
        f"(`edit --beat N --expression KIND`, or the frame's `--set reaction_kind=…`) and a delivery."
    )


def restraint_kind_lines(
    spine: Mapping[str, Any], pitch: Mapping[str, Any], *, episode: int
) -> list[str]:
    """Warn on a restrained expression kind on a beat (or its frames) the pitch calls emotional (every desk).

    A grief beat is warned by :func:`restraint_beat_lines` already and is left out here.
    """

    beats = _episode_beats(spine, episode)
    lines: list[str] = []
    seen: set[str] = set()
    for index, wanted in enumerate(pitch.get("emotional_beats") or []):
        beat = _match_beat(str(wanted.get("beat") or ""), beats, spine, index)
        if beat is None or str(beat.get("beat_id")) in seen or _is_grief(spine, beat):
            continue
        seen.add(str(beat.get("beat_id")))
        kinds = restrained_kinds(beat_expressions(spine, beat))
        if kinds:
            lines.append(
                _restrained_kind_line(
                    beat,
                    kinds,
                    f'pitch emotional beat {index + 1} ("{wanted.get("beat")}")',
                )
            )
    return lines


def restraint_beat_lines(spine: Mapping[str, Any], *, episode: int) -> list[str]:
    """Warn on restraint words in the episode's beat text and expressions, and on a restrained
    expression kind (beat or frames) on a grief beat (every desk)."""

    lines: list[str] = []
    for beat in _episode_beats(spine, episode):
        if _is_grief(spine, beat):
            kinds = restrained_kinds(beat_expressions(spine, beat))
            if kinds:
                lines.append(_restrained_kind_line(beat, kinds, "a grief beat"))
                continue
        text = " ".join(
            [
                str(beat.get("motion_intent") or ""),
                str(beat.get("reaction_kind") or "").replace("_", " "),
            ]
        )
        found = restraint_words(text)
        if found:
            lines.append(
                f"!! beat {beat.get('ordinal') or beat.get('beat_id')}: restrained direction ({', '.join(found)}). "
                "On a drama, grief or horror beat it films as a blank face: name a big expression and a delivery."
            )
    return lines


def script_pitch_lines(
    desk: Path, spine: Mapping[str, Any], *, episode: int
) -> list[str]:
    """What the script gate prints from the pitch: the card, its world rules and the checks against it.

    Nothing when the episode has no card. A card without a yes is printed as
    such (the checks still run against it).
    """

    stored = current_pitch(desk, episode)
    if stored is None:
        return []
    lines = [
        pitch_text(desk, stored, with_voices=False, with_hook_line=False).replace(
            "  World rules:",
            "  World rules — check every line and beat against these:",
            1,
        )
    ]
    lines += emotional_beat_lines(spine, stored.pitch, episode=episode)
    lines += restraint_kind_lines(spine, stored.pitch, episode=episode)
    lines += premise_device_lines(spine, stored.pitch, episode=episode)
    return lines


__all__ = [
    "DELIVERIES",
    "FIELDS",
    "PG_RULE",
    "PitchRefused",
    "StoredPitch",
    "approve_pitch",
    "approved_pitch",
    "current_pitch",
    "device_label",
    "device_words",
    "emotional_beat_lines",
    "is_approved",
    "memory_canon",
    "parse_pitch_markdown",
    "pg_words",
    "pitch_gate_refusal",
    "pitch_text",
    "pitch_warnings",
    "premise_device_lines",
    "read_pitch_file",
    "RESTRAINT_KINDS",
    "beat_expressions",
    "beat_frames",
    "restrained_kinds",
    "restraint_beat_lines",
    "restraint_kind_lines",
    "restraint_words",
    "run_approve_pitch",
    "run_pitch",
    "script_pitch_lines",
    "store_pitch",
    "stored_pitches",
    "validate_pitch",
]
