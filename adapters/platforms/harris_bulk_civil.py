from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from leads.models import CaseRecord
from leads.utils import case_dedupe_key, normalize_defendant, plaintiff_is_tax_suit

BULK_PAGE = "https://www.hcdistrictclerk.com/common/e-services/PublicDatasets.aspx"
USER_AGENT = "re-tax-leads/0.1 (bulk-dataset importer; research)"


def _parse_viewstate(html: str) -> dict[str, str]:
    def grab(name: str) -> str:
        m = re.search(rf'id="{name}" value="([^"]+)"', html)
        return m.group(1) if m else ""

    return {
        "__VIEWSTATE": grab("__VIEWSTATE"),
        "__EVENTVALIDATION": grab("__EVENTVALIDATION"),
        "__VIEWSTATEGENERATOR": grab("__VIEWSTATEGENERATOR"),
    }


def download_bulk_file(session: requests.Session, file_path: str) -> tuple[str, int, str]:
    """Download a Harris District Clerk bulk dataset via form POST."""
    resp = session.get(BULK_PAGE, timeout=60)
    resp.raise_for_status()
    fields = _parse_viewstate(resp.text)
    data = {
        **fields,
        "hiddenDownloadFile": file_path,
        "ctl00$ctl00$ctl00$ContentPlaceHolder1$ContentPlaceHolder2$ContentPlaceHolder2$buttonDownload": "Download",
    }
    dl = session.post(BULK_PAGE, data=data, timeout=180)
    return dl.text, dl.status_code, BULK_PAGE


def list_available_daily_summaries(html: str) -> list[str]:
    return re.findall(
        r"DownloadDoc\('Civil\\\\CaseSummaryMods_Daily-(\d{4}-\d{2}-\d{2})\.txt'\)",
        html,
    )


def pick_summary_files(since: date, html: str) -> list[str]:
    available = sorted(set(list_available_daily_summaries(html)))
    chosen = [d for d in available if date.fromisoformat(d) >= since]
    if not chosen and available:
        chosen = [available[-1]]
    return [rf"Civil\\CaseSummaryMods_Daily-{d}.txt" for d in chosen]


def parse_case_summary_tsv(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    return [row for row in reader if row.get("casenbr")]


def filter_tax_suits(
    rows: list[dict[str, str]],
    plaintiff_terms: list[str],
    since: date,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        plaintiff = row.get("plaintiff") or ""
        if not plaintiff_is_tax_suit(plaintiff, plaintiff_terms):
            continue
        filed_raw = (row.get("file_dt") or "")[:10]
        if not filed_raw:
            continue
        try:
            filed = date.fromisoformat(filed_raw)
        except ValueError:
            continue
        if filed < since:
            continue
        case_no = row.get("casenbr", "").strip()
        if case_no in seen:
            continue
        seen.add(case_no)
        out.append(row)
    return out


def row_to_case_record(row: dict[str, str], county_fips: str, source_url: str) -> CaseRecord:
    defendant = row.get("defendant") or ""
    filed_raw = (row.get("file_dt") or "")[:10]
    filed = date.fromisoformat(filed_raw)
    case_number = row.get("casenbr", "").strip()
    now = datetime.utcnow()
    return CaseRecord(
        id=None,
        county_fips=county_fips,
        case_number=case_number,
        plaintiff=row.get("plaintiff") or "",
        defendant_raw=defendant,
        defendant_normalized=normalize_defendant(defendant),
        case_type=row.get("toac") or row.get("cs_typ") or "",
        filed_date=filed,
        status=row.get("judgment") or row.get("cst") or "",
        source_url=source_url,
        retrieved_at=now,
        dedupe_key=case_dedupe_key(county_fips, case_number),
    )


def save_artifact(base: Path, county: str, name: str, content: str) -> str:
    day = datetime.utcnow().strftime("%Y-%m-%d")
    dest = base / county / day
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / name
    path.write_text(content, encoding="utf-8")
    return str(path)


class HarrisBulkDownloader:
    """Shared bulk download helper for Harris Clerk datasets."""

    def __init__(self, artifact_dir: Path | None = None) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.artifact_dir = artifact_dir or Path("artifacts/raw")

    def fetch_summaries_since(
        self, since: date, plaintiff_terms: list[str], county_fips: str = "48201"
    ) -> tuple[list[CaseRecord], list[dict[str, Any]]]:
        page = self.session.get(BULK_PAGE, timeout=60)
        page.raise_for_status()
        files = pick_summary_files(since, page.text)
        cases: list[CaseRecord] = []
        logs: list[dict[str, Any]] = []
        for fp in files:
            text, status, url = download_bulk_file(self.session, fp)
            artifact = save_artifact(self.artifact_dir, "harris", fp.replace("\\", "_"), text[:500000])
            logs.append({"url": url, "file": fp, "status": status, "artifact": artifact})
            rows = parse_case_summary_tsv(text)
            tax_rows = filter_tax_suits(rows, plaintiff_terms, since)
            for row in tax_rows:
                cases.append(row_to_case_record(row, county_fips, url))
        return cases, logs
