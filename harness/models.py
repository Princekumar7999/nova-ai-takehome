"""Data models for freight invoice auditing, document extraction, and report items."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
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

    @classmethod
    def from_dict(cls, d: dict) -> EvidenceQuote:
        return cls(doc=str(d["doc"]), page=int(d["page"]), quote=str(d["quote"]))


@dataclass
class Schedule1Contact:
    name: str
    title: str
    party: str
    categories: list[str]
    email: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Schedule1Contact:
        return cls(
            name=str(d["name"]),
            title=str(d["title"]),
            party=str(d["party"]),
            categories=[str(c) for c in d.get("categories", [])],
            email=str(d["email"]).lower(),
        )


@dataclass
class RateBand:
    lane: str
    weight_band: str
    min_weight: float
    max_weight: float
    is_min_charge: bool
    rates_by_period: dict[int, Decimal]  # period 1 -> rate, period 2 -> rate...
    currency: str = "USD"
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""

    def to_dict(self) -> dict:
        return {
            "lane": self.lane,
            "weight_band": self.weight_band,
            "min_weight": self.min_weight,
            "max_weight": self.max_weight,
            "is_min_charge": self.is_min_charge,
            "rates_by_period": {str(k): str(v) for k, v in self.rates_by_period.items()},
            "currency": self.currency,
            "source_doc": self.source_doc,
            "source_page": self.source_page,
            "raw_quote": self.raw_quote,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RateBand:
        rates = {int(k): Decimal(str(v)) for k, v in d.get("rates_by_period", {}).items()}
        return cls(
            lane=str(d["lane"]),
            weight_band=str(d["weight_band"]),
            min_weight=float(d["min_weight"]),
            max_weight=float(d["max_weight"]),
            is_min_charge=bool(d["is_min_charge"]),
            rates_by_period=rates,
            currency=str(d.get("currency", "USD")),
            source_doc=str(d.get("source_doc", "")),
            source_page=int(d.get("source_page", 1)),
            raw_quote=str(d.get("raw_quote", "")),
        )


@dataclass
class AnnexCRow:
    month: str  # "YYYY-MM"
    fuel_pct: Decimal  # e.g. 0.2016 for 20.16%
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""

    def to_dict(self) -> dict:
        return {
            "month": self.month,
            "fuel_pct": str(self.fuel_pct),
            "source_doc": self.source_doc,
            "source_page": self.source_page,
            "raw_quote": self.raw_quote,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AnnexCRow:
        return cls(
            month=str(d["month"]),
            fuel_pct=Decimal(str(d["fuel_pct"])),
            source_doc=str(d.get("source_doc", "")),
            source_page=int(d.get("source_page", 1)),
            raw_quote=str(d.get("raw_quote", "")),
        )


@dataclass
class AnnexFRow:
    month: str  # "YYYY-MM"
    fx_rate: Decimal  # INR per 1 USD
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""

    def to_dict(self) -> dict:
        return {
            "month": self.month,
            "fx_rate": str(self.fx_rate),
            "source_doc": self.source_doc,
            "source_page": self.source_page,
            "raw_quote": self.raw_quote,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AnnexFRow:
        return cls(
            month=str(d["month"]),
            fx_rate=Decimal(str(d["fx_rate"])),
            source_doc=str(d.get("source_doc", "")),
            source_page=int(d.get("source_page", 1)),
            raw_quote=str(d.get("raw_quote", "")),
        )


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
    schedule1: dict[str, Schedule1Contact] = field(default_factory=dict)
    contacts_list: list[Schedule1Contact] = field(default_factory=list)
    annex_a_rates: list[RateBand] = field(default_factory=list)
    annex_c_fuel: dict[str, AnnexCRow] = field(default_factory=dict)
    annex_d_accessorials: dict[str, Decimal] = field(default_factory=dict)
    annex_e_codes: dict[str, list[str]] = field(default_factory=dict)
    annex_f_fx: dict[str, AnnexFRow] = field(default_factory=dict)
    period_2_start: str = ""

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "carrier_party": self.carrier_party,
            "shipper_party": self.shipper_party,
            "effective_date": self.effective_date,
            "initial_term_months": self.initial_term_months,
            "end_date": self.end_date,
            "auto_renews": self.auto_renews,
            "notice_days": self.notice_days,
            "non_renewal_deadline": self.non_renewal_deadline,
            "late_fee_monthly_pct": str(self.late_fee_monthly_pct),
            "late_fee_grace_days": self.late_fee_grace_days,
            "late_fee_compounds": self.late_fee_compounds,
            "volume_rebate_tiers": [[str(s), str(p)] for s, p in self.volume_rebate_tiers],
            "qualifying_spend_definition": self.qualifying_spend_definition,
            "source_doc": self.source_doc,
            "term_quote": self.term_quote,
            "late_fee_quote": self.late_fee_quote,
            "rebate_quote": self.rebate_quote,
            "qs_quote": self.qs_quote,
            "schedule1": {k: v.to_dict() for k, v in self.schedule1.items()},
            "contacts_list": [c.to_dict() for c in self.contacts_list],
            "annex_a_rates": [r.to_dict() for r in self.annex_a_rates],
            "annex_c_fuel": {k: v.to_dict() for k, v in self.annex_c_fuel.items()},
            "annex_d_accessorials": {k: str(v) for k, v in self.annex_d_accessorials.items()},
            "annex_e_codes": self.annex_e_codes,
            "annex_f_fx": {k: v.to_dict() for k, v in self.annex_f_fx.items()},
            "period_2_start": self.period_2_start,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ContractMeta:
        tiers = [(Decimal(str(s)), Decimal(str(p))) for s, p in d.get("volume_rebate_tiers", [])]
        s1 = {k: Schedule1Contact.from_dict(v) for k, v in d.get("schedule1", {}).items()}
        clist = [Schedule1Contact.from_dict(c) for c in d.get("contacts_list", [])]
        rates = [RateBand.from_dict(r) for r in d.get("annex_a_rates", [])]
        fuel = {k: AnnexCRow.from_dict(v) for k, v in d.get("annex_c_fuel", {}).items()}
        acc = {k: Decimal(str(v)) for k, v in d.get("annex_d_accessorials", {}).items()}
        fx = {k: AnnexFRow.from_dict(v) for k, v in d.get("annex_f_fx", {}).items()}

        return cls(
            contract_id=str(d["contract_id"]),
            carrier_party=str(d["carrier_party"]),
            shipper_party=str(d["shipper_party"]),
            effective_date=str(d["effective_date"]),
            initial_term_months=int(d["initial_term_months"]),
            end_date=str(d["end_date"]),
            auto_renews=bool(d["auto_renews"]),
            notice_days=int(d["notice_days"]),
            non_renewal_deadline=d.get("non_renewal_deadline"),
            late_fee_monthly_pct=Decimal(str(d["late_fee_monthly_pct"])),
            late_fee_grace_days=int(d["late_fee_grace_days"]),
            late_fee_compounds=bool(d["late_fee_compounds"]),
            volume_rebate_tiers=tiers,
            qualifying_spend_definition=str(d.get("qualifying_spend_definition", "base_freight")),
            source_doc=str(d["source_doc"]),
            term_quote=str(d.get("term_quote", "")),
            late_fee_quote=str(d.get("late_fee_quote", "")),
            rebate_quote=str(d.get("rebate_quote", "")),
            qs_quote=str(d.get("qs_quote", "")),
            schedule1=s1,
            contacts_list=clist,
            annex_a_rates=rates,
            annex_c_fuel=fuel,
            annex_d_accessorials=acc,
            annex_e_codes=d.get("annex_e_codes", {}),
            annex_f_fx=fx,
            period_2_start=str(d.get("period_2_start", "")),
        )


@dataclass
class AmendmentMeta:
    amendment_id: str
    contract_id: str
    effective_from: str
    replaced_rates: list[RateBand] = field(default_factory=list)
    unheld_annex_refs: list[dict] = field(default_factory=list)
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""

    def to_dict(self) -> dict:
        return {
            "amendment_id": self.amendment_id,
            "contract_id": self.contract_id,
            "effective_from": self.effective_from,
            "replaced_rates": [r.to_dict() for r in self.replaced_rates],
            "unheld_annex_refs": self.unheld_annex_refs,
            "source_doc": self.source_doc,
            "source_page": self.source_page,
            "raw_quote": self.raw_quote,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AmendmentMeta:
        rates = [RateBand.from_dict(r) for r in d.get("replaced_rates", [])]
        return cls(
            amendment_id=str(d["amendment_id"]),
            contract_id=str(d["contract_id"]),
            effective_from=str(d["effective_from"]),
            replaced_rates=rates,
            unheld_annex_refs=d.get("unheld_annex_refs", []),
            source_doc=str(d.get("source_doc", "")),
            source_page=int(d.get("source_page", 1)),
            raw_quote=str(d.get("raw_quote", "")),
        )


@dataclass
class InvoiceLine:
    line_no: int
    awb: str
    date_str: str
    charge_name: str
    lane: str
    chargeable_weight_kg: Optional[Decimal]
    rate_billed: Optional[Decimal]
    amount_billed: Decimal
    raw_line: str
    charge_code: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "line_no": self.line_no,
            "awb": self.awb,
            "date_str": self.date_str,
            "charge_name": self.charge_name,
            "lane": self.lane,
            "chargeable_weight_kg": str(self.chargeable_weight_kg) if self.chargeable_weight_kg is not None else None,
            "rate_billed": str(self.rate_billed) if self.rate_billed is not None else None,
            "amount_billed": str(self.amount_billed),
            "raw_line": self.raw_line,
            "charge_code": self.charge_code,
        }

    @classmethod
    def from_dict(cls, d: dict) -> InvoiceLine:
        return cls(
            line_no=int(d["line_no"]),
            awb=str(d["awb"]),
            date_str=str(d["date_str"]),
            charge_name=str(d["charge_name"]),
            lane=str(d.get("lane", "")),
            chargeable_weight_kg=Decimal(str(d["chargeable_weight_kg"])) if d.get("chargeable_weight_kg") is not None else None,
            rate_billed=Decimal(str(d["rate_billed"])) if d.get("rate_billed") is not None else None,
            amount_billed=Decimal(str(d["amount_billed"])),
            raw_line=str(d.get("raw_line", "")),
            charge_code=d.get("charge_code"),
        )


@dataclass
class InvoiceRecord:
    invoice_id: str
    contract_id: str
    customer_party: str
    carrier_party: str
    invoice_date: str
    due_date: Optional[str]
    currency: str
    total_billed: Decimal
    lines: list[InvoiceLine]
    source_doc: str
    source_page: int
    is_scan: bool = False
    raw_header_quote: str = ""
    resend_count: int = 0
    received_dates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "invoice_id": self.invoice_id,
            "contract_id": self.contract_id,
            "customer_party": self.customer_party,
            "carrier_party": self.carrier_party,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "currency": self.currency,
            "total_billed": str(self.total_billed),
            "lines": [l.to_dict() for l in self.lines],
            "source_doc": self.source_doc,
            "source_page": self.source_page,
            "is_scan": self.is_scan,
            "raw_header_quote": self.raw_header_quote,
            "resend_count": self.resend_count,
            "received_dates": self.received_dates,
        }

    @classmethod
    def from_dict(cls, d: dict) -> InvoiceRecord:
        lines = [InvoiceLine.from_dict(l) for l in d.get("lines", [])]
        return cls(
            invoice_id=str(d["invoice_id"]),
            contract_id=str(d.get("contract_id", "")),
            customer_party=str(d.get("customer_party", "")),
            carrier_party=str(d.get("carrier_party", "")),
            invoice_date=str(d["invoice_date"]),
            due_date=d.get("due_date"),
            currency=str(d.get("currency", "USD")),
            total_billed=Decimal(str(d["total_billed"])),
            lines=lines,
            source_doc=str(d.get("source_doc", "")),
            source_page=int(d.get("source_page", 1)),
            is_scan=bool(d.get("is_scan", False)),
            raw_header_quote=str(d.get("raw_header_quote", "")),
            resend_count=int(d.get("resend_count", 0)),
            received_dates=[str(r) for r in d.get("received_dates", [])],
        )


@dataclass
class CreditNoteRecord:
    credit_note_id: str
    invoice_ref: Optional[str]
    contract_id: Optional[str]
    credit_date: str
    currency: str
    total_credited: Decimal
    period: Optional[str] = None
    credit_type: Optional[str] = None
    source_doc: str = ""
    source_page: int = 1
    raw_quote: str = ""

    def to_dict(self) -> dict:
        return {
            "credit_note_id": self.credit_note_id,
            "invoice_ref": self.invoice_ref,
            "contract_id": self.contract_id,
            "credit_date": self.credit_date,
            "currency": self.currency,
            "total_credited": str(self.total_credited),
            "period": self.period,
            "credit_type": self.credit_type,
            "source_doc": self.source_doc,
            "source_page": self.source_page,
            "raw_quote": self.raw_quote,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CreditNoteRecord:
        return cls(
            credit_note_id=str(d["credit_note_id"]),
            invoice_ref=d.get("invoice_ref"),
            contract_id=d.get("contract_id"),
            credit_date=str(d["credit_date"]),
            currency=str(d.get("currency", "USD")),
            total_credited=Decimal(str(d["total_credited"])),
            period=d.get("period"),
            credit_type=d.get("credit_type"),
            source_doc=str(d.get("source_doc", "")),
            source_page=int(d.get("source_page", 1)),
            raw_quote=str(d.get("raw_quote", "")),
        )


@dataclass
class TrackingEvent:
    awb: str
    event: str
    event_date: str
    lane: Optional[str]
    source_doc: str
    source_page: int
    raw_quote: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> TrackingEvent:
        return cls(
            awb=str(d["awb"]),
            event=str(d["event"]),
            event_date=str(d["event_date"]),
            lane=d.get("lane"),
            source_doc=str(d["source_doc"]),
            source_page=int(d["source_page"]),
            raw_quote=str(d["raw_quote"]),
        )


@dataclass
class APRegisterEntry:
    invoice_id: str
    invoice_date: str
    due_date: str
    currency: str
    amount: Decimal
    status: str
    paid_date: Optional[str]
    source_doc: str
    source_page: int
    raw_quote: str

    def to_dict(self) -> dict:
        return {
            "invoice_id": self.invoice_id,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "currency": self.currency,
            "amount": str(self.amount),
            "status": self.status,
            "paid_date": self.paid_date,
            "source_doc": self.source_doc,
            "source_page": self.source_page,
            "raw_quote": self.raw_quote,
        }

    @classmethod
    def from_dict(cls, d: dict) -> APRegisterEntry:
        return cls(
            invoice_id=str(d["invoice_id"]),
            invoice_date=str(d["invoice_date"]),
            due_date=str(d["due_date"]),
            currency=str(d.get("currency", "USD")),
            amount=Decimal(str(d["amount"])),
            status=str(d["status"]).lower(),
            paid_date=d.get("paid_date"),
            source_doc=str(d["source_doc"]),
            source_page=int(d["source_page"]),
            raw_quote=str(d["raw_quote"]),
        )


@dataclass
class WaiverRecord:
    sender_name: str
    sender_email: str
    date_str: str
    charge_name: str
    awb: Optional[str]
    month: Optional[str]
    condition: Optional[str]
    source_doc: str
    source_page: int
    raw_quote: str
    is_quoted_in_forward: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> WaiverRecord:
        return cls(
            sender_name=str(d["sender_name"]),
            sender_email=str(d["sender_email"]).lower(),
            date_str=str(d["date_str"]),
            charge_name=str(d["charge_name"]),
            awb=d.get("awb"),
            month=d.get("month"),
            condition=d.get("condition"),
            source_doc=str(d["source_doc"]),
            source_page=int(d["source_page"]),
            raw_quote=str(d["raw_quote"]),
            is_quoted_in_forward=bool(d.get("is_quoted_in_forward", False)),
        )


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

    def to_dict(self) -> dict:
        return {
            "sender_name": self.sender_name,
            "sender_email": self.sender_email,
            "date_str": self.date_str,
            "contract_id": self.contract_id,
            "lane": self.lane,
            "rates": [r.to_dict() for r in self.rates],
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "source_doc": self.source_doc,
            "source_page": self.source_page,
            "raw_quote": self.raw_quote,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RateNoticeRecord:
        rates = [RateBand.from_dict(r) for r in d.get("rates", [])]
        return cls(
            sender_name=str(d["sender_name"]),
            sender_email=str(d["sender_email"]).lower(),
            date_str=str(d["date_str"]),
            contract_id=d.get("contract_id"),
            lane=str(d["lane"]),
            rates=rates,
            effective_from=d.get("effective_from"),
            effective_to=d.get("effective_to"),
            source_doc=str(d["source_doc"]),
            source_page=int(d["source_page"]),
            raw_quote=str(d["raw_quote"]),
        )


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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> DueDateExtensionRecord:
        return cls(
            sender_name=str(d["sender_name"]),
            sender_email=str(d["sender_email"]).lower(),
            date_str=str(d["date_str"]),
            invoice_id=str(d["invoice_id"]),
            extended_due_date=str(d["extended_due_date"]),
            source_doc=str(d["source_doc"]),
            source_page=int(d["source_page"]),
            raw_quote=str(d["raw_quote"]),
        )
