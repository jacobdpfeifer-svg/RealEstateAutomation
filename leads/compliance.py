"""
Compliance gates for the outreach subsystem (docs/PROJECT_PLAN.md §2, §6).

Nothing here sends anything. It only decides whether a draft is allowed to
be built at all: a truthful sender identity/address (CAN-SPAM), a
suppression-list check (opt-outs), and a per-state hold for jurisdictions
where wholesaling/broker rules need re-verification before outreach.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from adapters.registry import load_counties
from leads import db

# States named in docs/PROJECT_PLAN.md §6 to re-verify wholesaling/broker
# rules for before enabling outreach there. One place to update, not a
# literal scattered across call sites.
DEFAULT_HIGH_RISK_STATES = {"KY", "MD", "OK", "NC", "SC", "PA", "IL"}


def high_risk_states() -> set[str]:
    """The operator can widen this list via env; it never narrows silently."""
    extra = os.environ.get("OUTREACH_HIGH_RISK_STATES", "")
    extra_states = {s.strip().upper() for s in extra.split(",") if s.strip()}
    return DEFAULT_HIGH_RISK_STATES | extra_states


class SenderConfigMissing(RuntimeError):
    """CAN-SPAM requires truthful sender identification and a physical address
    before any draft is built, not just before it's sent."""


@dataclass(frozen=True)
class SenderConfig:
    name: str
    physical_address: str
    from_email: str


def load_sender_config() -> SenderConfig:
    name = os.environ.get("OUTREACH_SENDER_NAME", "").strip()
    address = os.environ.get("OUTREACH_SENDER_ADDRESS", "").strip()
    from_email = os.environ.get("OUTREACH_FROM_EMAIL", "").strip()
    missing = [
        key
        for key, value in (
            ("OUTREACH_SENDER_NAME", name),
            ("OUTREACH_SENDER_ADDRESS", address),
            ("OUTREACH_FROM_EMAIL", from_email),
        )
        if not value
    ]
    if missing:
        raise SenderConfigMissing(
            "Set " + ", ".join(missing) + " in config/secrets.env before queueing outreach "
            "drafts (CAN-SPAM requires truthful sender identification and a physical mailing "
            "address in every message)."
        )
    return SenderConfig(name=name, physical_address=address, from_email=from_email)


def can_spam_footer(sender: SenderConfig, *, opt_out_instructions: str) -> str:
    return (
        f"\n\n---\n{sender.name}\n{sender.physical_address}\n\n"
        f'To stop receiving messages like this, reply "unsubscribe" or contact us at '
        f"{sender.from_email}. {opt_out_instructions}"
    )


def state_for_county(county_fips: str) -> str:
    counties = load_counties()
    cfg = counties.get(county_fips)
    return cfg.state.upper() if cfg else ""


def check_eligibility(
    conn,
    *,
    email: str,
    county_fips: str,
    override_high_risk_state: bool = False,
) -> tuple[bool, str]:
    """Returns (eligible, reason); reason is empty when eligible."""
    email = (email or "").strip()
    if not email:
        return False, "no_email"
    if db.is_suppressed(conn, email):
        return False, "suppressed"
    state = state_for_county(county_fips)
    if state in high_risk_states() and not override_high_risk_state:
        return False, f"high_risk_state:{state}"
    return True, ""
