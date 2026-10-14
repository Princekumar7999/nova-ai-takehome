"""Class 5 Auditor: Cashback and Volume Rebate Auditor."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    ContractMeta,
    CreditNoteRecord,
    EvidenceQuote,
    InvoiceRecord,
    money_str,
    round_half_up,
)
from harness.resolver import resolve_fx


def get_quarter(date_str: str) -> str:
    month = int(date_str[5:7])
    year = date_str[:4]
    q = (month - 1) // 3 + 1
    return f"{year}-Q{q}"


def quarter_end_date(quarter_str: str) -> str:
    year, q = quarter_str.split("-Q")
    if q == "1":
        return f"{year}-03-31"
    elif q == "2":
        return f"{year}-06-30"
    elif q == "3":
        return f"{year}-09-30"
    else:
        return f"{year}-12-31"


def quarter_due_date(quarter_str: str) -> str:
    year, q = quarter_str.split("-Q")
    y = int(year)
    if q == "1":
        return f"{y}-04-30"
    elif q == "2":
        return f"{y}-07-30"
    elif q == "3":
        return f"{y}-10-30"
    else:
        return f"{y+1}-01-30"


def is_qualifying_line(charge_name: str, raw_line: str) -> bool:
    txt = f"{charge_name} {raw_line}".lower()
    # Exclusions
    excluded = ["duty", "customs duty", "disbursement", "sales tax", "tax", "gst", "vat", "late fee", "rebate", "credit"]
    for ex in excluded:
        if ex in txt:
            return False
    # Inclusions: base freight, fuel, security, accessorials
    return True


def audit_cashback(
    invoices: dict[str, InvoiceRecord],
    contracts: dict[str, ContractMeta],
    credit_notes: dict[str, CreditNoteRecord],
    as_of: str,
) -> list[dict]:
    """Audit for earned volume rebates and cashback credits."""
    portfolio_cashback = []

    for contract in contracts.values():
        if not contract.volume_rebate_tiers:
            continue

        # Collect all quarters that have closed on or before as_of
        # Look at invoices on this contract
        quarters_present = set()
        for inv in invoices.values():
            if inv.invoice_date <= as_of:
                if inv.contract_id == contract.contract_id or (contract.carrier_party and contract.carrier_party.lower() in inv.carrier_party.lower()):
                    q = get_quarter(inv.invoice_date)
                    q_end = quarter_end_date(q)
                    if q_end <= as_of:
                        quarters_present.add(q)

        for quarter in sorted(quarters_present):
            q_end = quarter_end_date(quarter)
            q_due = quarter_due_date(quarter)

            # 1. Calculate Qualifying Spend
            quarter_qs = Decimal("0.00")
            for inv in invoices.values():
                if inv.invoice_date <= as_of and get_quarter(inv.invoice_date) == quarter:
                    if inv.contract_id == contract.contract_id or (contract.carrier_party and contract.carrier_party.lower() in inv.carrier_party.lower()):
                        # Sum qualifying lines
                        inv_qs = Decimal("0.00")
                        for line in inv.lines:
                            if is_qualifying_line(line.charge_name, line.raw_line):
                                inv_qs += line.amount_billed

                        # FX conversion if INR
                        if inv.currency == "INR":
                            fx_rate, _ = resolve_fx(contract, inv.invoice_date)
                            inv_qs_usd = round_half_up(inv_qs / fx_rate, 2) if fx_rate else inv_qs
                        else:
                            inv_qs_usd = inv_qs

                        quarter_qs += inv_qs_usd

            # Subtract non-rebate credit notes dated in the quarter
            for cn in credit_notes.values():
                if cn.credit_date <= as_of and get_quarter(cn.credit_date) == quarter:
                    if cn.contract_id == contract.contract_id:
                        if cn.credit_type != "rebate":
                            quarter_qs -= cn.total_credited

            # 2. Check against volume rebate tiers
            tier_reached_rate = None
            for min_spend, pct in contract.volume_rebate_tiers:
                if quarter_qs >= min_spend:
                    tier_reached_rate = pct

            ev = []
            if contract.rebate_quote:
                ev.append(
                    EvidenceQuote(
                        doc=contract.source_doc,
                        page=4,
                        quote=contract.rebate_quote,
                    ).to_dict()
                )
            if contract.qs_quote:
                ev.append(
                    EvidenceQuote(
                        doc=contract.source_doc,
                        page=3,
                        quote=contract.qs_quote,
                    ).to_dict()
                )

            if tier_reached_rate is not None:
                earned_rebate = round_half_up(quarter_qs * (tier_reached_rate / Decimal("100")), 2)

                # Deduct existing rebate credit notes
                for cn in credit_notes.values():
                    if cn.credit_date <= as_of:
                        if cn.contract_id == contract.contract_id:
                            if cn.credit_type == "rebate" or cn.period == quarter or (cn.raw_quote and quarter in cn.raw_quote):
                                earned_rebate -= cn.total_credited

                if earned_rebate > Decimal("0.00"):
                    # Finding
                    action = "draft:claim_credit" if as_of >= q_due else "internal:track_credit"
                    portfolio_cashback.append(
                        {
                            "id": f"cashback:{contract.contract_id}/{quarter}/rebate",
                            "class": "cashback",
                            "variant": "CB-01",
                            "contract_id": contract.contract_id,
                            "period": quarter,
                            "credit_type": "rebate",
                            "verdict": "finding",
                            "amount_usd": money_str(earned_rebate),
                            "amount_range": None,
                            "deadline": None,
                            "action": action,
                            "confidence": 0.95,
                            "evidence": ev,
                            "missing_doc": None,
                            "supersedes": [],
                            "learned_at": None,
                        }
                    )
            else:
                # No tier reached (e.g. decoy)
                portfolio_cashback.append(
                    {
                        "id": f"cashback:{contract.contract_id}/{quarter}/rebate",
                        "class": "cashback",
                        "variant": "CB-02",
                        "contract_id": contract.contract_id,
                        "period": quarter,
                        "credit_type": "rebate",
                        "verdict": "no_finding",
                        "amount_usd": None,
                        "amount_range": None,
                        "deadline": None,
                        "action": "none",
                        "confidence": 0.90,
                        "evidence": ev,
                        "missing_doc": None,
                        "supersedes": [],
                        "learned_at": None,
                    }
                )

    return portfolio_cashback
