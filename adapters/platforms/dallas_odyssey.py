from __future__ import annotations

import os
import re
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Any

import requests

from leads.models import CaseRecord
from leads.http import SourceChangedError, SourceSession
from leads.utils import case_dedupe_key, normalize_defendant, plaintiff_is_tax_suit

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_PORTAL = "https://courtsportal.dallascounty.org/DALLASPROD"
SMART_SEARCH_PATH = "/SmartSearch/SmartSearch/SmartSearch"
DASHBOARD_PATH = "/Home/Dashboard/29"
RECAPTCHA_SITEKEY = "6LfaKq4pAAAAAMldM38bgzOyUGw7iRpHPbt-C8Lg"


def _cell_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(html))
    return re.sub(r"\s+", " ", text).strip()


def parse_smart_search_results(html: str) -> list[dict[str, str]]:
    """
    Parse Tyler Odyssey Smart Search result markup into row dicts.

    Accepts either live portal HTML or saved fixtures. Looks for case-number
    links and nearby party / file-date / type fields.
    """
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(row: dict[str, str]) -> None:
        case_number = (row.get("case_number") or "").strip()
        if not case_number or case_number in seen:
            return
        seen.add(case_number)
        rows.append(row)

    # Fixture-friendly data-* attributes (checked first so offline fixtures win)
    for m in re.finditer(
        r'data-case-number=["\']([^"\']+)["\'][^>]*'
        r'data-filed=["\']([^"\']*)["\'][^>]*'
        r'data-style=["\']([^"\']*)["\'][^>]*'
        r'data-plaintiff=["\']([^"\']*)["\'][^>]*'
        r'data-defendant=["\']([^"\']*)["\']',
        html,
        re.I,
    ):
        _add(
            {
                "case_number": m.group(1).strip(),
                "filed_date": m.group(2).strip(),
                "style": m.group(3).strip(),
                "plaintiff": m.group(4).strip(),
                "defendant": m.group(5).strip(),
                "case_type": "Tax",
                "status": "",
                "source_path": "",
            }
        )

    # Live Odyssey: case detail links plus nearby context
    link_pattern = re.compile(
        r'<a[^>]+href=["\']([^"\']*Case(?:Detail|/CaseDetail)[^"\']*)["\'][^>]*>\s*([^<]+?)\s*</a>',
        re.I,
    )
    for href, case_no in link_pattern.findall(html):
        case_number = _cell_text(case_no)
        if not case_number or len(case_number) < 4:
            continue
        idx = html.find(href)
        window = html[idx : idx + 2500] if idx >= 0 else ""
        filed = ""
        dm = re.search(
            r"(?:File(?:d)?\s*Date|Filing\s*Date)[^0-9]*(\d{1,2}/\d{1,2}/\d{4})",
            window,
            re.I,
        )
        if dm:
            filed = dm.group(1)
        if not filed:
            dm = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", window)
            if dm:
                filed = dm.group(1)
        parties = ""
        pm = re.search(
            r"(?:Style|Parties?|Party\s*Name)[^:<]*[:<][^A-Za-z0-9]*([^<]{5,200})",
            window,
            re.I,
        )
        if pm:
            parties = _cell_text(pm.group(1))
        case_type = ""
        tm = re.search(r"(?:Case\s*Type|Type)[^:<]*[:<][^A-Za-z0-9]*([^<]{3,80})", window, re.I)
        if tm:
            case_type = _cell_text(tm.group(1))
        status = ""
        sm = re.search(r"(?:Status)[^:<]*[:<][^A-Za-z0-9]*([^<]{3,80})", window, re.I)
        if sm:
            status = _cell_text(sm.group(1))
        _add(
            {
                "case_number": case_number,
                "filed_date": filed,
                "style": parties,
                "case_type": case_type,
                "status": status,
                "source_path": href,
            }
        )
    return rows


def _parse_us_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def _split_style(style: str, plaintiff_terms: list[str]) -> tuple[str, str]:
    """Best-effort plaintiff/defendant split from Odyssey style text."""
    style = (style or "").strip()
    if not style:
        return "", ""
    for sep in (" VS. ", " VS ", " V. ", " V ", " AGAINST "):
        if sep in style.upper():
            # Find separator case-insensitively
            idx = style.upper().find(sep)
            left = style[:idx].strip(" -–")
            right = style[idx + len(sep) :].strip(" -–")
            # Prefer county/tax side as plaintiff
            if plaintiff_is_tax_suit(left, plaintiff_terms):
                return left, right
            if plaintiff_is_tax_suit(right, plaintiff_terms):
                return right, left
            return left, right
    if "plaintiff" in style.lower() or any(t.upper() in style.upper() for t in plaintiff_terms):
        return style, ""
    return "", style


def row_to_case_record(
    row: dict[str, str],
    *,
    county_fips: str,
    plaintiff_terms: list[str],
    source_url: str,
) -> CaseRecord | None:
    case_number = (row.get("case_number") or "").strip()
    if not case_number:
        return None
    filed = _parse_us_date(row.get("filed_date") or "")
    if not filed:
        return None
    plaintiff = (row.get("plaintiff") or "").strip()
    defendant = (row.get("defendant") or "").strip()
    if not plaintiff or not defendant:
        p2, d2 = _split_style(row.get("style") or "", plaintiff_terms)
        plaintiff = plaintiff or p2
        defendant = defendant or d2
    if not plaintiff_is_tax_suit(plaintiff, plaintiff_terms) and not plaintiff_is_tax_suit(
        row.get("style") or "", plaintiff_terms
    ):
        # Keep rows already filtered upstream; still require a defendant
        if not any(t.upper() in (row.get("style") or "").upper() for t in plaintiff_terms):
            return None
        if not plaintiff:
            plaintiff = plaintiff_terms[0]
    if not defendant:
        return None
    now = datetime.utcnow()
    return CaseRecord(
        id=None,
        county_fips=county_fips,
        case_number=case_number,
        plaintiff=plaintiff or plaintiff_terms[0],
        defendant_raw=defendant,
        defendant_normalized=normalize_defendant(defendant),
        case_type=row.get("case_type") or "Tax",
        filed_date=filed,
        status=row.get("status") or "",
        source_url=source_url,
        retrieved_at=now,
        dedupe_key=case_dedupe_key(county_fips, case_number),
    )


def load_fixture_cases(
    fixture_dir: Path,
    *,
    since: date,
    county_fips: str,
    plaintiff_terms: list[str],
) -> list[CaseRecord]:
    """Load CaseRecords from saved Odyssey HTML fixtures under fixture_dir."""
    if not fixture_dir.exists():
        return []
    cases: list[CaseRecord] = []
    seen: set[str] = set()
    for path in sorted(fixture_dir.glob("*.html")):
        html = path.read_text(encoding="utf-8", errors="replace")
        for row in parse_smart_search_results(html):
            case = row_to_case_record(
                row,
                county_fips=county_fips,
                plaintiff_terms=plaintiff_terms,
                source_url=str(path),
            )
            if not case or case.filed_date < since:
                continue
            if case.case_number in seen:
                continue
            seen.add(case.case_number)
            cases.append(case)
    return cases


def solve_recaptcha_v2(api_key: str, site_key: str, page_url: str) -> str:
    """Solve reCAPTCHA v2 via Anti-Captcha (createTask + getTaskResult)."""
    create = requests.post(
        "https://api.anti-captcha.com/createTask",
        json={
            "clientKey": api_key,
            "task": {
                "type": "RecaptchaV2TaskProxyless",
                "websiteURL": page_url,
                "websiteKey": site_key,
            },
        },
        timeout=60,
    )
    create.raise_for_status()
    created = create.json()
    if created.get("errorId"):
        raise RuntimeError(f"Anti-Captcha createTask error: {created}")
    task_id = created["taskId"]
    for _ in range(40):
        import time

        time.sleep(3)
        poll = requests.post(
            "https://api.anti-captcha.com/getTaskResult",
            json={"clientKey": api_key, "taskId": task_id},
            timeout=60,
        )
        poll.raise_for_status()
        body = poll.json()
        if body.get("errorId"):
            raise RuntimeError(f"Anti-Captcha getTaskResult error: {body}")
        if body.get("status") == "ready":
            return body["solution"]["gRecaptchaResponse"]
    raise TimeoutError("Anti-Captcha timed out waiting for reCAPTCHA solution")


class DallasOdysseyClient:
    """Dallas County Tyler Odyssey Smart Search (portal_scrape)."""

    def __init__(
        self,
        portal_base: str = DEFAULT_PORTAL,
        session: requests.Session | None = None,
        *,
        site_key: str = RECAPTCHA_SITEKEY,
    ) -> None:
        self.portal_base = portal_base.rstrip("/")
        self.site_key = site_key
        self.session = session or SourceSession()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    @property
    def dashboard_url(self) -> str:
        return f"{self.portal_base}{DASHBOARD_PATH}"

    @property
    def search_url(self) -> str:
        return f"{self.portal_base}{SMART_SEARCH_PATH}"

    def _recaptcha_token(self) -> str:
        # Allow a pre-solved token for debugging / CI.
        env_token = os.environ.get("DALLAS_RECAPTCHA_TOKEN", "").strip()
        if env_token:
            return env_token
        api_key = os.environ.get("ANTICAPTCHA_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "Dallas Odyssey Smart Search requires reCAPTCHA for anonymous use. "
                "Set ANTICAPTCHA_API_KEY (or DALLAS_RECAPTCHA_TOKEN), or place saved "
                "HTML fixtures under artifacts/raw/dallas/clerk/."
            )
        return solve_recaptcha_v2(api_key, self.site_key, self.dashboard_url)

    def search_business_name(
        self,
        query: str,
        *,
        file_date_start: date | None = None,
        file_date_end: date | None = None,
    ) -> tuple[str, int]:
        """POST a Business Name smart search; returns (html, status_code)."""
        dash = self.session.get(self.dashboard_url, timeout=60)
        dash.raise_for_status()
        token = self._recaptcha_token()
        start = file_date_start.strftime("%m/%d/%Y") if file_date_start else ""
        end = file_date_end.strftime("%m/%d/%Y") if file_date_end else ""
        data = {
            "Settings.CaptchaEnabled": "True",
            "Settings.CaptchaDisabledForAuthenticated": "True",
            "caseCriteria.SearchCriteria": query,
            "caseCriteria.JudicialOfficerSearchBy": "",
            "caseCriteria.NameLast": "",
            "caseCriteria.NameFirst": "",
            "caseCriteria.NameMiddle": "",
            "caseCriteria.NameSuffix": "",
            "caseCriteria.AdvancedSearchOptionsOpen": "true",
            "caseCriteria.CourtLocation": "All Locations",
            "caseCriteria.SearchBy": "BusinessName",
            "caseCriteria.SearchCases": "true",
            "caseCriteria.SearchByPartyName": "false",
            "caseCriteria.SearchByNickName": "false",
            "caseCriteria.SearchByBusinessName": "true",
            "caseCriteria.UseSoundex": "false",
            "caseCriteria.PhoneNumber": "",
            "caseCriteria.FBINumber": "",
            "caseCriteria.SONumber": "",
            "caseCriteria.BookingNumber": "",
            "caseCriteria.CaseType": "",
            "caseCriteria.CaseStatus": "",
            "caseCriteria.FileDateStart": start,
            "caseCriteria.FileDateEnd": end,
            "caseCriteria.JudicialOfficer": "",
            "g-recaptcha-response": token,
            "Search": "Submit",
        }
        resp = self.session.post(
            self.search_url,
            data=data,
            headers={"Referer": self.dashboard_url},
            timeout=120,
            allow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text, resp.status_code

    def search_tax_suits(
        self,
        since: date,
        plaintiff_terms: list[str],
        *,
        county_fips: str,
        queries: list[str] | None = None,
    ) -> list[CaseRecord]:
        queries = queries or [f"{t}*" for t in plaintiff_terms[:2]]
        today = date.today()
        cases: list[CaseRecord] = []
        seen: set[str] = set()
        for q in queries:
            html, status = self.search_business_name(
                q, file_date_start=since, file_date_end=today
            )
            if status >= 400:
                raise RuntimeError(f"Dallas Odyssey search HTTP {status} for query={q!r}")
            rows = parse_smart_search_results(html)
            if not rows and not re.search(r"no (?:cases|results|records) (?:were )?found", _cell_text(html), re.I):
                raise SourceChangedError("Odyssey returned no recognized results or empty-result message")
            for row in rows:
                case = row_to_case_record(
                    row,
                    county_fips=county_fips,
                    plaintiff_terms=plaintiff_terms,
                    source_url=self.search_url,
                )
                if not case or case.filed_date < since:
                    continue
                if case.case_number in seen:
                    continue
                seen.add(case.case_number)
                cases.append(case)
        return cases
