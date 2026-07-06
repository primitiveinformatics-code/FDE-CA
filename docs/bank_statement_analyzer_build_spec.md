# Agentic Bank Statement Analyzer — Build Spec

Desktop app for an Indian CA firm. Ingests multi-bank PDF statements, reconciles self-transfers and refunds, classifies income for ITR filing, and exports a fully-referenced Excel workbook. Reasoning is done by Claude Sonnet via tool-use; all arithmetic is deterministic code.

## Tech stack

- **UI:** PySide6 (Qt for Python) — native desktop widgets. Do not use Streamlit (rejected: can't freeze cleanly into a single .exe with PyInstaller).
- **LLM:** Claude Sonnet, Anthropic tool-use (function calling) loop. Used only for: locating fields on unrecognized bank layouts, classification/tagging, adjudicating ambiguous matches.
- **PDF extraction:** pdfplumber (text-first) + Tesseract OCR (fallback for scanned/image PDFs).
- **Data / math:** pandas. **All arithmetic, totals, and reconciliation math must be computed in code — never by the LLM.**
- **Excel output:** xlsxwriter.
- **Secrets:** OS keyring — API key entered once in-app, stored in the OS credential vault, never plaintext.
- **Packaging:** PyInstaller, single `.exe`. No server/webview dependency (this is why PySide6 was chosen over Streamlit).

## Architecture: orchestrator + deterministic workers

Pattern: one **Orchestrator** (Sonnet) plans the run and delegates; deterministic Python tools do the heavy lifting (parsing, matching, math); the LLM is invoked only where judgment is needed. Do not build a single mega-prompt or a fully autonomous free-roaming agent.

1. **Orchestrator (Sonnet)** — plans processing order, dispatches per-statement work, collects structured results, triggers reconciliation/classification/export. Never touches raw PII (works on masked, structured data). Tracks cumulative tokens/cost via `cost_meter`.
2. **Extraction agent (per statement)** — deterministic parse first (pdfplumber/pandas + OCR fallback). Sonnet (`field_locator`) is used only to locate basic fields / map columns on unrecognized layouts. Confirms name/account/IFSC/period or flags the file. Assigns every row a `serial + bank + pdf` reference.
3. **Reconciliation agent** — deterministic `transfer_matcher`: match on UPI RRN/reference number when present; if absent (cheque/NEFT), fall back to amount + date (±N days) + narration keyword similarity, always tagged lower-confidence. Sonnet (`adjudicator`) resolves only ambiguous leftovers. Emits confirmed pairs (both legs referenced) + unmatched call-outs. The same deterministic-first/adjudicator-fallback pattern also drives `refund_matcher` (see FR-3a): refund/reversal credits are paired back to their originating debit wherever possible.
4. **Classification agent** — groups non-transfer, non-refund credits by counterparty; suggests an ITR head (Salary, Interest, Business/Professional receipts, Rent, Capital gains, Other) with a confidence tag; flags "26AS-likely" credits; detects credit-card bill payments/fees; buckets debits into expense categories; flags recurring payments (EMI/SIP/rent/subscriptions) and anomalies (large credits, large cash deposits, same-day round-tripping).
5. **Export agent (deterministic, no LLM)** — re-hydrates PII references locally, runs all final sums through `calculator`/pandas, writes the multi-sheet Excel via xlsxwriter, plus a Tally-friendly CSV export. If the review toggle (FR-11) was on, applies the CA's in-app edits before writing.

**Shared tools:** `pdf_parser`/`ocr`, `pii_masker`, `transfer_matcher`, `refund_matcher`, `calculator`, `xlsxwriter`, `csv_export`, `cost_meter`, `reference_hydrator`.

## Functional requirements

- **FR-1 Ingestion:** Native file picker, any number of PDFs/banks. Handle password-protected PDFs (prompt per file). Detect scanned/image PDFs → OCR fallback; if neither text nor OCR-able, flag rather than proceed silently. When OCR is used, show the raw OCR'd text next to each extracted field so the CA/staff can manually verify/correct it before the file proceeds.
- **FR-2 Basic-field confirmation:** Extract account-holder name, account number, bank, IFSC, statement period per file. If any field can't be confidently located, flag the file to a "Needs attention" list and exclude it from downstream matching until resolved.
- **FR-3 Self-transfer reconciliation:** Identify transfers between the client's own accounts (any statement uploaded in the same run is treated as the same client; a transfer whose counterparty resolves to another uploaded account is a self-transfer candidate). Match on UPI RRN → fallback amount+date+narration (flagged lower-confidence) → counterparty name/VPA. Unmatched candidates are called out explicitly. Confirmed self-transfers are excluded from income, listed on their own sheet with both legs referenced.
- **FR-3a Refund / reversal reconciliation:** Identify refunds and reversals (merchant refunds, failed/cancelled-transaction reversals, chargebacks) among credits. Match each refund credit back to its originating debit using the same tiered strategy as FR-3: UPI RRN/reference-number equality (high confidence) → fallback amount + a longer date window (default 45 days, configurable) + narration similarity, preferring same-account pairs (medium confidence) over cross-account pairs (low confidence) → leftovers escalated to the `adjudicator` for genuinely ambiguous cases, or called out explicitly as unmatched ("orphan") refund candidates when no plausible original debit exists. Confirmed refund pairs (and orphan refund candidates) are excluded from income — same treatment as self-transfers — and are always listed on their own "Refunds" sheet with both legs referenced (or the single leg, for an orphan), mirroring the "Self-transfers" sheet's shape. Every refund, matched or not, is also flagged on "Needs attention" for CA confirmation.
- **FR-4 Income identification & classification:** List non-transfer, non-refund credits grouped by counterparty (total, frequency, dates). Suggest an ITR head + confidence per source. Auto-tag interest credits as Interest income. Exclude loan disbursals, refunds/reversals (see FR-3a), and gifts from income (same treatment as self-transfers) but always flag them on "Needs attention" for CA confirmation.
- **FR-5 Credit-card payment detection:** Identify and list credit-card bill payments and card charges/fees, each with date, amount, source reference.
- **FR-6 Calculations via tool, not the model:** All sums/nets/reconciliations computed by a deterministic calculator/pandas tool — never the LLM. Balance-continuity check per account: opening + Σcredits − Σdebits = closing.
- **FR-7 TDS / 26AS cross-hint:** Flag interest/professional-receipt credits likely to appear in Form 26AS/AIS.
- **FR-8 Recurring-payment detection:** Surface EMIs, SIPs, rent, subscriptions (from periodicity) as a distinct group in the expenditure summary.
- **FR-9 Anomaly & round-trip flags:** Flag unusually large credits, cash deposits over a configurable threshold, and same-day in/out round-tripping — surfaced on "Needs attention".
- **FR-10 CSV / Tally export:** Emit a generic, Tally-import-friendly CSV of the full normalised ledger alongside the Excel workbook.
- **FR-11 Optional pre-export review (toggle, default OFF):** UI toggle; when ON, show an editable grid so the CA/staff can correct a suggested tax head or expense category (or un-flag a match) before the workbook is generated. When OFF (default), export is fully automatic.
- **FR-12 Token usage & cost viewer:** Live counter in the UI sidebar tracking cumulative tokens and estimated $ cost during the run; summary card on completion with the run total; a configurable cost threshold triggers a warning banner if a run's estimated cost exceeds it.

## Explicitly out of scope for this build (deferred to v1.1 — do not build)

- Bank-format learning cache (persisted per-bank parsing profiles)
- Multi-year / multi-client batching (queued processing of several clients/FYs)

## Privacy & security requirements (P0 — non-negotiable)

- Mask PII (account number, PAN, full name, mobile, email) locally with pandas **before** any text is sent to the Sonnet API. Raw PII must never leave the machine.
- References are re-hydrated locally only, when writing the Excel/CSV output.
- API key stored via OS keyring, never in plaintext or in the bundled `.exe`.

## Output spec — Excel workbook + CSV

One `.xlsx` per client run. Every row carries a source reference: `serial · bank · pdf`.

| Sheet | Contents | Key columns |
|---|---|---|
| Summary | Client, accounts covered, period, headline totals, flag count | Totals, review count |
| Self-transfers | Confirmed pairs (both legs) + unmatched candidates | out_ref, in_ref, amount, status |
| Refunds | Confirmed original-debit/refund-credit pairs + unmatched ("orphan") refund candidates | original_debit_ref, refund_ref, amount, status |
| Income sources | Non-transfer, non-refund credits grouped by payer, suggested ITR head | payer, total, freq, dates, head, confidence, ref |
| Credit-card payments | CC bill payments & card fees | date, amount, card_hint, ref |
| Expenditure summary | Debits bucketed by category with totals | category, total, count |
| Needs attention | Every flag: unread fields, unmatched transfers, unmatched/orphan refunds, low-confidence heads, edge-case income, anomalies | issue, file, ref, suggestion |
| All transactions | Full normalised ledger, backing detail for all sheets | serial, bank, pdf, date, desc, dr, cr, bal, tags |

Plus a separate CSV export (FR-10) mirroring the "All transactions" columns, Tally-import-friendly.

Expense category taxonomy (confirmed, use as-is): UPI merchant spend, cash/ATM, loan EMIs, charges & fees, credit-card payments, taxes/TDS, other.

## Non-functional requirements

- **P0 Privacy:** raw PII never sent to the API; verify with an outbound-payload test.
- **P0 Traceability:** 100% of output figures reference a source line.
- **P0 Numeric integrity:** no LLM arithmetic; balance-continuity check per account.
- **P1 Resilience:** one bad file flags and is excluded; doesn't abort the run.
- **P1 Cost visibility:** FR-12 (live sidebar counter, end-of-run summary, threshold warning).
- **P2 Portability:** runs offline except metered API calls; clear error if no network/key.

## Scope constraint

v1 processes a single Indian financial year (Apr–Mar) per run. Cross-FY and custom date ranges are out of scope.

## Success metrics

- Time to first draft: < 5 min for ~5 accounts.
- Basic-field extraction: 100% correct or explicitly flagged.
- Self-transfer match rate: ≥ 95%, remainder flagged.
- Refund match rate: ≥ 90% of refund/reversal credits paired to an originating debit where one exists in the uploaded statements, remainder flagged as orphan candidates.
- 100% of output figures carry a source reference.
