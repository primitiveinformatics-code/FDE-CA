from datetime import date

from bank_statement_analyzer.parsing.pdf_parser import (
    extract_account_holder_name,
    extract_account_number,
    extract_ifsc,
    extract_statement_period,
    map_columns,
    mask_account_number_display,
    normalize_amount,
    parse_date_flexible,
    parse_table_rows,
)


def test_normalize_amount_variants():
    assert normalize_amount("1,234.56") == 1234.56
    assert normalize_amount("₹ 500.00") == 500.0
    assert normalize_amount("") == 0.0
    assert normalize_amount("-") == 0.0
    assert normalize_amount("(100.00)") == -100.0
    assert normalize_amount("250.00 Dr") == -250.0


def test_parse_date_flexible_formats():
    assert parse_date_flexible("01-04-2025") == date(2025, 4, 1)
    assert parse_date_flexible("01/04/2025") == date(2025, 4, 1)
    assert parse_date_flexible("2025-04-01") == date(2025, 4, 1)
    assert parse_date_flexible("garbage") is None


def test_map_columns_debit_credit_layout():
    header = ["Date", "Narration", "Withdrawal Amt", "Deposit Amt", "Closing Balance"]
    col_map = map_columns(header)
    assert col_map is not None
    assert col_map["date"] == 0
    assert col_map["description"] == 1
    assert col_map["debit"] == 2
    assert col_map["credit"] == 3
    assert col_map["balance"] == 4


def test_map_columns_amount_drcr_layout():
    header = ["Txn Date", "Particulars", "Amount", "Dr/Cr", "Balance"]
    col_map = map_columns(header)
    assert col_map is not None
    assert col_map["amount"] == 2
    assert col_map["drcr"] == 3


def test_map_columns_unrecognized_returns_none():
    header = ["Col A", "Col B", "Col C"]
    assert map_columns(header) is None


def test_parse_table_rows_debit_credit():
    header = ["Date", "Narration", "Withdrawal Amt", "Deposit Amt", "Balance"]
    col_map = map_columns(header)
    rows = [
        ["01-04-2025", "Opening balance b/f", "", "", "10000.00"],
        ["02-04-2025", "ATM WDL", "2000.00", "", "8000.00"],
        ["03-04-2025", "SALARY CREDIT", "", "50000.00", "58000.00"],
    ]
    txns = parse_table_rows(rows, col_map, "HDFC", "test.pdf", "ACC-1")
    assert len(txns) == 3
    assert txns[1].debit == 2000.0
    assert txns[2].credit == 50000.0
    assert txns[0].ref == "1 · HDFC · test.pdf"


def test_extract_account_number():
    assert extract_account_number("Account No: 123456789012") == "123456789012"


def test_extract_ifsc():
    assert extract_ifsc("Branch IFSC: HDFC0001234 Mumbai") == "HDFC0001234"


def test_extract_account_holder_name():
    assert extract_account_holder_name("Customer Name: JOHN DOE\nAccount No: 123") == "JOHN DOE"


def test_extract_statement_period():
    start, end = extract_statement_period("Statement for the period 01-04-2025 to 30-04-2025")
    assert start == date(2025, 4, 1)
    assert end == date(2025, 4, 30)


def test_mask_account_number_display():
    assert mask_account_number_display("123456789012") == "XXXXXXXX9012"
    assert mask_account_number_display(None) is None
