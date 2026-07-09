"""Classification agent (architecture step 4): deterministic keyword
tagging first; the `classifier` LLM tool is only invoked per unique
counterparty group that keyword rules left unresolved.
"""
from __future__ import annotations

from bank_statement_analyzer.classification.classifier import (
    AnomalyFlag,
    RecurringGroup,
    detect_anomalies,
    detect_recurring,
    tag_transactions,
)
from bank_statement_analyzer.config import MatchingConfig
from bank_statement_analyzer.llm.client import LLMClient
from bank_statement_analyzer.parsing.models import Transaction
from bank_statement_analyzer.pii.masker import PIIVault, mask_narration

_EXCLUDED_FROM_INCOME_TAGS = {"loan_disbursal_candidate", "gift_candidate"}


def classify(
    transactions: list[Transaction],
    excluded_refs: set[str],
    llm_client: LLMClient | None,
    vault: PIIVault,
    cfg: MatchingConfig | None = None,
) -> tuple[list[RecurringGroup], list[AnomalyFlag]]:
    tag_transactions(transactions, excluded_refs)

    unresolved_credits = [
        t for t in transactions
        if t.is_credit
        and t.ref not in excluded_refs
        and t.itr_head is None
        and not (t.tags & _EXCLUDED_FROM_INCOME_TAGS)
    ]
    groups: dict[str, list[Transaction]] = {}
    for t in unresolved_credits:
        groups.setdefault(t.counterparty or "UNKNOWN", []).append(t)

    for counterparty, txns in groups.items():
        if llm_client is None:
            continue
        samples = [mask_narration(vault, t.description) for t in txns[:5]]
        try:
            decision = llm_client.classify_income(counterparty, samples)
        except Exception:  # noqa: BLE001 - one bad classification call must not abort the run
            continue
        head = decision.get("itr_head")
        confidence = decision.get("confidence")
        for t in txns:
            t.itr_head = head
            t.itr_head_confidence = confidence

    recurring = detect_recurring(transactions, excluded_refs, cfg)
    anomalies = detect_anomalies(transactions, excluded_refs, cfg)
    return recurring, anomalies
