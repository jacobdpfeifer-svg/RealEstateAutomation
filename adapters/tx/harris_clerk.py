from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from adapters.platforms.harris_bulk_civil import HarrisBulkDownloader
from adapters.registry import CountyConfig
from leads.models import CaseRecord


class HarrisClerkAdapter:
    county_fips = "48201"
    plaintiff_search_term = "HARRIS COUNTY"

    def __init__(self, cfg: CountyConfig, artifact_dir: Path | None = None) -> None:
        self.cfg = cfg
        self.downloader = HarrisBulkDownloader(artifact_dir=artifact_dir)
        self._cache: list[CaseRecord] = []
        self.fetch_logs: list[dict] = []

    def search_tax_suits(self, since: date) -> list[CaseRecord]:
        cases, self.fetch_logs = self.downloader.fetch_summaries_since(
            since=since,
            plaintiff_terms=self.cfg.plaintiff_terms,
            county_fips=self.county_fips,
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
