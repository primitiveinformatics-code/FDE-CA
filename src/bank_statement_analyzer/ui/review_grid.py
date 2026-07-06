"""FR-11: optional pre-export review grid.

Only shown when the review toggle is ON (default OFF). Lets the CA/staff
correct a suggested ITR head or expense category before the workbook is
generated. Edits are collected and applied by `export_agent.apply_review_edits`.
"""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from bank_statement_analyzer.config import EXPENSE_CATEGORIES, ITR_HEADS
from bank_statement_analyzer.parsing.models import Transaction

HEADERS = ["Ref", "Description", "Amount", "ITR head / Category", "Confidence"]


class ReviewGrid(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self._refs: list[str] = []
        self._is_credit: list[bool] = []

    def load_transactions(self, transactions: list[Transaction]) -> None:
        reviewable = [t for t in transactions if t.is_credit or t.category]
        self.table.setRowCount(len(reviewable))
        self._refs = [t.ref for t in reviewable]
        self._is_credit = [t.is_credit for t in reviewable]

        for row, t in enumerate(reviewable):
            self.table.setItem(row, 0, QTableWidgetItem(t.ref))
            self.table.setItem(row, 1, QTableWidgetItem(t.description))
            self.table.setItem(row, 2, QTableWidgetItem(f"{t.amount:.2f}"))

            combo = QComboBox()
            options = ITR_HEADS if t.is_credit else EXPENSE_CATEGORIES
            combo.addItems(options)
            current = t.itr_head if t.is_credit else t.category
            if current and current in options:
                combo.setCurrentText(current)
            self.table.setCellWidget(row, 3, combo)

            self.table.setItem(row, 4, QTableWidgetItem(t.itr_head_confidence or ""))

        self.table.resizeColumnsToContents()

    def collect_edits(self) -> list[dict]:
        edits = []
        for row, ref in enumerate(self._refs):
            combo: QComboBox = self.table.cellWidget(row, 3)
            if combo is None:
                continue
            field = "itr_head" if self._is_credit[row] else "category"
            edits.append({"ref": ref, "field": field, "value": combo.currentText()})
        return edits
