"""Resolvers for pickup dates, contractual rates, fuel surcharges, FX, and authority precedence."""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    AmendmentMeta,
    ContractMeta,
    EvidenceQuote,
    InvoiceLine,
    InvoiceRecord,
    RateBand,
    RateNoticeRecord,
    TrackingEvent,
    round_half_up,
)


def resolve_pickup_date(
    awb: str,
    invoice_line: Optional[InvoiceLine],
    invoice_date: str,
    tracking_events: dict[str, list[TrackingEvent]],
) -> tuple[str, list[EvidenceQuote], str]:
    """Resolve pickup date per glossary hierarchy:
    1. tracking export PICKED_UP
    2. carrier picked up email
    3. AWB date
    4. ship date printed on invoice
    Returns (pickup_date, evidence_list, source_type).
    """
    quotes = []

    # 1 & 2: Tracking events for this AWB
    events = tracking_events.get(awb, [])
    for ev in events:
        if ev.event == "PICKED_UP":
            quotes.append(EvidenceQuote(doc=ev.source_doc, page=ev.source_page, quote=ev.raw_quote))
            src_type = "tracking" if "tracking" in ev.source_doc else "email_picked_up"
            return ev.event_date, quotes, src_type

    # 3 & 4: Invoice line date
    if invoice_line and invoice_line.date_str:
        return invoice_line.date_str, [], "invoice_line"

    return invoice_date, [], "invoice_date"


def resolve_fx(contract: ContractMeta, invoice_date: str) -> tuple[Optional[Decimal], Optional[EvidenceQuote]]:
    """Return Annex F exchange rate for the calendar month of invoice_date.
    Returns (fx_rate, evidence_quote) or (None, None).
    """
    mth = invoice_date[:7]  # YYYY-MM
    row = contract.annex_f_fx.get(mth)
    if row:
        eq = EvidenceQuote(doc=row.source_doc, page=row.source_page, quote=row.raw_quote)
        return row.fx_rate, eq
    return None, None


def is_authorized_contact(contract: ContractMeta, email: str, category: str) -> bool:
    """Check if sender email is listed on Schedule 1 for the category."""
    c = contract.schedule1.get(email.lower())
    if c and category.lower() in c.categories:
        return True
    return False


def get_schedule1_holder(contract: ContractMeta, category: str) -> dict:
    """Get the Schedule 1 holder for a missing document category."""
    c = contract.schedule1.get(category.lower())
    if not c:
        # Fallback to Key Account Manager / notices
        c = contract.schedule1.get("notices")
    if c:
        return {"party": c.party, "person": c.name, "role": c.title}
    # Default carrier holder
    return {"party": contract.carrier_party or "Carrier", "role": "Key Account Manager"}
