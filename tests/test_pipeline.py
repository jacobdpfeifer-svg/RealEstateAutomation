from __future__ import annotations

import csv
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.platforms.arcgis_owner import ArcGISConfig, ArcGISOwnerSearch
from adapters.platforms.harris_bulk_civil import filter_tax_suits, parse_case_summary_tsv
from adapters.registry import CountyConfig, load_counties
from adapters.tx.harris_clerk import HarrisClerkAdapter
from adapters.tx.harris_tax import HarrisTaxAdapter
from leads import db
from leads.pipeline import Pipeline
from leads.review import approve_lead, export_csv, paste_contact
from leads.utils import normalize_defendant, plaintiff_is_tax_suit

FIXTURES = Path(__file__).parent / "fixtures" / "harris"


class TestHarrisBulkCivil(unittest.TestCase):
    def test_parse_and_filter_tax_suits(self) -> None:
        text = (FIXTURES / "case_summary_sample.tsv").read_text(encoding="utf-8")
        rows = parse_case_summary_tsv(text)
        self.assertGreater(len(rows), 0)
        tax_rows = filter_tax_suits(
            rows,
            plaintiff_terms=["HARRIS COUNTY TAX ASSESSOR-COLLECTOR"],
            since=date(2025, 1, 1),
        )
        self.assertGreater(len(tax_rows), 0)
        self.assertTrue(
            all("TAX" in (r.get("plaintiff") or "").upper() for r in tax_rows)
        )

    def test_plaintiff_filter_negative(self) -> None:
        self.assertFalse(plaintiff_is_tax_suit("FROST BANK", ["HARRIS COUNTY"]))
        self.assertTrue(
            plaintiff_is_tax_suit(
                "HARRIS COUNTY TAX ASSESSOR-COLLECTOR",
                ["HARRIS COUNTY"],
            )
        )


class TestHarrisArcGIS(unittest.TestCase):
    def test_fixture_arcgis_to_property(self) -> None:
        data = json.loads((FIXTURES / "hcad_arcgis_smith.json").read_text())
        self.assertGreater(len(data.get("features") or []), 0)
        attrs = data["features"][0]["attributes"]
        client = ArcGISOwnerSearch(
            ArcGISConfig(
                mapserver_url="https://example.com/MapServer/0",
                owner_field="owner_name_1",
                apn_field="HCAD_NUM",
            )
        )
        prop = client.attrs_to_property(attrs, case_id=1, match_confidence=0.9)
        self.assertTrue(prop.apn)
        self.assertTrue(prop.owner_of_record)
        self.assertTrue(prop.situs_address)


class TestHarrisTaxAdapter(unittest.TestCase):
    def test_normalize_defendant(self) -> None:
        raw = "JUMGLOBAL AUTOS LLC SERIFAT OLAJUMOKE BISUGA OWNER"
        norm = normalize_defendant(raw)
        self.assertNotIn("LLC", norm)
        self.assertIn("JUMGLOBAL", norm)


class TestPipelineOffline(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(__file__).parent / "_test_leads.db"
        if self.db_path.exists():
            self.db_path.unlink()

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def test_db_review_export(self) -> None:
        with db.db_session(self.db_path) as conn:
            case_id = db.upsert_case(
                conn,
                {
                    "county_fips": "48201",
                    "case_number": "202640765",
                    "plaintiff": "HARRIS COUNTY TAX ASSESSOR-COLLECTOR",
                    "defendant_raw": "GARAGE HOUSTON LLC",
                    "defendant_normalized": "GARAGE HOUSTON",
                    "case_type": "OCV",
                    "filed_date": "2026-06-17",
                    "status": "OPEN",
                    "source_url": "fixture",
                    "retrieved_at": "2026-08-22T00:00:00",
                    "dedupe_key": "testcase1",
                },
            )
            lead_id = db.ensure_lead(conn, case_id, "needs_review")
            conn.execute(
                """
                INSERT INTO property_record (
                    case_id, apn, situs_address, owner_of_record, assessor_url,
                    match_confidence, dedupe_key
                ) VALUES (?, '123', '100 Main, Houston, TX 77002', 'GARAGE HOUSTON LLC', '', 0.9, 'prop1')
                """,
                (case_id,),
            )
            prop_id = conn.execute(
                "SELECT id FROM property_record WHERE case_id = ?", (case_id,)
            ).fetchone()["id"]
            conn.execute(
                "UPDATE lead SET property_id = ? WHERE id = ?", (prop_id, lead_id)
            )
            paste_contact(
                conn,
                lead_id,
                name="John Owner",
                phone="7135550100",
                email="j@example.com",
            )
            approve_lead(conn, lead_id, "looks good")
            csv_out = export_csv(conn, "approved")
            self.assertIn("202640765", csv_out)
            self.assertIn("7135550100", csv_out)


class TestCountyConfig(unittest.TestCase):
    def test_load_counties_tx_phase1(self) -> None:
        counties = load_counties(ROOT / "config" / "counties.toml")
        harris = counties["48201"]
        self.assertEqual(harris.state, "TX")
        self.assertEqual(harris.ingest, ["bulk_dataset"])
        dallas = counties["48113"]
        self.assertEqual(dallas.ingest, ["portal_scrape"])


if __name__ == "__main__":
    unittest.main()
