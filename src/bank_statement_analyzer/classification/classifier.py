"""Deterministic classification pass (FR-4, FR-5, FR-7, FR-8, FR-9).

Runs after reconciliation, over every transaction that is *not* part of
a confirmed self-transfer or refund pair (those are excluded from both
income and expense — they aren't real inflow/outflow, they're the same
money moving or being returned). Tags what it can from keywords alone;
anything left with `itr_head is None` on a credit is what
`classification_agent` sends to the `classifier` LLM tool for a
suggestion, since the counterparty is unrecognized.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from bank_statement_analyzer.config import MatchingConfig, DEFAULT_CONFIG
from bank_statement_analyzer.classification.categories import (
    classify_expense_category,
    extract_counterparty,
    is_26as_likely,
    is_cc_payment_or_fee,
    is_gift,
    is_interest_credit,
    is_loan_disbursal,
    recurring_type_hint,
)
from bank_statement_analyzer.parsing.models import Transaction


def tag_transactions(transactions: list[Transaction], excluded_refs: set[str]) -> None:
    """Mutates transactions in place with category / itr_head / tags."""
    for t in transactions:
        if t.ref in excluded_refs:
            continue
        if t.is_debit:
            t.category = classify_expense_category(t.description)
            if is_cc_payment_or_fee(t.description):
                t.tags.add("cc_payment_or_fee")
        elif t.is_credit:
            t.counterparty = extract_counterparty(t.description)
            if is_interest_credit(t.description):
                t.itr_head = "Interest"
                t.itr_head_confidence = "high"
                t.tags.add("interest")
            elif is_loan_disbursal(t.description):
                t.tags.add("loan_disbursal_candidate")
            elif is_gift(t.description):
                t.tags.add("gift_candidate")
            if is_26as_likely(t.itr_head):
                t.tags.add("26AS-likely")


@dataclass
class RecurringGroup:
    counterparty: str
    amount: float
    kind: str  # "EMI" | "SIP" | "Rent" | "Subscription" | "Recurring"
    refs: list[str]
    avg_period_days: float


def _is_regular(dates: list[date], tolerance_days: int) -> bool:
    if len(dates) < 2:
        return False
    dates = sorted(dates)
    diffs = [(b - a).days for a, b in zip(dates, dates[1:])]
    median_diff = statistics.median(diffs)
    return all(abs(d - median_diff) <= tolerance_days for d in diffs)


def detect_recurring(
    transactions: list[Transaction],
    excluded_refs: set[str],
    cfg: MatchingConfig | None = None,
) -> list[RecurringGroup]:
    cfg = cfg or DEFAULT_CONFIG.matching
    debits = [t for t in transactions if t.is_debit and t.ref not in excluded_refs]
    groups: dict[tuple[str, float], list[Transaction]] = {}
    for t in debits:
        key = (extract_counterparty(t.description), round(t.debit, 2))
        groups.setdefault(key, []).append(t)

    out = []
    for (counterparty, amount), txns in groups.items():
        if len(txns) < cfg.recurring_min_occurrences:
            continue
        dates = [t.txn_date for t in txns]
        if not _is_regular(dates, cfg.recurring_period_tolerance_days):
            continue
        dates_sorted = sorted(dates)
        diffs = [(b - a).days for a, b in zip(dates_sorted, dates_sorted[1:])]
        kind = recurring_type_hint(txns[0].description) or "Recurring"
        refs = [t.ref for t in txns]
        for t in txns:
            t.tags.add(f"recurring:{kind.lower()}")
        out.append(RecurringGroup(
            counterparty=counterparty, amount=amount, kind=kind,
            refs=refs, avg_period_days=round(statistics.mean(diffs), 1),
        ))
    return out


@dataclass
class AnomalyFlag:
    ref: str
    kind: str   # "large_credit" | "large_cash_deposit" | "round_trip"
    detail: str


def detect_anomalies(
    transactions: list[Transaction],
    excluded_refs: set[str],
    cfg: MatchingConfig | None = None,
) -> list[AnomalyFlag]:
    cfg = cfg or DEFAULT_CONFIG.matching
    flags: list[AnomalyFlag] = []

    credits = [t for t in transactions if t.is_credit and t.ref not in excluded_refs]
    if credits:
        median_credit = statistics.median(t.credit for t in credits)
        large_threshold = max(cfg.large_credit_floor, median_credit * cfg.large_credit_multiple_of_median)
        for t in credits:
            if t.credit >= large_threshold:
                flags.append(AnomalyFlag(t.ref, "large_credit", f"credit of {t.credit} exceeds threshold {large_threshold:.2f}"))
                t.tags.add("anomaly:large_credit")

    for t in credits:
        if "cash" in t.description.lower() and t.credit >= cfg.large_cash_deposit_threshold:
            flags.append(AnomalyFlag(t.ref, "large_cash_deposit", f"cash deposit of {t.credit} exceeds threshold {cfg.large_cash_deposit_threshold}"))
            t.tags.add("anomaly:large_cash_deposit")

    # Same-day (or near-day) round-tripping: opposite-direction legs of
    # equal amount on the same account that reconciliation did NOT already
    # confirm as a self-transfer/refund.
    non_excluded = [t for t in transactions if t.ref not in excluded_refs]
    by_account: dict[str, list[Transaction]] = {}
    for t in non_excluded:
        by_account.setdefault(t.account_ref, []).append(t)

    for account_ref, txns in by_account.items():
        debits = [t for t in txns if t.is_debit]
        credits_ = [t for t in txns if t.is_credit]
        used: set[str] = set()
        for d in debits:
            for c in credits_:
                if c.ref in used:
                    continue
                if abs(d.amount - c.amount) < 0.01 and abs((d.txn_date - c.txn_date).days) <= cfg.round_trip_window_days:
                    flags.append(AnomalyFlag(d.ref, "round_trip", f"same-day round-trip with {c.ref}, amount {d.amount}"))
                    flags.append(AnomalyFlag(c.ref, "round_trip", f"same-day round-trip with {d.ref}, amount {c.amount}"))
                    d.tags.add("anomaly:round_trip")
                    c.tags.add("anomaly:round_trip")
                    used.add(c.ref)
                    break

    return flags
