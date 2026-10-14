# AI use

## Tools

- **Google Antigravity (Gemini / Claude Sonnet via Antigravity IDE)** — used for the entire implementation: architecture design, code generation, debugging, and documentation.
- **PyMuPDF (pymupdf)** — PDF text extraction (open source, no AI).
- **RapidOCR** — CPU-based OCR for image-only PDFs (open source, no AI in the sense of hosted LLMs).

No external LLM API calls are made at runtime. The harness is fully deterministic and offline.

## How you worked with them

The AI assistant was used to:
1. Read and analyse all provided files (`BRIEF.pdf`, `README.md`, `GLOSSARY.md`, example documents, grader source).
2. Generate initial harness skeleton and iteratively refine each auditor module.
3. Debug false findings (e.g. TX-US-2035-0208/0211/0215 false overcharges caused by period-date boundary error; TX-US-2035-0257 false DG-fee finding caused by missing mixed-shipment clause 5.3 check).
4. Verify score after each fix by running `make grade` and reading `report_card.json`.
5. Draft documentation (SOLUTION.md, DESIGN.md, this file).

The AI was checked by: running the grader after every significant change; reading individual invoice PDFs to verify parsed values; manually cross-referencing contract clauses against computed amounts.

Key errors the AI made and were corrected:
- Period 2 start date was determined from Annex C month count (month index ≥ 7) rather than the explicit `Period P2: pickups …` date in the MSA → fixed.
- Dry ice OC-02 check did not exclude mixed DG shipments per clause 5.3 → fixed.
- Schedule 1 holder lookup used dict keyed by category rather than scanning `contacts_list` for carrier personnel → fixed to return the correct named person.

## Written without AI

The `Makefile`, `README.md`, `GLOSSARY.md`, `BRIEF.pdf`, `SUBMISSION.md` and all grader code were provided with the assignment and were not modified.

## Prompt log

The full session was conducted in the Antigravity IDE. The conversation is available in the IDE session history. Key prompt milestones:

1. *"Role: Senior AI Systems Engineer … complete the Nova AI take-home assignment …"* — initial comprehensive brief that initiated the full implementation.
2. *"make at least 20 commits"* — incremental commit cadence instruction.
3. *"complete the assignment in speed … quality must not compromise"* — final push instruction.
4. *"complete the assignment under 25 mins"* — final completion instruction.

The AI ran `make grade` after each major change and iterated based on the `report_card.json` output until achieving 99.9/100 with 9/9 answer sheet rows matching.
