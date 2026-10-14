"""Data models for freight invoice auditing, document extraction, and report items."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple


def round_half_up(val: Decimal | float | str, places: int = 2) -> Decimal:
    """Standard half-up rounding to specified decimal places."""
    d = Decimal(str(val))
    target = Decimal("10") ** -places
    return d.quantize(target, rounding=ROUND_HALF_UP)


def money_str(val: Decimal | float | str | None) -> str | None:
    if val is None:
        return None
    return f"{round_half_up(val, 2):.2f}"


def norm_id(s: str | None) -> str:
    """Normalize invoice or identifier: uppercase, without spaces, '#' or ':'."""
    return re.sub(r"[\s#:]", "", str(s or "")).upper()


def norm_whitespace(s: str | None) -> str:
    return " ".join(str(s or "").split())


@dataclass
class EvidenceQuote:
    doc: str
    page: int
    quote: str

    def to_dict(self) -> dict:
        return {"doc": self.doc, "page": self.page, "quote": self.quote}


@dataclass
class Schedule1Contact:
    name: str
    title: str
    party: str
    categories: list[str]
    email: str


@dataclass
class RateBand:
    lane: str
    weight_band: str  # e.g. "0-44.5 kg", "45-99.5 kg", "100-299.5 kg", "300+ kg", "minimum charge"
    min_weight: float
    max_weight: float
    is_min_charge: bool
    rates_by_period: dict[int, Decimal]  # period 1 -> rate, period 2 -> rate...
    currency: str = "USD"
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""


@dataclass
class AnnexCRow:
    month: str  # "YYYY-MM"
    fuel_pct: Decimal  # e.g. 0.2016 for 20.16%
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""


@dataclass
class AnnexFRow:
    month: str  # "YYYY-MM"
    fx_rate: Decimal  # INR per 1 USD
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""


@dataclass
class ContractMeta:
    contract_id: str
    carrier_party: str
    shipper_party: str
    effective_date: str  # YYYY-MM-DD
    initial_term_months: int
    end_date: str  # YYYY-MM-DD
    auto_renews: bool
    notice_days: int
    non_renewal_deadline: Optional[str]  # YYYY-MM-DD
    late_fee_monthly_pct: Decimal
    late_fee_grace_days: int
    late_fee_compounds: bool
    volume_rebate_tiers: list[tuple[Decimal, Decimal]]  # [(min_spend, rebate_pct), ...] sorted asc
    qualifying_spend_definition: str
    source_doc: str
    term_quote: str
    late_fee_quote: str
    rebate_quote: str
    qs_quote: str
    schedule1: dict[str, Schedule1Contact] = field(default_factory=dict)  # category -> contact or email -> contact
    contacts_list: list[Schedule1Contact] = field(default_factory=list)
    annex_a_rates: list[RateBand] = field(default_factory=list)
    annex_c_fuel: dict[str, AnnexCRow] = field(default_factory=dict)  # "YYYY-MM" -> row
    annex_d_accessorials: dict[str, Decimal] = field(default_factory=dict)
    annex_e_codes: dict[str, list[str]] = field(default_factory=dict)  # code -> list of labels
    annex_f_fx: dict[str, AnnexFRow] = field(default_factory=dict)  # "YYYY-MM" -> row


@dataclass
class AmendmentMeta:
    amendment_id: str
    contract_id: str
    effective_from: str  # YYYY-MM-DD
    replaced_rates: list[RateBand] = field(default_factory=list)
    unheld_annex_refs: list[dict] = field(default_factory=list)  # [{"lane": lane, "name": "Annex A rev 2", "applies_from": "YYYY-MM-DD"}]
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""


@dataclass
class InvoiceLine:
    line_no: int
    awb: str
    date_str: str  # YYYY-MM-DD
    charge_name: str
    lane: str
    chargeable_weight_kg: Optional[Decimal]
    rate_billed: Optional[Decimal]
    amount_billed: Decimal
    raw_line: str
    charge_code: Optional[str] = None  # Annex E charge code if mapped


@dataclass
class InvoiceRecord:
    invoice_id: str
    contract_id: str
    customer_party: str
    carrier_party: str
    invoice_date: str  # YYYY-MM-DD
    due_date: Optional[str]  # YYYY-MM-DD
    currency: str  # USD or INR
    total_billed: Decimal
    lines: list[InvoiceLine]
    source_doc: str
    source_page: int
    is_scan: bool = False
    raw_header_quote: str = ""
    resend_count: int = 0
    received_dates: list[str] = field(default_factory=list)


@dataclass
class CreditNoteRecord:
    credit_note_id: str
    invoice_ref: Optional[str]
    contract_id: Optional[str]
    credit_date: str  # YYYY-MM-DD
    currency: str
    total_credited: Decimal
    period: Optional[str] = None
    credit_type: Optional[str] = None
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""


@dataclass
class TrackingEvent:
    awb: str
    event: str  # PICKED_UP, DELIVERED, etc.
    event_date: str  # YYYY-MM-DD
    lane: Optional[str]
    source_doc: str
    source_page: int
    raw_quote: str


@dataclass
class APRegisterEntry:
    invoice_id: str
    invoice_date: str
    due_date: str
    currency: str
    amount: Decimal
    status: str  # "paid", "scheduled", "open", etc.
    paid_date: Optional[str]
    source_doc: str
    source_page: int
    raw_quote: str


@dataclass
class WaiverRecord:
    sender_name: str
    sender_email: str
    date_str: str
    charge_name: str
    awb: Optional[str]
    month: Optional[str]  # "YYYY-MM"
    condition: Optional[str]
    source_doc: str
    source_page: int
    raw_quote: str
    is_quoted_in_forward: bool = False


@dataclass
class RateNoticeRecord:
    sender_name: str
    sender_email: str
    date_str: str
    contract_id: Optional[str]
    lane: str
    rates: list[RateBand]
    effective_from: Optional[str]
    effective_to: Optional[str]
    source_doc: str
    source_page: int
    raw_quote: str


@dataclass
class DueDateExtensionRecord:
    sender_name: str
    sender_email: str
    date_str: str
    invoice_id: str
    extended_due_date: str
    source_doc: str
    source_page: int
    raw_quote: str


@dataclass
class FeedbackItem:
    item_id: str
    finding_key: dict  # {"invoice_id": ..., "class": ...} or cashback/expiry
    solicited: bool
    decision: str  # confirm, close, reject, override_amount, resolve_conflict, one_off_approval
    fact: dict  # type, subject, value, evidence
    scope: dict  # kind (standing/one_off), applies_to, valid_from, valid_to
    note: str
    after_batch: str
    decided_at: str
