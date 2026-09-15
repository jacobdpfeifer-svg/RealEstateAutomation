from __future__ import annotations

from rapidfuzz import fuzz

from adapters.platforms.arcgis_owner import ArcGISConfig, ArcGISOwnerSearch
from adapters.registry import CountyConfig
from leads.models import PropertyRecord


class HarrisTaxAdapter:
    county_fips = "48201"

    def __init__(self, cfg: CountyConfig) -> None:
        self.cfg = cfg
        arcgis_url = cfg.raw.get(
            "arcgis_url",
            "https://www.gis.hctx.net/arcgis/rest/services/HCAD/Parcels/MapServer/0",
        )
        self.client = ArcGISOwnerSearch(
            ArcGISConfig(
                mapserver_url=arcgis_url,
                owner_field=cfg.raw.get("owner_field", "owner_name_1"),
                apn_field=cfg.raw.get("apn_field", "HCAD_NUM"),
            )
        )
        self._match_threshold = 0.85

    def _score(self, query: str, owner: str) -> float:
        q = query.upper().strip()
        o = owner.upper().strip()
        if not q or not o:
            return 0.0
        return fuzz.token_set_ratio(q, o) / 100.0

    def search_by_owner(self, name: str) -> list[PropertyRecord]:
        rows, _url, _status = self.client.search_by_owner(name)
        props: list[PropertyRecord] = []
        for attrs in rows:
            owner = str(attrs.get(self.client.config.owner_field) or "")
            score = self._score(name, owner)
            if score < 0.5:
                continue
            props.append(
                self.client.attrs_to_property(attrs, case_id=0, match_confidence=score)
            )
        props.sort(key=lambda p: p.match_confidence, reverse=True)
        return props

    def get_by_apn(self, apn: str) -> PropertyRecord | None:
        attrs, _url, _status = self.client.get_by_apn(apn)
        if not attrs:
            return None
        return self.client.attrs_to_property(attrs, case_id=0, match_confidence=1.0)
