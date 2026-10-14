"""Parser for business events delivered via email: Waivers, Rate Notices, and Due Date Extensions."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

from harness.invoice_parser import parse_date_str, parse_money
from harness.models import (
    DueDateExtensionRecord,
    RateBand,
    RateNoticeRecord,
    WaiverRecord,
    norm_id,
)


def parse_email_events(
    doc_path: str,
    from_name: str,
    from_email: str,
    subject: str,
    body: str,
    date_day: str,
) -> tuple[list[WaiverRecord], list[RateNoticeRecord], list[DueDateExtensionRecord]]:
    """Inspect email body and subject for waivers, rate notices, and due extensions."""
    waivers = []
    rate_notices = []
    extensions = []

    full_text = f"Subject: {subject}\n\n{body}"

    # 1. Waivers
    # "we waive the Delivery fee on AWB TE97049901"
    # "We waive the Delivery fee on AWB TE33628218 in full."
    # "We waive storage charges for the month of 2040-10"
    m_w = re.search(r"(?:we\s+waive|waived)\s+(?:the\s+)?([A-Za-z\s]+?)\s+on\s+AWB\s+([A-Z0-9\-]+)", full_text, re.I)
    if m_w:
        charge = m_w.group(1).strip()
        awb = m_w.group(2).strip()
        quote = m_w.group(0)
        is_quoted = bool(re.search(r"-----Original Message-----|wrote:|Forwarded message", body, re.I))

        # Find the line containing the waiver for exact citation quote
        matched_line = ""
        for line in full_text.splitlines():
            if quote in line or (charge in line and awb in line):
                matched_line = line.strip()
                break

        waivers.append(
            WaiverRecord(
                sender_name=from_name,
                sender_email=from_email.lower(),
                date_str=date_day,
                charge_name=charge,
                awb=awb,
                month=None,
                condition=None,
                source_doc=doc_path,
                source_page=1,
                raw_quote=matched_line or quote,
                is_quoted_in_forward=is_quoted,
            )
        )
    else:
        # Waiver by month / condition
        m_wm = re.search(
            r"(?:we\s+waive|waived)\s+(?:the\s+)?([A-Za-z\s]+?)\s+(?:for\s+the\s+month\s+of\s+|for\s+)([A-Z0-9\-]+)",
            full_text,
            re.I,
        )
        if m_wm and "contract" not in m_wm.group(1).lower():
            charge = m_wm.group(1).strip()
            period_str = m_wm.group(2).strip()
            matched_line = ""
            for line in full_text.splitlines():
                if "waive" in line.lower() and charge.lower() in line.lower():
                    matched_line = line.strip()
                    break
            waivers.append(
                WaiverRecord(
                    sender_name=from_name,
                    sender_email=from_email.lower(),
                    date_str=date_day,
                    charge_name=charge,
                    awb=None,
                    month=period_str,
                    condition=None,
                    source_doc=doc_path,
                    source_page=1,
                    raw_quote=matched_line or m_wm.group(0),
                    is_quoted_in_forward=False,
                )
            )

    # 2. Rate Notices
    # Subject: Rate notice USW1-USC3
    # Body: USW1-USC3 100-299.5 kg: 3.03 USD
    if "rate notice" in subject.lower() or "rates" in subject.lower():
        notice_rates = []
        for line in full_text.splitlines():
            # USW1-USC3 100-299.5 kg: 3.03 USD
            m_r = re.match(
                r"^([A-Z0-9]+-[A-Z0-9]+)\s+(\d+(?:\.\d+)?\s*(?:-\s*\d+(?:\.\d+)?|\+))\s*kg:?\s+([\d.]+)\s*([A-Z]{3})?",
                line.strip(),
            )
            if m_r:
                lane = m_r.group(1).strip()
                band_str = m_r.group(2).strip() + " kg"
                rate = Decimal(m_r.group(3))
                curr = m_r.group(4) or "USD"
                if "+" in band_str:
                    min_w = float(re.search(r"(\d+(?:\.\d+)?)", band_str).group(1))
                    max_w = 999999.0
                else:
                    parts = re.findall(r"(\d+(?:\.\d+)?)", band_str)
                    min_w = float(parts[0])
                    max_w = float(parts[1])

                notice_rates.append(
                    RateBand(
                        lane=lane,
                        weight_band=band_str,
                        min_weight=min_w,
                        max_weight=max_w,
                        is_min_charge=False,
                        rates_by_period={1: rate, 2: rate},
                        currency=curr,
                        source_doc=doc_path,
                        source_page=1,
                        raw_quote=line.strip(),
                    )
                )

        if notice_rates:
            lane = notice_rates[0].lane
            # Check effective dates if stated in email
            m_eff = re.search(r"effective\s+from\s+(\d{4}-\d{2}-\d{2})(?:\s+to\s+(\d{4}-\d{2}-\d{2}))?", full_text, re.I)
            eff_from = m_eff.group(1) if m_eff else None
            eff_to = m_eff.group(2) if m_eff else None

            rate_notices.append(
                RateNoticeRecord(
                    sender_name=from_name,
                    sender_email=from_email.lower(),
                    date_str=date_day,
                    contract_id=None,
                    lane=lane,
                    rates=notice_rates,
                    effective_from=eff_from,
                    effective_to=eff_to,
                    source_doc=doc_path,
                    source_page=1,
                    raw_quote=notice_rates[0].raw_quote,
                )
            )

    # 3. Due Date Extensions
    # "We extend the due date of invoice TX-US-2032-5494 to 2032-08-25."
    m_ext = re.search(
        r"(?:extend\s+the\s+due\s+date\s+of\s+invoice|extension\s+for\s+invoice)\s+([A-Z0-9/\-]+)\s+to\s+(\d{4}-\d{2}-\d{2})",
        full_text,
        re.I,
    )
    if m_ext:
        iid = norm_id(m_ext.group(1))
        ext_date = m_ext.group(2)
        matched_line = ""
        for line in full_text.splitlines():
            if m_ext.group(0) in line or (iid in line and ext_date in line):
                matched_line = line.strip()
                break

        extensions.append(
            DueDateExtensionRecord(
                sender_name=from_name,
                sender_email=from_email.lower(),
                date_str=date_day,
                invoice_id=iid,
                extended_due_date=ext_date,
                source_doc=doc_path,
                source_page=1,
                raw_quote=matched_line or m_ext.group(0),
            )
        )

    return waivers, rate_notices, extensions
