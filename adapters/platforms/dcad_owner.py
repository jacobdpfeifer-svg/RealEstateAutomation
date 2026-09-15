from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Any
import requests
from rapidfuzz import fuzz

from leads.models import PropertyRecord

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_OWNER_URL = "https://www.dallascad.org/searchowner.aspx"


@dataclass
class DCADConfig:
    owner_url: str = DEFAULT_OWNER_URL
    detail_url_template: str = "https://www.dallascad.org/AcctDetailRes.aspx?ID={apn}"
    match_threshold: float = 0.5


def _parse_hidden(html: str, name: str) -> str:
    m = re.search(rf'id="{re.escape(name)}"[^>]*value="([^"]*)"', html)
    if not m:
        m = re.search(rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html)
    return m.group(1) if m else ""


def _cell_text(cell_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(cell_html))
    return re.sub(r"\s+", " ", text).strip()


def parse_owner_search_results(html: str) -> list[dict[str, str]]:
    """Parse DCAD SearchResults1_dgResults table into row dicts."""
    table_m = re.search(
        r'<table[^>]*id="SearchResults1_dgResults"[^>]*>(.*?)</table>',
        html,
        re.S | re.I,
    )
    if not table_m:
        return []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_m.group(1), re.S | re.I)
    out: list[dict[str, str]] = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        if len(cells) < 6:
            continue
        plain = [_cell_text(c) for c in cells]
        if plain[0] in ("#", "") or not plain[0].isdigit():
            continue
        apn_m = re.search(r"AcctDetail(?:Res|Com)\.aspx\?ID=([^\"']+)", row, re.I)
        if not apn_m:
            continue
        out.append(
            {
                "apn": apn_m.group(1).strip(),
                "situs_address": plain[1],
                "city": plain[2],
                "owner_of_record": plain[3],
                "total_value": plain[4],
                "property_type": plain[5],
            }
        )
    return out


def parse_acct_detail(html: str, *, apn_fallback: str = "") -> dict[str, str]:
    """Extract owner + situs from an AcctDetailRes/Com page."""
    title = ""
    m = re.search(
        r'id="lblPageTitle"[^>]*>(.*?)</span>',
        html,
        re.S | re.I,
    )
    if m:
        title = _cell_text(m.group(1))
    apn = apn_fallback
    apn_m = re.search(r"Account\s*#\s*([A-Z0-9]+)", title, re.I)
    if apn_m:
        apn = apn_m.group(1)

    addr = ""
    am = re.search(
        r'id="PropAddr1_lblPropAddr"[^>]*>(.*?)</span>',
        html,
        re.S | re.I,
    )
    if am:
        addr = _cell_text(am.group(1))

    owner = ""
    # Owner section header is followed by the owner name as adjacent text/nodes.
    om = re.search(
        r'id="lblOwner"[^>]*>Owner[^<]*</span>\s*([^<]+)',
        html,
        re.S | re.I,
    )
    if om:
        owner = re.sub(r"\s+", " ", unescape(om.group(1))).strip()
    if not owner:
        multi = re.search(
            r"Owner Name.*?<td[^>]*>\s*([^<]+)\s*</td>",
            html,
            re.S | re.I,
        )
        if multi:
            owner = re.sub(r"\s+", " ", unescape(multi.group(1))).strip()

    city_state = ""
    # Mailing/situs city line often follows the street address near the owner block.
    cm = re.search(
        r'id="lblOwner"[^>]*>.*?</span>\s*[^<]+\s*([^<]+,\s*TEXAS\s*\d*)',
        html,
        re.S | re.I,
    )
    if cm:
        city_state = re.sub(r"\s+", " ", unescape(cm.group(1))).strip()

    situs = addr
    if city_state and addr and city_state.upper() not in addr.upper():
        situs = f"{addr}, {city_state}"

    return {
        "apn": apn,
        "situs_address": situs,
        "owner_of_record": owner,
        "city_state": city_state,
    }


def _score(query: str, owner: str) -> float:
    q = query.upper().strip()
    o = owner.upper().strip()
    if not q or not o:
        return 0.0
    return fuzz.token_set_ratio(q, o) / 100.0


class DCADOwnerSearch:
    """Dallas Central Appraisal District ASP.NET owner / account lookup."""

    def __init__(self, config: DCADConfig | None = None, session: requests.Session | None = None) -> None:
        self.config = config or DCADConfig()
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def search_by_owner(self, name: str, *, limit: int = 25) -> tuple[list[dict[str, Any]], str, int]:
        queries = self._query_variants(name)
        last_url = self.config.owner_url
        last_status = 0
        for query in queries:
            rows, last_url, last_status = self._post_owner_search(query, limit=limit)
            if rows:
                return rows, last_url, last_status
        return [], last_url, last_status

    @staticmethod
    def _query_variants(name: str) -> list[str]:
        """DCAD often matches last-name tokens better than full 'LAST FIRST' strings."""
        raw = name.strip()
        if not raw:
            return []
        parts = [p for p in re.split(r"\s+", raw.upper()) if p]
        variants: list[str] = []
        for candidate in (raw, parts[0] if parts else "", parts[-1] if len(parts) > 1 else ""):
            if candidate and candidate not in variants:
                variants.append(candidate)
        return variants

    def _post_owner_search(self, name: str, *, limit: int = 25) -> tuple[list[dict[str, Any]], str, int]:
        import time

        last_status = 0
        for attempt in range(3):
            resp = self.session.get(self.config.owner_url, timeout=60)
            last_status = resp.status_code
            if resp.status_code == 403:
                time.sleep(1.5 * (attempt + 1))
                continue
            resp.raise_for_status()
            fields = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": _parse_hidden(resp.text, "__VIEWSTATE"),
                "__VIEWSTATEGENERATOR": _parse_hidden(resp.text, "__VIEWSTATEGENERATOR"),
                "__EVENTVALIDATION": _parse_hidden(resp.text, "__EVENTVALIDATION"),
                "txtOwnerName": name.strip(),
                "cmdSubmit": "Search",
                # Real property: RESIDENTIAL (commercial optional — dual select can empty results)
                "AcctTypeCheckList1:chkAcctType:0": "on",
            }
            post = self.session.post(self.config.owner_url, data=fields, timeout=60)
            last_status = post.status_code
            if post.status_code == 403:
                time.sleep(1.5 * (attempt + 1))
                continue
            post.raise_for_status()
            rows = parse_owner_search_results(post.text)[:limit]
            return rows, self.config.owner_url, post.status_code
        return [], self.config.owner_url, last_status

    def get_by_apn(self, apn: str) -> tuple[dict[str, str] | None, str, int]:
        url = self.config.detail_url_template.format(apn=apn.strip())
        resp = self.session.get(url, timeout=60)
        if resp.status_code == 404 or "Account #" not in resp.text:
            # Try commercial detail template
            alt = url.replace("AcctDetailRes.aspx", "AcctDetailCom.aspx")
            resp = self.session.get(alt, timeout=60)
            url = alt
        if resp.status_code >= 400 or "Account #" not in resp.text:
            return None, url, resp.status_code
        return parse_acct_detail(resp.text, apn_fallback=apn), url, resp.status_code

    def row_to_property(
        self,
        row: dict[str, Any],
        *,
        case_id: int,
        match_confidence: float,
        query_name: str = "",
    ) -> PropertyRecord:
        owner = str(row.get("owner_of_record") or "")
        city = str(row.get("city") or "")
        street = str(row.get("situs_address") or "")
        situs = street
        if city and city.upper() not in street.upper():
            situs = f"{street}, {city}, TX"
        apn = str(row.get("apn") or "")
        conf = match_confidence
        if query_name and owner:
            conf = _score(query_name, owner)
        return PropertyRecord(
            id=None,
            case_id=case_id,
            apn=apn,
            situs_address=situs,
            owner_of_record=owner,
            tax_delinquent_amt=None,
            years_delinquent=None,
            assessor_url=self.config.detail_url_template.format(apn=apn),
            match_confidence=conf,
        )

    def detail_to_property(
        self,
        detail: dict[str, str],
        *,
        case_id: int,
        match_confidence: float = 1.0,
    ) -> PropertyRecord:
        apn = detail.get("apn") or ""
        return PropertyRecord(
            id=None,
            case_id=case_id,
            apn=apn,
            situs_address=detail.get("situs_address") or "",
            owner_of_record=detail.get("owner_of_record") or "",
            tax_delinquent_amt=None,
            years_delinquent=None,
            assessor_url=self.config.detail_url_template.format(apn=apn),
            match_confidence=match_confidence,
        )
