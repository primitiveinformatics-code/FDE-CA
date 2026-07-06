"""Local-only re-hydration of any PII tokens before final export.

`Transaction`/`StatementMeta` objects carry raw data throughout the
pipeline; only the *copies* handed to the LLM (via `pii.masker`) are
tokenized. If an LLM response ever gets stored back onto a transaction
field (e.g. a suggested counterparty label derived from masked input),
this pass guarantees any leftover `[KIND-n]` token is resolved back to
its real value before a human ever sees the workbook. This never touches
the network — it runs immediately before `xlsx_export`/`csv_export`.
"""
from __future__ import annotations

from bank_statement_analyzer.parsing.models import Transaction
from bank_statement_analyzer.pii.masker import PIIVault


def hydrate_transactions(vault: PIIVault, transactions: list[Transaction]) -> None:
    for t in transactions:
        t.description = vault.rehydrate(t.description)
        if t.counterparty:
            t.counterparty = vault.rehydrate(t.counterparty)
