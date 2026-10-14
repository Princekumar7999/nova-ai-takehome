"""Ingestion pipeline that processes a batch of emails and attachments and stores raw extracted documents."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.reader import DocumentReader
from harness.storage import Storage


class IngestionPipeline:
    def __init__(self, state_dir: Path | str):
        self.storage = Storage(state_dir)

    def ingest_batch(self, batch_dir: Path | str) -> None:
        batch_path = Path(batch_dir).resolve()
        batch_name = batch_path.name

        # If already ingested, we can still idempotently update or re-parse
        eml_files = sorted(batch_path.glob("*.eml"))

        for eml_file in eml_files:
            eml_bytes = eml_file.read_bytes()
            eml_parsed = DocumentReader.parse_email_message(eml_bytes)
            eml_rel = f"{batch_name}/{eml_file.name}"

            # Store email itself as document
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

            # Process attachments
            for att_name, att_bytes in eml_parsed["attachments"]:
                ext = att_name.rsplit(".", 1)[-1].lower() if "." in att_name else ""
                att_rel = f"{eml_rel}#{att_name}"

                try:
                    pages_text, is_scan = DocumentReader.read_pages(att_bytes, ext)
                except Exception as e:
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

        self.storage.record_batch(batch_name)
        self.storage.save()
