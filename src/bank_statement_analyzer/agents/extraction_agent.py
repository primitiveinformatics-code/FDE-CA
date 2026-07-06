"""Per-statement extraction agent (architecture step 2).

Deterministic parse first; `field_locator` (Sonnet) is invoked only when
the column layout can't be confidently mapped, and only ever with
already-masked sample text.
"""
from __future__ import annotations

from bank_statement_analyzer.llm.client import ClaudeToolClient
from bank_statement_analyzer.parsing.models import ParseResult
from bank_statement_analyzer.parsing.pdf_parser import parse_statement
from bank_statement_analyzer.pii.masker import PIIVault, mask_narration


def process_file(
    pdf_path: str,
    bank: str | None,
    account_ref: str,
    password: str | None,
    llm_client: ClaudeToolClient | None,
    vault: PIIVault,
) -> ParseResult:
    def field_locator_fn(header_row: list[str], sample_rows: list[list[str]]):
        if llm_client is None:
            return None
        masked_rows = [
            [mask_narration(vault, cell) if isinstance(cell, str) else cell for cell in row]
            for row in sample_rows
        ]
        try:
            return llm_client.locate_columns(header_row, masked_rows)
        except Exception:  # noqa: BLE001 - a field_locator failure must not abort the run
            return None

    return parse_statement(
        pdf_path, bank, account_ref, password=password, field_locator_fn=field_locator_fn,
    )
