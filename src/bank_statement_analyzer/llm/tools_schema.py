"""Tool-use schemas for the three points where Sonnet is invoked:
`field_locator` (unrecognized column layouts), `adjudicator` (ambiguous
self-transfer/refund leftovers), and `classifier` (ITR head suggestion
for an unrecognized counterparty). Each is a forced tool-call — the
model must respond via the tool's `input_schema`, never free text — so
the caller always gets a structured, parseable answer.
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
