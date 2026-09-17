from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

ENTITY_SUFFIXES = (
    " LLC", " INC", " CORP", " LTD", " LP", " LLP", " TRUST", " ESTATE",
    " ET AL", " ET UX", " ET VIR", " AKA", " D/B/A", " DB/A",
)


def write_private_text(path: Path, text: str) -> None:
    """Create owner-only artifacts/exports, including when called outside the CLI."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)


def case_dedupe_key(county_fips: str, case_number: str) -> str:
    raw = f"{county_fips}:{case_number.strip().upper()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def property_dedupe_key(apn: str) -> str:
    return hashlib.sha256(apn.strip().upper().encode()).hexdigest()[:32]


def normalize_defendant(name: str) -> str:
    n = name.upper().strip()
    n = re.sub(r"[^\w\s&/-]", " ", n)
    for suffix in ENTITY_SUFFIXES:
        n = n.replace(suffix, " ")
    n = re.sub(r"\s+", " ", n).strip()
    return n


def is_entity_defendant(name: str) -> bool:
    upper = name.upper()
    markers = (" LLC", " INC", " CORP", " TRUST", " ESTATE", " LP", " LLP")
    return any(m in upper for m in markers)


def plaintiff_is_tax_suit(plaintiff: str, terms: Iterable[str]) -> bool:
    pl = plaintiff.upper()
    if any(t.upper() in pl for t in terms):
        if any(k in pl for k in ("TAX", "COLLECTOR", "ASSESSOR", "DELINQ")):
            return True
        if "COUNTY" in pl:
            return True
    return False


def case_type_matches(case_type: str, filters: Iterable[str] | None) -> bool:
    """True when configured case_type_filter allows this source type.

    Empty filters mean no type restriction. Blank source types are kept so
    plaintiff matching remains the primary scope when the clerk omits type.
    Otherwise a filter term must appear as a case-insensitive substring of
    the source type (so 'Tax' matches 'TAX' / 'Tax Delinquent', 'OCV' matches 'OCV').
    """
    terms = [str(term).strip() for term in (filters or []) if str(term).strip()]
    if not terms:
        return True
    value = (case_type or "").strip()
    if not value:
        return True
    upper = value.upper()
    return any(term.upper() in upper for term in terms)


def parse_since_days(since: str) -> int:
    since = since.strip().lower()
    if since.endswith("d"):
        return int(since[:-1])
    if since.endswith("w"):
        return int(since[:-1]) * 7
    return int(since)


def parse_money(raw: Any) -> Optional[float]:
    """Parse '$280,620' / 280620 / '280620.00' into a float; empty/junk → None."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in ("", ".", "-", "-."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None
