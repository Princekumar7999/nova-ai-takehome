"""Mode safety and unattended-action policy engine for supervised and autonomous actions."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional


def apply_action_policies(
    findings: list[dict],
    portfolio: dict[str, list[dict]],
    mode: str = "supervised",
) -> None:
    """Apply supervised and autonomous action safety rules and unattended-action test."""
    # 1. Calculate outbound amounts per invoice and per cashback key
    invoice_outbound_totals: dict[str, Decimal] = {}
    for f in findings:
        if f.get("verdict") == "finding":
            inv_id = f.get("invoice_id")
            if inv_id and f.get("amount_usd"):
                amt = Decimal(str(f["amount_usd"]))
                invoice_outbound_totals[inv_id] = invoice_outbound_totals.get(inv_id, Decimal("0.00")) + amt

    cashback_outbound_totals: dict[str, Decimal] = {}
    for cb in portfolio.get("cashback", []):
        if cb.get("verdict") == "finding":
            cb_id = cb.get("id") or cb.get("contract_id", "")
            if cb.get("amount_usd"):
                amt = Decimal(str(cb["amount_usd"]))
                cashback_outbound_totals[cb_id] = cashback_outbound_totals.get(cb_id, Decimal("0.00")) + amt

    # 2. Check for open conflicts
    conflicted_lanes = set()
    for f in findings:
        if f.get("verdict") == "conflict":
            # Extract lane if present
            for ev in f.get("evidence", []):
                quote = ev.get("quote", "")
                import re
                m = re.search(r"([A-Z0-9]+-[A-Z0-9]+)", quote)
                if m:
                    conflicted_lanes.add(m.group(1))

    # Helper function to process each item
    def process_item(item: dict, is_cashback: bool = False) -> None:
        verdict = item.get("verdict")
        action = item.get("action", "none")

        if verdict == "no_finding":
            item["action"] = "none"
            return

        if verdict == "conflict":
            item["action"] = "escalate"
            return

        # Internal actions stay internal
        if action in ("internal:alert", "internal:payment_reminder", "internal:track_credit"):
            return

        # Outbound actions: dispute, claim_credit, request_document
        base_remedy = action.split(":")[-1]
        if base_remedy not in ("dispute", "claim_credit", "request_document"):
            return

        # Check $5,000 threshold
        if is_cashback:
            cb_id = item.get("id") or item.get("contract_id", "")
            total_amt = cashback_outbound_totals.get(cb_id, Decimal(str(item.get("amount_usd") or "0.00")))
        else:
            inv_id = item.get("invoice_id", "")
            total_amt = invoice_outbound_totals.get(inv_id, Decimal(str(item.get("amount_usd") or "0.00")))

        over_threshold = total_amt >= Decimal("5000.00")

        # Unattended-action checks
        passes_unattended = True

        # Check 1: Under threshold
        if over_threshold:
            passes_unattended = False

        # Check 2: No conflict on lane
        for ev in item.get("evidence", []):
            import re
            m = re.search(r"([A-Z0-9]+-[A-Z0-9]+)", ev.get("quote", ""))
            if m and m.group(1) in conflicted_lanes:
                passes_unattended = False
                break

        # Check 3: Can be undone
        if base_remedy not in ("dispute", "claim_credit", "request_document"):
            passes_unattended = False

        # Apply mode
        if mode == "supervised":
            if over_threshold:
                item["action"] = "human_review"
            else:
                item["action"] = f"draft:{base_remedy}"
        elif mode == "autonomous":
            if passes_unattended:
                item["action"] = f"execute:{base_remedy}"
            else:
                item["action"] = "human_review"

    # Process all findings
    for f in findings:
        process_item(f, is_cashback=False)

    # Process portfolio cashback
    for cb in portfolio.get("cashback", []):
        process_item(cb, is_cashback=True)
