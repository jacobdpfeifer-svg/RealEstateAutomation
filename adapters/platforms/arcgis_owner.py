from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

from leads.models import PropertyRecord


@dataclass
class ArcGISConfig:
    mapserver_url: str
    owner_field: str = "owner_name_1"
    apn_field: str = "HCAD_NUM"
    address_fields: tuple[str, ...] = ("site_str_num", "site_str_name", "site_city", "site_zip")
    assessor_url_template: str = "https://public.hcad.org/records/RealDetail.asp?acct={apn}"


class ArcGISOwnerSearch:
    """Query county parcel MapServer by owner name (UrbanKit/assessor-lookup pattern)."""

    def __init__(self, config: ArcGISConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "re-tax-leads/0.1")

    def _query_url(self, params: dict[str, Any]) -> str:
        return f"{self.config.mapserver_url}/query?{urlencode(params)}"

    def search_by_owner(self, name: str, *, limit: int = 25) -> tuple[list[dict[str, Any]], str, int]:
        token = name.strip().replace("'", "''")
        where = f"UPPER({self.config.owner_field}) LIKE UPPER('%{token}%')"
        out_fields = ",".join(
            {self.config.apn_field, self.config.owner_field, *self.config.address_fields}
        )
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": str(limit),
        }
        url = self._query_url(params)
        resp = self.session.get(url, timeout=60)
        data = resp.json()
        features = data.get("features") or []
        rows = [f.get("attributes") or {} for f in features]
        return rows, url, resp.status_code

    def get_by_apn(self, apn: str) -> tuple[dict[str, Any] | None, str, int]:
        field = self.config.apn_field
        where = f"{field} = '{apn.strip()}'"
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": "1",
        }
        url = self._query_url(params)
        resp = self.session.get(url, timeout=60)
        data = resp.json()
        features = data.get("features") or []
        if not features:
            return None, url, resp.status_code
        return features[0].get("attributes") or {}, url, resp.status_code

    def attrs_to_property(
        self,
        attrs: dict[str, Any],
        *,
        case_id: int,
        match_confidence: float,
    ) -> PropertyRecord:
        parts = []
        num = attrs.get("site_str_num")
        street = attrs.get("site_str_name") or ""
        if num not in (None, ""):
            parts.append(str(num))
        if street:
            parts.append(str(street))
        city = attrs.get("site_city") or ""
        zip_code = attrs.get("site_zip") or ""
        if city:
            parts.append(str(city))
        if zip_code:
            parts.append(str(zip_code))
        situs = ", ".join(p for p in parts if p)
        apn = str(attrs.get(self.config.apn_field) or "")
        owner = str(attrs.get(self.config.owner_field) or "")
        assessor_url = self.config.assessor_url_template.format(apn=apn)
        from leads.utils import property_dedupe_key

        return PropertyRecord(
            id=None,
            case_id=case_id,
            apn=apn,
            situs_address=situs,
            owner_of_record=owner,
            tax_delinquent_amt=None,
            years_delinquent=None,
            assessor_url=assessor_url,
            match_confidence=match_confidence,
        )
