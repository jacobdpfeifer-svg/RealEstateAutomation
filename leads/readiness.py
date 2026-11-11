"""Current inventory evidence; counts do not certify ownership or outreach consent."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from leads import db
from leads.review import review_summary


def readiness_report(conn) -> dict:
    rows = conn.execute(
        """
        SELECT c.county_fips, COUNT(*) AS leads,
          SUM(CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END) AS with_property,
          SUM(CASE WHEN TRIM(COALESCE(ct.phone, '')) != '' OR
                        TRIM(COALESCE(ct.email, '')) != '' THEN 1 ELSE 0 END) AS with_contact,
          SUM(CASE WHEN l.pipeline_status = 'approved' AND p.id IS NOT NULL AND
                       (TRIM(COALESCE(ct.phone, '')) != '' OR
                        TRIM(COALESCE(ct.email, '')) != '') THEN 1 ELSE 0 END) AS approved_with_contact,
          SUM(CASE WHEN c.source_url LIKE ? OR c.source_url LIKE ?
                   THEN 1 ELSE 0 END) AS sample_source_marked,
          SUM(CASE WHEN l.pipeline_status IN ('new', 'error') THEN 1 ELSE 0 END) AS resumable
        FROM lead l JOIN case_record c ON c.id = l.case_id
        LEFT JOIN property_record p ON p.id = l.property_id
        LEFT JOIN contact_record ct ON ct.id = l.contact_id
        GROUP BY c.county_fips ORDER BY c.county_fips
        """,
        ("%sample%", "%tests/fixtures%"),
    ).fetchall()
    counties = [dict(row) for row in rows]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "backend": db.backend_name(conn),
        "batchdata_key_present": bool(os.getenv("BATCHDATA_API_KEY", "").strip()),
        "counties": counties,
        "approved_with_contact": sum(row["approved_with_contact"] for row in counties),
        "review": review_summary(conn, "all"),
        "buyer_pipeline_implemented": False,
        "outreach_pipeline_implemented": False,
        "limits": [
            "Contact counts do not verify ownership, source authenticity, deliverability, or permission to contact.",
            "Sample detection uses source-path markers; an unmarked source is not certified authentic.",
            "Adding a provider key does not resume needs_review leads.",
            "This check makes no provider requests and does not assess source freshness or paid spending limits.",
        ],
    }
