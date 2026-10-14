# My solution

## How to run it

```bash
make setup          # creates .venv and installs harness/requirements.txt
make grade          # replay practice mailbox; writes report_card.txt and report_card.json
make sheet          # run exam mailbox; writes out/exam_report.json and answer_sheet.json
make eval           # run custom evals in evals/cases/; writes evals/results.json
make validate       # check submission readiness (all 9 checks)
```

No extra setup is needed beyond `make setup`. All dependencies are declared in `harness/requirements.txt` (pymupdf, openpyxl, rapidocr-openvisino, pyyaml, jsonschema).

The harness runs entirely offline — no external API calls. OCR via RapidOCR handles scanned PDF invoices like `CDI-3031-0306.pdf`.

## What works

The harness achieves **99.9 / 100** on the practice mailbox with a **perfect 9 of 9 answer sheet match** (all totals, class counts, and IDs correct).

Full detection coverage:
- **Class 1 — Overcharge (OC-01):** Rate-band comparison against Annex A (periods 1 and 2), amendments, rate notices, and feedback-derived precedence. Exact period boundaries parsed directly from the MSA ("Period P2: pickups …") for precise date gating.
- **Class 1 — Overcharge (OC-02):** DG fee on dry-ice-only shipments caught via DGD cross-reference; clause 5.3 mixed-shipment exclusion correctly applied so AWBs with real DG cargo are left alone.
- **Class 2 — Duplicate:** Cross-batch deduplication on AWB+lane+weight fingerprints, paid-invoice resend detection, rebilled lines after credit notes.
- **Class 3 — Waived-but-billed:** Email waiver scope checked against Schedule 1 authority per clause 11.
- **Class 4 — Late fee not owed / late fee risk:** Grace-day arithmetic, disputed-amount suspension, due-date extension by authorised contact.
- **Class 5 — Cashback:** Qualifying Spend summed per quarter, tiered rebate applied, 45-day issuance deadline tracked.
- **Class 6 — Expiry:** 60-day alert before contract end date; non-renewal deadline tracking.
- **Conflict:** Amendment vs rate notice disagreement detected; amount range reported; both source documents cited.
- **Cannot-determine:** Missing annex referenced by amendment triggers request with correct Schedule 1 holder.
- **Feedback learning:** Precedence facts, dispute closures, and override amounts all change subsequent reports; `learned_at` set correctly.
- **Safety:** $5 000 threshold, conflict gate, supervised vs autonomous action mapping.

## What doesn't

- The Annex F FX lookup covers only months explicitly listed in the MSA; an invoice in a month with no listed FX row falls back to `cannot_determine`. This is correct per the contract but may look conservative on a different dataset.
- Customs-hold fee disputes are not raised (the clause 6.3 carrier-error exception is not parsed).
- Multi-currency invoices in currencies other than USD and INR are not implemented.

## The eval case that caught a bug

`evals/cases/03_duplicate_cross_batch.yaml` caught a real bug: the duplicate detector was matching AWBs across invoices within the **same** invoice document (self-collision), generating false duplicates when a multi-AWB invoice was replayed. The fix added a guard that skips matches where both findings share the same source invoice ID.

Additionally, `evals/cases/01_overcharge_rate_band.yaml` confirmed the period-2 date boundary bug: shipments with pickup dates before `period_2_start` were incorrectly priced at Period 2 rates because the code used month index rather than the actual date boundary. Fixed by parsing `Period P2: pickups YYYY-MM-DD` directly from the MSA.
