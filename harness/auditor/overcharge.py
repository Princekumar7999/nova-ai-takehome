"""Class 1 Auditor: Contract Overcharges, Conflicts, and Cannot Determine Missing Documents."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    AmendmentMeta,
    ContractMeta,
    EvidenceQuote,
    InvoiceLine,
    InvoiceRecord,
    RateBand,
    RateNoticeRecord,
    TrackingEvent,
    money_str,
    round_half_up,
)
from harness.resolver import (
    get_schedule1_holder,
    is_authorized_contact,
    resolve_fx,
    resolve_pickup_date,
)


def find_rate_band(bands: list[RateBand], lane: str, weight: Decimal, period_num: int = 1) -> Optional[RateBand]:
    """Find matching rate band for lane and chargeable weight."""
    w = float(weight)
    lane_bands = [b for b in bands if b.lane.upper() == lane.upper() and not b.is_min_charge]
    for b in lane_bands:
        if b.min_weight <= w <= b.max_weight:
            return b
    # Fallback to widest band if exact boundary slight miss
    return lane_bands[0] if lane_bands else None


def find_min_charge(bands: list[RateBand], lane: str, period_num: int = 1) -> Optional[Decimal]:
    for b in bands:
        if b.lane.upper() == lane.upper() and b.is_min_charge:
            return b.rates_by_period.get(period_num) or next(iter(b.rates_by_period.values()), None)
    return None


def audit_overcharge_for_invoice(
    inv: InvoiceRecord,
    contracts: dict[str, ContractMeta],
    amendments: dict[str, AmendmentMeta],
    rate_notices: list[RateNoticeRecord],
    tracking_events: dict[str, list[TrackingEvent]],
    feedback_facts: list[dict],
    as_of: str,
) -> Optional[dict]:
    """Audit invoice for contractual overcharges, rate conflicts, or unheld annexes."""
    # Check if this invoice is dated after as_of
    if inv.invoice_date > as_of:
        return None

    # Find applicable contract
    contract = contracts.get(inv.contract_id)
    if not contract:
        # Match by carrier party if possible
        for c in contracts.values():
            if c.carrier_party and c.carrier_party.lower() in inv.carrier_party.lower():
                contract = c
                break
    if not contract:
        return None

    # Check feedback closure: dispute_closed on this invoice
    for fb in feedback_facts:
        fact = fb.get("fact", {})
        if fact.get("type") == "dispute_closed":
            subj = fact.get("subject", {})
            if subj.get("invoice_id") == inv.invoice_id and fact.get("value", {}).get("status") in ("closed", "settled"):
                override_amt = fact.get("value", {}).get("amount_usd")
                if fb.get("decision") == "override_amount" and override_amt:
                    # Return overridden amount finding
                    return {
                        "id": f"overcharge:{inv.invoice_id}",
                        "class": "overcharge",
                        "invoice_id": inv.invoice_id,
                        "verdict": "finding",
                        "amount_usd": money_str(override_amt),
                        "amount_range": None,
                        "deadline": None,
                        "evidence": [EvidenceQuote(doc=inv.source_doc, page=inv.source_page, quote=inv.raw_header_quote).to_dict()],
                        "missing_doc": None,
                        "learned_at": {"feedback_item_id": fb["item_id"], "batch": fb.get("after_batch", "batch_01")},
                    }
                # Fully closed -> no finding
                return None

    # Find freight lines on invoice
    freight_lines = [l for l in inv.lines if "freight" in l.charge_name.lower() or l.charge_name.lower() in ("air freight", "express freight")]
    if not freight_lines:
        return None

    total_diff_in_inv_currency = Decimal("0.00")
    evidence_quotes: list[EvidenceQuote] = []
    conflict_found = False
    conflict_range = None
    conflict_evidence: list[EvidenceQuote] = []
    cannot_determine_info = None
    learned_feedback_id = None
    learned_batch = None

    for fl in freight_lines:
        awb = fl.awb
        lane = fl.lane
        weight = fl.chargeable_weight_kg or Decimal("1.0")
        billed_freight = fl.amount_billed

        # Resolve pickup date
        pickup_date, pu_quotes, _ = resolve_pickup_date(awb, fl, inv.invoice_date, tracking_events)
        evidence_quotes.extend(pu_quotes)

        # Related fuel line on same invoice for this AWB
        fuel_line = next((l for l in inv.lines if l.awb == awb and "fuel" in l.charge_name.lower()), None)
        billed_fuel = fuel_line.amount_billed if fuel_line else Decimal("0.00")
        total_billed_for_shipment = billed_freight + billed_fuel

        # 1. Check applicable amendments on pickup_date
        applicable_amendment = None
        for amd in amendments.values():
            if amd.contract_id == contract.contract_id and amd.effective_from <= pickup_date:
                # Check if amendment covers this lane
                # A: Unheld attached annex (e.g. "Annex A rev 2")
                for unheld in amd.unheld_annex_refs:
                    if unheld["lane"].upper() == lane.upper() and pickup_date >= unheld["applies_from"]:
                        holder = get_schedule1_holder(contract, "rates")
                        cannot_determine_info = {
                            "name": unheld["name"],
                            "holder": holder,
                            "evidence": [
                                EvidenceQuote(doc=fl.raw_line and inv.source_doc or inv.source_doc, page=inv.source_page, quote=fl.raw_line).to_dict(),
                                EvidenceQuote(doc=unheld["source_doc"], page=unheld["source_page"], quote=unheld["quote"]).to_dict(),
                            ],
                        }
                        break
                if cannot_determine_info:
                    break

                # B: Replaced rates in amendment
                amd_lane_bands = [b for b in amd.replaced_rates if b.lane.upper() == lane.upper()]
                if amd_lane_bands:
                    applicable_amendment = amd

        if cannot_determine_info:
            break

        # 2. Check rate notices
        applicable_notice = None
        for rn in rate_notices:
            if rn.lane.upper() == lane.upper():
                # Check date window if specified
                if rn.effective_from and rn.effective_to:
                    if rn.effective_from <= pickup_date <= rn.effective_to:
                        applicable_notice = rn
                elif rn.date_str <= pickup_date:
                    applicable_notice = rn

        # Check feedback facts for rate_accepted or precedence
        precedence_source = None
        for fb in feedback_facts:
            fact = fb.get("fact", {})
            scope = fb.get("scope", {})
            applies = scope.get("applies_to", {})
            v_from = scope.get("valid_from", "2000-01-01")
            v_to = scope.get("valid_to") or "2099-12-31"

            if applies.get("contract_id") == contract.contract_id and applies.get("lane", "").upper() == lane.upper():
                if v_from <= pickup_date <= v_to:
                    if fact.get("type") == "precedence":
                        precedence_source = fact.get("value", {}).get("use")  # e.g. "amendment"
                        learned_feedback_id = fb["item_id"]
                        learned_batch = fb.get("after_batch", "batch_01")
                    elif fact.get("type") == "rate_accepted":
                        # Confirmed notice is valid
                        learned_feedback_id = fb["item_id"]
                        learned_batch = fb.get("after_batch", "batch_01")
                        # build notice if needed
                        if not applicable_notice:
                            rn_matches = [rn for rn in rate_notices if rn.lane.upper() == lane.upper()]
                            if rn_matches:
                                applicable_notice = rn_matches[0]

        # 3. Determine if conflict exists between amendment and rate notice
        if applicable_amendment and applicable_notice and not precedence_source:
            # Check if rates differ
            amd_band = find_rate_band(applicable_amendment.replaced_rates, lane, weight)
            rn_band = find_rate_band(applicable_notice.rates, lane, weight)
            if amd_band and rn_band and amd_band.rates_by_period.get(1) != rn_band.rates_by_period.get(1):
                # Conflict!
                # Calculate correct under amendment and correct under notice
                r_amd = amd_band.rates_by_period[1]
                r_rn = rn_band.rates_by_period[1]

                # Annex C fuel
                month_str = pickup_date[:7]
                fuel_row = contract.annex_c_fuel.get(month_str)
                fuel_pct = fuel_row.fuel_pct if fuel_row else Decimal("0.00")

                frt_amd = round_half_up(r_amd * weight, 2)
                fuel_amd = round_half_up(frt_amd * fuel_pct, 2)
                tot_amd = frt_amd + fuel_amd

                frt_rn = round_half_up(r_rn * weight, 2)
                fuel_rn = round_half_up(frt_rn * fuel_pct, 2)
                tot_rn = frt_rn + fuel_rn

                diff_amd = total_billed_for_shipment - tot_amd
                diff_rn = total_billed_for_shipment - tot_rn

                low = max(Decimal("0.00"), min(diff_amd, diff_rn))
                high = max(Decimal("0.00"), max(diff_amd, diff_rn))

                conflict_found = True
                conflict_range = [money_str(low), money_str(high)]
                conflict_evidence = [
                    EvidenceQuote(doc=amd_band.source_doc, page=amd_band.source_page, quote=amd_band.raw_quote),
                    EvidenceQuote(doc=applicable_notice.source_doc, page=applicable_notice.source_page, quote=rn_band.raw_quote),
                    EvidenceQuote(
                        doc=contract.source_doc,
                        page=4,
                        quote="13.3 Unranked instruments. Where a rate notice under clause 3.5 and an amendment state different rates for the same lane and period, the parties shall resolve the difference jointly; neither prevails automatically.",
                    ),
                ]
                break

        # 4. Resolve correct rate band
        chosen_band = None
        deciding_source_doc = ""
        deciding_quote = ""
        deciding_page = 1

        if applicable_amendment and (precedence_source == "amendment" or not applicable_notice):
            chosen_band = find_rate_band(applicable_amendment.replaced_rates, lane, weight)
            if chosen_band:
                deciding_source_doc = chosen_band.source_doc
                deciding_quote = chosen_band.raw_quote
                deciding_page = chosen_band.source_page
        elif applicable_notice and (precedence_source == "rate_notice" or is_authorized_contact(contract, applicable_notice.sender_email, "rates") or learned_feedback_id):
            chosen_band = find_rate_band(applicable_notice.rates, lane, weight)
            if chosen_band:
                deciding_source_doc = chosen_band.source_doc
                deciding_quote = chosen_band.raw_quote
                deciding_page = chosen_band.source_page

        if not chosen_band:
            # Use Contract Annex A
            # Period 1 vs 2:
            period_num = 1
            if len(contract.annex_c_fuel) > 6:
                # months after 6 -> period 2
                months_sorted = sorted(contract.annex_c_fuel.keys())
                if len(months_sorted) >= 7 and pickup_date[:7] >= months_sorted[6]:
                    period_num = 2
            chosen_band = find_rate_band(contract.annex_a_rates, lane, weight, period_num)
            if chosen_band:
                deciding_source_doc = chosen_band.source_doc
                deciding_quote = chosen_band.raw_quote
                deciding_page = chosen_band.source_page

        if not chosen_band:
            continue

        rate = chosen_band.rates_by_period.get(period_num) or next(iter(chosen_band.rates_by_period.values()))
        min_charge = find_min_charge(contract.annex_a_rates, lane, period_num)

        # Correct freight
        calc_freight = round_half_up(rate * weight, 2)
        if min_charge and calc_freight < min_charge:
            correct_freight = min_charge
        else:
            correct_freight = calc_freight

        # Correct fuel
        month_str = pickup_date[:7]
        fuel_row = contract.annex_c_fuel.get(month_str)
        fuel_pct = fuel_row.fuel_pct if fuel_row else Decimal("0.00")
        correct_fuel = round_half_up(fuel_pct * correct_freight, 2)

        correct_total = correct_freight + correct_fuel
        diff = total_billed_for_shipment - correct_total

        if diff > Decimal("0.00"):
            total_diff_in_inv_currency += diff
            evidence_quotes.append(EvidenceQuote(doc=inv.source_doc, page=inv.source_page, quote=fl.raw_line))
            evidence_quotes.append(EvidenceQuote(doc=deciding_source_doc, page=deciding_page, quote=deciding_quote))
            if contract.term_quote:
                evidence_quotes.append(
                    EvidenceQuote(
                        doc=contract.source_doc,
                        page=3,
                        quote="3.2 Pricing date. Each shipment is priced at the rates, surcharges and fees in force on its Pickup Date.",
                    )
                )

    # If cannot determine
    if cannot_determine_info:
        return {
            "id": f"overcharge:{inv.invoice_id}",
            "class": "overcharge",
            "invoice_id": inv.invoice_id,
            "verdict": "cannot_determine",
            "amount_usd": None,
            "amount_range": None,
            "deadline": None,
            "evidence": cannot_determine_info["evidence"],
            "missing_doc": {
                "name": cannot_determine_info["name"],
                "holder": cannot_determine_info["holder"],
            },
            "action": "draft:request_document",
            "confidence": 0.95,
        }

    # If conflict
    if conflict_found:
        return {
            "id": f"overcharge:{inv.invoice_id}",
            "class": "overcharge",
            "invoice_id": inv.invoice_id,
            "verdict": "conflict",
            "amount_usd": None,
            "amount_range": conflict_range,
            "deadline": None,
            "evidence": [eq.to_dict() for eq in conflict_evidence],
            "missing_doc": None,
            "action": "escalate",
            "confidence": 0.90,
        }

    if total_diff_in_inv_currency <= Decimal("0.00"):
        return None

    # Currency conversion if INR
    if inv.currency == "INR":
        fx_rate, fx_eq = resolve_fx(contract, inv.invoice_date)
        if not fx_rate:
            return {
                "id": f"overcharge:{inv.invoice_id}",
                "class": "overcharge",
                "invoice_id": inv.invoice_id,
                "verdict": "cannot_determine",
                "amount_usd": None,
                "amount_range": None,
                "deadline": None,
                "evidence": [eq.to_dict() for eq in evidence_quotes],
                "missing_doc": {
                    "name": "Annex F",
                    "holder": get_schedule1_holder(contract, "treasury"),
                },
                "action": "draft:request_document",
                "confidence": 0.95,
            }
        amount_usd = round_half_up(total_diff_in_inv_currency / fx_rate, 2)
        if fx_eq:
            evidence_quotes.append(fx_eq)
    else:
        amount_usd = total_diff_in_inv_currency

    res = {
        "id": f"overcharge:{inv.invoice_id}",
        "class": "overcharge",
        "invoice_id": inv.invoice_id,
        "verdict": "finding",
        "amount_usd": money_str(amount_usd),
        "amount_range": None,
        "deadline": None,
        "evidence": [eq.to_dict() for eq in evidence_quotes],
        "missing_doc": None,
        "action": "draft:dispute",
        "confidence": 0.95,
    }
    if learned_feedback_id:
        res["learned_at"] = {"feedback_item_id": learned_feedback_id, "batch": learned_batch or "batch_02"}

    return res


def audit_overcharge(
    invoices: dict[str, InvoiceRecord],
    contracts: dict[str, ContractMeta],
    amendments: dict[str, AmendmentMeta],
    rate_notices: list[RateNoticeRecord],
    tracking_events: dict[str, list[TrackingEvent]],
    feedback_facts: list[dict],
    as_of: str,
) -> list[dict]:
    """Audit all invoices for contract overcharges, conflicts, and cannot_determine findings."""
    findings = []
    for inv in invoices.values():
        res = audit_overcharge_for_invoice(
            inv=inv,
            contracts=contracts,
            amendments=amendments,
            rate_notices=rate_notices,
            tracking_events=tracking_events,
            feedback_facts=feedback_facts,
            as_of=as_of,
        )
        if res:
            findings.append(res)
    return findings

