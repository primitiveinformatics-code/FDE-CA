"""Tally-import-friendly CSV export (FR-10), mirroring the "All
transactions" sheet columns of the Excel workbook.
"""
from __future__ import annotations

import csv

from bank_statement_analyzer.run_context import RunResult

CSV_HEADERS = ["Serial", "Bank", "PDF", "Date", "Description", "Dr", "Cr", "Balance", "Tags"]


def export_csv(run: RunResult, output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
        for t in sorted(run.transactions, key=lambda x: (x.bank, x.pdf, x.serial)):
            writer.writerow([
                t.serial, t.bank, t.pdf, t.txn_date.isoformat(), t.description,
                f"{t.debit:.2f}" if t.debit else "", f"{t.credit:.2f}" if t.credit else "",
                f"{t.balance:.2f}" if t.balance is not None else "",
                ", ".join(sorted(t.tags)),
            ])
