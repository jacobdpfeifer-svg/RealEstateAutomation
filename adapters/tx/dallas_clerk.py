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


class DallasClerkAdapter:
    """
    Dallas County district/county courts via Tyler Odyssey Smart Search.

    Live portal access requires reCAPTCHA (ANTICAPTCHA_API_KEY or
    DALLAS_RECAPTCHA_TOKEN). When those are unset, the adapter reads saved
    HTML fixtures from artifacts/raw/dallas/clerk/ (or DALLAS_CLERK_FIXTURE_DIR).
    """

    county_fips = "48113"
    plaintiff_search_term = "DALLAS COUNTY"

    def __init__(self, cfg: CountyConfig, artifact_dir: Path | None = None) -> None:
        self.cfg = cfg
        portal = cfg.raw.get(
            "clerk_portal_url",
            "https://courtsportal.dallascounty.org/DALLASPROD",
        )
        # Record-search landing page redirects humans; adapters talk to Odyssey.
        if "dallascounty.org/services/record-search" in portal:
            portal = "https://courtsportal.dallascounty.org/DALLASPROD"
        self.portal_url = portal.rstrip("/")
        root = Path(__file__).resolve().parents[2]
        self.fixture_dir = Path(
            os.environ.get(
                "DALLAS_CLERK_FIXTURE_DIR",
                str(artifact_dir or root / "artifacts" / "raw" / "dallas" / "clerk"),
            )
        )
        self.client = DallasOdysseyClient(portal_base=self.portal_url)
        self._cache: list[CaseRecord] = []

    def _can_live_search(self) -> bool:
        return bool(
            os.environ.get("ANTICAPTCHA_API_KEY", "").strip()
            or os.environ.get("DALLAS_RECAPTCHA_TOKEN", "").strip()
            or os.environ.get("DALLAS_CLERK_FORCE_LIVE", "").strip() == "1"
        )

    def search_tax_suits(self, since: date) -> list[CaseRecord]:
        terms = self.cfg.plaintiff_terms or [self.plaintiff_search_term]
        cases: list[CaseRecord] = []

        fixture_cases = load_fixture_cases(
            self.fixture_dir,
            since=since,
            county_fips=self.county_fips,
            plaintiff_terms=terms,
        )
        if fixture_cases:
            cases.extend(fixture_cases)

        if self._can_live_search():
            live = self.client.search_tax_suits(
                since,
                terms,
                county_fips=self.county_fips,
            )
            seen = {c.case_number for c in cases}
            for case in live:
                if case.case_number not in seen:
                    cases.append(case)
                    seen.add(case.case_number)
        elif not cases:
            raise RuntimeError(
                "Dallas ClerkAdapter: no fixtures found and live Odyssey search is "
                "blocked without ANTICAPTCHA_API_KEY / DALLAS_RECAPTCHA_TOKEN. "
                f"Save Smart Search HTML under {self.fixture_dir} or configure captcha. "
                f"Portal: {self.portal_url}"
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
