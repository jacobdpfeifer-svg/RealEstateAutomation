import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from leads import db
from leads.cli import main
from test_reliability import seed


class TestPostgresSessionLock(unittest.TestCase):
    def connection(self, acquired=True):
        conn = Mock(spec=db.PGConnection)
        conn.execute.return_value.fetchone.return_value = {"acquired": acquired}
        return conn

    def test_contention_stops_before_migration_or_work_and_closes_connection(self):
        conn = self.connection(False)
        with patch.object(db, "connect", return_value=conn), patch.object(db, "migrate") as migrate:
            with self.assertRaisesRegex(db.DatabaseBusyError, "already using"):
                with db.db_session():
                    self.fail("contending session must not execute work")
        migrate.assert_not_called()
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_lock_precedes_migration_and_session_close_releases_after_failure(self):
        conn = self.connection()
        def migrate(c):
            self.assertIn("pg_try_advisory_lock", c.execute.call_args.args[0])
            c.commit()  # Migration commits must not end the session-level lock.
        with patch.object(db, "connect", return_value=conn), patch.object(db, "migrate", side_effect=migrate):
            with self.assertRaisesRegex(ValueError, "synthetic"):
                with db.db_session() as active:
                    self.assertIs(active, conn)
                    active.commit()
                    raise ValueError("synthetic")
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_busy_cli_returns_actionable_error_without_traceback(self):
        stderr = io.StringIO()
        with patch("leads.cli.load_secrets"), patch.object(db, "connect", return_value=self.connection(False)), redirect_stderr(stderr):
            self.assertEqual(main(["state"]), 1)
        self.assertIn("already using", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class TestReviewSummary(unittest.TestCase):
    def test_summary_aggregates_reasons_without_disclosing_free_text_or_identities(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.db"
            with db.db_session(path) as conn:
                seed(conn)
                db.update_lead_status(conn, 1, "needs_review", "No owner match; SECRET PERSONAL NOTE")
                seed(conn, "SECOND")
                db.update_lead_status(conn, 2, "needs_review", "No contact; Multiple owner matches")
                seed(conn, "THIRD")
            out = io.StringIO()
            with patch("leads.cli.load_secrets"), redirect_stdout(out):
                self.assertEqual(main(["review", "summary", "--db", str(path)]), 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["rows"], 2)
            self.assertEqual(payload["counties"][0]["no_owner_match"], 1)
            self.assertEqual(payload["counties"][0]["no_contact"], 1)
            self.assertNotIn("SECRET", out.getvalue())
            self.assertNotIn("JANE", out.getvalue())
            out = io.StringIO()
            with patch("leads.cli.load_secrets"), redirect_stdout(out):
                self.assertEqual(main(["review", "summary", "--status", "dead_letter", "--db", str(path)]), 0)
            self.assertEqual(json.loads(out.getvalue()), {"rows": 0, "counties": []})


class TestDailyScript(unittest.TestCase):
    def test_daily_invocation_is_bounded_and_preserves_existing_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "python"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
            fake.chmod(0o700)
            env = dict(os.environ, LEADS_PYTHON=str(fake))
            script = Path(__file__).resolve().parents[1] / "scripts/run_daily.sh"
            result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["-m", "leads", "run", "--all-enabled", "--since", "7d", "--max-enrich", "5", "--append-only"])
