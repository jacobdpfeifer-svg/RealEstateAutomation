"""Classify county clerk/tax URLs as bulk_dataset > api > portal_scrape."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import requests

from leads.http import SourceBlockedError, SourceSession, safe_error

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

USER_AGENT = "re-tax-leads/0.1 bootstrap"
METHOD_RANK = {"bulk_dataset": 0, "api": 1, "portal_scrape": 2, "unknown": 3, "error": 4}
OWNER_FIELD_HINTS = ("owner_name", "ownername", "owner", "own_name")
APN_FIELD_HINTS = ("apn", "hcad", "acct", "account", "propid", "geoid", "parcel")
VALUE_FIELD_HINTS = (
    "totval",
    "total_value",
    "total_valu",
    "appraised",
    "market",
    "fcv",
    "tot_val",
    "value",
)
TYPE_FIELD_HINTS = (
    "property_type",
    "proptype",
    "parceltype",
    "state_cd",
    "class",
    "landuse",
    "use_code",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_probe_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    catalog_path = path or _project_root() / "config" / "probes.toml"
    data = tomllib.loads(catalog_path.read_text(encoding="utf-8"))
    return list(data.get("probe") or [])


def catalog_keys(path: Path | None = None) -> list[str]:
    return [str(row["key"]).lower() for row in load_probe_catalog(path)]


def classify_response(url: str, status: int, content_type: str, text: str) -> dict[str, Any]:
    """Return method + subtype from a fetched public page/API body."""
    snippet = (text or "")[:120000]
    lowered = snippet.lower()
    ct = (content_type or "").lower()
    url_l = (url or "").lower()

    if status >= 400:
        return {"method": "error", "subtype": f"http_{status}"}

    if "json" in ct or snippet.lstrip().startswith("{") or snippet.lstrip().startswith("["):
        try:
            payload = json.loads(snippet)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            fields = payload.get("fields") or []
            field_names = [
                str(f.get("name") or "")
                for f in fields
                if isinstance(f, dict)
            ]
            if (
                payload.get("type") in ("Feature Layer", "Feature Server", "Map Server")
                or field_names
                or payload.get("layers")
            ):
                return {
                    "method": "api",
                    "subtype": "arcgis_rest",
                    "layer": payload.get("name"),
                    "field_count": len(field_names) or len(payload.get("layers") or []),
                    "fields": field_names,
                    "owner_field": _guess_field(field_names, OWNER_FIELD_HINTS),
                    "apn_field": _guess_field(field_names, APN_FIELD_HINTS),
                    "value_field": _guess_field(field_names, VALUE_FIELD_HINTS),
                    "type_field": _guess_field(field_names, TYPE_FIELD_HINTS),
                }
            if any(k in payload for k in ("results", "parcels", "property", "data")):
                return {"method": "api", "subtype": "json_search"}
        return {"method": "api", "subtype": "json"}

    if "downloaddoc" in lowered or "publicdatasets" in lowered or "casesummarymods" in lowered:
        return {"method": "bulk_dataset", "subtype": "clerk_bulk"}
    if any(tok in lowered for tok in ("shapefile", "geodatabase", "data downloads")) and (
        "download" in lowered or ".zip" in lowered or "gis" in url_l
    ):
        return {"method": "bulk_dataset", "subtype": "gis_download"}

    if "g-recaptcha" in lowered or "smartsearch" in lowered:
        return {
            "method": "portal_scrape",
            "subtype": "odyssey",
            "recaptcha_required": "g-recaptcha" in lowered,
            "smart_search": "smartsearch" in lowered,
        }
    if "odyssey" in lowered or "tylertech" in url_l or "publicaccess" in url_l:
        return {"method": "portal_scrape", "subtype": "odyssey"}
    if "__viewstate" in lowered:
        return {"method": "portal_scrape", "subtype": "aspnet"}
    if any(
        tok in lowered
        for tok in ("case search", "search by case", "party name", "business name", "caselookup")
    ):
        return {"method": "portal_scrape", "subtype": "html_search"}

    return {"method": "unknown", "subtype": ""}


def _guess_field(names: list[str], hints: tuple[str, ...]) -> str:
    """Prefer earlier hints over earlier field names.

    Field-order matching made Bexar PropID beat AcctNumb and Tarrant LAND_VALUE
    beat TOTAL_VALU whenever a weaker hint appeared first in the layer.
    """
    lowered = [(n, n.lower()) for n in names if n]
    for hint in hints:
        for n, low in lowered:
            if hint in low:
                return n
    return ""


def _best_method(hits: list[dict[str, Any]], *, role: str = "clerk") -> dict[str, Any] | None:
    viable = [h for h in hits if h.get("method") in METHOD_RANK and h.get("method") != "error"]
    if not viable:
        return hits[0] if hits else None
    return min(viable, key=lambda h: _hit_rank(h, role=role))


def _hit_rank(hit: dict[str, Any], *, role: str) -> float:
    method = hit.get("method") or "unknown"
    subtype = hit.get("subtype") or ""
    rank = float(METHOD_RANK.get(method, 9))
    # Parcel GIS zips are bulk, but they are worse than a queryable owner API
    # for tax lookup (shapefile download ≠ live owner search).
    if role == "tax" and method == "bulk_dataset" and subtype == "gis_download":
        rank = METHOD_RANK["api"] + 0.5
    return rank


def fetch_target(session: requests.Session, url: str, timeout: int = 45) -> dict[str, Any]:
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
    except (requests.RequestException, SourceBlockedError) as exc:
        return {"url": url, "method": "error", "subtype": "request_error", "error": safe_error(exc)}
    classified = classify_response(
        str(resp.url),
        resp.status_code,
        resp.headers.get("Content-Type", ""),
        resp.text or "",
    )
    classified["url"] = url
    classified["final_url"] = str(resp.url)
    classified["status_code"] = resp.status_code
    if classified.get("method") == "bulk_dataset" and classified.get("subtype") == "clerk_bulk":
        summaries = sorted(
            set(
                re.findall(
                    r"DownloadDoc\('Civil\\\\CaseSummaryMods_Daily-(\d{4}-\d{2}-\d{2})\.txt'\)",
                    resp.text or "",
                )
            )
        )
        classified["daily_summaries_available"] = len(summaries)
        classified["latest_summary"] = summaries[-1] if summaries else None
    return classified


def probe_county(entry: dict[str, Any], session: requests.Session | None = None) -> dict[str, Any]:
    sess = session or SourceSession()
    sess.headers.setdefault("User-Agent", USER_AGENT)
    clerk_hits = [fetch_target(sess, row["url"]) for row in (entry.get("clerk") or [])]
    tax_hits = [fetch_target(sess, row["url"]) for row in (entry.get("tax") or [])]
    clerk_best = _best_method(clerk_hits, role="clerk") or {}
    tax_best = _best_method(tax_hits, role="tax") or {}
    return {
        "county": entry.get("name"),
        "key": entry.get("key"),
        "fips": entry.get("fips"),
        "state": entry.get("state"),
        "clerk": clerk_best,
        "tax": tax_best,
        "clerk_candidates": clerk_hits,
        "tax_candidates": tax_hits,
        "recommended": {
            "ingest": [clerk_best.get("method")] if clerk_best.get("method") in ("bulk_dataset", "api", "portal_scrape") else [],
            "tax_lookup": _tax_lookup_values(tax_best),
            "note": (
                "Do not write a portal scraper until bulk/API has been ruled out. "
                "Adapters are not registered until a probe-only county is promoted."
            ),
        },
    }


def _tax_lookup_values(tax_best: dict[str, Any]) -> list[str]:
    method = tax_best.get("method")
    subtype = tax_best.get("subtype")
    if method == "api" and subtype == "arcgis_rest":
        return ["arcgis_rest"]
    if method in ("api", "bulk_dataset", "portal_scrape"):
        return [method]
    return []


def probe_keys(keys: list[str], catalog_path: Path | None = None) -> list[dict[str, Any]]:
    catalog = load_probe_catalog(catalog_path)
    wanted = {k.lower() for k in keys}
    session = SourceSession()
    session.headers["User-Agent"] = USER_AGENT
    out: list[dict[str, Any]] = []
    for entry in catalog:
        if entry.get("key", "").lower() in wanted:
            out.append(probe_county(entry, session=session))
    return out
