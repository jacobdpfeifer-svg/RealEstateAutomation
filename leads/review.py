from __future__ import annotations

import csv
import io
from datetime import datetime

from adapters.registry import load_counties
from leads import db, ledger
from leads.models import PipelineStatus
from leads.research_hints import build_research_hints, looks_like_entity, parse_situs
from providers.skip_trace.stub import contact_from_manual


def _review_status(status: str) -> str | None:
    mapping = {
        "pending": PipelineStatus.NEEDS_REVIEW.value,
        "needs_review": PipelineStatus.NEEDS_REVIEW.value,
        "approved": PipelineStatus.APPROVED.value,
        "rejected": PipelineStatus.REJECTED.value,
        "all": None,
    }
    return mapping.get(status, status)


def list_review_queue(conn, status: str = "pending") -> list[dict]:
    rows = db.list_leads(conn, _review_status(status))
    return [dict(r) for r in rows]


def review_summary(conn, status: str = "pending") -> dict:
    """Aggregate in SQL so no identities or arbitrary review notes leave the DB."""
    reasons = {
        "no_owner_match": "No owner match",
        "no_contact": "No contact",
        "multiple_matches": "Multiple owner matches",
        "entity_defendant": "Entity defendant",
        "low_confidence": "Low owner-match confidence",
    }
    columns = ", ".join(
        f"SUM(CASE WHEN l.review_note LIKE ? THEN 1 ELSE 0 END) AS {key}"
        for key in reasons
    )
    params = tuple(f"%{reason}%" for reason in reasons.values())
    query = (
        "SELECT c.county_fips, l.pipeline_status, COUNT(*) AS count, " + columns +
        " FROM lead l JOIN case_record c ON c.id = l.case_id"
    )
    key = _review_status(status)
    if key is not None:
        query += " WHERE l.pipeline_status = ?"
        params += (key,)
    query += " GROUP BY c.county_fips, l.pipeline_status ORDER BY c.county_fips, l.pipeline_status"
    rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    return {"rows": sum(row["count"] for row in rows), "counties": rows}


def approve_lead(conn, lead_id: int, note: str = "") -> None:
    db.update_lead_status(conn, lead_id, PipelineStatus.APPROVED.value, note)
    ledger.record(conn, entity_type="lead", entity_id=lead_id, event_type="lead_approved", notes=note)


def retry_lead(conn, lead_id: int, note: str = "") -> None:
    """Explicitly requeue a failed lead after investigating the cause."""
    cur = conn.execute(
        "UPDATE lead SET pipeline_status = 'new', enrichment_attempts = 0, review_note = ?, "
        "updated_at = ? WHERE id = ? AND pipeline_status IN ('error', 'dead_letter')",
        (note, datetime.utcnow().isoformat(), lead_id),
    )
    if cur.rowcount != 1:
        raise ValueError("Retry requires an existing error/dead_letter lead")
    ledger.record(conn, entity_type="lead", entity_id=lead_id, event_type="enrichment_retry", notes=note)


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


def research_hints_for_lead(conn, lead_id: int) -> dict:
    """Load property + case and build people-search URLs. No network, no writes."""
    row = conn.execute(
        """
        SELECT l.property_id, p.owner_of_record, p.situs_address, p.apn,
               c.defendant_raw, c.county_fips
        FROM lead l
        JOIN case_record c ON c.id = l.case_id
        LEFT JOIN property_record p ON p.id = l.property_id
        WHERE l.id = ?
        """,
        (lead_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Lead not found: {lead_id}")
    if not row["property_id"]:
        raise ValueError(f"Lead {lead_id} has no property — enrich first")
    situs = row["situs_address"] or ""
    name = (row["owner_of_record"] or row["defendant_raw"] or "").strip()
    state = ""
    try:
        counties = load_counties()
        cfg = counties.get(row["county_fips"] or "")
        if cfg:
            state = cfg.state
    except OSError:
        state = ""
    street, city, state, zip_code = parse_situs(situs, state=state)
    urls = build_research_hints(
        name=name, situs=situs, city=city, state=state, zip_code=zip_code
    )
    return {
        "lead_id": lead_id,
        "name": name,
        "situs": situs,
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_code,
        "apn": row["apn"] or "",
        "entity": looks_like_entity(name),
        "urls": urls,
    }


def format_research_hints(payload: dict) -> str:
    lines = []
    header = " — ".join(part for part in (payload.get("name"), payload.get("situs")) if part)
    if header:
        lines.append(header)
    for label, url in (payload.get("urls") or {}).items():
        lines.append(f"{label}: {url}")
    return "\n".join(lines) + ("\n" if lines else "")


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
    cur = conn.execute(
        """
        INSERT INTO contact_record (
            property_id, name, phone, email, address, provider, confidence, retrieved_at, manual_paste
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        RETURNING id
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
    contact_id = cur.fetchone()["id"]
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


def _csv_text(value):
    """Neutralize formula prefixes on export; never change stored data.

    CSV consumers vary: this is an initial-open mitigation, not a guarantee
    after a spreadsheet application saves and reopens the file.
    """
    if isinstance(value, str) and value:
        if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(
            ("=", "+", "-", "@", "＝", "＋", "－", "＠")
        ):
            return "'" + value
    return value


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
        "property_type",
        "total_value",
        "match_confidence",
        "contact_name",
        "phone",
        "email",
        "pipeline_status",
        "priority_score",
        "review_note",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {key: _csv_text(value) for key, value in {
                "lead_id": r.get("id"),
                "case_number": r.get("case_number"),
                "defendant_raw": r.get("defendant_raw"),
                "plaintiff": r.get("plaintiff"),
                "filed_date": r.get("filed_date"),
                "situs_address": r.get("situs_address"),
                "apn": r.get("apn"),
                "property_type": r.get("property_type"),
                "total_value": r.get("total_value"),
                "match_confidence": r.get("match_confidence"),
                "contact_name": r.get("contact_name"),
                "phone": r.get("phone"),
                "email": r.get("email"),
                "pipeline_status": r.get("pipeline_status"),
                "priority_score": r.get("priority_score"),
                "review_note": r.get("review_note"),
            }.items()}
        )
    return buf.getvalue()
