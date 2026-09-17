from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

from adapters.base import owner_match_score
from leads.models import PropertyRecord
from leads.http import SourceChangedError, SourceSession
from leads.utils import parse_money


_TYPE_FALLBACKS = (
    "property_type",
    "parcel_type",
    "state_cd",
    "STATE_CD",
    "State_cd",
    "PropType",
    "Property_Class",
    "class",
    "PARCELTYPE",
    "PropUse",
)
_VALUE_FALLBACKS = (
    "total_value",
    "TotVal",
    "TOT_VAL",
    "TOTAL_VALU",
    "total_market_val",
    "total_appraised_val",
    "market_value",
    "FCV_CUR",
    "ImprVal",
)


@dataclass
class ArcGISConfig:
    mapserver_url: str
    owner_field: str = "owner_name_1"
    apn_field: str = "HCAD_NUM"
    address_fields: tuple[str, ...] = ("site_str_num", "site_str_name", "site_city", "site_zip")
    assessor_url_template: str = "https://public.hcad.org/records/RealDetail.asp?acct={apn}"
    value_field: str = ""
    type_field: str = ""
    situs_state: str = "TX"


def _with_situs_state(address: str, state: str) -> str:
    text = (address or "").strip()
    token = (state or "").strip()
    if not text:
        return ""
    if token and token.upper() not in text.upper():
        return f"{text} {token}".strip()
    return text


def _first_attr(attrs: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        val = attrs.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return ""


def _tuple_fields(raw: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    if isinstance(raw, str) and raw.strip():
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    return fallback


class ArcGISOwnerSearch:
    """Query county parcel MapServer by owner name (UrbanKit/assessor-lookup pattern)."""

    def __init__(self, config: ArcGISConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or SourceSession()
        self.session.headers.setdefault("User-Agent", "re-tax-leads/0.1")

    def _query_url(self, params: dict[str, Any]) -> str:
        return f"{self.config.mapserver_url}/query?{urlencode(params)}"

    def _out_fields(self, *, all_fields: bool = False) -> str:
        if all_fields:
            return "*"
        names = [self.config.apn_field, self.config.owner_field, *self.config.address_fields]
        if self.config.value_field:
            names.append(self.config.value_field)
        if self.config.type_field:
            names.append(self.config.type_field)
        return ",".join(dict.fromkeys(name for name in names if name))

    def search_by_owner(self, name: str, *, limit: int = 25) -> tuple[list[dict[str, Any]], str, int]:
        if not name.strip():
            return [], "", 0
        token = name.strip().replace("'", "''")
        where = f"UPPER({self.config.owner_field}) LIKE UPPER('%{token}%')"
        params = {
            "where": where,
            "outFields": self._out_fields(),
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
            "outFields": self._out_fields(all_fields=True),
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

    def _situs_address(self, attrs: dict[str, Any]) -> str:
        parts = [str(attrs.get(key) or "").strip() for key in self.config.address_fields]
        parts = [part for part in parts if part]
        if not parts:
            return ""
        state = (self.config.situs_state or "").strip()
        if len(parts) == 1:
            return _with_situs_state(parts[0], state)
        if len(parts) == 2:
            return _with_situs_state(", ".join(parts), state)
        zip_code = parts[-1]
        city = parts[-2]
        street = " ".join(parts[:-2]).strip()
        locality = f"{state} {zip_code}".strip() if state else zip_code
        return ", ".join(part for part in (street, city, locality) if part)

    def attrs_to_property(
        self,
        attrs: dict[str, Any],
        *,
        case_id: int,
        match_confidence: float,
    ) -> PropertyRecord:
        situs = self._situs_address(attrs)
        apn = str(attrs.get(self.config.apn_field) or "")
        owner = str(attrs.get(self.config.owner_field) or "")
        assessor_url = self.config.assessor_url_template.format(apn=apn)
        type_keys = (self.config.type_field,) + _TYPE_FALLBACKS if self.config.type_field else _TYPE_FALLBACKS
        property_type = _first_attr(attrs, tuple(dict.fromkeys(type_keys)))
        total_value = None
        value_keys = (self.config.value_field,) + _VALUE_FALLBACKS if self.config.value_field else _VALUE_FALLBACKS
        for key in dict.fromkeys(value_keys):
            if not key:
                continue
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


def config_from_county(cfg: Any, *, defaults: dict[str, Any]) -> ArcGISConfig:
    """Build ArcGISConfig from a CountyConfig (or anything with .raw / .state)."""
    raw = getattr(cfg, "raw", {}) or {}
    state = str(getattr(cfg, "state", "") or raw.get("state") or defaults.get("situs_state") or "TX")
    return ArcGISConfig(
        mapserver_url=str(raw.get("arcgis_url") or defaults["mapserver_url"]),
        owner_field=str(raw.get("owner_field") or defaults.get("owner_field") or "owner_name_1"),
        apn_field=str(raw.get("apn_field") or defaults.get("apn_field") or "HCAD_NUM"),
        address_fields=_tuple_fields(
            raw.get("address_fields"),
            defaults.get("address_fields") or ArcGISConfig.address_fields,
        ),
        assessor_url_template=str(
            raw.get("assessor_url_template")
            or defaults.get("assessor_url_template")
            or ArcGISConfig.assessor_url_template
        ),
        value_field=str(raw.get("value_field") or defaults.get("value_field") or ""),
        type_field=str(raw.get("type_field") or defaults.get("type_field") or ""),
        situs_state=str(raw.get("situs_state") or state),
    )


def properties_from_owner_search(client: ArcGISOwnerSearch, name: str) -> list[PropertyRecord]:
    """Score owner rows and map them to PropertyRecords (shared tax-adapter body)."""
    rows, _url, _status = client.search_by_owner(name)
    props: list[PropertyRecord] = []
    for attrs in rows:
        owner = str(attrs.get(client.config.owner_field) or "")
        score = owner_match_score(name, owner)
        if score < 0.5:
            continue
        props.append(client.attrs_to_property(attrs, case_id=0, match_confidence=score))
    props.sort(key=lambda p: p.match_confidence, reverse=True)
    return props


def property_from_apn(client: ArcGISOwnerSearch, apn: str) -> PropertyRecord | None:
    attrs, _url, _status = client.get_by_apn(apn)
    if not attrs:
        return None
    return client.attrs_to_property(attrs, case_id=0, match_confidence=1.0)
