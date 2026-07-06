"""Deterministic self-transfer reconciliation (FR-3).

Any statement uploaded in the same run is treated as belonging to the
same client, so a debit in one uploaded account paired with a credit in
another uploaded account is a self-transfer candidate. Matching order:

1. UPI RRN / reference number equality (high confidence).
2. Amount + date-window + narration similarity, boosted when both legs
   share a UPI VPA (low confidence — always flagged as such per spec).
3. Anything left in the candidate universe that couldn't be paired is
   surfaced as an explicit unmatched candidate; RRN collisions with more
   than one plausible leg on either side are surfaced as ambiguous for
   the `adjudicator` LLM step.

No LLM calls happen in this module — it is pure, deterministic Python
over already-extracted, already-masked-or-not (masking is irrelevant
here since this never touches the network) transaction data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bank_statement_analyzer.config import MatchingConfig, TRANSFER_MODE_KEYWORDS, DEFAULT_CONFIG
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
class SelfTransferResult:
    confirmed: list[MatchedPair] = field(default_factory=list)
    unmatched: list[UnmatchedCandidate] = field(default_factory=list)
    ambiguous: list[AmbiguousGroup] = field(default_factory=list)


def _is_transfer_like(t: Transaction) -> bool:
    desc = (t.description or "").lower()
    if extract_rrn(t.description):
        return True
    return any(kw in desc for kw in TRANSFER_MODE_KEYWORDS)


def match_self_transfers(
    transactions: list[Transaction],
    cfg: MatchingConfig | None = None,
) -> SelfTransferResult:
    cfg = cfg or DEFAULT_CONFIG.matching
    result = SelfTransferResult()

    candidates = [t for t in transactions if _is_transfer_like(t)]
    debits = [t for t in candidates if t.is_debit]
    credits = [t for t in candidates if t.is_credit]

    matched_refs: set[str] = set()

    # --- Tier 1: RRN match -------------------------------------------------
    rrn_debits: dict[str, list[Transaction]] = {}
    rrn_credits: dict[str, list[Transaction]] = {}
    for t in debits:
        rrn = extract_rrn(t.description)
        if rrn:
            rrn_debits.setdefault(rrn, []).append(t)
    for t in credits:
        rrn = extract_rrn(t.description)
        if rrn:
            rrn_credits.setdefault(rrn, []).append(t)

    for rrn, d_legs in rrn_debits.items():
        c_legs = rrn_credits.get(rrn)
        if not c_legs:
            continue
        d_legs = [d for d in d_legs if d.account_ref not in {c.account_ref for c in c_legs}]
        if len(d_legs) == 1 and len(c_legs) == 1:
            d, c = d_legs[0], c_legs[0]
            if abs(d.amount - c.amount) < 0.01:
                result.confirmed.append(MatchedPair(
                    leg_a_ref=d.ref, leg_b_ref=c.ref, amount=d.amount,
                    date_diff_days=days_between(d.txn_date, c.txn_date),
                    method="rrn", confidence="high",
                ))
                matched_refs.add(d.ref)
                matched_refs.add(c.ref)
        elif len(d_legs) >= 1 and len(c_legs) >= 1:
            result.ambiguous.append(AmbiguousGroup(
                anchor_ref=d_legs[0].ref,
                candidate_refs=[c.ref for c in c_legs],
                kind="self_transfer",
                field_context={"rrn": rrn},
            ))

    # --- Tier 2: amount + date-window + narration fallback ----------------
    remaining_debits = [t for t in debits if t.ref not in matched_refs]
    remaining_credits = [t for t in credits if t.ref not in matched_refs]

    for d in remaining_debits:
        if d.ref in matched_refs:
            continue
        scored = []
        for c in remaining_credits:
            if c.ref in matched_refs or c.account_ref == d.account_ref:
                continue
            if abs(d.amount - c.amount) >= 0.01:
                continue
            if days_between(d.txn_date, c.txn_date) > cfg.transfer_date_window_days:
                continue
            score = narration_similarity(d.description, c.description) + counterparty_bonus(d.description, c.description)
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        good = [c for score, c in scored if score >= cfg.transfer_narration_similarity_min]
        if len(good) == 1:
            c = good[0]
            result.confirmed.append(MatchedPair(
                leg_a_ref=d.ref, leg_b_ref=c.ref, amount=d.amount,
                date_diff_days=days_between(d.txn_date, c.txn_date),
                method="amount_date_narration", confidence="low",
            ))
            matched_refs.add(d.ref)
            matched_refs.add(c.ref)
        elif len(good) > 1 and (good[0] is scored[0][1]) and (scored[0][0] - scored[1][0] > 0.15):
            # Clear best candidate despite multiple passing the threshold.
            c = scored[0][1]
            result.confirmed.append(MatchedPair(
                leg_a_ref=d.ref, leg_b_ref=c.ref, amount=d.amount,
                date_diff_days=days_between(d.txn_date, c.txn_date),
                method="amount_date_narration", confidence="low",
            ))
            matched_refs.add(d.ref)
            matched_refs.add(c.ref)
        elif len(good) > 1:
            result.ambiguous.append(AmbiguousGroup(
                anchor_ref=d.ref,
                candidate_refs=[c.ref for c in good],
                kind="self_transfer",
                field_context={"method": "amount_date_narration"},
            ))

    # --- Tier 3: leftovers called out explicitly ---------------------------
    ambiguous_refs = {r for g in result.ambiguous for r in [g.anchor_ref, *g.candidate_refs]}
    for t in candidates:
        if t.ref in matched_refs or t.ref in ambiguous_refs:
            continue
        result.unmatched.append(UnmatchedCandidate(
            ref=t.ref, amount=t.amount, txn_date=t.txn_date,
            reason="transfer-like narration with no matching counterpart leg found",
            kind="self_transfer",
        ))

    return result
