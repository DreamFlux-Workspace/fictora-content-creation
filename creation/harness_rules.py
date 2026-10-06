"""General checks the harness runs before money moves or a join widens a seam.

``edit``, ``film`` / ``step``, ``join``, ``cue``, ``tempo`` and
``finish --thumbnail`` call this module. The caption check is the same kind of
rule in ``creation.post.safe_zones.caption_box``: yellow in the frame is not
always the caption.

The server still refuses some of these at filming. The harness refuses them
first, with nothing sent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from creation.spine_view import beats_by_take, episode_id_for, spoken_lines

#: Words that make the server refuse the take: inner voice must not be written
#: into the take as fixture lines.
_INNER_VOICE_WORDS = re.compile(r"\binner voice\b", re.IGNORECASE)

#: A thought that ends this many seconds before its take is clear of the cut.
_THOUGHT_GAP_SECONDS = 0.5

#: Gain matching that moves a take by less than this is left unremarked.
_GAIN_NOTE_DB = 1.0

#: The hook's first line should be heard by this time.
FIRST_LINE_BY_SECONDS = 0.5

_HAND_OVER_MOUTH = re.compile(
    r"hand[- ]over[- ](?:the[- ])?mouth|dramatic[_ ]gasp", re.IGNORECASE
)
_LOCKED_CAMERA = re.compile(r"locked|hold|static|still", re.IGNORECASE)
_YOUNG = re.compile(r"\b(?:newborn|hatchling|baby|pup|cub|infant)\b", re.IGNORECASE)
_PROPORTIONS = re.compile(
    r"oversized head|huge eyes|large eyes|chubby|small body|short limbs|baby proportions",
    re.IGNORECASE,
)
_CREATURE = re.compile(
    r"\b(?:creature|monster|beast|dragon|hatchling|wyrm|reptile|demon|wolf|hound)\b",
    re.IGNORECASE,
)
_CREATURE_SOUND_OK = re.compile(
    r"\b(?:hiss|roar|growl|screech|howl|bark|chirp|snarl|shriek|bellow|croak|yelp|whine|rumble)\b",
    re.IGNORECASE,
)
_LINE_START = re.compile(r"\((\d+(?:\.\d+)?)\s*[–-]")

#: A one- or two-word line held longer than this was stretched by the transcript.
SHORT_LINE_HOLD_SECONDS = 3.0

#: Spoken lines a take should carry so the cut stays punchy.
LINES_PER_TAKE = 3

_NEAR_TOUCH = re.compile(r"an inch|stops short|almost touch|just short", re.IGNORECASE)
_CLEAR_GAP = re.compile(
    r"hand's width|hands width|a clear gap|not touching|no contact", re.IGNORECASE
)
_HOLD = re.compile(
    r"\b(?:hold|holds|holding|grip|grips|gripping|catch|catches|grasp|grasps)\b",
    re.IGNORECASE,
)
_FORBID_HOLD = re.compile(
    # A hyphenated compound ("no face-touching") is a standing contact rule,
    # not a ban on holding the prop the frame stages.
    r"\b(?:no|not|never|forbid|forbidden|without|anyone)\b.{0,48}(?<!-)\b(?:hold|holding|grip|gripping|touch|touching)\b",
    re.IGNORECASE,
)
_ADULT_AGE = re.compile(r"\b([1-9]\d)\b")
_ADULT_FACE = re.compile(
    r"no blush|narrow face|defined jaw|stubble|adult face|crow",
    re.IGNORECASE,
)
_MOUTH_SOUND = re.compile(r"\b(?:slurp|chew|munch|sip|crunch|chomp)\b", re.IGNORECASE)
_MOUTH_SHOWN = re.compile(r"\bmouth\b|\blips\b", re.IGNORECASE)
_HANDS_ON = ("pour", "ladle", "slam", "slide", "chop", "fold")
#: A cue this close to 0 s sits on the opening line.
_OPENING_CUE_SECONDS = 0.05
#: Ends an episode's deliberate opening hook (fictora-drama ``opening_sound``): it belongs at 0 s.
OPENING_SOUND_MARKER = "on the first frame"
#: Every episode's first take carries an audible event inside this window (founder decision, 5 Oct 2026).
OPENING_WINDOW_SECONDS = 0.5

#: What ``finish`` prints when the cover route answers an audio error.
THUMBNAIL_AUDIO_ERROR = (
    "the cover route answered an audio error (operator_audio_failed). Nothing was charged. "
    "Use a still from the take as the platform cover."
)


def _beat_label(beat: Mapping[str, Any]) -> str:
    ordinal = beat.get("ordinal")
    if ordinal is not None:
        return f"beat {ordinal}"
    return f"beat {beat.get('beat_id')}"


def _strings(value: Any, found: list[str]) -> None:
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            _strings(item, found)
    elif isinstance(value, list):
        for item in value:
            _strings(item, found)


def on_screen_speaker_stop(
    beat: Mapping[str, Any], names: Mapping[str, str]
) -> str | None:
    """Refuse a beat whose on-screen line and motion subject are different people.

    Filming raises ``motion subject must match dialogue speaker`` after the
    edit has already been accepted. An off-screen line plays over whoever the
    beat moves, so it is not this check.

    Parameters
    ----------
    beat
        One beat, as spine JSON.
    names
        ``cast_id`` to display name.

    Returns
    -------
    str | None
        The stop line, or ``None`` when the beat can be filmed.
    """

    direction = beat.get("motion_direction") or {}
    if not isinstance(direction, Mapping):
        return None
    subject = direction.get("subject_cast_id")
    if not subject:
        return None
    lines = [
        line for line in beat.get("dialogue_lines") or [] if isinstance(line, Mapping)
    ]
    if not lines:
        return None
    line = lines[0]
    if line.get("off_screen") is True:
        return None
    speaker = line.get("cast_id")
    if not speaker or speaker == subject:
        return None
    who = names.get(str(speaker), str(speaker))
    moved = names.get(str(subject), str(subject))
    return (
        f"{_beat_label(beat)}: {who} says the line, but the beat moves {moved}. "
        "Filming refuses this (motion subject must match dialogue speaker). "
        "Set the motion subject to the speaker, or mark the line off-screen, before the take."
    )


def inner_voice_wording_stop(beat: Mapping[str, Any]) -> str | None:
    """Refuse a beat that still says "inner voice" in the words the take compiles.

    Parameters
    ----------
    beat
        One beat, as spine JSON.

    Returns
    -------
    str | None
        The stop line, or ``None`` when those words are gone.
    """

    blobs: list[str] = []
    _strings(beat.get("motion_intent"), blobs)
    _strings(beat.get("motion_direction"), blobs)
    _strings(beat.get("shot_plan"), blobs)
    for line in beat.get("dialogue_lines") or []:
        if isinstance(line, Mapping):
            _strings(line.get("text"), blobs)
            _strings(line.get("spoken_text"), blobs)
    if not any(_INNER_VOICE_WORDS.search(text) for text in blobs):
        return None
    return (
        f'{_beat_label(beat)}: the words "inner voice" are still in the beat. '
        "Filming refuses a take that writes them as fixture lines. "
        "Put the thought on the character with `fictora-produce inner-voice`, "
        "then take those words out of the beat."
    )


def early_extra_shots_stop(
    spine: Mapping[str, Any],
    *,
    episode: int,
    beat: Mapping[str, Any],
    plan: Sequence[Any] | None = None,
) -> str | None:
    """Refuse more than one shot on a beat that is not the last beat of its take.

    A row board anchors two panels per beat. A second shot on an earlier beat
    pushes every later line one row early. The take's last beat can hold the
    extra shots; nothing after it slides.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    beat
        The beat being planned or filmed.
    plan
        The plan about to be sent. Default: the plan already on the beat.

    Returns
    -------
    str | None
        The stop line, or ``None`` when the plan is one shot or the beat is last.
    """

    shots = list(beat.get("shot_plan") or []) if plan is None else list(plan)
    if len(shots) <= 1:
        return None
    pattern = tuple(int(count) for count in spine.get("beats_per_storyboard_set") or ())
    groups = beats_by_take(spine, episode=episode, take_count=len(pattern) or 1)
    beat_id = str(beat.get("beat_id"))
    for group in groups:
        ids = [str(item.get("beat_id")) for item in group]
        if beat_id not in ids:
            continue
        if ids[-1] == beat_id:
            return None
        return (
            f"{_beat_label(beat)}: {len(shots)} shots, and this beat is not the last beat of its take. "
            "The board anchors two panels per beat, so the extra shot pushes every later line one row early. "
            "Give more than one shot only to the take's last beat."
        )
    return None


def _episode_beats(spine: Mapping[str, Any], episode: int) -> list[Mapping[str, Any]]:
    episode_id = episode_id_for(spine, episode)
    return [
        beat
        for beat in spine.get("beats") or []
        if isinstance(beat, Mapping) and beat.get("episode_id") == episode_id
    ]


def _cast_names(spine: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(card.get("cast_id")): str(card.get("name") or card.get("cast_id"))
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    }


def film_stops(spine: Mapping[str, Any], *, episode: int) -> list[str]:
    """Every filming rule that must pass before a take is enrolled.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal about to be filmed.

    Returns
    -------
    list[str]
        Stop lines. Empty when the episode can be sent.
    """

    names = _cast_names(spine)
    stops: list[str] = []
    for beat in _episode_beats(spine, episode):
        for stop in (
            on_screen_speaker_stop(beat, names),
            inner_voice_wording_stop(beat),
            early_extra_shots_stop(spine, episode=episode, beat=beat),
        ):
            if stop:
                stops.append(stop)
    return stops


def film_stop_message(spine: Mapping[str, Any], *, episode: int) -> str | None:
    """The film refusal, or ``None`` when :func:`film_stops` is empty.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal about to be filmed.

    Returns
    -------
    str | None
        One message. Nothing has been sent.
    """

    stops = film_stops(spine, episode=episode)
    if not stops:
        return None
    return "Stopped before filming. Nothing was sent.\n" + "\n".join(stops)


def gain_match_note(gains_db: Sequence[float]) -> str | None:
    """Say when gain matching moved the takes enough to widen a quiet-to-spoken seam.

    Two takes already near −18 LUFS can still be moved apart. A quiet tail
    against a spoken open then jumps by about twice the gain.
    ``join --no-gain-match`` keeps each take's level.

    Parameters
    ----------
    gains_db
        The gain applied to each part, in join order.

    Returns
    -------
    str | None
        The note, or ``None`` when every gain is under 1 dB.
    """

    if not gains_db or all(abs(gain) < _GAIN_NOTE_DB for gain in gains_db):
        return None
    shown = ", ".join(f"{gain:+.1f} dB" for gain in gains_db)
    return (
        f"Gain matching moved the takes ({shown}). "
        "Takes that finish already set near −18 LUFS should be joined with --no-gain-match: "
        "matching a quiet tail against a spoken open widens the seam."
    )


def thumbnail_answered_audio_error(message: str) -> bool:
    """True when the episode-thumbnail route failed as an audio job.

    The episode-thumbnail route can answer HTTP 502 ``operator_audio_failed``
    from an image route. Nothing was charged. The cover is a still from the take.

    Parameters
    ----------
    message
        The API error text.

    Returns
    -------
    bool
        Whether finish should skip the cover and keep the marked take.
    """

    lowered = message.lower()
    return (
        "operator_audio_failed" in lowered or "the audio could not be made" in lowered
    )


def inner_voice_cues(record_path: Any) -> list[dict[str, Any]]:
    """Inner-voice cues stored on a finish record, or an empty list.

    Parameters
    ----------
    record_path
        Path of ``take-epNN-tK-finish-vN.json``, or ``None``.

    Returns
    -------
    list[dict[str, Any]]
        Cue objects with ``start`` and ``seconds`` when ``finish`` laid them.
    """

    if record_path is None:
        return []
    try:
        raw = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    return [cue for cue in raw.get("inner_voice") or [] if isinstance(cue, dict)]


def thought_short_of_cut(
    cues: Sequence[Mapping[str, Any]],
    *,
    duration: float,
    label: str,
    last_take: bool,
) -> str | None:
    """Warn when a take's last thought ends before the cut and another take follows.

    A join fails when one take ends in silence and the next opens on a line.
    A thought that runs up to the cut keeps that seam a speech handoff.

    Parameters
    ----------
    cues
        Inner-voice cues laid on this take (``start`` and ``seconds`` on the take).
    duration
        Take length in seconds.
    label
        ``ep01 t1``.
    last_take
        True for the last part of the join. Nothing follows it, so the gap is not a seam.

    Returns
    -------
    str | None
        The warning, or ``None`` when the last thought runs up to the cut.
    """

    if last_take or duration <= 0:
        return None
    ends: list[float] = []
    for cue in cues:
        start = cue.get("start")
        seconds = cue.get("seconds")
        if isinstance(start, (int, float)) and isinstance(seconds, (int, float)):
            ends.append(float(start) + float(seconds))
    if not ends:
        return None
    gap = duration - max(ends)
    if gap <= _THOUGHT_GAP_SECONDS:
        return None
    return (
        f"!! {label}: the last thought ends {gap:.1f}s before the cut. "
        "Move it so it runs up to the cut (`inner-voice`), or the next take's opening line jumps the seam."
    )


def seam_fix_line() -> str:
    """What fixes a join seam the bed cannot close.

    A loud end into a quiet open stays over 5 dB until a sound that belongs in
    the scene is put on the quiet side and the dead air is trimmed.

    Returns
    -------
    str
        The line ``join`` prints with the stop.
    """

    return (
        "A louder bed barely changes a seam this size. Put a steady sound that belongs in the scene "
        "on the quiet side and trim the dead air, including a silent head, then join again."
    )


def late_opening_line(rows: Sequence[str], *, take_id: str) -> str | None:
    """Flag take 1 when its first line starts after the hook window.

    A first line that starts after the hook window is late. Trim the head of
    take 1 so the line is heard by 0.5 s.

    Parameters
    ----------
    rows
        ``check-lines`` rows, including the ``line 1`` row with its ``(start–end s)``.
    take_id
        Take id. Only ``t1`` is the hook.

    Returns
    -------
    str | None
        The warning, or ``None`` when the first line is in time or has no time.
    """

    if take_id != "t1":
        return None
    for row in rows:
        if not re.search(r"line 1 \(", row):
            continue
        found = _LINE_START.search(row)
        if found is None:
            return None
        start = float(found.group(1))
        if start <= FIRST_LINE_BY_SECONDS + 0.05:
            return None
        return (
            f"!! the first line starts at {start:.2f}s. The hook wants it by {FIRST_LINE_BY_SECONDS:.1f}s. "
            "review --transcribe, then trim the head of take 1."
        )
    return None


def _opening_beat(spine: Mapping[str, Any], episode: int) -> Mapping[str, Any] | None:
    beats = _episode_beats(spine, episode)
    if not beats:
        return None
    return min(beats, key=lambda beat: int(beat.get("ordinal") or 0))


def _hook_mouth_text(beat: Mapping[str, Any]) -> str | None:
    blobs: list[str] = []
    _strings(beat.get("motion_intent"), blobs)
    _strings(beat.get("reaction_kind"), blobs)
    _strings(beat.get("shot_plan"), blobs)
    kind = str(beat.get("reaction_kind") or "").replace(" ", "_").lower()
    if kind != "dramatic_gasp" and not any(
        _HAND_OVER_MOUTH.search(text) for text in blobs
    ):
        return None
    return (
        f"{_beat_label(beat)}: the hook would draw a hand over the mouth. "
        'Before the board, put "hand over mouth" in the beat\'s forbidden elements '
        "and keep both hands on the prop, below the chin."
    )


def hook_mouth_stop(spine: Mapping[str, Any], *, episode: int) -> str | None:
    """Refuse a first beat that would draw a hand over the mouth.

    A gasp on the hook draws a hand over the mouth, so the line cannot be read.
    The board is the paid step that draws it.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal about to be boarded.

    Returns
    -------
    str | None
        The stop line, or ``None`` when the opening beat does not hide the mouth.
    """

    first = _opening_beat(spine, episode)
    if first is None:
        return None
    return _hook_mouth_text(first)


def hook_mouth_edit_stop(
    spine: Mapping[str, Any], beat: Mapping[str, Any]
) -> str | None:
    """Refuse an edit that puts a hand over the mouth on the episode's first beat.

    Parameters
    ----------
    spine
        Spine JSON.
    beat
        The beat about to be sent, including the new expression or intent.

    Returns
    -------
    str | None
        The stop line, or ``None`` when this beat is not the hook or does not hide the mouth.
    """

    episode_id = beat.get("episode_id")
    same = [
        item
        for item in spine.get("beats") or []
        if isinstance(item, Mapping) and item.get("episode_id") == episode_id
    ]
    if not same:
        return None
    first = min(same, key=lambda item: int(item.get("ordinal") or 0))
    if str(first.get("beat_id")) != str(beat.get("beat_id")):
        return None
    return _hook_mouth_text(beat)


def locked_camera_line(spine: Mapping[str, Any], *, episode: int) -> str | None:
    """Warn when every shot on the episode is a locked camera.

    A draft locks the camera. Set the move before the board is drawn, with
    ``edit --beat N --shot``. A push-in on the first frame is how a close-up
    hook gets past a medium opening.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    str | None
        The warning, or ``None`` when any shot already has a move.
    """

    cameras: list[str] = []
    for beat in _episode_beats(spine, episode):
        plan = beat.get("shot_plan") or []
        if isinstance(plan, list) and plan:
            for shot in plan:
                if isinstance(shot, Mapping):
                    cameras.append(
                        str(shot.get("camera") or shot.get("camera_move") or "")
                    )
            continue
        direction = beat.get("motion_direction") or {}
        if isinstance(direction, Mapping):
            cameras.append(
                str(direction.get("camera_move") or direction.get("camera") or "")
            )
    if not cameras:
        return None
    if any(
        camera.strip() and _LOCKED_CAMERA.fullmatch(camera.strip()) is None
        for camera in cameras
    ):
        return None
    return (
        "The draft locks the camera. Set a move before the board is drawn: "
        'edit --beat N --shot "size|subject|camera|angle". '
        "A push-in on the first frame is the close-up hook. "
        "Speaking rows only take locked, dolly, pan, tilt or handheld."
    )


def young_creature_lines(spine: Mapping[str, Any]) -> list[str]:
    """Stop lines for a young creature whose brief has no proportions.

    A young character drawn from a look note comes back adult. The redraw that
    holds names the proportions: oversized head, large eyes, small body.

    Parameters
    ----------
    spine
        Spine JSON.

    Returns
    -------
    list[str]
        One line per young cast card that lacks those proportion words.
    """

    lines: list[str] = []
    for card in spine.get("cast") or []:
        if not isinstance(card, Mapping):
            continue
        blobs: list[str] = []
        _strings(card, blobs)
        text = " ".join(blobs)
        if _YOUNG.search(text) is None or _PROPORTIONS.search(text) is not None:
            continue
        name = str(card.get("name") or card.get("cast_id") or "the creature")
        lines.append(
            f"{name} is young and will be drawn adult. "
            "Name the proportions in the brief before the plate: oversized head, large eyes, "
            "small body. A look note does not hold; use redraw-plate --note with those words."
        )
    return lines


def creature_sound_stop(description: str) -> str | None:
    """Refuse a non-human cue that does not name its sound.

    A non-human cue comes back as a familiar animal unless the description
    names the sound. Place that sound on the frame the mouth opens.

    Parameters
    ----------
    description
        The cue description about to be sent.

    Returns
    -------
    str | None
        The stop line, or ``None`` when the cue is not a creature or already names the sound.
    """

    if (
        _CREATURE.search(description) is None
        or _CREATURE_SOUND_OK.search(description) is not None
    ):
        return None
    return (
        "A non-human cue comes back as a familiar animal unless the description names the sound. "
        "Name the sound, say what it is not, and place it on the frame the mouth opens."
    )


def short_line_hold(text: str, *, start: float, end: float) -> str | None:
    """Flag a one- or two-word line whose caption window is too long.

    A short line the transcript misses gets the whole following speech stretch,
    so the caption stays up for several seconds. Set the window by hand.

    Parameters
    ----------
    text
        The line as timed.
    start
        Window start, in seconds.
    end
        Window end, in seconds.

    Returns
    -------
    str | None
        The warning, or ``None`` when the line is longer or the window is short.
    """

    words = re.findall(r"\w+", text, flags=re.UNICODE)
    if len(words) == 0 or len(words) > 2:
        return None
    held = end - start
    if held <= SHORT_LINE_HOLD_SECONDS:
        return None
    return (
        f"!! a {len(words)}-word line is held {held:.1f}s. "
        "The transcript stretched it. Set the caption with finish --line-start and --line-end."
    )


def _episode_frames(spine: Mapping[str, Any], episode: int) -> list[Mapping[str, Any]]:
    episode_id = episode_id_for(spine, episode)
    return [
        frame
        for frame in spine.get("frames") or []
        if isinstance(frame, Mapping) and frame.get("episode_id") == episode_id
    ]


def _episode_text(spine: Mapping[str, Any], episode: int) -> str:
    blobs: list[str] = []
    for beat in _episode_beats(spine, episode):
        _strings(beat.get("motion_intent"), blobs)
        _strings(beat.get("shot_plan"), blobs)
    for frame in _episode_frames(spine, episode):
        _strings(frame.get("visual_brief"), blobs)
    return " ".join(blobs)


def near_touch_line(spine: Mapping[str, Any], *, episode: int) -> str | None:
    """Warn when a near-touch will be drawn as contact.

    "An inch away" and "stops short" are drawn as a touch. Say the gap as a
    hand's width and forbid the contact before the board is drawn.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    str | None
        The warning, or ``None`` when the episode does not ask for a near-touch.
    """

    text = _episode_text(spine, episode)
    if _NEAR_TOUCH.search(text) is None or _CLEAR_GAP.search(text) is not None:
        return None
    return (
        "A near-touch ('an inch', 'stops short') is drawn as contact. "
        "Say a hand's width of gap, and forbid the touch, before the board is drawn."
    )


def staging_contradiction_lines(spine: Mapping[str, Any], *, episode: int) -> list[str]:
    """Name frames that both stage a hold and forbid that hold.

    Rewriting a beat does not rewrite the frame's stored staging. The redraw
    keeps the old prop. Edit the frame, then redraw with no note.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[str]
        One line per contradictory frame.
    """

    lines: list[str] = []
    for frame in _episode_frames(spine, episode):
        brief = frame.get("visual_brief")
        brief = brief if isinstance(brief, Mapping) else {}
        # What the frame stages vs. what it forbids: "steps closer without
        # touching him" in the staging is a distance, not a ban.
        staged: list[str] = []
        _strings(
            {key: value for key, value in brief.items() if key != "forbidden_elements"},
            staged,
        )
        forbidden: list[str] = []
        _strings(brief.get("forbidden_elements"), forbidden)
        if (
            _HOLD.search(" ".join(staged)) is None
            or _FORBID_HOLD.search(" ".join(forbidden)) is None
        ):
            continue
        label = frame.get("frame_id") or frame.get("ordinal")
        lines.append(
            f"{label}: the frame both stages a hold and forbids it. "
            "A beat rewrite does not change stored staging. "
            "Edit the frame (`edit --frame N --set ...`), then redraw with no note."
        )
    return lines


def thin_take_lines(
    spine: Mapping[str, Any], *, episode: int, take_count: int
) -> list[str]:
    """Warn when a take has fewer than three spoken lines.

    A take with one or two lines, or none, plays slow. A line or a visible
    action wants to land every few seconds.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.
    take_count
        How many takes the episode is cut into.

    Returns
    -------
    list[str]
        One line per thin take.
    """

    if take_count < 1:
        return []
    lines: list[str] = []
    for index, group in enumerate(
        beats_by_take(spine, episode=episode, take_count=take_count), start=1
    ):
        count = sum(len(spoken_lines(spine, beat)) for beat in group)
        if count >= LINES_PER_TAKE:
            continue
        if count == 0:
            lines.append(
                f"take t{index} has no spoken line. Put a line or a visible action on it."
            )
            continue
        lines.append(
            f"take t{index} has {count} spoken line(s). A take wants {LINES_PER_TAKE} lines, "
            "and a line or a visible action every 3 to 4 seconds."
        )
    return lines


def _card_text(card: Mapping[str, Any]) -> str:
    blobs: list[str] = []
    _strings(card, blobs)
    return " ".join(blobs)


def _adult_age(text: str) -> int | None:
    for raw in _ADULT_AGE.findall(text):
        age = int(raw)
        if 18 <= age <= 80:
            return age
    return None


def adult_face_lines(spine: Mapping[str, Any]) -> list[str]:
    """Warn when a stated adult age has no adult face words.

    A stated age without adult features is drawn young. The note that holds
    names the face: narrow face, no blush. A look note does not hold. This
    does not stop the plates.

    Parameters
    ----------
    spine
        Spine JSON.

    Returns
    -------
    list[str]
        One line per adult card that lacks those face words. Empty when no
        card states an age.
    """

    lines: list[str] = []
    for card in spine.get("cast") or []:
        if not isinstance(card, Mapping):
            continue
        text = _card_text(card)
        age = _adult_age(text)
        if age is None or _ADULT_FACE.search(text) is not None:
            continue
        name = str(card.get("name") or card.get("cast_id") or "the character")
        lines.append(
            f"{name} is {age} and can be drawn young. "
            "Name adult features in the brief before the plate: narrow face, no blush. "
            "A look note does not hold."
        )
    return lines


def sparkle_adult_line(spine: Mapping[str, Any], *, episode: int) -> str | None:
    """Stop a sparkle expression on a character whose card states an adult age.

    ``sparkle_delight`` draws stars and a rounder, younger face. Use a calm
    expression before the board is drawn. A card with no stated age is left
    alone.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    str | None
        The stop line, or ``None`` when no adult beat uses a sparkle expression.
    """

    cards = {
        str(card.get("cast_id")): card
        for card in spine.get("cast") or []
        if isinstance(card, Mapping) and card.get("cast_id")
    }
    for beat in _episode_beats(spine, episode):
        kind = str(beat.get("reaction_kind") or "").replace(" ", "_").lower()
        if "sparkle" not in kind:
            continue
        speakers: set[str] = set()
        direction = beat.get("motion_direction") or {}
        if isinstance(direction, Mapping) and direction.get("subject_cast_id"):
            speakers.add(str(direction["subject_cast_id"]))
        for line in beat.get("dialogue_lines") or []:
            if isinstance(line, Mapping) and line.get("cast_id"):
                speakers.add(str(line["cast_id"]))
        for cast_id in speakers:
            card = cards.get(cast_id)
            if card is None:
                continue
            age = _adult_age(_card_text(card))
            if age is None:
                continue
            name = str(card.get("name") or cast_id)
            return (
                f"{_beat_label(beat)}: sparkle_delight on {name} ({age}) draws stars "
                "and a younger face. Use a calm expression before the board is drawn."
            )
    return None


def hands_on_sound_lines(spine: Mapping[str, Any], *, episode: int) -> list[str]:
    """Warn when a hands-on action has no sound note.

    A pour, a slam, or a slide that the plan never names stays silent. Add a
    sound note for each before finish, and place a missing sound on the frame
    the action lands.

    Parameters
    ----------
    spine
        Spine JSON.
    episode
        Episode ordinal.

    Returns
    -------
    list[str]
        One line when any of those actions is missing from the sound notes.
    """

    notes = " ".join(
        str(note.get("text") or "")
        for note in spine.get("sound_notes") or []
        if isinstance(note, Mapping)
    ).casefold()
    text = _episode_text(spine, episode)
    missing = [
        word
        for word in _HANDS_ON
        if re.search(rf"\b{word}", text, flags=re.IGNORECASE) and word not in notes
    ]
    if not missing:
        return []
    listed = ", ".join(missing)
    return [
        f"Hands-on actions with no sound note: {listed}. "
        "Add a sound note for each before finish, and place a missing sound "
        "on the frame the action lands."
    ]


def mouth_sound_note(description: str) -> str | None:
    """Warn when a mouth sound does not say the mouth is in frame.

    A slurp, chew, or sip only reads in sync on a frame that shows the mouth.
    The cue is still sent.

    Parameters
    ----------
    description
        The cue description about to be sent.

    Returns
    -------
    str | None
        The warning, or ``None`` when the cue is not a mouth sound or already
        names the mouth.
    """

    if _MOUTH_SOUND.search(description) is None or _MOUTH_SHOWN.search(description):
        return None
    return (
        "A mouth sound (slurp, chew, sip) only reads in sync on a frame that shows the mouth. "
        "Place it on that frame."
    )


def is_opening_cue(sound: str) -> bool:
    """Whether a cue is an episode's deliberate opening hook (its words end "on the first frame").

    Parameters
    ----------
    sound
        The cue's Sound label as the take facts carry it.

    Returns
    -------
    bool
        True for the server's opening hook.
    """

    return OPENING_SOUND_MARKER in sound.casefold()


def opening_sound_line(sound: str, start: float) -> str | None:
    """Warn when a cue sits on the opening line by accident.

    A sound at 0 s lands on the first line. Place it on the frame the action lands.
    The episode's opening hook is the one cue that belongs there, so it is never named.

    Parameters
    ----------
    sound
        What the cue is.
    start
        Where it is laid, in seconds.

    Returns
    -------
    str | None
        The warning, or ``None`` when the cue starts after the opening or is the opening hook.
    """

    if start > _OPENING_CUE_SECONDS or is_opening_cue(sound):
        return None
    return (
        f'!! "{sound}" sits at {start:.2f}s, on the opening line. '
        "Place it on the frame the action lands."
    )


def opening_sound_flat(
    cues: Sequence[tuple[str, float]], *, episode: int
) -> str | None:
    """Warn (``opening_sound_flat``) when an episode's first half second has no sound event.

    Every episode opens on a deliberate, audible event tied to its first picture
    (founder decision, 5 Oct 2026). A nudge, never a block: the finish goes on.

    Parameters
    ----------
    cues
        ``(sound, start)`` of every effect laid on the episode's first take.
    episode
        The episode number, for the message.

    Returns
    -------
    str | None
        The warning, or ``None`` when a cue starts inside the opening window.
    """

    if any(start <= OPENING_WINDOW_SECONDS for _sound, start in cues):
        return None
    return (
        f"!! opening_sound_flat: episode {episode} has no sound in its first {OPENING_WINDOW_SECONDS:g} s. "
        "Refreshing the take facts cannot add one: the server writes the opening hook into take 1 when it "
        "is filmed, and this take was filmed without it (an older server, or a take filmed before the hook). "
        "To give it one, lay a hand cue at 0 s: make one sound tied to the first picture with `cue` "
        '(a door, a phone buzz, a breath; name it "..., on the first frame") and finish take 1 again '
        "with `--cue FILE@0`. Or leave it: this is a nudge, not a stop."
    )
