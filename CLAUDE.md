# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Desktop app (PySide6) for an Indian CA firm: ingests multi-bank PDF statements, reconciles self-transfers and refunds, classifies income for ITR filing, and exports a fully-referenced Excel workbook plus a Tally-import CSV. The full functional spec (FR-1 through FR-12, non-functional requirements, output sheet layout) lives in `docs/bank_statement_analyzer_build_spec.md` — read it when a task touches requirements/behavior, since code comments reference FR numbers (e.g. `FR-3a`, `FR-12`) without restating them.

## Commands

```bash
# setup
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r dev-requirements.txt   # installs requirements.txt + pytest + openpyxl

# run the app
python -m bank_statement_analyzer.main

# tests
python -m pytest
python -m pytest tests/test_refund_matcher.py            # one file
python -m pytest tests/test_refund_matcher.py::test_name  # one test

# package as a single .exe (PyInstaller)
pyinstaller pyinstaller.spec   # output: dist/BankStatementAnalyzer
```

No linter/formatter/type-checker is configured in this repo (no ruff/black/flake8/mypy config) — don't invent lint commands.

Tesseract OCR must be installed separately as an external binary (not pip-installable) for the scanned-PDF fallback.

The app requires an Anthropic API key on first run (stored via OS keyring — see `secrets_store.py`), but is fully usable without one: it falls back to deterministic-only mode and flags anything that would have needed LLM judgment for manual review instead of failing.

## Architecture

**Pattern: one Orchestrator + deterministic workers, LLM only for narrow judgment calls.** This is a hard constraint from the build spec — do not turn this into a single mega-prompt or a free-roaming agent. All arithmetic, matching, and export logic is deterministic Python; the LLM (Claude, via forced tool-use) is invoked in exactly three places, each wrapped in try/except so an LLM failure degrades to a "needs attention" flag rather than aborting the run:

1. `field_locator` — maps unrecognized bank statement column layouts (`llm/client.py:locate_columns`, called from `agents/extraction_agent.py`).
2. `adjudicator` — resolves ambiguous self-transfer/refund matches that deterministic matching left ambiguous (`llm/client.py:adjudicate_match`, called from `agents/reconciliation_agent.py`).
3. `classifier` — suggests an ITR head for a grouped income counterparty that keyword rules didn't resolve (`llm/client.py:classify_income`, called from `agents/classification_agent.py`).

`llm/client.py`'s `ClaudeToolClient.call_tool` forces every call to return a single `tool_use` block via `tool_choice`, so responses are always structured — never free text to parse. Every call records usage on the shared `CostMeter` (`cost_meter.py`, FR-12): one instance per run, shared across all LLM calls, exposes `.summary()` for the UI sidebar and a threshold callback for a cost-warning banner.

### Pipeline (driven by `agents/orchestrator.py::Orchestrator.run`)

```
per file: extraction_agent.process_file  (pdfplumber/OCR parse; field_locator only if layout unrecognized)
       -> reconciliation_agent.reconcile  (transfer_matcher + refund_matcher; adjudicator only for leftovers)
       -> classification_agent.classify   (keyword tagging first; classifier only for unresolved counterparties)
       -> calculators/calculator.py       (totals, balance continuity — FR-6, never LLM math)
       -> RunResult (run_context.py)
       -> export_agent.export             (xlsxwriter workbook + Tally CSV; deterministic, no LLM)
```

`Transaction` and `StatementMeta` (`parsing/models.py`) are the core dataclasses threaded through every stage — fields like `tags`, `category`, `itr_head` are additive, filled in by later stages on the same object. Every `Transaction` carries a `ref` property (`serial · bank · pdf`, via `utils/references.py`) so every number in the final workbook traces back to a source line (P0 traceability requirement).

### PII masking is P0 / non-negotiable

Raw PII (account number, PAN, name, mobile, email) must never reach the Anthropic API. `pii/masker.py`'s `PIIVault` tokenizes PII into opaque reversible tokens (e.g. `[PAN-1]`); only the local vault can reverse a token, and only `export/reference_hydrator.py` does so, at export time, never over the network. Every agent module masks narration text (`mask_narration`) before building any LLM payload — when adding a new LLM call site, mask first. `logging_config.py` follows the same rule: it only ever logs stage names/file names/token counts/exceptions, never transaction narration or account data.

### Module map (`src/bank_statement_analyzer/`)

- `config.py` — all tunable thresholds (matching windows, anomaly thresholds, cost pricing), fixed ITR-head/expense-category taxonomies, and keyword banks used by deterministic classification heuristics. Tune knobs here rather than scattering magic numbers through matcher/classifier code.
- `parsing/` — `pdf_parser.py` (pdfplumber + OCR fallback), `ocr.py`, `models.py` (`Transaction`, `StatementMeta`, `ParseResult`).
- `pii/masker.py` — see above.
- `reconciliation/` — `transfer_matcher.py` (self-transfers) and `refund_matcher.py` (FR-3a), both tiered: exact reference-number match -> amount+date-window+narration-similarity fallback -> ambiguous leftovers punted to the adjudicator. `common.py` holds shared `MatchedPair`/`UnmatchedCandidate`/`AmbiguousGroup` types.
- `classification/` — `classifier.py` (keyword-based tagging, recurring-payment detection, anomaly detection), `categories.py`.
- `calculators/calculator.py` — all sums, per-account balance-continuity checks, income/expense grouping. This is the only place arithmetic happens.
- `export/` — `xlsx_export.py` (multi-sheet workbook per the spec's sheet layout), `csv_export.py` (Tally-friendly), `reference_hydrator.py` (the only place PII tokens are reversed, and only locally).
- `llm/` — `client.py` (`ClaudeToolClient`), `tools_schema.py` (tool-use JSON schemas for the three tools above).
- `agents/` — `orchestrator.py` plus one module per pipeline stage (`extraction_agent.py`, `reconciliation_agent.py`, `classification_agent.py`, `export_agent.py`). Thin glue: deterministic-first, LLM-fallback-only, always masking before any LLM call.
- `ui/` — PySide6. `main_window.py` (main flow: add PDFs -> client name -> API key -> optional review toggle (FR-11) -> run on a `QThread` worker -> needs-attention review -> export), `cost_sidebar.py` (live token/cost display), `review_grid.py`, `needs_attention_view.py`, `collapsible_box.py`.
- `run_context.py` — `RunResult`, the single aggregate object the export layer consumes.
- `cost_meter.py`, `secrets_store.py` (OS keyring wrapper — Windows Credential Manager / macOS Keychain / Linux Secret Service), `logging_config.py` (rotating log file under the OS app-data dir).

### Resilience conventions

One bad file, one bad LLM call, or one unresolved match must never abort the whole run (P1 resilience) — it gets excluded/flagged into `needs_attention` on `RunResult` instead. Follow this pattern (broad `except Exception` with a comment justifying it, e.g. `# noqa: BLE001`) when adding new per-file or per-call logic in the agents.
