"""Aggregate result of one full run — the single object the export layer
consumes. Built up by the orchestrator/agents, consumed by
`export.xlsx_export` and `export.csv_export`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bank_statement_analyzer.calculators.calculator import (
    CategoryTotal,
    ContinuityResult,
    CounterpartyTotal,
    RunTotals,
)
from bank_statement_analyzer.classification.classifier import AnomalyFlag, RecurringGroup
from bank_statement_analyzer.parsing.models import StatementMeta, Transaction
from bank_statement_analyzer.reconciliation.common import MatchedPair, UnmatchedCandidate


@dataclass
class NeedsAttentionItem:
    issue: str
    file: str
    ref: str | None
    suggestion: str


@dataclass
class ReconciliationResult:
    confirmed: list[MatchedPair] = field(default_factory=list)
    unmatched: list[UnmatchedCandidate] = field(default_factory=list)


@dataclass
class RunResult:
    client_label: str
    statement_metas: dict[str, StatementMeta] = field(default_factory=dict)
    transactions: list[Transaction] = field(default_factory=list)

    self_transfers: ReconciliationResult = field(default_factory=ReconciliationResult)
    refunds: ReconciliationResult = field(default_factory=ReconciliationResult)

    income_groups: list[CounterpartyTotal] = field(default_factory=list)
    expense_categories: list[CategoryTotal] = field(default_factory=list)
    cc_payment_refs: list[str] = field(default_factory=list)
    recurring: list[RecurringGroup] = field(default_factory=list)
    anomalies: list[AnomalyFlag] = field(default_factory=list)
    needs_attention: list[NeedsAttentionItem] = field(default_factory=list)

    run_totals: RunTotals | None = None
    continuity: list[ContinuityResult] = field(default_factory=list)

    cost_summary: dict = field(default_factory=dict)

    def transactions_by_ref(self) -> dict[str, Transaction]:
        return {t.ref: t for t in self.transactions}

    def excluded_refs(self) -> set[str]:
        """Refs that are confirmed self-transfer or refund legs — excluded
        from income/expense classification since they aren't real
        inflow/outflow."""
        refs: set[str] = set()
        for pair in (*self.self_transfers.confirmed, *self.refunds.confirmed):
            refs.add(pair.leg_a_ref)
            refs.add(pair.leg_b_ref)
        return refs
