from __future__ import annotations

import os
from pathlib import Path


def load_secrets(path: Path | None = None) -> dict[str, str]:
    """
    Load KEY=VALUE pairs from config/secrets.env into os.environ (without
    overwriting keys already present). Returns the loaded mapping.
    """
    root = Path(__file__).resolve().parent.parent
    secrets_path = path or root / "config" / "secrets.env"
    loaded: dict[str, str] = {}
    if not secrets_path.exists():
        return loaded
    for line in secrets_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        loaded[key] = value
        if key not in os.environ or os.environ.get(key, "") == "":
            os.environ[key] = value
    return loaded
