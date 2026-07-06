"""Excel workbook export (FR output spec) — deterministic, no LLM.

Sheets: Summary, Self-transfers, Refunds, Income sources,
Credit-card payments, Expenditure summary, Needs attention,
All transactions. Every row carries a `serial · bank · pdf` reference so
any figure traces back to its source line (P0 traceability).
"""
from __future__ import annotations

import xlsxwriter

from bank_statement_analyzer.run_context import RunResult


def _add_formats(workbook: xlsxwriter.Workbook) -> dict:
    return {
        "header": workbook.add_format({"bold": True, "bg_color": "#D9E1F2", "border": 1}),
        "currency": workbook.add_format({"num_format": "#,##0.00"}),
        "date": workbook.add_format({"num_format": "dd-mmm-yyyy"}),
        "title": workbook.add_format({"bold": True, "font_size": 14}),
        "bold": workbook.add_format({"bold": True}),
        "wrap": workbook.add_format({"text_wrap": True}),
    }


def _write_header(ws, row: int, headers: list[str], fmt) -> None:
    for col, h in enumerate(headers):
        ws.write(row, col, h, fmt)


def _write_summary_sheet(workbook, run: RunResult, fmts) -> None:
    ws = workbook.add_worksheet("Summary")
    ws.write(0, 0, "Bank Statement Analysis — Summary", fmts["title"])
    ws.write(2, 0, "Client", fmts["bold"])
    ws.write(2, 1, run.client_label)

    accounts = sorted(run.statement_metas.keys())
    ws.write(3, 0, "Accounts covered", fmts["bold"])
    ws.write(3, 1, ", ".join(accounts))

    periods = [
        f"{m.period_start} to {m.period_end}"
        for m in run.statement_metas.values() if m.period_start and m.period_end
    ]
    ws.write(4, 0, "Period(s)", fmts["bold"])
    ws.write(4, 1, "; ".join(sorted(set(periods))))

    row = 6
    ws.write(row, 0, "Headline totals", fmts["bold"])
    row += 1
    if run.run_totals:
        ws.write(row, 0, "Total credits")
        ws.write(row, 1, run.run_totals.total_credits, fmts["currency"])
        row += 1
        ws.write(row, 0, "Total debits")
        ws.write(row, 1, run.run_totals.total_debits, fmts["currency"])
        row += 1
        ws.write(row, 0, "Net")
        ws.write(row, 1, run.run_totals.net, fmts["currency"])
        row += 1

    row += 1
    ws.write(row, 0, "Self-transfers confirmed", fmts["bold"])
    ws.write(row, 1, len(run.self_transfers.confirmed))
    row += 1
    ws.write(row, 0, "Refunds confirmed", fmts["bold"])
    ws.write(row, 1, len(run.refunds.confirmed))
    row += 1
    ws.write(row, 0, "Needs-attention flag count", fmts["bold"])
    ws.write(row, 1, len(run.needs_attention))

    row += 2
    ws.write(row, 0, "Balance continuity checks", fmts["bold"])
    row += 1
    _write_header(ws, row, ["Account", "Opening", "Closing", "Computed closing", "Diff", "OK?"], fmts["header"])
    for c in run.continuity:
        row += 1
        ws.write(row, 0, c.account_ref)
        ws.write(row, 1, c.opening_balance, fmts["currency"])
        ws.write(row, 2, c.closing_balance, fmts["currency"])
        ws.write(row, 3, c.computed_closing, fmts["currency"])
        ws.write(row, 4, c.diff, fmts["currency"])
        ws.write(row, 5, "OK" if c.ok else "MISMATCH")

    ws.set_column(0, 0, 26)
    ws.set_column(1, 5, 18)


def _write_reconciliation_sheet(workbook, sheet_name: str, reco, transactions_by_ref, fmts, leg_a_label: str, leg_b_label: str) -> None:
    ws = workbook.add_worksheet(sheet_name)
    headers = [leg_a_label, leg_b_label, "Amount", "Date diff (days)", "Method", "Confidence", "Status"]
    _write_header(ws, 0, headers, fmts["header"])
    row = 0
    for pair in reco.confirmed:
        row += 1
        ws.write(row, 0, pair.leg_a_ref)
        ws.write(row, 1, pair.leg_b_ref)
        ws.write(row, 2, pair.amount, fmts["currency"])
        ws.write(row, 3, pair.date_diff_days)
        ws.write(row, 4, pair.method)
        ws.write(row, 5, pair.confidence)
        ws.write(row, 6, "confirmed")
    for cand in reco.unmatched:
        row += 1
        txn = transactions_by_ref.get(cand.ref)
        if txn is not None and txn.is_debit:
            ws.write(row, 0, cand.ref)
            ws.write(row, 1, "")
        else:
            ws.write(row, 0, "")
            ws.write(row, 1, cand.ref)
        ws.write(row, 2, cand.amount, fmts["currency"])
        ws.write(row, 3, "")
        ws.write(row, 4, "")
        ws.write(row, 5, "low")
        ws.write(row, 6, f"unmatched: {cand.reason}")
    ws.set_column(0, 1, 22)
    ws.set_column(2, 6, 18)


def _write_income_sheet(workbook, run: RunResult, fmts) -> None:
    ws = workbook.add_worksheet("Income sources")
    _write_header(ws, 0, ["Payer", "Total", "Frequency", "Dates", "ITR head", "Confidence", "Refs"], fmts["header"])
    row = 0
    for g in run.income_groups:
        row += 1
        ws.write(row, 0, g.counterparty)
        ws.write(row, 1, g.total, fmts["currency"])
        ws.write(row, 2, g.frequency)
        ws.write(row, 3, "; ".join(g.dates))
        ws.write(row, 4, g.itr_head or "")
        ws.write(row, 5, g.itr_head_confidence or "")
        ws.write(row, 6, "; ".join(g.refs))
    ws.set_column(0, 0, 30)
    ws.set_column(1, 1, 16)
    ws.set_column(2, 2, 12)
    ws.set_column(3, 6, 30)


def _write_cc_payments_sheet(workbook, run: RunResult, fmts) -> None:
    ws = workbook.add_worksheet("Credit-card payments")
    _write_header(ws, 0, ["Date", "Amount", "Card hint", "Ref"], fmts["header"])
    row = 0
    by_ref = run.transactions_by_ref()
    for ref in run.cc_payment_refs:
        t = by_ref.get(ref)
        if not t:
            continue
        row += 1
        ws.write_datetime(row, 0, t.txn_date, fmts["date"])
        ws.write(row, 1, t.amount, fmts["currency"])
        ws.write(row, 2, t.description[:60])
        ws.write(row, 3, t.ref)
    ws.set_column(0, 0, 14)
    ws.set_column(1, 1, 14)
    ws.set_column(2, 2, 40)
    ws.set_column(3, 3, 22)


def _write_expenditure_sheet(workbook, run: RunResult, fmts) -> None:
    ws = workbook.add_worksheet("Expenditure summary")
    _write_header(ws, 0, ["Category", "Total", "Count"], fmts["header"])
    row = 0
    for c in run.expense_categories:
        row += 1
        ws.write(row, 0, c.category)
        ws.write(row, 1, c.total, fmts["currency"])
        ws.write(row, 2, c.count)
    ws.set_column(0, 0, 24)
    ws.set_column(1, 2, 14)


def _write_needs_attention_sheet(workbook, run: RunResult, fmts) -> None:
    ws = workbook.add_worksheet("Needs attention")
    _write_header(ws, 0, ["Issue", "File", "Ref", "Suggestion"], fmts["header"])
    row = 0
    for item in run.needs_attention:
        row += 1
        ws.write(row, 0, item.issue)
        ws.write(row, 1, item.file)
        ws.write(row, 2, item.ref or "")
        ws.write(row, 3, item.suggestion)
    ws.set_column(0, 0, 32)
    ws.set_column(1, 1, 24)
    ws.set_column(2, 2, 22)
    ws.set_column(3, 3, 50)


def _write_all_transactions_sheet(workbook, run: RunResult, fmts) -> None:
    ws = workbook.add_worksheet("All transactions")
    headers = ["Serial", "Bank", "PDF", "Date", "Description", "Dr", "Cr", "Balance", "Tags"]
    _write_header(ws, 0, headers, fmts["header"])
    row = 0
    for t in sorted(run.transactions, key=lambda x: (x.bank, x.pdf, x.serial)):
        row += 1
        ws.write(row, 0, t.serial)
        ws.write(row, 1, t.bank)
        ws.write(row, 2, t.pdf)
        ws.write_datetime(row, 3, t.txn_date, fmts["date"])
        ws.write(row, 4, t.description)
        ws.write(row, 5, t.debit or "", fmts["currency"])
        ws.write(row, 6, t.credit or "", fmts["currency"])
        ws.write(row, 7, t.balance if t.balance is not None else "", fmts["currency"])
        ws.write(row, 8, ", ".join(sorted(t.tags)))
    ws.set_column(0, 0, 8)
    ws.set_column(1, 2, 16)
    ws.set_column(3, 3, 14)
    ws.set_column(4, 4, 45)
    ws.set_column(5, 7, 14)
    ws.set_column(8, 8, 30)


def export_workbook(run: RunResult, output_path: str) -> None:
    workbook = xlsxwriter.Workbook(output_path)
    fmts = _add_formats(workbook)
    by_ref = run.transactions_by_ref()

    _write_summary_sheet(workbook, run, fmts)
    _write_reconciliation_sheet(workbook, "Self-transfers", run.self_transfers, by_ref, fmts, "Out ref", "In ref")
    _write_reconciliation_sheet(workbook, "Refunds", run.refunds, by_ref, fmts, "Original debit ref", "Refund credit ref")
    _write_income_sheet(workbook, run, fmts)
    _write_cc_payments_sheet(workbook, run, fmts)
    _write_expenditure_sheet(workbook, run, fmts)
    _write_needs_attention_sheet(workbook, run, fmts)
    _write_all_transactions_sheet(workbook, run, fmts)

    workbook.close()
