from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.platforms.dallas_odyssey import load_fixture_cases, parse_smart_search_results
from adapters.platforms.dcad_owner import DCADOwnerSearch, parse_acct_detail, parse_owner_search_results
from adapters.registry import load_counties
from adapters.tx.dallas_clerk import DallasClerkAdapter
from providers.skip_trace.batchdata import BatchDataSkipTraceProvider
from providers.skip_trace.base import get_skip_trace_provider

FIXTURES = Path(__file__).parent / "fixtures" / "dallas"


class TestDCADParsing(unittest.TestCase):
    def test_parse_owner_search_fixture(self) -> None:
        html = (FIXTURES / "dcad_owner_search_johnson.html").read_text(encoding="utf-8")
        rows = parse_owner_search_results(html)
        self.assertGreaterEqual(len(rows), 5)
        self.assertTrue(rows[0]["apn"])
        self.assertIn("JOHNSON", rows[0]["owner_of_record"].upper())
        self.assertTrue(rows[0]["situs_address"])

    def test_parse_acct_detail_fixture(self) -> None:
        html = (FIXTURES / "dcad_acct_detail_sample.html").read_text(encoding="utf-8")
        detail = parse_acct_detail(html)
        self.assertTrue(detail["apn"])
        self.assertIn("JOHNSON", detail["owner_of_record"].upper())
        self.assertTrue(detail["situs_address"])

    def test_tax_adapter_scores_from_fixture_html(self) -> None:
        html = (FIXTURES / "dcad_owner_search_johnson.html").read_text(encoding="utf-8")
        rows = parse_owner_search_results(html)
        client = DCADOwnerSearch()
        prop = client.row_to_property(rows[0], case_id=1, match_confidence=0.9, query_name="JOHNSON ALDA")
        self.assertEqual(prop.apn, rows[0]["apn"])
        self.assertGreaterEqual(prop.match_confidence, 0.85)
        self.assertEqual(prop.property_type.upper(), "RESIDENTIAL")
        self.assertEqual(prop.total_value, 280620.0)


class TestDallasOdysseyFixtures(unittest.TestCase):
    def test_parse_smart_search_fixture(self) -> None:
        html = (FIXTURES / "odyssey_smart_search_sample.html").read_text(encoding="utf-8")
        rows = parse_smart_search_results(html)
        self.assertGreaterEqual(len(rows), 3)
        numbers = {r["case_number"] for r in rows}
        self.assertIn("DC-26-08421", numbers)

    def test_load_fixture_cases_filters_since(self) -> None:
        cases = load_fixture_cases(
            FIXTURES,
            since=date(2026, 8, 1),
            county_fips="48113",
            plaintiff_terms=["DALLAS COUNTY TAX", "DALLAS COUNTY"],
        )
        self.assertGreaterEqual(len(cases), 2)
        self.assertTrue(all(c.filed_date >= date(2026, 8, 1) for c in cases))
        self.assertTrue(all(c.defendant_normalized for c in cases))

    def test_load_fixture_cases_applies_case_type_filter(self) -> None:
        kwargs = dict(
            since=date(2026, 8, 1),
            county_fips="48113",
            plaintiff_terms=["DALLAS COUNTY TAX", "DALLAS COUNTY"],
        )
        kept = load_fixture_cases(FIXTURES, case_type_filter=["Tax", "Delinquent"], **kwargs)
        dropped = load_fixture_cases(FIXTURES, case_type_filter=["OCV"], **kwargs)
        self.assertGreaterEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_clerk_adapter_uses_fixture_dir(self) -> None:
        counties = load_counties(ROOT / "config" / "counties.toml")
        cfg = counties["48113"]
        adapter = DallasClerkAdapter(cfg, artifact_dir=FIXTURES)
        cases = adapter.search_tax_suits(date(2026, 8, 1))
        self.assertGreaterEqual(len(cases), 2)


class TestBatchDataProvider(unittest.TestCase):
    def test_extract_persons_object_shape(self) -> None:
        data = {
            "status": {"code": 200},
            "results": {
                "persons": [
                    {
                        "fullName": "Jane Owner",
                        "phoneNumbers": [{"number": "2145550100"}],
                        "emails": [{"email": "jane@example.com"}],
                        "rank": 92,
                    }
                ]
            },
        }
        persons = BatchDataSkipTraceProvider._extract_persons(data)
        self.assertEqual(len(persons), 1)

    def test_extract_persons_list_shape(self) -> None:
        data = {
            "results": [
                {
                    "persons": [
                        {"full": "Bob", "phones": [{"number": "4695550199"}], "rank": 70}
                    ]
                }
            ]
        }
        persons = BatchDataSkipTraceProvider._extract_persons(data)
        self.assertEqual(len(persons), 1)

    def test_trace_parses_mock_response(self) -> None:
        provider = BatchDataSkipTraceProvider(api_key="test-key")
        mock_json = {
            "results": {
                "persons": [
                    {
                        "name": {"first": "Jane", "last": "Owner"},
                        "phoneNumbers": [{"number": "2145550100"}],
                        "emails": [{"email": "jane@example.com"}],
                        "score": 0.91,
                    }
                ]
            }
        }

        class FakeResp:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return mock_json

        with patch.object(provider.session, "post", return_value=FakeResp()):
            contacts = provider.trace(
                name="Jane Owner",
                address="100 Main St",
                city="Dallas",
                state="TX",
                apn="123",
            )
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0].phone, "2145550100")
        self.assertEqual(contacts[0].email, "jane@example.com")
        self.assertGreaterEqual(contacts[0].confidence, 0.9)

    def test_factory_falls_back_to_stub_without_key(self) -> None:
        with patch.dict("os.environ", {"BATCHDATA_API_KEY": ""}, clear=False):
            # Ensure empty
            import os

            os.environ.pop("BATCHDATA_API_KEY", None)
            provider = get_skip_trace_provider("batchdata")
            self.assertEqual(provider.name, "stub")


class TestCountyConfigPhase1(unittest.TestCase):
    def test_dallas_and_batchdata_wired(self) -> None:
        counties = load_counties(ROOT / "config" / "counties.toml")
        dallas = counties["48113"]
        self.assertEqual(dallas.ingest, ["portal_scrape"])
        self.assertEqual(dallas.skip_trace, ["batchdata"])
        self.assertIn("dallas_clerk", dallas.clerk_adapter)
        self.assertIn("dallas_tax", dallas.tax_adapter)
        harris = counties["48201"]
        self.assertEqual(harris.skip_trace, ["batchdata"])


if __name__ == "__main__":
    unittest.main()
