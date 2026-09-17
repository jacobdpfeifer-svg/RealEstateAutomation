"""
Outreach drafting (docs/PROJECT_PLAN.md §2, §5, §6, phase 6) — shipped first
against the seller pipeline, as the plan directs, before any buyer-side
delivery exists.

This module never sends email. It renders a draft grounded in the lead's
own DB record, runs it past leads/compliance.py, and stores it in
email_draft with status='queued'. Turning a queued row into an actual
Gmail draft is a separate, human-in-the-loop step outside this process
(Gmail MCP tools or the Gmail API) — see docs/OUTREACH_HANDOFF.md. That
step reports the resulting Gmail draft id back via mark_drafted() so a
later `queue` run doesn't duplicate it. Nothing here creates a standing
auto-send rule; every send is still a separate, explicit human action.
"""

from __future__ import annotations

from typing import Any

from leads import db, ledger
from leads.compliance import can_spam_footer, check_eligibility, load_sender_config

TEMPLATE_VERSION = "seller_v1"

_OPT_OUT_INSTRUCTIONS = (
    "We remove you from future outreach within 10 business days of any opt-out request."
)


def _format_money(amount: float | None) -> str:
    if amount is None:
        return "an outstanding balance"
    return f"${amount:,.0f}"


def render_subject(row: dict[str, Any]) -> str:
    address = row.get("situs_address") or "your property"
    return f"Regarding the tax matter on {address}"


def render_body(row: dict[str, Any], sender) -> str:
    """Grounded in the lead's own record only — no marketing language implying
    an ownership/contractual interest the sender doesn't have (§2)."""
    name = (row.get("contact_name") or "").strip() or "there"
    address = row.get("situs_address") or "your property"
    case_number = row.get("case_number") or "on file"
    amount = _format_money(row.get("tax_delinquent_amt"))
    years = row.get("years_delinquent")
    years_clause = f" for {years} year(s)" if years else ""
    body = (
        f"Hi {name},\n\n"
        f"I'm reaching out about the tax matter tied to {address} (case {case_number}), "
        f"showing {amount} delinquent{years_clause}. We work with property owners in this "
        f"situation to look at options before it goes further.\n\n"
        f"If you'd like to talk it through, just reply to this email.\n\n"
        f"Best,\n{sender.name}"
    )
    return body + can_spam_footer(sender, opt_out_instructions=_OPT_OUT_INSTRUCTIONS)


def queue_ready_leads(
    conn,
    *,
    county_fips: str | None = None,
    override_high_risk_state: bool = False,
) -> dict[str, Any]:
    """Draft outreach for approved leads with a contact email.

    Fails safe: raises compliance.SenderConfigMissing rather than drafting a
    non-compliant email when the operator hasn't set a sender identity yet.
    """
    sender = load_sender_config()
    queued: list[int] = []
    skipped: dict[str, int] = {}
    for row in db.list_leads(conn, "approved"):
        row = dict(row)
        if county_fips and row.get("county_fips") != county_fips:
            continue
        if db.has_active_draft(conn, row["id"]):
            skipped["already_queued"] = skipped.get("already_queued", 0) + 1
            continue
        eligible, reason = check_eligibility(
            conn,
            email=row.get("email") or "",
            county_fips=row.get("county_fips") or "",
            override_high_risk_state=override_high_risk_state,
        )
        if not eligible:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        draft_id = db.insert_email_draft(
            conn,
            lead_id=row["id"],
            case_id=row["case_id"],
            to_email=row["email"],
            to_name=row.get("contact_name") or "",
            subject=render_subject(row),
            body=render_body(row, sender),
            template_version=TEMPLATE_VERSION,
        )
        ledger.record(
            conn,
            entity_type="draft",
            entity_id=draft_id,
            event_type="drafted",
            channel="email",
            template_version=TEMPLATE_VERSION,
            notes=f"lead {row['id']}",
        )
        queued.append(draft_id)
    return {"queued": queued, "skipped": skipped}


def list_digest(conn, status: str = "queued") -> list[dict[str, Any]]:
    return [dict(r) for r in db.list_email_drafts(conn, status)]


def mark_drafted(conn, draft_id: int, gmail_draft_id: str) -> None:
    """Record that a human/agent created the real Gmail draft for this row."""
    db.update_email_draft_status(conn, draft_id, "drafted_in_gmail", gmail_draft_id=gmail_draft_id)
    ledger.record(
        conn,
        entity_type="draft",
        entity_id=draft_id,
        event_type="drafted_in_gmail",
        channel="email",
    )


def record_opt_out(conn, email: str, note: str = "") -> int:
    suppression_id = db.add_suppression(conn, email, note or "opt_out")
    ledger.record(
        conn,
        entity_type="suppression",
        entity_id=suppression_id,
        event_type="unsubscribed",
        channel="email",
        notes=note,
    )
    return suppression_id
