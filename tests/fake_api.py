"""An in-memory stand-in for ``DramaApiRunSession`` shaped like the deployed API's answers."""

from __future__ import annotations

import copy
import json
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from creation.harness.session import PROVIDER_SPEC_SEGMENT


def png_bytes(gray: int = 128) -> bytes:
    """A tiny real PNG (brightness measurable)."""

    buf = BytesIO()
    Image.new("RGB", (8, 8), (gray, gray, gray)).save(buf, format="PNG")
    return buf.getvalue()


def spine_fixture(
    *, episodes: int = 2, approved: bool = True, spoken_language: str = "en-US"
) -> dict[str, Any]:
    """A spine like prod's: episode 1 is ``episode_01`` and episode 2 ``ep_02``."""

    ids = ["episode_01", "ep_02", "ep_03"][:episodes]
    beats: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    for number, episode_id in enumerate(ids, start=1):
        beats.append(
            {
                "beat_id": f"beat_{episode_id}_01",
                "episode_id": episode_id,
                "ordinal": 1,
                "motion_intent": f"Hana wipes the counter (ep {number})",
                "motion_direction": {"camera_move": "locked", "intensity": "low"},
                "dialogue_lines": [
                    {
                        "line_id": f"line_{episode_id}_01",
                        "cast_id": "cast_hana",
                        "text": "We're closed.",
                    },
                    {
                        "line_id": f"line_{episode_id}_02",
                        "cast_id": "cast_ren",
                        "text": "Not for me.",
                    },
                ],
            }
        )
        for row in (1, 2):
            frames.append(
                {
                    "frame_id": f"frame_{episode_id}_{row:02d}",
                    "episode_id": episode_id,
                    "ordinal": row,
                    "board_row": row,
                    "storyboard_group_id": f"sb_{episode_id}_set01",
                    "visual_brief": {
                        "shot_scale": "medium",
                        "camera_angle": "eye level",
                        "viewpoint": "front",
                        "cell_role": "action",
                        "row_direction": {"camera_move": "dolly_in"},
                        "subject_blocking": [
                            {
                                "cast_id": "cast_hana",
                                "frame_position": "bottom edge, left"
                                if row == 2
                                else "upper third",
                            }
                        ],
                        "story_objects": ["a cup"],
                        "ui_safe_zone": "bottom fifth clear of faces",
                    },
                }
            )
    return {
        "spine_id": "sp1",
        "spine_version": "v5",
        "approval_state": "approved" if approved else "draft",
        "spoken_language": spoken_language,
        "title": "Closing Time",
        "outline_mode": "arc_at_episode_two",
        "cast": [
            {"cast_id": "cast_hana", "name": "Hana"},
            {"cast_id": "cast_ren", "name": "Ren"},
        ],
        "episode_summaries": [
            {
                "episode_id": episode_id,
                "ordinal": number,
                "title": f"Ep {number}",
                "authoring_state": "drafted",
            }
            for number, episode_id in enumerate(ids, start=1)
        ],
        "beats_per_storyboard_set": [1],
        "beats": beats,
        "frames": frames,
        "media_assets": [
            {
                "asset_id": f"asset_{episode_id}_set01",
                "relation_type": "episode",
                "relation_id": episode_id,
                "url": f"https://r2.example/{episode_id}-board.png",
                "stale": False,
            }
            for episode_id in ids
        ]
        + [
            {
                "relation_type": "cast_card",
                "relation_id": "cast_hana",
                "url": "https://r2.example/hana.png",
            },
            {
                "relation_type": "cast_card",
                "relation_id": "cast_ren",
                "url": "https://r2.example/ren.png",
            },
        ],
        "look_notes": [],
    }


Handler = Callable[[str, str, dict[str, Any] | None], Any]


def openapi_doc(
    *,
    episode_ordinal: bool = True,
    one_take_estimate: bool = True,
    episode_thumbnail: bool = False,
) -> dict[str, Any]:
    """The slice of ``/openapi.json`` the kit reads: which request fields the deploy accepts."""

    film = {"episode_count": {}, "reroll_take_index": {}, "seed_attempt": {}}
    if episode_ordinal:
        film["episode_ordinal"] = {}
    estimate = {"spine_version": {}, "episode_ids": {}}
    if one_take_estimate:
        estimate["reroll_take_index"] = {}
    doc: dict[str, Any] = {
        "components": {
            "schemas": {
                "DramaVideoGenerationCreateRequest": {"properties": film},
                "DramaBatchEstimateRequest": {"properties": estimate},
            }
        }
    }
    if episode_thumbnail:
        doc["paths"] = {
            "/v1/spines/{spine_id}/episodes/{ordinal}/thumbnail": {"post": {}},
        }
    return doc


class FakeApi:
    """Records every call; answers from ``routes`` (``(METHOD, path)`` -> payload or callable)."""

    def __init__(self, out: Path, *, spine: dict[str, Any] | None = None) -> None:
        self.out = out
        self.out.mkdir(parents=True, exist_ok=True)
        self.session_id = "sess-1"
        self.prefix = "pfx"
        self.spine_doc = spine if spine is not None else spine_fixture()
        self.calls: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
        self.routes: dict[tuple[str, str], Any] = {
            ("GET", "/openapi.json"): openapi_doc()
        }
        self.jobs: dict[str, dict[str, Any]] = {}
        self.polled: list[tuple[str, bool]] = []
        self.client = self
        self.events: list[tuple[str, dict[str, Any]]] = []

    def close(self) -> None:
        """The harness closes its HTTP client."""

    def _answer(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        key: str | None = None,
    ) -> Any:
        if PROVIDER_SPEC_SEGMENT in path:
            raise AssertionError("provider-spec must never be requested")
        self.calls.append((method, path, copy.deepcopy(body), key))
        answer = self.routes.get((method, path.split("?", 1)[0]))
        if answer is None:
            raise AssertionError(f"unexpected {method} {path}")
        if callable(answer):
            answer = answer(method, path, body)
        if isinstance(answer, SystemExit):
            raise answer
        return copy.deepcopy(answer)

    def get(self, path: str) -> dict[str, Any]:
        """GET."""
        if path == "/v1/spines/sp1":
            self.calls.append(("GET", path, None, None))
            return copy.deepcopy(self.spine_doc)
        return self._answer("GET", path, None)

    def get_optional(self, path: str) -> tuple[int, Any]:
        """GET without raising; an unset ``/v1/video-generations/{id}`` answers 404 (the film poll reads it)."""
        if (
            path.startswith("/v1/video-generations/")
            and ("GET", path) not in self.routes
        ):
            return 404, {"error": {"code": "not_found"}}
        if (
            path.startswith("/v1/jobs/")
            and ("GET", path.split("?", 1)[0]) not in self.routes
        ):
            # A job status the test did not set (run_unit reads a saved job's status first).
            return 404, {"error": {"code": "not_found"}}
        if path.endswith("/voice-mode") and ("GET", path) not in self.routes:
            # A deploy whose voice-mode route the test did not set: read as an older server (locked).
            return 404, {"detail": "Not Found"}
        if path.endswith("/music-blend") and ("GET", path) not in self.routes:
            # A deploy whose music-blend route the test did not set: read as an older server (the genre's).
            return 404, {"detail": "Not Found"}
        if path.endswith("/look-options") and ("GET", path) not in self.routes:
            # A deploy whose look-options route the test did not set: read as an older server (no offer).
            return 404, {"detail": "Not Found"}
        if path == "/v1/capabilities" and ("GET", path) not in self.routes:
            # A deploy whose capabilities the test did not set: read as an older server.
            return 404, {"detail": "Not Found"}
        try:
            return 200, self._answer("GET", path, None)
        except SystemExit as exc:
            return 409, {"error": {"code": str(exc.code)}}

    def post(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """POST."""
        return self._answer("POST", path, body, idempotency_key)

    def post_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]:
        """POST without raising; a route may answer ``(status, body)`` for a refusal."""
        answer = self._answer("POST", path, body, idempotency_key)
        if isinstance(answer, tuple):
            return answer
        return 202, answer

    def patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """PATCH."""
        return self._answer("PATCH", path, body)

    def put(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """PUT."""
        return self._answer("PUT", path, body, idempotency_key)

    def put_optional(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, Any]:
        """PUT without raising; a route may answer ``(status, body)`` for a refusal."""
        answer = self._answer("PUT", path, body, idempotency_key)
        if isinstance(answer, tuple):
            return answer
        return 200, answer

    def delete(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """DELETE."""
        return self._answer("DELETE", path, body)

    def delete_optional(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[int, Any]:
        """DELETE without raising; a route may answer ``(status, body)`` for a refusal; unset is a 404."""
        if ("DELETE", path) not in self.routes:
            self.calls.append(("DELETE", path, copy.deepcopy(body), idempotency_key))
            return 404, {"detail": "Not Found"}
        answer = self._answer("DELETE", path, body, idempotency_key)
        if isinstance(answer, tuple):
            return answer
        return 200, answer

    def spine(self, spine_id: str) -> dict[str, Any]:
        """GET the spine."""
        return self.get(f"/v1/spines/{spine_id}")

    def save(self, name: str, payload: Any) -> None:
        """Write one artefact."""
        (self.out / name).write_text(json.dumps(payload, default=str), encoding="utf-8")

    def emit(self, phase: str, **payload: Any) -> None:
        """Log one event."""
        self.events.append((phase, payload))

    def poll_job(
        self, job_id: str, *, label: str, video_route: bool, deadline_seconds: float
    ) -> dict[str, Any]:
        """Return the job's terminal payload."""
        self.polled.append((job_id, video_route))
        if video_route and job_id not in self.jobs:
            # Server captions are on by default, so a film waits for the episode the server finished.
            return {"job_id": job_id, "status": "completed"}
        return copy.deepcopy(self.jobs[job_id])

    def posted(self, path: str) -> list[dict[str, Any] | None]:
        """Bodies POSTed to ``path``."""
        return [
            body
            for method, called, body, _ in self.calls
            if method == "POST" and called == path
        ]


def serve_outfits(api: FakeApi, spine_id: str = "sp1") -> None:
    """Answer ``POST``/``GET /v1/spines/{id}/outfits`` like fictora-drama's outfit plan, on ``api.spine_doc``.

    Adds or rewords an outfit (at most four), plans one for episodes (tagging every written shot of the
    person, ``pending_episode_ordinals`` for episodes with none), removes an unused one, and answers the
    server's refusals (``cast_not_drawn``, ``unknown_wardrobe_variant``, ``wardrobe_variant_limit``,
    ``outfit_in_use``, ``spine_version_conflict``) as ``(status, envelope)``.
    """

    from creation.spine_view import board_assets, frames_by_set

    path = f"/v1/spines/{spine_id}/outfits"

    def refuse(status: int, code: str, **details: Any) -> tuple[int, dict[str, Any]]:
        return status, {"error": {"code": code, "message": code, "details": details}}

    def ordinals(doc: dict[str, Any]) -> list[int]:
        return sorted(int(s["ordinal"]) for s in doc.get("episode_summaries") or [])

    def shots(
        doc: dict[str, Any], cast_id: str, number: int
    ) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
        rows = []
        for take, frames in sorted(frames_by_set(doc, episode=number).items()):
            for frame in frames:
                for entry in (frame.get("visual_brief") or {}).get(
                    "subject_blocking"
                ) or []:
                    if entry.get("cast_id") == cast_id:
                        rows.append((take, frame, entry))
        return rows

    def change(_m: str, _p: str, body: dict[str, Any] | None) -> Any:
        body = body or {}
        doc = api.spine_doc if not body.get("preview") else copy.deepcopy(api.spine_doc)
        if body.get("spine_version") != doc.get("spine_version"):
            return refuse(
                409,
                "spine_version_conflict",
                latest_spine_version=doc.get("spine_version"),
            )
        card = next(
            (c for c in doc["cast"] if c["cast_id"] == body.get("cast_id")), None
        )
        if card is None:
            return refuse(404, "cast_not_found")
        brief = card.get("visual_brief")
        if not isinstance(brief, dict):
            return refuse(400, "cast_not_drawn")
        variants = brief.setdefault("wardrobe_variants", [])
        plan = doc.setdefault("outfit_plan", [])
        mine = [p for p in plan if p["cast_id"] == card["cast_id"]]
        changed = False
        sheet = False
        result: dict[str, Any] = {
            "shots_changed": [],
            "pending_episode_ordinals": [],
            "boards_to_redraw": [],
        }
        if body.get("remove"):
            label = body["remove"].casefold()
            used_eps = sorted(
                {
                    n
                    for p in mine
                    if p["wardrobe_variant"].casefold() == label
                    for n in p["episode_ordinals"]
                }
                | {
                    n
                    for n in ordinals(doc)
                    for _, _, entry in shots(doc, card["cast_id"], n)
                    if (entry.get("wardrobe_variant") or "").casefold() == label
                }
            )
            if used_eps:
                return refuse(
                    409, "outfit_in_use", episode_ordinals=used_eps, frame_ids=[]
                )
            before = len(variants)
            variants[:] = [v for v in variants if v["label"].casefold() != label]
            changed = len(variants) != before
        if body.get("add"):
            add = body["add"]
            have = next(
                (
                    v
                    for v in variants
                    if v["label"].casefold() == add["label"].casefold()
                ),
                None,
            )
            if have is None:
                if len(variants) >= 4:
                    return refuse(409, "wardrobe_variant_limit", limit=4)
                variants.append(
                    {"label": add["label"], "instruction": add["instruction"]}
                )
                changed = sheet = True
            elif have["instruction"] != add["instruction"]:
                have["instruction"] = add["instruction"]
                changed = sheet = True
        if body.get("wear"):
            wear = body["wear"]
            label = wear.get("wardrobe_variant")
            if label is not None:
                have = next(
                    (v for v in variants if v["label"].casefold() == label.casefold()),
                    None,
                )
                if have is None:
                    return refuse(
                        400,
                        "unknown_wardrobe_variant",
                        available=[v["label"] for v in variants],
                    )
                label = have["label"]
            numbers = sorted(set(wear["episode_ordinals"]))
            for p in mine:
                p["episode_ordinals"] = [
                    n for n in p["episode_ordinals"] if n not in numbers
                ]
            if label is not None:
                entry = next((p for p in mine if p["wardrobe_variant"] == label), None)
                if entry is None:
                    entry = {
                        "cast_id": card["cast_id"],
                        "wardrobe_variant": label,
                        "episode_ordinals": [],
                    }
                    plan.append(entry)
                entry["episode_ordinals"] = sorted(
                    set(entry["episode_ordinals"]) | set(numbers)
                )
            plan[:] = [p for p in plan if p["episode_ordinals"]]
            for number in numbers:
                if not frames_by_set(doc, episode=number):
                    result["pending_episode_ordinals"].append(number)
                    continue
                drawn = {take for take, _ in board_assets(doc, episode=number)}
                for take, frame, entry in shots(doc, card["cast_id"], number):
                    before = entry.get("wardrobe_variant")
                    if (before or "").casefold() == (label or "").casefold():
                        continue
                    entry["wardrobe_variant"] = label
                    changed = True
                    result["shots_changed"].append(
                        {
                            "episode_ordinal": number,
                            "frame_id": frame["frame_id"],
                            "set_index": take,
                            "before": before,
                            "after": label,
                        }
                    )
                    if (
                        take in drawn
                        and {"episode_ordinal": number, "set_index": take}
                        not in result["boards_to_redraw"]
                    ):
                        result["boards_to_redraw"].append(
                            {"episode_ordinal": number, "set_index": take}
                        )
        previous = doc["spine_version"]
        if changed and not body.get("preview"):
            doc["spine_version"] = f"{previous}+"
        return 200, {
            "request_id": "req_1",
            "spine_id": spine_id,
            "cast_id": card["cast_id"],
            "applied": changed and not body.get("preview"),
            "preview": bool(body.get("preview")),
            "previous_spine_version": previous,
            "spine_version": doc["spine_version"],
            "outfits": copy.deepcopy(variants),
            "plan": [p for p in plan if p["cast_id"] == card["cast_id"]],
            "sheet_needs_redraw": sheet,
            **result,
        }

    def view(_m: str, _p: str, _b: Any) -> dict[str, Any]:
        doc = api.spine_doc
        cast = []
        for card in doc["cast"]:
            brief = card.get("visual_brief")
            if not isinstance(brief, dict):
                continue
            episodes = []
            for number in ordinals(doc):
                if not frames_by_set(doc, episode=number):
                    continue
                counts: dict[str, int] = {}
                for _, _, entry in shots(doc, card["cast_id"], number):
                    key = entry.get("wardrobe_variant") or "base"
                    counts[key] = counts.get(key, 0) + 1
                episodes.append({"episode_ordinal": number, "shots_by_outfit": counts})
            cast.append(
                {
                    "cast_id": card["cast_id"],
                    "name": card.get("name") or card["cast_id"],
                    "base_outfit": list(brief.get("wardrobe_anchors") or []),
                    "outfits": copy.deepcopy(brief.get("wardrobe_variants") or []),
                    "plan": [
                        p
                        for p in doc.get("outfit_plan") or []
                        if p["cast_id"] == card["cast_id"]
                    ],
                    "episodes": episodes,
                }
            )
        return {
            "spine_id": spine_id,
            "spine_version": doc["spine_version"],
            "cast": cast,
        }

    api.routes[("POST", path)] = change
    api.routes[("GET", path)] = view
