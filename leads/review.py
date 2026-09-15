from __future__ import annotations

import csv
import io
from datetime import datetime

from leads import db, ledger
from leads.models import PipelineStatus
from providers.skip_trace.stub import contact_from_manual


def list_review_queue(conn, status: str = "pending") -> list[dict]:
    mapping = {
        "pending": PipelineStatus.NEEDS_REVIEW.value,
        "needs_review": PipelineStatus.NEEDS_REVIEW.value,
        "approved": PipelineStatus.APPROVED.value,
        "rejected": PipelineStatus.REJECTED.value,
        "all": None,
    }
    key = mapping.get(status, status)
    rows = db.list_leads(conn, key)
    return [dict(r) for r in rows]


def approve_lead(conn, lead_id: int, note: str = "") -> None:
    db.update_lead_status(conn, lead_id, PipelineStatus.APPROVED.value, note)
    ledger.record(conn, entity_type="lead", entity_id=lead_id, event_type="lead_approved", notes=note)


def reject_lead(conn, lead_id: int, note: str = "") -> None:
    db.update_lead_status(conn, lead_id, PipelineStatus.REJECTED.value, note)
    ledger.record(conn, entity_type="lead", entity_id=lead_id, event_type="lead_rejected", notes=note)


def skip_lead(conn, lead_id: int, note: str = "") -> None:
    db.update_lead_status(conn, lead_id, PipelineStatus.REJECTED.value, note or "skipped")
    ledger.record(
        conn,
        entity_type="lead",
        entity_id=lead_id,
        event_type="lead_rejected",
        notes=note or "skipped",
    )


def paste_contact(
    conn,
    lead_id: int,
    *,
    name: str,
    phone: str = "",
    email: str = "",
    address: str = "",
) -> None:
    row = conn.execute(
        "SELECT property_id, pipeline_status FROM lead WHERE id = ?", (lead_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Lead not found: {lead_id}")
    if not row["property_id"]:
        raise ValueError(f"Lead {lead_id} has no property — enrich first")
    contact = contact_from_manual(
        row["property_id"], name=name, phone=phone, email=email, address=address
    )
    conn.execute(
        """
        INSERT INTO contact_record (
            property_id, name, phone, email, address, provider, confidence, retrieved_at, manual_paste
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            contact.property_id,
            contact.name,
            contact.phone,
            contact.email,
            contact.address,
            contact.provider,
            contact.confidence,
            contact.retrieved_at.isoformat(),
        ),
    )
    contact_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        UPDATE lead SET contact_id = ?, pipeline_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            contact_id,
            PipelineStatus.NEEDS_REVIEW.value,
            datetime.utcnow().isoformat(),
            lead_id,
        ),
    )


def export_csv(conn, status: str = "approved") -> str:
    rows = list_review_queue(conn, status)
    buf = io.StringIO()
    fields = [
        "lead_id",
        "case_number",
        "defendant_raw",
        "plaintiff",
        "filed_date",
        "situs_address",
        "apn",
        "match_confidence",
        "contact_name",
        "phone",
        "email",
        "pipeline_status",
        "priority_score",
        "review_note",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {
                "lead_id": r.get("id"),
                "case_number": r.get("case_number"),
                "defendant_raw": r.get("defendant_raw"),
                "plaintiff": r.get("plaintiff"),
                "filed_date": r.get("filed_date"),
                "situs_address": r.get("situs_address"),
                "apn": r.get("apn"),
                "match_confidence": r.get("match_confidence"),
                "contact_name": r.get("contact_name"),
                "phone": r.get("phone"),
                "email": r.get("email"),
                "pipeline_status": r.get("pipeline_status"),
                "priority_score": r.get("priority_score"),
                "review_note": r.get("review_note"),
            }
        )
    return buf.getvalue()
