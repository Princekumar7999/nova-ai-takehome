"""Persistent state storage manager for disk-based state across batch invocations."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.models import (
    AmendmentMeta,
    APRegisterEntry,
    ContractMeta,
    CreditNoteRecord,
    DueDateExtensionRecord,
    InvoiceRecord,
    RateNoticeRecord,
    TrackingEvent,
    WaiverRecord,
)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        return super().default(obj)


class Storage:
    def __init__(self, state_dir: Path | str):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._data_file = self.state_dir / "harness_state.json"
        self._data: dict[str, Any] = self._load()

    def _init_state(self) -> dict[str, Any]:
        return {
            "ingested_batches": [],
            "documents": {},
            "contracts": {},
            "amendments": {},
            "invoices": {},
            "credit_notes": {},
            "tracking_events": {},
            "ap_register": {},
            "waivers": [],
            "rate_notices": [],
            "due_extensions": {},
            "feedback": [],
            "prior_reports": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self._data_file.is_file():
            return self._init_state()
        try:
            raw = json.loads(self._data_file.read_text())
            hydrated = self._init_state()
            hydrated["ingested_batches"] = raw.get("ingested_batches", [])
            hydrated["documents"] = raw.get("documents", {})
            hydrated["feedback"] = raw.get("feedback", [])
            hydrated["prior_reports"] = raw.get("prior_reports", {})

            # Hydrate contracts
            for k, v in raw.get("contracts", {}).items():
                hydrated["contracts"][k] = ContractMeta.from_dict(v) if isinstance(v, dict) else v

            # Hydrate amendments
            for k, v in raw.get("amendments", {}).items():
                hydrated["amendments"][k] = AmendmentMeta.from_dict(v) if isinstance(v, dict) else v

            # Hydrate invoices
            for k, v in raw.get("invoices", {}).items():
                hydrated["invoices"][k] = InvoiceRecord.from_dict(v) if isinstance(v, dict) else v

            # Hydrate credit notes
            for k, v in raw.get("credit_notes", {}).items():
                hydrated["credit_notes"][k] = CreditNoteRecord.from_dict(v) if isinstance(v, dict) else v

            # Hydrate tracking events
            for k, ev_list in raw.get("tracking_events", {}).items():
                hydrated["tracking_events"][k] = [
                    TrackingEvent.from_dict(e) if isinstance(e, dict) else e for e in ev_list
                ]

            # Hydrate AP register
            for k, ap_list in raw.get("ap_register", {}).items():
                hydrated["ap_register"][k] = [
                    APRegisterEntry.from_dict(a) if isinstance(a, dict) else a for a in ap_list
                ]

            # Hydrate waivers
            hydrated["waivers"] = [
                WaiverRecord.from_dict(w) if isinstance(w, dict) else w for w in raw.get("waivers", [])
            ]

            # Hydrate rate notices
            hydrated["rate_notices"] = [
                RateNoticeRecord.from_dict(rn) if isinstance(rn, dict) else rn for rn in raw.get("rate_notices", [])
            ]

            # Hydrate due extensions
            for k, ext_list in raw.get("due_extensions", {}).items():
                hydrated["due_extensions"][k] = [
                    DueDateExtensionRecord.from_dict(e) if isinstance(e, dict) else e for e in ext_list
                ]

            return hydrated
        except Exception:
            return self._init_state()

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
