"""Core dataclasses shared across the pipeline.

`Transaction` rows flow: extraction -> reconciliation -> classification ->
export. Fields are additive (tags/category/itr_head get filled in by later
stages) so the same row object is threaded through the whole run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Transaction:
    serial: int             # 1-based row index within its source statement
    bank: str
    pdf: str                 # original filename (for the reference triple)
    account_ref: str         # opaque local id, e.g. "ACC-1"; never the raw number
    txn_date: date
    description: str         # narration, PII-masked before ever reaching the LLM
    debit: float = 0.0
    credit: float = 0.0
    balance: float | None = None

    # populated by later stages
    tags: set[str] = field(default_factory=set)
    category: str | None = None          # expense category (debits)
    itr_head: str | None = None          # ITR head suggestion (credits)
    itr_head_confidence: str | None = None
    counterparty: str | None = None
    rrn: str | None = None               # extracted UPI RRN / reference no.
    match_confidence: str | None = None  # for self-transfer / refund legs
    matched_ref: str | None = None       # ref of the paired leg, if any

    @property
    def ref(self) -> str:
        from bank_statement_analyzer.utils.references import make_ref
        return make_ref(self.serial, self.bank, self.pdf)

    @property
    def is_credit(self) -> bool:
        return self.credit > 0

    @property
    def is_debit(self) -> bool:
        return self.debit > 0

    @property
    def amount(self) -> float:
        return self.credit if self.is_credit else self.debit


@dataclass
class StatementMeta:
    bank: str
    pdf: str
    account_ref: str
    account_holder_name: str | None = None
    account_number_masked: str | None = None
    account_number_hash: str | None = None   # for matching same account across files
    ifsc: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    used_ocr: bool = False
    ocr_raw_text: str | None = None          # shown next to fields for manual verification (FR-1)
    flagged: bool = False
    flag_reason: str | None = None


@dataclass
class ParseResult:
    meta: StatementMeta
    transactions: list[Transaction] = field(default_factory=list)
    success: bool = True
    error: str | None = None
