from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from adapters.registry import list_enabled_counties
from leads import db, ledger
from leads.pipeline import Pipeline
from leads.review import (
    approve_lead,
    export_csv,
    list_review_queue,
    paste_contact,
    reject_lead,
    skip_lead,
)
from leads.secrets import load_secrets
from leads.utils import parse_since_days


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


# Load config/secrets.env once at import so BatchData / captcha keys are available.
load_secrets()


def cmd_state(args: argparse.Namespace) -> int:
    with db.db_session(args.db) as conn:
        summary = db.state_summary(conn)
        backlog = conn.execute(
            "SELECT COUNT(*) AS n FROM lead WHERE pipeline_status = 'needs_review'"
        ).fetchone()["n"]
        errors = conn.execute(
            "SELECT COUNT(*) AS n FROM lead WHERE pipeline_status = 'error'"
        ).fetchone()["n"]
        last_err = conn.execute(
            "SELECT error, retrieved_at FROM fetch_log WHERE error != '' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        out = {
            "counties": summary,
            "review_backlog": backlog,
            "errors": errors,
            "last_error": dict(last_err) if last_err else None,
        }
        print(json.dumps(out, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    since = date.today() - timedelta(days=parse_since_days(args.since))
    counties: list[str] = []
    if args.all_enabled:
        counties = [c.name.lower() for c in list_enabled_counties()]
    elif args.county:
        counties = [args.county.lower()]
    else:
        print("Specify --county or --all-enabled", file=sys.stderr)
        return 2

    with db.db_session(args.db) as conn:
        pipe = Pipeline(conn)
        for county in counties:
            try:
                result = pipe.run_all(county, since, dry_run=args.dry_run)
                print(json.dumps({county: result}, indent=2))
            except (NotImplementedError, RuntimeError) as exc:
                print(f"{county}: {exc}", file=sys.stderr)
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    with db.db_session(args.db) as conn:
        pipe = Pipeline(conn)
        if args.stage == "enrich-pending":
            result = pipe.enrich_pending(args.county.lower() if args.county else None)
        elif args.stage == "score":
            result = pipe.score()
        else:
            print(f"Unknown stage: {args.stage}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2))
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    with db.db_session(args.db) as conn:
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
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    with db.db_session(args.db) as conn:
        csv_data = export_csv(conn, args.status)
        if args.output:
            Path(args.output).write_text(csv_data, encoding="utf-8")
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


def build_parser() -> argparse.ArgumentParser:
    root = _project_root()
    parser = argparse.ArgumentParser(prog="leads", description="Tax lawsuit RE lead pipeline")
    parser.add_argument("--db", default=str(root / "leads.db"), help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_state = sub.add_parser("state", help="Counts, errors, review backlog")
    p_state.set_defaults(func=cmd_state)

    p_run = sub.add_parser("run", help="Ingest + enrich + score")
    p_run.add_argument("--county", help="County name or fips (e.g. harris)")
    p_run.add_argument("--all-enabled", action="store_true")
    p_run.add_argument("--since", default="7d", help="Lookback e.g. 7d, 30d")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_pipe = sub.add_parser("pipeline", help="Run individual pipeline stages")
    p_pipe.add_argument("stage", choices=["enrich-pending", "score"])
    p_pipe.add_argument("--county", default=None)
    p_pipe.set_defaults(func=cmd_pipeline)

    p_review = sub.add_parser("review", help="Human review queue")
    review_sub = p_review.add_subparsers(dest="review_cmd", required=True)

    p_list = review_sub.add_parser("list", help="List leads by status")
    p_list.add_argument("--status", default="pending")
    p_list.set_defaults(func=cmd_review)

    for action in ("approve", "reject", "skip"):
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

    return parser


def main(argv: list[str] | None = None) -> int:
    # Ensure project root is importable when run as module
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
