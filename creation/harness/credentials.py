"""Credential resolution for hosted drama API harness runs."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from creation.harness.env import load_env_file

DEFAULT_DRAMA_API = "https://fictora-drama-generation-prod-drama.up.railway.app"
RAILWAY_DRAMA_SERVICE = "fictora-drama-generation"


def load_drama_api_credentials(
    repo_root: Path,
    *,
    default_base_url: str = DEFAULT_DRAMA_API,
    extra_env_files: tuple[Path, ...] = (),
) -> tuple[str, str]:
    """Resolve drama generation API base URL and bearer token.

    Parameters
    ----------
    repo_root
        Drama repository root (loads ``repo_root / \".env\"`` when present).
    default_base_url
        Base URL when ``FICTORA_DRAMA_GENERATION_API_BASE_URL`` is unset.
    extra_env_files
        Additional dotenv paths loaded without overriding existing keys.

    Returns
    -------
    tuple[str, str]
        ``(base_url, service_token)``.

    Raises
    ------
    SystemExit
        When no token is available from env or Railway CLI.
    """
    load_env_file(repo_root / ".env")
    for path in extra_env_files:
        load_env_file(path)
    base_url = os.environ.get("FICTORA_DRAMA_GENERATION_API_BASE_URL", "").strip() or default_base_url
    token = os.environ.get("FICTORA_DRAMA_GENERATION_SERVICE_TOKEN", "").strip()
    if token:
        return base_url, token
    completed = subprocess.run(
        ["railway", "variables", "--service", RAILWAY_DRAMA_SERVICE, "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SystemExit("FICTORA_DRAMA_GENERATION_SERVICE_TOKEN must be set")
    raw = json.loads(completed.stdout)
    token = (raw.get("FICTORA_DRAMA_GENERATION_SERVICE_TOKEN") or "").strip()
    if not token:
        tokens = json.loads(raw.get("FICTORA_DRAMA_GENERATION_SERVICE_TOKENS_JSON") or "{}")
        if not tokens:
            raise SystemExit("FICTORA_DRAMA_GENERATION_SERVICE_TOKEN must be set")
        token = next(iter(tokens))
    return base_url, token
