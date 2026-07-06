"""PII masking — P0, non-negotiable.

Raw PII (account number, PAN, full name, mobile, email) must never leave
the machine. This module masks such values into opaque, reversible
tokens *before* any text is handed to the Claude API. Only the local
`PIIVault` can reverse a token back to its original value, and that only
happens in `reference_hydrator` at export time — never over the network.

Nothing here mutates the raw `Transaction`/`StatementMeta` objects used
for matching/calculation/export; masking only ever produces a *copy* of
text meant for an LLM payload.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PAN_REGEX = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
EMAIL_REGEX = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
MOBILE_REGEX = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)")
# Long digit runs (account numbers, card numbers) embedded in narration text.
# Deliberately broad (9-18 digits) — over-masking a reference number is safe,
# leaking an account number is not.
LONG_NUMBER_REGEX = re.compile(r"\b\d{9,18}\b")


@dataclass
class PIIVault:
    """Local-only store mapping opaque tokens -> original PII values.

    Never serialize this object to disk in plaintext and never send its
    contents anywhere except the final local export step.
    """

    _value_to_token: dict[str, str] = field(default_factory=dict)
    _token_to_value: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def tokenize(self, kind: str, value: str) -> str:
        if not value:
            return value
        existing = self._value_to_token.get((kind, value).__repr__())
        if existing:
            return existing
        n = self._counters.get(kind, 0) + 1
        self._counters[kind] = n
        token = f"[{kind}-{n}]"
        self._value_to_token[(kind, value).__repr__()] = token
        self._token_to_value[token] = value
        return token

    def rehydrate(self, text: str) -> str:
        """Replace tokens back to their original values. Local use only."""
        if not text:
            return text
        for token, value in self._token_to_value.items():
            text = text.replace(token, value)
        return text


def mask_account_number(vault: PIIVault, account_number: str) -> str:
    return vault.tokenize("ACCT", account_number)


def mask_name(vault: PIIVault, full_name: str) -> str:
    return vault.tokenize("NAME", full_name)


def mask_narration(vault: PIIVault, text: str) -> str:
    """Scrub PAN / email / mobile / long-account-like numbers from free text."""
    if not text:
        return text

    def _sub(pattern: re.Pattern, kind: str, s: str) -> str:
        def repl(m: re.Match) -> str:
            return vault.tokenize(kind, m.group(0))
        return pattern.sub(repl, s)

    text = _sub(PAN_REGEX, "PAN", text)
    text = _sub(EMAIL_REGEX, "EMAIL", text)
    text = _sub(MOBILE_REGEX, "MOBILE", text)
    text = _sub(LONG_NUMBER_REGEX, "NUM", text)
    return text


def mask_statement_fields_for_llm(vault: PIIVault, meta) -> dict:
    """Masked view of a StatementMeta safe to include in an LLM payload."""
    return {
        "bank": meta.bank,
        "account_holder_name": mask_name(vault, meta.account_holder_name or ""),
        "account_number": mask_account_number(vault, meta.account_number_masked or ""),
        "ifsc": meta.ifsc,  # branch code, not personal data
        "period_start": str(meta.period_start) if meta.period_start else None,
        "period_end": str(meta.period_end) if meta.period_end else None,
    }


def mask_transactions_for_llm(vault: PIIVault, transactions) -> list[dict]:
    """Masked, minimal view of transactions safe to include in an LLM payload.

    Only narration text is scrubbed; dates/amounts/refs carry no PII.
    """
    out = []
    for t in transactions:
        out.append({
            "ref": t.ref,
            "date": str(t.txn_date),
            "description": mask_narration(vault, t.description),
            "debit": t.debit,
            "credit": t.credit,
        })
    return out
