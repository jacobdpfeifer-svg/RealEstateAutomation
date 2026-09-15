from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class PipelineStatus(str, Enum):
    NEW = "new"
    ENRICHED = "enriched"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONTACTED = "contacted"
    CLOSED = "closed"
    ERROR = "error"


@dataclass
class CaseRecord:
    county_fips: str
    case_number: str
    plaintiff: str
    defendant_raw: str
    defendant_normalized: str
    case_type: str
    filed_date: date
    status: str
    source_url: str
    retrieved_at: datetime
    dedupe_key: str
    id: Optional[int] = None


@dataclass
class PropertyRecord:
    case_id: int
    apn: str
    situs_address: str
    owner_of_record: str
    tax_delinquent_amt: Optional[float]
    years_delinquent: Optional[int]
    assessor_url: str
    match_confidence: float
    id: Optional[int] = None


@dataclass
class ContactRecord:
    property_id: int
    name: str
    phone: str
    email: str
    address: str
    provider: str
    confidence: float
    retrieved_at: datetime
    id: Optional[int] = None


@dataclass
class Lead:
    case_id: int
    property_id: Optional[int]
    contact_id: Optional[int]
    pipeline_status: PipelineStatus
    priority_score: float = 0.0
    review_note: str = ""
    id: Optional[int] = None


@dataclass
class FetchLogEntry:
    entity_type: str
    entity_id: Optional[int]
    url: str
    http_status: int
    artifact_path: str
    error: str = ""
    retrieved_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CallLog:
    lead_id: int
    channel: str
    outcome: str
    offer_amount: Optional[float]
    notes: str
    called_at: datetime
