"""Parsers for Invoices, Credit Notes, AP Registers, and Tracking Exports."""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from harness.models import (
    APRegisterEntry,
    CreditNoteRecord,
    InvoiceLine,
    InvoiceRecord,
    TrackingEvent,
    norm_id,
    round_half_up,
)


def parse_date_str(s: str | None) -> Optional[str]:
    """Parse various date formats to ISO YYYY-MM-DD."""
    if not s:
        return None
    s = s.strip()
    # YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # DD/MM/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mth:02d}-{d:02d}"
    # DD Mon YYYY, e.g. 14 May 2033
    try:
        dt = datetime.strptime(s, "%d %b %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    try:
        dt = datetime.strptime(s, "%d %B %Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def parse_money(s: str | None) -> Optional[Decimal]:
    if not s:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", s)
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def parse_invoice(doc_path: str, pages: list[str], is_scan: bool = False) -> Optional[InvoiceRecord]:
    """Parse invoice from PDF pages or statement XLSX pages."""
    full_text = "\n".join(pages)

    # Exclude MSAs and Amendments
    if "Master Services Agreement" in full_text or "Amendment No." in full_text or "MSA-" in doc_path or "AMD-" in doc_path:
        return None

    # Detect if this is an invoice or statement
    is_inv = bool(
        re.search(r"INVOICE|Charges\s+advice|STATEMENT\s+OF\s+ACCOUNT", full_text, re.I)
        or re.search(r"TX-US-|CDU-|CDI-|TXI|TX-ST", doc_path)
    )
    if not is_inv:
        return None
    # Credit note is not an invoice
    if "CREDIT NOTE" in full_text:
        return None

    # Invoice ID
    m_id = re.search(
        r"(?:INVOICE|Charges\s+advice|STATEMENT\s+OF\s+ACCOUNT)\s+([A-Z0-9/\-:#]+)", full_text, re.I
    )
    if not m_id:
        m_id = re.search(r"([A-Z0-9]+(?:/[A-Z0-9\-]+|-[A-Z0-9\-]+)+)", doc_path)
    if not m_id:
        return None
    raw_id = m_id.group(1).strip()
    inv_id = norm_id(raw_id)

    # Dates: Invoice date & Due date
    inv_date = None
    due_date = None

    # Tarrowby format: Invoice date 2035-03-03 Due date 2035-04-17
    # Or Statement date 2034-12-01 | Due date 2035-01-15
    m_dates = re.search(
        r"(?:Invoice\s+date|Statement\s+date|Document\s+date)\s+([\d/\-]+)[^\w\n]+(?:Due\s+date|Payable\s+by)\s+([\d/\-]+)",
        full_text,
        re.I,
    )
    if m_dates:
        inv_date = parse_date_str(m_dates.group(1))
        due_date = parse_date_str(m_dates.group(2))
    else:
        m_idate = re.search(r"(?:Invoice|Statement|Document)\s+date\s+([\d/\-]+)", full_text, re.I)
        if m_idate:
            inv_date = parse_date_str(m_idate.group(1))
        m_ddate = re.search(r"(?:Due\s+date|Payable\s+by)\s+([\d/\-]+)", full_text, re.I)
        if m_ddate:
            due_date = parse_date_str(m_ddate.group(1))

    # Contract ID
    m_cid = re.search(r"Contract(?:\s+reference)?\s+([A-Z0-9\-]+)", full_text, re.I)
    contract_id = m_cid.group(1).strip() if m_cid else ""

    # Currency
    currency = "USD"
    if "INR" in full_text or "Amount (INR)" in full_text or "CDI-" in doc_path or "TXI" in doc_path:
        currency = "INR"

    # Customer & Carrier
    customer_party = ""
    carrier_party = ""
    if "Quillmere Pharma" in full_text:
        customer_party = "QMP"
    elif "Ferrowyn Life Sciences" in full_text:
        customer_party = "FWN"

    if "Tarrowby Express" in full_text or "TBX" in contract_id or "TX-" in inv_id:
        carrier_party = "TBX"
    elif "Caldrith Air" in full_text or "CDA" in contract_id or "CDU-" in inv_id or "CDI-" in inv_id:
        carrier_party = "CDA"

    # Total billed
    m_tot = re.search(
        r"(?:Total\s+payable|Total\s+USD|Total\s+INR|Total)\s+(?:USD|INR|[\$₹])?\s*\|?\s*([\d,]+\.\d{2})",
        full_text,
        re.I,
    )
    total_billed = parse_money(m_tot.group(1)) if m_tot else Decimal("0.00")

    # Lines
    lines: list[InvoiceLine] = []

    for page_idx, page_text in enumerate(pages):
        for line_raw in page_text.splitlines():
            line_str = line_raw.strip()
            if not line_str or line_str.startswith("No.") or line_str.startswith("Route"):
                continue

            # Format 1: Tarrowby PDF invoice line
            # 1 TX64694457 2035-02-24 Air freight USS2-INW2 192 kg 2.84 545.28
            # or Late fee: 13 - - Late fee on TX-US-2032-5494: USD 680.12 overdue, month 1 @ 1.86% - 1 month 1.86% 12.65
            # or Screening fee (adj.) (ref TE16812897) USC3-USE2 263 kg 0.16 42.08
            m_tarr = re.match(
                r"^(\d+)\s+([A-Z0-9\-]+)\s+([\d/\-]+)\s+(.+?)\s+([A-Z0-9]+-[A-Z0-9]+)\s+(.+?)\s+([\d,]+\.\d{2})$",
                line_str,
            )
            if m_tarr:
                l_no = int(m_tarr.group(1))
                awb = m_tarr.group(2).strip()
                l_date = parse_date_str(m_tarr.group(3)) or (inv_date or "")
                desc = m_tarr.group(4).strip()
                lane = m_tarr.group(5).strip()
                middle = m_tarr.group(6).strip()
                amt = parse_money(m_tarr.group(7)) or Decimal("0")

                # parse weight & rate from middle if present
                # middle could be: "192 kg 2.84" or "545.28 20.53%" or "1 shpt 116.13"
                weight = None
                rate = None
                m_w = re.search(r"([\d.]+)\s*kg", middle)
                if m_w:
                    weight = Decimal(m_w.group(1))
                rates = re.findall(r"([\d.]+)", middle)
                if rates:
                    rate = Decimal(rates[-1])

                lines.append(
                    InvoiceLine(
                        line_no=l_no,
                        awb=awb,
                        date_str=l_date,
                        charge_name=desc,
                        lane=lane,
                        chargeable_weight_kg=weight,
                        rate_billed=rate,
                        amount_billed=amt,
                        raw_line=line_str,
                    )
                )
                continue

            # Format 2: Caldrith PDF invoice line
            # USE2-INS2 CA51959332 Air freight 299 kg 5.69 1,701.31 19/05/2030
            m_cald = re.match(
                r"^([A-Z0-9]+-[A-Z0-9]+)\s+([A-Z0-9\-]+)\s+(.+?)\s+([\d,]+\.\d{2})\s+([\d/\-]+)$", line_str
            )
            if m_cald:
                lane = m_cald.group(1).strip()
                awb = m_cald.group(2).strip()
                middle = m_cald.group(3).strip()
                amt = parse_money(m_cald.group(4)) or Decimal("0")
                l_date = parse_date_str(m_cald.group(5)) or (inv_date or "")

                # Middle: "Air freight 299 kg 5.69" or "Fuel surcharge 1,701.31 23.23%" or "Storage 5 days 59.33"
                weight = None
                rate = None
                m_w = re.search(r"([\d.]+)\s*kg", middle)
                if m_w:
                    weight = Decimal(m_w.group(1))

                # Extract charge name by taking everything before the numbers/units
                m_desc = re.match(
                    r"^(Air\s+freight|Fuel\s+surcharge|Security\s+surcharge|Storage|Delivery\s+fee|Dangerous\s+goods\s+fee|Dry\s+ice\s+handling\s+fee|Screening\s+fee|Hold\s+handling|Customs\s+hold\s+fee)",
                    middle,
                    re.I,
                )
                desc = m_desc.group(1).strip() if m_desc else middle

                rates = re.findall(r"([\d.]+)", middle)
                if rates:
                    rate = Decimal(rates[-1])

                lines.append(
                    InvoiceLine(
                        line_no=len(lines) + 1,
                        awb=awb,
                        date_str=l_date,
                        charge_name=desc,
                        lane=lane,
                        chargeable_weight_kg=weight,
                        rate_billed=rate,
                        amount_billed=amt,
                        raw_line=line_str,
                    )
                )
                continue

            # Format 3: XLSX Statement line
            # Monthly account fee | 2034-11 | 1 month | 350.28 | 350.28
            if "|" in line_str:
                cells = [c.strip() for c in line_str.split("|")]
                if len(cells) >= 4 and not cells[0].startswith("Charge") and not cells[0].startswith("Total"):
                    desc = cells[0]
                    ref = cells[1]
                    amt = parse_money(cells[-1])
                    rate = parse_money(cells[-2]) if len(cells) >= 5 else amt
                    if amt is not None:
                        lines.append(
                            InvoiceLine(
                                line_no=len(lines) + 1,
                                awb=ref,
                                date_str=inv_date or "",
                                charge_name=desc,
                                lane="",
                                chargeable_weight_kg=None,
                                rate_billed=rate,
                                amount_billed=amt,
                                raw_line=line_str,
                            )
                        )
                        continue

            # Format 4: Late fee line
            # 13 - - Late fee on TX-US-2032-5494: USD 680.12 overdue, month 1 @ 1.86% - 1 month 1.86% 12.65
            if "Late fee on" in line_str or "Late payment fee" in line_str:
                amt = parse_money(line_str.split()[-1])
                if amt is not None:
                    m_ref_inv = re.search(r"Late fee on\s+([A-Z0-9/\-]+)", line_str, re.I)
                    ref_inv = m_ref_inv.group(1).strip() if m_ref_inv else ""
                    lines.append(
                        InvoiceLine(
                            line_no=len(lines) + 1,
                            awb=ref_inv,
                            date_str=inv_date or "",
                            charge_name="Late payment fee",
                            lane="",
                            chargeable_weight_kg=None,
                            rate_billed=None,
                            amount_billed=amt,
                            raw_line=line_str,
                            charge_code="LATE",
                        )
                    )
                    continue

    return InvoiceRecord(
        invoice_id=inv_id,
        contract_id=contract_id,
        customer_party=customer_party,
        carrier_party=carrier_party,
        invoice_date=inv_date or "2030-01-01",
        due_date=due_date,
        currency=currency,
        total_billed=total_billed,
        lines=lines,
        source_doc=doc_path,
        source_page=1,
        is_scan=is_scan,
        raw_header_quote=f"INVOICE {raw_id}",
    )


def parse_credit_note(doc_path: str, pages: list[str]) -> Optional[CreditNoteRecord]:
    """Parse credit note document."""
    full_text = "\n".join(pages)
    if "CREDIT NOTE" not in full_text and "CDC" not in doc_path and "TXC" not in doc_path:
        return None

    m_id = re.search(r"CREDIT NOTE\s+([A-Z0-9/\-:#]+)", full_text, re.I)
    raw_id = m_id.group(1).strip() if m_id else doc_path
    cn_id = norm_id(raw_id)

    m_date = re.search(r"Credit date\s+([\d/\-]+)", full_text, re.I)
    cn_date = parse_date_str(m_date.group(1)) if m_date else "2030-01-01"

    m_cid = re.search(r"Contract(?:\s+reference)?\s+([A-Z0-9\-]+)", full_text, re.I)
    contract_id = m_cid.group(1).strip() if m_cid else None

    # Currency
    currency = "USD"
    if "INR" in full_text:
        currency = "INR"

    # Total credit
    m_tot = re.search(r"Total credit\s+(?:USD|INR|[\$₹])?\s*([\d,]+\.\d{2})", full_text, re.I)
    amt = parse_money(m_tot.group(1)) if m_tot else Decimal("0.00")

    # Credit type & period
    m_tp = re.search(r"Credit type\s+([a-z_]+)\s*\|\s*period\s+([A-Z0-9\-]+)", full_text, re.I)
    cr_type = m_tp.group(1).strip().lower() if m_tp else None
    period = m_tp.group(2).strip() if m_tp else None

    return CreditNoteRecord(
        credit_note_id=cn_id,
        invoice_ref=None,
        contract_id=contract_id,
        credit_date=cn_date,
        currency=currency,
        total_credited=amt,
        period=period,
        credit_type=cr_type,
        source_doc=doc_path,
        source_page=1,
        raw_quote=f"CREDIT NOTE {raw_id}",
    )


def parse_ap_register(doc_path: str, pages: list[str]) -> list[APRegisterEntry]:
    """Parse AP register rows from XLSX."""
    entries = []
    for page_idx, page_text in enumerate(pages):
        page_num = page_idx + 1
        for line in page_text.splitlines():
            # Invoice no. | Invoice date | Due date | Amount | Status | Paid date
            # TX-US-2034-0379 | 2034-11-26 | due 2035-01-10 | USD 1243.96 | paid | 2035-01-03
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 5 and ("paid" in line.lower() or "open" in line.lower() or "scheduled" in line.lower()):
                iid = norm_id(cells[0])
                idate = parse_date_str(cells[1])
                m_due = re.search(r"(\d{4}-\d{2}-\d{2})", cells[2])
                ddate = m_due.group(1) if m_due else parse_date_str(cells[2])
                amt = parse_money(cells[3]) or Decimal("0")
                curr = "INR" if "INR" in cells[3] else "USD"
                status = cells[4].lower()
                pdate = parse_date_str(cells[5]) if len(cells) >= 6 else None

                entries.append(
                    APRegisterEntry(
                        invoice_id=iid,
                        invoice_date=idate or "",
                        due_date=ddate or "",
                        currency=curr,
                        amount=amt,
                        status=status,
                        paid_date=pdate,
                        source_doc=doc_path,
                        source_page=page_num,
                        raw_quote=line.strip(),
                    )
                )
    return entries


def parse_tracking(doc_path: str, pages: list[str]) -> list[TrackingEvent]:
    """Parse tracking export rows from XLSX."""
    events = []
    for page_idx, page_text in enumerate(pages):
        page_num = page_idx + 1
        for line in page_text.splitlines():
            # AWB | Event | Date | Station | Reason code
            # TX23827044 | PICKED_UP | 2034-11-04 | USS2 | -
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 3 and not cells[0].startswith("AWB"):
                awb = cells[0]
                ev = cells[1]
                ev_date = parse_date_str(cells[2])
                station = cells[3] if len(cells) >= 4 else None
                if ev_date:
                    events.append(
                        TrackingEvent(
                            awb=awb,
                            event=ev,
                            event_date=ev_date,
                            lane=station,
                            source_doc=doc_path,
                            source_page=page_num,
                            raw_quote=line.strip(),
                        )
                    )
    return events
