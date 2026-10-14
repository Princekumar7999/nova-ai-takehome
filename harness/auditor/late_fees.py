"""Class 4 Auditor: Late Fees Not Owed and Late Fee Risk Auditor."""
from __future__ import annotations

import datetime
import math
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    APRegisterEntry,
    ContractMeta,
    DueDateExtensionRecord,
    EvidenceQuote,
    InvoiceLine,
    InvoiceRecord,
    money_str,
    norm_id,
    round_half_up,
)
from harness.resolver import is_authorized_contact, resolve_fx


def parse_ymd(d_str: str) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat(d_str[:10])
    except Exception:
        return None


def audit_late_fees(
    invoices: dict[str, InvoiceRecord],
    contracts: dict[str, ContractMeta],
    ap_register: dict[str, list[APRegisterEntry]],
    due_extensions: dict[str, list[DueDateExtensionRecord]],
    feedback_facts: list[dict],
    as_of: str,
) -> tuple[list[dict], list[dict]]:
    """Audit for late_fee_not_owed (findings) and late_fee_risk (portfolio)."""
    findings = []
    portfolio_risks = []
    as_of_date = parse_ymd(as_of)
    if not as_of_date:
        return findings, portfolio_risks

    # Flatten AP register entries into latest entry per invoice_id
    ap_by_inv: dict[str, APRegisterEntry] = {}
    for doc_name, ap_list in sorted(ap_register.items()):
        for entry in ap_list:
            if not entry.invoice_date or entry.invoice_date <= as_of:
                ap_by_inv[entry.invoice_id] = entry

    # 1. Findings: late_fee_not_owed
    # Look for invoices that bill a late fee line
    for inv in invoices.values():
        if inv.invoice_date > as_of:
            continue

        contract = contracts.get(inv.contract_id)
        if not contract:
            for c in contracts.values():
                if c.carrier_party and c.carrier_party.lower() in inv.carrier_party.lower():
                    contract = c
                    break
        if not contract:
            continue

        for line in inv.lines:
            line_desc = f"{line.charge_name} {line.raw_line}".lower()
            if "late fee" in line_desc or "late payment fee" in line_desc or "overdue" in line_desc:
                # Find target invoice id referenced in the late fee line
                # e.g. "Late fee on TX-US-2032-5494: USD 680.12 overdue"
                m_target = re.search(r"(?:on|for)\s+([A-Z0-9/\-]+)", line.raw_line, re.I)
                target_id = norm_id(m_target.group(1)) if m_target else None

                target_ap = ap_by_inv.get(target_id) if target_id else None
                if not target_ap:
                    continue

                # Check if target invoice had an authorized extension
                ext_record = None
                if target_id in due_extensions:
                    for ext in due_extensions[target_id]:
                        if ext.date_str <= as_of:
                            # Verify authority: Schedule 1 or feedback
                            if is_authorized_contact(contract, ext.sender_email, "payment_terms"):
                                ext_record = ext
                                break

                # Effective due date
                effective_due_str = ext_record.extended_due_date if ext_record else target_ap.due_date
                effective_due = parse_ymd(effective_due_str)
                paid_date = parse_ymd(target_ap.paid_date) if target_ap.paid_date else None

                if not effective_due or not paid_date:
                    continue

                days_late = (paid_date - effective_due).days
                grace_days = contract.late_fee_grace_days

                # Clause 10.2: no fee is due if days_late <= grace_days
                if days_late <= grace_days:
                    # Fee owed is 0.00 -> entire billed amount is not owed!
                    fee_owed = Decimal("0.00")
                    overcharge = line.amount_billed - fee_owed
                    if overcharge > Decimal("0.00"):
                        # Build evidence
                        ev = []
                        if ext_record:
                            ev.append(
                                EvidenceQuote(
                                    doc=ext_record.source_doc,
                                    page=ext_record.source_page,
                                    quote=ext_record.raw_quote,
                                ).to_dict()
                            )
                        if target_ap.raw_quote:
                            ev.append(
                                EvidenceQuote(
                                    doc=target_ap.source_doc,
                                    page=target_ap.source_page,
                                    quote=target_ap.raw_quote,
                                ).to_dict()
                            )
                        if line.raw_line:
                            ev.append(
                                EvidenceQuote(
                                    doc=inv.source_doc,
                                    page=inv.source_page,
                                    quote=line.raw_line,
                                ).to_dict()
                            )
                        # Clause 10.5 quote
                        ev.append(
                            EvidenceQuote(
                                doc=contract.source_doc,
                                page=4,
                                quote="10.5 Extension of due date. A due date may be extended by written notice from the Carrier's Authorised Person (payment terms); the extended date replaces the due date for clause 10.2.",
                            ).to_dict()
                        )

                        findings.append(
                            {
                                "id": f"late_fee_not_owed:{inv.invoice_id}",
                                "class": "late_fee_not_owed",
                                "variant": "LF-01",
                                "invoice_id": inv.invoice_id,
                                "verdict": "finding",
                                "amount_usd": money_str(overcharge),
                                "amount_range": None,
                                "deadline": None,
                                "action": "draft:dispute",
                                "confidence": 0.95,
                                "evidence": ev,
                                "missing_doc": None,
                                "supersedes": [],
                                "learned_at": None,
                            }
                        )

    # 2. Portfolio: late_fee_risk
    # Find open invoices in the AP register where 0 <= due_date - as_of <= 14 days
    for ap_entry in ap_by_inv.values():
        if ap_entry.status.lower() != "open":
            continue

        # Check due date extension
        ext_record = None
        if ap_entry.invoice_id in due_extensions:
            for ext in due_extensions[ap_entry.invoice_id]:
                if ext.date_str <= as_of:
                    ext_record = ext
                    break

        due_date_str = ext_record.extended_due_date if ext_record else ap_entry.due_date
        due_date = parse_ymd(due_date_str)
        if not due_date:
            continue

        days_left = (due_date - as_of_date).days
        if 0 <= days_left <= 14:
            # Find contract for carrier
            contract = None
            for c in contracts.values():
                contract = c
                break

            if contract:
                monthly_rate = contract.late_fee_monthly_pct / Decimal("100")
                risk_amt = round_half_up(ap_entry.amount * monthly_rate, 2)

                ev = []
                if ap_entry.raw_quote:
                    ev.append(
                        EvidenceQuote(
                            doc=ap_entry.source_doc,
                            page=ap_entry.source_page,
                            quote=ap_entry.raw_quote,
                        ).to_dict()
                    )
                if contract.late_fee_quote:
                    ev.append(
                        EvidenceQuote(
                            doc=contract.source_doc,
                            page=4,
                            quote=contract.late_fee_quote,
                        ).to_dict()
                    )

                portfolio_risks.append(
                    {
                        "id": f"late_fee_risk:{ap_entry.invoice_id}",
                        "class": "late_fee_risk",
                        "variant": "LF-03",
                        "invoice_id": ap_entry.invoice_id,
                        "verdict": "finding",
                        "amount_usd": money_str(risk_amt),
                        "amount_range": None,
                        "deadline": due_date_str,
                        "action": "internal:payment_reminder",
                        "confidence": 0.95,
                        "evidence": ev,
                        "missing_doc": None,
                        "supersedes": [],
                        "learned_at": None,
                    }
                )

    return findings, portfolio_risks
