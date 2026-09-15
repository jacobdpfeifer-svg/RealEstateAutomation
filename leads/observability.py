"""Small, PII-free JSON events for cron stderr; no external logging service."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("leads.operations")


def configure_logging() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def event(name: str, **fields) -> None:
    logger.info(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "event": name, **fields}))
