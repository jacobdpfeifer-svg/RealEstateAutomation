from __future__ import annotations

import argparse
import json
import sys
import os
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from adapters.registry import list_enabled_counties
from leads import db, ledger
from leads.pipeline import Pipeline
from leads.http import safe_error
from leads.observability import configure_logging, event
from leads.probe import catalog_keys, load_probe_catalog, probe_keys
from leads.review import (
    approve_lead,
    export_csv,
    list_review_queue,
    paste_contact,
    reject_lead,
    retry_lead,
    skip_lead,
)
from leads.secrets import load_secrets
from leads.utils import parse_since_days, write_private_text
from leads.validate import run_phase1_validation

_DB_HELP = (
    "Database path. Default leads.db uses DATABASE_URL when it is Postgres; "
    "any other path stays SQLite."
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def cmd_state(args: argparse.Namespace) -> int:
    with db.db_session(args.db) as conn:
        summary = db.state_summary(conn)
        backlog = conn.execute(
            "SELECT COUNT(*) AS n FROM lead WHERE pipeline_status = 'needs_review'"
        ).fetchone()["n"]
        errors = conn.execute(
            "SELECT COUNT(*) AS n FROM lead WHERE pipeline_status = 'error'"
        ).fetchone()["n"]
        dead_letter = conn.execute("SELECT COUNT(*) AS n FROM lead WHERE pipeline_status = 'dead_letter'").fetchone()["n"]
        last_err = conn.execute(
            "SELECT error, retrieved_at FROM fetch_log WHERE error != '' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        out = {
            "backend": db.backend_name(conn),
            "counties": summary,
            "review_backlog": backlog,
            "errors": errors,
            "dead_letter": dead_letter,
            "last_error": dict(last_err) if last_err else None,
        }
        print(json.dumps(out, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    allow_disabled = bool(getattr(args, "allow_disabled", False))
    if allow_disabled and args.all_enabled:
        print("--allow-disabled cannot be combined with --all-enabled", file=sys.stderr)
        return 2
    if allow_disabled and not args.county:
        print("--allow-disabled requires --county", file=sys.stderr)
        return 2
    since = date.today() - timedelta(days=parse_since_days(args.since))
    counties: list[str] = []
    if args.all_enabled:
        counties = [c.name.lower() for c in list_enabled_counties()]
    elif args.county:
        counties = [args.county.lower()]
    else:
        print("Specify --county or --all-enabled", file=sys.stderr)
        return 2

    failed = False
    run_id = uuid.uuid4().hex
    # Even schema initialization must not touch the configured database in dry-run.
    with db.pipeline_lock(args.db, disabled=args.dry_run), db.db_session(":memory:" if args.dry_run else args.db) as conn:
        pipe = Pipeline(
            conn,
            max_enrich=args.max_enrich,
            require_enabled=not allow_disabled,
        )
        for county in counties:
            started = time.monotonic()
            event("county_started", run_id=run_id, county=county)
            try:
                result = pipe.run_all(county, since, dry_run=args.dry_run)
                failed = failed or bool(result.get("enrich", {}).get("errors", 0))
                print(json.dumps({county: result}, indent=2))
                event("county_finished", run_id=run_id, county=county,
                      elapsed_seconds=round(time.monotonic() - started, 3), result=result)
            except Exception as exc:
                conn.rollback()
                failed = True
                event("county_failed", run_id=run_id, county=county, error=safe_error(exc),
                      elapsed_seconds=round(time.monotonic() - started, 3))
                print(f"{county}: {safe_error(exc)}; check source access and adapter contracts", file=sys.stderr)
    return 1 if failed else 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    with db.pipeline_lock(args.db), db.db_session(args.db) as conn:
        pipe = Pipeline(conn, max_enrich=args.max_enrich)
        if args.stage == "enrich-pending":
            result = pipe.enrich_pending(args.county.lower() if args.county else None)
        elif args.stage == "score":
            result = pipe.score()
        else:
            print(f"Unknown stage: {args.stage}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
        event("stage_finished", stage=args.stage, result=result)
    return 1 if result.get("errors", 0) else 0


def cmd_review(args: argparse.Namespace) -> int:
    with db.db_session(args.db) as conn:
        try:
            if args.review_cmd == "list":
                rows = list_review_queue(conn, args.status)
                print(json.dumps(rows, indent=2, default=str))
            elif args.review_cmd == "approve":
                approve_lead(conn, args.lead_id, args.note or "")
                print(f"Approved lead {args.lead_id}")
            elif args.review_cmd == "reject":
                reject_lead(conn, args.lead_id, args.note or "")
                print(f"Rejected lead {args.lead_id}")
            elif args.review_cmd == "skip":
                skip_lead(conn, args.lead_id, args.note or "")
                print(f"Skipped lead {args.lead_id}")
            elif args.review_cmd == "retry":
                retry_lead(conn, args.lead_id, args.note or "")
                print(f"Requeued lead {args.lead_id}")
            elif args.review_cmd == "paste":
                paste_contact(
                    conn,
                    args.lead_id,
                    name=args.name,
                    phone=args.phone or "",
                    email=args.email or "",
                    address=args.address or "",
                )
                print(f"Pasted contact for lead {args.lead_id}")
            else:
                print(f"Unknown review command: {args.review_cmd}", file=sys.stderr)
                return 2
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with db.db_session(args.db) as conn:
        csv_data = export_csv(conn, args.status)
        if args.output:
            write_private_text(Path(args.output), csv_data)
            print(f"Wrote {args.output}")
        else:
            print(csv_data, end="")
    return 0


def cmd_ledger(args: argparse.Namespace) -> int:
    with db.db_session(args.db) as conn:
        if args.ledger_cmd == "log":
            event_id = ledger.record(
                conn,
                entity_type=args.entity_type,
                entity_id=args.entity_id,
                event_type=args.event_type,
                weight=args.weight,
                channel=args.channel or "",
                template_version=args.template_version or "",
                matching_version=args.matching_version or "",
                notes=args.notes or "",
            )
            print(f"Logged outcome_event {event_id}")
        elif args.ledger_cmd == "report":
            print(json.dumps(ledger.report(conn), indent=2, default=str))
        elif args.ledger_cmd == "history":
            rows = ledger.history(
                conn,
                entity_type=args.entity_type,
                entity_id=args.entity_id,
            )
            print(json.dumps(rows, indent=2, default=str))
        else:
            print(f"Unknown ledger command: {args.ledger_cmd}", file=sys.stderr)
            return 2
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    root = _project_root()
    default_db = str(root / "leads.db")
    db_path = args.db
    if Path(args.db).resolve() == Path(default_db).resolve():
        db_path = str(root / "artifacts" / "phase1_validation.db")
    report = run_phase1_validation(db_path, since=args.since, skip_enrich=args.skip_enrich, max_enrich=args.max_enrich)
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    runs = report.get("runs", {})
    return 0 if runs and all(
        run.get("ok") and not run.get("result", {}).get("enrich", {}).get("errors", 0)
        for run in runs.values()
    ) else 1


def cmd_probe(args: argparse.Namespace) -> int:
    keys = catalog_keys()
    county = args.county.lower().strip()
    if county == "all":
        selected = keys
    elif county == "next":
        selected = [k for k in ("tarrant", "bexar", "maricopa") if k in keys]
    elif county in keys:
        selected = [county]
    else:
        print(f"Unknown county {county!r}. Known: {', '.join(keys)}, all, next", file=sys.stderr)
        return 2
    report = {
        "selected": selected,
        "catalog": [
            {"key": row["key"], "fips": row["fips"], "name": row["name"]}
            for row in load_probe_catalog()
        ],
        "probes": probe_keys(selected),
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0


def nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def build_parser() -> argparse.ArgumentParser:
    root = _project_root()
    parser = argparse.ArgumentParser(prog="leads", description="Tax lawsuit RE lead pipeline")
    parser.add_argument("--db", default=str(root / "leads.db"), help=_DB_HELP)
    sub = parser.add_subparsers(dest="command", required=True)

    p_state = sub.add_parser("state", help="Counts, errors, review backlog")
    p_state.set_defaults(func=cmd_state)

    p_run = sub.add_parser("run", help="Ingest + enrich + score")
    p_run.add_argument("--county", help="County name or fips (e.g. harris)")
    p_run.add_argument("--all-enabled", action="store_true")
    p_run.add_argument("--since", default="7d", help="Lookback e.g. 7d, 30d")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--max-enrich", type=nonnegative_int, default=None,
                       help="Maximum enrichment attempts across all selected counties; 0 = ingest only")
    p_run.add_argument(
        "--allow-disabled",
        action="store_true",
        help="Run one disabled county (requires --county; incompatible with --all-enabled)",
    )
    p_run.set_defaults(func=cmd_run)

    p_pipe = sub.add_parser("pipeline", help="Run individual pipeline stages")
    p_pipe.add_argument("stage", choices=["enrich-pending", "score"])
    p_pipe.add_argument("--county", default=None)
    p_pipe.add_argument("--max-enrich", type=nonnegative_int, default=None)
    p_pipe.set_defaults(func=cmd_pipeline)

    p_review = sub.add_parser("review", help="Human review queue")
    review_sub = p_review.add_subparsers(dest="review_cmd", required=True)

    p_list = review_sub.add_parser("list", help="List leads by status")
    p_list.add_argument("--status", default="pending")
    p_list.set_defaults(func=cmd_review)

    for action in ("approve", "reject", "skip", "retry"):
        p = review_sub.add_parser(action)
        p.add_argument("lead_id", type=int)
        p.add_argument("--note", default="")
        p.set_defaults(func=cmd_review)

    p_paste = review_sub.add_parser("paste", help="Manual skip trace paste")
    p_paste.add_argument("lead_id", type=int)
    p_paste.add_argument("--name", required=True)
    p_paste.add_argument("--phone", default="")
    p_paste.add_argument("--email", default="")
    p_paste.add_argument("--address", default="")
    p_paste.set_defaults(func=cmd_review)

    p_export = sub.add_parser("export", help="Export leads to CSV")
    p_export.add_argument("--status", default="approved")
    p_export.add_argument("--format", default="csv", choices=["csv"])
    p_export.add_argument("-o", "--output", default=None)
    p_export.set_defaults(func=cmd_export)

    p_ledger = sub.add_parser("ledger", help="Outcomes ledger: log wins/losses, view reports")
    ledger_sub = p_ledger.add_subparsers(dest="ledger_cmd", required=True)

    p_ledger_log = ledger_sub.add_parser("log", help="Append one outcome event")
    p_ledger_log.add_argument("entity_type", help="e.g. lead, match, draft")
    p_ledger_log.add_argument("entity_id", type=int)
    p_ledger_log.add_argument("event_type", help="e.g. replied, bounced, deal_closed_won")
    p_ledger_log.add_argument(
        "--weight",
        type=float,
        default=None,
        help="Override the default weight for this event_type",
    )
    p_ledger_log.add_argument("--channel", default="")
    p_ledger_log.add_argument("--template-version", default="")
    p_ledger_log.add_argument("--matching-version", default="")
    p_ledger_log.add_argument("--notes", default="")
    p_ledger_log.set_defaults(func=cmd_ledger)

    p_ledger_report = ledger_sub.add_parser(
        "report", help="Aggregate win/loss totals by entity, event, and template/matching version"
    )
    p_ledger_report.set_defaults(func=cmd_ledger)

    p_ledger_history = ledger_sub.add_parser("history", help="Raw event history, optionally filtered")
    p_ledger_history.add_argument("--entity-type", dest="entity_type", default=None)
    p_ledger_history.add_argument("--entity-id", dest="entity_id", type=int, default=None)
    p_ledger_history.set_defaults(func=cmd_ledger)

    p_validate = sub.add_parser(
        "validate",
        help="Phase 1 Harris/Dallas validation (dry-run Harris, Dallas fixtures, skip-trace preflight)",
    )
    p_validate.add_argument("--since", default="60d")
    p_validate.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Dallas ingest dry-run only (no DCAD / skip-trace)",
    )
    p_validate.add_argument("-o", "--output", default=None, help="Write JSON report")
    p_validate.add_argument("--max-enrich", type=nonnegative_int, default=5)
    p_validate.set_defaults(func=cmd_validate)

    p_probe = sub.add_parser("probe", help="Classify county clerk/tax URLs (bulk > api > portal)")
    p_probe.add_argument("--county", default="next", help="County key, all, or next")
    p_probe.add_argument("-o", "--output", default=None)
    p_probe.set_defaults(func=cmd_probe)

    # Suppressed defaults preserve a --db supplied before a subcommand.
    def add_database_option(parent):
        for action in parent._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    child.add_argument("--db", default=argparse.SUPPRESS, help=_DB_HELP)
                    add_database_option(child)
    add_database_option(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Ensure project root is importable when run as module
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    parser = build_parser()
    args = parser.parse_args(argv)
    os.umask(0o077)
    load_secrets()
    configure_logging()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
