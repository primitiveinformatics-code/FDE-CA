"""Reconciliation agent (architecture step 3): runs the deterministic
`transfer_matcher`/`refund_matcher`, then hands only the genuinely
ambiguous leftovers to the `adjudicator` LLM tool — never raw PII, only
refs/dates/amounts and masked narration.
"""
from __future__ import annotations

from bank_statement_analyzer.config import MatchingConfig
from bank_statement_analyzer.llm.client import LLMClient
from bank_statement_analyzer.parsing.models import Transaction
from bank_statement_analyzer.pii.masker import PIIVault, mask_narration
from bank_statement_analyzer.reconciliation.common import AmbiguousGroup, MatchedPair, UnmatchedCandidate
from bank_statement_analyzer.reconciliation.refund_matcher import match_refunds
from bank_statement_analyzer.reconciliation.transfer_matcher import match_self_transfers
from bank_statement_analyzer.run_context import ReconciliationResult


def _resolve_ambiguous(
    groups: list[AmbiguousGroup],
    transactions_by_ref: dict[str, Transaction],
    llm_client: LLMClient | None,
    vault: PIIVault,
    anchor_is_leg_a: bool,
) -> tuple[list[MatchedPair], list[UnmatchedCandidate]]:
    confirmed: list[MatchedPair] = []
    unmatched: list[UnmatchedCandidate] = []

    for group in groups:
        anchor = transactions_by_ref[group.anchor_ref]
        if llm_client is None:
            unmatched.append(UnmatchedCandidate(
                ref=anchor.ref, amount=anchor.amount, txn_date=anchor.txn_date,
                reason=f"ambiguous {group.kind} match ({len(group.candidate_refs)} candidates) — LLM adjudication unavailable",
                kind=group.kind,
            ))
            continue

        candidates = [
            {
                "ref": transactions_by_ref[ref].ref,
                "date": str(transactions_by_ref[ref].txn_date),
                "amount": transactions_by_ref[ref].amount,
                "description": mask_narration(vault, transactions_by_ref[ref].description),
            }
            for ref in group.candidate_refs
        ]
        try:
            decision = llm_client.adjudicate_match(anchor.ref, candidates)
        except Exception:  # noqa: BLE001 - adjudication failure must not abort the run
            unmatched.append(UnmatchedCandidate(
                ref=anchor.ref, amount=anchor.amount, txn_date=anchor.txn_date,
                reason=f"ambiguous {group.kind} match — adjudicator call failed",
                kind=group.kind,
            ))
            continue

        chosen_ref = decision.get("chosen_ref")
        if not chosen_ref or chosen_ref not in group.candidate_refs:
            unmatched.append(UnmatchedCandidate(
                ref=anchor.ref, amount=anchor.amount, txn_date=anchor.txn_date,
                reason=f"ambiguous {group.kind} match — adjudicator found no confident match",
                kind=group.kind,
            ))
            continue

        chosen = transactions_by_ref[chosen_ref]
        leg_a, leg_b = (anchor, chosen) if anchor_is_leg_a else (chosen, anchor)
        confirmed.append(MatchedPair(
            leg_a_ref=leg_a.ref, leg_b_ref=leg_b.ref, amount=anchor.amount,
            date_diff_days=abs((leg_a.txn_date - leg_b.txn_date).days),
            method="adjudicated", confidence=decision.get("confidence", "medium"),
        ))

    return confirmed, unmatched


def reconcile(
    transactions: list[Transaction],
    llm_client: LLMClient | None,
    vault: PIIVault,
    cfg: MatchingConfig | None = None,
) -> tuple[ReconciliationResult, ReconciliationResult]:
    by_ref = {t.ref: t for t in transactions}

    st = match_self_transfers(transactions, cfg)
    rf = match_refunds(transactions, cfg)

    st_confirmed_extra, st_unmatched_extra = _resolve_ambiguous(
        st.ambiguous, by_ref, llm_client, vault, anchor_is_leg_a=True,
    )
    rf_confirmed_extra, rf_unmatched_extra = _resolve_ambiguous(
        rf.ambiguous, by_ref, llm_client, vault, anchor_is_leg_a=False,
    )

    self_transfer_result = ReconciliationResult(
        confirmed=[*st.confirmed, *st_confirmed_extra],
        unmatched=[*st.unmatched, *st_unmatched_extra],
    )
    refund_result = ReconciliationResult(
        confirmed=[*rf.confirmed, *rf_confirmed_extra],
        unmatched=[*rf.unmatched, *rf_unmatched_extra],
    )
    return self_transfer_result, refund_result
