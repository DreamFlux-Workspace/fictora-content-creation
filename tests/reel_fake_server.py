"""A stand-in for the server's reel engine (:class:`creation.post.reel_server.ReelServer`).

Records every upload and request; answers like the reel route in operator mode:
the "reel" is the first uploaded take's bytes, the cover a few JPEG bytes, the
plan one segment per take ending on the last one's new fact.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from creation.post.reel_server import ReelServerError, ReelServerUnreachable

CAPTION = "The door was never locked.\n\nTiny Show · Part 1: The Door\nFollow for part 2.\n\n#mystery #shortdrama\n"


@dataclass
class FakeReelServer:
    """Duck-types :class:`ReelServer` for ``run_reel(..., server=...)``."""

    unreachable: bool = False
    refuse: str | None = None
    uploads: list[tuple[str, Path]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[tuple[str, str]] = field(default_factory=list)
    files: dict[str, bytes] = field(default_factory=dict)
    reused: bool = False
    closed: bool = False

    def upload(self, path: Path, *, kind: str) -> tuple[str, str]:
        if self.unreachable:
            raise ReelServerUnreachable("ConnectError")
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        url = f"https://assets.test/uploads/{sha[:12]}{path.suffix}"
        self.files[url] = data
        self.uploads.append((kind, path))
        return url, sha

    def make(
        self, job_id: str, episode_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        if self.unreachable:
            raise ReelServerUnreachable("ConnectError")
        if self.refuse:
            raise ReelServerError(f"the server refused the reel: {self.refuse}")
        self.jobs.append((job_id, episode_id))
        self.requests.append(body)
        takes = body["operator"]["takes"]
        first = takes[0]["video_url"]
        self.files["https://assets.test/reels/reel.mp4"] = self.files[first]
        self.files["https://assets.test/reels/cover.jpg"] = b"\xff\xd8\xff\xe0fake-jpeg"
        segments = [
            {
                "take": t["take_id"],
                "start_s": 0.0,
                "end_s": 1.0,
                "role": role,
                "why": "",
            }
            for t, role in zip(
                takes,
                ["cold_open", *["plant"] * (len(takes) - 2), "new_fact"][-len(takes) :],
            )
        ]
        if len(takes) == 1:
            segments = [
                {
                    "take": "t1",
                    "start_s": 3.0,
                    "end_s": 4.5,
                    "role": "cold_open",
                    "why": "flash-forward",
                },
                {
                    "take": "t1",
                    "start_s": 0.0,
                    "end_s": 5.3,
                    "role": "new_fact",
                    "why": "ends on the reveal",
                },
            ]
        if isinstance(body.get("plan"), dict):
            # A hand-edited plan is rendered as it is.
            segments = list(body["plan"].get("segments") or [])
        plan = {
            "kind": "fictora-reel-plan",
            "schema": 1,
            "episode": 1,
            "seconds": body.get("seconds", 15.0),
            "total_s": round(sum(s["end_s"] - s["start_s"] for s in segments), 3),
            "takes": {
                t["take_id"]: {
                    "start_s": 0.0,
                    "duration_s": 6.0,
                    "cues": len(t["cues"]),
                }
                for t in takes
            },
            "strongest": {
                "take": "t1",
                "at_s": 3.5,
                "score": 0.6,
                "parts": {},
                "beat_role": "pivot",
            },
            "segments": segments,
            "ending": {"style": body.get("ending", "hard")},
            "hook_line": {"mode": None, "skipped": "turned off with the request"}
            if body.get("no_hook_line")
            else {
                "mode": "hook",
                "text": body.get("hook_line") or "She never left",
                "placement": "top",
            },
            "captions": f"{sum(len(t['cues']) for t in takes)} caption cue(s), word flicker, house style",
            "loudness": "-18.0 LUFS",
            "cover": "the strongest frame",
            "notes": [],
            "warnings": ["a server warning"],
        }
        return {
            "reel_video_url": "https://assets.test/reels/reel.mp4",
            "cover_url": None
            if body.get("no_cover")
            else "https://assets.test/reels/cover.jpg",
            "caption_text": CAPTION,
            "part": 1,
            "duration_ms": 6800,
            "rules_version": "reel-rules-v1",
            "cached": len(self.requests) > 1,
            "mode": "operator",
            "plan": plan,
            "warnings": ["a server warning"],
            "report": [
                "auto take gain +0.0 dB (take -18.0 LUFS) -> mix -18.0 LUFS (in band, target -18)"
            ],
        }

    def download(self, url: str, dest: Path) -> Path:
        with dest.open("xb") as handle:
            handle.write(self.files[url])
        return dest

    def forget_uploads(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
