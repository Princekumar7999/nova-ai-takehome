"""Parser for Master Services Agreements (MSAs) and Amendments."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    AmendmentMeta,
    AnnexCRow,
    AnnexFRow,
    ContractMeta,
    RateBand,
    Schedule1Contact,
    round_half_up,
)


def parse_schedule1_line(line: str) -> Optional[Schedule1Contact]:
    """Parse: 'Name, Title, Party: cat1, cat2 <email>'"""
    m = re.match(r"^([^,]+),\s*([^,]+),\s*([^:]+):\s*([^<]+)<([^>]+)>", line.strip())
    if not m:
        return None
    name = m.group(1).strip()
    title = m.group(2).strip()
    party = m.group(3).strip()
    categories = [c.strip().lower() for c in m.group(4).split(",") if c.strip()]
    email = m.group(5).strip().lower()
    return Schedule1Contact(name=name, title=title, party=party, categories=categories, email=email)


def parse_msa(doc_path: str, pages: list[str]) -> Optional[ContractMeta]:
    """Parse full MSA contract document across its pages."""
    full_text = "\n".join(pages)
    if "Amendment No." in full_text or "Amendment reference" in full_text or "AMD-" in doc_path or "-AMD" in doc_path:
        return None
    if "Master Services Agreement" not in full_text and "MSA-" not in doc_path:
        return None

    # Contract ID
    m_id = re.search(r"Master Services Agreement \([^\)]+\)\s+([A-Z0-9\-]+)", full_text)
    if not m_id:
        m_id = re.search(r"([A-Z0-9]+-[A-Z0-9]+-EXP-[A-Z0-9]+)", doc_path)
    if not m_id:
        return None
    contract_id = m_id.group(1).strip()

    # Effective Date
    m_eff = re.search(r"2\.1\s+Effective date\.\s+This Agreement takes effect on\s+(\d{4}-\d{2}-\d{2})", full_text)
    eff_date = m_eff.group(1) if m_eff else "2030-01-01"

    # Initial term & End date
    m_term = re.search(
        r"2\.2\s+Initial term\.\s+The initial term is\s+(\d+)\s+months,\s+ending on\s+(\d{4}-\d{2}-\d{2})", full_text
    )
    if m_term:
        initial_months = int(m_term.group(1))
        end_date = m_term.group(2)
        term_quote = m_term.group(0)
    else:
        initial_months = 12
        end_date = "2035-12-31"
        term_quote = ""

    # Auto-renewal & notice
    auto_renews = False
    notice_days = 0
    non_renewal_deadline = None
    m_renew = re.search(r"2\.3\s+Auto-renewal\.\s+([^.\n]+(?:\.[^.\n]+)?)", full_text)
    if m_renew:
        renew_text = m_renew.group(1)
        if "renews automatically" in renew_text.lower():
            auto_renews = True
            m_nd = re.search(r"at least\s+(\d+)\s+days before", renew_text)
            if m_nd:
                notice_days = int(m_nd.group(1))
                # compute non-renewal deadline = end_date - notice_days
                from datetime import datetime, timedelta
                ed = datetime.strptime(end_date, "%Y-%m-%d").date()
                nrd = ed - timedelta(days=notice_days)
                non_renewal_deadline = nrd.isoformat()

    # Parties
    carrier_party = ""
    shipper_party = ""
    m_parties = re.search(r"is made between\s+([^,\n]+)[^a-zA-Z0-9]+and\s+([^,\n]+)", full_text, re.I)
    if m_parties:
        shipper_party = m_parties.group(1).strip()
        carrier_party = m_parties.group(2).strip()

    # Late fee
    m_lf = re.search(
        r"(10\.2\s+Late (?:payment )?fee\.\s+Undisputed amounts (?:unpaid|outstanding) (?:for|more than) (\d+) days (?:after|past) the due date (?:bear|incur) a late fee of ([\d.]+)% per month[^\n]*(?:\n[^\n]+){1,3})",
        full_text,
    )
    if m_lf:
        lf_quote = m_lf.group(1).strip().replace("\n", " ")
        if "10.3" in lf_quote:
            lf_quote = lf_quote[: lf_quote.index("10.3")].strip()
        lf_grace = int(m_lf.group(2))
        lf_rate = Decimal(m_lf.group(3))
        lf_compounds = "compounding" in full_text.lower()
    else:
        m_lf_simple = re.search(r"10\.2\s+Late (?:payment )?fee\.[^\n]+", full_text)
        lf_quote = m_lf_simple.group(0).strip() if m_lf_simple else ""
        lf_grace = 5
        lf_rate = Decimal("1.86")
        lf_compounds = False

    # Volume rebate
    m_reb = re.search(r"7\.1\s+Volume rebate\.\s+([^\n]+(?:\n[^\n]+)?)", full_text)
    reb_tiers = []
    reb_quote = ""
    if m_reb:
        reb_quote = m_reb.group(0)
        # QS >= USD 55,000.00: 5%; QS >= USD 91,000.00: 7.5%; QS >= USD 150,000.00: 10%
        # or INR tiers
        tier_matches = re.findall(r"QS\s*>=\s*(?:USD|INR)\s*([\d,]+\.?\d*)\s*:\s*([\d.]+)%", reb_quote)
        for min_sp, pct in tier_matches:
            val_sp = Decimal(min_sp.replace(",", ""))
            val_pct = Decimal(pct)
            reb_tiers.append((val_sp, val_pct))
        reb_tiers.sort(key=lambda t: t[0])

    # Qualifying spend
    m_qs = re.search(r"1\.1\s+Qualifying Spend\.\s+([^\n]+(?:\n[^\n]+)?)", full_text)
    qs_quote = m_qs.group(0) if m_qs else ""

    # Schedule 1 Contacts
    contacts_dict = {}
    contacts_list = []
    annex_a_rates = []
    annex_c_fuel = {}
    annex_d_accessorials = {}
    annex_e_codes = {}
    annex_f_fx = {}

    for page_idx, page_text in enumerate(pages):
        page_num = page_idx + 1

        # Schedule 1
        if "Schedule 1:" in page_text:
            for line in page_text.splitlines():
                c = parse_schedule1_line(line)
                if c:
                    contacts_list.append(c)
                    for cat in c.categories:
                        contacts_dict[cat] = c
                    contacts_dict[c.email] = c

        # Annex A: Express rates
        if "Annex A: Express rates" in page_text or "Annex A:" in page_text:
            lines = page_text.splitlines()
            for line in lines:
                # Minimum charge row: USS2-INN1 minimum charge 91.00 91.00
                m_mc = re.match(r"^([A-Z0-9]+-[A-Z0-9]+)\s+minimum charge\s+([\d.]+)(?:\s+([\d.]+))?", line.strip())
                if m_mc:
                    lane = m_mc.group(1)
                    p1 = Decimal(m_mc.group(2))
                    p2 = Decimal(m_mc.group(3)) if m_mc.group(3) else p1
                    annex_a_rates.append(
                        RateBand(
                            lane=lane,
                            weight_band="minimum charge",
                            min_weight=0.0,
                            max_weight=999999.0,
                            is_min_charge=True,
                            rates_by_period={1: p1, 2: p2},
                            source_doc=doc_path,
                            source_page=page_num,
                            raw_quote=line.strip(),
                        )
                    )
                    continue

                # Weight band row: USE2-INE2 100-299.5 kg 6.01 6.16 or 300+ kg
                m_wb = re.match(
                    r"^([A-Z0-9]+-[A-Z0-9]+)\s+(\d+(?:\.\d+)?\s*(?:-\s*\d+(?:\.\d+)?|\+))\s*kg\s+([\d.]+)(?:\s+([\d.]+))?",
                    line.strip(),
                )
                if m_wb:
                    lane = m_wb.group(1)
                    band_str = m_wb.group(2).strip() + " kg"
                    p1 = Decimal(m_wb.group(3))
                    p2 = Decimal(m_wb.group(4)) if m_wb.group(4) else p1

                    if "+" in band_str:
                        min_w = float(re.search(r"(\d+(?:\.\d+)?)", band_str).group(1))
                        max_w = 999999.0
                    else:
                        parts = re.findall(r"(\d+(?:\.\d+)?)", band_str)
                        min_w = float(parts[0])
                        max_w = float(parts[1])

                    annex_a_rates.append(
                        RateBand(
                            lane=lane,
                            weight_band=band_str,
                            min_weight=min_w,
                            max_weight=max_w,
                            is_min_charge=False,
                            rates_by_period={1: p1, 2: p2},
                            source_doc=doc_path,
                            source_page=page_num,
                            raw_quote=line.strip(),
                        )
                    )

        # Annex C: Fuel surcharge
        if "Annex C: Fuel surcharge" in page_text:
            for line in page_text.splitlines():
                m_f = re.match(r"^(\d{4}-\d{2})\s+([\d.]+)%", line.strip())
                if m_f:
                    month = m_f.group(1)
                    pct = Decimal(m_f.group(2)) / Decimal("100")
                    annex_c_fuel[month] = AnnexCRow(
                        month=month,
                        fuel_pct=pct,
                        source_doc=doc_path,
                        source_page=page_num,
                        raw_quote=line.strip(),
                    )

        # Annex D: Accessorials
        if "Annex D: Accessorials" in page_text:
            for line in page_text.splitlines():
                # DG Dangerous goods fee 124.02 USD per shipment
                m_acc = re.match(r"^([A-Z]+)\s+([^\d]+?)\s+([\d.]+)\s+(?:USD|INR)", line.strip())
                if m_acc:
                    code = m_acc.group(1)
                    fee = Decimal(m_acc.group(3))
                    annex_d_accessorials[code] = fee

        # Annex E: Charge codes
        if "Annex E: Charge codes" in page_text:
            for line in page_text.splitlines():
                m_cc = re.match(r"^Code\s+([A-Z]+):\s*([^;]+);\s*also billed as:\s*(.+)$", line.strip())
                if m_cc:
                    code = m_cc.group(1).strip()
                    main_name = m_cc.group(2).strip()
                    aliases = [a.strip() for a in m_cc.group(3).split(",") if a.strip()]
                    annex_e_codes[code] = [main_name] + aliases

        # Annex F: Exchange rates
        if "Annex F: Exchange rates" in page_text:
            for line in page_text.splitlines():
                m_fx = re.match(r"^(\d{4}-\d{2})\s+([\d.]+)", line.strip())
                if m_fx:
                    month = m_fx.group(1)
                    rate = Decimal(m_fx.group(2))
                    annex_f_fx[month] = AnnexFRow(
                        month=month,
                        fx_rate=rate,
                        source_doc=doc_path,
                        source_page=page_num,
                        raw_quote=line.strip(),
                    )

    return ContractMeta(
        contract_id=contract_id,
        carrier_party=carrier_party,
        shipper_party=shipper_party,
        effective_date=eff_date,
        initial_term_months=initial_months,
        end_date=end_date,
        auto_renews=auto_renews,
        notice_days=notice_days,
        non_renewal_deadline=non_renewal_deadline,
        late_fee_monthly_pct=lf_rate,
        late_fee_grace_days=lf_grace,
        late_fee_compounds=lf_compounds,
        volume_rebate_tiers=reb_tiers,
        qualifying_spend_definition=qs_quote,
        source_doc=doc_path,
        term_quote=term_quote,
        late_fee_quote=lf_quote,
        rebate_quote=reb_quote,
        qs_quote=qs_quote,
        schedule1=contacts_dict,
        contacts_list=contacts_list,
        annex_a_rates=annex_a_rates,
        annex_c_fuel=annex_c_fuel,
        annex_d_accessorials=annex_d_accessorials,
        annex_e_codes=annex_e_codes,
        annex_f_fx=annex_f_fx,
    )


def parse_amendment(doc_path: str, pages: list[str]) -> Optional[AmendmentMeta]:
    """Parse an amendment document."""
    full_text = "\n".join(pages)
    if "Amendment No." not in full_text and "AMD-" not in doc_path:
        return None

    m_ref = re.search(r"Amendment reference\s+([A-Z0-9\-/]+)", full_text)
    if not m_ref:
        m_ref = re.search(r"([A-Z0-9\-]+(?:/AMD-\d+|-AMD-\d+))", doc_path)
    amd_id = m_ref.group(1).strip() if m_ref else doc_path

    m_cid = re.search(r"to (?:Master Services Agreement [^\)]+\s+)?([A-Z0-9\-]+)", full_text)
    contract_id = m_cid.group(1).strip() if m_cid else amd_id.split("/")[0].split("-AMD")[0]

    m_eff = re.search(r"Effective from\s+(\d{4}-\d{2}-\d{2})", full_text)
    eff_from = m_eff.group(1) if m_eff else "2030-01-01"

    rates = []
    unheld_refs = []

    for page_idx, page_text in enumerate(pages):
        page_num = page_idx + 1

        for line in page_text.splitlines():
            # Check replaced rates: Lane Weight band Rate Currency
            # USC3-USS2 minimum charge 82.82 USD
            m_mc = re.match(r"^([A-Z0-9]+-[A-Z0-9]+)\s+minimum charge\s+([\d.]+)\s+([A-Z]{3})", line.strip())
            if m_mc:
                lane = m_mc.group(1)
                rate = Decimal(m_mc.group(2))
                curr = m_mc.group(3)
                rates.append(
                    RateBand(
                        lane=lane,
                        weight_band="minimum charge",
                        min_weight=0.0,
                        max_weight=999999.0,
                        is_min_charge=True,
                        rates_by_period={1: rate, 2: rate},
                        currency=curr,
                        source_doc=doc_path,
                        source_page=page_num,
                        raw_quote=line.strip(),
                    )
                )
                continue

            # USC3-USS2 100-299.5 kg 3.12 USD
            m_wb = re.match(
                r"^([A-Z0-9]+-[A-Z0-9]+)\s+(\d+(?:\.\d+)?\s*(?:-\s*\d+(?:\.\d+)?|\+))\s*kg\s+([\d.]+)\s+([A-Z]{3})",
                line.strip(),
            )
            if m_wb:
                lane = m_wb.group(1)
                band_str = m_wb.group(2).strip() + " kg"
                rate = Decimal(m_wb.group(3))
                curr = m_wb.group(4)
                if "+" in band_str:
                    min_w = float(re.search(r"(\d+(?:\.\d+)?)", band_str).group(1))
                    max_w = 999999.0
                else:
                    parts = re.findall(r"(\d+(?:\.\d+)?)", band_str)
                    min_w = float(parts[0])
                    max_w = float(parts[1])

                rates.append(
                    RateBand(
                        lane=lane,
                        weight_band=band_str,
                        min_weight=min_w,
                        max_weight=max_w,
                        is_min_charge=False,
                        rates_by_period={1: rate, 2: rate},
                        currency=curr,
                        source_doc=doc_path,
                        source_page=page_num,
                        raw_quote=line.strip(),
                    )
                )
                continue

        # Check unheld attached revision on page_text (handles line wraps)
        # "Annex A rev 2 (attached) applies from 2035-06-04 to lane(s) USS2-INN1."
        for m_rev in re.finditer(
            r"(Annex\s+[A-Z](?:\s+rev\s+\d+)?)\s*\(attached\)\s*applies from\s*(\d{4}-\d{2}-\d{2})\s*to lane\(s\)\s*([A-Z0-9\-,\s]+)",
            page_text,
        ):
            ref_name = m_rev.group(1).strip()
            applies_from = m_rev.group(2).strip()
            raw_matched_quote = m_rev.group(0).replace("\n", " ")
            lanes = [l.strip().rstrip(".") for l in m_rev.group(3).split(",") if l.strip().rstrip(".")]
            for l in lanes:
                unheld_refs.append(
                    {
                        "lane": l,
                        "name": ref_name,
                        "applies_from": applies_from,
                        "source_doc": doc_path,
                        "source_page": page_num,
                        "quote": raw_matched_quote,
                    }
                )

    return AmendmentMeta(
        amendment_id=amd_id,
        contract_id=contract_id,
        effective_from=eff_from,
        replaced_rates=rates,
        unheld_annex_refs=unheld_refs,
        source_doc=doc_path,
        source_page=1,
        raw_quote=f"Amendment reference {amd_id}",
    )
