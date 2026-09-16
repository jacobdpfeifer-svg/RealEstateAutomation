from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.registry import load_counties
from leads.probe import _best_method, catalog_keys, classify_response, load_probe_catalog
from leads.utils import parse_money


class TestClassifyResponse(unittest.TestCase):
    def test_arcgis_json_is_api(self) -> None:
        body = json_dumps(
            {
                "type": "Feature Layer",
                "name": "Parcels",
                "fields": [
                    {"name": "Owner"},
                    {"name": "AcctNumb"},
                    {"name": "TotVal"},
                ],
            }
        )
        hit = classify_response("https://example.com/MapServer/0?f=json", 200, "application/json", body)
        self.assertEqual(hit["method"], "api")
        self.assertEqual(hit["subtype"], "arcgis_rest")
        self.assertEqual(hit["owner_field"], "Owner")
        self.assertEqual(hit["apn_field"], "AcctNumb")

    def test_odyssey_html_is_portal(self) -> None:
        html = '<form>SmartSearch <div class="g-recaptcha"></div></form>'
        hit = classify_response("https://courtsportal.example/DALLASPROD", 200, "text/html", html)
        self.assertEqual(hit["method"], "portal_scrape")
        self.assertTrue(hit.get("recaptcha_required"))

    def test_case_summary_label_is_not_bulk(self) -> None:
        html = "<html>Tyler Odyssey Portal. View Case Summary for party name search.</html>"
        hit = classify_response("https://portal-txbexar.tylertech.cloud/Portal/", 200, "text/html", html)
        self.assertNotEqual(hit["method"], "bulk_dataset")

    def test_bulk_clerk_page(self) -> None:
        html = "PublicDatasets DownloadDoc('Civil\\\\CaseSummaryMods_Daily-2026-09-01.txt')"
        hit = classify_response(
            "https://www.hcdistrictclerk.com/common/e-services/PublicDatasets.aspx",
            200,
            "text/html",
            html,
        )
        self.assertEqual(hit["method"], "bulk_dataset")

    def test_http_error(self) -> None:
        hit = classify_response("https://example.com", 404, "text/html", "missing")
        self.assertEqual(hit["method"], "error")


class TestProbeCatalog(unittest.TestCase):
    def test_next_metros_are_cataloged(self) -> None:
        keys = catalog_keys()
        self.assertIn("harris", keys)
        self.assertIn("dallas", keys)
        self.assertIn("tarrant", keys)
        self.assertIn("bexar", keys)
        self.assertIn("maricopa", keys)
        catalog = load_probe_catalog()
        tarrant = next(row for row in catalog if row["key"] == "tarrant")
        self.assertTrue(tarrant.get("clerk"))
        self.assertTrue(tarrant.get("tax"))

    def test_tax_prefers_arcgis_over_gis_zip(self) -> None:
        hits = [
            {"method": "bulk_dataset", "subtype": "gis_download", "url": "https://tad.example/downloads"},
            {"method": "api", "subtype": "arcgis_rest", "url": "https://mapit.example/FeatureServer/0"},
        ]
        best = _best_method(hits, role="tax")
        self.assertEqual(best["subtype"], "arcgis_rest")

    def test_probe_only_counties_are_disabled(self) -> None:
        counties = load_counties(ROOT / "config" / "counties.toml")
        for key in ("tarrant", "bexar", "maricopa"):
            cfg = counties[key]
            self.assertFalse(cfg.enabled)
            self.assertEqual(cfg.clerk_adapter, "")
            self.assertEqual(cfg.tax_adapter, "")
        self.assertEqual(counties["tarrant"].tax_lookup, ["arcgis_rest"])
        self.assertEqual(counties["bexar"].tax_lookup, ["arcgis_rest"])
        self.assertEqual(counties["maricopa"].tax_lookup, ["arcgis_rest"])


class TestParseMoney(unittest.TestCase):
    def test_currency_and_plain(self) -> None:
        self.assertEqual(parse_money("$280,620"), 280620.0)
        self.assertEqual(parse_money(1500), 1500.0)
        self.assertIsNone(parse_money(""))
        self.assertIsNone(parse_money(None))


def json_dumps(obj: dict) -> str:
    import json

    return json.dumps(obj)


if __name__ == "__main__":
    unittest.main()
