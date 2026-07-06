"""Shared primitives for the self-transfer and refund matchers.

Both matchers follow the same three-tier strategy from the PRD:
RRN/reference-number match -> amount+date+narration fallback (lower
confidence) -> leftover ambiguity handed to the LLM adjudicator. This
module holds the pieces both share so the two matchers stay thin and
consistent with each other.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from bank_statement_analyzer.config import RRN_PATTERNS

_RRN_COMPILED = [re.compile(p, re.IGNORECASE) for p in RRN_PATTERNS]

_VPA_REGEX = re.compile(r"\b[\w.+-]+@[a-zA-Z][\w]+\b")  # e.g. name@okhdfcbank


def extract_rrn(description: str) -> str | None:
    if not description:
        return None
    for pattern in _RRN_COMPILED:
        m = pattern.search(description)
        if m:
            return m.group(1).upper()
    return None


def extract_vpa(description: str) -> str | None:
    if not description:
        return None
    m = _VPA_REGEX.search(description)
    return m.group(0).lower() if m else None


def narration_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def counterparty_bonus(a: str, b: str) -> float:
    """Extra similarity credit when both narrations share a VPA."""
    va, vb = extract_vpa(a), extract_vpa(b)
    if va and vb and va == vb:
        return 0.4
    return 0.0


@dataclass
class MatchedPair:
    leg_a_ref: str          # e.g. the outgoing/debit leg
    leg_b_ref: str          # e.g. the incoming/credit leg
    amount: float
    date_diff_days: int
    method: str              # "rrn" | "amount_date_narration"
    confidence: str           # "high" | "medium" | "low"


@dataclass
class UnmatchedCandidate:
    ref: str
    amount: float
    txn_date: date
    reason: str
    kind: str                 # "self_transfer" | "refund"


@dataclass
class AmbiguousGroup:
    """Multiple plausible legs for the same candidate — needs adjudication."""
    anchor_ref: str
    candidate_refs: list[str]
    kind: str
    field_context: dict = field(default_factory=dict)


def days_between(a: date, b: date) -> int:
    return abs((a - b).days)
