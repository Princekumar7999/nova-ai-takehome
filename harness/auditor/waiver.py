"""Class 3 Auditor: Authorized Waivers vs Billed Charges Auditor."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    ContractMeta,
    EvidenceQuote,
    InvoiceLine,
    InvoiceRecord,
    WaiverRecord,
    money_str,
    round_half_up,
)
from harness.resolver import get_schedule1_holder, is_authorized_contact, resolve_fx


def audit_waivers(
    invoices: dict[str, InvoiceRecord],
    contracts: dict[str, ContractMeta],
    waivers: list[WaiverRecord],
    feedback_facts: list[dict],
    as_of: str,
) -> list[dict]:
    """Audit for waived but billed charges."""
    findings = []
    reported_keys = set()

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

        # Check feedback decisions for this invoice
        # e.g. one_off_approval on waiver_scope or reject
        feedback_approved = False
        feedback_rejected = False
        learned_feedback_id = None
        learned_batch = None

        for fb in feedback_facts:
            fkey = fb.get("finding_key", {})
            if fkey.get("invoice_id") == inv.invoice_id and fkey.get("class") == "waived_but_billed":
                decision = fb.get("decision")
                if decision in ("reject", "close"):
                    feedback_rejected = True
                    break
                if decision in ("confirm", "one_off_approval"):
                    feedback_approved = True
                    learned_feedback_id = fb.get("item_id")
                    learned_batch = fb.get("after_batch", "batch_01")

        if feedback_rejected:
            continue

        for w in waivers:
            if w.date_str > as_of:
                continue

            # Check authority: Schedule 1 or feedback sender_authority
            has_authority = is_authorized_contact(contract, w.sender_email, "waivers")
            if not has_authority:
                # Check feedback
                for fb in feedback_facts:
                    fact = fb.get("fact", {})
                    if fact.get("type") == "sender_authority":
                        subj = fact.get("subject", {})
                        if subj.get("person", "").lower() in w.sender_name.lower():
                            if "waivers" in fact.get("value", {}).get("categories", []):
                                has_authority = True
                                learned_feedback_id = fb.get("item_id")
                                learned_batch = fb.get("after_batch", "batch_01")
                                break

            # If quoted in forward with no original: cannot_determine
            if w.is_quoted_in_forward:
                finding_key = ("waived_but_billed", inv.invoice_id)
                if finding_key not in reported_keys:
                    reported_keys.add(finding_key)
                    holder = get_schedule1_holder(contract, "notices")
                    findings.append(
                        {
                            "id": f"waived_but_billed:{inv.invoice_id}",
                            "class": "waived_but_billed",
                            "invoice_id": inv.invoice_id,
                            "verdict": "cannot_determine",
                            "amount_usd": None,
                            "amount_range": None,
                            "deadline": None,
                            "action": "draft:request_document",
                            "confidence": 0.90,
                            "evidence": [EvidenceQuote(doc=w.source_doc, page=w.source_page, quote=w.raw_quote).to_dict()],
                            "missing_doc": {
                                "name": "original waiver email",
                                "holder": holder,
                            },
                        }
                    )
                continue

            if not has_authority and not feedback_approved:
                # Unauthorized sender -> no finding
                continue

            # Check scope: does waiver name AWB on invoice or month?
            # Find lines on invoice matching the waived charge
            waived_lines: list[InvoiceLine] = []
            for line in inv.lines:
                # Check AWB match
                if w.awb and line.awb.upper() == w.awb.upper():
                    # Check charge name match (e.g. "delivery fee" in line.charge_name)
                    if w.charge_name.lower() in line.charge_name.lower() or line.charge_name.lower() in w.charge_name.lower():
                        waived_lines.append(line)
                elif w.month and (inv.invoice_date.startswith(w.month) or line.date_str.startswith(w.month)):
                    if w.charge_name.lower() in line.charge_name.lower() or line.charge_name.lower() in w.charge_name.lower():
                        waived_lines.append(line)

            if waived_lines:
                tot_waived = sum((l.amount_billed for l in waived_lines), Decimal("0.00"))
                # Currency conversion if INR
                if inv.currency == "INR":
                    fx_rate, fx_eq = resolve_fx(contract, inv.invoice_date)
                    amount_usd = round_half_up(tot_waived / fx_rate, 2) if fx_rate else tot_waived
                else:
                    amount_usd = tot_waived

                # Evidence quotes
                ev_quotes = [
                    EvidenceQuote(doc=w.source_doc, page=w.source_page, quote=w.raw_quote),
                ]
                # Schedule 1 quote
                contact = contract.schedule1.get(w.sender_email)
                if contact:
                    contact_quote = f"{contact.name}, {contact.title}, {contact.party}: {', '.join(contact.categories)} <{contact.email}>"
                    ev_quotes.append(EvidenceQuote(doc=contract.source_doc, page=11, quote=contact_quote))

                # Invoice line quote
                for wl in waived_lines:
                    ev_quotes.append(EvidenceQuote(doc=inv.source_doc, page=inv.source_page, quote=wl.raw_line))

                # Waiver clause quote
                ev_quotes.append(
                    EvidenceQuote(
                        doc=contract.source_doc,
                        page=4,
                        quote="11.1 Waiver authority. A charge may be waived only in writing by a person listed in Schedule 1 for waivers. Schedule 1 is exhaustive.",
                    )
                )

                finding_key = ("waived_but_billed", inv.invoice_id)
                if finding_key not in reported_keys:
                    reported_keys.add(finding_key)
                    item = {
                        "id": f"waived_but_billed:{inv.invoice_id}",
                        "class": "waived_but_billed",
                        "invoice_id": inv.invoice_id,
                        "verdict": "finding",
                        "amount_usd": money_str(amount_usd),
                        "amount_range": None,
                        "deadline": None,
                        "action": "draft:dispute",
                        "confidence": 0.95,
                        "evidence": [eq.to_dict() for eq in ev_quotes],
                        "missing_doc": None,
                    }
                    if learned_feedback_id:
                        item["learned_at"] = {"feedback_item_id": learned_feedback_id, "batch": learned_batch or "batch_02"}
                    findings.append(item)

    return findings
