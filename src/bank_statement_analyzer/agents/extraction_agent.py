"""Per-statement extraction agent (architecture step 2).

Deterministic parse first. Two Sonnet fallbacks, both only invoked when
the deterministic step fails to confidently resolve something:

- `field_locator`, for an unrecognized column layout — always PII-masked
  before it reaches the API.
- `account_info_locator`, for the account holder name / account number /
  IFSC / bank / statement period, when regex can't confidently locate
  one or more of them — sent RAW (unmasked) by default, since extracting
  a value necessarily means the model has to read it. This is a
  deliberate, scoped exception to this app's general PII-masking rule,
  controlled by `keep_metadata_local` (the UI's "keep names & account
  numbers local" toggle, off by default).
"""
from __future__ import annotations

import logging

from bank_statement_analyzer.llm.client import LLMClient
from bank_statement_analyzer.parsing.models import ParseResult
from bank_statement_analyzer.parsing.pdf_parser import parse_statement
from bank_statement_analyzer.pii.masker import PIIVault, mask_narration

logger = logging.getLogger(__name__)


def process_file(
    pdf_path: str,
    bank: str | None,
    account_ref: str,
    password: str | None,
    llm_client: LLMClient | None,
    vault: PIIVault,
    keep_metadata_local: bool = False,
) -> ParseResult:
    def field_locator_fn(header_row: list[str], sample_rows: list[list[str]]):
        if llm_client is None:
            return None
        # `header_row` is caller-supplied and, when table-detection
        # misfires on an unusual layout (the exact case that lands us
        # here), can turn out to be a slice of running document text
        # rather than genuine column labels — mask it exactly like the
        # sample rows before it ever reaches the API (P0 privacy).
        masked_header = [mask_narration(vault, cell) if isinstance(cell, str) else cell for cell in header_row]
        masked_rows = [
            [mask_narration(vault, cell) if isinstance(cell, str) else cell for cell in row]
            for row in sample_rows
        ]
        try:
            return llm_client.locate_columns(masked_header, masked_rows)
        except Exception:  # noqa: BLE001 - a field_locator failure must not abort the run
            logger.exception("field_locator LLM call failed for %s; falling back to unmapped/flagged", pdf_path)
            return None

    def account_info_fn(header_text: str):
        if llm_client is None or keep_metadata_local:
            return None
        try:
            return llm_client.locate_account_info(header_text)
        except Exception:  # noqa: BLE001 - an account_info failure must not abort the run
            logger.exception("account_info LLM call failed for %s; falling back to regex-only fields", pdf_path)
            return None

    result = parse_statement(
        pdf_path, bank, account_ref, password=password,
        field_locator_fn=field_locator_fn, account_info_fn=account_info_fn,
    )
    logger.info(
        "extraction_agent.process_file(%s): success=%s, transactions=%d, flagged=%s%s",
        pdf_path, result.success, len(result.transactions), result.meta.flagged,
        f", reason={result.meta.flag_reason!r}" if result.meta.flagged else "",
    )
    return result
