"""Deterministic refund/reversal reconciliation.

Extension to the PRD's self-transfer reconciliation: refunds (merchant
refunds, failed-transaction reversals, cancelled-order credits,
chargebacks) are identified and, where possible, paired back to the
original debit — exactly the same "confirmed pair / unmatched candidate"
shape as `transfer_matcher`, and surfaced on their own "Refunds" sheet.

Refunds must never be counted as income (FR-4 already says refunds are
excluded from income like self-transfers), but unlike self-transfers the
originating leg and the refund leg are usually on the *same* account, so
matching is keyed on same-account-first rather than cross-account-only.

Matching order, mirroring `transfer_matcher`:

1. UPI RRN / reference number equality with a prior debit (high confidence)
   — some banks echo the original transaction's RRN in the reversal
   narration.
2. Amount + a (longer, configurable) date window + narration similarity,
   preferring a same-account original debit (medium confidence) over a
   cross-account one (low confidence).
3. A refund-keyword credit with no plausible original debit is an
   "orphan" refund: still excluded from income and still flagged on
   Needs Attention, just with no paired leg.

Pure deterministic Python — no LLM involvement. Ambiguous leftovers
(multiple equally-plausible original debits) are handed to the
`adjudicator` LLM step by the reconciliation agent, same as transfers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bank_statement_analyzer.config import MatchingConfig, REFUND_KEYWORDS, DEFAULT_CONFIG
from bank_statement_analyzer.parsing.models import Transaction
from bank_statement_analyzer.reconciliation.common import (
    AmbiguousGroup,
    MatchedPair,
    UnmatchedCandidate,
    counterparty_bonus,
    days_between,
    extract_rrn,
    narration_similarity,
)


@dataclass
class RefundResult:
    confirmed: list[MatchedPair] = field(default_factory=list)
    unmatched: list[UnmatchedCandidate] = field(default_factory=list)
    ambiguous: list[AmbiguousGroup] = field(default_factory=list)


def is_refund_like(t: Transaction) -> bool:
    if not t.is_credit:
        return False
    desc = (t.description or "").lower()
    return any(kw in desc for kw in REFUND_KEYWORDS)


def match_refunds(
    transactions: list[Transaction],
    cfg: MatchingConfig | None = None,
) -> RefundResult:
    cfg = cfg or DEFAULT_CONFIG.matching
    result = RefundResult()

    refund_credits = [t for t in transactions if is_refund_like(t)]
    all_debits = [t for t in transactions if t.is_debit]

    matched_refs: set[str] = set()

    # --- Tier 1: RRN match to an earlier debit ------------------------------
    debits_by_rrn: dict[str, list[Transaction]] = {}
    for d in all_debits:
        rrn = extract_rrn(d.description)
        if rrn:
            debits_by_rrn.setdefault(rrn, []).append(d)

    remaining_refunds: list[Transaction] = []
    for r in refund_credits:
        rrn = extract_rrn(r.description)
        d_legs = debits_by_rrn.get(rrn) if rrn else None
        if not d_legs:
            remaining_refunds.append(r)
            continue
        d_legs = [d for d in d_legs if d.ref not in matched_refs and d.txn_date <= r.txn_date]
        if len(d_legs) == 1:
            d = d_legs[0]
            result.confirmed.append(MatchedPair(
                leg_a_ref=d.ref, leg_b_ref=r.ref, amount=r.amount,
                date_diff_days=days_between(d.txn_date, r.txn_date),
                method="rrn", confidence="high",
            ))
            matched_refs.add(d.ref)
            matched_refs.add(r.ref)
        elif len(d_legs) > 1:
            result.ambiguous.append(AmbiguousGroup(
                anchor_ref=r.ref,
                candidate_refs=[d.ref for d in d_legs],
                kind="refund",
                field_context={"rrn": rrn},
            ))
        else:
            remaining_refunds.append(r)

    # --- Tier 2: amount + date-window + narration fallback ------------------
    for r in remaining_refunds:
        if r.ref in matched_refs:
            continue
        scored = []
        for d in all_debits:
            if d.ref in matched_refs or d.txn_date > r.txn_date:
                continue
            if abs(d.amount - r.amount) >= 0.01:
                continue
            if days_between(d.txn_date, r.txn_date) > cfg.refund_date_window_days:
                continue
            score = narration_similarity(d.description, r.description) + counterparty_bonus(d.description, r.description)
            same_account = d.account_ref == r.account_ref
            if same_account:
                score += 0.2
            scored.append((score, same_account, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        good = [(s, sa, d) for s, sa, d in scored if s >= cfg.refund_narration_similarity_min]

        if len(good) == 1 or (len(good) > 1 and good[0][0] - good[1][0] > 0.15):
            score, same_account, d = good[0]
            result.confirmed.append(MatchedPair(
                leg_a_ref=d.ref, leg_b_ref=r.ref, amount=r.amount,
                date_diff_days=days_between(d.txn_date, r.txn_date),
                method="amount_date_narration",
                confidence="medium" if same_account else "low",
            ))
            matched_refs.add(d.ref)
            matched_refs.add(r.ref)
        elif len(good) > 1:
            result.ambiguous.append(AmbiguousGroup(
                anchor_ref=r.ref,
                candidate_refs=[d.ref for _, _, d in good],
                kind="refund",
                field_context={"method": "amount_date_narration"},
            ))
        else:
            result.unmatched.append(UnmatchedCandidate(
                ref=r.ref, amount=r.amount, txn_date=r.txn_date,
                reason="refund/reversal narration with no matching original debit found",
                kind="refund",
            ))

    return result
