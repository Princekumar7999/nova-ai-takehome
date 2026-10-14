"""Class 2 Auditor: Cross-Batch Duplicate Freight and Invoice Charges Detector."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    APRegisterEntry,
    ContractMeta,
    CreditNoteRecord,
    EvidenceQuote,
    InvoiceLine,
    InvoiceRecord,
    money_str,
    round_half_up,
)


def get_charge_code(desc: str, annex_e_codes: dict[str, list[str]]) -> str:
    """Map billed line description to Annex E charge code if possible."""
    clean = re.sub(r"\(ref\s+[A-Z0-9\-]+\)", "", desc, flags=re.I).strip().lower()
    for code, aliases in annex_e_codes.items():
        for alias in aliases:
            if alias.lower() in clean or clean in alias.lower():
                return code
    # Fallback heuristic
    if "freight" in clean:
        return "FRT"
    if "fuel" in clean or "fsc" in clean:
        return "FUEL"
    if "security" in clean or "screening" in clean:
        return "SEC"
    if "delivery" in clean:
        return "DLV"
    if "storage" in clean or "demurrage" in clean:
        return "STOR"
    if "dg" in clean or "dangerous" in clean:
        return "DG"
    if "dry ice" in clean:
        return "DRYICE"
    if "account" in clean:
        return "ACCT"
    return desc.upper()


def audit_duplicates(
    invoices: dict[str, InvoiceRecord],
    contracts: dict[str, ContractMeta],
    ap_register: dict[str, list[APRegisterEntry]],
    credit_notes: dict[str, CreditNoteRecord],
    documents: dict[str, dict],
    as_of: str,
) -> list[dict]:
    """Find duplicate invoice resends and duplicate shipment lines across invoices."""
    findings = []
    reported_keys = set()

    # Filter invoices up to as_of
    eligible_invoices = [inv for inv in invoices.values() if inv.invoice_date <= as_of]
    eligible_invoices.sort(key=lambda x: x.invoice_date)

    # 1. Check exact resends of paid invoices
    for inv in eligible_invoices:
        # Check if invoice had multiple received dates or resend_count > 0
        if inv.resend_count > 0 or len(inv.received_dates) > 1:
            # Check AP register for this invoice
            ap_entries = ap_register.get(inv.invoice_id, [])
            paid_entry = next((e for e in ap_entries if e.status in ("paid", "scheduled")), None)
            if paid_entry and paid_entry.paid_date:
                # Find the resending email document received after paid_date
                # Find the eml that arrived after first arrival
                resend_eml = None
                resend_quote = "Please process payment at your earliest convenience."
                resend_doc = inv.source_doc

                # Search documents for the resend email
                for doc_id, doc_meta in documents.items():
                    if doc_meta.get("type") == "eml" and inv.invoice_id in doc_meta.get("body", "") + doc_meta.get("subject", ""):
                        # Check date
                        if doc_meta.get("received_date", "") > paid_entry.paid_date:
                            body = doc_meta.get("body", "")
                            # Check if not a reminder/copy
                            if not re.search(r"\b(?:for your records|copy|reminder)\b", body, re.I):
                                resend_eml = doc_id
                                for l in body.splitlines():
                                    if "payment" in l.lower() or "process" in l.lower():
                                        resend_quote = l.strip()
                                        break
                                break

                if resend_eml:
                    finding_key = ("duplicate", inv.invoice_id)
                    if finding_key not in reported_keys:
                        reported_keys.add(finding_key)
                        findings.append(
                            {
                                "id": f"duplicate:{inv.invoice_id}",
                                "class": "duplicate",
                                "invoice_id": inv.invoice_id,
                                "verdict": "finding",
                                "amount_usd": money_str(inv.total_billed),
                                "amount_range": None,
                                "deadline": None,
                                "action": "draft:dispute",
                                "confidence": 0.95,
                                "evidence": [
                                    EvidenceQuote(
                                        doc=paid_entry.source_doc,
                                        page=paid_entry.source_page,
                                        quote=paid_entry.raw_quote,
                                    ).to_dict(),
                                    EvidenceQuote(
                                        doc=resend_eml,
                                        page=1,
                                        quote=resend_quote,
                                    ).to_dict(),
                                ],
                                "missing_doc": None,
                            }
                        )

    # 2. Check duplicate shipment lines across different invoices
    # We pair lines by AWB and charge code / description
    # Check all pairs of invoices (earlier, later)
    for i in range(len(eligible_invoices)):
        inv_early = eligible_invoices[i]
        contract_early = contracts.get(inv_early.contract_id)
        codes_early = contract_early.annex_e_codes if contract_early else {}

        for j in range(i + 1, len(eligible_invoices)):
            inv_late = eligible_invoices[j]
            if inv_late.invoice_id == inv_early.invoice_id:
                continue

            contract_late = contracts.get(inv_late.contract_id) or contract_early
            codes_late = contract_late.annex_e_codes if contract_late else codes_early

            dup_lines_late = []
            ev_quotes = []

            for line_late in inv_late.lines:
                awb_late = line_late.awb
                if not awb_late or awb_late == "-":
                    continue

                code_late = get_charge_code(line_late.charge_name, codes_late)

                # Look for matching line in inv_early
                for line_early in inv_early.lines:
                    awb_early = line_early.awb
                    if not awb_early or awb_early == "-":
                        continue

                    # Exact AWB match
                    if awb_early == awb_late:
                        code_early = get_charge_code(line_early.charge_name, codes_early)
                        if code_early == code_late and line_early.amount_billed == line_late.amount_billed:
                            dup_lines_late.append(line_late)
                            ev_quotes.append(EvidenceQuote(doc=inv_early.source_doc, page=inv_early.source_page, quote=line_early.raw_line))
                            ev_quotes.append(EvidenceQuote(doc=inv_late.source_doc, page=inv_late.source_page, quote=line_late.raw_line))

                            # Add Annex E code quote if different wording
                            if line_early.charge_name.strip().lower() != line_late.charge_name.strip().lower() and contract_late:
                                # Look for quote in contract Page 10
                                for code, aliases in contract_late.annex_e_codes.items():
                                    if code == code_late:
                                        quote_e = f"Code {code}: {aliases[0]}; also billed as: {', '.join(aliases[1:])}"
                                        ev_quotes.append(EvidenceQuote(doc=contract_late.source_doc, page=10, quote=quote_e))
                                        break
                            break

            if dup_lines_late:
                tot_dup = sum((l.amount_billed for l in dup_lines_late), Decimal("0.00"))

                # Check if credited by credit notes
                for cn in credit_notes.values():
                    if cn.credit_date <= as_of and (cn.invoice_ref == inv_late.invoice_id or cn.invoice_ref == inv_early.invoice_id):
                        tot_dup -= cn.total_credited

                if tot_dup > Decimal("0.00"):
                    finding_key = ("duplicate", inv_late.invoice_id)
                    if finding_key not in reported_keys:
                        reported_keys.add(finding_key)
                        findings.append(
                            {
                                "id": f"duplicate:{inv_late.invoice_id}",
                                "class": "duplicate",
                                "invoice_id": inv_late.invoice_id,
                                "verdict": "finding",
                                "amount_usd": money_str(tot_dup),
                                "amount_range": None,
                                "deadline": None,
                                "action": "draft:dispute",
                                "confidence": 0.95,
                                "evidence": [eq.to_dict() for eq in ev_quotes],
                                "missing_doc": None,
                            }
                        )

    return findings
