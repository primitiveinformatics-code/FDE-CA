from bank_statement_analyzer.agents.export_agent import apply_review_edits
from bank_statement_analyzer.calculators.calculator import expense_by_category, group_income_by_counterparty
from bank_statement_analyzer.run_context import RunResult


def test_review_edit_rebuilds_income_and_expense_summaries(make_txn):
    credit = make_txn(1, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-01", "ACME CORP CONSULTING FEE", credit=45000)
    debit = make_txn(2, "HDFC", "hdfc.pdf", "ACC-1", "2025-04-05", "ATM CASH WDL", debit=2000)
    credit.counterparty = "ACME CORP CONSULTING FEE"
    debit.category = "Cash/ATM"

    run = RunResult(
        client_label="Test",
        transactions=[credit, debit],
        income_groups=group_income_by_counterparty([credit]),
        expense_categories=expense_by_category([debit]),
    )
    # Sanity: before the edit, itr_head is unset in the pre-built summary.
    assert run.income_groups[0].itr_head is None

    apply_review_edits(run, [
        {"ref": credit.ref, "field": "itr_head", "value": "Business/Professional receipts"},
        {"ref": debit.ref, "field": "category", "value": "Loan EMIs"},
    ])

    assert credit.itr_head == "Business/Professional receipts"
    assert credit.itr_head_confidence == "confirmed"
    assert debit.category == "Loan EMIs"

    # The rebuilt summaries must reflect the edit, not the stale snapshot.
    assert run.income_groups[0].itr_head == "Business/Professional receipts"
    assert run.expense_categories[0].category == "Loan EMIs"
    assert run.expense_categories[0].total == 2000
