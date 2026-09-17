from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

from adapters.platforms.dallas_odyssey import (
    DallasOdysseyClient,
    load_fixture_cases,
)
from adapters.registry import CountyConfig
from leads.models import CaseRecord


class BexarClerkAdapter:
    """
    Bexar County courts via Tyler Odyssey Smart Search.

    Default input is saved Smart Search HTML. Live portal POSTs are off unless
    BEXAR_CLERK_LIVE_ENABLED=1 and the operator has confirmed permission.
    An AWS WAF challenge or a missing landing-page CAPTCHA is not permission.
    """

    county_fips = "48029"
    plaintiff_search_term = "BEXAR COUNTY"

    def __init__(self, cfg: CountyConfig, artifact_dir: Path | None = None) -> None:
        self.cfg = cfg
        portal = str(cfg.raw.get("clerk_portal_url") or "https://portal-txbexar.tylertech.cloud/Portal/").rstrip("/")
        self.portal_url = portal
        root = Path(__file__).resolve().parents[2]
        self.fixture_dir = Path(
            os.environ.get(
                "BEXAR_CLERK_FIXTURE_DIR",
                str(artifact_dir or root / "artifacts" / "raw" / "bexar" / "clerk"),
            )
        )
        recaptcha_raw = cfg.raw.get("odyssey_recaptcha_required")
        if recaptcha_raw is None:
            recaptcha_required = False
        elif isinstance(recaptcha_raw, bool):
            recaptcha_required = recaptcha_raw
        else:
            recaptcha_required = str(recaptcha_raw).strip().lower() in ("1", "true", "yes")
        self.client = DallasOdysseyClient(
            portal_base=self.portal_url,
            dashboard_path=str(cfg.raw.get("odyssey_dashboard_path") or "/Home/Dashboard/29"),
            search_path=str(cfg.raw.get("odyssey_search_path") or "/SmartSearch/SmartSearch/SmartSearch"),
            warmup_path=str(cfg.raw.get("odyssey_warmup_path") or "/"),
            recaptcha_required=recaptcha_required,
        )
        self._cache: list[CaseRecord] = []

    def _can_live_search(self) -> bool:
        return os.environ.get("BEXAR_CLERK_LIVE_ENABLED", "") == "1"

    def search_tax_suits(self, since: date) -> list[CaseRecord]:
        terms = self.cfg.plaintiff_terms or [self.plaintiff_search_term]
        cases: list[CaseRecord] = []

        fixture_cases = load_fixture_cases(
            self.fixture_dir,
            since=since,
            county_fips=self.county_fips,
            plaintiff_terms=terms,
            case_type_filter=self.cfg.case_type_filter,
        )
        if fixture_cases:
            cases.extend(fixture_cases)

        if self._can_live_search():
            live = self.client.search_tax_suits(
                since,
                terms,
                county_fips=self.county_fips,
                case_type_filter=self.cfg.case_type_filter,
            )
            seen = {c.case_number for c in cases}
            for case in live:
                if case.case_number not in seen:
                    cases.append(case)
                    seen.add(case.case_number)
        elif not cases and not any(self.fixture_dir.glob("*.html")):
            raise RuntimeError(
                "Bexar ClerkAdapter: no fixtures found and live Odyssey search is "
                "disabled by default. Save Smart Search HTML under "
                f"{self.fixture_dir}. Portal: {self.portal_url}"
            )

        self._cache = cases
        return cases

    def fetch_case_detail(self, case_number: str) -> CaseRecord | None:
        if not self._cache:
            self.search_tax_suits(date.today() - timedelta(days=365))
        for case in self._cache:
            if case.case_number == case_number:
                return case
        return None
