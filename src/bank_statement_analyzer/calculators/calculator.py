"""Deterministic arithmetic — the only place sums/totals/reconciliation
math may happen. The LLM never computes a number; it only classifies,
locates fields, or adjudicates ambiguous matches. Every function here is
pure and independently testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from bank_statement_analyzer.parsing.models import StatementMeta, Transaction

TOLERANCE = 0.01


def to_dataframe(transactions: list[Transaction]) -> pd.DataFrame:
    return pd.DataFrame([{
        "ref": t.ref,
        "serial": t.serial,
        "bank": t.bank,
        "pdf": t.pdf,
        "account_ref": t.account_ref,
        "date": t.txn_date,
        "description": t.description,
        "debit": t.debit,
        "credit": t.credit,
        "balance": t.balance,
        "tags": ",".join(sorted(t.tags)) if t.tags else "",
        "category": t.category,
        "itr_head": t.itr_head,
        "itr_head_confidence": t.itr_head_confidence,
        "counterparty": t.counterparty,
    } for t in transactions])


@dataclass
class ContinuityResult:
    account_ref: str
    opening_balance: float
    closing_balance: float
    total_debits: float
    total_credits: float
    computed_closing: float
    diff: float
    ok: bool


def balance_continuity(
    transactions: list[Transaction],
    opening_balance: float,
    closing_balance: float,
    account_ref: str = "",
    tolerance: float = TOLERANCE,
) -> ContinuityResult:
    total_debits = round(sum(t.debit for t in transactions), 2)
    total_credits = round(sum(t.credit for t in transactions), 2)
    computed_closing = round(opening_balance + total_credits - total_debits, 2)
    diff = round(computed_closing - closing_balance, 2)
    return ContinuityResult(
        account_ref=account_ref,
        opening_balance=round(opening_balance, 2),
        closing_balance=round(closing_balance, 2),
        total_debits=total_debits,
        total_credits=total_credits,
        computed_closing=computed_closing,
        diff=diff,
        ok=abs(diff) <= tolerance,
    )


def all_account_continuity(
    transactions: list[Transaction],
    statement_metas: dict[str, StatementMeta],
) -> list[ContinuityResult]:
    results = []
    by_account: dict[str, list[Transaction]] = {}
    for t in transactions:
        by_account.setdefault(t.account_ref, []).append(t)
    for account_ref, meta in statement_metas.items():
        txns = by_account.get(account_ref, [])
        if meta.opening_balance is None or meta.closing_balance is None:
            continue
        results.append(balance_continuity(
            txns, meta.opening_balance, meta.closing_balance, account_ref,
        ))
    return results


@dataclass
class CounterpartyTotal:
    counterparty: str
    total: float
    frequency: int
    dates: list[str]
    refs: list[str]
    itr_head: str | None
    itr_head_confidence: str | None


def group_income_by_counterparty(credit_transactions: list[Transaction]) -> list[CounterpartyTotal]:
    groups: dict[str, list[Transaction]] = {}
    for t in credit_transactions:
        key = t.counterparty or t.description.strip().upper()
        groups.setdefault(key, []).append(t)
    out = []
    for key, txns in groups.items():
        total = round(sum(t.credit for t in txns), 2)
        heads = [t.itr_head for t in txns if t.itr_head]
        head = max(set(heads), key=heads.count) if heads else None
        confidences = [t.itr_head_confidence for t in txns if t.itr_head_confidence]
        confidence = max(set(confidences), key=confidences.count) if confidences else None
        out.append(CounterpartyTotal(
            counterparty=key,
            total=total,
            frequency=len(txns),
            dates=sorted(str(t.txn_date) for t in txns),
            refs=[t.ref for t in txns],
            itr_head=head,
            itr_head_confidence=confidence,
        ))
    out.sort(key=lambda c: c.total, reverse=True)
    return out


@dataclass
class CategoryTotal:
    category: str
    total: float
    count: int
    refs: list[str]


def expense_by_category(debit_transactions: list[Transaction]) -> list[CategoryTotal]:
    groups: dict[str, list[Transaction]] = {}
    for t in debit_transactions:
        key = t.category or "Other"
        groups.setdefault(key, []).append(t)
    out = [
        CategoryTotal(
            category=key,
            total=round(sum(t.debit for t in txns), 2),
            count=len(txns),
            refs=[t.ref for t in txns],
        )
        for key, txns in groups.items()
    ]
    out.sort(key=lambda c: c.total, reverse=True)
    return out


@dataclass
class RunTotals:
    total_credits: float
    total_debits: float
    net: float
    txn_count: int


def run_totals(transactions: list[Transaction]) -> RunTotals:
    total_credits = round(sum(t.credit for t in transactions), 2)
    total_debits = round(sum(t.debit for t in transactions), 2)
    return RunTotals(
        total_credits=total_credits,
        total_debits=total_debits,
        net=round(total_credits - total_debits, 2),
        txn_count=len(transactions),
    )


def sum_amounts(refs: list[str], transactions_by_ref: dict[str, Transaction]) -> float:
    return round(sum(transactions_by_ref[r].amount for r in refs if r in transactions_by_ref), 2)
