from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leads import db, ledger, outreach
from leads.compliance import SenderConfigMissing
from leads.review import approve_lead


class TestOutreach(unittest.TestCase):
    def setUp(self) -> None:
        self.db_path = Path(__file__).parent / "_test_outreach.db"
        if self.db_path.exists():
            self.db_path.unlink()
        self._env_backup = {
            key: os.environ.pop(key, None)
            for key in ("OUTREACH_SENDER_NAME", "OUTREACH_SENDER_ADDRESS", "OUTREACH_FROM_EMAIL")
        }

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set_sender_config(self) -> None:
        os.environ["OUTREACH_SENDER_NAME"] = "Test Outreach Co"
        os.environ["OUTREACH_SENDER_ADDRESS"] = "123 Main St, Houston, TX 77002"
        os.environ["OUTREACH_FROM_EMAIL"] = "outreach@example.com"

    def _approved_lead(
        self,
        *,
        county_fips: str = "48201",
        case_number: str = "OUTREACH1",
        email: str = "owner@example.com",
    ) -> int:
        with db.db_session(self.db_path) as conn:
            case_id = db.upsert_case(
                conn,
                {
                    "county_fips": county_fips,
                    "case_number": case_number,
                    "plaintiff": "HARRIS COUNTY TAX ASSESSOR-COLLECTOR",
                    "defendant_raw": "TEST DEFENDANT",
                    "defendant_normalized": "TEST DEFENDANT",
                    "case_type": "OCV",
                    "filed_date": "2026-06-17",
                    "status": "OPEN",
                    "source_url": "fixture",
                    "retrieved_at": "2026-08-22T00:00:00",
                    "dedupe_key": f"outreachtest:{case_number}",
                },
            )
            lead_id = db.ensure_lead(conn, case_id, "needs_review")
            prop_cur = conn.execute(
                """
                INSERT INTO property_record (
                    case_id, apn, situs_address, owner_of_record, tax_delinquent_amt,
                    years_delinquent, assessor_url, match_confidence, dedupe_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (case_id, "APN1", "123 Test St", "TEST DEFENDANT", 5000.0, 3, "", 0.9, f"propdedupe:{case_number}"),
            )
            property_id = prop_cur.fetchone()["id"]
            contact_cur = conn.execute(
                """
                INSERT INTO contact_record (
                    property_id, name, phone, email, address, provider, confidence, retrieved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (property_id, "Jane Owner", "", email, "", "stub", 0.5, "2026-08-22T00:00:00"),
            )
            contact_id = contact_cur.fetchone()["id"]
            conn.execute(
                "UPDATE lead SET property_id = ?, contact_id = ? WHERE id = ?",
                (property_id, contact_id, lead_id),
            )
            approve_lead(conn, lead_id, "verified owner")
            return lead_id

    def test_queue_requires_sender_config(self) -> None:
        self._approved_lead()
        with db.db_session(self.db_path) as conn:
            with self.assertRaises(SenderConfigMissing):
                outreach.queue_ready_leads(conn)

    def test_queue_drafts_eligible_lead(self) -> None:
        self._set_sender_config()
        lead_id = self._approved_lead()
        with db.db_session(self.db_path) as conn:
            result = outreach.queue_ready_leads(conn)
            self.assertEqual(len(result["queued"]), 1)
            drafts = outreach.list_digest(conn, "queued")
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0]["lead_id"], lead_id)
            self.assertIn("123 Test St", drafts[0]["subject"])
            self.assertIn("Test Outreach Co", drafts[0]["body"])
            self.assertIn("123 Main St, Houston, TX 77002", drafts[0]["body"])
            rows = ledger.history(conn, entity_type="draft", entity_id=drafts[0]["id"])
            self.assertEqual(rows[0]["event_type"], "drafted")
            self.assertEqual(rows[0]["template_version"], outreach.TEMPLATE_VERSION)

    def test_queue_is_idempotent(self) -> None:
        self._set_sender_config()
        self._approved_lead()
        with db.db_session(self.db_path) as conn:
            outreach.queue_ready_leads(conn)
            result = outreach.queue_ready_leads(conn)
            self.assertEqual(result["queued"], [])
            self.assertEqual(result["skipped"].get("already_queued"), 1)

    def test_queue_skips_suppressed_email(self) -> None:
        self._set_sender_config()
        self._approved_lead(case_number="OUTREACH2", email="blocked@example.com")
        with db.db_session(self.db_path) as conn:
            outreach.record_opt_out(conn, "blocked@example.com", "asked to stop")
            result = outreach.queue_ready_leads(conn)
            self.assertEqual(result["queued"], [])
            self.assertEqual(result["skipped"].get("suppressed"), 1)

    def test_queue_skips_high_risk_state_unless_overridden(self) -> None:
        self._set_sender_config()
        # Maricopa (04013) is Arizona; add it to the high-risk list for this test only.
        os.environ["OUTREACH_HIGH_RISK_STATES"] = "AZ"
        try:
            self._approved_lead(county_fips="04013", case_number="OUTREACH3")
            with db.db_session(self.db_path) as conn:
                blocked = outreach.queue_ready_leads(conn)
                self.assertEqual(blocked["queued"], [])
                self.assertTrue(any(k.startswith("high_risk_state") for k in blocked["skipped"]))

                allowed = outreach.queue_ready_leads(conn, override_high_risk_state=True)
                self.assertEqual(len(allowed["queued"]), 1)
        finally:
            os.environ.pop("OUTREACH_HIGH_RISK_STATES", None)

    def test_mark_drafted_records_gmail_id_and_ledger(self) -> None:
        self._set_sender_config()
        self._approved_lead()
        with db.db_session(self.db_path) as conn:
            outreach.queue_ready_leads(conn)
            draft = outreach.list_digest(conn, "queued")[0]
            outreach.mark_drafted(conn, draft["id"], "gmail-draft-123")
            drafted = outreach.list_digest(conn, "drafted_in_gmail")
            self.assertEqual(len(drafted), 1)
            self.assertEqual(drafted[0]["gmail_draft_id"], "gmail-draft-123")
            rows = ledger.history(conn, entity_type="draft", entity_id=draft["id"])
            self.assertEqual(rows[-1]["event_type"], "drafted_in_gmail")

    def test_opt_out_blocks_future_queueing(self) -> None:
        self._set_sender_config()
        self._approved_lead(case_number="OUTREACH4", email="repeat@example.com")
        with db.db_session(self.db_path) as conn:
            outreach.record_opt_out(conn, "REPEAT@example.com", "unsubscribed")
            result = outreach.queue_ready_leads(conn)
            self.assertEqual(result["queued"], [])
            self.assertEqual(result["skipped"].get("suppressed"), 1)


if __name__ == "__main__":
    unittest.main()
