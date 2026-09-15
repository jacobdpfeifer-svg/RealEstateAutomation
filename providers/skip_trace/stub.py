from __future__ import annotations

from datetime import datetime

from leads.models import ContactRecord


class StubSkipTraceProvider:
    """Phase 1 stub — returns empty; use manual paste via CLI."""

    name = "stub"

    def trace(
        self,
        name: str,
        address: str,
        city: str,
        state: str,
        apn: str = "",
    ) -> list[ContactRecord]:
        return []


class ManualSkipTraceProvider:
    """Wrap manually pasted contact data."""

    name = "manual"

    def trace(
        self,
        name: str,
        address: str,
        city: str,
        state: str,
        apn: str = "",
    ) -> list[ContactRecord]:
        return []


def contact_from_manual(
    property_id: int,
    *,
    name: str,
    phone: str = "",
    email: str = "",
    address: str = "",
    confidence: float = 1.0,
) -> ContactRecord:
    return ContactRecord(
        id=None,
        property_id=property_id,
        name=name,
        phone=phone,
        email=email,
        address=address,
        provider="manual_paste",
        confidence=confidence,
        retrieved_at=datetime.utcnow(),
    )
