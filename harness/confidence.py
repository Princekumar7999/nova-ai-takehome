"""Confidence estimation engine for freight leakage audit findings."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def compute_finding_confidence(
    finding_class: str,
    verdict: str,
    evidence_count: int,
    has_clause_quote: bool = True,
    is_scan: bool = False,
    has_missing_doc: bool = False,
) -> float:
    """Calculate confidence bounded in [0.0, 1.0] for audit findings."""
    if verdict == "no_finding":
        return 0.90

    if verdict == "cannot_determine":
        return 0.90

    if verdict == "conflict":
        return 0.90

    # Base confidence for positive findings
    score = 0.95

    # Small deduction if scanned image
    if is_scan:
        score = 0.92

    # Deduction if evidence quotes are sparse
    if evidence_count < 2:
        score = min(score, 0.85)

    return round(score, 2)
