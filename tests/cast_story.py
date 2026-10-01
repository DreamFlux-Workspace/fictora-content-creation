"""A story for the cast commands: Hana has a look, beat 2 is silent and drawn, and a fake server.

Production learnings 1 Oct 2026: L-20261001-23 / -103 (a voice-only character an episode later put in a
frame had no look, and `redraw-plate` was refused `cast_visual_brief_missing`), L-20261001-19 (a new
character could only reach the screen through hand patches) and L-20261001-21 / -28 (a show voiced in
Japanese but drafted en-US refused every pinned line).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from creation import cast_commands as cc
from creation.harness.http_util import api_error_text
from fake_api import FakeApi, spine_fixture

HANA_BRIEF: dict[str, Any] = {
    "age_band": "late 20s",
    "face_anchors": ["round face", "small mole under the left eye"],
    "hair_anchors": ["black bob"],
    "silhouette": "slight, upright",
    "wardrobe_anchors": ["green apron over a white shirt"],
    "default_expression": "tired smile",
    "default_gaze": "down at the counter",
    "default_posture": "leaning on the counter",
    "visual_medium": "manhwa ink and flat colour",
    "surface_treatment": "clean line art, soft cel shading",
    "palette": ["teal", "warm amber"],
    "reference_lighting": "even studio light",
    "reference_background": "plain light grey",
    "forbidden_elements": ["glasses"],
}

SAM_LOOK = """\
age: 50s
gender: male
face: long face; grey stubble; deep-set eyes
hair: short grey crew cut
silhouette: tall, stooped
wardrobe: navy transit uniform jacket; brass name badge
gaze: past the viewer, wary
A night-shift station guard who has seen too much.
"""


def _refusal(code: str, message: str, details: dict[str, Any]) -> SystemExit:
    body = {
        "error": {"code": code, "message": message, "details": details},
        "request_id": "req_1",
    }
    return SystemExit(
        f"HTTP 400 PATCH https://drama.example/v1/spines/sp1: {api_error_text(body)}"
    )


def _story(*, approved: bool) -> dict[str, Any]:
    """Episode 1: beat 1 speaks (Hana, Ren); beat 2 is silent and drawn on frame_episode_01_02; Hana has a look."""

    spine = spine_fixture(approved=approved)
    spine["cast"][0]["visual_brief"] = copy.deepcopy(HANA_BRIEF)
    spine["cast"][0]["visual_description"] = "Hana, the shop owner."
    spine["beats"].insert(
        1,
        {
            "beat_id": "beat_episode_01_02",
            "episode_id": "episode_01",
            "ordinal": 2,
            "frame_id": "frame_episode_01_02",
            "motion_intent": "Hana looks at the door",
            "motion_direction": {
                "camera_move": "locked",
                "intensity": "low",
                "subject_cast_id": "cast_hana",
            },
            "dialogue_lines": [],
        },
    )
    for frame in spine["frames"]:
        frame["cast_refs"] = ["cast_hana"]
        frame["visual_brief"]["subject_blocking"][0].update(
            {"pose": "behind the counter", "gaze": "at the door", "interaction": "none"}
        )
    return spine


def _with_sam(spine: dict[str, Any], *, in_frame: bool = False) -> dict[str, Any]:
    """Sam, added earlier with `line --new-voice`: a voice card with no look and one off-screen line.

    ``in_frame``: a later episode put him in a frame (L-20261001-23), so he is drawn and owes a plate.
    """

    spine["cast"].append(
        {
            "cast_id": "cast_sam",
            "name": "Sam",
            "role": "station guard",
            "visual_description": "Never shown on screen; heard only as a voice: gravelly",
            "visual_brief": None,
            "voice_brief": {"provider_voice": "Bill"},
        }
    )
    spine["beats"][1]["dialogue_lines"].append(
        {
            "line_id": "line_episode_01_02_sam",
            "cast_id": "cast_sam",
            "text": "Last train's gone.",
            "off_screen": True,
        }
    )
    if in_frame:
        frame = spine["frames"][0]
        frame["cast_refs"].append("cast_sam")
        frame["visual_brief"]["subject_blocking"].append(
            {"cast_id": "cast_sam", "frame_position": "doorway", "pose": "leaning", "gaze": "at Hana",
             "interaction": "none"}
        )  # fmt: skip
    _flag_voice_only(spine)
    return spine


def _voice_only(spine: dict[str, Any]) -> set[str]:
    """fictora-drama ``voice_only_cast_ids``: every line off screen, no frame and no beat stages them."""

    heard: set[str] = set()
    seen: set[str] = set()
    for beat in spine["beats"]:
        here = set()
        for line in beat["dialogue_lines"]:
            (heard if line.get("off_screen") else seen).add(line["cast_id"])
            if line.get("off_screen"):
                here.add(line["cast_id"])
        subject = (beat.get("motion_direction") or {}).get("subject_cast_id")
        if subject and subject not in here:
            seen.add(subject)
    for frame in spine["frames"]:
        seen.update(frame.get("cast_refs") or [])
        seen.update(
            b["cast_id"]
            for b in (frame.get("visual_brief") or {}).get("subject_blocking") or []
        )
    return heard - seen


def _flag_voice_only(spine: dict[str, Any]) -> None:
    """The spine view flags each card the story only hears (``DramaCastCardView.voice_only``)."""

    unseen = _voice_only(spine)
    for card in spine["cast"]:
        card["voice_only"] = card["cast_id"] in unseen


def _server(api: FakeApi) -> Any:
    """Apply a story patch the way fictora-drama does for the fields these commands send.

    A frame may not stage a character the spine BEFORE the patch only hears, nor one the same patch
    adds as a voice (``spine_frame_cast_edits``): the reason the kit stages a new character last.
    """

    def apply(patch: dict[str, Any]) -> None:
        doc = api.spine_doc
        unseen = _voice_only(doc) | {
            c["cast_id"] for c in patch.get("add_voice_only_cast") or []
        }
        for frame_patch in patch.get("frames") or []:
            staged = frame_patch.get("cast_refs") or []
            heard = [cid for cid in staged if cid in unseen]
            if heard:
                raise _refusal(
                    "voice_only_cast_on_screen",
                    "A voice-only character is heard, never seen: no frame stages them.",
                    {"frame_cast_ids": {frame_patch["frame_id"]: heard}},
                )
        known = {c["cast_id"] for c in doc["cast"]}
        for card in patch.get("cast") or []:
            if card["cast_id"] not in known:
                raise _refusal(
                    "invalid_patch",
                    "unknown ids",
                    {"unknown_ids": {"cast": [card["cast_id"]]}},
                )
        for card in patch.get("add_voice_only_cast") or []:
            doc["cast"].append(
                {
                    "cast_id": card["cast_id"],
                    "name": card["name"],
                    "role": card["role"],
                    "visual_description": "Never shown on screen",
                    "visual_brief": None,
                    "voice_brief": {
                        "provider_voice": card.get("provider_voice") or "Bill"
                    },
                }
            )
        for card in patch.get("cast") or []:
            target = next(c for c in doc["cast"] if c["cast_id"] == card["cast_id"])
            target.update({k: v for k, v in card.items() if k != "cast_id"})
        beats = {b["beat_id"]: b for b in doc["beats"]}
        removed = set(patch.get("remove_dialogue_line_ids") or [])
        for beat in doc["beats"]:
            beat["dialogue_lines"] = [
                ln for ln in beat["dialogue_lines"] if ln["line_id"] not in removed
            ]
        for add in patch.get("add_dialogue_lines") or []:
            line = {k: v for k, v in add.items() if k != "beat_id"}
            beats[add["beat_id"]]["dialogue_lines"].append(
                {"line_id": f"line_{add['beat_id'][5:]}_new", **line}
            )
        for edit in patch.get("beats") or []:
            beats[edit["beat_id"]]["motion_direction"] = edit["motion_direction"]
        lines = {ln["line_id"]: ln for b in doc["beats"] for ln in b["dialogue_lines"]}
        for edit in patch.get("dialogue_lines") or []:
            lines[edit["line_id"]].update(
                {k: v for k, v in edit.items() if k != "line_id"}
            )
        frames = {f["frame_id"]: f for f in doc["frames"]}
        for frame_patch in patch.get("frames") or []:
            frames[frame_patch["frame_id"]].update(
                {k: v for k, v in frame_patch.items() if k != "frame_id"}
            )
        doc["spine_version"] = f"v{int(doc['spine_version'][1:]) + 1}"
        _flag_voice_only(doc)

    return apply


def _before_gate(api: FakeApi, spine: dict[str, Any]) -> None:
    _flag_voice_only(spine)
    api.spine_doc = spine
    apply = _server(api)

    def patch(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        apply((body or {})["patch"])
        return {}

    api.routes[("PATCH", "/v1/spines/sp1")] = patch


def _after_gate(api: FakeApi, spine: dict[str, Any]) -> list[dict[str, Any]]:
    _flag_voice_only(spine)
    api.spine_doc = spine
    apply = _server(api)
    edits: list[dict[str, Any]] = []

    def preview(_m: str, _p: str, body: dict[str, Any] | None) -> dict[str, Any]:
        edits.append(copy.deepcopy((body or {})["edit"]))
        return {"proposal_id": f"prop_{len(edits)}", "items": []}

    def execute(*_: Any) -> dict[str, Any]:
        edit = edits[-1]
        if edit["target_type"] == "cast_card":
            apply({"cast": [edit["patch"]]})
        else:
            apply(edit["patch"])
        return {"stale_storyboard_sets": []}

    api.routes[("POST", "/v1/spines/sp1/cascade/preview")] = preview
    api.routes[("POST", "/v1/spines/sp1/cascade/execute")] = execute
    return edits


def _patches(api: FakeApi) -> list[dict[str, Any]]:
    return [
        body["patch"] for method, _, body, _ in api.calls if method == "PATCH" and body
    ]


def _writes(api: FakeApi) -> list[tuple[str, str]]:
    return [
        (method, path)
        for method, path, _, _ in api.calls
        if method in {"PATCH", "POST"}
    ]


def _look_file(tmp_path: Path) -> str:
    path = tmp_path / "sam-look.txt"
    path.write_text(SAM_LOOK, encoding="utf-8")
    return f"@{path}"


SAM_BRIEF = {
    "age_band": "50s",
    "gender_presentation": "male",
    "face_anchors": ["long face", "grey stubble", "deep-set eyes"],
    "hair_anchors": ["short grey crew cut"],
    "silhouette": "tall, stooped",
    "wardrobe_anchors": ["navy transit uniform jacket", "brass name badge"],
    "default_expression": cc.DEFAULT_EXPRESSION,
    "default_gaze": "past the viewer, wary",
    "default_posture": cc.DEFAULT_POSTURE,
    # The drawing style is the show's, taken from a cast card that has a look.
    "visual_medium": "manhwa ink and flat colour",
    "surface_treatment": "clean line art, soft cel shading",
    "palette": ["teal", "warm amber"],
    "reference_lighting": "even studio light",
    "reference_background": "plain light grey",
    "forbidden_elements": [],
}


STAGING = '{"frame_position": "right third", "pose": "in the doorway", "gaze": "at Hana", "interaction": "holds the door"}'
