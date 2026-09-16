"""Phase 1 live/fixture validation: Harris + Dallas ingest without assuming API keys."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from adapters.registry import get_skip_provider_for_county
from leads import db
from leads.http import safe_error
from leads.pipeline import Pipeline
from leads.secrets import load_secrets
from leads.utils import parse_since_days


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _present(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def preflight() -> dict[str, Any]:
    load_secrets()
    root = _project_root()
    fixture_dir = Path(
        os.environ.get(
            "DALLAS_CLERK_FIXTURE_DIR",
            str(root / "artifacts" / "raw" / "dallas" / "clerk"),
        )
    )
    html_files = sorted(fixture_dir.glob("*.html")) if fixture_dir.exists() else []
    skip = get_skip_provider_for_county("dallas")
    return {
        "secrets": {
            "BATCHDATA_API_KEY": "present" if _present("BATCHDATA_API_KEY") else "missing",
            "ANTICAPTCHA_API_KEY": "present" if _present("ANTICAPTCHA_API_KEY") else "missing",
            "DALLAS_RECAPTCHA_TOKEN": "present" if _present("DALLAS_RECAPTCHA_TOKEN") else "missing",
        },
        "skip_trace_provider": skip.name,
        "dallas_clerk_live": os.environ.get("DALLAS_CLERK_LIVE_ENABLED", "") == "1",
        "dallas_fixtures": {
            "dir": str(fixture_dir),
            "html_files": len(html_files),
            "names": [p.name for p in html_files],
        },
    }


def _run_county(pipe: Pipeline, county: str, since: date, *, dry_run: bool) -> dict[str, Any]:
    try:
        return {"ok": True, "result": pipe.run_all(county, since, dry_run=dry_run)}
    except Exception as exc:
        return {"ok": False, "error": safe_error(exc)}


def run_phase1_validation(
    db_path: Path | str | None = None,
    *,
    since: str = "60d",
    skip_enrich: bool = False,
) -> dict[str, Any]:
    """
    Dry-run Harris (live bulk listing) and run Dallas from fixtures into a
    dedicated validation DB. Skip-trace uses BatchData when keyed, else stub.
    """
    checks = preflight()
    since_date = date.today() - timedelta(days=parse_since_days(since))
    root = _project_root()
    path = Path(db_path) if db_path else root / "artifacts" / "phase1_validation.db"
    path.parent.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "preflight": checks,
        "since": since,
        "since_date": since_date.isoformat(),
        "db": str(path),
        "runs": {},
    }

    with db.db_session(path) as conn:
        pipe = Pipeline(conn)
        report["runs"]["harris_dry_run"] = _run_county(pipe, "harris", since_date, dry_run=True)
        dallas_dry = skip_enrich
        report["runs"]["dallas"] = _run_county(pipe, "dallas", since_date, dry_run=dallas_dry)
        report["state"] = db.state_summary(conn)
        backlog = conn.execute(
            "SELECT COUNT(*) AS n FROM lead WHERE pipeline_status = 'needs_review'"
        ).fetchone()["n"]
        report["review_backlog"] = backlog

    blockers: list[str] = []
    if checks["secrets"]["BATCHDATA_API_KEY"] == "missing":
        blockers.append(
            "BATCHDATA_API_KEY missing — skip-trace fell back to stub; contacts will be empty until the key is set."
        )
    if not checks["dallas_clerk_live"] and checks["dallas_fixtures"]["html_files"] == 0:
        blockers.append(
            "Dallas clerk requires saved Odyssey HTML or explicitly enabled, permitted live access."
        )
    report["blockers"] = blockers
    report["ready_for_daily"] = (
        not skip_enrich and not blockers
        and all(run.get("ok") and not run.get("result", {}).get("enrich", {}).get("errors", 0)
                for run in report["runs"].values())
    )
    return report
