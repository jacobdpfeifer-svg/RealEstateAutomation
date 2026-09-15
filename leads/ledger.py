"""
Outcomes ledger: an append-only record of what the system did and what
happened as a result, tied to any entity (a lead, a buyer match, a drafted
or sent email, ...). This is the "memory" the matching and outreach
subsystems will eventually learn from (matching / outreach phases).

Nothing here trains a model. It just makes sure every win and every loss is
captured, with enough detail (which template/matching version produced it)
to compare later. Aggregation into stats happens in report(); anything more
automated than that is future work and should stay human-approved per the
guardrails in the project plan.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from leads import db

# Canonical event vocabulary with a default weight. Callers can override the
# weight per-call, but using these keeps the ledger comparable across the
# codebase instead of every call site inventing its own scale.
DEFAULT_WEIGHTS: dict[str, float] = {
    # neutral log entries
    "sent": 0.0,
    "opened": 0.0,
    # positive signals
    "replied": 1.0,
    "positive_reply": 2.0,
    "match_accepted": 1.0,
    "lead_approved": 1.0,
    "deal_closed_won": 5.0,
    # negative signals
    "no_response": -0.5,
    "negative_reply": -1.0,
    "match_declined": -1.0,
    "lead_rejected": -1.0,
    "bounced": -1.0,
    "unsubscribed": -2.0,
    "deal_closed_lost": -2.0,
}


def record(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int,
    event_type: str,
    weight: float | None = None,
    channel: str = "",
    template_version: str = "",
    matching_version: str = "",
    notes: str = "",
) -> int:
    """Append one outcome row. Never updates or deletes an existing row."""
    resolved_weight = DEFAULT_WEIGHTS.get(event_type, 0.0) if weight is None else weight
    return db.insert_outcome_event(
        conn,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        weight=resolved_weight,
        channel=channel,
        template_version=template_version,
        matching_version=matching_version,
        notes=notes,
    )


def history(
    conn: sqlite3.Connection,
    *,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = db.outcome_events(conn, entity_type=entity_type, entity_id=entity_id)
    return [dict(r) for r in rows]


def report(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Aggregate the ledger into simple counting stats — no model training.
    This is deliberately just arithmetic: total win/loss weight and counts,
    broken down by entity type, event type, and template/matching version,
    so it's obvious which drafts or matching logic are actually working.
    """
    rows = db.outcome_summary(conn)
    totals = {"events": 0, "net_weight": 0.0, "wins": 0, "losses": 0}
    for r in rows:
        n = r["n"] or 0
        w = r["total_weight"] or 0.0
        totals["events"] += n
        totals["net_weight"] += w
        if w > 0:
            totals["wins"] += n
        elif w < 0:
            totals["losses"] += n
    return {"totals": totals, "breakdown": rows}
