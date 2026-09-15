from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

from leads.models import PropertyRecord
from leads.http import SourceChangedError, SourceSession
from leads.utils import parse_money


@dataclass
class ArcGISConfig:
    mapserver_url: str
    owner_field: str = "owner_name_1"
    apn_field: str = "HCAD_NUM"
    address_fields: tuple[str, ...] = ("site_str_num", "site_str_name", "site_city", "site_zip")
    assessor_url_template: str = "https://public.hcad.org/records/RealDetail.asp?acct={apn}"


def _first_attr(attrs: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        val = attrs.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


class ArcGISOwnerSearch:
    """Query county parcel MapServer by owner name (UrbanKit/assessor-lookup pattern)."""

    def __init__(self, config: ArcGISConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or SourceSession()
        self.session.headers.setdefault("User-Agent", "re-tax-leads/0.1")

    def _query_url(self, params: dict[str, Any]) -> str:
        return f"{self.config.mapserver_url}/query?{urlencode(params)}"

    def search_by_owner(self, name: str, *, limit: int = 25) -> tuple[list[dict[str, Any]], str, int]:
        if not name.strip():
            return [], "", 0
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
        resp.raise_for_status()
        features = self._features(resp.json())
        rows = [f.get("attributes") or {} for f in features]
        return rows, url, resp.status_code

    def get_by_apn(self, apn: str) -> tuple[dict[str, Any] | None, str, int]:
        field = self.config.apn_field
        token = apn.strip().replace("'", "''")
        where = f"{field} = '{token}'"
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": "1",
        }
        url = self._query_url(params)
        resp = self.session.get(url, timeout=60)
        resp.raise_for_status()
        features = self._features(resp.json())
        if not features:
            return None, url, resp.status_code
        return features[0].get("attributes") or {}, url, resp.status_code

    def _features(self, data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict) or "error" in data or not isinstance(data.get("features"), list):
            raise SourceChangedError("ArcGIS error or missing features array")
        if data.get("exceededTransferLimit"):
            raise SourceChangedError("ArcGIS results truncated; narrow lookup or use bulk data")
        for feature in data["features"]:
            attrs = feature.get("attributes") if isinstance(feature, dict) else None
            if not isinstance(attrs, dict) or not attrs.get(self.config.apn_field) or not attrs.get(self.config.owner_field):
                raise SourceChangedError("ArcGIS required parcel fields missing")
        return data["features"]

    def attrs_to_property(
        self,
        attrs: dict[str, Any],
        *,
        case_id: int,
        match_confidence: float,
    ) -> PropertyRecord:
        street = " ".join(str(attrs.get(key) or "").strip() for key in ("site_str_num", "site_str_name")).strip()
        city = str(attrs.get("site_city") or "").strip()
        zip_code = str(attrs.get("site_zip") or "").strip()
        situs = ", ".join(part for part in (street, city, f"TX {zip_code}".strip()) if part)
        apn = str(attrs.get(self.config.apn_field) or "")
        owner = str(attrs.get(self.config.owner_field) or "")
        assessor_url = self.config.assessor_url_template.format(apn=apn)
        property_type = _first_attr(
            attrs,
            ("property_type", "state_cd", "STATE_CD", "PropType", "Property_Class", "class"),
        )
        total_value = None
        for key in ("total_value", "TotVal", "TOT_VAL", "market_value", "FCV_CUR", "ImprVal"):
            parsed = parse_money(attrs.get(key))
            if parsed is not None:
                total_value = parsed
                break

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
            property_type=property_type,
            total_value=total_value,
        )
