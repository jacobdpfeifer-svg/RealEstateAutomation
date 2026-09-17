"""Offline regressions for production runs that must preserve existing rows."""
import io
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from datetime import date, datetime
from unittest.mock import Mock, patch

from leads import db
from leads.cli import build_parser, cmd_run
from leads.models import CaseRecord
from leads.pipeline import Pipeline


def fixture(number, county="48201"):
    return CaseRecord(county, number, "COUNTY TAX", "TEST PERSON", "TEST PERSON",
                      "Tax", date(2026, 9, 1), "OPEN", "fixture",
                      datetime(2026, 9, 2), number)


def data(case):
    row = asdict(case)
    row["filed_date"] = case.filed_date.isoformat()
    row["retrieved_at"] = case.retrieved_at.isoformat()
    return row


class TestAppendOnly(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.migrate(self.conn)
        self.addCleanup(self.conn.close)

    def seed(self, case, status="new"):
        return db.ensure_lead(self.conn, db.upsert_case(self.conn, data(case)), status)

    def snapshot(self, table):
        return [dict(row) for row in self.conn.execute(f"SELECT * FROM {table} ORDER BY id")]

    def test_run_preserves_existing_cases_and_leads_in_all_statuses_and_counties(self):
        for i, status in enumerate(("new", "error", "needs_review", "enriched", "approved")):
            self.seed(fixture(f"OLD-{i}"), status)
        self.seed(fixture("DALLAS", "48113"))
        before = {table: self.snapshot(table) for table in ("case_record", "lead")}
        changed = replace(fixture("OLD-0"), status="CLOSED", retrieved_at=datetime(2026, 9, 16))
        clerk = Mock(search_tax_suits=Mock(return_value=[changed, fixture("NEW"), fixture("NEW")]), fetch_logs=[])
        tax = Mock(search_by_owner=Mock(return_value=[]))
        with patch("leads.pipeline.get_clerk_adapter", return_value=clerk), patch("leads.pipeline.get_tax_adapter", return_value=tax):
            result = Pipeline(self.conn, max_enrich=5, append_only=True).run_all("harris", date(2026, 9, 1))
        self.assertEqual(result["ingest"]["inserted"], 1)
        self.assertEqual(result["enrich"]["processed"], 1)
        self.assertEqual(result["score"]["scored"], 1)
        tax.search_by_owner.assert_called_once()
        for table, rows in before.items():
            self.assertEqual(self.snapshot(table)[:len(rows)], rows)

    def test_duplicate_only_run_and_fresh_pipeline_do_not_resume_old_backlog(self):
        self.seed(fixture("OLD"))
        clerk = Mock(search_tax_suits=Mock(return_value=[fixture("OLD"), fixture("NEW")]), fetch_logs=[])
        with patch("leads.pipeline.get_clerk_adapter", return_value=clerk):
            first = Pipeline(self.conn, max_enrich=0, append_only=True).run_all("harris", date(2026, 9, 1))
            before = {t: self.snapshot(t) for t in ("case_record", "lead")}
            with patch("leads.pipeline.get_tax_adapter") as tax:
                second = Pipeline(self.conn, max_enrich=5, append_only=True).run_all("harris", date(2026, 9, 1))
            tax.assert_not_called()
        self.assertEqual(first["enrich"]["deferred"], 1)
        self.assertEqual(second["ingest"]["inserted"], 0)
        self.assertEqual(second["enrich"]["processed"], 0)
        self.assertEqual(second["score"]["scored"], 0)
        for t in before:
            self.assertEqual(self.snapshot(t), before[t])

    def test_atomic_conflict_does_not_update_existing_case(self):
        original = fixture("OLD")
        self.seed(original)
        before = self.snapshot("case_record")
        changed = replace(original, status="CLOSED")
        self.assertIsNone(db.upsert_case(self.conn, data(changed), update_existing=False))
        self.assertEqual(self.snapshot("case_record"), before)
        self.assertIsNotNone(db.upsert_case(self.conn, data(fixture("NEW")), update_existing=False))

    def test_cli_passes_append_only_to_pipeline(self):
        args = build_parser().parse_args(["run", "--county", "harris", "--append-only", "--max-enrich", "5", "--db", ":memory:"])
        seen = []
        def run(pipe, *args, **kwargs):
            seen.append(pipe.append_only)
            return {"enrich": {"errors": 0}}
        with patch.object(Pipeline, "run_all", run), redirect_stdout(io.StringIO()):
            self.assertEqual(cmd_run(args), 0)
        self.assertEqual(seen, [True])
