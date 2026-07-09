"""Deterministic PDF statement parsing (FR-1, FR-2).

Order of operations per file:

1. Open with pdfplumber (prompting the caller-supplied password if the
   file is encrypted).
2. Extract tables page-by-page. If a page has no extractable text at
   all, fall back to OCR (`ocr.py`) and keep the raw OCR text on
   `StatementMeta.ocr_raw_text` for manual verification in the UI.
3. Map the header row to the canonical column set (date / description /
   debit / credit / balance) using a synonym dictionary. Most bank
   layouts are covered by this. When the header can't be confidently
   mapped, the caller may supply `field_locator_fn` — this is the one
   point where Sonnet is invoked, and only with masked header/sample-row
   text, to return a column mapping.
4. Extract account holder name / account number / IFSC / statement
   period from the document text via regex. Anything that can't be
   located confidently leaves the file flagged for "Needs attention"
   and excluded from downstream matching (FR-2).

Nothing here does arithmetic beyond parsing a string into a float —
totals/sums/reconciliation all happen in `calculators.calculator`.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Callable

from bank_statement_analyzer.parsing.models import ParseResult, StatementMeta, Transaction
from bank_statement_analyzer.parsing.ocr import ocr_document, page_needs_ocr
from bank_statement_analyzer.pii.masker import PIIVault, mask_narration

logger = logging.getLogger(__name__)

# Table-detection heuristics occasionally misfire and hand us a "header" or
# "sample" row that's actually a slice of running document text (title line,
# account blurb) rather than genuine column labels — which can contain PII.
# Every row logged for diagnostics goes through this scrub first. Tokens are
# never reversed (this vault is log-only, write-only) so a fresh throwaway
# instance is fine.
_log_vault = PIIVault()


def _masked_row(row: list[str] | None) -> list[str] | None:
    if row is None:
        return None
    return [mask_narration(_log_vault, c) if isinstance(c, str) else c for c in row]

# --- column-header synonym dictionary ---------------------------------------

DATE_SYNONYMS = ["date", "txn date", "transaction date", "value date", "tran date", "posting date"]
DESC_SYNONYMS = ["narration", "description", "particulars", "transaction details", "details", "remarks"]
DEBIT_SYNONYMS = ["debit", "withdrawal", "withdrawal amt", "debit amount", "withdrawal amount", "dr"]
CREDIT_SYNONYMS = ["credit", "deposit", "deposit amt", "credit amount", "deposit amount", "cr"]
BALANCE_SYNONYMS = ["balance", "closing balance", "running balance", "balance amt", "available balance"]
AMOUNT_SYNONYMS = ["amount", "txn amount", "transaction amount"]
DRCR_SYNONYMS = ["dr/cr", "cr/dr", "type", "indicator"]

_DATE_FORMATS = [
    "%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y",
    "%d-%b-%Y", "%d %b %Y", "%d-%b-%y", "%d %B %Y",
    "%Y-%m-%d", "%m/%d/%Y",
]

_CURRENCY_STRIP_RE = re.compile(r"₹|Rs\.?|INR|,|\s", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"-?\(?\d+(?:\.\d+)?\)?")


def _normalize_header_cell(cell: str) -> str:
    return re.sub(r"[^a-z/ ]", "", (cell or "").strip().lower())


def _normalized_row(row: list[str]) -> list[str]:
    return [_normalize_header_cell(c) for c in row]


def normalize_amount(raw: str | None) -> float:
    if raw is None:
        return 0.0
    s = raw.strip()
    if not s or s in ("-", "--", "NA", "N/A"):
        return 0.0
    negative = s.startswith("(") and s.endswith(")")
    cleaned = _CURRENCY_STRIP_RE.sub("", s)
    cleaned = cleaned.replace("(", "").replace(")", "")
    trailing_dr = bool(re.search(r"dr$", cleaned, re.IGNORECASE))
    cleaned = re.sub(r"(?i)\s*(dr|cr)\s*$", "", cleaned).strip()
    m = _AMOUNT_RE.search(cleaned)
    if not m:
        return 0.0
    value = float(m.group(0).replace("(", "").replace(")", ""))
    if negative or trailing_dr:
        value = -abs(value)
    return value


def parse_date_flexible(raw: str | None) -> date | None:
    if not raw:
        return None
    s = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def map_columns(header_row: list[str]) -> dict[str, int] | None:
    """Map a header row to canonical column indices, or None if the
    minimum required set (date, description, and either debit+credit or
    amount+drcr) can't be confidently identified."""
    normalized = [_normalize_header_cell(c) for c in header_row]
    col_map: dict[str, int] = {}

    def find(synonyms: list[str]) -> int | None:
        for i, cell in enumerate(normalized):
            if cell in synonyms:
                return i
        # Fuzzy fallback restricted to longer synonyms: short abbreviations
        # like "dr"/"cr" must match the whole cell exactly (above), not as a
        # substring, or a compound header like "Dr/Cr" would falsely match
        # both the debit and credit column search.
        for i, cell in enumerate(normalized):
            if any((cell.startswith(s) or s in cell) for s in synonyms if len(s) > 2):
                return i
        return None

    date_idx = find(DATE_SYNONYMS)
    desc_idx = find(DESC_SYNONYMS)
    debit_idx = find(DEBIT_SYNONYMS)
    credit_idx = find(CREDIT_SYNONYMS)
    balance_idx = find(BALANCE_SYNONYMS)
    amount_idx = find(AMOUNT_SYNONYMS)
    drcr_idx = find(DRCR_SYNONYMS)

    if date_idx is None or desc_idx is None:
        logger.debug(
            "map_columns: could not identify date/description columns in header=%r "
            "(normalized=%r, date_idx=%s, desc_idx=%s)",
            _masked_row(header_row), normalized, date_idx, desc_idx,
        )
        return None

    col_map["date"] = date_idx
    col_map["description"] = desc_idx
    if balance_idx is not None:
        col_map["balance"] = balance_idx

    if debit_idx is not None and credit_idx is not None:
        col_map["debit"] = debit_idx
        col_map["credit"] = credit_idx
        return col_map
    if amount_idx is not None and drcr_idx is not None:
        col_map["amount"] = amount_idx
        col_map["drcr"] = drcr_idx
        return col_map
    logger.debug(
        "map_columns: found date/description but no debit+credit or amount+drcr pair "
        "in header=%r (debit_idx=%s, credit_idx=%s, amount_idx=%s, drcr_idx=%s)",
        _masked_row(header_row), debit_idx, credit_idx, amount_idx, drcr_idx,
    )
    return None


def parse_table_rows(
    rows: list[list[str]],
    col_map: dict[str, int],
    bank: str,
    pdf: str,
    account_ref: str,
    start_serial: int = 1,
) -> list[Transaction]:
    transactions = []
    serial = start_serial
    for row in rows:
        if not row or len(row) <= max(col_map.values()):
            continue
        txn_date = parse_date_flexible(row[col_map["date"]])
        if txn_date is None:
            continue  # header/subtotal/footer row, not a transaction
        description = (row[col_map["description"]] or "").strip()

        if "debit" in col_map:
            debit = abs(normalize_amount(row[col_map["debit"]]))
            credit = abs(normalize_amount(row[col_map["credit"]]))
        else:
            amount = abs(normalize_amount(row[col_map["amount"]]))
            drcr = (row[col_map["drcr"]] or "").strip().lower()
            is_debit = drcr.startswith("d")
            debit = amount if is_debit else 0.0
            credit = 0.0 if is_debit else amount

        balance = normalize_amount(row[col_map["balance"]]) if "balance" in col_map else None

        transactions.append(Transaction(
            serial=serial, bank=bank, pdf=pdf, account_ref=account_ref,
            txn_date=txn_date, description=description,
            debit=debit, credit=credit, balance=balance,
        ))
        serial += 1
    return transactions


# --- statement metadata extraction ------------------------------------------

_ACCOUNT_NUMBER_RE = re.compile(
    r"(?:account|a/c|acct)\.?\s*(?:no\.?|number)?\s*[:\-]?\s*([xX\*0-9]{6,20})",
    re.IGNORECASE,
)
_IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
_NAME_RE = re.compile(r"(?:account\s*holder\s*name|customer\s*name|name)\s*[:\-]\s*([A-Za-z .]{3,60})", re.IGNORECASE)
_PERIOD_RE = re.compile(
    r"(?:statement\s+(?:for|from|period)[^0-9A-Za-z]{0,20})?"
    r"(\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{2,4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})"
    r"[\s:]*(?:to|-|–|through)[\s:]*"
    r"(\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{2,4}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
    re.IGNORECASE,
)


def extract_account_number(text: str) -> str | None:
    m = _ACCOUNT_NUMBER_RE.search(text)
    return m.group(1) if m else None


def extract_ifsc(text: str) -> str | None:
    m = _IFSC_RE.search(text)
    return m.group(0) if m else None


def extract_account_holder_name(text: str) -> str | None:
    m = _NAME_RE.search(text)
    return m.group(1).strip() if m else None


def extract_statement_period(text: str) -> tuple[date | None, date | None]:
    m = _PERIOD_RE.search(text)
    if not m:
        return None, None
    start = parse_date_flexible(m.group(1))
    end = parse_date_flexible(m.group(2))
    return start, end


KNOWN_BANK_NAMES = [
    "HDFC BANK", "ICICI BANK", "STATE BANK OF INDIA", "SBI", "AXIS BANK",
    "KOTAK MAHINDRA BANK", "PUNJAB NATIONAL BANK", "PNB", "BANK OF BARODA",
    "CANARA BANK", "UNION BANK OF INDIA", "IDFC FIRST BANK", "YES BANK",
    "INDUSIND BANK", "BANK OF INDIA", "INDIAN BANK", "CENTRAL BANK OF INDIA",
]


def guess_bank_name(text: str) -> str | None:
    upper = text.upper()
    for name in KNOWN_BANK_NAMES:
        if name in upper:
            return name.title()
    return None


def mask_account_number_display(account_number: str | None) -> str | None:
    if not account_number:
        return None
    digits = re.sub(r"\D", "", account_number)
    if len(digits) < 4:
        return "XX" + digits
    return "X" * (len(digits) - 4) + digits[-4:]


FieldLocatorFn = Callable[[list[str], list[list[str]]], dict[str, int] | None]
AccountInfoFn = Callable[[str], dict | None]
ACCOUNT_INFO_HEADER_CHARS = 4000  # name/account/IFSC/period are always near the top; no need to send the whole statement


def is_password_protected(pdf_path: str) -> bool:
    """Quick pre-flight check so the UI can prompt for a password before
    the full parse runs (FR-1: prompt per file)."""
    import pdfplumber

    try:
        with pdfplumber.open(pdf_path):
            pass
        return False
    except Exception as e:  # noqa: BLE001 - broad on purpose, see message sniff below
        message = str(e).lower()
        return "password" in message or "encrypt" in message


def parse_statement(
    pdf_path: str,
    bank: str | None,
    account_ref: str,
    password: str | None = None,
    field_locator_fn: FieldLocatorFn | None = None,
    account_info_fn: AccountInfoFn | None = None,
) -> ParseResult:
    """Parse one statement PDF end-to-end. `field_locator_fn`, if given,
    is called ONLY with already-extracted header/sample-row text (never
    raw PII) when deterministic column mapping fails. `account_info_fn`,
    if given, is called with RAW (unmasked) header text when regex
    couldn't confidently locate the account holder name / account number
    / IFSC / bank / statement period — see its docstring in `llm/client.py`
    for the privacy tradeoff this one carries. `bank` may be None, in
    which case it's guessed from the document text and the file is
    flagged for the CA to confirm if guessing fails (FR-2)."""
    import pdfplumber

    pdf_name = pdf_path.rsplit("/", 1)[-1]
    meta = StatementMeta(bank=bank or "", pdf=pdf_name, account_ref=account_ref)
    logger.info("[%s] parse_statement starting (bank hint=%r, password_supplied=%s)", pdf_name, bank, bool(password))

    try:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            logger.info("[%s] opened OK: %d page(s)", pdf_name, len(pdf.pages))
            full_text_parts = []
            used_ocr = False
            all_rows: list[list[str]] = []
            header_row: list[str] | None = None
            fallback_header_row: list[str] | None = None  # first row ever seen; used only if no real header is ever found
            fallback_rows: list[list[str]] = []  # rows collected while still searching for a real header

            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                page_ocr_needed = page_needs_ocr(page_text)
                if page_ocr_needed:
                    used_ocr = True
                tables = page.extract_tables() or []
                if len(page_text.strip()) > 200 and all(len(t) <= 1 for t in tables):
                    # Default table detection is ruling-line/lattice based and
                    # finds nothing on statements with no visible cell borders
                    # (common for digitally-generated bank PDFs) — it then
                    # returns at best a spurious 1-row "table". Retry with a
                    # text-position-based strategy, which works on borderless
                    # layouts, before concluding this page has no table.
                    text_tables = page.extract_tables(table_settings={
                        "vertical_strategy": "text", "horizontal_strategy": "text",
                    }) or []
                    if any(len(t) > 1 for t in text_tables):
                        # Deliberately replace, not merge, with the
                        # line-based result: the two strategies can slice
                        # a row into a different number of cells, so
                        # reusing a line-based header's column indices
                        # against text-based data rows (or vice versa)
                        # silently misaligns columns. Column mapping is
                        # re-derived from whichever single strategy's
                        # rows we end up using (see the header-row scan
                        # below), never mixed across strategies.
                        logger.info(
                            "[%s] page %d: default (line-based) table detection found no real rows "
                            "despite %d char(s) of text; using text-position-based table strategy instead",
                            pdf_name, page_num, len(page_text.strip()),
                        )
                        tables = text_tables
                logger.debug(
                    "[%s] page %d: %d extracted-text char(s), needs_ocr=%s, %d table(s) found",
                    pdf_name, page_num, len(page_text.strip()), page_ocr_needed, len(tables),
                )
                for table in tables:
                    if not table:
                        continue
                    if header_row is None:
                        if fallback_header_row is None:
                            fallback_header_row = table[0]
                        # Don't blindly trust row 0 as the header: a
                        # text-position-based table strategy on a
                        # borderless layout can capture a document
                        # title/blurb line as its own fake single-row
                        # "table" ahead of the real header/data table.
                        # Scan for the first row that actually looks like
                        # a header; if this table doesn't have one,
                        # discard it as noise and keep looking at the
                        # next table/page instead of locking in early.
                        located_idx = next(
                            (i for i, row in enumerate(table[:30]) if map_columns(row) is not None), None,
                        )
                        if located_idx is not None:
                            header_row = table[located_idx]
                            all_rows.extend(table[located_idx + 1:])
                        else:
                            fallback_rows.extend(table[1:])
                    elif any(_normalized_row(row) == _normalized_row(header_row) for row in table[:5]):
                        # Same header repeated on this page/table (very common
                        # on multi-page statements, sometimes preceded by a
                        # blurb row too) — drop everything through it, keep
                        # only the data rows after.
                        repeat_idx = next(
                            i for i, row in enumerate(table[:5]) if _normalized_row(row) == _normalized_row(header_row)
                        )
                        all_rows.extend(table[repeat_idx + 1:])
                    else:
                        all_rows.extend(table)
                full_text_parts.append(page_text)

            if header_row is None and fallback_header_row is not None:
                # No row anywhere matched a recognized header pattern —
                # genuinely unrecognized layout. Fall back to the very
                # first row seen (old behavior) so there's still a
                # header/sample-rows pair for the field_locator LLM
                # fallback (or the "unrecognized layout" flag) to work with.
                logger.info(
                    "[%s] no row matched a known header pattern anywhere in the document; "
                    "using the first row seen as a provisional header for field_locator/flagging",
                    pdf_name,
                )
                header_row = fallback_header_row
                all_rows = fallback_rows

            full_text = "\n".join(full_text_parts)
            logger.info(
                "[%s] page scan complete: used_ocr=%s, header_row_found=%s, raw_data_rows=%d, total_text_chars=%d",
                pdf_name, used_ocr, header_row is not None, len(all_rows), len(full_text.strip()),
            )
            if header_row is not None:
                logger.info("[%s] header row: %r", pdf_name, _masked_row(header_row))

            if used_ocr:
                meta.used_ocr = True
                logger.info("[%s] running OCR fallback (no/low extractable text on at least one page)", pdf_name)
                meta.ocr_raw_text = ocr_document(pdf)
                logger.info("[%s] OCR complete: %d char(s) recovered", pdf_name, len(meta.ocr_raw_text or ""))
                full_text = full_text + "\n" + meta.ocr_raw_text

            if not full_text.strip() and not all_rows:
                meta.flagged = True
                meta.flag_reason = "No extractable text or table found (not text-based and OCR yielded nothing)"
                logger.warning("[%s] FLAGGED: %s", pdf_name, meta.flag_reason)
                return ParseResult(meta=meta, transactions=[], success=False, error=meta.flag_reason)

            if not meta.bank:
                meta.bank = guess_bank_name(full_text) or ""

            meta.account_number_masked = mask_account_number_display(extract_account_number(full_text))
            meta.account_holder_name = extract_account_holder_name(full_text)
            meta.ifsc = extract_ifsc(full_text)
            meta.period_start, meta.period_end = extract_statement_period(full_text)
            logger.info(
                "[%s] fields extracted: bank=%r, account_holder=%s, account_number=%s, ifsc=%s, period=%s..%s",
                pdf_name, meta.bank,
                "found" if meta.account_holder_name else "MISSING",
                meta.account_number_masked or "MISSING", meta.ifsc or "MISSING",
                meta.period_start, meta.period_end,
            )

            missing_fields = [
                name for name, val in [
                    ("bank name", meta.bank),
                    ("account holder name", meta.account_holder_name),
                    ("account number", meta.account_number_masked),
                    ("statement period", meta.period_start),
                ] if not val
            ]

            if missing_fields and account_info_fn is not None:
                logger.info(
                    "[%s] regex could not confidently locate: %s; invoking account_info LLM fallback "
                    "(raw header text sent — see account_info_fn docstring for the privacy tradeoff)",
                    pdf_name, ", ".join(missing_fields),
                )
                try:
                    info = account_info_fn(full_text[:ACCOUNT_INFO_HEADER_CHARS])
                except Exception:  # noqa: BLE001 - an account_info failure must not abort the run
                    logger.exception("[%s] account_info LLM call failed; continuing with regex-only fields", pdf_name)
                    info = None
                if info:
                    if not meta.bank and info.get("bank"):
                        meta.bank = info["bank"]
                    if not meta.account_holder_name and info.get("account_holder_name"):
                        meta.account_holder_name = info["account_holder_name"]
                    if not meta.account_number_masked and info.get("account_number"):
                        meta.account_number_masked = mask_account_number_display(info["account_number"])
                    if not meta.ifsc and info.get("ifsc"):
                        meta.ifsc = info["ifsc"]
                    if not meta.period_start and info.get("period_start"):
                        meta.period_start = parse_date_flexible(info["period_start"])
                    if not meta.period_end and info.get("period_end"):
                        meta.period_end = parse_date_flexible(info["period_end"])
                    filled = [k for k in ("bank", "account_holder_name", "account_number", "ifsc", "period_start", "period_end") if info.get(k)]
                    logger.info("[%s] account_info LLM fallback returned usable values for: %s", pdf_name, ", ".join(filled) or "nothing")
                else:
                    logger.info("[%s] account_info LLM fallback returned nothing usable (low confidence or no client)", pdf_name)

                missing_fields = [
                    name for name, val in [
                        ("bank name", meta.bank),
                        ("account holder name", meta.account_holder_name),
                        ("account number", meta.account_number_masked),
                        ("statement period", meta.period_start),
                    ] if not val
                ]

            if missing_fields:
                meta.flagged = True
                meta.flag_reason = f"Could not confidently locate: {', '.join(missing_fields)}"
                logger.warning("[%s] flagged (non-fatal, continues to table parse): %s", pdf_name, meta.flag_reason)

            if header_row is None or not all_rows:
                meta.flagged = True
                meta.flag_reason = (meta.flag_reason + "; " if meta.flag_reason else "") + "No transaction table detected"
                logger.warning(
                    "[%s] FLAGGED, aborting parse: %s (pdfplumber found no table on any page — "
                    "often means the statement renders rows as plain text/lines rather than a "
                    "detectable table grid; may need a scanned-PDF OCR pass or a different "
                    "table-extraction strategy for this bank's layout)",
                    pdf_name, meta.flag_reason,
                )
                return ParseResult(meta=meta, transactions=[], success=False, error=meta.flag_reason)

            col_map = map_columns(header_row)
            logger.info("[%s] deterministic column mapping: %s", pdf_name, col_map if col_map else "FAILED (unrecognized header)")
            if col_map is None and field_locator_fn is not None:
                logger.info("[%s] deterministic mapping failed; invoking field_locator LLM fallback", pdf_name)
                col_map = field_locator_fn(header_row, all_rows[:5])
                logger.info("[%s] field_locator result: %s", pdf_name, col_map if col_map else "no confident mapping returned")
            elif col_map is None:
                logger.warning("[%s] deterministic mapping failed and no field_locator_fn supplied (no LLM client configured)", pdf_name)
            if col_map is None:
                meta.flagged = True
                meta.flag_reason = (meta.flag_reason + "; " if meta.flag_reason else "") + "Could not map statement columns (unrecognized layout)"
                logger.warning(
                    "[%s] FLAGGED, aborting parse: %s (header_row=%r, sample_row=%r)",
                    pdf_name, meta.flag_reason, _masked_row(header_row), _masked_row(all_rows[0] if all_rows else None),
                )
                return ParseResult(meta=meta, transactions=[], success=False, error=meta.flag_reason)

            transactions = parse_table_rows(all_rows, col_map, bank, pdf_name, account_ref)
            logger.info("[%s] parsed %d transaction(s) out of %d raw table row(s)", pdf_name, len(transactions), len(all_rows))
            if not transactions:
                meta.flagged = True
                meta.flag_reason = (meta.flag_reason + "; " if meta.flag_reason else "") + "No transaction rows parsed"
                logger.warning(
                    "[%s] FLAGGED, aborting parse: %s (col_map=%r matched, but no row's date column "
                    "parsed as a date — check date format against sample row %r)",
                    pdf_name, meta.flag_reason, col_map, _masked_row(all_rows[0] if all_rows else None),
                )
                return ParseResult(meta=meta, transactions=[], success=False, error=meta.flag_reason)

            if transactions[0].balance is not None:
                meta.opening_balance = round(transactions[0].balance - transactions[0].credit + transactions[0].debit, 2)
            if transactions[-1].balance is not None:
                meta.closing_balance = transactions[-1].balance
            logger.info(
                "[%s] parse successful: flagged=%s, opening_balance=%s, closing_balance=%s",
                pdf_name, meta.flagged, meta.opening_balance, meta.closing_balance,
            )

            return ParseResult(meta=meta, transactions=transactions, success=not meta.flagged)

    except Exception as e:  # noqa: BLE001 - one bad file must not abort the run (P1 resilience)
        meta.flagged = True
        meta.flag_reason = f"Parse error: {e}"
        logger.exception("[%s] unhandled exception during parse", pdf_name)
        return ParseResult(meta=meta, transactions=[], success=False, error=str(e))
