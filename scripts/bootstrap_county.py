#!/usr/bin/env python3
"""Probe county data sources and record bootstrap findings."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.registry import load_counties


def probe_harris() -> dict:
    session = requests.Session()
    session.headers["User-Agent"] = "re-tax-leads/0.1 bootstrap"
    bulk_url = "https://www.hcdistrictclerk.com/common/e-services/PublicDatasets.aspx"
    page = session.get(bulk_url, timeout=60)
    summaries = re.findall(
        r"DownloadDoc\('Civil\\\\CaseSummaryMods_Daily-(\d{4}-\d{2}-\d{2})\.txt'\)",
        page.text,
    )
    arc_url = "https://www.gis.hctx.net/arcgis/rest/services/HCAD/Parcels/MapServer/0"
    meta = session.get(f"{arc_url}?f=json", timeout=30).json()
    return {
        "county": "Harris",
        "fips": "48201",
        "clerk": {
            "method": "bulk_dataset",
            "url": bulk_url,
            "daily_summaries_available": len(set(summaries)),
            "latest_summary": sorted(set(summaries))[-1] if summaries else None,
            "note": "Use form POST DownloadDoc — do not scrape interactive search",
        },
        "tax": {
            "method": "arcgis_rest",
            "url": arc_url,
            "layer": meta.get("name"),
            "field_count": len(meta.get("fields") or []),
        },
    }


def probe_dallas() -> dict:
    session = requests.Session()
    session.headers["User-Agent"] = "re-tax-leads/0.1 bootstrap"
    portal = session.get("https://courtsportal.dallascounty.org/DALLASPROD/Home/Dashboard/29", timeout=60)
    dcad = session.get("https://www.dallascad.org/searchowner.aspx", timeout=60)
    has_recaptcha = "g-recaptcha" in portal.text
    has_smart_search = "SmartSearch" in portal.text
    return {
        "county": "Dallas",
        "fips": "48113",
        "clerk": {
            "method": "portal_scrape",
            "url": "https://courtsportal.dallascounty.org/DALLASPROD",
            "status_code": portal.status_code,
            "smart_search": has_smart_search,
            "recaptcha_required": has_recaptcha,
            "note": (
                "Tyler Odyssey Smart Search; anonymous use needs reCAPTCHA "
                "(ANTICAPTCHA_API_KEY) or saved HTML fixtures under artifacts/raw/dallas/clerk/"
            ),
        },
        "tax": {
            "method": "portal_scrape",
            "url": "https://www.dallascad.org/searchowner.aspx",
            "status_code": dcad.status_code,
            "aspnet": "__VIEWSTATE" in dcad.text,
            "owner_field": "txtOwnerName" in dcad.text,
            "note": "DCAD ASP.NET owner search is live via requests (no ArcGIS owner API)",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap county portal probes")
    parser.add_argument("--county", choices=["harris", "dallas", "all"], default="all")
    parser.add_argument("--output", default=None, help="Write JSON report to file")
    args = parser.parse_args()

    report: dict = {"counties_config": []}
    for cfg in load_counties().values():
        if cfg.fips not in {c["fips"] for c in report["counties_config"]}:
            report["counties_config"].append(
                {"fips": cfg.fips, "name": cfg.name, "ingest": cfg.ingest, "tax_lookup": cfg.tax_lookup}
            )

    probes = []
    if args.county in ("harris", "all"):
        probes.append(probe_harris())
    if args.county in ("dallas", "all"):
        probes.append(probe_dallas())
    report["probes"] = probes

    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
