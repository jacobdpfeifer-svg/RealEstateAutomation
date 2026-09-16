#!/usr/bin/env python3
"""Probe county data sources and record bootstrap findings.

Hits the catalog in config/probes.toml and classifies each URL as
bulk_dataset > api > portal_scrape. Do not write a portal scraper until
bulk and API access have been ruled out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.registry import load_counties
from leads.probe import catalog_keys, load_probe_catalog, probe_keys


NEXT_METROS = ("tarrant", "bexar", "maricopa")


def main() -> int:
    keys = catalog_keys()
    parser = argparse.ArgumentParser(description="Bootstrap county portal probes")
    parser.add_argument(
        "--county",
        default="all",
        help="County key, 'all' (catalog), or 'next' (Tarrant/Bexar/Maricopa)",
    )
    parser.add_argument("--output", "-o", default=None, help="Write JSON report to file")
    args = parser.parse_args()

    county = args.county.lower().strip()
    if county == "all":
        selected = keys
    elif county == "next":
        selected = [k for k in NEXT_METROS if k in keys]
    elif county in keys:
        selected = [county]
    else:
        print(f"Unknown county {county!r}. Known: {', '.join(keys)}, all, next", file=sys.stderr)
        return 2

    report: dict = {"counties_config": [], "selected": selected}
    seen: set[str] = set()
    for cfg in load_counties().values():
        if cfg.fips in seen:
            continue
        seen.add(cfg.fips)
        report["counties_config"].append(
            {
                "fips": cfg.fips,
                "name": cfg.name,
                "enabled": cfg.enabled,
                "ingest": cfg.ingest,
                "tax_lookup": cfg.tax_lookup,
            }
        )

    probes = probe_keys(selected)
    report["probes"] = probes
    report["catalog"] = [
        {"key": row["key"], "fips": row["fips"], "name": row["name"]}
        for row in load_probe_catalog()
    ]

    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
