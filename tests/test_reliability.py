from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from leads import db
from leads.cli import build_parser, cmd_review, cmd_run, cmd_validate
from leads.validate import run_phase1_validation
from leads.models import CaseRecord, ContactRecord, PropertyRecord
from leads.pipeline import Pipeline
from leads.review import retry_lead, approve_lead, reject_lead, skip_lead
from leads.secrets import load_secrets
from leads.utils import write_private_text


def case(number="TEST-1"):
    return CaseRecord("48201", number, "HARRIS COUNTY TAX", "JANE EXAMPLE", "JANE EXAMPLE",
                      "Tax", date(2026, 1, 1), "OPEN", "fixture", datetime(2026, 1, 2), number)


def seed(conn, number="TEST-1"):
    data = asdict(case(number))
    data["filed_date"] = data["filed_date"].isoformat()
    data["retrieved_at"] = data["retrieved_at"].isoformat()
    return db.ensure_lead(conn, db.upsert_case(conn, data))


def property_match():
    return PropertyRecord(0, "TEST-APN", "100 Example St, Houston, TX 77002", "JANE EXAMPLE",
                          None, None, "fixture", 0.99)


def contact():
    return ContactRecord(0, "Jane Example", "7135550100", "jane@example.com", "100 Example St",
                         "test", 0.99, datetime(2026, 1, 2))


class TestEnrichmentReliability(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        self.conn = db.connect(self.path)
        db.migrate(self.conn)
        self.addCleanup(self.conn.close)
        self.lead = seed(self.conn)
        self.tax = Mock()
        self.tax.search_by_owner.return_value = [property_match()]
        self.skip = Mock()
        self.skip.name = "test"
        self.skip.trace.return_value = [contact()]
        self.tax_patch = patch("leads.pipeline.get_tax_adapter", return_value=self.tax)
        self.skip_patch = patch("leads.pipeline.get_skip_provider_for_county", return_value=self.skip)
        self.tax_patch.start()
        self.skip_patch.start()
        self.addCleanup(self.tax_patch.stop)
        self.addCleanup(self.skip_patch.stop)
        self.pipe = Pipeline(self.conn)
        self.pipe.max_attempts = 3

    def test_enrich_cap_limits_calls_and_resume_preserves_backlog(self):
        seed(self.conn, "TEST-2")
        seed(self.conn, "TEST-3")
        pipe = Pipeline(self.conn, max_enrich=1)
        result = pipe.enrich_pending("harris")
        self.assertEqual((result["processed"], result["deferred"]), (1, 2))
        self.assertEqual(pipe.enrich_pending()["processed"], 0)
        self.assertEqual(self.skip.trace.call_count, 1)
        self.assertEqual(Pipeline(self.conn, max_enrich=0).enrich_pending()["processed"], 0)
        self.assertEqual(Pipeline(self.conn, max_enrich=1).enrich_pending()["processed"], 1)
        self.assertEqual(self.skip.trace.call_count, 2)

    def test_failed_enrichment_consumes_cap(self):
        seed(self.conn, "TEST-2")
        self.skip.trace.side_effect = requests.Timeout()
        result = Pipeline(self.conn, max_enrich=1).enrich_pending()
        self.assertEqual((result["errors"], result["deferred"]), (1, 1))
        self.assertEqual(self.skip.trace.call_count, 1)
        with self.assertRaises(ValueError):
            Pipeline(self.conn, max_enrich=-1)

    def test_failures_rollback_then_dead_letter_and_explicit_retry(self):
        self.skip.trace.side_effect = requests.Timeout("secret owner=Jane, key=hidden")
        for attempt in range(3):
            result = self.pipe.enrich_pending()
            self.assertEqual(result["errors"], 1)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM property_record").fetchone()[0], 0)
        lead = self.conn.execute("SELECT * FROM lead").fetchone()
        self.assertEqual(lead["pipeline_status"], "dead_letter")
        self.assertEqual(lead["enrichment_attempts"], 3)
        self.assertNotIn("secret", lead["review_note"])
        self.assertEqual(self.pipe.enrich_pending()["processed"], 0)
        self.assertEqual(self.skip.trace.call_count, 3)
        retry_lead(self.conn, self.lead, "provider repaired")
        self.skip.trace.side_effect = None
        self.assertEqual(self.pipe.enrich_pending()["enriched"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outcome_event").fetchone()[0], 1)

    def test_no_contact_goes_to_review_and_rerun_does_not_retrace(self):
        self.skip.trace.return_value = []
        self.assertEqual(self.pipe.enrich_pending()["needs_review"], 1)
        self.assertIn("No contact", self.conn.execute("SELECT review_note FROM lead").fetchone()[0])
        self.assertEqual(self.pipe.enrich_pending()["processed"], 0)
        self.skip.trace.assert_called_once()
        self.assertEqual(self.skip.trace.call_args.kwargs["state"], "TX")

    def test_failure_after_contact_insert_does_not_leave_duplicates(self):
        original = self.pipe._enrich_case
        def fail_after_write(*args):
            original(*args)
            raise RuntimeError("failure after contact insert")
        with patch.object(self.pipe, "_enrich_case", side_effect=fail_after_write):
            self.pipe.enrich_pending()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contact_record").fetchone()[0], 0)
        self.pipe.enrich_pending()
        self.pipe.enrich_pending()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contact_record").fetchone()[0], 1)

    def test_completed_lead_persists_when_next_lead_fails(self):
        seed(self.conn, "TEST-2")
        self.skip.trace.side_effect = [[contact()], requests.Timeout()]
        result = self.pipe.enrich_pending()
        self.assertEqual((result["enriched"], result["errors"]), (1, 1))
        with db.db_session(self.path) as other:
            states = [r[0] for r in other.execute("SELECT pipeline_status FROM lead ORDER BY id")]
            self.assertEqual(states, ["enriched", "error"])

    def test_no_owner_match_records_reason(self):
        self.tax.search_by_owner.return_value = []
        self.pipe.enrich_pending()
        self.skip.trace.assert_not_called()
        self.assertIn("No owner match", self.conn.execute("SELECT review_note FROM lead").fetchone()[0])

    def test_reingest_preserves_review_decision_and_counts_new_rows(self):
        self.conn.execute("UPDATE lead SET pipeline_status = 'approved'")
        clerk = Mock(search_tax_suits=Mock(return_value=[case(), case("TEST-2")]), fetch_logs=[])
        with patch("leads.pipeline.get_clerk_adapter", return_value=clerk):
            self.assertEqual(self.pipe.ingest_county("harris", date(2026, 1, 1))["inserted"], 1)
            self.assertEqual(self.pipe.ingest_county("harris", date(2026, 1, 1))["inserted"], 0)
        self.assertEqual(self.conn.execute("SELECT pipeline_status FROM lead WHERE id = ?", (self.lead,)).fetchone()[0], "approved")


class TestLocalDatabaseSafety(unittest.TestCase):
    def test_old_schema_migrates_once_without_losing_rows(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        legacy = db.SCHEMA_SQLITE.replace("    property_type TEXT NOT NULL DEFAULT '',\n", "").replace("    total_value REAL,\n", "")
        conn.executescript(legacy)
        seed(conn)
        conn.commit()
        db.migrate(conn)
        db.migrate(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM lead").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT enrichment_attempts FROM lead").fetchone()[0], 0)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        conn.execute("PRAGMA user_version = 999")
        with self.assertRaises(RuntimeError):
            db.migrate(conn)

    def test_pipeline_lock_releases_and_rejects_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.db"
            with db.pipeline_lock(path):
                with self.assertRaises(RuntimeError):
                    with db.pipeline_lock(path):
                        pass
            with db.pipeline_lock(path):
                pass

    def test_explicit_database_overrides_postgres_environment(self):
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://not-used"}), patch.object(db.PGConnection, "__init__", side_effect=AssertionError("must not connect")) as pg:
            with db.db_session(":memory:") as conn:
                self.assertIsInstance(conn, sqlite3.Connection)
            pg.assert_not_called()

    def test_nonexistent_review_decision_does_not_create_ledger_event(self):
        with db.db_session(":memory:") as conn:
            for action in (approve_lead, reject_lead, skip_lead):
                with self.subTest(action=action.__name__), self.assertRaises(ValueError):
                    action(conn, 999, "test")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM outcome_event").fetchone()[0], 0)

    def test_private_export_and_empty_environment_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "output.csv"
            write_private_text(path, "synthetic")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            env = Path(temp) / "sample.env"
            env.write_text("EXAMPLE_KEY=file-value\n")
            with patch.dict("os.environ", {"EXAMPLE_KEY": ""}):
                load_secrets(env)
                import os
                self.assertEqual(os.environ["EXAMPLE_KEY"], "")


class TestCLIOutcomes(unittest.TestCase):
    def test_dry_run_never_opens_target_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "do-not-open.db"
            path.write_bytes(b"sentinel: not even a valid database")
            args = build_parser().parse_args(["--db", str(path), "run", "--county", "harris", "--dry-run"])
            with patch.object(Pipeline, "run_all", return_value={"ingest": {"found": 0}}), redirect_stdout(io.StringIO()):
                self.assertEqual(cmd_run(args), 0)
            self.assertEqual(path.read_bytes(), b"sentinel: not even a valid database")
            self.assertEqual(len(list(Path(temp).iterdir())), 1)

    def test_county_failure_is_nonzero_and_other_county_still_runs(self):
        args = build_parser().parse_args(["--db", ":memory:", "run", "--all-enabled"])
        errors = io.StringIO()
        with patch.object(Pipeline, "run_all", side_effect=[requests.Timeout("SECRET URL"), {"enrich": {"errors": 0}}]) as run, redirect_stdout(io.StringIO()), redirect_stderr(errors):
            self.assertEqual(cmd_run(args), 1)
            self.assertEqual(run.call_count, 2)
        self.assertNotIn("SECRET", errors.getvalue())

    def test_db_position_and_negative_cap(self):
        parser = build_parser()
        for argv in (["--db", "scratch.db", "state"], ["state", "--db", "scratch.db"],
                     ["review", "list", "--db", "scratch.db"],
                     ["--db", "scratch.db", "review", "list"]):
            self.assertEqual(parser.parse_args(argv).db, "scratch.db")
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["run", "--county", "harris", "--max-enrich", "-1"])

    def test_all_county_cap_is_shared(self):
        args = build_parser().parse_args(["--db", ":memory:", "run", "--all-enabled", "--max-enrich", "5"])
        budgets = []
        def run(pipe, county, since, dry_run=False):
            budgets.append(pipe.max_enrich - pipe.enrich_attempted)
            pipe.enrich_attempted += min(3, budgets[-1])
            return {"enrich": {"errors": 0}}
        with patch.object(Pipeline, "run_all", run), redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_run(args), 0)
        self.assertEqual(budgets, [5, 2])

    def test_enrichment_error_is_nonzero(self):
        args = build_parser().parse_args(["--db", ":memory:", "run", "--county", "harris"])
        with patch.object(Pipeline, "run_all", return_value={"enrich": {"errors": 1}}), redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_run(args), 1)

    def test_missing_review_lead_is_clean_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scratch.db"
            with db.db_session(path):
                pass
            args = build_parser().parse_args(["review", "reject", "999", "--note", "missing", "--db", str(path)])
            errors = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(errors):
                self.assertEqual(cmd_review(args), 1)
            self.assertIn("Lead not found: 999", errors.getvalue())
            self.assertNotIn("Traceback", errors.getvalue())


class TestValidationOutcomes(unittest.TestCase):
    def test_capped_validation_is_not_daily_ready_with_deferred_work(self):
        checks = {"secrets": {"BATCHDATA_API_KEY": "present"}, "dallas_clerk_live": False,
                  "dallas_fixtures": {"html_files": 1}}
        runs = [{"ok": True, "result": {"ingest": {"found": 1}}},
                {"ok": True, "result": {"enrich": {"errors": 0, "deferred": 1}}}]
        with tempfile.TemporaryDirectory() as directory, patch("leads.validate.preflight", return_value=checks), patch("leads.validate._run_county", side_effect=runs):
            result = run_phase1_validation(Path(directory) / "scratch.db", max_enrich=1)
        self.assertFalse(result["ready_for_daily"])
        self.assertEqual(result["max_enrich"], 1)

    def test_validation_exit_includes_harris_failure(self):
        args = build_parser().parse_args(["validate", "--db", "scratch.db"])
        report = {"runs": {"harris_dry_run": {"ok": False}, "dallas": {"ok": True}}}
        with patch("leads.cli.run_phase1_validation", return_value=report), redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_validate(args), 1)
