from __future__ import annotations

import os
from datetime import date, datetime

from adapters.registry import get_clerk_adapter, get_skip_provider_for_county, get_tax_adapter
from leads import db
from leads.models import PipelineStatus
from leads.http import SourceBlockedError, safe_error
from leads.utils import is_entity_defendant, property_dedupe_key


MATCH_AUTO_THRESHOLD = 0.85


class Pipeline:
    def __init__(self, conn, config_path=None) -> None:
        self.conn = conn
        self.config_path = config_path
        self.max_attempts = int(os.environ.get("LEADS_MAX_ENRICH_ATTEMPTS", "3"))
        if self.max_attempts < 1:
            raise ValueError("LEADS_MAX_ENRICH_ATTEMPTS must be positive")

    def ingest_county(self, county_key: str, since: date, *, dry_run: bool = False) -> dict:
        clerk = get_clerk_adapter(county_key, self.config_path)
        cases = clerk.search_tax_suits(since)
        inserted = 0
        if dry_run:
            return {"county": county_key, "found": len(cases), "inserted": 0, "dry_run": True}
        for case in cases:
            existing = self.conn.execute(
                "SELECT id FROM case_record WHERE dedupe_key = ?", (case.dedupe_key,)
            ).fetchone()
            case_id = db.upsert_case(
                self.conn,
                {
                    "county_fips": case.county_fips,
                    "case_number": case.case_number,
                    "plaintiff": case.plaintiff,
                    "defendant_raw": case.defendant_raw,
                    "defendant_normalized": case.defendant_normalized,
                    "case_type": case.case_type,
                    "filed_date": case.filed_date.isoformat(),
                    "status": case.status,
                    "source_url": case.source_url,
                    "retrieved_at": case.retrieved_at.isoformat(),
                    "dedupe_key": case.dedupe_key,
                },
            )
            db.ensure_lead(self.conn, case_id, PipelineStatus.NEW.value)
            inserted += int(existing is None)
        for log in getattr(clerk, "fetch_logs", []):
            db.log_fetch(self.conn, entity_type="clerk", entity_id=None, url=log["url"],
                         http_status=log["status"], artifact_path=log["artifact"])
        self.conn.commit()  # Ingest survives interruption during external enrichment.
        return {"county": county_key, "found": len(cases), "inserted": inserted}

    def enrich_pending(self, county_key: str | None = None) -> dict:
        query = """
            SELECT l.id AS lead_id, l.case_id, l.enrichment_attempts,
                   c.defendant_normalized, c.defendant_raw, c.county_fips
            FROM lead l
            JOIN case_record c ON c.id = l.case_id
            WHERE l.pipeline_status IN ('new', 'error')
        """
        params: tuple = ()
        if county_key:
            from adapters.registry import load_counties

            cfg = load_counties(self.config_path)[county_key.lower()]
            query += " AND c.county_fips = ?"
            params = (cfg.fips,)
        rows = self.conn.execute(query, params).fetchall()
        enriched = 0
        needs_review = 0
        errors = 0
        dead_letter = 0
        processed = 0
        for row in rows:
            self.conn.execute("SAVEPOINT enrich_lead")
            try:
                result = self._enrich_case(row["case_id"], row["defendant_normalized"], row["defendant_raw"])
                if result == PipelineStatus.ENRICHED.value:
                    enriched += 1
                else:
                    needs_review += 1
            except Exception as exc:
                self.conn.execute("ROLLBACK TO SAVEPOINT enrich_lead")
                attempts = row["enrichment_attempts"] + 1
                status = "dead_letter" if attempts >= self.max_attempts else PipelineStatus.ERROR.value
                db.update_lead_status(self.conn, row["lead_id"], status, "enrichment: " + safe_error(exc))
                self.conn.execute("UPDATE lead SET enrichment_attempts = ? WHERE id = ?", (attempts, row["lead_id"]))
                db.log_fetch(self.conn, entity_type="lead", entity_id=row["lead_id"],
                             url="", http_status=0, error="enrichment: " + safe_error(exc))
                errors += 1
                dead_letter += int(status == "dead_letter")
                # Subsequent cases cannot succeed against a blocked host this run.
                if isinstance(exc, SourceBlockedError):
                    self.conn.execute("RELEASE SAVEPOINT enrich_lead")
                    self.conn.commit()
                    processed += 1
                    break
            self.conn.execute("RELEASE SAVEPOINT enrich_lead")
            self.conn.commit()  # One lead is the durable resume boundary.
            processed += 1
        return {"processed": processed, "enriched": enriched, "needs_review": needs_review,
                "errors": errors, "dead_letter": dead_letter}

    def _enrich_case(self, case_id: int, defendant_norm: str, defendant_raw: str) -> str:
        case_row = self.conn.execute(
            "SELECT county_fips FROM case_record WHERE id = ?", (case_id,)
        ).fetchone()
        county_fips = case_row["county_fips"]
        from adapters.registry import load_counties

        counties = load_counties(self.config_path)
        cfg = next(c for c in counties.values() if c.fips == county_fips)
        tax = get_tax_adapter(cfg.name.lower(), self.config_path)
        skip = get_skip_provider_for_county(cfg.name.lower(), self.config_path)

        candidates = tax.search_by_owner(defendant_norm or defendant_raw)
        if not candidates:
            self.conn.execute(
                "UPDATE lead SET pipeline_status = ?, review_note = ?, updated_at = ? WHERE case_id = ?",
                (PipelineStatus.NEEDS_REVIEW.value, "No owner match; manual research required",
                 datetime.utcnow().isoformat(), case_id),
            )
            return PipelineStatus.NEEDS_REVIEW.value

        best = candidates[0]
        multi_hit = len(candidates) > 1
        entity = is_entity_defendant(defendant_raw)
        low_conf = best.match_confidence < MATCH_AUTO_THRESHOLD

        prop_key = property_dedupe_key(best.apn or best.situs_address)
        self.conn.execute(
            """
            INSERT INTO property_record (
                case_id, apn, situs_address, owner_of_record, tax_delinquent_amt,
                years_delinquent, assessor_url, match_confidence, dedupe_key,
                property_type, total_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id, dedupe_key) DO UPDATE SET
                match_confidence = excluded.match_confidence,
                property_type = excluded.property_type,
                total_value = excluded.total_value
            """,
            (
                case_id,
                best.apn,
                best.situs_address,
                best.owner_of_record,
                best.tax_delinquent_amt,
                best.years_delinquent,
                best.assessor_url,
                best.match_confidence,
                prop_key,
                best.property_type,
                best.total_value,
            ),
        )
        prop_id = self.conn.execute(
            "SELECT id FROM property_record WHERE case_id = ? AND dedupe_key = ?",
            (case_id, prop_key),
        ).fetchone()["id"]

        parts = best.situs_address.rsplit(",", 2)
        city = parts[-2].strip() if len(parts) >= 2 else ""
        state = cfg.state
        street = parts[0].strip() if parts else best.situs_address

        contacts = skip.trace(
            name=best.owner_of_record or defendant_raw,
            address=street,
            city=city,
            state=state,
            apn=best.apn,
        )
        contact_id = None
        if contacts:
            c = contacts[0]
            c.property_id = prop_id
            cur = self.conn.execute(
                """
                INSERT INTO contact_record (
                    property_id, name, phone, email, address, provider, confidence, retrieved_at, manual_paste
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                RETURNING id
                """,
                (
                    prop_id,
                    c.name,
                    c.phone,
                    c.email,
                    c.address,
                    c.provider,
                    c.confidence,
                    c.retrieved_at.isoformat(),
                ),
            )
            contact_id = cur.fetchone()["id"]

        status = PipelineStatus.ENRICHED.value
        if multi_hit or entity or low_conf or not contacts:
            status = PipelineStatus.NEEDS_REVIEW.value

        reasons = []
        if multi_hit:
            reasons.append("Multiple owner matches")
        if entity:
            reasons.append("Entity defendant")
        if low_conf:
            reasons.append("Low owner-match confidence")
        if not contacts:
            reasons.append("No contact; manual skip trace required")

        self.conn.execute(
            """
            UPDATE lead SET property_id = ?, contact_id = ?, pipeline_status = ?, updated_at = ?,
                            review_note = ?
            WHERE case_id = ?
            """,
            (prop_id, contact_id, status, datetime.utcnow().isoformat(), "; ".join(reasons), case_id),
        )
        return status

    def score(self) -> dict:
        rows = self.conn.execute(
            """
            SELECT l.id, c.filed_date, p.match_confidence, p.tax_delinquent_amt
            FROM lead l
            JOIN case_record c ON c.id = l.case_id
            LEFT JOIN property_record p ON p.id = l.property_id
            WHERE l.pipeline_status IN ('enriched', 'needs_review', 'new')
            """
        ).fetchall()
        updated = 0
        for row in rows:
            days = max(0, (date.today() - date.fromisoformat(row["filed_date"])).days)
            conf = row["match_confidence"] or 0.0
            delinq = row["tax_delinquent_amt"] or 0.0
            score = conf * 50 + min(days, 365) / 365 * 30 + min(delinq, 50000) / 50000 * 20
            self.conn.execute(
                "UPDATE lead SET priority_score = ?, updated_at = ? WHERE id = ?",
                (round(score, 2), datetime.utcnow().isoformat(), row["id"]),
            )
            updated += 1
        return {"scored": updated}

    def run_all(self, county_key: str, since: date, *, dry_run: bool = False) -> dict:
        ingest = self.ingest_county(county_key, since, dry_run=dry_run)
        if dry_run:
            return {"ingest": ingest}
        enrich = self.enrich_pending(county_key)
        score = self.score()
        return {"ingest": ingest, "enrich": enrich, "score": score}
