"""The Orchestrator (architecture step 1).

Plans the run, dispatches per-statement extraction, collects structured
results, triggers reconciliation/classification, and assembles the
`RunResult` that `export_agent` turns into the workbook. Works only on
masked/structured data — the extraction/reconciliation/classification
agents are the only things that ever build an LLM payload, and they
always mask first (`pii.masker`). Tracks cumulative cost via the shared
`CostMeter` (FR-12).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from bank_statement_analyzer.agents import classification_agent, export_agent, extraction_agent, reconciliation_agent
from bank_statement_analyzer.calculators.calculator import (
    all_account_continuity,
    expense_by_category,
    group_income_by_counterparty,
    run_totals,
)
from bank_statement_analyzer.config import AppConfig, DEFAULT_CONFIG
from bank_statement_analyzer.cost_meter import CostMeter
from bank_statement_analyzer.llm.client import ClaudeToolClient
from bank_statement_analyzer.parsing.models import StatementMeta, Transaction
from bank_statement_analyzer.pii.masker import PIIVault
from bank_statement_analyzer.run_context import NeedsAttentionItem, RunResult

ProgressCallback = Callable[[str], None]


@dataclass
class FileInput:
    path: str
    bank: str | None = None      # None -> guessed from document text
    password: str | None = None  # for password-protected PDFs (FR-1)


def _ref_to_file(ref: str | None, transactions_by_ref: dict[str, Transaction]) -> str:
    if ref and ref in transactions_by_ref:
        return transactions_by_ref[ref].pdf
    return ""


class Orchestrator:
    def __init__(self, config: AppConfig | None = None, cost_meter: CostMeter | None = None):
        self.config = config or DEFAULT_CONFIG
        self.cost_meter = cost_meter or CostMeter(cfg=self.config.cost)
        self.vault = PIIVault()

    def _make_llm_client(self, api_key: str | None) -> ClaudeToolClient | None:
        if not api_key:
            return None
        return ClaudeToolClient(api_key=api_key, cost_meter=self.cost_meter, model=self.config.model_name)

    def run(
        self,
        files: list[FileInput],
        client_label: str,
        api_key: str | None,
        progress_callback: ProgressCallback | None = None,
    ) -> RunResult:
        llm_client = self._make_llm_client(api_key)
        notify = progress_callback or (lambda _msg: None)

        statement_metas: dict[str, StatementMeta] = {}
        all_transactions: list[Transaction] = []
        needs_attention: list[NeedsAttentionItem] = []

        # --- Step 2: per-statement extraction (one bad file flags, doesn't abort) ---
        for i, file in enumerate(files):
            account_ref = f"ACC-{i + 1}"
            notify(f"Parsing {file.path}...")
            try:
                result = extraction_agent.process_file(
                    file.path, file.bank, account_ref, file.password, llm_client, self.vault,
                )
            except Exception as e:  # noqa: BLE001 - P1 resilience: bad file must not abort the run
                meta = StatementMeta(bank=file.bank or "", pdf=file.path.rsplit("/", 1)[-1], account_ref=account_ref)
                meta.flagged = True
                meta.flag_reason = f"Unhandled parse error: {e}"
                statement_metas[account_ref] = meta
                needs_attention.append(NeedsAttentionItem(
                    issue=meta.flag_reason, file=meta.pdf, ref=None,
                    suggestion="Re-check the file manually; it was excluded from this run.",
                ))
                continue

            statement_metas[account_ref] = result.meta
            if result.meta.flagged:
                needs_attention.append(NeedsAttentionItem(
                    issue=result.meta.flag_reason or "Flagged during extraction",
                    file=result.meta.pdf, ref=None,
                    suggestion="Resolve manually; excluded from matching until fixed.",
                ))
            if not result.success:
                continue  # FR-2: excluded from downstream matching until resolved
            all_transactions.extend(result.transactions)

        transactions_by_ref = {t.ref: t for t in all_transactions}

        # --- Step 3: reconciliation ---
        notify("Reconciling self-transfers and refunds...")
        self_transfers, refunds = reconciliation_agent.reconcile(all_transactions, llm_client, self.vault)
        excluded_refs = {
            ref for pair in (*self_transfers.confirmed, *refunds.confirmed)
            for ref in (pair.leg_a_ref, pair.leg_b_ref)
        }

        for cand in self_transfers.unmatched:
            needs_attention.append(NeedsAttentionItem(
                issue="Unmatched self-transfer candidate",
                file=_ref_to_file(cand.ref, transactions_by_ref), ref=cand.ref,
                suggestion=cand.reason,
            ))
        for cand in refunds.unmatched:
            needs_attention.append(NeedsAttentionItem(
                issue="Unmatched refund candidate",
                file=_ref_to_file(cand.ref, transactions_by_ref), ref=cand.ref,
                suggestion=cand.reason,
            ))

        # --- Step 4: classification ---
        notify("Classifying income, expenses, and anomalies...")
        recurring, anomalies = classification_agent.classify(
            all_transactions, excluded_refs, llm_client, self.vault,
        )

        for t in all_transactions:
            if "loan_disbursal_candidate" in t.tags:
                needs_attention.append(NeedsAttentionItem(
                    issue="Possible loan disbursal (excluded from income)",
                    file=t.pdf, ref=t.ref, suggestion="Confirm this is a loan disbursal, not taxable income.",
                ))
            if "gift_candidate" in t.tags:
                needs_attention.append(NeedsAttentionItem(
                    issue="Possible gift (excluded from income)",
                    file=t.pdf, ref=t.ref, suggestion="Confirm this is a gift, not taxable income.",
                ))
            if t.itr_head_confidence == "low":
                needs_attention.append(NeedsAttentionItem(
                    issue="Low-confidence ITR head suggestion",
                    file=t.pdf, ref=t.ref,
                    suggestion=f"Suggested head: {t.itr_head}. Please confirm.",
                ))
        for flag in anomalies:
            needs_attention.append(NeedsAttentionItem(
                issue=f"Anomaly: {flag.kind}",
                file=_ref_to_file(flag.ref, transactions_by_ref), ref=flag.ref,
                suggestion=flag.detail,
            ))

        # --- Step 6 prep: deterministic math (FR-6) ---
        notify("Running final calculations...")
        continuity = all_account_continuity(all_transactions, statement_metas)
        for c in continuity:
            if not c.ok:
                needs_attention.append(NeedsAttentionItem(
                    issue="Balance continuity mismatch",
                    file=statement_metas[c.account_ref].pdf if c.account_ref in statement_metas else "",
                    ref=None,
                    suggestion=f"Opening {c.opening_balance} + credits {c.total_credits} - debits {c.total_debits} = {c.computed_closing}, expected {c.closing_balance} (diff {c.diff}).",
                ))

        non_excluded = [t for t in all_transactions if t.ref not in excluded_refs]
        credits = [t for t in non_excluded if t.is_credit]
        debits = [t for t in non_excluded if t.is_debit]
        income_groups = group_income_by_counterparty(credits)
        expense_categories = expense_by_category(debits)
        cc_payment_refs = [t.ref for t in debits if "cc_payment_or_fee" in t.tags]
        totals = run_totals(non_excluded)

        return RunResult(
            client_label=client_label,
            statement_metas=statement_metas,
            transactions=all_transactions,
            self_transfers=self_transfers,
            refunds=refunds,
            income_groups=income_groups,
            expense_categories=expense_categories,
            cc_payment_refs=cc_payment_refs,
            recurring=recurring,
            anomalies=anomalies,
            needs_attention=needs_attention,
            run_totals=totals,
            continuity=continuity,
            cost_summary=self.cost_meter.summary(),
        )

    def export(self, run: RunResult, xlsx_path: str, csv_path: str, review_edits: list[dict] | None = None) -> None:
        run.cost_summary = self.cost_meter.summary()
        export_agent.export(run, self.vault, xlsx_path, csv_path, review_edits=review_edits)
