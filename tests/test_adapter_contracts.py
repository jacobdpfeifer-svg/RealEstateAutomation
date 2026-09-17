from __future__ import annotations

import json
import unittest
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests

from adapters.platforms.arcgis_owner import ArcGISConfig, ArcGISOwnerSearch
from adapters.platforms.dcad_owner import DCADOwnerSearch
from adapters.platforms.dallas_odyssey import DallasOdysseyClient, load_fixture_cases
from adapters.platforms.harris_bulk_civil import parse_case_summary_tsv, pick_summary_files
from adapters.registry import load_counties
from adapters.tx.dallas_clerk import DallasClerkAdapter
from leads.http import SourceChangedError
from providers.skip_trace.batchdata import BatchDataSkipTraceProvider

FIXTURES = Path(__file__).parent / "fixtures"


def response(body="", status=200, data=None):
    result = requests.Response()
    result.status_code = status
    result._content = (json.dumps(data) if data is not None else body).encode()
    result._content_consumed = True
    return result


class TestSourceContracts(unittest.TestCase):
    def test_arcgis_distinguishes_empty_results_from_errors(self):
        session = Mock()
        client = ArcGISOwnerSearch(ArcGISConfig("https://example.test/MapServer/0"), session)
        for payload in [{"error": {"code": 400}}, {}, {"features": [{}]}, {"features": [], "exceededTransferLimit": True}]:
            with self.subTest(payload=payload):
                session.get.return_value = response(data=payload)
                with self.assertRaises(SourceChangedError):
                    client.search_by_owner("Example")
        session.get.return_value = response(data={"features": []})
        self.assertEqual(client.search_by_owner("Example")[0], [])
        session.get.return_value = response(status=503)
        with self.assertRaises(requests.HTTPError):
            client.search_by_owner("Example")

    def test_arcgis_address_and_apn_escaping(self):
        session = Mock()
        session.get.return_value = response(data={"features": []})
        client = ArcGISOwnerSearch(ArcGISConfig("https://example.test/MapServer/0"), session)
        client.get_by_apn("123' OR '1'='1")
        where = parse_qs(urlsplit(session.get.call_args.args[0]).query)["where"][0]
        self.assertEqual(where, "HCAD_NUM = '123'' OR ''1''=''1'")
        attrs = {"HCAD_NUM": "TEST", "owner_name_1": "EXAMPLE", "site_str_num": 100,
                 "site_str_name": "EXAMPLE ST", "site_city": "HOUSTON", "site_zip": "77002"}
        self.assertEqual(client.attrs_to_property(attrs, case_id=1, match_confidence=1).situs_address,
                         "100 EXAMPLE ST, HOUSTON, TX 77002")

    def test_dcad_form_post_contract(self):
        html = (FIXTURES / "dallas/dcad_owner_search_johnson.html").read_text()
        session = Mock()
        session.get.return_value = response(html)
        session.post.return_value = response(html)
        rows, _, _ = DCADOwnerSearch(session=session).search_by_owner("JOHNSON ALDA")
        self.assertGreater(len(rows), 0)
        fields = session.post.call_args.kwargs["data"]
        self.assertEqual(fields["__VIEWSTATE"], "stub")
        self.assertEqual(fields["txtOwnerName"], "JOHNSON ALDA")
        self.assertIn("timeout", session.post.call_args.kwargs)

    def test_dcad_denial_is_not_an_empty_match(self):
        session = Mock()
        session.get.return_value = response(status=403)
        with self.assertRaises(requests.HTTPError):
            DCADOwnerSearch(session=session).search_by_owner("Example")
        self.assertEqual(session.get.call_count, 1)
        session.post.assert_not_called()

    def test_dcad_changed_form_and_response_are_errors(self):
        session = Mock()
        client = DCADOwnerSearch(session=session)
        session.get.return_value = response("<html>Sign in</html>")
        with self.assertRaises(SourceChangedError):
            client.search_by_owner("Example")
        session.get.return_value = response('<input id="__VIEWSTATE" value="test"><input id="txtOwnerName">')
        session.post.return_value = response("<html>Maintenance</html>")
        with self.assertRaises(SourceChangedError):
            client.search_by_owner("Example")
        session.post.return_value = response("No records found")
        self.assertEqual(client.search_by_owner("Example")[0], [])

    def test_harris_rejects_html_as_tsv_and_preserves_empty_dataset(self):
        for body in ["", "<html>Access denied</html>", "new_column\nvalue"]:
            with self.subTest(body=body):
                with self.assertRaises(SourceChangedError):
                    parse_case_summary_tsv(body)
        self.assertEqual(parse_case_summary_tsv("casenbr\tfile_dt\tplaintiff\tdefendant\n"), [])
        with self.assertRaises(SourceChangedError):
            pick_summary_files(date(2026, 1, 1), "<html>Maintenance</html>")
        self.assertEqual(pick_summary_files(date(2026, 2, 1), "DownloadDoc('Civil\\\\CaseSummaryMods_Daily-2026-01-01.txt')"), [])

    def test_saved_odyssey_unrecognized_html_is_not_zero_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.html"
            path.write_text("<html>Complete CAPTCHA</html>")
            kwargs = dict(since=date(2026, 1, 1), county_fips="48113", plaintiff_terms=["DALLAS COUNTY"])
            with self.assertRaises(SourceChangedError):
                load_fixture_cases(Path(directory), **kwargs)
            path.write_text("No cases found")
            self.assertEqual(load_fixture_cases(Path(directory), **kwargs), [])

    def test_odyssey_challenge_is_not_an_empty_search(self):
        client = DallasOdysseyClient(session=Mock())
        with patch.object(client, "search_business_name", return_value=("<html>Complete CAPTCHA</html>", 200)):
            with self.assertRaises(SourceChangedError):
                client.search_tax_suits(date(2026, 1, 1), ["DALLAS COUNTY"], county_fips="48113")
        with patch.object(client, "search_business_name", return_value=("No cases found", 200)):
            self.assertEqual(client.search_tax_suits(date(2026, 1, 1), ["DALLAS COUNTY"], county_fips="48113"), [])

    def test_dallas_keys_do_not_implicitly_enable_live_search(self):
        adapter = DallasClerkAdapter(load_counties()["dallas"], artifact_dir=FIXTURES / "dallas")
        with patch.dict("os.environ", {"ANTICAPTCHA_API_KEY": "test", "DALLAS_RECAPTCHA_TOKEN": "test", "DALLAS_CLERK_LIVE_ENABLED": ""}):
            self.assertFalse(adapter._can_live_search())
            self.assertEqual(adapter.search_tax_suits(date(2099, 1, 1)), [])

    def test_batchdata_unknown_payload_is_not_a_successful_no_match(self):
        session = Mock()
        session.post.return_value = response(data={"unexpected": "value"})
        provider = BatchDataSkipTraceProvider(api_key="synthetic", session=session)
        with self.assertRaises(SourceChangedError):
            provider.trace("Jane Example", "100 Example St", "Houston", "TX")
        self.assertEqual(session.post.call_count, 1)
        session.post.return_value = response(data={"results": {"persons": []}})
        self.assertEqual(provider.trace("Jane Example", "100 Example St", "Houston", "TX"), [])
