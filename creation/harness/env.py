"""Environment loading for harness CLIs."""

from __future__ import annotations

import os
import re
from pathlib import Path


def load_env_file(path: Path) -> None:
    """Load missing environment keys from a dotenv file without overriding existing values.

    Parameters
    ----------
    path
        Dotenv file to read when it exists.
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value).strip()
        os.environ.setdefault(key, value)
