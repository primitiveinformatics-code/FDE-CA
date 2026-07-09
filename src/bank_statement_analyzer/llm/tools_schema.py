"""Tool-use schemas for the points where Sonnet is invoked: `field_locator`
(unrecognized column layouts), `adjudicator` (ambiguous self-transfer/refund
leftovers), `classifier` (ITR head suggestion for an unrecognized
counterparty), and `account_info_locator` (basic-field extraction when
regex can't confidently locate them — see its own docstring below for the
privacy tradeoff this one carries). Each is a forced tool-call — the model
must respond via the tool's `input_schema`, never free text — so the
caller always gets a structured, parseable answer.
"""
from __future__ import annotations

from bank_statement_analyzer.config import ITR_HEADS

FIELD_LOCATOR_TOOL = {
    "name": "report_column_mapping",
    "description": (
        "Report which column index (0-based) in a bank statement table "
        "corresponds to each canonical field. Use null for a field that "
        "isn't present in this layout."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date": {"type": "integer"},
            "description": {"type": "integer"},
            "debit": {"type": ["integer", "null"]},
            "credit": {"type": ["integer", "null"]},
            "amount": {"type": ["integer", "null"]},
            "drcr": {"type": ["integer", "null"]},
            "balance": {"type": ["integer", "null"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["date", "description", "confidence"],
    },
}

ADJUDICATOR_TOOL = {
    "name": "report_match_decision",
    "description": (
        "Decide which one candidate reference (if any) is the correct "
        "counterpart leg for the anchor transaction, or null if none is."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "anchor_ref": {"type": "string"},
            "chosen_ref": {"type": ["string", "null"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "rationale": {"type": "string"},
        },
        "required": ["anchor_ref", "chosen_ref", "confidence", "rationale"],
    },
}

ACCOUNT_INFO_TOOL = {
    "name": "report_account_info",
    "description": (
        "Report the account holder name, account number, IFSC code, bank "
        "name, and statement period found in this bank statement's header "
        "text. Use null for any field not confidently present — do not guess."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "bank": {"type": ["string", "null"]},
            "account_holder_name": {"type": ["string", "null"]},
            "account_number": {"type": ["string", "null"]},
            "ifsc": {"type": ["string", "null"]},
            "period_start": {"type": ["string", "null"], "description": "ISO date YYYY-MM-DD"},
            "period_end": {"type": ["string", "null"], "description": "ISO date YYYY-MM-DD"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["confidence"],
    },
}

CLASSIFIER_TOOL = {
    "name": "report_income_classification",
    "description": "Suggest an ITR head for a grouped, non-transfer income source.",
    "input_schema": {
        "type": "object",
        "properties": {
            "counterparty": {"type": "string"},
            "itr_head": {"type": "string", "enum": ITR_HEADS},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "rationale": {"type": "string"},
        },
        "required": ["counterparty", "itr_head", "confidence"],
    },
}
