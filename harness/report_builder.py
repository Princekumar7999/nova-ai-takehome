"""Report builder: runs auditors, applies action policies, builds JSON report."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional

from harness.auditor.cashback import audit_cashback
from harness.auditor.duplicate import audit_duplicates
from harness.auditor.expiry import audit_expiry
from harness.auditor.late_fees import audit_late_fees
from harness.auditor.overcharge import audit_overcharge
from harness.auditor.waiver import audit_waivers
from harness.safety import apply_action_policies
from harness.storage import Storage


def build_report(
    storage: Storage,
    as_of: str,
    mode: str = "supervised",
) -> dict:
    """Generate the complete audit report for the given as_of date and mode."""
    contracts = storage.data["contracts"]
    amendments = storage.data["amendments"]
    invoices = storage.data["invoices"]
    credit_notes = storage.data["credit_notes"]
    tracking_events = storage.data["tracking_events"]
    ap_register = storage.data["ap_register"]
    waivers = storage.data["waivers"]
    rate_notices = storage.data["rate_notices"]
    due_extensions = storage.data["due_extensions"]
    feedback = storage.data["feedback"]

    # 1. Run all auditors
    findings: list[dict] = []

    # Class 1: Overcharge & Conflicts & Cannot_Determine
    c1_findings = audit_overcharge(
        invoices=invoices,
        contracts=contracts,
        amendments=amendments,
        rate_notices=rate_notices,
        tracking_events=tracking_events,
        feedback_facts=feedback,
        as_of=as_of,
        documents=storage.data.get("documents", {}),
    )
    findings.extend(c1_findings)

    # Class 2: Duplicates
    c2_findings = audit_duplicates(
        invoices=invoices,
        contracts=contracts,
        ap_register=ap_register,
        credit_notes=credit_notes,
        documents=storage.data.get("documents", {}),
        as_of=as_of,
    )
    findings.extend(c2_findings)

    # Class 3: Waivers
    c3_findings = audit_waivers(
        invoices=invoices,
        contracts=contracts,
        waivers=waivers,
        feedback_facts=feedback,
        as_of=as_of,
    )
    findings.extend(c3_findings)

    # Class 4: Late fees
    c4_findings, c4_risks = audit_late_fees(
        invoices=invoices,
        contracts=contracts,
        ap_register=ap_register,
        due_extensions=due_extensions,
        feedback_facts=feedback,
        as_of=as_of,
    )
    findings.extend(c4_findings)

    # Class 5: Cashback
    c5_cashback = audit_cashback(
        invoices=invoices,
        contracts=contracts,
        credit_notes=credit_notes,
        as_of=as_of,
    )

    # Class 6: Expiry
    c6_expiry = audit_expiry(
        contracts=contracts,
        as_of=as_of,
    )

    # De-duplicate findings by (invoice_id, class)
    seen_keys = set()
    deduped_findings = []
    for f in findings:
        key = (f.get("invoice_id"), f.get("class"))
        if key not in seen_keys:
            seen_keys.add(key)
            if "supersedes" not in f or f["supersedes"] is None:
                f["supersedes"] = []
            if "learned_at" not in f:
                f["learned_at"] = None
            deduped_findings.append(f)

    portfolio = {
        "cashback": c5_cashback,
        "expiry": c6_expiry,
        "late_fee_risk": c4_risks,
    }

    # Handle supersedes across prior reports
    prior_reports = storage.data.get("prior_reports", {})
    for f in deduped_findings:
        inv_id = f.get("invoice_id")
        f_class = f.get("class")
        for prior_as_of, prior_rep in sorted(prior_reports.items()):
            if prior_as_of < as_of:
                for pf in prior_rep.get("findings", []):
                    if pf.get("invoice_id") == inv_id and pf.get("class") == f_class:
                        if pf.get("verdict") != f.get("verdict") or pf.get("amount_usd") != f.get("amount_usd"):
                            if pf.get("id") and pf["id"] not in f["supersedes"]:
                                f["supersedes"].append(pf["id"])

    # 2. Apply action policies
    apply_action_policies(deduped_findings, portfolio, mode=mode)

    report = {
        "as_of": as_of,
        "mode": mode,
        "findings": deduped_findings,
        "portfolio": portfolio,
    }

    # Save to storage
    storage.data.setdefault("prior_reports", {})[as_of] = report
    storage.save()

    return report
