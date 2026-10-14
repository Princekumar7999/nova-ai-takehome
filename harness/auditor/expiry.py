"""Class 6 Auditor: Contract Expiration Auditor."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from harness.models import ContractMeta, EvidenceQuote


def parse_ymd(d_str: str) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat(d_str[:10])
    except Exception:
        return None


def audit_expiry(
    contracts: dict[str, ContractMeta],
    as_of: str,
) -> list[dict]:
    """Audit for contracts nearing expiry or non-renewal notice deadlines (within 60 days)."""
    portfolio_expiry = []
    as_of_d = parse_ymd(as_of)
    if not as_of_d:
        return portfolio_expiry

    for contract in sorted(contracts.values(), key=lambda c: c.contract_id):
        deadline_str = (
            contract.non_renewal_deadline
            if (contract.auto_renews and contract.non_renewal_deadline)
            else contract.end_date
        )
        deadline_d = parse_ymd(deadline_str)
        if not deadline_d:
            continue

        days_left = (deadline_d - as_of_d).days
        if 0 <= days_left <= 60:
            portfolio_expiry.append(
                {
                    "id": f"expiry:{contract.contract_id}",
                    "class": "expiry",
                    "variant": "EX-01",
                    "contract_id": contract.contract_id,
                    "verdict": "finding",
                    "amount_usd": None,
                    "amount_range": None,
                    "deadline": deadline_str,
                    "action": "internal:alert",
                    "confidence": 0.95,
                    "evidence": [
                        EvidenceQuote(
                            doc=contract.source_doc,
                            page=3,
                            quote=contract.term_quote,
                        ).to_dict()
                    ],
                    "missing_doc": None,
                    "supersedes": [],
                    "learned_at": None,
                }
            )

    return portfolio_expiry
