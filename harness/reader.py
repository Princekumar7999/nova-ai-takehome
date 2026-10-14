"""Unified document reader and citation resolver adhering strictly to grader/extract.py semantics."""
from __future__ import annotations

import io
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from grader.extract import (
    SEP,
    attachments as extract_attachments,
    email_lines,
    norm,
    pages_of,
    parse_eml,
    pdf_is_image_only,
    pdf_pages,
    xlsx_pages,
)
from harness.ocr import OCREngine, is_image_only_pdf


class DocumentReader:
    """Reads eml files, attachments, and extracts text per page matching grader conventions."""

    @staticmethod
    def parse_email_message(data: bytes) -> dict:
        msg = parse_eml(data)
        name, addr = parseaddr(str(msg["From"]))
        dt = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
        date_iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""
        date_day = dt.strftime("%Y-%m-%d") if dt else ""

        body = msg.get_body(preferencelist=("plain",))
        body_text = body.get_content().replace("\r\n", "\n").rstrip("\n") if body else ""

        att_list = extract_attachments(data)
        lines = email_lines(msg)

        return {
            "from_name": name,
            "from_email": addr.lower(),
            "to": str(msg["To"] or ""),
            "cc": str(msg["Cc"] or ""),
            "date_iso": date_iso,
            "date_day": date_day,
            "subject": str(msg["Subject"] or ""),
            "body": body_text,
            "full_eml_text": "\n".join(lines),
            "attachments": att_list,  # list of (filename, bytes)
        }

    @staticmethod
    def read_pages(data: bytes, ext: str) -> tuple[list[str], bool]:
        """Return (pages_text, is_scan). If scan, falls back to RapidOCR."""
        ext = ext.lower().lstrip(".")
        if ext == "pdf":
            if is_image_only_pdf(data):
                ocr_pages = OCREngine.get_instance().extract_pdf_pages(data)
                return ocr_pages, True
            return pdf_pages(data), False
        if ext == "xlsx":
            return xlsx_pages(data), False
        if ext == "eml":
            return ["\n".join(email_lines(parse_eml(data)))], False
        raise ValueError(f"Unsupported format: .{ext}")
