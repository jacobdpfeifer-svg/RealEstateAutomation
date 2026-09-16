from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from rapidfuzz import fuzz

from leads.models import CaseRecord, ContactRecord, PropertyRecord


@runtime_checkable
class ClerkAdapter(Protocol):
    county_fips: str
    plaintiff_search_term: str

    def search_tax_suits(self, since: date) -> list[CaseRecord]: ...

    def fetch_case_detail(self, case_number: str) -> CaseRecord | None: ...


@runtime_checkable
class TaxAdapter(Protocol):
    county_fips: str

    def search_by_owner(self, name: str) -> list[PropertyRecord]: ...

    def get_by_apn(self, apn: str) -> PropertyRecord | None: ...


@runtime_checkable
class SkipTraceProvider(Protocol):
    name: str

    def trace(
        self,
        name: str,
        address: str,
        city: str,
        state: str,
        apn: str = "",
    ) -> list[ContactRecord]: ...


def owner_match_score(query: str, owner: str) -> float:
    """Shared owner-name normalization/scoring for county tax adapters."""
    query, owner = query.upper().strip(), owner.upper().strip()
    return fuzz.token_set_ratio(query, owner) / 100.0 if query and owner else 0.0
