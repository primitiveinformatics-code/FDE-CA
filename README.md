# Bank Statement Analyzer

Desktop app for an Indian CA firm: ingests multi-bank PDF statements,
reconciles self-transfers and refunds, classifies income for ITR
filing, and exports a fully-referenced Excel workbook (+ Tally CSV).

See `docs/bank_statement_analyzer_build_spec.md` for the full build spec.

## Architecture

Orchestrator (Sonnet, tool-use) + deterministic Python workers. The LLM
is invoked only for three narrow judgment calls — locating columns on an
unrecognized bank layout (`field_locator`), resolving an ambiguous
self-transfer/refund leftover (`adjudicator`), and suggesting an ITR
head for an unrecognized income counterparty (`classifier`). All
arithmetic, matching, and export runs in deterministic code.

```
src/bank_statement_analyzer/
  config.py                 thresholds, taxonomies, pricing
  parsing/                  pdfplumber + OCR extraction, statement models
  pii/                      PII masking (P0 — never sent raw to the LLM)
  reconciliation/           self-transfer + refund matchers
  classification/           ITR head / expense category / anomaly detection
  calculators/               all sums & balance-continuity checks
  export/                   xlsxwriter workbook + Tally CSV
  llm/                      Anthropic tool-use client + schemas
  agents/                   orchestrator + per-stage agents
  ui/                       PySide6 desktop UI
  cost_meter.py              FR-12 token/cost tracking
  secrets_store.py           OS keyring wrapper for the API key
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r dev-requirements.txt
```

Tesseract OCR must be installed separately for the scanned-PDF fallback
(`apt install tesseract-ocr` / the Windows installer / `brew install
tesseract`) — it's an external binary, not a Python package.

## Running

```bash
source .venv/bin/activate
python -m bank_statement_analyzer.main
```

The first run prompts for an Anthropic API key, stored in the OS
credential vault (never in plaintext). The app is fully usable without
a key too — it runs in deterministic-only mode and flags anything that
would otherwise need LLM judgment for manual review.

## Tests

```bash
source .venv/bin/activate
python -m pytest
```

## Packaging (single .exe)

```bash
source .venv/bin/activate
pyinstaller pyinstaller.spec
```

Output lands in `dist/BankStatementAnalyzer`.
