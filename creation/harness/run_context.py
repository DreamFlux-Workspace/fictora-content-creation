"""Persist drama session id beside a desk or e2e run (spines are session-scoped)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from creation.harness.http_util import resolve_session_id


def meta_path_for_api_dir(api_dir: Path) -> Path:
    """Return ``run_meta.json`` path one level above ``api/``."""

    return api_dir.parent / "run_meta.json"


def load_run_meta(api_dir: Path) -> dict[str, Any]:
    """Load saved session and spine ids when present."""

    path = meta_path_for_api_dir(api_dir)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_run_meta(
    api_dir: Path,
    *,
    session_id: str,
    spine_id: str | None = None,
    preset_id: str | None = None,
    preset_version: str | None = None,
) -> Path:
    """Write session binding so later steps and resumes hit the same spine."""

    path = meta_path_for_api_dir(api_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"session_id": session_id}
    if spine_id:
        payload["spine_id"] = spine_id
    if preset_id:
        payload["preset_id"] = preset_id
    if preset_version:
        payload["preset_version"] = preset_version
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_session_for_run(api_dir: Path) -> str:
    """Pick session id from env, run_meta, or a new harness id."""

    import os

    meta = load_run_meta(api_dir)
    from_env = (os.environ.get("FICTORA_DRAMA_GENERATION_SESSION_ID") or "").strip()
    if from_env:
        return from_env
    if meta.get("session_id"):
        return str(meta["session_id"])
    return resolve_session_id(env_session=None)
