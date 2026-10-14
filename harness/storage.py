"""Persistent state storage across batch invocations under --state-dir."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        return super().default(obj)


class Storage:
    def __init__(self, state_dir: Path | str):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._data_file = self.state_dir / "harness_state.json"
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._data_file.is_file():
            try:
                return json.loads(self._data_file.read_text())
            except Exception:
                return self._init_state()
        return self._init_state()

    def _init_state(self) -> dict[str, Any]:
        return {
            "ingested_batches": [],
            "documents": {},  # canonical_doc -> doc_meta
            "contracts": {},  # contract_id -> contract_dict
            "amendments": {},  # amendment_id -> amendment_dict
            "invoices": {},  # invoice_id -> invoice_dict
            "credit_notes": {},  # credit_note_id -> credit_note_dict
            "tracking_events": {},  # awb -> list of events
            "ap_register": {},  # invoice_id -> list of ap entries
            "waivers": [],  # list of waiver dicts
            "rate_notices": [],  # list of rate notice dicts
            "due_extensions": {},  # invoice_id -> list of extension dicts
            "feedback": [],  # list of feedback item dicts
            "prior_reports": {},  # batch_name -> report dict
        }

    def save(self) -> None:
        temp_file = self.state_dir / "harness_state.json.tmp"
        temp_file.write_text(json.dumps(self._data, cls=DecimalEncoder, indent=2))
        temp_file.replace(self._data_file)

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def record_batch(self, batch_name: str) -> None:
        if batch_name not in self._data["ingested_batches"]:
            self._data["ingested_batches"].append(batch_name)

    def is_batch_ingested(self, batch_name: str) -> bool:
        return batch_name in self._data["ingested_batches"]
