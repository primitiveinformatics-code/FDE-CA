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

import re
from datetime import date, datetime
from typing import Callable

from bank_statement_analyzer.parsing.models import ParseResult, StatementMeta, Transaction
from bank_statement_analyzer.parsing.ocr import ocr_document, page_needs_ocr

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
    r"\s*(?:to|-|–|through)\s*"
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
) -> ParseResult:
    """Parse one statement PDF end-to-end. `field_locator_fn`, if given,
    is called ONLY with already-extracted header/sample-row text (never
    raw PII) when deterministic column mapping fails. `bank` may be None,
    in which case it's guessed from the document text and the file is
    flagged for the CA to confirm if guessing fails (FR-2)."""
    import pdfplumber

    pdf_name = pdf_path.rsplit("/", 1)[-1]
    meta = StatementMeta(bank=bank or "", pdf=pdf_name, account_ref=account_ref)

    try:
        with pdfplumber.open(pdf_path, password=password) as pdf:
            full_text_parts = []
            used_ocr = False
            all_rows: list[list[str]] = []
            header_row: list[str] | None = None

            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_needs_ocr(page_text):
                    used_ocr = True
                for table in page.extract_tables() or []:
                    if not table:
                        continue
                    if header_row is None:
                        header_row = table[0]
                        all_rows.extend(table[1:])
                    else:
                        all_rows.extend(table)
                full_text_parts.append(page_text)

            full_text = "\n".join(full_text_parts)

            if used_ocr:
                meta.used_ocr = True
                meta.ocr_raw_text = ocr_document(pdf)
                full_text = full_text + "\n" + meta.ocr_raw_text

            if not full_text.strip() and not all_rows:
                meta.flagged = True
                meta.flag_reason = "No extractable text or table found (not text-based and OCR yielded nothing)"
                return ParseResult(meta=meta, transactions=[], success=False, error=meta.flag_reason)

            if not meta.bank:
                meta.bank = guess_bank_name(full_text) or ""

            meta.account_number_masked = mask_account_number_display(extract_account_number(full_text))
            meta.account_holder_name = extract_account_holder_name(full_text)
            meta.ifsc = extract_ifsc(full_text)
            meta.period_start, meta.period_end = extract_statement_period(full_text)

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

            if header_row is None or not all_rows:
                meta.flagged = True
                meta.flag_reason = (meta.flag_reason + "; " if meta.flag_reason else "") + "No transaction table detected"
                return ParseResult(meta=meta, transactions=[], success=False, error=meta.flag_reason)

            col_map = map_columns(header_row)
            if col_map is None and field_locator_fn is not None:
                col_map = field_locator_fn(header_row, all_rows[:5])
            if col_map is None:
                meta.flagged = True
                meta.flag_reason = (meta.flag_reason + "; " if meta.flag_reason else "") + "Could not map statement columns (unrecognized layout)"
                return ParseResult(meta=meta, transactions=[], success=False, error=meta.flag_reason)

            transactions = parse_table_rows(all_rows, col_map, bank, pdf_name, account_ref)
            if not transactions:
                meta.flagged = True
                meta.flag_reason = (meta.flag_reason + "; " if meta.flag_reason else "") + "No transaction rows parsed"
                return ParseResult(meta=meta, transactions=[], success=False, error=meta.flag_reason)

            if transactions[0].balance is not None:
                meta.opening_balance = round(transactions[0].balance - transactions[0].credit + transactions[0].debit, 2)
            if transactions[-1].balance is not None:
                meta.closing_balance = transactions[-1].balance

            return ParseResult(meta=meta, transactions=transactions, success=not meta.flagged)

    except Exception as e:  # noqa: BLE001 - one bad file must not abort the run (P1 resilience)
        meta.flagged = True
        meta.flag_reason = f"Parse error: {e}"
        return ParseResult(meta=meta, transactions=[], success=False, error=str(e))
