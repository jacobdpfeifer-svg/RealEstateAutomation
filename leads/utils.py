from __future__ import annotations

import hashlib
import re
from typing import Iterable

ENTITY_SUFFIXES = (
    " LLC", " INC", " CORP", " LTD", " LP", " LLP", " TRUST", " ESTATE",
    " ET AL", " ET UX", " ET VIR", " AKA", " D/B/A", " DB/A",
)


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


def parse_since_days(since: str) -> int:
    since = since.strip().lower()
    if since.endswith("d"):
        return int(since[:-1])
    if since.endswith("w"):
        return int(since[:-1]) * 7
    return int(since)
