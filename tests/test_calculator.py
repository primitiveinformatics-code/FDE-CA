from bank_statement_analyzer.calculators.calculator import (
    balance_continuity,
    expense_by_category,
    group_income_by_counterparty,
    run_totals,
)


def test_balance_continuity_ok(make_txn):
    txns = [
        make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "credit 1", credit=1000),
        make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-02", "debit 1", debit=400),
    ]
    result = balance_continuity(txns, opening_balance=500, closing_balance=1100)
    assert result.ok
    assert result.computed_closing == 1100
    assert result.diff == 0


def test_balance_continuity_flags_mismatch(make_txn):
    txns = [
        make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "credit 1", credit=1000),
    ]
    result = balance_continuity(txns, opening_balance=500, closing_balance=2000)
    assert not result.ok
    assert result.diff == -500


def test_group_income_by_counterparty(make_txn):
    txns = [
        make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "ACME CORP SALARY", credit=50000),
        make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-05-01", "ACME CORP SALARY", credit=50000),
    ]
    for t in txns:
        t.counterparty = "ACME CORP"
        t.itr_head = "Salary"
        t.itr_head_confidence = "high"
    groups = group_income_by_counterparty(txns)
    assert len(groups) == 1
    assert groups[0].total == 100000
    assert groups[0].frequency == 2
    assert groups[0].itr_head == "Salary"


def test_expense_by_category(make_txn):
    txns = [
        make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "ATM WDL", debit=2000),
        make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-02", "ATM WDL", debit=1000),
    ]
    for t in txns:
        t.category = "Cash/ATM"
    cats = expense_by_category(txns)
    assert len(cats) == 1
    assert cats[0].total == 3000
    assert cats[0].count == 2


def test_run_totals(make_txn):
    txns = [
        make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "c", credit=100),
        make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-02", "d", debit=40),
    ]
    totals = run_totals(txns)
    assert totals.total_credits == 100
    assert totals.total_debits == 40
    assert totals.net == 60
