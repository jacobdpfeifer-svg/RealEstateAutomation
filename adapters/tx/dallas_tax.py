from __future__ import annotations

from rapidfuzz import fuzz

from adapters.platforms.dcad_owner import DCADConfig, DCADOwnerSearch
from adapters.registry import CountyConfig
from leads.models import PropertyRecord


class DallasTaxAdapter:
    """Dallas Central Appraisal District owner search (ASP.NET portal_scrape)."""

    county_fips = "48113"

    def __init__(self, cfg: CountyConfig) -> None:
        self.cfg = cfg
        owner_url = cfg.raw.get("dcad_owner_url", "https://www.dallascad.org/searchowner.aspx")
        self.client = DCADOwnerSearch(
            DCADConfig(
                owner_url=owner_url,
                detail_url_template=cfg.raw.get(
                    "dcad_detail_url",
                    "https://www.dallascad.org/AcctDetailRes.aspx?ID={apn}",
                ),
            )
        )
        self._match_threshold = 0.85

    def _score(self, query: str, owner: str) -> float:
        q = query.upper().strip()
        o = owner.upper().strip()
        if not q or not o:
            return 0.0
        return fuzz.token_set_ratio(q, o) / 100.0

    def search_by_owner(self, name: str, *, case_id: int = 0) -> list[PropertyRecord]:
        rows, _url, _status = self.client.search_by_owner(name)
        props: list[PropertyRecord] = []
        for row in rows:
            owner = str(row.get("owner_of_record") or "")
            score = self._score(name, owner)
            if score < 0.5:
                continue
            props.append(
                self.client.row_to_property(
                    row,
                    case_id=case_id,
                    match_confidence=score,
                    query_name=name,
                )
            )
        props.sort(key=lambda p: p.match_confidence, reverse=True)
        return props

    def get_by_apn(self, apn: str, *, case_id: int = 0) -> PropertyRecord | None:
        detail, _url, _status = self.client.get_by_apn(apn)
        if not detail:
            return None
        return self.client.detail_to_property(detail, case_id=case_id, match_confidence=1.0)
