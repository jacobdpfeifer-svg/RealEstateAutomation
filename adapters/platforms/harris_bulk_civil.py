from __future__ import annotations

import csv
import io
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from leads.models import CaseRecord
from leads.http import SourceChangedError, SourceSession
from leads.utils import case_dedupe_key, normalize_defendant, plaintiff_is_tax_suit, write_private_text

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


def download_bulk_file(session: requests.Session, file_path: str, bulk_page: str = BULK_PAGE) -> tuple[str, int, str]:
    """Download a Harris District Clerk bulk dataset via form POST."""
    resp = session.get(bulk_page, timeout=60)
    resp.raise_for_status()
    fields = _parse_viewstate(resp.text)
    if not fields["__VIEWSTATE"]:
        raise SourceChangedError("Harris bulk download form is missing VIEWSTATE")
    data = {
        **fields,
        "hiddenDownloadFile": file_path,
        "ctl00$ctl00$ctl00$ContentPlaceHolder1$ContentPlaceHolder2$ContentPlaceHolder2$buttonDownload": "Download",
    }
    dl = session.post(bulk_page, data=data, timeout=180)
    dl.raise_for_status()
    return dl.text, dl.status_code, bulk_page


def list_available_daily_summaries(html: str) -> list[str]:
    return re.findall(
        r"DownloadDoc\('Civil\\\\CaseSummaryMods_Daily-(\d{4}-\d{2}-\d{2})\.txt'\)",
        html,
    )


def pick_summary_files(since: date, html: str) -> list[str]:
    available = sorted(set(list_available_daily_summaries(html)))
    if not available:
        raise SourceChangedError("Harris daily summary listing is missing")
    chosen = [d for d in available if date.fromisoformat(d) >= since]
    return [rf"Civil\\CaseSummaryMods_Daily-{d}.txt" for d in chosen]


def parse_case_summary_tsv(text: str) -> list[dict[str, str]]:
    if not text.strip():
        raise SourceChangedError("Harris summary response is empty")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    required = {"casenbr", "file_dt", "plaintiff", "defendant"}
    if not required.issubset(reader.fieldnames or []):
        raise SourceChangedError("Harris summary TSV headers changed")
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
    write_private_text(path, content)
    return str(path)


class HarrisBulkDownloader:
    """Shared bulk download helper for Harris Clerk datasets."""

    def __init__(self, artifact_dir: Path | None = None, bulk_page: str = BULK_PAGE) -> None:
        self.session = SourceSession()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.bulk_page = bulk_page
        self.artifact_dir = artifact_dir or Path(__file__).resolve().parents[2] / "artifacts/raw"

    def fetch_summaries_since(
        self, since: date, plaintiff_terms: list[str], county_fips: str = "48201"
    ) -> tuple[list[CaseRecord], list[dict[str, Any]]]:
        page = self.session.get(self.bulk_page, timeout=60)
        page.raise_for_status()
        files = pick_summary_files(since, page.text)
        cases: list[CaseRecord] = []
        logs: list[dict[str, Any]] = []
        for fp in files:
            text, status, url = download_bulk_file(self.session, fp, self.bulk_page)
            artifact = ""
            if os.environ.get("LEADS_SAVE_RAW", "") == "1":
                artifact = save_artifact(self.artifact_dir, "harris", fp.replace("\\", "_"), text)
            logs.append({"url": url, "file": fp, "status": status, "artifact": artifact})
            rows = parse_case_summary_tsv(text)
            tax_rows = filter_tax_suits(rows, plaintiff_terms, since)
            for row in tax_rows:
                cases.append(row_to_case_record(row, county_fips, url))
        return cases, logs
