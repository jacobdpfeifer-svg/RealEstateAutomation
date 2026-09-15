from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from adapters.base import ClerkAdapter, TaxAdapter
from providers.skip_trace.base import get_skip_trace_provider


@dataclass
class CountyConfig:
    fips: str
    name: str
    state: str
    enabled: bool
    plaintiff: str
    plaintiff_terms: list[str]
    case_type_filter: list[str]
    ingest: list[str]
    tax_lookup: list[str]
    skip_trace: list[str]
    clerk_adapter: str
    tax_adapter: str
    raw: dict[str, Any]


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_counties(config_path: Path | None = None) -> dict[str, CountyConfig]:
    path = config_path or _project_root() / "config" / "counties.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, CountyConfig] = {}
    for row in data.get("county", []):
        cfg = CountyConfig(
            fips=row["fips"],
            name=row["name"],
            state=row["state"],
            enabled=bool(row.get("enabled", True)),
            plaintiff=row.get("plaintiff", ""),
            plaintiff_terms=list(row.get("plaintiff_terms") or [row.get("plaintiff", "")]),
            case_type_filter=list(row.get("case_type_filter") or []),
            ingest=list(row.get("ingest") or []),
            tax_lookup=list(row.get("tax_lookup") or []),
            skip_trace=list(row.get("skip_trace") or ["stub"]),
            clerk_adapter=str(row.get("clerk_adapter") or ""),
            tax_adapter=str(row.get("tax_adapter") or ""),
            raw=row,
        )
        out[cfg.fips] = cfg
        out[cfg.name.lower()] = cfg
    return out


def _import_class(dotted: str) -> type:
    module_path, class_name = dotted.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def get_clerk_adapter(county_key: str, config_path: Path | None = None) -> ClerkAdapter:
    counties = load_counties(config_path)
    key = county_key.lower()
    if key not in counties:
        raise KeyError(f"Unknown county: {county_key}")
    cfg = counties[key]
    if not cfg.enabled:
        raise RuntimeError(f"County disabled: {cfg.name}")
    if not cfg.clerk_adapter:
        raise RuntimeError(f"No clerk adapter registered for {cfg.name} (probe-only)")
    cls = _import_class(cfg.clerk_adapter)
    return cls(cfg)


def get_tax_adapter(county_key: str, config_path: Path | None = None) -> TaxAdapter:
    counties = load_counties(config_path)
    key = county_key.lower()
    if key not in counties:
        raise KeyError(f"Unknown county: {county_key}")
    cfg = counties[key]
    if not cfg.enabled:
        raise RuntimeError(f"County disabled: {cfg.name}")
    if not cfg.tax_adapter:
        raise RuntimeError(f"No tax adapter registered for {cfg.name} (probe-only)")
    cls = _import_class(cfg.tax_adapter)
    return cls(cfg)


def list_enabled_counties(config_path: Path | None = None) -> list[CountyConfig]:
    counties = load_counties(config_path)
    seen: set[str] = set()
    out: list[CountyConfig] = []
    for cfg in counties.values():
        if cfg.fips in seen:
            continue
        if cfg.enabled:
            seen.add(cfg.fips)
            out.append(cfg)
    return sorted(out, key=lambda c: c.name)


def get_skip_provider_for_county(county_key: str, config_path: Path | None = None):
    counties = load_counties(config_path)
    cfg = counties[county_key.lower()]
    provider_name = cfg.skip_trace[0] if cfg.skip_trace else "stub"
    return get_skip_trace_provider(provider_name)
