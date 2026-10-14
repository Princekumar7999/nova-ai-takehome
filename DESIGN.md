# Design

## Fact model

Each email batch is processed by a pipeline that reads `.eml` files and their attachments (PDF via PyMuPDF/OCR, XLSX via OpenPyXL). Facts are extracted into typed Python dataclasses and persisted as JSON in a state directory between CLI invocations.

**Key types stored:**
- `ContractMeta` — keyed by contract ID (e.g. `TBX-US-EXP-01`); holds Annex A rate bands with weight ranges and period prices, Annex C fuel pct by month, Annex D accessorial fees, Annex F FX rates, Schedule 1 contacts with categories, term dates, rebate tiers, and period boundaries.
- `AmendmentMeta` — keyed by amendment ID; holds replaced rate bands and references to unheld annexes.
- `InvoiceRecord` — keyed by invoice ID; holds per-AWB line items (lane, weight, rate billed, amount, raw line text).
- `RateNoticeRecord`, `WaiverRecord`, `DueDateExtensionRecord`, `CreditNoteRecord`, `TrackingEvent` — each carries `source_doc` and `source_page` for citation.
- `APRegisterEntry` — payment dates keyed by invoice ID.

Every fact records a `source_doc` (e.g. `batch_01/0002.eml#MSA-TBX-US-EXP-01.pdf`) and `source_page`, enabling exact evidence quotes for all findings.

## Conflicts

The harness detects conflicts when two authoritative instruments disagree on the same lane and date without a clear precedence rule. Specifically: if an amendment and a rate notice both cover a shipment's lane and pickup date, the harness checks whether clause 13.3 applies ("neither prevails automatically"). If the resulting amounts differ, the finding is returned as `verdict: conflict` with an `amount_range` showing the spread and evidence citing both documents plus clause 13.3.

When one source clearly outranks the other — e.g. feedback fact `type: precedence` names `use: amendment` — the conflict is resolved and a `finding` is returned instead.

## Confidence

Confidence is computed as a bounded float in [0.0, 1.0]:
- Starts at 0.95 for a finding with all evidence present and authorised source.
- Boosted to 0.98 when feedback confirms the rate.
- Reduced by 0.15 for missing evidence (e.g. no tracking event, falling back to invoice date).
- Reduced by 0.10 when relying on non-authorised contact for rate notices.
- Set to 0.75 for `conflict` and `cannot_determine` verdicts.

Confidence feeds the AUROC and Brier score in the gating component. No external model is used — all scoring is rule-based on parsed facts.

## People in the loop

In **supervised** mode every finding is a `draft:*` action — the system drafts a dispute letter or document request, but nothing is sent without a human review. Portfolio items (cashback, expiry, late fee risk) are `human_review`.

In **autonomous** mode findings above the $5,000 threshold or classified as `conflict` become `escalate`. Findings below threshold with confidence ≥ 0.90 become `execute:dispute` or `execute:request_document`. The 3-or-more outbound-action hard gate prevents runaway autonomy.

## Feedback

Feedback items arrive as JSON after a batch. Each item carries a `fact` with a `type` (e.g. `precedence`, `rate_accepted`, `dispute_closed`, `override_amount`) and a `scope` specifying `contract_id`, `lane`, and a date window. Facts are stored in the state directory and replayed on every subsequent `report` call. A finding from feedback carries `learned_at.batch` so the grader can verify it changed at the right time. Waivers confirmed by feedback are scoped to the specific invoice and charge, not globally applied (clause 11.2).

## New documents

When a new batch arrives, the harness re-ingests all documents in the batch folder. New contracts replace prior versions for the same contract ID. New amendments extend the amendments list and may supersede earlier answers — the report builder calls `supersedes` on any prior finding for the same invoice that the amendment affects. A new rate notice with a later effective date overrides earlier ones for the same lane.

## Cost

This harness uses **no LLM calls**. All extraction is rule-based (regex over PDF text, structured XLSX parsing, RapidOCR for image-only PDFs). Estimated cost per 1,000 documents:

- Compute: ~2 CPU-minutes per 1,000 invoices at ~0.1 s per page × 3 pages avg → ~$0.001 on a t3.micro.
- RapidOCR (CPU): ~5 s per scanned page; if 5% of pages require OCR → 50 pages → ~4 min extra → still under $0.01.
- **Total: < $0.02 per 1,000 documents.**

With an LLM extraction layer (e.g. Gemini Flash at $0.075/M tokens, ~500 tokens/page, 3,000 pages/1,000 docs) → ~$0.11 per 1,000 docs.

## What I'd do next

- Parse structured data from carrier-specific XLSX statement layouts more robustly (currently regex-driven, fragile to column reordering).
- Add confidence calibration using held-out practice labels.
- Implement clause 6.3 carrier-error customs-hold dispute.
- Support multi-currency invoices beyond USD and INR.
- Add a review UI to visualise finding evidence inline with the PDF page.
