"""Ingestion pipeline that processes emails and attachments and populates structured business objects."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.contract_parser import parse_amendment, parse_msa
from harness.email_events_parser import parse_email_events
from harness.invoice_parser import (
    parse_ap_register,
    parse_credit_note,
    parse_date_str,
    parse_invoice,
    parse_tracking,
)
from harness.models import TrackingEvent, norm_id
from harness.reader import DocumentReader
from harness.storage import Storage


class IngestionPipeline:
    def __init__(self, state_dir: Path | str):
        self.storage = Storage(state_dir)

    def ingest_batch(self, batch_dir: Path | str) -> None:
        batch_path = Path(batch_dir).resolve()
        batch_name = batch_path.name

        eml_files = sorted(batch_path.glob("*.eml"))

        for eml_file in eml_files:
            eml_bytes = eml_file.read_bytes()
            eml_parsed = DocumentReader.parse_email_message(eml_bytes)
            eml_rel = f"{batch_name}/{eml_file.name}"

            # Store email document
            self.storage.data["documents"][eml_rel] = {
                "doc": eml_rel,
                "batch": batch_name,
                "type": "eml",
                "received_date": eml_parsed["date_day"],
                "received_iso": eml_parsed["date_iso"],
                "from_name": eml_parsed["from_name"],
                "from_email": eml_parsed["from_email"],
                "to": eml_parsed["to"],
                "cc": eml_parsed["cc"],
                "subject": eml_parsed["subject"],
                "body": eml_parsed["body"],
                "pages": [eml_parsed["full_eml_text"]],
                "is_scan": False,
            }

            # Parse email events: waivers, rate notices, due extensions
            waivers, rate_notices, extensions = parse_email_events(
                doc_path=eml_rel,
                from_name=eml_parsed["from_name"],
                from_email=eml_parsed["from_email"],
                subject=eml_parsed["subject"],
                body=eml_parsed["body"],
                date_day=eml_parsed["date_day"],
            )

            for w in waivers:
                self.storage.data["waivers"].append(w)
            for rn in rate_notices:
                self.storage.data["rate_notices"].append(rn)
            for ext in extensions:
                self.storage.data["due_extensions"].setdefault(ext.invoice_id, []).append(ext)

            # Check if email is carrier "picked up" confirmation
            # e.g. "Shipment TE56915687 picked up on 2031-12-25"
            m_pu = re.search(r"(?:AWB|shipment)?\s*([A-Z0-9\-]+)\s+(?:was\s+)?picked\s+up\s+on\s+(\d{4}-\d{2}-\d{2})", eml_parsed["body"], re.I)
            if m_pu:
                awb_pu = m_pu.group(1).strip()
                d_pu = m_pu.group(2).strip()
                matched_line = ""
                for l in eml_parsed["body"].splitlines():
                    if m_pu.group(0) in l:
                        matched_line = l.strip()
                        break
                ev = TrackingEvent(
                    awb=awb_pu,
                    event="PICKED_UP",
                    event_date=d_pu,
                    lane=None,
                    source_doc=eml_rel,
                    source_page=1,
                    raw_quote=matched_line or m_pu.group(0),
                )
                self.storage.data["tracking_events"].setdefault(awb_pu, []).append(ev)

            # Process attachments
            for att_name, att_bytes in eml_parsed["attachments"]:
                ext = att_name.rsplit(".", 1)[-1].lower() if "." in att_name else ""
                att_rel = f"{eml_rel}#{att_name}"

                try:
                    pages_text, is_scan = DocumentReader.read_pages(att_bytes, ext)
                except Exception:
                    pages_text, is_scan = [], False

                self.storage.data["documents"][att_rel] = {
                    "doc": att_rel,
                    "batch": batch_name,
                    "type": ext,
                    "att_name": att_name,
                    "parent_eml": eml_rel,
                    "received_date": eml_parsed["date_day"],
                    "received_iso": eml_parsed["date_iso"],
                    "from_name": eml_parsed["from_name"],
                    "from_email": eml_parsed["from_email"],
                    "subject": eml_parsed["subject"],
                    "pages": pages_text,
                    "is_scan": is_scan,
                }

                # Try parsing as MSA contract
                msa = parse_msa(att_rel, pages_text)
                if msa:
                    self.storage.data["contracts"][msa.contract_id] = msa

                # Try parsing as Amendment
                amd = parse_amendment(att_rel, pages_text)
                if amd:
                    self.storage.data["amendments"][amd.amendment_id] = amd

                # Try parsing as Invoice
                inv = parse_invoice(att_rel, pages_text, is_scan=is_scan)
                if inv:
                    existing = self.storage.data["invoices"].get(inv.invoice_id)
                    if existing:
                        # Existing invoice re-sent
                        # Increment resend count and add received date
                        if hasattr(existing, "resend_count"):
                            existing.resend_count += 1
                            existing.received_dates.append(eml_parsed["date_day"])
                        elif isinstance(existing, dict):
                            existing["resend_count"] = existing.get("resend_count", 0) + 1
                            existing.setdefault("received_dates", []).append(eml_parsed["date_day"])
                    else:
                        inv.received_dates = [eml_parsed["date_day"]]
                        self.storage.data["invoices"][inv.invoice_id] = inv

                # Try parsing as Credit Note
                cn = parse_credit_note(att_rel, pages_text)
                if cn:
                    self.storage.data["credit_notes"][cn.credit_note_id] = cn

                # Try parsing as AP Register
                ap_entries = parse_ap_register(att_rel, pages_text)
                for ape in ap_entries:
                    self.storage.data["ap_register"].setdefault(ape.invoice_id, []).append(ape)

                # Try parsing as Tracking Export
                tr_events = parse_tracking(att_rel, pages_text)
                for tr in tr_events:
                    self.storage.data["tracking_events"].setdefault(tr.awb, []).append(tr)

        self.storage.record_batch(batch_name)
        self.storage.save()
