import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from adapters.platforms.dallas_odyssey import load_fixture_cases, parse_smart_search_results
from adapters.registry import load_counties
from adapters.tx.dallas_clerk import DallasClerkAdapter
from leads import db
from leads.cli import main
from leads.http import SourceChangedError
from leads.readiness import readiness_report
from test_reliability import seed

ROOT = Path(__file__).resolve().parents[1]


class TestSavedCourtInput(unittest.TestCase):
    def test_one_valid_page_cannot_mask_a_failed_page(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "1.html").write_text("No cases found")
            for body in ("<html>Maintenance</html>", "AWS WAF: No cases found"):
                with self.subTest(body=body):
                    (folder / "2.html").write_text(body)
                    with self.assertRaises(SourceChangedError):
                        load_fixture_cases(folder, since=date(2026, 1, 1), county_fips="48113",
                                           plaintiff_terms=["DALLAS COUNTY"])

    def test_incomplete_case_cannot_borrow_next_cases_identity_and_date(self):
        html = '''<a href="/Case/CaseDetail?id=1">CASE-ONE</a><div>Status: Active</div>
          <a href="/Case/CaseDetail?id=2">CASE-TWO</a><div>Filed Date: 09/01/2026</div>
          <div>Style: DALLAS COUNTY VS. SECOND PERSON</div>'''
        rows = parse_smart_search_results(html)
        self.assertEqual(rows[0]["filed_date"], "")
        self.assertEqual(rows[0]["style"], "")
        self.assertEqual(rows[1]["filed_date"], "09/01/2026")

    def test_default_adapter_rejects_renamed_sample_even_outside_date_window(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "real-capture.html"
            path.write_bytes((ROOT / "tests/fixtures/dallas/odyssey_smart_search_sample.html").read_bytes())
            with patch.dict("os.environ", {"DALLAS_CLERK_FIXTURE_DIR": directory,
                                           "DALLAS_CLERK_LIVE_ENABLED": "0"}):
                adapter = DallasClerkAdapter(load_counties()["dallas"])
                with self.assertRaisesRegex(SourceChangedError, "bundled sample"):
                    adapter.search_tax_suits(date(2027, 1, 1))


class TestReadiness(unittest.TestCase):
    def test_counts_missing_contacts_and_samples_without_personal_data(self):
        with db.db_session(":memory:") as conn:
            lead_id = seed(conn)
            conn.execute("UPDATE case_record SET source_url = ?", ("/private/sample.html",))
            db.update_lead_status(conn, lead_id, "approved", "PERSONAL SECRET")
            report = readiness_report(conn)
            self.assertEqual(report["approved_with_contact"], 0)
            self.assertEqual(report["counties"][0]["sample_source_marked"], 1)
            self.assertNotIn("PERSONAL SECRET", json.dumps(report))
            self.assertNotIn("JANE", json.dumps(report))

    def test_read_only_session_rejects_writes_and_cli_does_not_migrate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.db"
            with db.db_session(path) as conn:
                seed(conn)
            before = path.read_bytes()
            with db.read_only_session(path) as conn:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("DELETE FROM lead")
            with patch("leads.cli.load_secrets"), patch.object(db, "migrate") as migrate, redirect_stdout(io.StringIO()):
                self.assertEqual(main(["readiness", "--db", str(path)]), 0)
            migrate.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            with patch("leads.cli.load_secrets"), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["readiness", "--db", str(path)]), 1)
            self.assertFalse(path.exists())
