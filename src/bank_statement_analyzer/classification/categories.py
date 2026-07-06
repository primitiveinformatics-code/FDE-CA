"""Deterministic keyword taxonomy + counterparty normalization.

These are the cheap, unambiguous classifications that don't need the
`classifier` LLM tool at all (interest credits, CC payments/fees, cash,
EMIs, taxes...). Anything left with `itr_head is None` after this pass is
what `classification_agent` sends to the LLM for a suggestion.
"""
from __future__ import annotations

import re

from bank_statement_analyzer.config import (
    CASH_KEYWORDS,
    CC_FEE_KEYWORDS,
    CC_PAYMENT_KEYWORDS,
    EMI_KEYWORDS,
    GIFT_KEYWORDS,
    INTEREST_KEYWORDS,
    LOAN_DISBURSAL_KEYWORDS,
    RENT_KEYWORDS,
    SIP_KEYWORDS,
    SUBSCRIPTION_KEYWORDS,
    TDS_TAX_KEYWORDS,
    UPI_KEYWORDS,
)

_SPLIT_RE = re.compile(r"[/\-,]")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 &]")


def _has_any(desc: str, keywords: list[str]) -> bool:
    d = desc.lower()
    return any(kw in d for kw in keywords)


def classify_expense_category(description: str) -> str:
    """FR-6 taxonomy: UPI merchant spend, Cash/ATM, Loan EMIs,
    Charges & fees, Credit-card payments, Taxes/TDS, Other."""
    if _has_any(description, CC_PAYMENT_KEYWORDS):
        return "Credit-card payments"
    if _has_any(description, CC_FEE_KEYWORDS):
        return "Charges & fees"
    if _has_any(description, TDS_TAX_KEYWORDS):
        return "Taxes/TDS"
    if _has_any(description, CASH_KEYWORDS):
        return "Cash/ATM"
    if _has_any(description, EMI_KEYWORDS):
        return "Loan EMIs"
    if _has_any(description, UPI_KEYWORDS):
        return "UPI merchant spend"
    return "Other"


def is_cc_payment_or_fee(description: str) -> bool:
    return _has_any(description, CC_PAYMENT_KEYWORDS) or _has_any(description, CC_FEE_KEYWORDS)


def is_interest_credit(description: str) -> bool:
    return _has_any(description, INTEREST_KEYWORDS)


def is_loan_disbursal(description: str) -> bool:
    return _has_any(description, LOAN_DISBURSAL_KEYWORDS)


def is_gift(description: str) -> bool:
    return _has_any(description, GIFT_KEYWORDS)


def recurring_type_hint(description: str) -> str | None:
    if _has_any(description, EMI_KEYWORDS):
        return "EMI"
    if _has_any(description, SIP_KEYWORDS):
        return "SIP"
    if _has_any(description, RENT_KEYWORDS):
        return "Rent"
    if _has_any(description, SUBSCRIPTION_KEYWORDS):
        return "Subscription"
    return None


def is_26as_likely(itr_head: str | None) -> bool:
    return itr_head in ("Interest", "Business/Professional receipts")


def extract_counterparty(description: str) -> str:
    """Best-effort counterparty key from narration, for grouping credits.

    Bank narrations are unstructured, so this is a heuristic: strip the
    transfer-mode prefix (UPI/NEFT/...), drop pure-numeric reference
    tokens, and keep the longest remaining alphabetic token as the name.
    Ambiguous cases stay lumped by this key, which is fine — grouping
    only needs to be *consistent*, not perfectly accurate; a human
    reviews the "Income sources" sheet regardless.
    """
    if not description:
        return "UNKNOWN"
    tokens = [tok.strip() for tok in _SPLIT_RE.split(description) if tok.strip()]
    candidates = [tok for tok in tokens if not tok.isdigit() and len(tok) >= 3]
    if not candidates:
        return _NON_ALNUM_RE.sub("", description.upper()).strip()[:40] or "UNKNOWN"
    best = max(candidates, key=len)
    return _NON_ALNUM_RE.sub("", best.upper()).strip()[:40] or "UNKNOWN"
