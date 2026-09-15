from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leads import db, ledger
from leads.review import approve_lead, reject_lead


class TestLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(__file__).parent / "_test_ledger.db"
        if self.db_path.exists():
            self.db_path.unlink()

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def test_record_uses_default_weight(self) -> None:
        with db.db_session(self.db_path) as conn:
            ledger.record(conn, entity_type="draft", entity_id=1, event_type="positive_reply")
            rows = ledger.history(conn, entity_type="draft", entity_id=1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["weight"], 2.0)

    def test_record_allows_weight_override(self) -> None:
        with db.db_session(self.db_path) as conn:
            ledger.record(
                conn,
                entity_type="match",
                entity_id=1,
                event_type="deal_closed_won",
                weight=9.0,
            )
            rows = ledger.history(conn, entity_type="match", entity_id=1)
            self.assertEqual(rows[0]["weight"], 9.0)

    def test_report_aggregates_wins_and_losses(self) -> None:
        with db.db_session(self.db_path) as conn:
            ledger.record(conn, entity_type="draft", entity_id=1, event_type="positive_reply")
            ledger.record(conn, entity_type="draft", entity_id=2, event_type="bounced")
            ledger.record(conn, entity_type="draft", entity_id=3, event_type="bounced")
            out = ledger.report(conn)
            self.assertEqual(out["totals"]["events"], 3)
            self.assertEqual(out["totals"]["wins"], 1)
            self.assertEqual(out["totals"]["losses"], 2)
            self.assertAlmostEqual(out["totals"]["net_weight"], 2.0 - 2.0)

    def test_review_decisions_write_to_ledger(self) -> None:
        with db.db_session(self.db_path) as conn:
            case_id = db.upsert_case(
                conn,
                {
                    "county_fips": "48201",
                    "case_number": "TESTCASE1",
                    "plaintiff": "HARRIS COUNTY TAX ASSESSOR-COLLECTOR",
                    "defendant_raw": "TEST DEFENDANT",
                    "defendant_normalized": "TEST DEFENDANT",
                    "case_type": "OCV",
                    "filed_date": "2026-06-17",
                    "status": "OPEN",
                    "source_url": "fixture",
                    "retrieved_at": "2026-08-22T00:00:00",
                    "dedupe_key": "ledgertest1",
                },
            )
            lead_id = db.ensure_lead(conn, case_id, "needs_review")
            approve_lead(conn, lead_id, "good match")
            rows = ledger.history(conn, entity_type="lead", entity_id=lead_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_type"], "lead_approved")
            self.assertGreater(rows[0]["weight"], 0)

            lead_id_2 = db.ensure_lead(conn, db.upsert_case(
                conn,
                {
                    "county_fips": "48201",
                    "case_number": "TESTCASE2",
                    "plaintiff": "HARRIS COUNTY TAX ASSESSOR-COLLECTOR",
                    "defendant_raw": "TEST DEFENDANT 2",
                    "defendant_normalized": "TEST DEFENDANT 2",
                    "case_type": "OCV",
                    "filed_date": "2026-06-17",
                    "status": "OPEN",
                    "source_url": "fixture",
                    "retrieved_at": "2026-08-22T00:00:00",
                    "dedupe_key": "ledgertest2",
                },
            ), "needs_review")
            reject_lead(conn, lead_id_2, "bad match")
            rows2 = ledger.history(conn, entity_type="lead", entity_id=lead_id_2)
            self.assertEqual(rows2[0]["event_type"], "lead_rejected")
            self.assertLess(rows2[0]["weight"], 0)


if __name__ == "__main__":
    unittest.main()
