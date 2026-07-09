# Test scenario checklist & simulated-statement prompt

Reference for generating synthetic bank statements to exercise this app end
to end. Companion to `bank_statement_analyzer_build_spec.md` — scenarios
below are grouped by the FR they exercise. Use this when asking Claude (or
anyone) to produce test data, and as a checklist for manual QA of a run.

## Scenario checklist by pipeline stage

**Ingestion & parsing (FR-1, FR-2)**
- Multiple banks in one run, each with a different column layout/narration
  style. Recognized names live in `pdf_parser.py:KNOWN_BANK_NAMES` (HDFC,
  ICICI, SBI, Axis, Kotak, PNB, BoB, Canara, Union Bank, IDFC First, Yes
  Bank, IndusInd, Bank of India, Indian Bank, Central Bank) — also include
  one bank *not* in that list to confirm the "guess from text" fallback.
- Two table layouts: separate Debit/Credit columns vs. single Amount +
  Dr/Cr indicator column.
- Header synonym variety (Narration/Particulars/Transaction Details; Value
  Date/Txn Date; Withdrawal Amt/Debit) — and one genuinely unrecognized
  layout that must fall through to the `field_locator` LLM.
- Multiple date formats: `DD-MM-YYYY`, `DD/MM/YY`, `DD-Mon-YYYY`,
  `DD Month YYYY`, ISO, US `MM/DD/YYYY`.
- Amount formatting: ₹/Rs./INR prefixes, comma thousands separators,
  parenthesized negatives, trailing "Dr"/"Cr" suffix.
- Password-protected PDF.
- Scanned/image-only PDF (OCR fallback) vs. borderless digitally-generated
  PDF (text present, no ruling lines) vs. a genuinely unparseable file (no
  text, no OCR-able content — hard flag).
- Multi-page statement where the header row repeats on every page.
- Missing/unlocatable basic fields — account holder name, account number,
  IFSC, bank name, statement period — each individually omittable to prove
  the "Needs attention" flag fires per field.

**Self-transfer reconciliation (FR-3)**
- Clean UPI RRN match, both legs across two different uploaded accounts.
- No RRN (cheque/NEFT) → amount + date-window (±3 days) + narration
  fallback, optionally boosted by a shared UPI VPA in both narrations.
- Multiple equally-plausible candidates (RRN collision or narration-
  similarity tie) → ambiguous → adjudicator.
- Transfer-shaped narration with no real counterpart anywhere → explicit
  unmatched.
- Same-account debit/credit (must NOT be treated as self-transfer — that's
  round-tripping, a different feature).

**Refund/reversal reconciliation (FR-3a)**
- Refund keyword credit (`refund`, `reversal`, `chargeback`, `txn fail`,
  `order cancel`, etc.) matched to its original debit via RRN (high
  confidence).
- Amount + wider date window (45 days) + narration fallback, same-account
  original debit (medium confidence) vs. cross-account (low confidence).
- Genuinely orphan refund — refund credit with no plausible original debit
  anywhere in the upload set.
- Ambiguous refund (multiple candidate original debits, same amount,
  within window) → adjudicator.

**Income classification (FR-4, FR-7)**
- Each of the 6 ITR heads: Salary, Interest, Business/Professional
  receipts, Rent, Capital gains, Other.
- Auto-tagged interest credits (keyword-based, high confidence, auto-
  flagged 26AS-likely).
- Unrecognized counterparty with no keyword match → routed to the
  `classifier` LLM.
- Loan disbursal credit and gift credit — excluded from income like
  transfers, but still surfaced on Needs Attention.
- Recurring vs. one-off counterparty (grouping is by extracted counterparty
  name).

**Credit card activity (FR-5)**
- CC bill payment narration.
- CC fees/charges (annual fee, late fee, finance charge, over-limit fee,
  GST on CC) as a distinct expense category.

**Balance integrity (FR-6)**
- At least one account where opening + credits − debits = closing exactly.
- One account deliberately off by more than the 1-paisa tolerance, to
  confirm the continuity check flags it.

**Recurring payments (FR-8)**
- EMI/SIP/Rent/Subscription debits: same counterparty + same amount, ≥3
  occurrences, roughly regular period (±4 day tolerance).
- A near-miss that should NOT qualify: only 2 occurrences, or irregular
  spacing.

**Anomalies (FR-9)**
- A credit ≥5× the median credit, or ≥₹2,00,000, whichever is higher.
- A cash deposit ≥₹50,000 with "cash"/"cdm" in the narration.
- Same-day (±1 day) equal-amount debit+credit on the same account that
  reconciliation did NOT already claim as a transfer/refund (true
  round-tripping, not a matched pair).

**Cross-cutting**
- PII presence (realistic name/account/PAN/mobile/email in header text) to
  verify masking never leaks to the LLM payload.
- At least one file that fails to parse entirely, alongside good files, to
  confirm one bad file doesn't abort the whole run (P1 resilience).

## Prompt template for generating simulated statements

The key failure mode when asking an LLM for "sample bank statements" is
generic, clean data that only exercises the happy path. To get real
coverage: enumerate the scenarios explicitly, force cross-file
relationships (self-transfers/refunds only test anything if the same
RRN/amount appears in two different files), and ask for a deliberately
imperfect set (missing fields, broken balances, ambiguous duplicates)
rather than a tidy one.

```
Generate a set of simulated Indian bank statements (plain text or table form,
not real PDFs) for testing a bank-statement-reconciliation tool. Use a single
fictional client, one Indian financial year (e.g. 1 Apr 2025-31 Mar 2026),
across N accounts at different banks: [list banks].

For each account, produce a statement with:
- Bank name, account holder name, a fake account number, fake IFSC,
  statement period, opening balance
- A column layout typical of that bank (mix: some Debit/Credit columns,
  some single Amount + Dr/Cr column), with that bank's typical narration
  format (UPI/NEFT/IMPS references, RRNs where applicable)
- A running balance that reconciles exactly (opening + credits - debits
  = closing), except for [account X], which should be OFF by a stated
  amount to test balance-continuity flagging

Plant the following scenarios, and tell me afterwards which file/row each
one lands in:
1. A clean self-transfer between two of the accounts with a matching UPI
   RRN in both narrations.
2. A self-transfer with no RRN (NEFT/cheque) that should only match via
   amount + date proximity + narration similarity.
3. Two equally-plausible same-amount transfer candidates on the same day,
   deliberately ambiguous (should NOT auto-resolve).
4. A transfer-looking debit with no real counterpart anywhere (should end
   up unmatched).
5. A merchant refund credit with an RRN matching an earlier debit in the
   SAME account.
6. A refund credit ~30 days after a same-amount debit with NO RRN echo,
   relying on narration similarity, in a DIFFERENT account (should match
   at low/medium confidence, not high).
7. An orphan refund: a refund-keyword credit with no plausible original
   debit in any file.
8. A salary credit (regular monthly, same payer/amount).
9. An interest credit (bank-standard "INT.PD"/"SB INT" wording).
10. A credit from an unrecognizable one-off counterparty (to force the
    income classifier's LLM fallback).
11. A loan disbursal credit and a gift credit (both should be excluded
    from income but flagged for review).
12. A credit-card bill payment debit and a separate card late-fee/annual-fee
    debit.
13. Three-plus monthly EMI debits, same amount/counterparty, ~30 days apart
    (recurring detection), and separately a SIP and a rent debit doing the
    same.
14. One credit that's a clear outlier (>5x the typical credit size).
15. A cash deposit over ₹50,000.
16. A same-day equal-amount debit+credit round-trip that is NOT a real
    transfer/refund pair (different counterparties/purpose).
17. Ordinary noise: UPI merchant spend, ATM withdrawals, a few small
    unremarkable transactions, so the normal case is also represented.

Also include, as a separate file/account: one statement with a garbled or
missing account-number/IFSC line (to test the "needs attention" flag for
unrecoverable fields), and one with a completely non-standard column
layout/header wording no synonym list would catch.

Output: the statements themselves, plus a short answer-key table mapping
each numbered scenario above to the specific file and transaction row(s)
it appears in.
```
