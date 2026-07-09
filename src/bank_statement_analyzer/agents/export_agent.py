"""Export agent (architecture step 5) — deterministic, no LLM.

Re-hydrates PII locally, applies any CA review edits (FR-11), runs all
final sums through `calculator`, then writes the Excel workbook + Tally
CSV via `xlsxwriter`/`csv_export`.
"""
from __future__ import annotations

import logging

from bank_statement_analyzer.calculators.calculator import expense_by_category, group_income_by_counterparty
from bank_statement_analyzer.export.csv_export import export_csv
from bank_statement_analyzer.export.reference_hydrator import hydrate_transactions
from bank_statement_analyzer.export.xlsx_export import export_workbook
from bank_statement_analyzer.pii.masker import PIIVault
from bank_statement_analyzer.run_context import RunResult

logger = logging.getLogger(__name__)


def apply_review_edits(run: RunResult, edits: list[dict]) -> None:
    """Apply CA edits collected from the FR-11 review grid.

    Each edit: {"ref": str, "field": "itr_head" | "category", "value": str}.

    The "Income sources"/"Expenditure summary" sheets are pre-aggregated
    (`run.income_groups`/`run.expense_categories`), so an edit to the
    underlying transaction has no effect on the workbook unless those
    summaries are rebuilt from the (now-edited) transactions afterwards.
    """
    by_ref = run.transactions_by_ref()
    for edit in edits:
        t = by_ref.get(edit.get("ref"))
        if t is None:
            continue
        field = edit.get("field")
        if field == "itr_head":
            t.itr_head = edit.get("value")
            t.itr_head_confidence = "confirmed"
        elif field == "category":
            t.category = edit.get("value")

    excluded_refs = run.excluded_refs()
    credits = [t for t in run.transactions if t.is_credit and t.ref not in excluded_refs]
    debits = [t for t in run.transactions if t.is_debit and t.ref not in excluded_refs]
    run.income_groups = group_income_by_counterparty(credits)
    run.expense_categories = expense_by_category(debits)


def export(
    run: RunResult,
    vault: PIIVault,
    xlsx_path: str,
    csv_path: str,
    review_edits: list[dict] | None = None,
) -> None:
    if review_edits:
        logger.info("Applying %d review edit(s) before export", len(review_edits))
        apply_review_edits(run, review_edits)
    hydrate_transactions(vault, run.transactions)
    logger.info(
        "Writing workbook: %s (%d transaction(s), %d income group(s), %d expense categor(y/ies), %d needs-attention item(s))",
        xlsx_path, len(run.transactions), len(run.income_groups), len(run.expense_categories), len(run.needs_attention),
    )
    export_workbook(run, xlsx_path)
    logger.info("Writing CSV: %s", csv_path)
    export_csv(run, csv_path)
    logger.info("Export complete: %s, %s", xlsx_path, csv_path)
